import numpy as np
from copy import deepcopy
from solver.steady_state import solve_network_robust as solve_network

def _sens_one(args):
    nodes,edges,target_type,target_id,param,val=args
    ns,es=deepcopy(nodes),deepcopy(edges)
    collection=ns if target_type=='node' else es
    obj=next(x for x in collection if x['id']==target_id)
    if param in obj: obj[param]=val
    else: obj.setdefault('params',{})[param]=val
    try:
        p,q,info,d=solve_network(ns,es); total=sum(x.get('liquid_rate_m3d',0) for x in d.values())
        return {'Value':float(val),'Total liquid [m3/d]':total,'Violations':info.get('violations',0),'Residual':info.get('max_abs_residual',0),'Converged':info.get('success',False)}
    except Exception as exc: return {'Value':float(val),'Total liquid [m3/d]':np.nan,'Violations':-1,'Residual':np.nan,'Converged':False,'Error':str(exc)}

def run_sensitivity(nodes,edges,target_type,target_id,param,values,workers=1):
    """``workers>1`` runs the values in parallel processes (same results/order as serial)."""
    items=[(nodes,edges,target_type,target_id,param,v) for v in values]
    if workers and workers>1 and len(items)>1:
        from network.uncertainty import parallel_map
        return parallel_map(_sens_one,items,workers)
    return [_sens_one(a) for a in items]
