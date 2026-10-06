"""Uptime / downtime of wells, compressors, pumps, separators and lines in the prognosis."""
import os
from network import availability as av
from network.templates import build
from network.forecast import run_forecast
from solver.v21 import solve_v21
from tests.support.app_harness import run_app
APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app.py')


def test_uptime_combination():
    assert av.uptime({'params': {}}) == 1.0
    assert abs(av.uptime({'params': {'uptime': 0.9, 'mtbf_days': 90, 'mttr_days': 10, 'planned_downtime_days_per_year': 36.525}}) - 0.9 * 0.9 * 0.9) < 1e-9
    assert av.uptime({'params': {'uptime': 95}}) == 0.95


def test_series_parallel_factors():
    nodes = [{'id': 'a', 'kind': 'well', 'params': {'uptime': 0.9}}, {'id': 'b', 'kind': 'well', 'params': {}}, {'id': 'm', 'kind': 'manifold', 'params': {}},
             {'id': 'c1', 'kind': 'manifold', 'params': {}}, {'id': 's', 'kind': 'separator', 'params': {'uptime': 0.5}}]
    edges = [{'id': 'e1', 'source': 'a', 'target': 'm', 'kind': 'pipeline', 'params': {}}, {'id': 'e2', 'source': 'b', 'target': 'm', 'kind': 'pipeline', 'params': {}},
             {'id': 'p1', 'source': 'm', 'target': 's', 'kind': 'compressor', 'params': {'uptime': 0.8}}, {'id': 'p2', 'source': 'm', 'target': 's', 'kind': 'compressor', 'params': {'uptime': 0.6}}]
    f = av.delivery_factors(nodes, edges, {'e1': 10, 'e2': 10, 'p1': 15, 'p2': 5})
    assert abs(f['m'] - 0.5 * (0.75 * 0.8 + 0.25 * 0.6)) < 1e-12 and abs(f['a'] - 0.9 * f['m']) < 1e-12 and abs(f['b'] - f['m']) < 1e-12
    g = av.supply_factors(nodes, edges, {'e1': 10, 'e2': 10, 'p1': 15, 'p2': 5})
    assert abs(g['m'] - 0.95) < 1e-12 and abs(g['s'] - 0.5 * (0.75 * 0.8 + 0.25 * 0.6) * 0.95) < 1e-12


def test_forecast_reflects_equipment_downtime():
    n, e = build('subsea_compressor_gas'); base = run_forecast(n, e, '2028-01-01', years=1, step_days=180)['field']
    for x in e:
        if x['id'] == 'SUBSEA-COMP': x['params']['uptime'] = 0.8
    for x in n:
        if x['kind'] == 'separator': x['params']['uptime'] = 0.9
    fc = run_forecast(n, e, '2028-01-01', years=1, step_days=180); f = fc['field']
    assert abs(f[0]['Gas [Sm3/d]'] / base[0]['Gas [Sm3/d]'] - 0.72) < 0.02 and abs(f[0]['Uptime [%]'] - 72) < 2
    assert f[0]['Gas deferred [Sm3/d]'] > 0 and base[0]['Gas deferred [Sm3/d]'] == 0
    assert abs(sum(w['Gas [Sm3/d]'] for w in fc['wells'] if w['Date'] == f[0]['Date']) - f[0]['Gas [Sm3/d]']) < 1e-3 * f[0]['Gas [Sm3/d]']   # wells still add up to the field
    assert f[-1]['Cumulative gas [Sm3]'] < base[-1]['Cumulative gas [Sm3]'] * 0.8


def test_pump_and_injector_availability():
    n, e = build('subsea_booster_pump')
    for x in e:
        if x['id'] == 'BOOSTER': x['params']['uptime'] = 0.5
    assert run_forecast(n, e, '2028-01-01', years=0.5, step_days=180)['field'][0]['Uptime [%]'] < 60
    n, e = build('waterflood_pattern'); base = run_forecast(n, e, '2028-01-01', years=0.5, step_days=180)['field'][0]['Water injection [m3/d]']
    for x in n:
        if x['kind'] == 'water_source': x['params']['uptime'] = 0.5
    assert run_forecast(n, e, '2028-01-01', years=0.5, step_days=180)['field'][0]['Water injection [m3/d]'] < base * 0.6


def test_register_roundtrip_and_typical():
    n, e = build('topside_compressor_gas'); k = av.apply_typical(n, e); assert k >= 8 and av.has_any(n, e)
    assert all('uptime' not in (x.get('params') or {}) for x in n if x['kind'] == 'reservoir')
    rows = av.register(n, e); assert len(rows) == len([x for x in n if x['kind'] != 'reservoir']) + len(e)
    for r in rows:
        if r['Kind'] == 'compressor': r['Uptime [%]'] = 80.0; r['MTBF [d]'] = 100.0; r['MTTR [d]'] = 25.0
    assert av.apply_register(n, e, rows) >= 1
    c = next(x for x in e if x['kind'] == 'compressor'); assert abs(av.uptime(c) - 0.8 * 0.8) < 1e-9
    av.clear(n, e); assert not av.has_any(n, e)


def test_page_renders_and_buttons_work():
    n, e = build('subsea_compressor_gas')
    r = run_app(APP, {'nodes': n, 'edges': e}, pressed={'solve_btn_top'}); assert not [c for c in r.calls if c[0] in ('error', 'exception')]
    r2 = run_app(APP, dict(r.session_state), pressed={'av_typical'}); assert av.has_any(r2.session_state['nodes'], r2.session_state['edges'])
    st = dict(r2.session_state); r3 = run_app(APP, st, pressed={'solve_btn_top', 'fc_run'}); assert not [c for c in r3.calls if c[0] in ('error', 'exception')]
    assert r3.session_state['forecast']['field'][0]['Uptime [%]'] < 100
    r4 = run_app(APP, dict(r3.session_state)); assert not [c for c in r4.calls if c[0] in ('error', 'exception')]
