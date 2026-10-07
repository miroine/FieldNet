"""v32.6 round 5: drainage-strategy recommendation, tank history match, stable-branch capacity enforcement."""
import copy, math
import numpy as np, pandas as pd
import pytest
from network.templates import build


def _case(key='pure_depletion_oil'):
    b = build(key); return (b[0], b[1]) if isinstance(b, tuple) else (b['nodes'], b['edges'])


# ---- drainage strategy -------------------------------------------------------------------------------------------
def test_plateau_helper_finds_longest_run_at_or_above_fraction_of_peak():
    from network.drainage import plateau
    days = [0, 100, 200, 300, 400, 500, 600]; rate = [10, 100, 98, 95, 50, 99, 99]
    r, yrs, peak = plateau(days, rate)
    assert peak == 100 and yrs == pytest.approx(300 / 365.25) and r == pytest.approx(np.mean([100, 98, 95]))
    assert plateau([0, 1], [0, 0]) == (0.0, 0.0, 0.0) and plateau([], []) == (0.0, 0.0, 0.0)


def test_npv_discounting_and_capex():
    from network.drainage import npv
    days = [0, 365.25, 730.5]; rate = [100.0, 100.0, 0.0]
    v = npv(days, rate, price=2.0, opex_per_year=10.0, capex=50.0, rate_discount=0.0, years_total=2)
    assert v == pytest.approx(-50 + 2 * (100 * 365.25 * 2.0 - 10.0))
    assert npv(days, rate, 2.0, 10.0, 50.0, 0.10, 2) < v


def test_primary_phase_by_in_place_volume():
    from network.drainage import primary_phase
    assert primary_phase(_case('pure_depletion_oil')[0]) == 'oil' and primary_phase(_case('gas_condensate_tieback')[0]) == 'gas'


def test_drainage_strategy_sweeps_wells_and_recommends_with_a_reason(tmp_path):
    from network.drainage import drainage_strategy, to_html
    import xml.dom.minidom  # noqa: F401
    n, e = _case(); order = [x['id'] for x in n if x['kind'] == 'well'][:3]
    for x in n:
        if x['kind'] == 'separator': x['params']['max_liquid_rate_m3d'] = 700.0
    r = drainage_strategy(n, e, order, '2030-01-01', 3, 180, plateau_target=500.0, economics={'price_per_sm3': 3000, 'capex_per_well': 5e7, 'opex_per_well_year': 1e6, 'discount_rate': 0.08})
    assert [x['Wells'] for x in r['rows']] == [1, 2, 3] and 1 <= r['recommended_wells'] <= 3 and r['reason'] and r['summary'].startswith(str(r['recommended_wells']))
    assert all('NPV' in x for x in r['rows']) and r['phase'] == 'oil'
    assert any(x['Binding constraint'] and 'Liquid capacity' in x['Binding constraint'] for x in r['rows'])      # the 700 Sm3/d separator limit binds
    page = to_html(r); assert '<svg' in page and 'Recommendation' in page and page.count('<tr') >= 4 and '#1baf7a' in page


def test_unreachable_plateau_target_is_reported_not_forced():
    from network.drainage import drainage_strategy
    n, e = _case(); order = [x['id'] for x in n if x['kind'] == 'well'][:2]
    r = drainage_strategy(n, e, order, '2030-01-01', 2, 365, plateau_target=1e7)
    assert 'not reachable' in r['reason']


# ---- history match -----------------------------------------------------------------------------------------------------
def _synthetic_history(n_true=30e6, j_true=800.0, quarters=24):
    from network.reservoir_mb import Tank
    from network.mb_analysis import CUM
    node = {'id': 'T', 'kind': 'reservoir', 'name': 'T', 'params': {'fluid_phase': 'oil', 'stoiip_sm3': n_true, 'reservoir_pressure_bar': 300, 'bubble_point_bar': 150, 'boi_rm3_sm3': 1.25,
                                                                      'ct_1bar': 1.5e-4, 'aquifer_pi_m3d_bar': j_true, 'swi': 0.2}}
    tk = Tank(node); rows = [{'Date': pd.Timestamp('2020-01-01'), 'Pressure [bar]': tk.p, **{c: 0.0 for c in CUM}}]; d = pd.Timestamp('2020-01-01')
    for i in range(quarters):
        d = d + pd.Timedelta(days=91); tk.step(8000 * 91, 2000 * 91 if i > 8 else 0, 8000 * 91 * 110, 0, 0, 91)
        rows.append({'Date': d, 'Pressure [bar]': tk.p, 'Cum oil [Sm3]': tk.np, 'Cum gas [Sm3]': tk.gp, 'Cum water [m3]': tk.wp, 'Cum water inj [m3]': 0.0, 'Cum gas inj [Sm3]': 0.0,
                     'Aquifer influx [m3]': 0.0, 'Net communication [m3]': 0.0})
    return node, pd.DataFrame(rows)


def test_replay_with_true_parameters_reproduces_the_history():
    from network.history_match import replay
    node, h = _synthetic_history(); assert np.max(np.abs(replay(node, h) - h['Pressure [bar]'].values)) < 1e-6


def test_history_match_recovers_aquifer_and_flags_unidentifiable_volume():
    from network.history_match import match_tank
    node, h = _synthetic_history(); guess = copy.deepcopy(node); guess['params'].update(stoiip_sm3=18e6, aquifer_pi_m3d_bar=100.0)
    r = match_tank(guess, h, True, True)
    assert r['rmse_bar'] < 0.5 < r['rmse_before_bar'] and r['quality'] == 'good'
    assert r['fitted']['J'] == pytest.approx(800.0, rel=0.1)
    lo, hi = r['ranges']['N']; assert lo <= 30e6 <= hi                      # the profile range contains the truth ...
    assert hi / lo > 1.05                                                  # ... and the range is honest that the volume is not pinned to a point (aquifer-supported, 5 % pressure fall)


