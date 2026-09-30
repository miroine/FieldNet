"""Finite-difference debottleneck screening for configured capacity constraints.

Each capacity is raised by ``uplift_fraction`` and the network is re-solved *with the
capacity limits enforced* (pro-rata well choking). The previous version re-solved with the
raw kernel, which only reports limits, so every uplift showed exactly zero gain.
"""
from copy import deepcopy
from solver.v21 import solve_v21

def _rate(details): return sum(float(v.get('liquid_rate_m3d',0)) for v in details.values())

def _solve(nodes,edges):
    return solve_v21(deepcopy(nodes),deepcopy(edges),attempts=2,enforce_constraints=True)

def debottleneck_screen(nodes,edges,uplift_fraction=0.10):
    base=_solve(nodes,edges); base_rate=_rate(base[3]); rows=[]
    for n in nodes:
        if (n.get('params') or {}).get('max_liquid_rate_m3d') is None: continue
        ns=deepcopy(nodes); nn=next(x for x in ns if x['id']==n['id']); old=float(nn['params']['max_liquid_rate_m3d']); nn['params']['max_liquid_rate_m3d']=old*(1+uplift_fraction)
        r=_solve(ns,edges); new=_rate(r[3]); rows.append({'Component':n.get('name',n['id']),'Parameter':'Liquid capacity','Base':old,'UpliftFraction':uplift_fraction,'ProductionGain_m3d':new-base_rate,'NewRate_m3d':new,'Quality':r[2].get('quality_gate')})
    for e in edges:
        if (e.get('params') or {}).get('max_rate_m3d') is None: continue
        es=deepcopy(edges); ee=next(x for x in es if x['id']==e['id']); old=float(ee['params']['max_rate_m3d']); ee['params']['max_rate_m3d']=old*(1+uplift_fraction)
        r=_solve(nodes,es); new=_rate(r[3]); rows.append({'Component':e.get('name',e['id']),'Parameter':'Connection capacity','Base':old,'UpliftFraction':uplift_fraction,'ProductionGain_m3d':new-base_rate,'NewRate_m3d':new,'Quality':r[2].get('quality_gate')})
    return sorted(rows,key=lambda x:x['ProductionGain_m3d'],reverse=True)
