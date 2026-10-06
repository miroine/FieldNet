"""Decline curves and external-table prediction sources."""
import copy
import math
import numpy as np
import pandas as pd
import pytest
from network.examples import demo_field_case
from network.forecast import run_forecast
from network.prediction_sources import (apply_prediction_sources, arps_rate, arps_cumulative, fit_arps, parse_external_csv,
                                        prediction_preview, arps_abandonment_time)
from physics.well_model import well_settings

Y = 365.25


def _trapz(y, x):
    return float(np.sum(0.5 * (y[1:] + y[:-1]) * np.diff(x)))


def test_arps_closed_forms():
    qi, di = 1000.0, 0.3; t = np.array([0.0, 365.25, 1000.0, 3000.0])
    assert np.allclose(arps_rate(t, qi, di, 0.0), qi * np.exp(-di * t / Y), rtol=1e-12)
    assert np.allclose(arps_rate(t, qi, di, 1.0), qi / (1 + di * t / Y), rtol=1e-12)
    assert np.allclose(arps_rate(t, qi, di, 0.5), qi / (1 + 0.5 * di * t / Y) ** 2, rtol=1e-12)
    assert arps_rate(0.0, qi, di, 1.3) == pytest.approx(qi)


def test_arps_cumulative_matches_numerical_integral_and_closed_forms():
    qi, di = 800.0, 0.25
    tt = np.linspace(0, 6 * Y, 60001)
    for b in (0.0, 0.5, 1.0, 1.5):
        q = arps_rate(tt, qi, di, b)
        numeric = _trapz(q, tt)
        assert arps_cumulative(6 * Y, qi, di, b) == pytest.approx(numeric, rel=1e-5)
    assert arps_cumulative(2 * Y, qi, di, 0.0) == pytest.approx(qi / di * (1 - math.exp(-2 * di)) * Y)
    assert arps_cumulative(2 * Y, qi, di, 1.0) == pytest.approx(qi / di * math.log(1 + 2 * di) * Y)


def test_modified_hyperbolic_and_abandonment():
    qi, di, b, dt = 1000.0, 0.5, 1.0, 0.1
    ts = (di / dt - 1) / (b * di) * Y  # switch time [d]
    q = arps_rate(np.array([ts - 1, ts, ts + 365.25]), qi, di, b, dt)
    assert q[1] == pytest.approx(qi / (1 + b * di * ts / Y) ** (1 / b))
    assert q[2] == pytest.approx(q[1] * math.exp(-dt))
    # continuity of cumulative and numerical agreement
    tt = np.linspace(0, 20 * Y, 80001)
    assert arps_cumulative(20 * Y, qi, di, b, dt) == pytest.approx(_trapz(arps_rate(tt, qi, di, b, dt), tt), rel=1e-5)
    # abandonment: rate is zero below the limit and cumulative stops growing
    ta = arps_abandonment_time(qi, 0.3, 0.0, None, 100.0)
    assert ta == pytest.approx(math.log(10) / 0.3 * Y)
    assert arps_rate(ta - 1, qi, 0.3, 0.0, None, 100.0) > 0 and arps_rate(ta + 1, qi, 0.3, 0.0, None, 100.0) == 0
    assert arps_cumulative(ta * 3, qi, 0.3, 0.0, None, 100.0) == pytest.approx(arps_cumulative(ta, qi, 0.3, 0.0))


@pytest.mark.parametrize('b', [0.0, 0.5, 1.0])
def test_fit_arps_recovers_noise_free_parameters(b):
    t = np.arange(0, 5 * Y, 30.0); q = arps_rate(t, 900.0, 0.35, b)
    r = fit_arps(t, q, b=b)
    assert r['success'] and r['qi'] == pytest.approx(900.0, rel=0.01) and r['di_per_year'] == pytest.approx(0.35, rel=0.01) and r['r2'] > 0.9999
    free = fit_arps(t, q)
    assert free['success'] and free['qi'] == pytest.approx(900.0, rel=0.01) and free['r2'] > 0.9999
    if b > 0: assert free['b'] == pytest.approx(b, rel=0.05)


