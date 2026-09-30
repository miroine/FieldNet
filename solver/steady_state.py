"""FieldNet steady-state network kernel (v30).

Unknowns: free node pressures, link flows, producer liquid rates and injector rates.
Equations (square system):
  * link momentum      Ps - Pt - dp(q) = 0                             [bar/10]
  * node mass balance  inflow + q_well - outflow - q_inj = 0           [m3/d / 1000]
  * producer           mid(q, q - q_cap, -excess(q, WHP)) = 0          (complementarity)
  * injector           same form with the injectivity excess

The producer equation replaces the old nested least-squares that was re-run inside every
residual evaluation. That nested solve was slow, started from the same guess every call,
could stop early (leaving ~0.1 m3/d of noise that polluted finite-difference Jacobians)
and had no notion of shut-in, rate caps or lift. The complementarity form expresses
'flowing at the IPR/VLP intersection, or dead at zero rate, or choked at the rate limit'
directly, and is exactly the condition GAP-style network solvers impose on wells.
"""
from __future__ import annotations
import math
from collections import defaultdict, deque
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix
from physics.well_model import well_settings, excess_bar, solve_well_rate, well_state
from physics.equipment import pump_head_bar, pump_power_kw, compressor_power_kw
from solver.constraints import evaluate_constraints
from solver.equations import (links_of, fixed_pressure, boundary_issues, edge_fluids, link_dp_bar,
                              injector_settings, injector_excess_bar, mid, fnum, INJECTOR_KINDS)

P_SCALE=10.0; Q_SCALE=1000.0


def _initial_link_flows(nodes, links, well_q, inj_q):
    """Push initial well rates downstream (split evenly at diverging nodes)."""
    out=defaultdict(list); indeg=defaultdict(int); ids=[n['id'] for n in nodes]
    for e in links: out[e['source']].append(e); indeg[e['target']]+=1
    supply={nid:well_q.get(nid,0.0) for nid in ids}; flows={}
    dq=deque([nid for nid in ids if indeg[nid]==0]); seen=set()
    while dq:
        u=dq.popleft(); seen.add(u); es=out[u]
        share=supply.get(u,0.0)/len(es) if es else 0.0
        for e in es:
            flows[e['id']]=share; supply[e['target']]=supply.get(e['target'],0.0)+share
            indeg[e['target']]-=1
            if indeg[e['target']]==0: dq.append(e['target'])
    return flows


def _initial_pressures(nodes, links, byid):
    """Seed free pressures from the nearest fixed boundary (downstream first).

    Production networks flow towards their boundaries, so a node sits a little above the
    nearest downstream fixed pressure. The old default (params initial_pressure_bar = 80-90
    bar on every well) often started wells above the separator pressure where they are
    dead, and the solver then got stuck on the dead branch."""
    down=defaultdict(list); up=defaultdict(list)
    for e in links: down[e['source']].append(e['target']); up[e['target']].append(e['source'])
    out={}
    for n in nodes:
        if fixed_pressure(n) is not None: continue
        for graph,sign in ((down,1.0),(up,-1.0)):
            seen={n['id']}; dq=deque([(n['id'],0)]); found=None
            while dq and found is None:
                x,h=dq.popleft()
                for y in graph[x]:
                    if y in seen: continue
                    seen.add(y); v=fixed_pressure(byid[y])
                    if v is not None: found=v+sign*1.0*(h+1); break
                    dq.append((y,h+1))
            if found is not None: out[n['id']]=max(found,1.5); break
    return out