def test_history_match_volumetric_depleting_tank_recovers_in_place_volume():
    from network.reservoir_mb import Tank
    from network.history_match import match_tank
    from network.mb_analysis import CUM
    node = {'id': 'G', 'kind': 'reservoir', 'name': 'G', 'params': {'fluid_phase': 'gas', 'giip_sm3': 5e9, 'reservoir_pressure_bar': 400, 'temperature_c': 110, 'gas_sg': 0.65, 'min_pressure_bar': 5}}
    tk = Tank(node); rows = [{'Date': pd.Timestamp('2020-01-01'), 'Pressure [bar]': tk.p, **{c: 0.0 for c in CUM}}]; d = pd.Timestamp('2020-01-01')
    for i in range(16):
        d += pd.Timedelta(days=91); tk.step(0, 0, 4e6 * 91, 0, 0, 91)
        rows.append({'Date': d, 'Pressure [bar]': tk.p, 'Cum oil [Sm3]': 0.0, 'Cum gas [Sm3]': tk.gp, 'Cum water [m3]': 0.0, 'Cum water inj [m3]': 0.0, 'Cum gas inj [Sm3]': 0.0, 'Aquifer influx [m3]': 0.0, 'Net communication [m3]': 0.0})
    guess = copy.deepcopy(node); guess['params']['giip_sm3'] = 8e9
    r = match_tank(guess, pd.DataFrame(rows), True, False)
    assert r['fitted']['N'] == pytest.approx(5e9, rel=0.03) and r['quality'] == 'good' and not any('poorly' in w for w in r['warnings'])
    from network.history_match import apply_match
    assert apply_match(guess, r) == ['giip_sm3'] and guess['params']['giip_sm3'] == pytest.approx(5e9, rel=0.03)


def test_history_match_edge_cases():
    from network.history_match import match_tank
    node, h = _synthetic_history()
    assert 'error' in match_tank(node, h.iloc[:2]) and 'error' in match_tank(node, h, False, False)
    flat = h.copy(); flat['Pressure [bar]'] = 300.0
    assert any('cannot be identified' in w for w in match_tank(node, flat, True, False)['warnings'])


# ---- stable-branch capacity enforcement ------------------------------------------------------------------------------------
def _fake_network():
    nodes = [{'id': 'W1', 'kind': 'well', 'name': 'W1', 'params': {}}, {'id': 'W2', 'kind': 'well', 'name': 'W2', 'params': {}}, {'id': 'SEP', 'kind': 'separator', 'name': 'SEP', 'params': {}}]
    edges = [{'id': 'e1', 'source': 'W1', 'target': 'SEP'}, {'id': 'e2', 'source': 'W2', 'target': 'SEP'}]
    return nodes, edges


def _fake_solver(limit, natural=100.0, loads_up_below=0.6):
    def solve(ns, es, guess):
        d = {}
        for n in ns:
            if n['kind'] != 'well': continue
            cap = n['params'].get('_network_cap_m3d', math.inf)
            q = min(natural, cap); d[n['id']] = {'liquid_rate_m3d': 0.0 if q < loads_up_below * natural else q}   # a steady choke below 60 % of natural kills the well
        tot = sum(v['liquid_rate_m3d'] for v in d.values())
        c = [{'Status': 'VIOLATED' if tot > limit * 1.0001 else 'OK', 'Constraint': 'Liquid capacity', 'Component': 'SEP', 'ComponentId': 'SEP', 'Limit': limit, 'Value': tot}]
        return {}, {}, {'constraints': c, 'max_abs_residual': 0.0, 'success': True}, d
    return solve


def test_choke_backs_off_to_the_lowest_stable_rate_instead_of_returning_dead_wells():
    from solver.v21 import enforce_capacity_constraints
    n, e = _fake_network()
    res, ns, actions = enforce_capacity_constraints(n, e, _fake_solver(limit=100.0))      # limit = 50 % of natural: not holdable
    rates = [v['liquid_rate_m3d'] for v in res[3].values()]
    assert all(r >= 60.0 - 1.0 for r in rates) and all(r > 0 for r in rates)               # stable (>= 60 % of natural), not dead
    assert 'cannot be held by steady choking' in res[2]['message'] and any(a['constraint'] == 'capacity (stability)' for a in actions)
    assert sum(rates) > 100.0                                                              # the remaining excess is left as a violation, honestly


def test_normal_choking_unchanged_when_the_limit_is_holdable():
    from solver.v21 import enforce_capacity_constraints
    n, e = _fake_network()
    res, ns, actions = enforce_capacity_constraints(n, e, _fake_solver(limit=140.0))       # 70 % of natural: stable
    assert sum(v['liquid_rate_m3d'] for v in res[3].values()) == pytest.approx(140.0, rel=0.02) and 'cannot be held' not in str(res[2].get('message', ''))


# ---- UI wiring --------------------------------------------------------------------------------------------------------------------------
def test_scenarios_tab_has_drainage_strategy_and_history_match_is_reachable():
    from tests.support.app_harness import run_app
    n, e = _case(); root = run_app('app.py', {'nodes': n, 'edges': e})
    assert ('subheader', 'Drainage strategy recommendation') in root.calls and ('button', '▶ Run drainage strategy study') in root.calls
