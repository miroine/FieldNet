import copy, pytest
from network.examples import demo_field_case
from solver.v21 import solve_v21
from solver.constraints import evaluate_constraints, REGISTRY
from ui.graph_contract import normalize_graph, solver_input
from network.forecast import run_forecast


def _case():
    n, e = demo_field_case(); n, e, _ = normalize_graph(n, e); return n, e


def _solve(n, e, enforce=True):
    ns, es = solver_input(n, e); return solve_v21(ns, es, enforce_constraints=enforce)


def _tot(d, k): return sum(v[k] for v in d.values())


def test_separator_oil_gas_water_capacities_are_honoured():
    n, e = _case(); base = _solve(n, e)[3]
    sep = next(x for x in n if x['id'] == 'SEP')
    for key, dk in (('max_oil_rate_m3d', 'oil_rate_m3d'), ('max_water_rate_m3d', 'water_rate_m3d'), ('max_gas_rate_sm3d', 'gas_rate_sm3d')):
        n2 = copy.deepcopy(n); s2 = next(x for x in n2 if x['id'] == 'SEP'); lim = 0.6 * _tot(base, dk); s2['params'][key] = lim
        p, q, info, d = _solve(n2, e, True)
        assert _tot(d, dk) <= lim * 1.02, (key, _tot(d, dk), lim)
        assert any(a['constraint'].endswith(('Oil capacity', 'Water capacity', 'Gas capacity')) for a in info['constraint_actions'])
        assert _tot(d, dk) > 0.5 * lim
        un = _solve(n2, e, False)[2]; assert un['violations'] >= 1     # not honoured -> reported as violated


def test_well_phase_rate_caps_inside_the_well_equation():
    n, e = _case(); w = next(x for x in n if x['kind'] == 'well' and x['id'] == 'P1')
    d0 = _solve(n, e)[3]['P1']
    w['params']['max_oil_rate_m3d'] = 0.5 * d0['oil_rate_m3d']
    d1 = _solve(n, e, False)[3]['P1']; assert d1['oil_rate_m3d'] <= 0.5 * d0['oil_rate_m3d'] * 1.001 and d1['status'] == 'rate_limited'
    w['params']['max_oil_rate_m3d'] = None; w['params']['max_gas_rate_sm3d'] = 0.3 * d0['gas_rate_sm3d']
    d2 = _solve(n, e, False)[3]['P1']; assert d2['gas_rate_sm3d'] <= 0.3 * d0['gas_rate_sm3d'] * 1.001


def test_flowline_velocity_and_erosion_limits_enforced():
    n, e = _case(); ns, es = solver_input(n, e); p, q, info, d = solve_v21(ns, es)
    from network.element_results import element_rows
    nr, er = element_rows(ns, es, p, q, d, info)
    line = max((r for r in er if r['Kind'] == 'pipeline' and r['Max velocity [m/s]']), key=lambda r: r['Max velocity [m/s]'])
    e2 = copy.deepcopy(e); ed = next(x for x in e2 if x['id'] == line['Edge ID']); ed['params']['max_velocity_ms'] = 0.7 * line['Max velocity [m/s]']
    p2, q2, i2, d2 = _solve(n, e2, True)
    nr2, er2 = element_rows(*solver_input(n, e2), p2, q2, d2, i2)
    v2 = next(r for r in er2 if r['Edge ID'] == line['Edge ID'])['Max velocity [m/s]']
    assert v2 <= 0.7 * line['Max velocity [m/s]'] * 1.05
    rows = [r for r in i2['constraints'] if r['Constraint'] == 'Maximum velocity']; assert rows
    ed['params'].pop('max_velocity_ms'); ed['params']['max_erosional_ratio'] = 0.5 * line['Max erosional ratio [-]']
    i3 = _solve(n, e2, False)[2]; assert any(r['Constraint'] == 'Maximum erosional ratio' and r['Status'] == 'VIOLATED' for r in i3['constraints'])


def test_max_pressure_and_dp_are_reported():
    n, e = _case(); ns, es = solver_input(n, e); p, q, info, d = solve_v21(ns, es)
    ed = copy.deepcopy(es); ed[0]['params']['max_dp_bar'] = 0.01; ed[0]['params']['max_pressure_bar'] = 1.0
    rows = evaluate_constraints(ns, ed, p, q, d, info)
    assert {r['Constraint'] for r in rows if r['Status'] == 'VIOLATED'} >= {'Maximum pressure drop', 'Maximum pressure'}


def test_registry_keys_unique():
    keys = [r['key'] for r in REGISTRY]; assert len(keys) == len(set(keys))


def test_constraints_are_shared_with_forecast():
    n, e = _case(); sep = next(x for x in n if x['id'] == 'SEP'); sep['params']['max_water_rate_m3d'] = 800.0
    fc = run_forecast(n, e, '2026-01-01', 1, 90, enforce_constraints=True)
    assert all(r['Water [m3/d]'] <= 800.0 * 1.03 for r in fc['field'])
