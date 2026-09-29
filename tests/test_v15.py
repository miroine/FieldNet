import math
from physics.well_completion import radial_pi_m3d_bar, non_darcy_skin, completion_deliverability, combine_completion_intervals
from physics.multiphase_v15 import beggs_brill_components_bar, detailed_pressure_profile
from physics.equipment_maps_v15 import pump_map_2d, compressor_map_2d, compressor_envelope, pump_envelope

def test_radial_pi_positive_and_skin_reduces_pi():
    a=radial_pi_m3d_bar(100,20,1.2,1.2,300,.1,0)
    b=radial_pi_m3d_bar(100,20,1.2,1.2,300,.1,5)
    assert a>0 and b<a

def test_non_darcy_skin_rate_dependence():
    assert non_darcy_skin(100,0.01)==1.0
    r=completion_deliverability(100,250,k_md=100,h_m=20,d_factor_per_m3d=.01)
    assert r['non_darcy_skin']==1.0 and r['pwf_bar']<250

def test_parallel_completion_intervals_sum_pi():
    x=dict(k_md=100,h_m=10,mu_cp=1.0,bo=1.2,re_m=300,rw_m=.1,skin=1)
    r=combine_completion_intervals([x,x])
    assert math.isclose(r['pi_m3d_bar'],2*r['interval_pi_m3d_bar'][0])

def test_multiphase_component_closure():
    r=beggs_brill_components_bar(500,1000,.15,1e-5,50,80,60,.3,80,35,.75)
    assert math.isclose(r['total_dp_bar'],r['friction_dp_bar']+r['hydrostatic_dp_bar']+r['acceleration_dp_bar'],rel_tol=1e-10,abs_tol=1e-10)

def test_detailed_profile_closure():
    r=detailed_pressure_profile(400,800,.15,1e-5,40,100,60,.2,60,segments=8)
    assert len(r['segments'])==8
    assert math.isclose(r['total_dp_bar'],r['friction_dp_bar']+r['hydrostatic_dp_bar'],rel_tol=1e-9,abs_tol=1e-9)

def test_pump_2d_speed_interpolation():
    lines=[{'speed_pct':80,'points':[{'rate_m3d':0,'head_bar':40,'efficiency':.6},{'rate_m3d':100,'head_bar':20,'efficiency':.7}]},
           {'speed_pct':100,'points':[{'rate_m3d':0,'head_bar':60,'efficiency':.65},{'rate_m3d':100,'head_bar':30,'efficiency':.75}]}]
    r=pump_map_2d(50,90,lines)
    assert r['in_envelope'] and 30<r['head_bar']<60

def test_compressor_2d_and_envelope():
    lines=[{'speed_pct':80,'points':[{'rate_sm3d':1000,'pressure_ratio':1.5,'efficiency':.7},{'rate_sm3d':2000,'pressure_ratio':1.3,'efficiency':.72}]},
           {'speed_pct':100,'points':[{'rate_sm3d':1000,'pressure_ratio':2.0,'efficiency':.75},{'rate_sm3d':2000,'pressure_ratio':1.6,'efficiency':.78}]}]
    r=compressor_map_2d(1500,90,lines)
    assert r['in_envelope'] and 1.3<r['pressure_ratio']<2.0
    assert compressor_envelope(1500,1000,2000,900,1000)['in_envelope']
    assert not compressor_envelope(900,1000,2000)['in_envelope']

def test_pump_npsh_envelope():
    assert pump_envelope(50,10,100,8,5)['in_envelope']
    assert not pump_envelope(50,10,100,4,5)['in_envelope']

def test_v15_audit_identity():
    from network.examples import demo_case
    from solver.professional import solve_professional
    from solver.audit_report import calculation_audit
    n,e=demo_case(); p,q,i,d=solve_professional(n,e); a=calculation_audit(n,e,i,d)
    assert a['application']=='FieldNet v29' and a['author']=='Merouane Hamdani'
