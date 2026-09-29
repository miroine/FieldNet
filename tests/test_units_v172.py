import math
import pytest
from physics.unit_system import *
from physics.hydraulics import pipe_dp_bar
from physics.equipment import pump_power_kw
from physics.ipr import ipr_rate_m3d

def close(a,b,tol=1e-11): assert math.isclose(a,b,rel_tol=tol,abs_tol=tol)

def test_reference_conversions():
    close(bar_to_psi(1),14.503773773020923); close(psi_to_bar(14.503773773020923),1)
    close(m_to_ft(1),3.280839895013123); close(m_to_in(.0254),1)
    close(m3_to_stb(1),6.289810770432105); close(sm3_to_scf(1),35.31466672148859)
    close(kw_to_hp(1),1.341022089595027)
    close(c_to_f(0),32); close(c_to_f(100),212)

def test_roundtrip_all_profile_quantities():
    pairs=[(pressure_to_display,pressure_from_display,123.4),(temperature_to_display,temperature_from_display,67.8),(length_to_display,length_from_display,2345),(diameter_to_display,diameter_from_display,.0889),(liquid_rate_to_display,liquid_rate_from_display,1234),(gas_rate_to_display,gas_rate_from_display,345678),(liquid_volume_to_display,liquid_volume_from_display,98765),(gas_volume_to_display,gas_volume_from_display,9e6),(gor_to_display,gor_from_display,150),(density_to_display,density_from_display,850),(power_to_display,power_from_display,2500),(pi_to_display,pi_from_display,12.3)]
    for to_,fr_,x in pairs:
        for profile in ('norwegian_si','field'): close(fr_(to_(x,profile),profile),x,1e-10)

def test_pressure_reference_semantics():
    close(gauge_to_absolute_bar(0),1.01325); close(absolute_to_gauge_bar(1.01325),0)
    assert STANDARD_CONDITIONS['standard_temperature_c']==15.0 and STANDARD_CONDITIONS['standard_pressure_bara']==1.01325

def test_standard_gas_and_liquid_are_independent():
    # 1 Sm3 gas is 35.3147 scf, while 1 Sm3 stock-tank liquid is 6.28981 stb.
    close(sm3_to_scf(1),35.31466672148859); close(m3_to_stb(1),6.289810770432105)
    assert not math.isclose(sm3_to_scf(1),m3_to_stb(1))

def test_cross_system_physics_invariance():
    # Build the same physical inputs through both UI profiles.
    p_si=pressure_from_display(200,'norwegian_si'); p_f=pressure_from_display(pressure_to_display(200,'field'),'field')
    q_si=liquid_rate_from_display(1000,'norwegian_si'); q_f=liquid_rate_from_display(liquid_rate_to_display(1000,'field'),'field')
    l_si=length_from_display(1000,'norwegian_si'); l_f=length_from_display(length_to_display(1000,'field'),'field')
    d_si=diameter_from_display(diameter_to_display(.1,'norwegian_si'),'norwegian_si'); d_f=diameter_from_display(diameter_to_display(.1,'field'),'field')
    close(p_si,p_f); close(q_si,q_f); close(l_si,l_f); close(d_si,d_f)
    close(ipr_rate_m3d(p_si,150,'PI',10),ipr_rate_m3d(p_f,150,'PI',10))
    close(pipe_dp_bar(q_si/86400,l_si,d_si,1e-5),pipe_dp_bar(q_f/86400,l_f,d_f,1e-5))

def test_reference_head_and_power_still_hold():
    close(pipe_dp_bar(0,1000,.1,1e-5,rho=1000,mu_pa_s=.001,dz_m=1000),98.0665,1e-8)
    close(pump_power_kw(864,10,efficiency=.8),12.5)

def test_invalid_nonfinite_rejected():
    with pytest.raises(ValueError): pressure_from_display(float('nan'),'field')

def test_project_mapping_roundtrip_and_unknown_keys_untouched():
    canonical={'pressure_bar':123.4,'length_m':1000,'diameter_m':.154,'params':{'temperature_c':70,'pi_m3d_bar':10,'gor_sm3sm3':120,'custom_factor':7}}
    displayed=convert_mapping_units(canonical,'field','to_display')
    assert displayed['pressure_bar']!=canonical['pressure_bar'] and displayed['params']['custom_factor']==7
    restored=convert_mapping_units(displayed,'field','from_display')
    for k in ('pressure_bar','length_m','diameter_m'): close(restored[k],canonical[k],1e-10)
    for k in ('temperature_c','pi_m3d_bar','gor_sm3sm3'): close(restored['params'][k],canonical['params'][k],1e-10)

def test_norwegian_si_diameter_is_mm_but_length_is_m():
    assert diameter_to_display(.0889,'norwegian_si')==pytest.approx(88.9)
    assert length_to_display(2000,'norwegian_si')==2000
