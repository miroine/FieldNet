"""v32.6: recovery & rate assumptions - tank target RF (taper cap), well rate calibration, EUR cap, gas p/z helper, UI wiring."""
import copy
import pytest
from network.templates import build
from tests.support.app_harness import run_app


def _case(key='pure_depletion_oil'):
    b = build(key); return (b[0], b[1]) if isinstance(b, tuple) else (b['nodes'], b['edges'])


def test_productivity_multiplier_scales_all_ipr_types():
    from physics.well_model import well_settings
    base = {'pi_m3d_bar': 10.0, 'qmax_m3d': 1000.0, 'gas_c_sm3d_bar2n': 50.0}
    a, b = well_settings(base), well_settings({**base, 'productivity_multiplier': 0.5})
    assert b['pi'] == pytest.approx(0.5 * a['pi']) and b['qmax'] == pytest.approx(0.5 * a['qmax']) and b['gas_c'] == pytest.approx(0.5 * a['gas_c'])
    assert well_settings({**base, 'productivity_multiplier': 'x'})['pi'] == pytest.approx(a['pi'])


def test_no_assumptions_means_unchanged_forecast_and_no_target_columns():
    from network.forecast import run_forecast
    from network.assumptions import has_assumptions
    n, e = _case(); assert not has_assumptions(n)
    r = run_forecast(n, e, '2030-01-01', years=1, step_days=180)
    assert all('Target RF [%]' not in x for x in r['recovery'])


def test_target_rf_tapers_to_target_and_never_overshoots():
    from network.forecast import run_forecast
    n, e = _case()
    for t in n:
        if t['kind'] == 'reservoir': t['params'].update(target_rf=0.02, rf_taper_days=120)
    free = run_forecast(copy.deepcopy(_case()[0]), e, '2030-01-01', years=5, step_days=180)
    r = run_forecast(n, e, '2030-01-01', years=5, step_days=180)
    rec = r['recovery'][0]
    assert rec['Target RF [%]'] == pytest.approx(2.0)
    assert free['recovery'][0]['RF oil [%]'] > 3.0                  # the unconstrained case would have gone well past the target
    assert 1.8 <= rec['Primary RF [%]'] <= 2.0 + 1e-6 and rec['Target status'] == 'target reached'
    cum = [x['Cumulative oil [Sm3]'] for x in r['field']]
    assert all(b >= a - 1e-6 for a, b in zip(cum, cum[1:]))


def test_target_above_physics_reports_below_target():
    from network.forecast import run_forecast
    n, e = _case()
    for t in n:
        if t['kind'] == 'reservoir': t['params']['target_rf'] = 0.60
    rec = run_forecast(n, e, '2030-01-01', years=1, step_days=180)['recovery'][0]
    assert rec['Target status'].startswith('below target') and rec['Primary RF [%]'] < 60


def test_well_eur_cap_stops_the_well():
    from network.forecast import run_forecast
    n, e = _case()
    w = next(x for x in n if x['kind'] == 'well'); w['params']['eur_cap'] = 2.0e5; w['params']['reservoir_id'] = next(x['id'] for x in n if x['kind'] == 'reservoir')
    r = run_forecast(n, e, '2030-01-01', years=3, step_days=180, store_elements=False)
    cum = max(x['Cumulative oil [Sm3]'] for x in r['wells'] if x['Well ID'] == w['id'])
    assert 1.5e5 <= cum <= 2.0e5 * 1.001


def test_gas_abandonment_pressure_roundtrip_and_monotonic():
    from network.assumptions import abandonment_pressure_for_rf, rf_for_abandonment_pressure
    p = {'reservoir_pressure_bar': 300.0, 'temperature_c': 100.0, 'gas_sg': 0.65}
    pa = abandonment_pressure_for_rf(p, 0.7)
    assert 20 < pa < 150 and rf_for_abandonment_pressure(p, pa) == pytest.approx(0.7, abs=1e-3)
    assert abandonment_pressure_for_rf(p, 0.8) < pa


def test_target_rf_percent_and_fraction_inputs():
    from network.assumptions import target_rf_of
    assert target_rf_of({'target_rf': 35}) == pytest.approx(0.35) and target_rf_of({'target_rf': 0.35}) == pytest.approx(0.35)
    assert target_rf_of({}) is None and target_rf_of({'target_rf': 0}) is None and target_rf_of({'target_rf': 'x'}) is None