def solve_network(nodes, edges, *, x_scale="jac", max_nfev=3000, initial_guess=None, reseed_attempts=2):
    # Unconnected components have no equations that can determine them; leave them out.
    linked={x for e in links_of(edges) for x in (e.get('source'),e.get('target'))}
    nodes=[n for n in nodes if n.get('id') in linked]
    issues=boundary_issues(nodes,edges)
    if issues: raise ValueError(issues[0]['message'])
    byid={n['id']:n for n in nodes}; links=links_of(edges)
    for e in links:
        if e.get('source') not in byid or e.get('target') not in byid:
            raise ValueError(f"Connection {e.get('id')} references a missing node")
    fluids=edge_fluids(nodes,edges)
    guess=initial_guess or {}
    gp=guess.get('pressures',{}) or {}; gq=guess.get('flows',{}) or {}; gw=guess.get('well_rates',{}) or {}

    unknown_p=[n['id'] for n in nodes if fixed_pressure(n) is None]
    wells=[n for n in nodes if n.get('kind')=='well']
    injectors=[n for n in nodes if n.get('kind') in INJECTOR_KINDS]
    wset={n['id']:well_settings(n.get('params',{})) for n in wells}
    iset={n['id']:injector_settings(n) for n in injectors}
    pidx={nid:i for i,nid in enumerate(unknown_p)}; off=len(pidx)
    qidx={e['id']:off+i for i,e in enumerate(links)}; off+=len(links)
    widx={n['id']:off+i for i,n in enumerate(wells)}; off+=len(wells)
    iidx={n['id']:off+i for i,n in enumerate(injectors)}; nvar=off+len(injectors)
    if nvar==0: return {},{},{'success':True,'cost':0,'message':'No unknowns','max_abs_residual':0,'constraints':[],'violations':0,'equipment':[]},{}

    fixed_vals=[v for v in (fixed_pressure(n) for n in nodes) if v is not None]
    p_default=(float(np.mean(fixed_vals)) if fixed_vals else 50.0)
    x0=np.zeros(nvar); lo=np.full(nvar,-np.inf); hi=np.full(nvar,np.inf)
    heur=_initial_pressures(nodes,links,byid)
    for nid,i in pidx.items():
        prm=byid[nid].get('params',{}) or {}
        h=heur.get(nid,p_default)
        if nid in gp: x0[i]=float(gp[nid])
        elif byid[nid].get('kind')=='well' or prm.get('initial_pressure_bar') is None: x0[i]=h
        else: x0[i]=fnum(prm,'initial_pressure_bar',h)
        lo[i]=1.0; hi[i]=5000.0
    def P0(nid):
        v=fixed_pressure(byid[nid]); return v if v is not None else x0[pidx[nid]]
    well_q0={}
    for n in wells:
        i=widx[n['id']]; s=wset[n['id']]; lo[i]=-1e7; hi[i]=1e7  # q>=0 is enforced by the complementarity residual; a hard bound makes TRF crawl towards zero
        if n['id'] in gw: q0=float(gw[n['id']])
        else: q0,_=solve_well_rate(P0(n['id']),s)
        x0[i]=min(max(q0,0.0),s['max_rate']); well_q0[n['id']]=x0[i]
    inj_q0={}
    for n in injectors:
        i=iidx[n['id']]; lo[i]=-1e7; hi[i]=1e7; s=iset[n['id']]
        x0[i]=float(gw.get(n['id'],max(min(s['ii']*max(injector_excess_bar(0.0,P0(n['id']),s),0.0),s['max_rate']),0.0)))
        inj_q0[n['id']]=x0[i]
    pushed=_initial_link_flows(nodes,links,well_q0,inj_q0)
    for e in links:
        i=qidx[e['id']]; lo[i]=-1e7; hi[i]=1e7
        if e['id'] in gq: x0[i]=float(gq[e['id']])
        elif pushed.get(e['id'],0.0)>1e-6: x0[i]=pushed[e['id']]
        else: x0[i]=max(fnum(e.get('params',{}) or {},'initial_rate_m3d',500.0),0.01)
    x0=np.minimum(np.maximum(x0,lo+1e-9*(np.abs(lo)+1)),hi-1e-9*(np.abs(hi)+1))
    x0=np.where(np.isfinite(x0),x0,0.0)

    def P(nid,x):
        v=fixed_pressure(byid[nid]); return v if v is not None else x[pidx[nid]]
    incoming=defaultdict(list); outgoing=defaultdict(list)
    for e in links: incoming[e['target']].append(e['id']); outgoing[e['source']].append(e['id'])

    # Per-component memoisation: finite-difference columns perturb only a few unknowns, so
    # most link/well evaluations repeat exactly the same inputs.
    memo={}
    def cached(key, fn):
        v=memo.get(key)
        if v is None:
            if len(memo)>20000: memo.clear()
            v=fn(); memo[key]=v
        return v

    def residual(x):
        r=[]
        for e in links:
            ps,pt=P(e['source'],x),P(e['target'],x); q=x[qidx[e['id']]]
            dp=cached(('L',e['id'],q,ps,pt),lambda: link_dp_bar(e,q,ps,pt,fluids[e['id']]))
            r.append((ps-pt-dp)/P_SCALE)
        for nid in unknown_p:
            bal=sum(x[qidx[k]] for k in incoming[nid])-sum(x[qidx[k]] for k in outgoing[nid])
            if nid in widx: bal+=x[widx[nid]]
            if nid in iidx: bal-=x[iidx[nid]]
            r.append(bal/Q_SCALE)
        for n in wells:
            q=x[widx[n['id']]]; s=wset[n['id']]
            if not s['open']: r.append(q/Q_SCALE); continue
            cap=s['max_rate'] if math.isfinite(s['max_rate']) else 1e9; whp=P(n['id'],x)
            h=cached(('W',n['id'],q,whp),lambda: excess_bar(q,whp,s))
            r.append(mid(q/Q_SCALE,(q-cap)/Q_SCALE,-h/P_SCALE))
        for n in injectors:
            q=x[iidx[n['id']]]; s=iset[n['id']]
            if not s['open']: r.append(q/Q_SCALE); continue
            cap=s['max_rate'] if math.isfinite(s['max_rate']) else 1e9
            r.append(mid(q/Q_SCALE,(q-cap)/Q_SCALE,-injector_excess_bar(q,P(n['id'],x),s)/P_SCALE))
        return np.asarray(r,dtype=float)

    # Jacobian sparsity pattern (row order mirrors residual()).
    rows=[]
    for e in links: rows.append([pidx[k] for k in (e['source'],e['target']) if k in pidx]+[qidx[e['id']]])
    for nid in unknown_p: rows.append([qidx[k] for k in incoming[nid]+outgoing[nid]]+([widx[nid]] if nid in widx else [])+([iidx[nid]] if nid in iidx else []))
    for n in wells: rows.append([widx[n['id']]]+([pidx[n['id']]] if n['id'] in pidx else []))
    for n in injectors: rows.append([iidx[n['id']]]+([pidx[n['id']]] if n['id'] in pidx else []))
    sparsity=lil_matrix((len(rows),nvar),dtype=int)
    for r_i,cols in enumerate(rows):
        for c in cols: sparsity[r_i,c]=1

    def _run(xs, nfev):
        return least_squares(residual,xs,max_nfev=int(nfev),xtol=1e-12,ftol=1e-12,gtol=1e-12,bounds=(lo,hi),x_scale=x_scale,jac_sparsity=sparsity)
    def _clip(v): return np.minimum(np.maximum(v,lo+1e-9*(np.abs(lo)+1)),hi-1e-9*(np.abs(hi)+1))
    budget=int(min(max_nfev,400+60*nvar))
    best=_run(x0,budget)
    # Branch selection (GAP convention): each producer must sit on the highest-rate stable
    # IPR/VLP intersection at its solved WHP; producers that can only trickle below their
    # minimum stable rate are shut in; otherwise, if the solve stalled, reseed the wells on
    # their stable branch and push those rates through the network. Repeat until consistent.
    shut_notes=[]; generic_reseeds=0
    for _ in range(6):
        xs=best.x.copy(); changed=False; converged=np.max(np.abs(best.fun))<=1e-8
        for n in wells:
            s=wset[n['id']]
            if not s['open']: continue
            qa,st=solve_well_rate(P(n['id'],xs),s); qs=max(xs[widx[n['id']]],0.0)
            if st=='below_min_rate' and qs>1e-6:
                s['open']=False; s['status_override']='shut_in_below_min_rate'; xs[widx[n['id']]]=0.0; changed=True
                shut_notes.append({'severity':'warning','code':'WELL_BELOW_MIN_RATE','component':n['id'],'message':f"{n.get('name',n['id'])} can only flow below its minimum stable rate ({s['min_rate']:.1f} m3/d) at the network back-pressure; it is reported as shut in."})
            elif abs(qa-qs)>max(0.5,1e-3*qa):
                xs[widx[n['id']]]=qa; changed=True
        if not changed:
            if converged or generic_reseeds>=int(reseed_attempts): break
            generic_reseeds+=1
            for n in wells:
                if wset[n['id']]['open']: xs[widx[n['id']]]=min(solve_well_rate(P(n['id'],xs),wset[n['id']])[0],wset[n['id']]['max_rate'])
        wq={n['id']:max(xs[widx[n['id']]],0.0) for n in wells}
        pushed=_initial_link_flows(nodes,links,wq,{})
        for e in links:
            if e['id'] in pushed: xs[qidx[e['id']]]=pushed[e['id']]
        trial=_run(_clip(xs),budget)
        if changed or np.max(np.abs(trial.fun))<np.max(np.abs(best.fun)): best=trial
    sol=best
    try:
        sv=np.linalg.svd(np.asarray(sol.jac),compute_uv=False); jac_cond=float(sv[0]/sv[-1]) if len(sv) and sv[-1]>1e-15 else float('inf')
    except Exception: jac_cond=float('nan')
    x=sol.x
    pressures={n['id']:float(P(n['id'],x)) for n in nodes}; flows={e['id']:float(x[qidx[e['id']]]) for e in links}
    details={}; well_warnings=[]
    for n in wells:
        s=wset[n['id']]; q=float(x[widx[n['id']]]); q=0.0 if (not s['open'] or q<1e-9) else q; whp=pressures[n['id']]
        d=well_state(q,whp,s); details[n['id']]=d
        if s['open']:
            dq=max(1.0,0.01*q); slope=(excess_bar(q+dq,whp,s)-excess_bar(max(q-dq,0.0),whp,s))/(q+dq-max(q-dq,0.0))
            d['stable']=bool(q<=1e-6 or slope<0 or d['status']=='rate_limited')
            if not d['stable']:
                well_warnings.append({'severity':'warning','code':'UNSTABLE_WELL_ROOT','component':n['id'],'message':f"{n.get('name',n['id'])} converged on the unstable (low-rate) IPR/VLP intersection."})
            if q<=1e-6:
                alt,_=solve_well_rate(whp,s)
                if alt>1.0:
                    well_warnings.append({'severity':'warning','code':'WELL_COULD_FLOW','component':n['id'],'message':f"{n.get('name',n['id'])} solved as dead, but a stable flowing point of {alt:.0f} m3/d exists at the same WHP."})
    injector_rows=[]
    for n in injectors:
        s=iset[n['id']]; q=max(float(x[iidx[n['id']]]),0.0); whp=pressures[n['id']]
        injector_rows.append({'Injector':n.get('name',n['id']),'id':n['id'],'Fluid':s['fluid'],'Rate [m3/d eq]':q,'WHP [bar]':whp,
                              'BHP [bar]':whp+s['rho']*9.80665*s['depth']/1e5,'Reservoir pressure [bar]':s['pr']})
    equipment=[]
    for e in links:
        prm=e.get('params',{}) or {}; q=flows[e['id']]
        if e.get('kind')=='pump':
            h=pump_head_bar(q,fnum(prm,'shutoff_head_bar',35),fnum(prm,'rated_rate_m3d',1500),fnum(prm,'min_head_bar',-1e9)); equipment.append({'Equipment':e.get('name',e['id']),'Type':'Pump','Rate [m3/d]':q,'Suction [bar]':pressures[e['source']],'Discharge [bar]':pressures[e['target']],'Head [bar]':h,'Power [kW]':pump_power_kw(q,h,fnum(prm,'rho_kgm3',850),fnum(prm,'efficiency',.75))})
        elif e.get('kind')=='compressor':
            qg=abs(q)*fnum(prm,'gor_sm3sm3',100); equipment.append({'Equipment':e.get('name',e['id']),'Type':'Compressor','Rate [m3/d]':q,'Gas [Sm3/d]':qg,'Suction [bar]':pressures[e['source']],'Discharge [bar]':pressures[e['target']],'Power [kW]':compressor_power_kw(qg,pressures[e['source']],pressures[e['target']],fnum(prm,'efficiency',.75))})
    res=residual(x)
    info={'success':bool(sol.success),'cost':float(sol.cost),'message':str(sol.message),'max_abs_residual':float(np.max(np.abs(res))) if len(res) else 0.0,
          'equipment':equipment,'nfev':int(sol.nfev),'optimality':float(sol.optimality),'status':int(sol.status),'jacobian_condition':jac_cond,
          'variable_scaling':str(x_scale),'n_unknowns':int(nvar),'well_warnings':shut_notes+well_warnings,'injectors':injector_rows,
          'well_rates':{nid:(float(x[i]) if (float(x[i])>=1e-9 and wset[nid]['open']) else 0.0) for nid,i in widx.items()},'injector_rates':{nid:max(float(x[i]),0.0) for nid,i in iidx.items()},
          'edge_fluids':fluids}
    info['constraints']=evaluate_constraints(nodes,links,pressures,flows,details); info['violations']=sum(r['Status']=='VIOLATED' for r in info['constraints'])
    return pressures,flows,info,details
