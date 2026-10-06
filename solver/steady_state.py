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
import math, copy
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


RECOVERY_BUDGET_S=20.0      # wall-clock allowed for the recovery chain of one solve
CONVERGED_RESIDUAL=1e-4     # scaled residual above which a stalled least-squares exit (xtol/ftol) is NOT reported as a solution


def _poor(info): return (not info) or float(info.get("max_abs_residual", 0.0)) > 1e-4


def solve_network_robust(nodes, edges, *, initial_guess=None, **kw):
    """``solve_network`` that does not accept a stalled exit.

    A warm start taken from a very different operating point (typically: wells whose natural rate is far above the facility limit,
    then choked) can stall the trust-region iteration with a small but non-zero residual. Recovery order:
    1. cold start; 2. continuation on the well rate - wells are first held to a small fraction of their natural rate (a regime
    with little friction, always solvable) and the limit is relaxed in steps, each step warm-started from the previous one.
    Returns the best result found (lowest residual)."""
    import time as _t
    t_end=_t.perf_counter()+float(RECOVERY_BUDGET_S)
    quick=dict(kw); quick['max_nfev']=min(int(kw.get('max_nfev',3000)),300)
    best=solve_network(nodes,edges,initial_guess=initial_guess,**quick)      # short budget: a well-posed case converges in a few dozen evaluations
    if not _poor(best[2]): return best
    cands=[best]
    if _t.perf_counter()>t_end: return _tag(best,'stalled (time budget)')
    if initial_guess:
        r=solve_network(nodes,edges,initial_guess=None,**quick); cands.append(r)
        if not _poor(r[2]): return _tag(r,'cold start')
    if _t.perf_counter()>t_end: return _tag(min(cands,key=lambda c: float(c[2].get('max_abs_residual',1e9))),'stalled (time budget)')
    try:
        r=_continuation(nodes,edges,min(cands,key=lambda c: float(c[2].get('max_abs_residual',1e9))),kw,t_end)
        if r is not None:
            cands.append(r)
            if not _poor(r[2]): return _tag(r,'rate continuation')
    except Exception: pass
    if _t.perf_counter()>t_end: return _tag(min(cands,key=lambda c: float(c[2].get('max_abs_residual',1e9))),'stalled (time budget)')
    r=solve_network(nodes,edges,initial_guess=initial_guess,**kw); cands.append(r)   # last resort: the full-budget solve (slow crawl)
    out=min(cands,key=lambda c: float(c[2].get('max_abs_residual',1e9)))
    return out


def _tag(r, how):
    info=dict(r[2]); info['recovery']=how; return (r[0],r[1],info,r[3])


def _continuation(nodes, edges, seed, kw, t_end=None):
    """Rate continuation. Every producer is held to a fraction of its natural rate (found at the seed pressures): the largest
    fraction that solves cleanly is the anchor, then the cap is relaxed in steps of x2.5, each step warm-started from the previous
    one, ending on the original model (user / network caps restored, nothing added)."""
    import copy as _c
    from physics.well_model import well_settings, solve_well_rate
    quick=dict(kw); quick['max_nfev']=min(int(kw.get('max_nfev',3000)),300)
    p0=seed[0] or {}
    ns=_c.deepcopy(nodes); base={}
    for n in ns:
        if n.get('kind')=='well':
            prm=n.setdefault('params',{}); s=well_settings(prm); q,_=solve_well_rate(p0.get(n['id'],50.0),s)
            if math.isfinite(s['max_rate']): q=min(q,s['max_rate'])
            base[n['id']]=(max(q,1.0),prm.get('_network_cap_m3d'))
    if not base: return None
    def setcaps(f):
        for n in ns:
            if n['id'] in base:
                q,old=base[n['id']]; cap=q*f if f<1.0 else old
                if cap is None: n['params'].pop('_network_cap_m3d',None)
                else: n['params']['_network_cap_m3d']=min(cap,old) if old is not None else cap
    def run(f,guess):
        setcaps(f); r=solve_network(ns,edges,initial_guess=guess,**quick)
        return r,{'pressures':r[0],'flows':r[1],'well_rates':(r[2].get('well_rates') or {})}
    anchor=None; guess=None
    import time as _t
    for f in (0.5,0.2,0.08,0.03):
        if t_end is not None and _t.perf_counter()>t_end: return None
        r,g=run(f,None)
        if not _poor(r[2]): anchor=f; guess=g; break
    if anchor is None: return None
    f=anchor
    while f<1.0:
        f=min(f*2.5,1.0); r,guess=run(f,guess)
    return r


