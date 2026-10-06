"""Tests for probabilistic production profiles, parallel Monte Carlo and the risk UI helpers."""
import json, math, time
from datetime import date, timedelta
import numpy as np
import pandas as pd
import pytest
from network.examples import demo_case, demo_field_case
from network.field_development import DevelopmentScenario, exact_timeline
from network.uncertainty import UncertainParameter, MonteCarloConfig, run_monte_carlo, default_workers, parallel_map
from network import risk_profiles as rp


# ----------------------------------------------------------------------------- synthetic helpers
def mk_result(oil, dts, nodes=None, tank=None, cum_cols=False, water=None):
    """Development-like result with oil rates `oil` held over intervals `dts` (days)."""
    d0 = date(2026, 1, 1); day = 0; rows = []; cum = 0.0
    for i, (q, dt) in enumerate(zip(oil, dts)):
        w = 0.0 if water is None else water[i]
        row = {'Date': (d0 + timedelta(days=day)).isoformat(), 'Day': day, 'Interval days': dt, 'Oil [m3/d]': q, 'Water [m3/d]': w,
               'Total liquid [m3/d]': q + w, 'Gas [Sm3/d]': 100 * q, 'Water injection [m3/d]': 0.5 * q}
        if tank is not None: row['P TANK [bar]'] = tank[i]
        cum += q * dt
        if cum_cols: row['Cumulative oil [m3]'] = cum
        rows.append(row); day += dt
    return {'forecast': {'field': rows, 'nodes': nodes or []}}

def prof(results, **kw):
    return rp.percentile_profiles(rp.collect_series(results, **kw))

def pick(df, var, i, col): return df[df['Variable'] == var].iloc[i][col]


# ----------------------------------------------------------------------------- percentile convention
def test_percentile_direction_and_mean():
    runs = [mk_result([float(k)] * 3, [10, 10, 0]) for k in range(1, 102)]  # rates 1..101
    df = prof(runs)
    r = df[(df.Variable == 'Oil rate')].iloc[0]
    assert r.P90 == pytest.approx(11.0) and r.P50 == pytest.approx(51.0) and r.P10 == pytest.approx(91.0)
    assert r.P90 < r.P50 < r.P10 and r.Mean == pytest.approx(51.0) and r.Min == 1 and r.Max == 101 and r.N == 101
    c = df[(df.Variable == 'Cumulative oil')].iloc[-1]
    assert c.P90 < c.P50 < c.P10
    assert list(df.columns) == rp.PROFILE_COLUMNS

def test_derived_water_cut_gor_and_unit_columns():
    df = prof([mk_result([100.0, 80.0], [10, 0], water=[100.0, 20.0])])
    assert pick(df, 'Water cut', 0, 'P50') == pytest.approx(50.0) and pick(df, 'Water cut', 1, 'P50') == pytest.approx(20.0)
    assert pick(df, 'GOR', 0, 'P50') == pytest.approx(100.0)
    assert df[df.Variable == 'Oil rate'].Unit.iloc[0] == 'm3/d' and df[df.Variable == 'Cumulative gas'].Unit.iloc[0] == 'Sm3'

def test_cumulative_percentiled_after_per_realization_integration():
    runs = [mk_result([10.0, 0.0], [10, 10]), mk_result([0.0, 10.0], [10, 10]), mk_result([5.0, 5.0], [10, 10])]
    df = prof(runs)
    # P10 (high) of per-run cumulatives: date0 [100,0,50] -> 90 ; date1 [100,100,100] -> 100
    cum = df[df.Variable == 'Cumulative oil']
    assert list(cum.P10) == pytest.approx([90.0, 100.0])
    # cumulative of the P10 rate profile (rates 9, 9) would be 180 -> must differ
    rate = df[df.Variable == 'Oil rate']
    wrong = float(np.sum(rate.P10.values * np.array([10, 10])))
    assert wrong == pytest.approx(180.0) and cum.P10.iloc[-1] != pytest.approx(wrong)

def test_cumulative_columns_preferred_then_rates_fallback():
    r = mk_result([10.0, 10.0], [10, 10], cum_cols=True)
    r['forecast']['field'][1]['Cumulative oil [m3]'] = 999.0
    assert pick(prof([r]), 'Cumulative oil', 1, 'P50') == pytest.approx(999.0)
    assert pick(prof([r], cumulative_source='rates'), 'Cumulative oil', 1, 'P50') == pytest.approx(200.0)