def test_calibration_reaches_targets_and_writes_multipliers():
    from network.calibration import calibrate_wells, apply_calibration, clear_calibration
    n, e = _case(); wells = [x for x in n if x['kind'] == 'well']
    wells[0]['params']['calibrate_rate'] = 90.0; wells[1]['params']['calibrate_rate'] = 70.0
    res = calibrate_wells(n, e)
    assert res['converged'] and all(r['Status'] == 'calibrated' for r in res['rows'])
    for r in res['rows']: assert abs(r['Achieved rate'] / r['Target rate'] - 1) <= 0.02
    assert apply_calibration(n, res) == 2 and wells[0]['params']['productivity_multiplier'] > 0
    assert clear_calibration(n) == 2 and 'productivity_multiplier' not in wells[0]['params']


def test_calibration_reports_unreachable_target():
    from network.calibration import calibrate_wells
    n, e = _case(); w = next(x for x in n if x['kind'] == 'well'); w['params']['calibrate_rate'] = 5e6
    res = calibrate_wells(n, e, max_iter=6)
    assert not res['converged'] and 'limited' in res['rows'][0]['Status'] or 'not converged' in res['rows'][0]['Status']


def test_table_helpers_round_trip_units():
    from network.assumptions import tank_table, well_table, apply_tank_rows, apply_well_rows
    n, _ = _case('onshore_gas_gathering')
    tt = tank_table(n); wt = well_table(n)
    tt[0]['Target RF [%]'] = 70.0; wt[0]['Max rate [MSm³/d]'] = 1.5; wt[0]['Calibrate to rate [MSm³/d]'] = 1.0; wt[0]['EUR cap [GSm³]'] = 2.0
    assert apply_tank_rows(n, tt, 200.0, set_gas_pmin=True) == 1 and apply_well_rows(n, wt) == 1
    t = next(x for x in n if x['kind'] == 'reservoir')['params']; w = next(x for x in n if x['id'] == wt[0]['Well ID'])['params']
    assert t['target_rf'] == pytest.approx(0.7) and t['rf_taper_days'] == 200 and t['min_pressure_bar'] > 1
    assert w['max_gas_rate_sm3d'] == pytest.approx(1.5e6) and w['calibrate_rate'] == pytest.approx(1e6) and w['eur_cap'] == pytest.approx(2e9)
    assert well_table(n)[0]['EUR cap [GSm³]'] == pytest.approx(2.0)
    tt[0]['Target RF [%]'] = None; apply_tank_rows(n, tt); assert 'target_rf' not in t


def test_forecast_tab_shows_assumptions_panel():
    n, e = _case()
    root = run_app('app.py', {'nodes': n, 'edges': e})
    assert ('markdown', '**Recovery factor per tank**') in root.calls and ('markdown', '**Rates per well**') in root.calls
    assert any(c[0] == 'button' and c[1] == 'Apply assumptions' for c in root.calls)


def test_tank_z_factor_matches_well_model_and_is_sane_at_hpht():
    from network.reservoir_mb import z_factor
    from physics.pvt_model import gas_z
    for p in (50, 150, 300, 450, 600):
        assert z_factor(p, 120, 0.7) == pytest.approx(gas_z(p, 120, 0.7), rel=1e-9)
    assert 1.05 < z_factor(450, 120, 0.7) < 1.2          # was 1.28 with the Papay form


def test_volumetric_gas_tank_follows_p_over_z_line():
    from network.reservoir_mb import Tank, z_factor
    t = Tank({'id': 'T', 'kind': 'reservoir', 'name': 'T', 'params': {'fluid_phase': 'gas', 'reservoir_pressure_bar': 400, 'temperature_c': 110, 'giip_sm3': 5e9, 'gas_sg': 0.65, 'min_pressure_bar': 5}})
    pz0 = t.p / z_factor(t.p, t.t, t.gas_sg)
    for _ in range(4): t.step(0, 0, 5e8, 0, 0, 100)
    assert (t.p / z_factor(t.p, t.t, t.gas_sg)) / pz0 == pytest.approx(1 - t.gp / t.g, abs=1e-3)


def test_rf_cap_accounts_for_availability():
    from network.assumptions import compute_caps
    from network.reservoir_mb import Tank
    tk = Tank({'id': 'T', 'kind': 'reservoir', 'name': 'T', 'params': {'fluid_phase': 'oil', 'stoiip_sm3': 1e6, 'target_rf': 0.1, 'rf_taper_days': 100}})
    nn = [{'id': 'W', 'kind': 'well', 'params': {'reservoir_id': 'T', 'water_cut': 0.0, 'availability_factor': 0.5}}]
    caps, shut, _ = compute_caps(nn, {'W': {'liquid_rate_m3d': 5000.0}}, {'T': tk}, {'W': {'cum_oil': 0.0, 'cum_gas': 0.0}})
    assert caps['W'] * 0.5 == pytest.approx(1e5 / 100, rel=1e-6)    # delivered rate (after uptime) equals R/tau
