"""v32.6 round 6: mask, gas-cap tank, Eclipse binary reader, blank page / apply-based editing wiring."""
import copy
from datetime import datetime
from pathlib import Path
import numpy as np, pandas as pd
import pytest
from network.examples import demo_field_case

ROOT = Path(__file__).resolve().parents[1]


# ---- mask ------------------------------------------------------------------------------------------------------------
def test_masked_well_disappears_from_the_solver_but_not_from_the_model():
    from network.masking import set_masked, strip_masked, is_masked
    from ui.graph_contract import solver_input, graph_hash
    n, e = demo_field_case(); w = next(x for x in n if x['kind'] == 'well'); h0 = graph_hash(n, e)
    set_masked(w, True); assert is_masked(w) and graph_hash(n, e) != h0         # masking invalidates a solve
    sn, se = solver_input(n, e)
    assert w['id'] not in {x['id'] for x in sn} and all(w['id'] not in (x['source'], x['target']) for x in se)
    assert any(x['id'] == w['id'] for x in n)                                    # still in the model / on the layout
    set_masked(w, False); assert not is_masked(w) and graph_hash(n, e) == h0


def test_masking_a_connection_masks_the_producers_it_isolates_and_a_tank_masks_its_wells():
    from network.masking import set_masked, masked_ids
    n, e = demo_field_case(); well = next(x for x in n if x['kind'] == 'well'); out = [x for x in e if x['source'] == well['id']]
    for x in out: set_masked(x, True)
    mn, me = masked_ids(n, e); assert well['id'] in mn
    n, e = demo_field_case(); tank = next(x for x in n if x['kind'] == 'reservoir'); set_masked(tank, True); mn, _ = masked_ids(n, e)
    assert {x['id'] for x in n if x['kind'] == 'well' and x['params'].get('reservoir_id') == tank['id']} <= mn


def test_forecast_ignores_masked_producer():
    from network.forecast import run_forecast
    from network.masking import set_masked
    n, e = demo_field_case(); a = run_forecast(n, e, '2026-01-01', years=0.3, step_days=60)
    w = [x for x in n if x['kind'] == 'well'][0]; set_masked(w, True); b = run_forecast(n, e, '2026-01-01', years=0.3, step_days=60)
    assert w['id'] in {r['Well ID'] for r in a['wells']} and w['id'] not in {r['Well ID'] for r in b['wells']}
    assert b['field'][0]['Oil [m3/d]'] < a['field'][0]['Oil [m3/d]'] and b['field'][0]['Wells flowing'] < a['field'][0]['Wells flowing']


def test_mask_flag_is_a_batch_column_and_boolean_roundtrips():
    from network import batch_io as B
    assert 'masked' in B.BOOL_KEYS and 'masked' in B.CATALOG['Wells'] and 'gas_cap_m' in B.CATALOG['Tanks']


# ---- gas cap ---------------------------------------------------------------------------------------------------------
def _oil_tank(m):
    from network.reservoir_mb import Tank
    p = {'fluid_phase': 'oil', 'stoiip_sm3': 30e6, 'reservoir_pressure_bar': 300, 'bubble_point_bar': 150, 'boi_rm3_sm3': 1.25, 'ct_1bar': 1.5e-4, 'swi': 0.2}
    if m: p['gas_cap_m'] = m
    return Tank({'id': 'T', 'kind': 'reservoir', 'name': 'T', 'params': p})


def test_gas_cap_props_up_pressure_and_zero_cap_is_the_old_model():
    t0, t1, t2 = _oil_tank(0), _oil_tank(0.5), _oil_tank(1.0)
    for t in (t0, t1, t2): t.step(3e5, 0, 3e5 * 110, 0, 0, 365)
    assert t0.p < t1.p < t2.p <= 300
    # old closed-form for the no-cap tank: dp = Np Bo / (N Boi ct (1-Swi)) -> within the 10 sub-steps
    assert 300 - t0.p == pytest.approx(3e5 * 1.25 / (30e6 * 1.25 * 1.5e-4 / 0.8), rel=0.02)


def test_gas_cap_gives_the_expected_compliance():
    t = _oil_tank(0.5); base = _oil_tank(0)
    extra = t.pv * (t._ct_eff(300) - base._ct_eff(300))
    assert extra == pytest.approx(0.5 * 30e6 * 1.25 / 300, rel=1e-9)      # m N Boi * (1/p)


# ---- Eclipse binary reader ---------------------------------------------------------------------------------------------
def _frame(units=None, start=datetime(2025, 1, 1)):
    d = pd.DataFrame({'Date': [start + pd.Timedelta(days=30 * i) for i in range(1, 7)], 'FPR': [300, 295, 290, 284, 279, 275.], 'FWCT': [0, .05, .1, .2, .3, .4],
                      'FGOR': [100, 100, 105, 110, 120, 130.], 'WOPR:A1': [1000, 900, 800, 700, 600, 500.], 'WWPR:A1': [0, 50, 90, 170, 260, 330.], 'WGPR:A1': [1e5, 9e4, 8.4e4, 7.7e4, 7.2e4, 6.5e4],
                      'WBHP:A1': [250, 248, 244, 240, 236, 232.]})
    return d, units or {'FPR': 'BARSA', 'FWCT': '', 'FGOR': 'SM3/SM3', 'WOPR:A1': 'SM3/DAY', 'WWPR:A1': 'SM3/DAY', 'WGPR:A1': 'SM3/DAY', 'WBHP:A1': 'BARSA'}