def solve_network(nodes, edges, *, x_scale="jac", max_nfev=3000, initial_guess=None, reseed_attempts=2):
    """Solve the steady-state network. Inline equipment nodes (choke/valve/pump/compressor) are expanded into
    inlet/outlet junctions + an internal link and folded back afterwards (network/equipment.py)."""
    from network.equipment import has_inline, expand_inline_equipment, expand_guess, collapse_results
    if has_inline(nodes):
        ns, es, mp = expand_inline_equipment(nodes, edges)
        return collapse_results(solve_network(ns, es, x_scale=x_scale, max_nfev=max_nfev, initial_guess=expand_guess(initial_guess, mp), reseed_attempts=reseed_attempts), mp)
    return _solve_network_core(nodes, edges, x_scale=x_scale, max_nfev=max_nfev, initial_guess=initial_guess, reseed_attempts=reseed_attempts)


FLUID_FOLLOW_TOL_GOR = 0.05     # relative GOR change that triggers a consistency re-solve
FLUID_FOLLOW_TOL_WC = 0.02      # absolute water-cut change that triggers it
FLUID_FOLLOW_MAX_PASSES = 3


def _solve_network_core(nodes, edges, *, x_scale="jac", max_nfev=3000, initial_guess=None, reseed_attempts=2, follow_wells=True):
    """Solve, then make the flowline fluid consistent with what the wells actually produce.

    Flowline/compressor ``gor_sm3sm3``, ``water_cut``, ``api`` and ``gas_sg`` are inputs of the line hydraulics, but they describe the
    fluid the upstream wells deliver. When a tank's CGR / GOR / water cut changes (or the user edits a well) a stale line value makes the lines carry the
    wrong gas volume (e.g. 16x too much gas after raising the CGR) and the solve gets slow or does not converge. After each solve the blended fluid of the
    stream in every line is computed from the solved well rates and, if it differs materially, the network is re-solved with it (warm-started,
    max 3 passes). A line can opt out with ``params['follow_wells']=False``."""
    kw=dict(x_scale=x_scale,max_nfev=max_nfev,reseed_attempts=reseed_attempts)
    cur=edges; passes=0
    if follow_wells:
        try: cur=_preblend(nodes,edges)       # fluids consistent BEFORE the first solve: a stale line fluid is the usual reason for a stalled first pass
        except Exception: cur=edges
    res=_solve_network_once(nodes,cur,initial_guess=initial_guess,**kw)
    if not follow_wells: return res
    try:
        from network.fluid_blend import propagate_blend_to_edges
        for _ in range(FLUID_FOLLOW_MAX_PASSES):
            p,q,info,d=res
            if not p or not math.isfinite(float(info.get('max_abs_residual',0.0))) or not any(float(v.get('liquid_rate_m3d',0.0))>1e-9 for v in d.values()): break
            new=propagate_blend_to_edges(nodes,cur,res,kinds=('pipeline','compressor'))
            byid={e['id']:e for e in cur}; changed=0
            for e in new:
                o=byid.get(e['id']); prm=e.get('params') or {}
                if o is None or not prm.get('fluid_blend') or (o.get('params') or {}).get('follow_wells') is False:
                    if o is not None and (o.get('params') or {}).get('follow_wells') is False: e['params']=copy.deepcopy(o.get('params'))
                    continue
                op=o.get('params') or {}; og=float(op.get('gor_sm3sm3',100.0) or 0.0); ng=float(prm.get('gor_sm3sm3',og)); ow=float(op.get('water_cut',0.2) or 0.0); nw=float(prm.get('water_cut',ow))
                if abs(ng-og)>FLUID_FOLLOW_TOL_GOR*max(og,1.0) or abs(nw-ow)>FLUID_FOLLOW_TOL_WC: changed+=1
            if not changed: break
            guess={'pressures':p,'flows':q,'well_rates':info.get('well_rates') or {}}
            r2=_solve_network_once(nodes,new,initial_guess=guess,**kw)
            if float(r2[2].get('max_abs_residual',1e9))>max(1e-4,float(info.get('max_abs_residual',0.0))*10): break     # never trade a converged solve for a worse one
            res=r2; cur=new; passes+=1
    except Exception: pass
    if cur is not edges:
        old={e['id']:e for e in edges}; ff={}
        for e in cur:
            o=old.get(e['id'])
            if o is not None and e is not o and (e.get('params') or {}).get('fluid_blend'):
                ff[e['id']]={k:float(e['params'][k]) for k in ('gor_sm3sm3','water_cut','api','gas_sg') if e['params'].get(k) is not None}
        info=dict(res[2]); info['fluid_follow']=ff
        if passes: info['fluid_follow_passes']=passes
        res=(res[0],res[1],info,res[3])
    return res


