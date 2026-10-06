"""Template library: every template builds, is structurally valid, solves, and shows what it claims to show; UI loader through the harness."""
import os, pytest
from network.templates import TEMPLATES, build, catalogue, categories, pipe, PIPE_OIL, PIPE_GAS
from solver.v21 import solve_v21
from tests.support.app_harness import run_app
APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app.py')
KEYS = list(TEMPLATES)


def test_registry_complete():
    need = {'hpht_4slot_gas', 'daisy_chain_oil', 'multi_tank_commingled', 'oil_gas_injection', 'pure_depletion_oil', 'wellhead_platform_tieback', 'subsea_booster_pump',
            'subsea_compressor_gas', 'topside_compressor_gas', 'horizontal_wells', 'hpht_tight_gas_frac'}
    assert need <= set(KEYS) and len(KEYS) >= 15
    for k, t in TEMPLATES.items():
        assert t['name'] and t['shows'] and t['watch'] and t['category'] in categories() and t['years'] > 0 and t['step'] in (30, 60, 90, 180, 365), k
    assert len(catalogue()) == len(KEYS)


@pytest.mark.parametrize('key', KEYS)
def test_structure(key):
    n, e = build(key); ids = [x['id'] for x in n]; eids = [x['id'] for x in e]
    assert len(set(ids)) == len(ids) and len(set(eids)) == len(eids)
    for x in e: assert x['source'] in ids and x['target'] in ids and x['source'] != x['target'], (key, x['id'])
    for x in n: assert all(isinstance(x.get(c), (int, float)) for c in ('x', 'y')), (key, x['id'])
    tanks = {x['id'] for x in n if x['kind'] == 'reservoir'}
    for w in n:
        if w['kind'] == 'well' and w['params'].get('reservoir_id'): assert w['params']['reservoir_id'] in tanks, (key, w['id'])
    for t in n:
        for c in (t.get('params') or {}).get('communication') or []: assert c['to'] in tanks, (key, t['id'])
    connected = {x['source'] for x in e} | {x['target'] for x in e}
    assert all(x['id'] in connected for x in n if x['kind'] != 'reservoir'), key
    a, b = build(key); a[0]['name'] = 'changed'; assert build(key)[0][0]['name'] != 'changed'


@pytest.mark.parametrize('key', KEYS)
def test_solves_and_flows(key):
    n, e = build(key); r = solve_v21(n, e)
    assert r[2]['success'] and r[2]['max_abs_residual'] < 1e-3, key
    wells = [x for x in n if x['kind'] == 'well']; flowing = [w for w in wells if (r[3].get(w['id']) or {}).get('status', 'flowing') == 'flowing' or abs(r[1].get(w['id'], 0)) > 0]
    assert wells and sum(abs(v) for v in r[1].values()) > 0 and flowing, key


@pytest.mark.parametrize('key', ['simple_well', 'gas_lift_field', 'waterflood_pattern', 'pure_depletion_oil'])
def test_short_forecast(key):
    from network.forecast import run_forecast
    n, e = build(key); fc = run_forecast(n, e, TEMPLATES[key]['start'], years=1, step_days=180)
    f = fc['field']; assert len(f) >= 2 and all(r['Converged'] for r in f) and f[0]['Wells flowing'] > 0 and f[-1]['Oil [m3/d]'] < f[0]['Oil [m3/d]'] * 1.01


def test_pump_and_compressors_show_benefit():
    for key, cid, ex, pg in (('subsea_booster_pump', 'BOOSTER', 'TIEBACK', PIPE_OIL), ('topside_compressor_gas', 'EXP-COMP', 'EXPORT', PIPE_GAS), ('subsea_compressor_gas', 'SUBSEA-COMP', 'TIEBACK', PIPE_GAS)):
        n, e = build(key)
        if key != 'subsea_booster_pump':
            for x in n:
                if x['kind'] in ('reservoir', 'well'): x['params']['reservoir_pressure_bar'] = 100.0
        with_eq = solve_v21(n, e); e2 = [x for x in e if x['id'] != cid] + [pipe('BYP', 'M1', 'M2', 50, 0.3, 0, **pg)]; without = solve_v21(n, e2)
        assert with_eq[1][ex] > without[1][ex] * 1.2, key


