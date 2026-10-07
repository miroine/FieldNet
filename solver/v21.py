"""FieldNet Network Solver 2.0 orchestration and diagnostics.

The steady-state kernel is the equation system. This layer adds topology checks, warm
starts, retry orchestration, optional capacity-constraint enforcement (pro-rata well
choke-back, as GAP does for separator/pipeline limits) and explainable diagnostics.
"""
from copy import deepcopy
from collections import defaultdict, deque
import math
from solver.steady_state import solve_network, solve_network_robust, apply_fluid_follow
from solver.physical_audit import reconstruct_physical_residuals
from solver.constraints import active_constraints
from solver.equations import fixed_pressure, boundary_issues, links_of
from physics.well_model import well_settings, solve_well_rate

LINK_KINDS={'pipeline','choke','control_valve','pump','compressor'}

def topology_precheck(nodes, edges):
    ids=[n.get('id') for n in nodes]; issues=[]
    if len(ids)!=len(set(ids)): issues.append({'severity':'error','code':'DUPLICATE_NODE_ID','message':'Duplicate node IDs make the equation system ambiguous.'})
    known=set(ids); adj={i:set() for i in known}
    eids=[e.get('id') for e in edges]
    if len(eids)!=len(set(eids)): issues.append({'severity':'error','code':'DUPLICATE_EDGE_ID','message':'Duplicate connection IDs make results ambiguous.'})
    for e in edges:
        if e.get('source') not in known or e.get('target') not in known:
            issues.append({'severity':'error','code':'DANGLING_EDGE','component':e.get('id'),'message':'Connection references a missing node.'}); continue
        if e.get('source')==e.get('target'): issues.append({'severity':'error','code':'SELF_LOOP','component':e.get('id'),'message':'Connection starts and ends at the same node.'})
        if e.get('kind','pipeline') not in LINK_KINDS: issues.append({'severity':'error','code':'UNKNOWN_LINK_KIND','component':e.get('id'),'message':f"Unsupported connection type {e.get('kind')!r}."})
        adj[e['source']].add(e['target']); adj[e['target']].add(e['source'])
    # Each connected component needs at least one pressure anchor. A sink with no
    # pressure is NOT an anchor (the old check accepted it and the kernel then crashed).
    seen=set(); byid={n['id']:n for n in nodes}
    for root in known:
        if root in seen: continue
        stack=[root]; comp=[]; seen.add(root)
        while stack:
            x=stack.pop(); comp.append(x)
            for y in adj[x]:
                if y not in seen: seen.add(y); stack.append(y)
        anchored=any(fixed_pressure(byid[x]) is not None for x in comp)
        if not anchored and len(comp)==1 and not any(e.get('source')==comp[0] or e.get('target')==comp[0] for e in edges):
            if byid[comp[0]].get('kind')=='reservoir': continue
            issues.append({'severity':'warning','code':'NOT_CONNECTED','component':comp[0],'message':'Component has no connections; it is excluded from the network solve.'}); continue
        if not anchored: issues.append({'severity':'error','code':'UNANCHORED_COMPONENT','component':','.join(comp),'message':'Connected component has no pressure boundary/reservoir anchor.'})
    issues.extend(boundary_issues(nodes,[e for e in edges if e.get('source') in known and e.get('target') in known]))
    issues.extend(loop_elevation_issues(nodes,[e for e in edges if e.get('source') in known and e.get('target') in known]))
    return issues

def loop_elevation_issues(nodes, edges, tol_m=0.5):
    """A closed loop must return to the same elevation. If the entered elevation changes
    around a loop do not sum to zero the hydrostatics drive a fictitious circulating flow."""
    try: import networkx as nx
    except ImportError: return []
    g=nx.MultiGraph(); dz={}
    for e in edges:
        if e.get('kind','pipeline') not in LINK_KINDS: continue
        g.add_edge(e['source'],e['target'],key=e['id']); dz[e['id']]=(e['source'],e['target'],float(e.get('elevation_change_m') or 0.0) if e.get('kind','pipeline')=='pipeline' else 0.0)
    out=[]
    try: cycles=nx.cycle_basis(nx.Graph(g))
    except Exception: return []
    for cyc in cycles:
        total=0.0; ok=True; ids=[]
        for a,b in zip(cyc,cyc[1:]+cyc[:1]):
            cand=[k for k,(s,t,_) in dz.items() if {s,t}=={a,b}]
            if not cand: ok=False; break
            s,t,h=dz[cand[0]]; total+=h if (s,t)==(a,b) else -h; ids.append(cand[0])
        if ok and abs(total)>tol_m:
            out.append({'severity':'warning','code':'LOOP_ELEVATION_MISMATCH','component':','.join(ids),'message':f"Elevation changes around loop {' → '.join(cyc+[cyc[0]])} sum to {total:+.1f} m instead of 0; this drives an artificial circulating flow. Check elevation_change_m."})
    return out