def test_fit_arps_failure_messages():
    r = fit_arps([0, 1], [10, 9]); assert not r['success'] and 'at least' in r['message']
    r = fit_arps([0, 1, 2, 3], [-1, -2, -3, -4], b=0.0); assert not r['success']
    r = fit_arps([0, 1, 2], [1, 2]); assert not r['success'] and 'same length' in r['message']


def wells(nodes): return [n for n in nodes if n['kind'] == 'well']


def with_source(nodes, wid, src):
    ns = copy.deepcopy(nodes)
    next(n for n in ns if n['id'] == wid)['params']['prediction_source'] = src
    return ns


def test_external_table_interpolation_step_hold_and_extrapolate():
    nodes, _ = demo_field_case()
    rows = [{'date': '2026-01-01', 'reservoir_pressure_bar': 300.0, 'water_cut': 0.1},
            {'date': '2026-01-11', 'reservoir_pressure_bar': 200.0, 'water_cut': float('nan'), 'pi_m3d_bar': 5.0},
            {'date': '2026-01-21', 'reservoir_pressure_bar': 100.0, 'water_cut': 0.5, 'pi_m3d_bar': 7.0}]
    src = {'type': 'external_table', 'rows': rows}
    def p(date, **kw):
        s = {**src, **kw}
        out = apply_prediction_sources(with_source(nodes, 'P1', s), date, '2026-01-01', {})
        return next(n for n in out if n['id'] == 'P1')['params']
    assert p('2026-01-06')['reservoir_pressure_bar'] == pytest.approx(250.0)
    assert p('2026-01-06')['water_cut'] == pytest.approx(0.1 + (0.5 - 0.1) * 5 / 20)   # NaN row skipped
    assert p('2026-01-06')['pi_m3d_bar'] == pytest.approx(5.0)                         # hold before first pi point
    assert p('2026-01-16', interp='step')['reservoir_pressure_bar'] == 200.0
    assert p('2026-03-01')['reservoir_pressure_bar'] == pytest.approx(100.0)           # hold last
    assert p('2026-02-01', extrapolate='linear')['reservoir_pressure_bar'] == pytest.approx(max(100 - 10 * (31 - 20) * 1.0, 0.1))
    q = next(n for n in apply_prediction_sources(with_source(nodes, 'P1', {**src, 'extrapolate': 'none'}), '2026-03-01', '2026-01-01', {}) if n['id'] == 'P1')['params']
    assert 'reservoir_pressure_bar' not in q or q['reservoir_pressure_bar'] != 100.0


def test_external_table_time_days_and_cum_oil_axes_and_max_oil_cap():
    nodes, _ = demo_field_case()
    src = {'type': 'external_table', 'x_axis': 'time_days', 'rows': [{'time_days': 0, 'pi_m3d_bar': 10.0}, {'time_days': 100, 'pi_m3d_bar': 20.0}]}
    out = apply_prediction_sources(with_source(nodes, 'P2', src), '2026-02-20', '2026-01-01', {})
    assert next(n for n in out if n['id'] == 'P2')['params']['pi_m3d_bar'] == pytest.approx(10.0 + 10.0 * 50 / 100)
    cum = {'x_axis': 'cum_oil_sm3', 'type': 'external_table', 'rows': [{'cum_oil_sm3': 0, 'water_cut': 0.1}, {'cum_oil_sm3': 1e6, 'water_cut': 0.6, 'max_oil_rate_m3d': 400.0}]}
    out = apply_prediction_sources(with_source(nodes, 'P2', cum), '2026-02-20', '2026-01-01', {'P2': {'cum_oil': 5e5}})
    pr = next(n for n in out if n['id'] == 'P2')['params']
    assert pr['water_cut'] == pytest.approx(0.35)
    assert pr['_potential_cap_m3d'] == pytest.approx(400.0 / (1 - 0.35))
    assert well_settings(pr)['max_rate'] == pytest.approx(400.0 / 0.65)