def apply_fluid_follow(edges, info):
    """Edges as the solver used them (line fluid = blended well fluid). ``edges`` itself is not modified."""
    ff=(info or {}).get('fluid_follow') or {}
    if not ff: return edges
    out=[]
    for e in edges:
        if e['id'] in ff: e=copy.deepcopy(e); e.setdefault('params',{}).update(ff[e['id']])
        out.append(e)
    return out


def _preblend(nodes, edges):
    """Line fluids from the wells' own fluid, weighted by each well's natural rate at the mean fixed pressure (no solve needed)."""
    from network.fluid_blend import propagate_blend_to_edges
    links=links_of(edges); byid={n['id']:n for n in nodes}
    fixed=[v for v in (fixed_pressure(n) for n in nodes) if v is not None]; p_ref=float(np.mean(fixed)) if fixed else 50.0
    details={}; wq={}
    for n in nodes:
        if n.get('kind')!='well': continue
        sx=well_settings(n.get('params',{}) or {})
        if not sx['open']: continue
        q,_=solve_well_rate(p_ref,sx)
        if math.isfinite(sx['max_rate']): q=min(q,sx['max_rate'])
        q=max(float(q),0.0); wq[n['id']]=q; wc=sx['water_cut']; oil=q*(1-wc)
        details[n['id']]={'liquid_rate_m3d':q,'oil_rate_m3d':oil,'water_rate_m3d':q*wc,'gas_rate_sm3d':oil*sx['gor']}
    if not wq: return edges
    flows=_initial_link_flows(nodes,links,wq,{})
    for e in links: flows.setdefault(e['id'],0.0)
    new=propagate_blend_to_edges(nodes,edges,({}, flows, {}, details),kinds=('pipeline','compressor'))
    old={e['id']:e for e in edges}; out=[]
    for e in new:
        o=old.get(e['id'])
        if o is None or (o.get('params') or {}).get('follow_wells') is False or not (e.get('params') or {}).get('fluid_blend'): out.append(o if o is not None else e); continue
        op=o.get('params') or {}; prm=e['params']; og=float(op.get('gor_sm3sm3',100.0) or 0.0)
        material=abs(float(prm.get('gor_sm3sm3',og))-og)>FLUID_FOLLOW_TOL_GOR*max(og,1.0) or abs(float(prm.get('water_cut',0.0))-float(op.get('water_cut',0.2) or 0.0))>FLUID_FOLLOW_TOL_WC
        out.append(e if material else o)
    return out


def _solve_network_once(nodes, edges, *, x_scale="jac", max_nfev=3000, initial_guess=None, reseed_attempts=2):
    from network.reservoir_mb import ensure_tank_links
    nodes=ensure_tank_links(nodes)
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
        return least_squares(residual,xs,method='dogbox',max_nfev=int(nfev),xtol=1e-12,ftol=1e-12,gtol=1e-12,bounds=(lo,hi),x_scale=x_scale,jac_sparsity=sparsity if nvar>2 else None)
    def _clip(v): return np.minimum(np.maximum(v,lo+1e-9*(np.abs(lo)+1)),hi-1e-9*(np.abs(hi)+1))
    budget=int(min(max_nfev,400+60*nvar))
    # Short first pass: if it stalls, re-seeding the wells on their stable branch (below) is
    # far cheaper than letting TRF crawl from a poor start.
    best=_run(x0,int(min(budget,120+15*nvar)))
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
    _res_max=float(np.max(np.abs(res))) if len(res) else 0.0
    info={'success':bool(sol.success) and _res_max<=CONVERGED_RESIDUAL,'scipy_success':bool(sol.success),'cost':float(sol.cost),'message':str(sol.message),'max_abs_residual':float(np.max(np.abs(res))) if len(res) else 0.0,
          'equipment':equipment,'nfev':int(sol.nfev),'optimality':float(sol.optimality),'status':int(sol.status),'jacobian_condition':jac_cond,
          'variable_scaling':str(x_scale),'n_unknowns':int(nvar),'well_warnings':shut_notes+well_warnings,'injectors':injector_rows,
          'well_rates':{nid:(float(x[i]) if (float(x[i])>=1e-9 and wset[nid]['open']) else 0.0) for nid,i in widx.items()},'injector_rates':{nid:max(float(x[i]),0.0) for nid,i in iidx.items()},
          'edge_fluids':fluids}
    info['constraints']=evaluate_constraints(nodes,links,pressures,flows,details,info); info['violations']=sum(r['Status']=='VIOLATED' for r in info['constraints'])
    return pressures,flows,info,details