def _apply_warm_start(nodes, edges, warm):
    """Legacy helper kept for callers that relied on params-based warm starts."""
    ns,es=deepcopy(nodes),deepcopy(edges)
    if not warm: return ns,es
    p=warm.get('pressures',{}); q=warm.get('flows',{})
    for n in ns:
        if n.get('pressure_bar') is None and n['id'] in p: n.setdefault('params',{})['initial_pressure_bar']=float(p[n['id']])
    for e in es:
        if e['id'] in q: e.setdefault('params',{})['initial_rate_m3d']=max(abs(float(q[e['id']])),0.01)
    return ns,es

def _clean_guess(nodes, edges, warm):
    if not warm: return None
    ids={n['id'] for n in nodes}; eids={e['id'] for e in edges}
    return {'pressures':{k:v for k,v in (warm.get('pressures') or {}).items() if k in ids},
            'flows':{k:v for k,v in (warm.get('flows') or {}).items() if k in eids},
            'well_rates':{k:v for k,v in (warm.get('well_rates') or {}).items() if k in ids}}

def _diagnose(info,audit,topology,tol):
    rows=list(topology)
    if not info.get('success'): rows.append({'severity':'error','code':'NONCONVERGED','message':info.get('message','Nonlinear solve failed.')})
    for r in sorted(audit.get('edge_residuals',[]),key=lambda x:abs(x['pressure_residual_bar']),reverse=True)[:5]:
        if abs(r['pressure_residual_bar'])>tol*10: rows.append({'severity':'error','code':'PRESSURE_EQUATION','component':r['id'],'value':r['pressure_residual_bar'],'message':'Physical pressure equation has a material residual.'})
    for r in sorted(audit.get('node_residuals',[]),key=lambda x:abs(x['mass_residual_m3d']),reverse=True)[:5]:
        if abs(r['mass_residual_m3d'])>tol*1000: rows.append({'severity':'error','code':'MASS_BALANCE','component':r['id'],'value':r['mass_residual_m3d'],'message':'Node mass balance has a material residual (for wells: the reported rate differs from the stable IPR/VLP rate at the solved WHP).'})
    jc=float(info.get('jacobian_condition',float('nan')))
    if math.isfinite(jc) and jc>1e10: rows.append({'severity':'warning','code':'ILL_CONDITIONED','value':jc,'message':'Jacobian is highly ill-conditioned; solution may be sensitive to perturbations.'})
    if info.get('optimality',0)>1e-3 and info.get('max_abs_residual',0)>tol: rows.append({'severity':'warning','code':'STAGNATION','value':info['optimality'],'message':'First-order optimality remains high; nonlinear iteration may have stagnated.'})
    rows.extend(info.get('well_warnings',[]))
    for c in info.get('constraints',[]):
        if c.get('Status')=='VIOLATED': rows.append({'severity':'warning','code':'OPERATING_LIMIT','component':c.get('Component'),'message':f"{c.get('Constraint')}: {c.get('Value'):.1f} vs limit {c.get('Limit'):.1f} {c.get('Unit','')}"})
    for a in info.get('constraint_actions',[]): rows.append({'severity':'info','code':'CONSTRAINT_ENFORCED','component':a.get('well'),'message':a.get('message')})
    if not rows: rows=[{'severity':'ok','code':'OK','message':'Topology, convergence, physical residuals and conditioning checks passed.'}]
    return rows

