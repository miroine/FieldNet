"""Finite-difference debottleneck screening for configured capacity constraints."""
from copy import deepcopy
from solver.steady_state import solve_network

def _rate(details): return sum(float(v.get('liquid_rate_m3d',0)) for v in details.values())

def debottleneck_screen(nodes,edges,uplift_fraction=0.10):
    base=solve_network(deepcopy(nodes),deepcopy(edges)); base_rate=_rate(base[3]); rows=[]
    # node liquid capacities
    for n in nodes:
        if n.get('params',{}).get('max_liquid_rate_m3d') is None: continue
        ns=deepcopy(nodes); nn=next(x for x in ns if x['id']==n['id']); old=float(nn['params']['max_liquid_rate_m3d']); nn['params']['max_liquid_rate_m3d']=old*(1+uplift_fraction)
        r=solve_network(ns,deepcopy(edges)); new=_rate(r[3]); rows.append({'Component':n.get('name',n['id']),'Parameter':'Liquid capacity','Base':old,'UpliftFraction':uplift_fraction,'ProductionGain_m3d':new-base_rate,'NewRate_m3d':new})
    for e in edges:
        if e.get('params',{}).get('max_rate_m3d') is None: continue
        es=deepcopy(edges); ee=next(x for x in es if x['id']==e['id']); old=float(ee['params']['max_rate_m3d']); ee['params']['max_rate_m3d']=old*(1+uplift_fraction)
        r=solve_network(deepcopy(nodes),es); new=_rate(r[3]); rows.append({'Component':e.get('name',e['id']),'Parameter':'Connection capacity','Base':old,'UpliftFraction':uplift_fraction,'ProductionGain_m3d':new-base_rate,'NewRate_m3d':new})
    return sorted(rows,key=lambda x:x['ProductionGain_m3d'],reverse=True)
