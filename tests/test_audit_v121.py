import math
from physics.units import *
from physics.hydraulics import pipe_dp_bar
from physics.equipment import pump_power_kw
from physics.ipr import ipr_rate_m3d
from network.reservoir import ReservoirTank, update_tank

def test_unit_roundtrips():
    for x in (0.1, 1.0, 123.456):
        assert math.isclose(pa_to_bar(bar_to_pa(x)), x, rel_tol=1e-12)
        assert math.isclose(m3s_to_m3d(m3d_to_m3s(x)), x, rel_tol=1e-12)
        assert math.isclose(m_to_inch(inch_to_m(x)), x, rel_tol=1e-12)
        assert math.isclose(bar_to_psi(psi_to_bar(x)), x, rel_tol=1e-12)
        assert math.isclose(m3d_to_stb_d(stb_d_to_m3d(x)), x, rel_tol=1e-12)

def test_static_head_reference():
    # 1000 m water column = 98.0665 bar at rho=1000 kg/m3.
    dp=pipe_dp_bar(0.0,1000.0,0.1,1e-5,rho=1000.0,mu_pa_s=1e-3,dz_m=1000.0)
    assert math.isclose(dp,98.0665,rel_tol=1e-8)

def test_pump_power_reference():
    # 864 m3/d = 0.01 m3/s, 10 bar => 10 kW hydraulic; /0.8 = 12.5 kW shaft.
    assert math.isclose(pump_power_kw(864.0,10.0,efficiency=0.8),12.5,rel_tol=1e-12)

def test_pi_reference():
    assert ipr_rate_m3d(200,150,'PI',pi_m3d_bar=10)==500

def test_tank_material_balance_reference():
    t=ReservoirTank('R','R',pressure_bar=250,initial_pressure_bar=250,pore_volume_m3=1e6,total_compressibility_1bar=1e-4)
    # capacity=100 m3/bar, net withdrawal 1000 m3 => 10 bar decline.
    assert math.isclose(update_tank(t,1000),240.0)
    # equal injection and withdrawal => no further decline.
    assert math.isclose(update_tank(t,500,500),240.0)
