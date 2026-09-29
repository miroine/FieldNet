import numpy as np
from copy import deepcopy
from solver.steady_state import solve_network

def run_sensitivity(nodes,edges,target_type,target_id,param,values):
    out=[]
    for val in values:
        ns,es=deepcopy(nodes),deepcopy(edges)
        collection=ns if target_type=='node' else es
        obj=next(x for x in collection if x['id']==target_id)
        if param in obj: obj[param]=val
        else: obj.setdefault('params',{})[param]=val
        try:
            p,q,info,d=solve_network(ns,es); total=sum(x.get('liquid_rate_m3d',0) for x in d.values())
            out.append({'Value':float(val),'Total liquid [m3/d]':total,'Violations':info.get('violations',0),'Residual':info.get('max_abs_residual',0),'Converged':info.get('success',False)})
        except Exception as exc: out.append({'Value':float(val),'Total liquid [m3/d]':np.nan,'Violations':-1,'Residual':np.nan,'Converged':False,'Error':str(exc)})
    return out
