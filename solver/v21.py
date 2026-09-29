"""FieldNet v21 Network Solver 2.0 orchestration and diagnostics.

The validated steady-state physics remains the equation kernel. v21 strengthens
numerical orchestration, warm starts, topology checks and explainable diagnostics.
"""
from copy import deepcopy
import math
from solver.steady_state import solve_network
from solver.physical_audit import reconstruct_physical_residuals
from solver.constraints import active_constraints

LINK_KINDS={'pipeline','choke','control_valve','pump','compressor'}

def topology_precheck(nodes, edges):
    ids=[n.get('id') for n in nodes]; issues=[]
    if len(ids)!=len(set(ids)): issues.append({'severity':'error','code':'DUPLICATE_NODE_ID','message':'Duplicate node IDs make the equation system ambiguous.'})
    known=set(ids); adj={i:set() for i in known}
    for e in edges:
        if e.get('source') not in known or e.get('target') not in known:
            issues.append({'severity':'error','code':'DANGLING_EDGE','component':e.get('id'),'message':'Connection references a missing node.'}); continue
        if e.get('source')==e.get('target'): issues.append({'severity':'error','code':'SELF_LOOP','component':e.get('id'),'message':'Connection starts and ends at the same node.'})
        adj[e['source']].add(e['target']); adj[e['target']].add(e['source'])
    # Each connected component needs at least one pressure/boundary anchor.
    seen=set()
    byid={n['id']:n for n in nodes}
    for root in known:
        if root in seen: continue
        stack=[root]; comp=[]; seen.add(root)
        while stack:
            x=stack.pop(); comp.append(x)
            for y in adj[x]:
                if y not in seen: seen.add(y); stack.append(y)
        anchored=any(byid[x].get('pressure_bar') is not None or byid[x].get('kind') in ('reservoir','sink') for x in comp)
        if not anchored: issues.append({'severity':'error','code':'UNANCHORED_COMPONENT','component':','.join(comp),'message':'Connected component has no pressure boundary/reservoir anchor.'})
    return issues

def _apply_warm_start(nodes, edges, warm):
    ns,es=deepcopy(nodes),deepcopy(edges)
    if not warm: return ns,es
    p=warm.get('pressures',{}); q=warm.get('flows',{})
    for n in ns:
        if n.get('pressure_bar') is None and n['id'] in p: n.setdefault('params',{})['initial_pressure_bar']=float(p[n['id']])
    for e in es:
        if e['id'] in q: e.setdefault('params',{})['initial_rate_m3d']=max(abs(float(q[e['id']])),0.01)
    return ns,es

def _diagnose(info,audit,topology,tol):
    rows=list(topology)
    if not info.get('success'): rows.append({'severity':'error','code':'NONCONVERGED','message':info.get('message','Nonlinear solve failed.')})
    for r in sorted(audit.get('edge_residuals',[]),key=lambda x:abs(x['pressure_residual_bar']),reverse=True)[:5]:
        if abs(r['pressure_residual_bar'])>tol*10: rows.append({'severity':'error','code':'PRESSURE_EQUATION','component':r['id'],'value':r['pressure_residual_bar'],'message':'Physical pressure equation has a material residual.'})
    for r in sorted(audit.get('node_residuals',[]),key=lambda x:abs(x['mass_residual_m3d']),reverse=True)[:5]:
        if abs(r['mass_residual_m3d'])>tol*1000: rows.append({'severity':'error','code':'MASS_BALANCE','component':r['id'],'value':r['mass_residual_m3d'],'message':'Node mass balance has a material residual.'})
    jc=float(info.get('jacobian_condition',float('nan')))
    if math.isfinite(jc) and jc>1e10: rows.append({'severity':'warning','code':'ILL_CONDITIONED','value':jc,'message':'Jacobian is highly ill-conditioned; solution may be sensitive to perturbations.'})
    if info.get('optimality',0)>1e-3: rows.append({'severity':'warning','code':'STAGNATION','value':info['optimality'],'message':'First-order optimality remains high; nonlinear iteration may have stagnated.'})
    for c in info.get('constraints',[]):
        if c.get('Status')=='VIOLATED': rows.append({'severity':'warning','code':'OPERATING_LIMIT','component':c.get('Component'),'message':str(c)})
    if not rows: rows=[{'severity':'ok','code':'OK','message':'Topology, convergence, physical residuals and conditioning checks passed.'}]
    return rows

def solve_v21(nodes, edges, *, warm_start=None, attempts=3, residual_tolerance=1e-4):
    """Robust v21 solve with warm starts, variable scaling and retry orchestration."""
    topo=topology_precheck(nodes,edges)
    if any(x['severity']=='error' for x in topo):
        info={'success':False,'message':'Topology precheck failed','max_abs_residual':float('inf'),'constraints':[],'violations':0,'solver_mode':'v21 Solver 2.0','debug':topo,'quality_gate':'FAIL'}
        return {},{},info,{}
    ns,es=_apply_warm_start(nodes,edges,warm_start)
    hist=[]; best=None
    for k in range(max(1,int(attempts))):
        # First attempt uses Jacobian variable scaling. Later attempts warm-start from the previous result.
        p,q,info,d=solve_network(ns,es,x_scale='jac',max_nfev=3000+1000*k)
        audit=reconstruct_physical_residuals(nodes,edges,p,q)
        score=max(audit['max_pressure_residual_bar']/10.0,audit['max_mass_residual_m3d']/1000.0)
        hist.append({'attempt':k+1,'success':bool(info.get('success')),'scaled_physical_residual':float(score),'nfev':info.get('nfev'),'jacobian_condition':info.get('jacobian_condition')})
        if best is None or score<best[0]: best=(score,p,q,info,d,audit)
        if info.get('success') and audit['max_pressure_residual_bar']<=residual_tolerance*10 and audit['max_mass_residual_m3d']<=residual_tolerance*1000: break
        ns,es=_apply_warm_start(nodes,edges,{'pressures':p,'flows':q})
    score,p,q,info,d,audit=best
    info=dict(info); info.update({'solver_mode':'v21 Solver 2.0 / validated steady-state physics kernel','attempt_history':hist,'physical_residual_audit':audit,'active_constraints':active_constraints(info.get('constraints',[]))})
    info['debug']=_diagnose(info,audit,topo,residual_tolerance)
    physical_ok=audit['max_pressure_residual_bar']<=residual_tolerance*10 and audit['max_mass_residual_m3d']<=residual_tolerance*1000
    info['quality_gate']='PASS' if info.get('success') and physical_ok else 'FAIL'
    info['warm_start_used']=bool(warm_start)
    return p,q,info,d
