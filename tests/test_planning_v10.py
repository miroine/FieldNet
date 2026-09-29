from network.planning import decline_multiplier,tank_pressure,uncertainty_cases,run_scenarios
from network.examples import demo_case

def test_decline_models_monotonic():
    for m in ('exponential','hyperbolic','harmonic'):
        assert decline_multiplier(365,m,0.15)<1 and decline_multiplier(730,m,0.15)<decline_multiplier(365,m,0.15)
def test_tank_pressure_depletes_and_injection_supports():
    p1=tank_pressure(250,10000); p2=tank_pressure(250,20000); ps=tank_pressure(250,20000,injected_m3=10000,aquifer_strength=1)
    assert p2<p1 and ps>p2
def test_uncertainty_cases():
    x=uncertainty_cases({'W':{'pressure_decline_bar_per_1000m3':1.0}})
    assert len(x)==3 and x[0]['depletion']['W']['pressure_decline_bar_per_1000m3']<x[2]['depletion']['W']['pressure_decline_bar_per_1000m3']
def test_scenario_runner():
    n,e=demo_case(); r=run_scenarios(n,e,'2026-01-01',0.1,30,[{'name':'Base'}]); assert r and r[0]['forecast']['field']
