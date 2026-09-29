import copy, numpy as np
from optimization.calibration_v23 import Observation, CalParameter, apply_parameters, evaluate_calibration, calibrate
from network.examples import demo_case
from solver.v21 import solve_v21

def test_apply_does_not_mutate():
 n,e=demo_case(); n0=copy.deepcopy(n); p=[CalParameter('x',next(x['id'] for x in n if x['kind']=='well'),'well_pi_mult',.5,2,1)]; apply_parameters(n,e,p,[1.2]); assert n==n0

def test_sigma_validation():
 n,e=demo_case(); wid=next(x['id'] for x in n if x['kind']=='well'); par=[CalParameter('x',wid,'well_pi_mult',.5,2,1)]; oid=n[0]['id']
 try: evaluate_calibration(n,e,par,[1],[Observation('node_pressure_bar',oid,100,0)])
 except ValueError: pass
 else: assert False

def test_synthetic_pressure_calibration_improves_fit():
 n,e=demo_case(); wid=next(x['id'] for x in n if x['kind']=='well'); par=[CalParameter('pi',wid,'well_pi_mult',.5,1.8,1)]
 nt,et=apply_parameters(n,e,par,[1.35]); p,q,info,d=solve_v21(nt,et); obs=[Observation('node_pressure_bar',wid,float(p[wid]),.05), Observation('edge_rate_m3d',e[0]['id'],float(q[e[0]['id']]),.2)]
 base=evaluate_calibration(n,e,par,[1],obs)['weighted_rmse']; r=calibrate(n,e,obs,par,max_nfev=30); assert r['weighted_rmse'] < base

def test_edge_rate_observation_supported():
 n,e=demo_case(); p,q,info,d=solve_v21(n,e); o=Observation('edge_rate_m3d',e[0]['id'],float(q[e[0]['id']]),1); par=[CalParameter('pi',next(x['id'] for x in n if x['kind']=='well'),'well_pi_mult',.8,1.2,1)]; ev=evaluate_calibration(n,e,par,[1], [o]); assert abs(ev['residuals'][0])<1e-7

def test_underdetermined_reported_not_identifiable():
 n,e=demo_case(); wid=next(x['id'] for x in n if x['kind']=='well'); p,q,info,d=solve_v21(n,e); pars=[CalParameter('p1',wid,'well_pi_mult',.8,1.2,1),CalParameter('p2',wid,'well_skin_add',-1,1,0)]; o=[Observation('node_pressure_bar',n[1]['id'],float(p[n[1]['id']]),1)]; r=calibrate(n,e,o,pars,max_nfev=3); assert not r['identifiable_linearized']
