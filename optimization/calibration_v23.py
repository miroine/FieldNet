"""FieldNet v24 calibration/history matching layer.

Outer inverse-model layer around Solver 2.0. It never mutates the supplied case and
keeps fit quality separate from numerical/physical solution quality.
"""
from copy import deepcopy
from dataclasses import dataclass, asdict
import math
import numpy as np
from scipy.optimize import least_squares
from solver.v21 import solve_v21

@dataclass(frozen=True)
class Observation:
    kind: str                 # node_pressure_bar | edge_rate_m3d
    target_id: str
    value: float
    sigma: float
    name: str = ''

@dataclass(frozen=True)
class CalParameter:
    key: str
    target_id: str
    kind: str                 # well_pi_mult | well_skin_add | edge_roughness_mult | pump_head_mult | compressor_ratio_mult
    lower: float
    upper: float
    base: float = 1.0


def discover_parameters(nodes, edges):
    out=[]
    for n in nodes:
        if n.get('kind')=='well':
            p=n.get('params',{})
            out.append(CalParameter(f"well:{n['id']}:pi_mult",n['id'],'well_pi_mult',float(p.get('cal_pi_mult_min',0.25)),float(p.get('cal_pi_mult_max',4.0)),1.0))
            if 'skin' in p:
                out.append(CalParameter(f"well:{n['id']}:skin_add",n['id'],'well_skin_add',float(p.get('cal_skin_add_min',-5.0)),float(p.get('cal_skin_add_max',20.0)),0.0))
    for e in edges:
        p=e.get('params',{}); k=e.get('kind')
        if k in ('pipe','pipeline'):
            out.append(CalParameter(f"edge:{e['id']}:roughness_mult",e['id'],'edge_roughness_mult',0.25,4.0,1.0))
        elif k=='pump': out.append(CalParameter(f"edge:{e['id']}:head_mult",e['id'],'pump_head_mult',0.5,1.5,1.0))
        elif k=='compressor': out.append(CalParameter(f"edge:{e['id']}:ratio_mult",e['id'],'compressor_ratio_mult',0.7,1.3,1.0))
    return out


def apply_parameters(nodes, edges, parameters, values):
    ns,es=deepcopy(nodes),deepcopy(edges); nm={n['id']:n for n in ns}; em={e['id']:e for e in es}
    for par,val in zip(parameters,values):
        v=float(np.clip(val,par.lower,par.upper))
        if par.kind=='well_pi_mult':
            p=nm[par.target_id].setdefault('params',{}); b=float(p.get('_v23_base_pi',p.get('pi_m3d_bar',10.0))); p['_v23_base_pi']=b; p['pi_m3d_bar']=b*v
        elif par.kind=='well_skin_add':
            p=nm[par.target_id].setdefault('params',{}); b=float(p.get('_v23_base_skin',p.get('skin',0.0))); p['_v23_base_skin']=b; p['skin']=b+v
        elif par.kind=='edge_roughness_mult':
            p=em[par.target_id].setdefault('params',{}); key='roughness_m'; b=float(p.get('_v23_base_roughness',p.get(key,4.5e-5))); p['_v23_base_roughness']=b; p[key]=b*v
        elif par.kind=='pump_head_mult':
            p=em[par.target_id].setdefault('params',{}); key='shutoff_head_bar'; b=float(p.get('_v23_base_head',p.get(key,35.0))); p['_v23_base_head']=b; p[key]=b*v
        elif par.kind=='compressor_ratio_mult':
            p=em[par.target_id].setdefault('params',{}); key='pressure_ratio'; b=float(p.get('_v23_base_ratio',p.get(key,1.5))); p['_v23_base_ratio']=b; p[key]=max(1.0,b*v)
    return ns,es