def test_potential_cap_is_min_with_network_and_user_caps():
    s = well_settings({'max_liquid_rate_m3d': 500.0, '_potential_cap_m3d': 300.0, '_network_cap_m3d': 400.0})['max_rate']
    assert s == 300.0
    assert well_settings({'max_liquid_rate_m3d': 200.0, '_potential_cap_m3d': 300.0})['max_rate'] == 200.0
    assert well_settings({'_potential_cap_m3d': 300.0})['max_rate'] == 300.0
    assert well_settings({})['max_rate'] == math.inf


def test_decline_source_sets_cap_with_basis_conversion_and_tables():
    nodes, _ = demo_field_case()
    base = {'type': 'decline', 'qi': 500.0, 'di_per_year': 0.4, 'b': 0.0}
    out = apply_prediction_sources(with_source(nodes, 'P1', {**base, 'basis': 'oil'}), '2027-01-01', '2026-01-01', {})
    pr = next(n for n in out if n['id'] == 'P1')['params']; q = 500.0 * math.exp(-0.4 * 365 / Y)
    assert pr['_decline_potential'] == pytest.approx(q) and pr['_potential_cap_m3d'] == pytest.approx(q / 0.95)
    out = apply_prediction_sources(with_source(nodes, 'P1', {**base, 'basis': 'gas'}), '2026-01-01', '2026-01-01', {})
    assert next(n for n in out if n['id'] == 'P1')['params']['_potential_cap_m3d'] == pytest.approx(500.0 / (110.0 * 0.95))
    out = apply_prediction_sources(with_source(nodes, 'P1', {**base, 'basis': 'liquid', 'water_cut_table': [
        {'date': '2026-01-01', 'value': 0.0}, {'date': '2027-01-01', 'value': 0.4}]}), '2026-07-02', '2026-01-01', {})
    pr = next(n for n in out if n['id'] == 'P1')['params']
    assert pr['water_cut'] == pytest.approx(0.2, abs=0.01) and pr['_potential_cap_m3d'] == pytest.approx(500.0 * math.exp(-0.4 * 182 / Y), rel=1e-3)
    # apply_as other than rate_cap -> no cap written
    out = apply_prediction_sources(with_source(nodes, 'P1', {**base, 'apply_as': 'none'}), '2026-01-01', '2026-01-01', {})
    assert '_potential_cap_m3d' not in next(n for n in out if n['id'] == 'P1')['params']


def test_none_unknown_ids_and_no_mutation():
    nodes, _ = demo_field_case()
    ns = with_source(nodes, 'P1', {'type': 'none'})
    ns.append({'id': 'X', 'kind': 'well', 'name': 'X', 'params': {'prediction_source': {'type': 'decline', 'qi': 100.0, 'di_per_year': 0.2}}})
    snap = copy.deepcopy(ns)
    out = apply_prediction_sources(ns, '2026-06-01', '2026-01-01', {'unknown_well': {'cum_oil': 1.0}})
    assert ns == snap and out is not ns
    assert '_potential_cap_m3d' not in next(n for n in out if n['id'] == 'P1')['params']
    assert '_potential_cap_m3d' in next(n for n in out if n['id'] == 'X')['params']
    with pytest.raises(ValueError): apply_prediction_sources(with_source(nodes, 'P1', {'type': 'magic'}), '2026-01-01', '2026-01-01')


def test_parse_external_csv_aliases_and_unit_conversion():
    txt = "Date,Pres (psi),WCT (%),GOR (scf/stb),Rate (stb/d),PI (stb/d/psi)\n2026-01-01,3000,10,500,1000,2.0\n2026-07-01,2500,,520,800,1.8\n"
    rows, warn = parse_external_csv(txt)
    assert len(rows) == 2 and rows[0]['date'] == '2026-01-01'
    assert rows[0]['reservoir_pressure_bar'] == pytest.approx(3000 * 0.0689476)
    assert rows[0]['water_cut'] == pytest.approx(0.10) and 'water_cut' not in rows[1]
    assert rows[0]['gor_sm3sm3'] == pytest.approx(500 * 0.0283168466 / 0.158987294928)
    assert rows[0]['max_liquid_rate_m3d'] == pytest.approx(1000 * 0.158987294928)
    assert rows[0]['pi_m3d_bar'] == pytest.approx(2.0 * 0.158987294928 / 0.0689476)
    assert any('psi -> bar' in w for w in warn) and any('% -> fraction' in w for w in warn)


