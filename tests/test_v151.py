import math
import pytest
from physics.equipment_maps_v15 import pump_map_2d, compressor_map_2d
from physics.well_completion import radial_pi_m3d_bar, completion_deliverability
from physics.multiphase_v15 import beggs_brill_components_bar

PUMP_LINES=[
 {'speed_pct':80,'points':[{'rate_m3d':0,'head_bar':40,'efficiency':.60},{'rate_m3d':100,'head_bar':20,'efficiency':.70}]},
 {'speed_pct':100,'points':[{'rate_m3d':0,'head_bar':60,'efficiency':.65},{'rate_m3d':100,'head_bar':30,'efficiency':.75}]},
]
COMP_LINES=[
 {'speed_pct':80,'points':[{'rate_sm3d':1000,'pressure_ratio':1.5,'efficiency':.70},{'rate_sm3d':2000,'pressure_ratio':1.3,'efficiency':.72}]},
 {'speed_pct':100,'points':[{'rate_sm3d':1000,'pressure_ratio':2.0,'efficiency':.75},{'rate_sm3d':2000,'pressure_ratio':1.6,'efficiency':.78}]},
]

def test_map_inside_reports_interpolated_not_extrapolated():
    r=pump_map_2d(50,90,PUMP_LINES)
    assert r['in_envelope'] and r['map_status']=='interpolated'
    assert not r['extrapolated'] and not r['speed_clamped'] and r['extrapolation_reasons']==[]

def test_speed_outside_map_is_explicit_and_clamped():
    r=pump_map_2d(50,110,PUMP_LINES)
    assert not r['in_envelope'] and r['extrapolated'] and r['speed_clamped']
    assert r['evaluated_speed_pct']==100 and 'speed_above_map' in r['extrapolation_reasons']

def test_flow_outside_map_is_explicit():
    r=compressor_map_2d(2500,90,COMP_LINES)
    assert not r['flow_in_envelope'] and r['extrapolated']
    assert 'flow_outside_map' in r['extrapolation_reasons']

def test_invalid_speed_line_rejected():
    with pytest.raises(ValueError):
        pump_map_2d(10,90,[{'speed_pct':80,'points':[{'rate_m3d':0,'head_bar':1,'efficiency':.5}]},{'speed_pct':100,'points':[]}])

def test_zero_skin_and_zero_non_darcy_are_limiting_cases():
    pi=radial_pi_m3d_bar(100,20,1.2,1.2,300,.1,0)
    r=completion_deliverability(0,250,k_md=100,h_m=20,skin=0,d_factor_per_m3d=0)
    assert pi>0 and r['non_darcy_skin']==0 and math.isclose(r['pwf_bar'],250.0,abs_tol=1e-12)

def test_pressure_component_closure_v151():
    r=beggs_brill_components_bar(500,1000,.15,1e-5,50,80,60,.3,80,35,.75)
    assert math.isclose(r['total_dp_bar'],r['friction_dp_bar']+r['hydrostatic_dp_bar']+r['acceleration_dp_bar'],rel_tol=1e-12,abs_tol=1e-12)

def test_v151_audit_identity():
    from network.examples import demo_case
    from solver.professional import solve_professional
    from solver.audit_report import calculation_audit
    n,e=demo_case(); p,q,i,d=solve_professional(n,e); a=calculation_audit(n,e,i,d)
    assert a['application']=='FieldNet v29.1' and a['author']=='Merouane Hamdani'
