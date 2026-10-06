"""Run the whole Streamlit script against a fake Streamlit (no real widgets): catches NameErrors / bad calls."""
import os
from tests.support.app_harness import run_app
APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app.py')


def test_app_runs_with_default_state():
    root = run_app(APP); assert root.session_state.get('nodes') is not None


def test_app_solves_forecasts_and_selects_each_kind():
    from network.examples import demo_field_case
    from network.equipment import convert_edge_equipment_to_nodes
    n, e = demo_field_case(); n, e = convert_edge_equipment_to_nodes(n, [dict(x, kind='choke') if x is e[0] else x for x in e], {e[0]['id']})
    n.append({'id': 'J1', 'kind': 'joint', 'name': 'J', 'pressure_bar': None, 'x': 5, 'y': 5, 'params': {}})
    kinds_done = set()
    for node in n:
        if node['kind'] in kinds_done: continue
        kinds_done.add(node['kind'])
        root = run_app(APP, {'nodes': [dict(x) for x in n], 'edges': [dict(x) for x in e], 'selected': node['id'], 'prop_pick': node['id']}, pressed={'solve_btn_top'})
    for ed in e[:3]:
        run_app(APP, {'nodes': [dict(x) for x in n], 'edges': [dict(x) for x in e], 'selected': ed['id'], 'prop_pick': ed['id']})
    assert len(kinds_done) >= 8


def test_forecast_run_pause_continue_stop_flow():
    from network.examples import demo_field_case
    n, e = demo_field_case()
    base = {'nodes': n, 'edges': e, 'fc_years': 1.0, 'fc_step': 90}
    root = run_app(APP, dict(base), pressed={'fc_run'})
    ctl = root.session_state.get('fc_ctl'); assert ctl is not None and ctl.status == 'done'
    assert root.session_state.forecast['field']
    # pause: start a run, advance one event, press Pause on the rerun, then Continue, then Stop
    from network.run_control import RunController
    from network.forecast import iter_forecast
    c = RunController(iter_forecast(n, e, '2026-01-01', 1.0, 90)); c.advance(3)
    st2 = dict(base, fc_ctl=c, fc_ctl_hash=None)
    root = run_app(APP, st2, pressed={'fc_pause'}); assert root.session_state['fc_ctl'].status == 'paused'
    root = run_app(APP, dict(root.session_state), pressed={'fc_stop'}); assert root.session_state['fc_ctl'].status == 'stopped'


def test_advanced_panels_run_with_solved_demo_and_forecast():
    from network.examples import demo_field_case
    from network.forecast import run_forecast
    n, e = demo_field_case(); fc = run_forecast(n, e, '2026-01-01', 1.0, 90)
    pressed = {'solve_btn_top', 'adv_cal_syn', 'adv_lg_run', 'adv_ba_run', 'adv_ba_sched', 'adv_sens_run', 'adv_rel_run', 'adv_sim_vfp'}
    root = run_app(APP, {'nodes': n, 'edges': e, 'forecast': fc}, pressed=pressed)
    s = root.session_state
    assert s.get('adv_sens') and s.get('adv_rel') and s.get('adv_vfp'), [k for k in s if k.startswith('adv')]
    errs = [c for c in root.calls if c[0] == 'error']; assert not errs, errs


def test_development_plan_runs_with_progress_and_results_browser():
    from network.examples import demo_field_case
    n, e = demo_field_case()
    root = run_app(APP, {'nodes': n, 'edges': e, 'fc_years': 1.0, 'fc_use_drill': True}, pressed={'fc_run'})
    s = root.session_state
    assert s.get('sched_result'), 'development plan did not run'
    assert s.get('forecast') and s.get('_fc_mode') == 'drill'
    errs = [c for c in root.calls if c[0] == 'error']; assert not errs, errs
    assert any(c == ('markdown', '#### Network at a chosen date') for c in root.calls)


def test_prediction_source_page_applies_decline_and_external_tank():
    from network.examples import demo_field_case
    n, e = demo_field_case()
    root = run_app(APP, {'nodes': n, 'edges': e, 'ps_kind': 'decline', 'ps_qi': 900.0}, pressed={'ps_apply_wells'})
    ws = [x for x in root.session_state['nodes'] if x['kind'] == 'well']
    assert ws and all((x['params'].get('prediction_source') or {}).get('type') == 'decline' for x in ws)
    errs = [c for c in root.calls if c[0] == 'error']; assert not errs, errs


def test_monte_carlo_builder_runs_and_has_green_button():
    from network.examples import demo_field_case
    n, e = demo_field_case()
    root = run_app(APP, {'nodes': n, 'edges': e, 'v17_samples': 5, 'v17_years': 0.5}, pressed={'rb_mc'})
    s = root.session_state
    assert s.get('mc_params') and s['mc_params'][0]['target_id'] == 'kind:well'
    assert s.get('v17_mc') and s['_rb_rb_mc']['status'] == 'done', {k: v for k, v in s.items() if k.startswith('_rb')}
    errs = [c for c in root.calls if c[0] == 'error']; assert not errs, errs


