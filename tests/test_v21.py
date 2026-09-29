import math
from solver.v21 import solve_v21, topology_precheck
from network.examples import demo_case

def test_v21_topology_detects_dangling_edge():
    n,e=demo_case(); e[0]['source']='missing'
    assert any(x['code']=='DANGLING_EDGE' for x in topology_precheck(n,e))

def test_v21_demo_converges_with_physical_gate():
    n,e=demo_case(); p,q,i,d=solve_v21(n,e)
    assert i['success'] and i['quality_gate']=='PASS'
    assert i['physical_residual_audit']['max_pressure_residual_bar'] < 1e-3
    assert i['physical_residual_audit']['max_mass_residual_m3d'] < 0.1

def test_v21_warm_start_reproduces_solution():
    n,e=demo_case(); p,q,i,d=solve_v21(n,e)
    p2,q2,i2,d2=solve_v21(n,e,warm_start={'pressures':p,'flows':q})
    assert i2['warm_start_used']
    for k in p: assert math.isclose(p[k],p2[k],rel_tol=1e-6,abs_tol=1e-5)
    for k in q: assert math.isclose(q[k],q2[k],rel_tol=1e-6,abs_tol=1e-4)

def test_v21_reports_solver_metrics():
    n,e=demo_case(); _,_,i,_=solve_v21(n,e)
    assert i['nfev']>0 and 'jacobian_condition' in i and 'attempt_history' in i and 'debug' in i

def test_v21_unanchored_component_fails_before_nonlinear_solve():
    n=[{'id':'a','kind':'manifold','pressure_bar':None},{'id':'b','kind':'manifold','pressure_bar':None}]
    e=[{'id':'x','source':'a','target':'b','kind':'pipeline','length_m':100,'diameter_m':.1,'roughness_m':1e-5,'elevation_change_m':0,'params':{}}]
    p,q,i,d=solve_v21(n,e)
    assert not i['success'] and any(x['code']=='UNANCHORED_COMPONENT' for x in i['debug'])