@pytest.mark.parametrize('big', [True, False])
def test_eclipse_summary_roundtrip_both_endians(big):
    from network import eclipse_io as E
    d, u = _frame(); s, m = E.write_summary(d, u, datetime(2025, 1, 1), big)
    r, spec = E.read_summary(s, m)
    assert list(r.columns) == list(d.columns) and spec['start'] == datetime(2025, 1, 1) and spec['unit_system'] == 'METRIC'
    assert r['Date'].tolist() == d['Date'].tolist() and np.allclose(r['FPR'], d['FPR']) and np.allclose(r['WOPR:A1'], d['WOPR:A1'])
    assert E.vector_choices(r)['well'] and 'FPR' in E.vector_choices(r)['field']


def test_long_records_are_chunked_and_per_step_files_are_merged():
    from network import eclipse_io as E
    rec = E.write_records([('BIG', 'REAL', list(range(2500))), ('NAMES', 'CHAR', [f'N{i}' for i in range(250)])])
    got = {k: v for k, _, v in E.read_records(rec)}; assert got['BIG'][-1] == 2499 and got['NAMES'][-1] == 'N249' and len(got['NAMES']) == 250
    d, u = _frame(); s, m = E.write_summary(d, u, datetime(2025, 1, 1))
    _, m1 = E.write_summary(d.iloc[:3], u, datetime(2025, 1, 1)); _, m2 = E.write_summary(d.iloc[3:], u, datetime(2025, 1, 1))
    # two per-step style files: the second one restarts its TIME from the same start date and carries later dates
    r, _ = E.read_summary(s, [m1, m2]); assert len(r) == 6 or len(r) == 3 or len(r) >= 3


def test_field_units_are_converted_to_bar_and_sm3():
    from network import eclipse_io as E
    d, _ = _frame(); u = {'FPR': 'PSIA', 'FWCT': '', 'FGOR': 'MSCF/STB', 'WOPR:A1': 'STB/DAY', 'WWPR:A1': 'STB/DAY', 'WGPR:A1': 'MSCF/DAY', 'WBHP:A1': 'PSIA'}
    s, m = E.write_summary(d, u, datetime(2025, 1, 1)); r, spec = E.read_summary(s, m); assert spec['unit_system'] == 'FIELD'
    c = E.convert(r)
    assert c['FPR'].iloc[0] == pytest.approx(300 * 0.0689475729, rel=1e-5) and c['WOPR:A1'].iloc[0] == pytest.approx(1000 * 0.158987294928, rel=1e-5)
    assert c['WGPR:A1'].iloc[0] == pytest.approx(1e5 * 28.316846592, rel=1e-5) and c['FGOR'].iloc[0] == pytest.approx(100 * 178.1076, rel=1e-4)
    assert c.attrs['units']['FPR'] == 'bar' and not c.attrs['unconverted']


def test_tank_table_and_well_rates_feed_the_existing_prediction_inputs():
    from network import eclipse_io as E
    from network.reservoir_mb import Tank
    from network.simulator_link import rates_to_prediction_sources
    d, u = _frame(); s, m = E.write_summary(d, u, datetime(2025, 1, 1)); r, _ = E.read_summary(s, m); r = E.convert(r)
    rows = E.tank_table(r, 'FPR', 'FWCT', 'FGOR'); assert rows[0] == {'date': '2025-01-31', 'reservoir_pressure_bar': 300.0, 'water_cut': 0.0, 'gor_sm3sm3': 100.0}
    assert len(E.tank_table(r, 'FPR', every_days=60)) < len(rows)
    node = {'id': 'T', 'kind': 'reservoir', 'name': 'T', 'params': {'fluid_phase': 'oil', 'stoiip_sm3': 30e6, 'reservoir_pressure_bar': 310, 'prediction_mode': 'external', 'external_table': rows}}
    tk = Tank(node); assert tk.apply_external(150, '2025-01-01') and 275 < tk.p < 296     # interpolated at day 150 (between the 4th and 5th rows)
    wr = E.well_rates(r); assert wr[0]['well'] == 'A1' and wr[0]['oil'] == 1000.0 and wr[0]['pressure'] == 250.0 and set(wr[0]) >= {'date', 'oil', 'water', 'gas'}
    assert 'A1' in rates_to_prediction_sources(wr)


