import math
from physics.well_performance_v20 import *
from physics.unit_system import pressure_to_display,pressure_from_display,liquid_rate_to_display,liquid_rate_from_display,gas_rate_to_display,gas_rate_from_display

def test_pi_inverse():
    assert math.isclose(ipr_bhp_bar(500,200,'PI',10,1000),150,rel_tol=1e-12)

def test_vogel_endpoints():
    assert math.isclose(ipr_bhp_bar(0,200,'Vogel',10,1000),200,rel_tol=1e-12)
    assert abs(ipr_bhp_bar(1000,200,'Vogel',10,1000))<1e-9

def test_gas_lift_monotonic_and_bounded():
    a=gas_lift_assist_bar(0,2000,.09,max_assist_bar=80); b=gas_lift_assist_bar(30000,2000,.09,max_assist_bar=80); c=gas_lift_assist_bar(1e9,2000,.09,max_assist_bar=80)
    assert a==0 and 0<b<c<=80

def test_esp_affinity_head():
    h1=esp_head_bar(0,rated_rate_m3d=1000,shutoff_head_bar=100,speed_fraction=1)
    h2=esp_head_bar(0,rated_rate_m3d=1000,shutoff_head_bar=100,speed_fraction=1.2)
    assert math.isclose(h2/h1,1.44,rel_tol=1e-12)

def test_esp_envelope_and_power():
    r=esp_performance(1000,rated_rate_m3d=1000,shutoff_head_bar=100,fluid_density_kgm3=900,efficiency=.7)
    assert r['within_rate_envelope'] and r['head_bar']>0 and r['hydraulic_power_kw']>0
    assert 'above_recommended_operating_rate' in esp_performance(1400,rated_rate_m3d=1000,shutoff_head_bar=100)['warnings']

def test_nodal_natural_solution():
    r=nodal_operating_point(220,40,1800,.1,ipr_model='PI',pi_m3d_bar=10,water_cut=.1,gor_sm3sm3=80,temperature_c=60)
    assert r['converged'] and r['rate_m3d']>0 and 0<r['bhp_bar']<220

def test_lift_improves_screening_rate():
    kw=dict(reservoir_pressure_bar=220,whp_bar=40,depth_m=1800,tubing_id_m=.1,ipr_model='PI',pi_m3d_bar=10,water_cut=.1,gor_sm3sm3=80,temperature_c=60)
    n=nodal_operating_point(**kw); e=nodal_operating_point(**kw,lift_type='ESP',esp={'rated_rate_m3d':1200,'shutoff_head_bar':50})
    assert n['converged'] and e['converged'] and e['rate_m3d']>n['rate_m3d']

def test_gas_lift_optimizer():
    r=optimize_gas_lift(220,40,1800,.1,pi_m3d_bar=10,max_injection_sm3d=30000,steps=7,water_cut=.1,gor_sm3sm3=80,temperature_c=60)
    assert len(r['candidates'])==7 and r['best'] is not None and r['best']['gas_injection_sm3d']>=0

def test_qa_flags_extreme_drawdown():
    q=well_performance_qa({'converged':True,'bhp_bar':10},200,max_drawdown_fraction=.8)
    assert not q['acceptable'] and 'extreme_drawdown' in q['warnings']

def test_field_si_round_trip_inputs():
    for v,to,fr in [(180,pressure_to_display,pressure_from_display),(1200,liquid_rate_to_display,liquid_rate_from_display),(30000,gas_rate_to_display,gas_rate_from_display)]:
        assert math.isclose(fr(to(v,'field'),'field'),v,rel_tol=1e-12)