# ----------------------------------------------------------------------------- alignment / failures
def test_alignment_of_different_length_runs():
    a = mk_result([10.0, 10.0, 10.0], [10, 10, 10]); b = mk_result([20.0, 20.0], [10, 10])
    s = rp.collect_series([a, b]); df = rp.percentile_profiles(s)
    o = df[df.Variable == 'Oil rate']
    assert list(o.N) == [2, 2, 1] and o.Mean.iloc[2] == pytest.approx(10.0) and o.Mean.iloc[0] == pytest.approx(15.0)
    assert len(o) == 3 and o.Date.is_monotonic_increasing

def test_failed_and_empty_runs_skipped():
    s = rp.collect_series([mk_result([1.0], [0]), None, {'success': False, 'error': 'x'}, {'forecast': {'field': []}}])
    assert s['n_runs'] == 1 and s['n_skipped'] == 3
    assert rp.percentile_profiles(rp.collect_series([None])).empty

def test_node_series_groups_and_listing():
    nodes = [{'Date': '2026-01-01', 'Node ID': 'w1', 'Name': 'W1', 'Pressure [bar]': 50.0, 'Kind': 'well'},
             {'Date': '2026-01-01', 'Node ID': 'w2', 'Name': 'W2', 'Pressure [bar]': 60.0, 'Kind': 'well'},
             {'Date': '2026-01-01', 'Node ID': 's1', 'Name': 'SEP', 'Pressure [bar]': 10.0}]
    df = prof([mk_result([1.0], [0], nodes=nodes, tank=[200.0])], node_kinds={'s1': 'separator'})
    names = rp.available_node_series(df)
    assert 'Mean node pressure: well' in names and 'Node pressure: SEP (s1)' in names
    assert pick(df, 'Mean node pressure: well', 0, 'P50') == pytest.approx(55.0)
    assert pick(df, 'System pressure (mean tank)', 0, 'P50') == pytest.approx(200.0)
    assert rp.variable_group('Tank pressure: TANK') == 'Tank'
    assert set(rp.list_series(df, 'Pressure').Group) >= {'System', 'Tank', 'Node group', 'Node'}

def test_variables_filter_and_missing_columns_defensive():
    df = prof([mk_result([1.0, 1.0], [10, 0])], variables=['Oil rate', 'Cumulative *'], include_nodes=False)
    assert set(df.Variable) == {'Oil rate', 'Cumulative oil', 'Cumulative gas', 'Cumulative water', 'Cumulative liquid', 'Cumulative water injection'}
    sparse = {'forecast': {'field': [{'Date': '2026-01-01', 'Oil [m3/d]': 5.0}, {'Date': '2026-01-11', 'Oil [m3/d]': 5.0}]}}
    d2 = prof([sparse]); assert pick(d2, 'Cumulative oil', 1, 'P50') == pytest.approx(50.0)


# ----------------------------------------------------------------------------- tables / io
def test_tables_wide_csv_records_and_poe():
    runs = [mk_result([float(k)] * 3, [10, 10, 0]) for k in range(1, 11)]
    df = prof(runs)
    res = rp.reserves_table(df); row = res[res.Variable == 'Cumulative oil'].iloc[0]
    assert row.P90 < row.P50 < row.P10 and row.N == 10 and row.Mean == pytest.approx(110.0)
    wide = rp.profiles_to_wide(df, variables=['Oil rate'])
    assert 'Oil rate [m3/d] P90' in wide.columns and len(wide) == 3
    assert rp.profiles_to_csv(df).splitlines()[0].startswith('Date,Day,Variable')
    back = rp.profiles_from_records(rp.profiles_to_records(df))
    assert list(back.columns) == list(df.columns) and np.allclose(back.P50, df.P50, rtol=1e-6)
    assert len(rp.profile_table(df, 'Oil rate', n_dates=2)) == 2
    assert rp.probability_of_exceedance([1, 2, 3, 4], 2) == pytest.approx(0.5)
    assert math.isnan(rp.probability_of_exceedance([], 1))

def test_records_are_strict_json():
    df = prof([mk_result([1.0, 1.0], [10, 0])])
    df.loc[0, 'P50'] = np.nan
    json.dumps(rp.profiles_to_records(df), allow_nan=False)