def _upstream_wells(nodes, edges, node_id=None, edge_id=None):
    byid={n['id']:n for n in nodes}; up=defaultdict(list); links=links_of(edges)
    for e in links: up[e['target']].append(e['source'])
    start=[]
    if node_id is not None: start=[node_id]
    if edge_id is not None:
        e=next((x for x in links if x['id']==edge_id),None)
        if e: start=[e['source']]
    seen=set(start); dq=deque(start); wells=[]
    while dq:
        x=dq.popleft()
        if byid.get(x,{}).get('kind')=='well': wells.append(x)
        for y in up[x]:
            if y not in seen: seen.add(y); dq.append(y)
    return wells

from solver.constraints import ENFORCEABLE


def _choke_stable(result,step_caps):
    """True when a choked solve is a usable operating point: converged, and the choked wells actually deliver (about) their caps."""
    p,q,info,d=result
    if float(info.get('max_abs_residual',0.0))>1e-4: return False
    want=sum(c[1] for c in step_caps.values()); got=sum(float((d.get(w) or {}).get('liquid_rate_m3d',0.0)) for w in step_caps)
    return want<=0 or got>=0.85*want

def enforce_capacity_constraints(nodes, edges, solve, *, max_iterations=8, initial_guess=None):
    """Honour facility/connection rate limits by pro-rata choking of upstream wells.

    Node ``max_liquid_rate_m3d`` (separators, exports...) and connection ``max_rate_m3d``
    limits are enforced; well limits are already enforced inside the well equation.
    Returns (result, capped_nodes, actions).
    """
    ns=deepcopy(nodes); byid={n['id']:n for n in ns}; actions=[]; guess=initial_guess
    result=solve(ns,edges,guess)
    for _ in range(max_iterations):
        p,q,info,d=result
        worst=[c for c in info.get('constraints',[]) if c.get('Status')=='VIOLATED' and c.get('Constraint') in ENFORCEABLE]
        if not worst: break
        changed=False; node_ids={n['id'] for n in ns}; step_caps={}; prev_result=result
        for c in worst:
            # Map the constraint row back to its component (a node capacity or a connection limit).
            comp=c['Component']; cid=c.get('ComponentId')
            node=next((n for n in ns if n['id']==cid and n.get('kind')!='well'),None) if cid in node_ids else None
            edge=None if node else next((e for e in edges if e['id']==cid or (cid is None and e.get('name',e['id'])==comp)),None)
            wells=_upstream_wells(ns,edges,node_id=node['id'] if node else None,edge_id=edge['id'] if edge else None)
            if not wells: continue
            f=max(min(float(c['Limit'])/max(float(c['Value']),1e-9),1.0),0.0)*0.999
            # Only wells that actually contribute to the limited quantity are choked (a water limit leaves dry wells alone).
            key={'Oil capacity':'oil_rate_m3d','Maximum oil rate':'oil_rate_m3d','Water capacity':'water_rate_m3d','Maximum water rate':'water_rate_m3d','Gas capacity':'gas_rate_sm3d','Maximum gas rate':'gas_rate_sm3d'}.get(c['Constraint'])
            for wid in wells:
                qw=float(d.get(wid,{}).get('liquid_rate_m3d',0.0))
                if qw<=1e-6 or (key and float(d.get(wid,{}).get(key,0.0))<=1e-9): continue
                prm=byid[wid].setdefault('params',{}); old=prm.get('_network_cap_m3d')
                new=qw*f if old is None else min(float(old),qw*f)
                if old is not None and new>=float(old)*(1-1e-6): continue     # no tighter than the cap already applied: nothing new to enforce
                step_caps[wid]=(qw if old is None else float(old),new,old); prm['_network_cap_m3d']=new; changed=True
                actions.append({'well':byid[wid].get('name',wid),'well_id':wid,'constraint':f"{comp} {c['Constraint']}",'cap_m3d':new,
                                'message':f"{byid[wid].get('name',wid)} choked to {new:.1f} m3/d to honour {comp} {c['Constraint'].lower()} ({c['Limit']:.0f})."})
        if not changed: break
        guess={'pressures':p,'flows':q,'well_rates':{k:v['liquid_rate_m3d'] for k,v in d.items()}}
        result=solve(ns,edges,guess)
        if step_caps and not _choke_stable(result,step_caps):
            # A steady choke at this rate is not sustainable (typically the flowline loads up at the lower rate and the wells die, or the
            # solve stalls). Back off to the tightest cap that still gives a stable operating point instead of returning a collapsed network.
            lo,hi,best=0.0,1.0,prev_result
            import solver.steady_state as _ss; _old_budget=_ss.RECOVERY_BUDGET_S; _ss.RECOVERY_BUDGET_S=min(_old_budget,4.0)   # trial solves that collapse must fail fast
            for _b in range(5):
                t=0.5*(lo+hi)
                for wid,(a,b,old) in step_caps.items(): byid[wid]['params']['_network_cap_m3d']=a+t*(b-a)
                trial=solve(ns,edges,{'pressures':prev_result[0],'flows':prev_result[1],'well_rates':{k:v['liquid_rate_m3d'] for k,v in prev_result[3].items()}})
                if _choke_stable(trial,{w:(a,a+t*(b-a),o) for w,(a,b,o) in step_caps.items()}): lo=t; best=trial
                else: hi=t
            _ss.RECOVERY_BUDGET_S=_old_budget
            for wid,(a,b,old) in step_caps.items():
                if old is None and lo==0.0: byid[wid]['params'].pop('_network_cap_m3d',None)
                else: byid[wid]['params']['_network_cap_m3d']=a+lo*(b-a)
            result=best
            result[2]['message']=(str(result[2].get('message',''))+f" | Capacity limit cannot be held by steady choking: below about {sum(a+lo*(b-a) for a,b,_ in step_caps.values())/max(sum(a for a,_,_ in step_caps.values()),1e-9)*100:.0f} % of the natural rate the flowline loads up and the wells stop flowing. "
                                   "The network is choked as far as it stays stable; the remaining excess is reported as a violation. Options: raise the limit, add a lower-rate well mix, or accept cycling (the forecast cycles wells to honour the volume).").strip(' |')
            actions.append({'well':'(several)','well_id':None,'constraint':'capacity (stability)','cap_m3d':None,'message':'Choke backed off to the lowest stable rate.'})
            break
        if float(result[2].get('max_abs_residual',0.0))>1e-4:
            result[2]['message']=(str(result[2].get('message',''))+' | Capacity enforcement stopped: the choked network does not converge (limit far below the wells\' natural rate - check separator/compressor limits and line sizes).').strip(' |')
            break
    return result, ns, actions

