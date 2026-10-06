"""Tests for network/gaslift_constraint.py. The demo case has one gas-lift well (P3); P1 and P2 are converted in the test."""
from network.examples import demo_field_case
from network.gaslift_constraint import allocate_with_lift_gas_limit


def _case():
    n, e = demo_field_case()
    for x in n:
        if x['id'] in ('P1', 'P2'): x['params'].update(lift_type='gas_lift', gas_lift_injection_sm3d=0.0)
        if x['id'] == 'P1': x['params'].update(pi_m3d_bar=6.0, water_cut=0.5)   # watered-out, weak well -> different marginal response
    return n, e


def test_limited_supply_is_respected_and_beats_no_lift():
    n, e = _case()
    r = allocate_with_lift_gas_limit(n, e, 60000.0)
    assert r['success'] and abs(r['total_allocated_sm3d'] - 60000.0) < 1.0
    assert set(r['allocation']) == {'P1', 'P2', 'P3'} and all(v >= 0 for v in r['allocation'].values())
    assert r['gain_vs_none_m3d'] > 50.0
    assert r['oil_unlimited_m3d'] >= r['oil_allocated_m3d'] and r['loss_vs_unlimited_m3d'] > 0 and r['supply_binding']
    assert r['marginal_value_m3d_per_1000sm3d'] > 0
    assert n[1]['params']['gas_lift_injection_sm3d'] == 0.0     # input not mutated
    assert len(r['allocation_table']) == 3


def test_more_gas_gives_more_oil_with_falling_marginal_value():
    n, e = _case()
    a = allocate_with_lift_gas_limit(n, e, 30000.0); b = allocate_with_lift_gas_limit(n, e, 100000.0)
    assert b['oil_allocated_m3d'] > a['oil_allocated_m3d']
    assert b['marginal_value_m3d_per_1000sm3d'] < a['marginal_value_m3d_per_1000sm3d']


def test_equal_slope_beats_equal_split():
    n, e = _case(); T = 60000.0
    r = allocate_with_lift_gas_limit(n, e, T)
    from solver.v21 import solve_v21
    ns = r['nodes']
    for x in ns:
        if x['id'] in r['allocation']: x['params']['gas_lift_injection_sm3d'] = T / 3
    d = solve_v21(ns, e)[3]; eq = sum(v['oil_rate_m3d'] for v in d.values())
    assert r['oil_allocated_m3d'] >= eq - 1.0


def test_non_binding_supply_and_no_gas_lift_wells():
    n, e = _case()
    r = allocate_with_lift_gas_limit(n, e, 5e6)
    assert not r['supply_binding'] and r['marginal_value_m3d_per_1000sm3d'] == 0.0
    assert r['total_allocated_sm3d'] < 5e6
    n2, e2 = demo_field_case()
    for x in n2: x['params'].pop('lift_type', None)
    r2 = allocate_with_lift_gas_limit(n2, e2, 1000.0)
    assert not r2['success'] and r2['allocation'] == {}


def test_custom_solve_fn_is_used():
    n, e = _case(); calls = []
    from solver.v21 import solve_v21
    def fn(a, b): calls.append(1); return solve_v21(a, b)
    allocate_with_lift_gas_limit(n, e, 20000.0, solve_fn=fn)
    assert calls
