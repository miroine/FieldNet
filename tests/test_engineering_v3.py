from physics.beggs_brill import beggs_brill_dp_bar, pressure_profile
from physics.choke import choke_dp_bar
from network.examples import demo_case
from solver.steady_state import solve_network

def test_bb_horizontal_positive():
    dp,p=beggs_brill_dp_bar(500,2000,.15,4.5e-5,0,60,50,.3,100); assert dp>0 and 0<=p['liquid_holdup']<=1 and p['flow_regime'] in ('segregated','transition','intermittent','distributed')
def test_profile_finite():
    z=pressure_profile(300,1000,.15,4.5e-5,10,80,50,.2,80,segments=10); assert len(z['pressure_bar'])==11 and all(x==x for x in z['pressure_bar'])
def test_choke_monotonic(): assert choke_dp_bar(800)>choke_dp_bar(400)>0
def test_v3_network():
    n,e=demo_case(); p,q,info,d=solve_network(n,e); assert info['success'] and info['max_abs_residual']<1e-4 and q['trunk']>0
