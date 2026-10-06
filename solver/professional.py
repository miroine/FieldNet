"""v14 professional solve orchestration.

Keeps the validated v13 physical residuals as the compatibility kernel while adding
quality gates, warm-start metadata, normalized residual scoring and continuation.
"""
from copy import deepcopy
import numpy as np
from solver.steady_state import solve_network
from solver.constraints import active_constraints
from solver.physical_audit import reconstruct_physical_residuals


def _scales(nodes, edges):
    ps=[abs(float(n.get('pressure_bar'))) for n in nodes if n.get('pressure_bar') is not None]
    qs=[abs(float(e.get('params',{}).get('initial_rate_m3d',0))) for e in edges]
    return {'pressure_bar':max(np.median(ps) if ps else 50.0,1.0),'rate_m3d':max(np.median([q for q in qs if q>0]) if any(q>0 for q in qs) else 500.0,1.0)}


def solve_professional(nodes, edges, residual_tolerance=1e-4, continuation_steps=1):
    """Solve with v13 physics kernel and add v14 engineering quality diagnostics.

    continuation_steps repeats the solve with previous solved pressures/rates used as
    initial guesses. This is useful after large case changes and remains backward compatible.
    """
    ns, es=deepcopy(nodes),deepcopy(edges)
    continuation_steps=max(int(continuation_steps),1)
    result=None
    history=[]
    for i in range(continuation_steps):
        result=solve_network(ns,es)
        p,q,info,d=result
        history.append({'step':i+1,'success':bool(info.get('success')),'max_abs_residual':float(info.get('max_abs_residual',0))})
        for n in ns:
            if n.get('pressure_bar') is None and n['id'] in p:
                n.setdefault('params',{})['initial_pressure_bar']=float(p[n['id']])
        for e in es:
            if e['id'] in q: e.setdefault('params',{})['initial_rate_m3d']=float(q[e['id']])
    p,q,info,d=result
    scales=_scales(nodes,edges)
    maxr=float(info.get('max_abs_residual',0.0))
    info=dict(info)
    info['solver_mode']='v14 professional / v13 compatibility physics kernel'
    info['scales']=scales
    info['normalized_residual_score']=maxr/max(float(residual_tolerance),1e-15)
    info['quality_gate']='PASS' if info.get('success') and maxr<=residual_tolerance else 'FAIL'
    info['continuation_history']=history
    info['active_constraints']=active_constraints(info.get('constraints',[]))
    from solver.steady_state import apply_fluid_follow
    audit=reconstruct_physical_residuals(nodes,apply_fluid_follow(edges,info),p,q)
    info['physical_residual_audit']=audit
    info['physical_quality_gate']='PASS' if audit['max_pressure_residual_bar'] <= residual_tolerance*10.0 and audit['max_mass_residual_m3d'] <= residual_tolerance*1000.0 else 'FAIL'
    if info['physical_quality_gate']!='PASS': info['quality_gate']='FAIL'
    return p,q,info,d