def test_gas_injection_and_multitank_and_horizontal_content():
    n, e = build('oil_gas_injection'); assert sum(1 for x in n if x['kind'] == 'gas_injector') == 2 and any(x['kind'] == 'compressor' for x in e)
    n, e = build('multi_tank_commingled'); assert sum(1 for x in n if x['kind'] == 'reservoir') == 3 and sum(len(x['params'].get('communication') or []) for x in n) >= 2 and sum(1 for x in n if x['kind'] == 'joint') == 2
    n, e = build('horizontal_wells'); hz = [x for x in n if x['kind'] == 'well' and x['params'].get('trajectory')]; assert hz and all(x['params'].get('completion') for x in hz)
    n, e = build('hpht_tight_gas_frac'); assert sum(1 for x in n if x['kind'] == 'well') == 8 and all(x['params']['skin'] < -3 for x in n if x['kind'] == 'well')
    n, e = build('hpht_4slot_gas'); assert sum(1 for x in n if x['kind'] == 'well') == 4 and max(x['params']['reservoir_pressure_bar'] for x in n if x['kind'] == 'reservoir') >= 700


def _no_errors(root):
    errs = [c for c in root.calls if c[0] in ('error', 'exception')]; assert not errs, errs


def test_loader_button_replaces_model_and_sets_forecast_defaults():
    from network.examples import demo_field_case
    n, e = demo_field_case()
    root = run_app(APP, {'nodes': n, 'edges': e, 'tpl_sel': 'subsea_booster_pump', 'tpl_cat': 'All', 'forecast': {'field': []}}, pressed={'tpl_load'}); _no_errors(root)
    ss = root.session_state; assert {x['id'] for x in ss['nodes']} == {x['id'] for x in build('subsea_booster_pump')[0]}
    assert ss['tpl_forecast']['template'] == 'subsea_booster_pump' and 'forecast' not in ss


def test_load_as_new_case_and_page_renders():
    from network.examples import demo_field_case
    n, e = demo_field_case(); _no_errors(run_app(APP, {'nodes': n, 'edges': e}))
    root = run_app(APP, {'nodes': n, 'edges': e, 'tpl_sel': 'daisy_chain_oil'}, pressed={'tpl_load_case'}); _no_errors(root)
    lib = root.session_state['case_library']; assert len(lib) == 1 and 'Daisy' in next(iter(lib.cases.values()))['name']


def test_templates_are_canvas_stable():
    """The canvas re-normalises the model on every move; a freshly loaded template must hash the same afterwards (no re-solve after moving a box)."""
    from ui.graph_contract import normalize_graph, graph_hash
    for k in KEYS:
        n, e = build(k); n2, e2, _ = normalize_graph(n, e); assert graph_hash(n, e) == graph_hash(n2, e2), k
        for x in n2: x['x'] = x['x'] + 37; x['y'] = x['y'] - 11
        assert graph_hash(n, e) == graph_hash(n2, e2), k


def test_old_models_with_default_edge_params_keep_hash():
    from ui.graph_contract import normalize_graph, graph_hash
    n, e = build('simple_well')
    for x in e: x['params'] = {}
    n2, e2, _ = normalize_graph(n, e); assert graph_hash(n, e) == graph_hash(n2, e2)


def test_sidebar_example_loader():
    from network.examples import demo_field_case
    n, e = demo_field_case()
    root = run_app(APP, {'nodes': n, 'edges': e, 'sb_tpl': 'hpht_tight_gas_frac'}, pressed={'sb_tpl_load'}); _no_errors(root)
    assert {x['id'] for x in root.session_state['nodes']} == {x['id'] for x in build('hpht_tight_gas_frac')[0]}


def test_network_display_modes():
    from network.net_display import label_maps, MODES, AUTO, browse_frame, fmt_gas, field_is_gas
    for key, expect in (('hpht_4slot_gas', 'MSm³/d'), ('daisy_chain_oil', 'WC')):
        n, e = build(key); r = solve_v21(n, e)
        nl, el = label_maps(n, e, r, AUTO); assert nl and el and any(expect in v for v in nl.values()), key
        for m in MODES:
            a, b = label_maps(n, e, r, m); assert isinstance(a, dict) and isinstance(b, dict) and (len(b) == len(e) or not b), m
        assert any('MSm³/d' in v for v in label_maps(n, e, r, 'Gas rate')[0].values())
        assert len(browse_frame(n, e, r, 'Nodes', 'Pressure [bar]')) and len(browse_frame(n, e, r, 'Lines', 'Gas [Sm3/d]'))
    assert label_maps(n, e, None, AUTO) == ({}, {}) and fmt_gas(2.5e6) == '2.50 MSm³/d' and fmt_gas(5e3) == '5.0 kSm³/d'


