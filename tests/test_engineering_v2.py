from physics.pvt import simple_black_oil
from physics.multiphase import homogeneous_dp_bar
from physics.vlp import tubing_bhp_bar
from physics.ipr import ipr_rate_m3d
from network.examples import demo_case
from solver.steady_state import solve_network

def test_pvt_physical():
    s=simple_black_oil(100,60); assert 500<s.oil_density_kgm3<1000 and s.gas_density_kgm3>1 and 0.5<s.gas_z<1.3

def test_zero_horizontal_dp():
    dp,_=homogeneous_dp_bar(0,1000,0.1,4.5e-5,0,50,50); assert abs(dp)<1e-10

def test_vertical_vlp():
    bhp,_=tubing_bhp_bar(500,30,2000,0.09,water_cut=0.3,gor_sm3sm3=80); assert bhp>30

def test_ipr_monotonic():
    assert ipr_rate_m3d(200,50,'PI',10)>ipr_rate_m3d(200,100,'PI',10)

def test_network_demo():
    n,e=demo_case(); p,q,info,d=solve_network(n,e); assert info['success']; assert info['max_abs_residual']<1e-4; assert q['trunk']>0; assert d['w1']['bhp_bar']>d['w1']['whp_bar']
