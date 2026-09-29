from network.examples import demo_case
from solver.steady_state import solve_network
from optimization.allocation import bottlenecks, optimize_allocation
from optimization.scenarios import run_sensitivity

def test_bottleneck_ranking():
    n,e=demo_case(); n[-1]['params']['max_liquid_rate_m3d']=10
    r=solve_network(n,e); b=bottlenecks(r[2]); assert b and b[0]['Status']=='VIOLATED'

def test_sensitivity_runs():
    n,e=demo_case(); rows=run_sensitivity(n,e,'node','s1','pressure_bar',[30,35,40]); assert len(rows)==3 and all('Total liquid [m3/d]' in x for x in rows)

def test_optimizer_returns_controls():
    n,e=demo_case(); r=optimize_allocation(n,e,maxiter=1,seed=1); assert len(r['controls'])==2 and r['result'] is not None