def test_network_tab_selector_and_browse_render():
    n, e = build('hpht_4slot_gas'); s = {'nodes': n, 'edges': e}
    r1 = run_app(APP, s, pressed={'solve_btn_top'}); _no_errors(r1)
    st = dict(r1.session_state); st['net_show'] = 'Gas rate'; st['br_scope'] = 'Lines'; st['br_par'] = 'Gas [Sm3/d]'
    _no_errors(run_app(APP, st))


def test_phase_detection_and_kpis():
    from network.phase_pref import detect, resolve
    from network.forecast import run_forecast
    from network.prognosis import forecast_kpis
    for key, ph in (('hpht_4slot_gas', 'Gas'), ('subsea_compressor_gas', 'Gas'), ('daisy_chain_oil', 'Oil'), ('gas_condensate_tieback', 'Gas'), ('pure_depletion_oil', 'Oil')):
        n, e = build(key); assert detect(n) == ph, key; assert detect(n, solve_v21(n, e)) == ph, key
    assert resolve('Gas', n) == 'Gas' and resolve('Oil', n) == 'Oil' and resolve('Auto', n) == 'Oil'
    n, e = build('subsea_compressor_gas'); k = forecast_kpis(run_forecast(n, e, '2028-01-01', years=1, step_days=180))
    assert k['peak_gas_sm3d'] > 1e6 and k['plateau_gas_years'] > 0 and k['rf_gas_pct'] and k['final_gas_sm3d'] > 0


def test_primary_phase_switch_renders_everywhere():
    for key, pref in (('hpht_4slot_gas', 'Auto'), ('hpht_4slot_gas', 'Oil'), ('daisy_chain_oil', 'Gas'), ('daisy_chain_oil', 'Auto')):
        n, e = build(key)
        r1 = run_app(APP, {'nodes': n, 'edges': e, 'primary_phase_pref': pref, 'fc_years': 1.0, 'fc_step': 180}, pressed={'solve_btn_top', 'fc_run'}); _no_errors(r1)
        ss = r1.session_state; assert ss['_phase_resolved'] == (pref if pref != 'Auto' else ('Gas' if 'gas' in key else 'Oil')), (key, pref)
        _no_errors(run_app(APP, dict(ss, nodal_well=next(x['id'] for x in n if x['kind'] == 'well'))))


def test_line_thickness_follows_flow_and_date_slider():
    from network.net_display import edge_widths, labels_from_rows, max_flow
    w = edge_widths({'a': 100.0, 'b': 25.0, 'c': 0.0, 'd': -100.0}); assert w['a'] == w['d'] > w['b'] > w['c'] == 1.0 and abs(w['b'] - (1.5 + 9.5 * 0.5)) < 1e-9
    assert edge_widths({'a': 50.0}, qmax=100.0)['a'] < edge_widths({'a': 100.0}, qmax=100.0)['a']
    from network.forecast import run_forecast
    from ui.results_browser import network_svg_at
    n, e = build('daisy_chain_oil'); fc = run_forecast(n, e, '2028-01-01', years=2, step_days=180)
    d0, d1 = fc['field'][0]['Date'], fc['field'][-1]['Date']
    a = network_svg_at(n, e, fc, d0); b = network_svg_at(n, e, fc, d1); c = network_svg_at(n, e, fc, d0, thickness=False)
    assert a != b and c != a
    import re
    wa = sorted(set(re.findall(r'stroke="#456" stroke-width="([\d.]+)"', a))); assert len(wa) > 2
    nr = [r for r in fc['nodes'] if r['Date'] == d0]; er = [r for r in fc['edges'] if r['Date'] == d0]
    nl, el = labels_from_rows(nr, er, 'Gas rate'); assert nl and all('MSm' in v or v in ('pipeline', 'pump') or 'kSm' in v for v in el.values())
    assert max_flow(fc['edges']) > 0


def test_merged_development_schedule_tab():
    n, e = build('daisy_chain_oil')
    r = run_app(APP, {'nodes': n, 'edges': e, 'fc_years': 1.0, 'fc_step': 180, 'fc_use_drill': True, 'sch_rigs': 2}, pressed={'fc_run'}); _no_errors(r)
    ss = r.session_state; assert ss['_fc_mode'] == 'drill' and ss['forecast'] and ss['sched_result']
    first = [row for row in ss['forecast']['field']][0]; assert first['Wells flowing'] < 6          # phased in, not all at start
    r2 = run_app(APP, dict(ss, fc_use_drill=False), pressed={'fc_run'}); _no_errors(r2)
    assert r2.session_state['_fc_mode'] == 'plain' and r2.session_state['forecast']['field'][0]['Wells flowing'] == 6 and 'sched_result' not in r2.session_state