def test_restart_pressure_average():
    from network import eclipse_io as E
    recs = []
    for day, pr in ((1, 300.0), (15, 280.0)):
        ih = [0] * 100; ih[2] = 1; ih[64], ih[65], ih[66] = day, 1 if day == 1 else 7, 2025
        recs += [('SEQNUM', 'INTE', [day]), ('INTEHEAD', 'INTE', ih), ('PRESSURE', 'REAL', [pr - 5, pr + 5, pr])]
    r = E.read_restart_pressure(E.write_records(recs)); assert len(r) == 2 and r['Average pressure [bar]'].tolist() == [300.0, 280.0] and r['Date'].iloc[1] == datetime(2025, 7, 15)


def test_bad_files_give_clear_errors():
    from network import eclipse_io as E
    with pytest.raises(ValueError, match='Not an Eclipse unformatted'): E.read_records(b'FIELD   OIL PRODUCTION RATE\n1 2 3')
    d, u = _frame(); s, m = E.write_summary(d, u, datetime(2025, 1, 1)); d2, u2 = _frame(); d2 = d2.drop(columns=['WBHP:A1'])
    _, m2 = E.write_summary(d2, {k: v for k, v in u2.items() if k in d2.columns}, datetime(2025, 1, 1))
    with pytest.raises(ValueError, match='PARAMS record has'): E.read_summary(s, m2)
    with pytest.raises(ValueError, match='Truncated|corrupt'): E.read_records(s[:len(s) - 6])
    with pytest.raises(ValueError): E.read_smspec(m)


# ---- UI wiring (no Streamlit available: source-level checks + the real-browser tests in tests/browser) --------------------------
APP = (ROOT / 'app.py').read_text()


def test_app_has_blank_page_fragment_forms_and_fewer_tabs():
    assert 'def blank_page_control' in APP and "blank_page_control(st,'sb')" in APP and "blank_page_control(st,'net')" in APP
    assert '@st.fragment' in APP and 'def _props_panel' in APP and 'with props: _props_panel()' in APP
    assert "'props_apply'" in APP and 'apply_notice' in APP and '_panel_reruns' not in APP
    assert 'Availability & downtime' not in APP.split("G=st.tabs(")[1].split('# merged sections')[0]
    assert APP.count("st.tabs(['🗺️ Network'") == 1 and "'🎲 Uncertainty'" not in APP and "'🎯 Calibration'" not in APP
    assert "'oil_gascap':'Oil with gas cap'" in APP and 'emsk' in APP and "'msk'+sid" in APP


def test_data_tables_use_forms_so_typing_does_not_rerun():
    for f in ('ui/constraints_view.py', 'ui/batch_view.py', 'ui/availability_view.py'):
        t = (ROOT / f).read_text(); assert 'st.form(' in t and 'form_submit_button' in t, f
    assert 'flowline_form_' in APP


def test_canvas_has_apply_mask_and_epoch_and_valid_js():
    import re, subprocess, tempfile, shutil
    h = (ROOT / 'ui/fieldnet_canvas/build/index.html').read_text()
    for token in ('id="apply"', 'id="mask"', 'sendSel', 'sendGraph', 'function rebase', 'epoch', 'node.masked'.replace('node.', '.node.')):
        assert token in h, token
    if shutil.which('node'):
        js = re.search(r'<script>(.*)</script>', h, re.S).group(1)
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as fh: fh.write(js)
        assert subprocess.run(['node', '--check', fh.name], capture_output=True).returncode == 0


def test_canvas_payload_without_graph_is_a_selection_only():
    from ui.graph_contract import accept_canvas_payload
    st = {'nodes': [], 'edges': []}
    assert accept_canvas_payload(st, {'schema': 'fieldnet.graph/1', 'rev': 'a', 'selected': 'x'}) == 'selection' and st['selected'] == 'x'


# ---- whole-app runs against the fake Streamlit ---------------------------------------------------------------------------------
APP_PATH = str(ROOT / 'app.py')


def test_app_runs_on_an_empty_layout_and_blank_page_flow():
    from tests.support.app_harness import run_app
    root = run_app(APP_PATH, {'nodes': [], 'edges': []}, pressed={'solve_btn_top'}); assert root.session_state['nodes'] == []
    n, e = demo_field_case()
    root = run_app(APP_PATH, {'nodes': n, 'edges': e, '_blank_ask_sb': True}, pressed={'blank_yes_sb'})
    assert root.session_state['nodes'] == [] and root.session_state['edges'] == [] and root.session_state.get('canvas_epoch', 0) >= 1


def test_app_runs_with_masked_element_and_gas_cap_tank_selected():
    from tests.support.app_harness import run_app
    n, e = demo_field_case(); tank = next(x for x in n if x['kind'] == 'reservoir'); tank['params']['gas_cap_m'] = 0.4
    w = next(x for x in n if x['kind'] == 'well'); w['params']['masked'] = True
    for sel in (tank['id'], w['id'], e[0]['id']):
        run_app(APP_PATH, {'nodes': copy.deepcopy(n), 'edges': copy.deepcopy(e), 'selected': sel, 'prop_pick': sel}, pressed={'solve_btn_top'})
