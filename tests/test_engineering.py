from physics.ipr import pi_rate_m3s, vogel_rate_m3s
from physics.hydraulics import friction_factor, pipe_dp_bar
from network.examples import demo_case
from solver.steady_state import solve_network

def test_pi(): assert abs(pi_rate_m3s(200,100,10)*86400-1000)<1e-9
def test_vogel_endpoints():
    assert abs(vogel_rate_m3s(200,200,1000))<1e-12
    assert abs(vogel_rate_m3s(200,0,1000)*86400-1000)<1e-8
def test_laminar_friction(): assert abs(friction_factor(1000,0)-0.064)<1e-12
def test_pipe_dp_positive(): assert pipe_dp_bar(0.01,1000,0.15,4.5e-5)>0
def test_demo_converges():
    n,e=demo_case(); p,q,info,d=solve_network(n,e); assert info['success']; assert info['max_abs_residual']<1e-4; assert list(q.values())[0]>0