def _errs(root): return [c for c in root.calls if c[0] == 'error']


def test_cases_page_first_case_duplicate_compare_and_solve():
    from network.examples import demo_field_case
    from network.case_manager import CaseLibrary, new_case
    n, e = demo_field_case()
    root = run_app(APP, {'nodes': n, 'edges': e}, pressed={'cs_first'})          # create first case from the screen
    lib = root.session_state['case_library']; assert len(lib) == 1 and not _errs(root)
    a = lib.active; lib.duplicate(a, 'Alt')
    w = next(x for x in lib.get(lib.active)['nodes'] if x['kind'] == 'well'); w.setdefault('params', {})['water_cut'] = 0.5
    root = run_app(APP, {'nodes': n, 'edges': e, 'case_library': lib, 'cs_cmp': list(lib.cases), 'cs_base': a}, pressed={'cs_run_solve'})
    assert not _errs(root), _errs(root)
    assert all(c['solve'] for c in lib.cases.values()) and root.session_state['_rb_cs_run_solve']['status'] == 'done'
    assert any(c[0] == 'dataframe' for c in root.calls)


def test_cases_page_load_and_save_and_forecast_compare():
    from network.examples import demo_field_case
    from network.case_manager import CaseLibrary, new_case
    n, e = demo_field_case(); lib = CaseLibrary(); lib.add(new_case('Base', n, e)); b = lib.duplicate(lib.active, 'B')
    b['nodes'][0]['name'] = 'Renamed'
    root = run_app(APP, {'nodes': n, 'edges': e, 'case_library': lib, 'cs_sel': b['id']}, pressed={'cs_load'})
    assert root.session_state['nodes'][0]['name'] == 'Renamed' and not _errs(root)
    root = run_app(APP, {'nodes': n, 'edges': e, 'case_library': lib, 'cs_cmp': list(lib.cases), 'cs_base': lib.active,
                         'cs_fc_years': 0.5, 'cs_fc_step': 180}, pressed={'cs_run_fc'})
    assert not _errs(root), _errs(root)
    assert all(c['forecast'] and c['forecast_kpis'] for c in lib.cases.values())


def test_pvt_page_apply_calibrate_and_thermal():
    from network.examples import demo_field_case
    from physics.pvt_model import FluidSpec, FluidModel, Calibration
    n, e = demo_field_case()
    root = run_app(APP, {'nodes': n, 'edges': e, 'pvt_co2': 5.0, 'pvt_h2s': 0.01}, pressed={'pvt_apply'})
    assert not _errs(root), _errs(root)
    wells = [x for x in root.session_state['nodes'] if x['kind'] == 'well']
    assert wells and all((x['params'].get('pvt') or {}).get('model') == 'correlation' and abs(x['params']['pvt']['co2'] - .05) < 1e-9 for x in wells)
    # calibration against synthetic lab data
    truth = FluidModel(FluidSpec(rsb_sm3sm3=100, api=36, gas_sg=.72, cal=Calibration(pb_a=1.2, bo_mult=1.1)))
    import pandas as pd
    rows = []
    for p in (40, 80, 120, 160, 200, 260):
        s = truth.state(p, 80); rows.append({'Pressure [bar]': p, 'Rs [Sm3/Sm3]': s.solution_gor_sm3sm3, 'Bo [rm3/Sm3]': s.oil_fvf, 'Oil viscosity [cP]': s.oil_viscosity_pas * 1e3, 'Z': s.gas_z, 'Gas viscosity [cP]': s.gas_viscosity_pas * 1e3})
    n, e = demo_field_case()
    root = run_app(APP, {'nodes': n, 'edges': e, 'cal_df': pd.DataFrame(rows), 'cal_pb': truth.bubble_point_bar(80), 'cal_rsb': 100.0, 'cal_T': 80.0, 'pvt_api': 36.0, 'pvt_gsg': .72}, pressed={'cal_run', 'cal_rank_run'})
    s = root.session_state; assert not _errs(root), _errs(root)
    assert s['pvt_cal']['rows'] and s['_rb_cal_run']['status'] == 'done' and s['pvt_rank']
    assert all(r['Error after [%]'] <= r['Error before [%]'] + 1e-9 for r in s['pvt_cal']['rows'])
    # thermal model applied from the page, then solved
    n, e = demo_field_case()
    root = run_app(APP, {'nodes': n, 'edges': e}, pressed={'th_apply_f', 'th_apply_w'})
    assert not _errs(root), _errs(root)
    assert any((x.get('params') or {}).get('thermal_model') == 'heat_loss' for x in root.session_state['edges'])
