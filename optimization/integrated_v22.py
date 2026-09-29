"""FieldNet v22 integrated production optimization.

Optimizes only controls that are represented by the steady-state network kernel.
The optimizer is screening/local unless differential_evolution completes its global
population search; no claim of global optimality is made.
"""
from copy import deepcopy
from dataclasses import dataclass, asdict
import math
import numpy as np
from scipy.optimize import differential_evolution
from solver.v21 import solve_v21

@dataclass(frozen=True)
class Decision:
    key: str; component_id: str; component_name: str; kind: str
    lower: float; upper: float; base: float; unit: str='fraction'

def discover_decisions(nodes, edges):
    out=[]
    for n in nodes:
        if n.get('kind')=='well' and n.get('params',{}).get('optimization_enabled',True):
            p=n.get('params',{}); base=float(p.get('opening_factor',1.0))
            out.append(Decision(f"well:{n['id']}:opening",n['id'],n.get('name',n['id']),'well_opening',float(p.get('min_opening_factor',0.05)),float(p.get('max_opening_factor',1.0)),base))
    for e in edges:
        p=e.get('params',{}); k=e.get('kind')
        if k=='control_valve' and p.get('optimization_enabled',True):
            out.append(Decision(f"edge:{e['id']}:opening",e['id'],e.get('name',e['id']),'valve_opening',float(p.get('min_opening',0.05)),float(p.get('max_opening',1.0)),float(p.get('opening',1.0))))
        elif k=='pump' and p.get('optimization_enabled',True):
            out.append(Decision(f"edge:{e['id']}:speed",e['id'],e.get('name',e['id']),'pump_speed',float(p.get('min_speed_fraction',0.6)),float(p.get('max_speed_fraction',1.1)),float(p.get('speed_fraction',1.0))))
        elif k=='compressor' and p.get('map_enabled',False) and p.get('optimization_enabled',True):
            out.append(Decision(f"edge:{e['id']}:speed",e['id'],e.get('name',e['id']),'compressor_speed',float(p.get('min_speed_fraction',0.6)),float(p.get('max_speed_fraction',1.1)),float(p.get('speed_fraction',1.0))))
    return out

def apply_decisions(nodes, edges, decisions, values):
    ns,es=deepcopy(nodes),deepcopy(edges); nm={n['id']:n for n in ns}; em={e['id']:e for e in es}
    for d,v in zip(decisions,values):
        v=min(max(float(v),d.lower),d.upper)
        if d.kind=='well_opening':
            p=nm[d.component_id].setdefault('params',{}); bp=float(p.get('_v22_base_pi',p.get('pi_m3d_bar',10.0))); bq=float(p.get('_v22_base_qmax',p.get('qmax_m3d',1500.0)))
            p['_v22_base_pi']=bp; p['_v22_base_qmax']=bq; p['opening_factor']=v; p['pi_m3d_bar']=bp*v; p['qmax_m3d']=bq*v
        elif d.kind=='valve_opening': em[d.component_id].setdefault('params',{})['opening']=v
        elif d.kind=='compressor_speed': em[d.component_id].setdefault('params',{})['speed_fraction']=v
        elif d.kind=='pump_speed':
            p=em[d.component_id].setdefault('params',{}); h=float(p.get('_v22_base_shutoff_head_bar',p.get('shutoff_head_bar',35.0))); q=float(p.get('_v22_base_rated_rate_m3d',p.get('rated_rate_m3d',1500.0)))
            p['_v22_base_shutoff_head_bar']=h; p['_v22_base_rated_rate_m3d']=q; p['speed_fraction']=v; p['shutoff_head_bar']=h*v*v; p['rated_rate_m3d']=q*v
    return ns,es

def _rate(details): return sum(max(float(x.get('liquid_rate_m3d',0.0)),0.0) for x in details.values())

def evaluate_case(nodes,edges,decisions,values,penalty_weight=1e5):
    ns,es=apply_decisions(nodes,edges,decisions,values)
    try: p,q,info,details=solve_v21(ns,es,attempts=2)
    except Exception as exc: return {'feasible':False,'rate_m3d':0.0,'objective':-1e12,'penalty':1e12,'error':str(exc),'result':None}
    rate=_rate(details); violations=[c for c in info.get('constraints',[]) if c.get('Status')=='VIOLATED']
    physical=info.get('quality_gate')=='PASS'; feasible=bool(info.get('success')) and physical and not violations
    margin_pen=sum(max(-float(c.get('Margin',0.0)),0.0)**2 for c in violations)
    penalty=float(penalty_weight)*(margin_pen + (0 if physical else 1.0) + (0 if info.get('success') else 1.0))
    return {'feasible':feasible,'rate_m3d':rate,'objective':rate-penalty,'penalty':penalty,'violations':violations,'quality_gate':info.get('quality_gate'),'result':(p,q,info,details),'nodes':ns,'edges':es}

def optimize_integrated(nodes,edges,*,maxiter=12,seed=22,penalty_weight=1e5):
    decisions=discover_decisions(nodes,edges)
    unsupported=[]
    if any(n.get('kind') in ('injector','water_injector','gas_injector') for n in nodes): unsupported.append('Injector allocation is not optimized because the current steady-state kernel has no coupled injection source equation.')
    if any(n.get('kind')=='well' and n.get('params',{}).get('lift_type') in ('gas_lift','gas lift') for n in nodes): unsupported.append('Gas-lift allocation remains a v20 nodal screening calculation and is not coupled into the network optimizer.')
    if not decisions: return {'success':False,'feasible':False,'message':'No solver-active optimization controls found.','decisions':[],'controls':{},'unsupported':unsupported}
    base=evaluate_case(nodes,edges,decisions,[d.base for d in decisions],penalty_weight)
    bounds=[(d.lower,d.upper) for d in decisions]
    def f(x): return -evaluate_case(nodes,edges,decisions,x,penalty_weight)['objective']
    opt=differential_evolution(f,bounds,seed=int(seed),maxiter=max(1,int(maxiter)),popsize=5,polish=True,tol=1e-4,workers=1,updating='immediate')
    best=evaluate_case(nodes,edges,decisions,opt.x,penalty_weight)
    controls={d.key:float(v) for d,v in zip(decisions,opt.x)}
    return {'success':best['result'] is not None,'feasible':best['feasible'],'message':str(opt.message),'method':'differential_evolution_screening_v22','global_optimum_guaranteed':False,'base_rate_m3d':base['rate_m3d'],'best_rate_m3d':best['rate_m3d'],'production_gain_m3d':best['rate_m3d']-base['rate_m3d'],'base_feasible':base['feasible'],'controls':controls,'decisions':[asdict(d) for d in decisions],'best':best,'unsupported':unsupported,'seed':int(seed),'maxiter':int(maxiter)}
