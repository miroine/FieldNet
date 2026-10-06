from copy import deepcopy
import numpy as np
from scipy.optimize import differential_evolution
from solver.steady_state import solve_network_robust as solve_network

def total_well_rate(details): return sum(v.get('liquid_rate_m3d',0.0) for v in details.values())

def optimize_allocation(nodes, edges, maxiter=18, seed=7):
    wells=[n for n in nodes if n.get('kind')=='well']
    if not wells: return {'success':False,'message':'No wells','best_rate_m3d':0,'controls':{},'result':None}
    # v5 control variable = per-well productivity availability multiplier, analogous to a bounded well opening.
    bounds=[(0.05,1.0)]*len(wells)
    def run(x):
        ns=deepcopy(nodes)
        for n,f in zip([z for z in ns if z.get('kind')=='well'],x):
            p=n.setdefault('params',{}); p['_base_pi']=p.get('_base_pi',p.get('pi_m3d_bar',10.0)); p['_base_qmax']=p.get('_base_qmax',p.get('qmax_m3d',1500.0)); p['pi_m3d_bar']=p['_base_pi']*f; p['qmax_m3d']=p['_base_qmax']*f
        try: result=solve_network(ns,deepcopy(edges))
        except Exception: return None
        return result
    def obj(x):
        r=run(x)
        if r is None:return 1e9
        p,q,info,d=r; rate=total_well_rate(d)
        penalty=sum(max(-c['Margin'],0.0)**2 for c in info.get('constraints',[]))*1000
        penalty += max(info.get('max_abs_residual',0)-1e-4,0)*1e7
        return -rate+penalty
    opt=differential_evolution(obj,bounds,seed=seed,maxiter=maxiter,popsize=6,polish=True,tol=1e-4)
    result=run(opt.x); controls={w['id']:float(f) for w,f in zip(wells,opt.x)}
    return {'success':bool(opt.success or result is not None),'message':str(opt.message),'best_rate_m3d':total_well_rate(result[3]) if result else 0,'controls':controls,'result':result}

def bottlenecks(info, top_n=8):
    rows=[]
    for c in info.get('constraints',[]):
        lim=abs(c.get('Limit',0.0)); margin=c.get('Margin',0.0); utilization=None
        if lim>1e-12:
            utilization=1.0-margin/lim
        rows.append({**c,'Utilization':utilization,'Severity':max(-margin,0.0)})
    rows.sort(key=lambda r:(r['Status']!='VIOLATED', -(r['Utilization'] if r['Utilization'] is not None else -1e9)))
    return rows[:top_n]
