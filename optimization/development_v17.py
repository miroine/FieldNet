"""Screening-level bounded development decision optimization for FieldNet v17."""
from __future__ import annotations
import copy
from dataclasses import dataclass
from typing import Callable
import numpy as np
from scipy.optimize import differential_evolution
from network.field_development import DevelopmentScenario, run_development_scenario

@dataclass(frozen=True)
class DecisionVariable:
    name: str
    target_id: str
    path: str
    low: float
    high: float

def _set(nodes,edges,d:DecisionVariable,value:float):
    obj=next((x for x in [*nodes,*edges] if str(x.get('id'))==str(d.target_id)),None)
    if obj is None: raise ValueError(f"Unknown decision target_id: {d.target_id}")
    cur=obj; parts=d.path.split('.')
    for p in parts[:-1]: cur=cur.setdefault(p,{})
    cur[parts[-1]]=float(value)

def optimize_development(nodes,edges,scenario:DevelopmentScenario,decisions:list[DecisionVariable],objective:str='cumulative_oil_m3',maxiter:int=20,forecast_runner:Callable|None=None):
    if not decisions: raise ValueError('At least one decision variable is required')
    for d in decisions:
        if d.high < d.low: raise ValueError(f'{d.name}: high must be >= low')
    def evaluate(x):
        nn,ee=copy.deepcopy(nodes),copy.deepcopy(edges)
        for d,v in zip(decisions,x): _set(nn,ee,d,v)
        kwargs={} if forecast_runner is None else {'forecast_runner':forecast_runner}
        r=run_development_scenario(nn,ee,copy.deepcopy(scenario),**kwargs)
        return -float(r['kpis'].get(objective,0.0))
    res=differential_evolution(evaluate,[(d.low,d.high) for d in decisions],seed=1701,maxiter=max(1,int(maxiter)),polish=True,workers=1,updating='immediate')
    return {'application':'FieldNet v21','success':bool(res.success),'feasible':bool(np.isfinite(res.fun)),'message':str(res.message),'objective':objective,'objective_value':float(-res.fun),'decisions':{d.name:float(v) for d,v in zip(decisions,res.x)},'evaluations':int(res.nfev)}
