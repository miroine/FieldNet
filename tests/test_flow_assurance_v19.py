import math
import pytest
from physics.flow_assurance import *
from physics.unit_system import velocity_to_display,velocity_from_display,heat_transfer_u_to_display,heat_transfer_u_from_display

def test_thermal_reference_and_limits():
    # analytical NTU=1 case
    m=2.; cp=2000.; d=.1; L=1000.; u=m*cp/(math.pi*d*L)
    out=exponential_temperature_c(80,5,u,d,L,m,cp)
    assert out == pytest.approx(5+(80-5)/math.e,rel=1e-12)
    assert exponential_temperature_c(80,5,0,d,L,m,cp)==pytest.approx(80)

def test_hydrate_envelope_monotonic_pressure():
    assert hydrate_equilibrium_temperature_c(200) > hydrate_equilibrium_temperature_c(20)

def test_api14e_native_reference():
    # rho=62.4 lb/ft3, C=100 -> 12.658 ft/s
    rho=62.4*16.01846337396014
    assert api14e_erosional_velocity_ms(rho,100)/.3048 == pytest.approx(100/math.sqrt(62.4))

def test_turner_density_behavior():
    assert turner_critical_velocity_ms(5,800) > turner_critical_velocity_ms(20,800)

def test_pipeline_report_and_flags():
    r=pipeline_flow_assurance(800,3000,.15,4.5e-5,0,100,60,.2,120,segments=8)
    assert len(r['segments'])==8
    assert r['outlet_temperature_c'] < 60
    assert all(k in r for k in ('hydrate_risk','wax_risk','erosion_risk','liquid_loading_risk','slugging_indicator'))
    assert len(r['limitations'])>=5

def test_wax_user_threshold_changes_flag():
    hot=pipeline_flow_assurance(1000,100,.15,4.5e-5,0,80,60,.1,100,segments=2,config=FlowAssuranceConfig(wax_appearance_temperature_c=0))
    cold=pipeline_flow_assurance(1000,100,.15,4.5e-5,0,80,60,.1,100,segments=2,config=FlowAssuranceConfig(wax_appearance_temperature_c=100))
    assert not hot['wax_risk'] and cold['wax_risk']

def test_unit_roundtrips_v19():
    for p in ('norwegian_si','field'):
        assert velocity_from_display(velocity_to_display(12.3,p),p)==pytest.approx(12.3)
        assert heat_transfer_u_from_display(heat_transfer_u_to_display(5.7,p),p)==pytest.approx(5.7)

def test_invalid_inputs_fail_visible():
    with pytest.raises(ValueError): api14e_erosional_velocity_ms(-1)
    with pytest.raises(ValueError): turner_critical_velocity_ms(10,5)

def test_zero_length_preserves_temperature():
    r=pipeline_flow_assurance(1000,0,.15,4.5e-5,0,100,55,.2,100,segments=1)
    assert r['outlet_temperature_c']==pytest.approx(55)

def test_flow_assurance_cross_unit_input_invariance():
    from physics.unit_system import (liquid_rate_to_display,liquid_rate_from_display,length_to_display,length_from_display,
        diameter_to_display,diameter_from_display,pressure_to_display,pressure_from_display,temperature_to_display,temperature_from_display)
    vals=[]
    for prof in ('norwegian_si','field'):
        q=liquid_rate_from_display(liquid_rate_to_display(900,prof),prof)
        L=length_from_display(length_to_display(2500,prof),prof)
        D=diameter_from_display(diameter_to_display(.14,prof),prof)
        P=pressure_from_display(pressure_to_display(120,prof),prof)
        T=temperature_from_display(temperature_to_display(55,prof),prof)
        vals.append(pipeline_flow_assurance(q,L,D,4.5e-5,30,P,T,.25,140,segments=6))
    a,b=vals
    assert a['outlet_pressure_bar']==pytest.approx(b['outlet_pressure_bar'],rel=1e-12)
    assert a['outlet_temperature_c']==pytest.approx(b['outlet_temperature_c'],rel=1e-12)
    assert a['maximum_erosion_ratio']==pytest.approx(b['maximum_erosion_ratio'],rel=1e-12)
