import pytest
from physics.advanced_wells import skin_adjusted_pi, artificial_lift_assist_bar, effective_vlp_bhp_bar
from physics.performance_maps import pump_map, compressor_map
from solver.constraints import active_constraints

def test_skin_adjusted_productivity():
    assert skin_adjusted_pi(10,0)==pytest.approx(10)
    assert skin_adjusted_pi(10,1)==pytest.approx(5)
    assert skin_adjusted_pi(10,4)<skin_adjusted_pi(10,1)

def test_artificial_lift_assistance():
    assert artificial_lift_assist_bar('none',40)==0
    assert artificial_lift_assist_bar('ESP',40)==40
    assert effective_vlp_bhp_bar(120,40)==80

def test_pump_map_interpolation_and_envelope():
    pts=[{'rate_m3d':0,'head_bar':50,'efficiency':.6},{'rate_m3d':1000,'head_bar':30,'efficiency':.8}]
    r=pump_map(500,pts); assert r['head_bar']==pytest.approx(40); assert r['efficiency']==pytest.approx(.7); assert r['in_envelope']
    assert not pump_map(1500,pts)['in_envelope']

def test_compressor_map_interpolation_and_envelope():
    pts=[{'rate_sm3d':100000,'pressure_ratio':2.0,'efficiency':.7},{'rate_sm3d':200000,'pressure_ratio':1.6,'efficiency':.8}]
    r=compressor_map(150000,pts); assert r['pressure_ratio']==pytest.approx(1.8); assert r['efficiency']==pytest.approx(.75); assert r['in_envelope']
    assert not compressor_map(250000,pts)['in_envelope']

def test_active_constraint_identification():
    rows=[{'Component':'A','Constraint':'cap','Limit':100,'Margin':2,'Status':'OK'}, {'Component':'B','Constraint':'min','Limit':100,'Margin':20,'Status':'OK'}, {'Component':'C','Constraint':'cap','Limit':100,'Margin':-1,'Status':'VIOLATED'}]
    a=active_constraints(rows,.05); assert [x['Component'] for x in a]==['C','A']
