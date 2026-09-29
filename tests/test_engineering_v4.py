from physics.equipment import pump_head_bar, pump_power_kw, compressor_discharge_bar
from solver.constraints import evaluate_constraints
from solver.steady_state import solve_network
from network.examples import demo_case

def test_pump_curve_falls_with_rate():
    assert pump_head_bar(0,40,1000) > pump_head_bar(500,40,1000) > pump_head_bar(900,40,1000)

def test_pump_power_positive():
    assert pump_power_kw(1000,20,850,.75) > 0

def test_compressor_discharge_limited():
    assert compressor_discharge_bar(100,3,220)==220

def test_constraint_violation_detection():
    nodes=[{'id':'s','kind':'separator','name':'SEP','pressure_bar':30,'params':{'max_liquid_rate_m3d':100}}]
    edges=[{'id':'e','source':'x','target':'s','params':{}}]
    rows=evaluate_constraints(nodes,edges,{'s':30},{'e':150},{})
    assert rows[0]['Status']=='VIOLATED'

def test_v3_demo_still_converges():
    n,e=demo_case(); p,q,info,d=solve_network(n,e)
    assert info['success'] and info['max_abs_residual'] < 1e-4

def test_pump_network_converges():
    nodes=[{'id':'a','kind':'manifold','name':'A','pressure_bar':20.0,'params':{}},{'id':'b','kind':'sink','name':'B','pressure_bar':40.0,'params':{}}]
    edges=[{'id':'p','source':'a','target':'b','kind':'pump','params':{'shutoff_head_bar':30,'rated_rate_m3d':1000,'initial_rate_m3d':500},'length_m':0,'diameter_m':.1,'roughness_m':0,'elevation_change_m':0}]
    p,q,info,d=solve_network(nodes,edges)
    assert info['success'] and q['p'] > 0 and info['equipment'][0]['Power [kW]'] > 0