def test_parse_external_csv_defaults_semicolon_and_errors():
    rows, warn = parse_external_csv("Time;Pressure;Water cut;Foo\n0;250;0.2;1\n365;240;0.3;2\n")
    assert rows[1]['time_days'] == 365 and rows[1]['reservoir_pressure_bar'] == 240 and rows[1]['water_cut'] == 0.3
    assert any('assuming bar' in w for w in warn) and any("'Foo'" in w for w in warn) and any('days' in w for w in warn)
    rows, warn = parse_external_csv(pd.DataFrame({'Time (years)': [0, 1], 'WCT': [20, 30]}))
    assert rows[1]['time_days'] == pytest.approx(365.25) and rows[1]['water_cut'] == pytest.approx(0.3)
    assert any('percent' in w for w in warn)
    with pytest.raises(ValueError): parse_external_csv("Pressure,WCT\n1,2\n")
    with pytest.raises(ValueError): parse_external_csv("")


def test_prediction_preview_shapes():
    d = prediction_preview({'type': 'decline', 'qi': 100.0, 'di_per_year': 0.2, 'b': 0.0, 'basis': 'oil'}, '2026-01-01', 2, 30)
    assert list(d.columns)[:2] == ['Date', 'Day'] and d['Rate (oil)'].iloc[0] == 100.0 and d['Cumulative'].is_monotonic_increasing
    e = prediction_preview({'type': 'external_table', 'rows': [{'date': '2026-01-01', 'water_cut': 0.1}, {'date': '2027-01-01', 'water_cut': 0.5}]}, '2026-01-01', 2, 90)
    assert e['water_cut'].iloc[0] == pytest.approx(0.1) and e['water_cut'].iloc[-1] == pytest.approx(0.5) and 'pi_m3d_bar' not in e.columns


def test_forecast_decline_cap_respected_each_step():
    nodes, edges = demo_field_case()
    base = run_forecast(nodes, edges, '2026-01-01', 1.0, 60)
    qi, di = 250.0, 0.5
    ns = with_source(nodes, 'P1', {'type': 'decline', 'basis': 'liquid', 'qi': qi, 'di_per_year': di, 'b': 0.0})
    fc = run_forecast(ns, edges, '2026-01-01', 1.0, 60)
    rows = [r for r in fc['wells'] if r['Well ID'] == 'P1']; assert rows
    b0 = next(r for r in base['wells'] if r['Well ID'] == 'P1')['Liquid [m3/d]']
    assert b0 > qi * 1.2  # the cap actually binds
    for r in rows:
        day = (pd.Timestamp(r['Date']) - pd.Timestamp('2026-01-01')).days
        assert r['Liquid [m3/d]'] <= qi * math.exp(-di * day / Y) * (1 + 1e-6) + 1e-6
    assert rows[-1]['Liquid [m3/d]'] < rows[0]['Liquid [m3/d]']


def test_forecast_follows_external_pressure_table():
    nodes, edges = demo_field_case()
    rows = [{'date': '2026-01-01', 'reservoir_pressure_bar': 280.0}, {'date': '2026-07-01', 'reservoir_pressure_bar': 200.0}]
    ns = with_source(nodes, 'P2', {'type': 'external_table', 'rows': rows})
    snap = copy.deepcopy(ns)
    fc = run_forecast(ns, edges, '2026-01-01', 0.5, 60)
    assert ns == snap
    for r in (x for x in fc['wells'] if x['Well ID'] == 'P2'):
        day = (pd.Timestamp(r['Date']) - pd.Timestamp('2026-01-01')).days
        exp = float(np.interp(day, [0, 181], [280.0, 200.0]))
        assert r['Reservoir pressure [bar]'] == pytest.approx(exp, abs=0.05)
    # the other wells still follow the tank
    assert next(x for x in fc['wells'] if x['Well ID'] == 'P1')['Reservoir pressure [bar]'] != pytest.approx(200.0, abs=1.0)