# ----------------------------------------------------------------------------- Monte Carlo
def pi_runner(nodes, edges, start, years, step, events, depletion):
    pi = sum(float(n.get('params', {}).get('pi_m3d_bar', 0)) for n in nodes if n.get('kind') == 'well')
    if pi < 0:
        raise RuntimeError('boom')
    rows = []; cum = 0.0
    for i, (d, dt) in enumerate(exact_timeline(start, years, step)):
        oil = 10 * pi * (1 - 0.01 * i); cum += oil * dt
        rows.append({'Date': d, 'Day': i * step, 'Total liquid [m3/d]': 12 * pi, 'Oil [m3/d]': oil, 'Water [m3/d]': 12 * pi - oil, 'Gas [Sm3/d]': 100 * oil,
                     'Cumulative liquid [m3]': cum, 'Violations': 0, 'Converged': True, 'Message': '', 'P T1 [bar]': 200 - i})
    return {'field': rows, 'wells': [], 'constraints': [], 'final_state': {}}

def flaky_runner(nodes, edges, start, years, step, events, depletion):
    if sum(float(n.get('params', {}).get('pi_m3d_bar', 0)) for n in nodes if n.get('kind') == 'well') > 22.8:
        raise RuntimeError('flaky')
    return pi_runner(nodes, edges, start, years, step, events, depletion)

def small_cfg(nodes, samples=4, name='PI factor'):
    w = next(x for x in nodes if x['kind'] == 'well')
    return MonteCarloConfig(samples, 11, 'lhs', [UncertainParameter(name, 'params.pi_m3d_bar', 'uniform', .7, 1, 1.3, target_id=w['id'])])

def test_monte_carlo_default_has_no_profiles_and_default_workers():
    n, e = demo_case(); r = run_monte_carlo(n, e, DevelopmentScenario('MC', '2026-01-01', .1, 30), small_cfg(n), forecast_runner=pi_runner)
    assert 'profiles' not in r and r['compute']['workers_used'] == 1 and 1 <= default_workers() <= 8

def test_keep_series_profiles_consistent_with_kpis_and_json():
    n, e = demo_case(); sc = DevelopmentScenario('MC', '2026-01-01', .3, 30)
    r = run_monte_carlo(n, e, sc, small_cfg(n, 12), forecast_runner=pi_runner, keep_series=True)
    df = rp.profiles_from_records(r['profiles'])
    last = df[(df.Variable == 'Cumulative oil')].iloc[-1]
    assert last.P50 == pytest.approx(r['metrics']['cumulative_oil_m3']['P50'], rel=1e-5)
    assert last.P90 == pytest.approx(r['metrics']['cumulative_oil_m3']['P90'], rel=1e-5) and last.N == 12
    assert r['series_meta']['stored'] and 'Oil rate' in r['series_meta']['variables'] and 'low case' in r['series_meta']['convention']
    json.dumps(r['profiles'], allow_nan=False); json.dumps(r, default=str)
    assert pick(df, 'Tank pressure: T1', 0, 'P50') == pytest.approx(200.0)

def test_series_variables_filter_in_mc():
    n, e = demo_case(); r = run_monte_carlo(n, e, DevelopmentScenario('MC', '2026-01-01', .1, 30), small_cfg(n, 5), forecast_runner=pi_runner, keep_series=True, series_variables=['Oil rate'])
    assert {x['Variable'] for x in r['profiles']} == {'Oil rate'}

def test_failed_sample_excluded_from_n():
    n, e = demo_case(); sc = DevelopmentScenario('MC', '2026-01-01', .1, 30)
    r = run_monte_carlo(n, e, sc, small_cfg(n, 20), forecast_runner=flaky_runner, keep_series=True)
    assert 0 < r['failed_samples'] < 20
    df = rp.profiles_from_records(r['profiles'])
    assert set(df[df.Variable == 'Oil rate'].N) == {r['successful_samples']}
    assert r['series_meta']['realizations_used'] == r['successful_samples']
    r2 = run_monte_carlo(n, e, sc, small_cfg(n, 20), forecast_runner=flaky_runner, keep_series=True, workers=2)
    assert [x['success'] for x in r2['runs']] == [x['success'] for x in r['runs']]
    assert r2['profiles'] == r['profiles']

def test_serial_vs_parallel_identical_real_case_and_deterministic():
    n, e = demo_field_case(); sc = DevelopmentScenario('MC', '2026-01-01', 0.5, 90)
    cfg = small_cfg(n, 4)
    t = time.time(); a = run_monte_carlo(n, e, sc, cfg, keep_series=True); ts = time.time() - t
    t = time.time(); b = run_monte_carlo(n, e, sc, cfg, keep_series=True, workers=2); tp = time.time() - t
    c = run_monte_carlo(n, e, sc, cfg, keep_series=True, workers=2)
    print(f'serial {ts:.2f}s parallel(2) {tp:.2f}s start={b["compute"]["start_method"]} notes={b["compute"]["notes"]}')
    assert a['successful_samples'] == 4
    for k in ('runs', 'metrics', 'sensitivity', 'profiles'):
        assert a[k] == b[k] == c[k]
    assert b['compute']['workers_used'] == 2 and b['compute']['start_method'] in ('fork', 'spawn')
    df = rp.profiles_from_records(a['profiles'])
    assert {'Oil rate', 'Cumulative oil', 'System pressure (mean tank)'} <= set(df.Variable) and (df.N.max() == 4)
    assert [x['sample'] for x in b['runs']] == [0, 1, 2, 3]
    json.dumps(b, default=str)