def _predictions(nodes, edges, observations):
    p,q,info,details=solve_v21(nodes,edges,attempts=2)
    if not info.get('success') or info.get('quality_gate')!='PASS': raise RuntimeError('Trial solve did not pass Solver 2.0 physical quality gate')
    node_ids=[n['id'] for n in nodes]; edge_ids=[e['id'] for e in edges]; vals=[]
    for o in observations:
        if o.kind=='node_pressure_bar': vals.append(float(p[o.target_id]))
        elif o.kind=='edge_rate_m3d': vals.append(float(q[o.target_id]))
        else: raise ValueError(f'Unsupported observation kind: {o.kind}')
    return np.asarray(vals), (p,q,info,details)


def evaluate_calibration(nodes,edges,parameters,values,observations):
    ns,es=apply_parameters(nodes,edges,parameters,values)
    pred,result=_predictions(ns,es,observations); obs=np.asarray([o.value for o in observations],float); sig=np.asarray([o.sigma for o in observations],float)
    if np.any(sig<=0): raise ValueError('Observation sigma must be > 0')
    r=(pred-obs)/sig
    return {'residuals':r,'predicted':pred,'observed':obs,'weighted_rmse':float(np.sqrt(np.mean(r*r))) if len(r) else 0.0,'nodes':ns,'edges':es,'result':result}


def calibrate(nodes,edges,observations,parameters=None,*,max_nfev=80):
    observations=[o if isinstance(o,Observation) else Observation(**o) for o in observations]
    if not observations: raise ValueError('At least one observation is required')
    parameters=list(parameters or discover_parameters(nodes,edges))
    if not parameters: raise ValueError('No calibration parameters available')
    x0=np.asarray([p.base for p in parameters],float); lo=np.asarray([p.lower for p in parameters],float); hi=np.asarray([p.upper for p in parameters],float)
    failures={'count':0}
    def fun(x):
        try: return evaluate_calibration(nodes,edges,parameters,x,observations)['residuals']
        except Exception:
            failures['count']+=1; return np.full(len(observations),1e4)
    opt=least_squares(fun,x0,bounds=(lo,hi),max_nfev=max(1,int(max_nfev)),x_scale='jac')
    ev=evaluate_calibration(nodes,edges,parameters,opt.x,observations)
    # Linearized covariance is diagnostic only; rank deficiency is reported, never hidden.
    j=np.asarray(opt.jac,float); rank=int(np.linalg.matrix_rank(j)); cond=float(np.linalg.cond(j)) if j.size else math.inf
    dof=max(len(observations)-len(parameters),0); covariance=None; std=None
    if j.size and rank==len(parameters) and dof>0:
        s2=float(np.sum(opt.fun**2)/dof); covariance=np.linalg.pinv(j.T@j)*s2; std=np.sqrt(np.maximum(np.diag(covariance),0.0))
    at_bounds=[bool(abs(v-p.lower)<=1e-6*max(1,abs(p.lower)) or abs(v-p.upper)<=1e-6*max(1,abs(p.upper))) for p,v in zip(parameters,opt.x)]
    identifiable=(rank==len(parameters) and len(observations)>len(parameters))
    return {'success':bool(opt.success),'message':str(opt.message),'method':'bounded_weighted_least_squares_v23','weighted_rmse':ev['weighted_rmse'],'parameters':[asdict(p) for p in parameters],'values':{p.key:float(v) for p,v in zip(parameters,opt.x)},'parameter_std':None if std is None else {p.key:float(s) for p,s in zip(parameters,std)},'at_bounds':{p.key:b for p,b in zip(parameters,at_bounds)},'jacobian_rank':rank,'jacobian_condition':cond,'identifiable_linearized':identifiable,'trial_failures':failures['count'],'observations':[asdict(o) for o in observations],'predicted':[float(x) for x in ev['predicted']],'normalized_residuals':[float(x) for x in ev['residuals']],'calibrated_nodes':ev['nodes'],'calibrated_edges':ev['edges'],'solver_info':ev['result'][2],'limitations':['Parameter uncertainty is a local linearized diagnostic, not a Bayesian posterior.','A low residual does not prove parameter uniqueness or physical correctness.','Calibration is only as representative as the supplied measurements and model physics.']}
