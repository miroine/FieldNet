from copy import deepcopy
from solver.professional import solve_professional

def _case_one(args):
    nodes,edges,case=args
    ns,es=deepcopy(nodes),deepcopy(edges)
    for change in case.get('changes',[]):
        coll=ns if change.get('target_type','node')=='node' else es
        obj=next(x for x in coll if x['id']==change['target_id'])
        field=change['field']; value=change['value']
        if field in obj: obj[field]=value
        else: obj.setdefault('params',{})[field]=value
    try:
        p,q,info,d=solve_professional(ns,es)
        return {'Scenario':case.get('name','Case'),'Total liquid [m3/d]':sum(v.get('liquid_rate_m3d',0) for v in d.values()),'Violations':info.get('violations',0),'Quality':info.get('quality_gate'),'Residual':info.get('max_abs_residual',0)}
    except Exception as exc: return {'Scenario':case.get('name','Case'),'Error':str(exc),'Quality':'FAIL'}

def run_cases(nodes,edges,cases,workers=1):
    """``workers>1`` runs the cases in parallel processes (same results/order as serial)."""
    items=[(nodes,edges,c) for c in cases]
    if workers and workers>1 and len(items)>1:
        from network.uncertainty import parallel_map
        return parallel_map(_case_one,items,workers)
    return [_case_one(a) for a in items]