def test_progress_called_from_parent_and_external_executor():
    from concurrent.futures import ThreadPoolExecutor
    n, e = demo_case(); calls = []
    sc = DevelopmentScenario('MC', '2026-01-01', .1, 30)
    with ThreadPoolExecutor(2) as ex:
        r = run_monte_carlo(n, e, sc, small_cfg(n, 6), forecast_runner=pi_runner, progress=lambda i, k: calls.append((i, k)), executor=ex, keep_series=True)
    ser = run_monte_carlo(n, e, sc, small_cfg(n, 6), forecast_runner=pi_runner, keep_series=True)
    assert calls[-1] == (6, 6) and [c[0] for c in calls] == sorted(c[0] for c in calls)
    assert r['runs'] == ser['runs'] and r['profiles'] == ser['profiles']

def test_memory_cap_note(monkeypatch):
    import network.uncertainty as U
    monkeypatch.setattr(U, 'MAX_SERIES_BYTES', 1)
    n, e = demo_case()
    r = run_monte_carlo(n, e, DevelopmentScenario('MC', '2026-01-01', .1, 30), small_cfg(n, 3), forecast_runner=pi_runner, keep_series=True)
    assert r['series_meta']['realizations_dropped_for_memory'] == 3 and any('memory cap' in x for x in r['compute']['notes'])
    assert r['successful_samples'] == 3


# ----------------------------------------------------------------------------- other batch evaluators
def _sq(x): return x * x

def test_parallel_map_ordered_and_other_evaluators_accept_workers():
    assert parallel_map(_sq, range(7), 2) == [x * x for x in range(7)] and parallel_map(_sq, [3], 4) == [9]
    from optimization.scenarios import run_sensitivity
    n, e = demo_case(); w = next(x for x in n if x['kind'] == 'well'); base = w['params']['pi_m3d_bar']
    a = run_sensitivity(n, e, 'node', w['id'], 'pi_m3d_bar', [base * .8, base, base * 1.2])
    b = run_sensitivity(n, e, 'node', w['id'], 'pi_m3d_bar', [base * .8, base, base * 1.2], workers=2)
    assert a == b


# ----------------------------------------------------------------------------- UI helpers & charts
def _plotly_ok():
    try:
        import plotly.graph_objects as go
        return hasattr(go, 'Figure')
    except Exception:
        return False

def _subplots_ok():
    try:
        import plotly.subplots  # noqa: F401
        return True
    except Exception:
        return False

def test_ui_pure_helpers():
    from ui.uncertainty_v17 import get_profiles_df, profile_variable_options, export_profiles_csv, export_profiles_json
    n, e = demo_case()
    r = run_monte_carlo(n, e, DevelopmentScenario('MC', '2026-01-01', .1, 30), small_cfg(n, 5), forecast_runner=pi_runner, keep_series=True)
    cache = {}; df = get_profiles_df(r, cache)
    assert get_profiles_df(r, cache) is df and get_profiles_df({}).empty
    assert profile_variable_options(df, 'Rate')[0] == 'Oil rate' and profile_variable_options(df, 'Pressure')[0] == 'System pressure (mean tank)'
    assert export_profiles_csv(r).startswith('Date,Day,Variable') and 'Oil rate [m3/d] P90' in export_profiles_csv(r, wide=True)
    j = json.loads(export_profiles_json(r)); assert 'low case' in j['convention'] and len(j['profiles']) == len(r['profiles'])

def test_risk_charts_smoke_if_plotly_available():
    if not _plotly_ok():
        return
    from ui import risk_charts
    df = prof([mk_result([float(k)] * 3, [10, 10, 0], tank=[200.0, 190.0, 180.0]) for k in range(1, 6)])
    fig = risk_charts.fan_chart(df, 'Oil rate', 'Oil', 'm3/d'); assert fig is not None
    assert risk_charts.fan_chart(df, 'Nope') is not None
    if _subplots_ok():
        assert risk_charts.profiles_grid(df, 'Rate') is not None and risk_charts.profiles_grid(df, 'Cumulative', cols=1) is not None