def split_isolated(nodes, edges):
    """Components with no connection at all (typically just added from the palette) are
    excluded from the solve instead of making the whole network unsolvable."""
    linked=set()
    for e in edges: linked.add(e.get('source')); linked.add(e.get('target'))
    keep=[n for n in nodes if n.get('id') in linked]
    iso=[n for n in nodes if n.get('id') not in linked]
    return keep, iso

def solve_v21(nodes, edges, *, warm_start=None, attempts=3, residual_tolerance=1e-4, enforce_constraints=False):
    """Robust solve with warm starts, variable scaling, retry orchestration and optional
    enforcement of facility capacity limits."""
    from network.reservoir_mb import ensure_tank_links
    from network.equipment import has_inline, expand_inline_equipment, expand_guess, collapse_results
    if has_inline(nodes):  # inline equipment nodes -> inlet/outlet junctions + link, folded back after the solve
        ns_, es_, mp_ = expand_inline_equipment(nodes, edges)
        return collapse_results(solve_v21(ns_, es_, warm_start=expand_guess(warm_start, mp_), attempts=attempts, residual_tolerance=residual_tolerance, enforce_constraints=enforce_constraints), mp_)
    nodes=ensure_tank_links(nodes); all_nodes=nodes
    nodes,isolated=split_isolated(nodes,edges)
    iso_rows=[{'severity':'warning','code':'NOT_CONNECTED','component':n.get('id'),'message':f"{n.get('name',n.get('id'))} has no connections and was excluded from the solve."} for n in isolated if n.get('kind')!='reservoir']  # tanks feed wells by assignment, not by pipes
    if not nodes:
        return {},{},{'success':False,'message':'No connected components to solve.','max_abs_residual':float('inf'),'constraints':[],'violations':0,'debug':iso_rows,'quality_gate':'FAIL','normalized_residual_score':float('inf')},{}
    topo=topology_precheck(nodes,edges)+iso_rows
    if any(x['severity']=='error' for x in topo):
        info={'success':False,'message':'Topology precheck failed: '+'; '.join(x['message'] for x in topo if x['severity']=='error'),'max_abs_residual':float('inf'),'constraints':[],'violations':0,'solver_mode':'v30 Solver','debug':topo,'quality_gate':'FAIL','normalized_residual_score':float('inf'),'topology_failed':True}
        return {},{},info,{}
    def _run(ns, es, guess):
        hist=[]; best=None; g=_clean_guess(ns,es,guess)
        for k in range(max(1,int(attempts))):
            try: p,q,info,d=solve_network_robust(ns,es,x_scale='jac',max_nfev=3000+1000*k,initial_guess=g)
            except ValueError as exc:
                return ({},{},{'success':False,'message':str(exc),'max_abs_residual':float('inf'),'constraints':[],'violations':0},{}),None,hist
            audit=reconstruct_physical_residuals(ns,apply_fluid_follow(es,info),p,q,info.get('injector_rates'))
            score=max(audit['max_pressure_residual_bar']/10.0,audit['max_mass_residual_m3d']/1000.0)
            hist.append({'attempt':k+1,'success':bool(info.get('success')),'scaled_physical_residual':float(score),'nfev':info.get('nfev'),'jacobian_condition':info.get('jacobian_condition')})
            if best is None or score<best[0]: best=(score,p,q,info,d,audit)
            if info.get('success') and audit['max_pressure_residual_bar']<=residual_tolerance*10 and audit['max_mass_residual_m3d']<=residual_tolerance*1000: break
            # Retry from the solved state, with producers re-seeded on their stable branch.
            wr={}
            for n in ns:
                if n.get('kind')=='well' and n['id'] in p: wr[n['id']]=solve_well_rate(p[n['id']],well_settings(n.get('params',{})))[0]
            g={'pressures':p,'flows':q,'well_rates':wr}
        score,p,q,info,d,audit=best
        return (p,q,info,d),audit,hist
    actions=[]; solved_nodes=nodes
    if enforce_constraints:
        store={}
        def _solve(ns,es,guess):
            r,audit,hist=_run(ns,es,guess); store['audit']=audit; store['hist']=store.get('hist',[])+hist; return r
        (p,q,info,d),solved_nodes,actions=enforce_capacity_constraints(nodes,edges,_solve,initial_guess=warm_start)
        audit=store.get('audit'); hist=store.get('hist',[])
    else:
        (p,q,info,d),audit,hist=_run(nodes,edges,warm_start)
    if audit is None:
        info=dict(info); info.update({'solver_mode':'v30 Solver','debug':[{'severity':'error','code':'MODEL_ERROR','message':info.get('message')}],'quality_gate':'FAIL','normalized_residual_score':float('inf'),'attempt_history':hist,'warm_start_used':bool(warm_start)})
        return p,q,info,d
    info=dict(info); info.update({'solver_mode':'v30 Solver / complementarity well model','attempt_history':hist,'physical_residual_audit':audit,'active_constraints':active_constraints(info.get('constraints',[])),'constraint_actions':actions,'constraints_enforced':bool(enforce_constraints)})
    info['debug']=_diagnose(info,audit,topo,residual_tolerance)
    physical_ok=audit['max_pressure_residual_bar']<=residual_tolerance*10 and audit['max_mass_residual_m3d']<=residual_tolerance*1000
    info['quality_gate']='PASS' if info.get('success') and physical_ok else 'FAIL'
    info['physical_quality_gate']='PASS' if physical_ok else 'FAIL'
    info['normalized_residual_score']=float(info.get('max_abs_residual',0.0))/max(float(residual_tolerance),1e-15)
    info['warm_start_used']=bool(warm_start)
    if actions:
        caps={a['well_id']:a['cap_m3d'] for a in actions}
        info['enforced_well_caps_m3d']=caps
    return p,q,info,d
