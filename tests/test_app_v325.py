"""v32.5 pages through the fake-Streamlit harness: yearly bars, groups, MB & voidage, nodal tools, fluid library, data hub / export / post-processing."""
import os, datetime as dt, pytest
from tests.support.app_harness import run_app
APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app.py')


def _state(with_group=True, **extra):
    """Session state after a real forecast run through the app (so the forecast hash is the one the app computes)."""
    from network.examples import demo_field_case
    from network import groups as grp
    n, e = demo_field_case(); t = next(x for x in n if x['kind'] == 'reservoir')
    if with_group: grp.group_from_tank(n, t['id'], 'North')
    root = run_app(APP, {'nodes': n, 'edges': e, 'fc_years': 2.0, 'fc_step': 180}, pressed={'fc_run', 'solve_btn_top'})
    st = dict(root.session_state); st.update(extra); st.pop('hub_cache', None)
    return st, st['nodes'], st['edges']


def _no_errors(root):
    errs = [c for c in root.calls if c[0] in ('error', 'exception')]; assert not errs, errs


def test_all_new_pages_render_with_forecast():
    s, n, e = _state(); root = run_app(APP, s, pressed={'solve_btn_top'}); _no_errors(root)
    assert root.session_state.get('hub_cache') is not None
    hub = root.session_state['hub_cache']['hub']; assert 'annual_field' in hub.datasets and 'groups_profile' in hub.datasets


def test_pages_render_without_forecast():
    from network.examples import demo_field_case
    n, e = demo_field_case(); root = run_app(APP, {'nodes': n, 'edges': e}); _no_errors(root)


def test_group_button_creates_group():
    s, n, e = _state(with_group=False)
    root = run_app(APP, s, pressed={'grp_make'}); nodes = root.session_state['nodes']
    assert any((x.get('params') or {}).get('group') for x in nodes)


def test_postprocess_button_adds_table():
    s, n, e = _state(); s['pp_src'] = "d = ds['annual_field'].copy()\nd['x'] = d['Oil [Sm3]']/1e6\nout['my_tbl'] = d"
    root = run_app(APP, s, pressed={'pp_run'}); _no_errors(root)
    assert 'my_tbl' in (root.session_state.get('pp_tables') or {})


def test_nodal_monte_carlo_and_blowout_buttons():
    s, n, e = _state(); w = next(x for x in n if x['kind'] == 'well')
    root = run_app(APP, dict(s, nodal_well=w['id']), pressed={'solve_btn_top', f"nu_run_{w['id']}", f"bo_run_{w['id']}", f"wm_syn_{w['id']}"}); _no_errors(root)
    ss = root.session_state; assert w['id'] in (ss.get('nodal_mc') or {}) and w['id'] in (ss.get('blowout') or {})


def test_matching_runs_on_synthetic_tests():
    s, n, e = _state(); w = next(x for x in n if x['kind'] == 'well')
    from physics import well_match as wm
    from network.reservoir_mb import apply_tank_links
    prm = dict(next(x for x in apply_tank_links(n) if x['id'] == w['id'])['params'])
    s['nodal_tests'] = {w['id']: wm.synthetic_tests(dict(prm, vlp_dp_multiplier=1.2), whps=(10., 20., 30.)).to_dict('records')}
    root = run_app(APP, dict(s, nodal_well=w['id']), pressed={'solve_btn_top', f"wm_run_{w['id']}"}); _no_errors(root)
    assert (root.session_state.get('nodal_match') or {}).get(w['id'])


def test_fluid_library_assign_via_button():
    from network import fluids as fl
    s, n, e = _state(); s['fluids'] = {'Heavy': fl.new_fluid('Heavy', 24.0, 0.78, 60.0)}
    t = next(x for x in n if x['kind'] == 'reservoir')
    root = run_app(APP, dict(s, fl_asg_fluid='Heavy', fl_asg_tanks=[t['id']]), pressed={'fl_asg_t'}); _no_errors(root)
    nodes = root.session_state['nodes']; assert next(x for x in nodes if x['id'] == t['id'])['params'].get('fluid_name') == 'Heavy'


def test_link_add_button():
    s, n, e = _state(); import copy
    t1 = next(x for x in n if x['kind'] == 'reservoir'); t2 = copy.deepcopy(t1); t2['id'] = 'T2'; t2['name'] = 'SEG-2'; t2['params'] = dict(t1['params'], communication=[]); n.append(t2); t1['params'].pop('communication', None)
    root = run_app(APP, dict(s, lnk_a='T1', lnk_b='T2', lnk_t=77.0), pressed={'lnk_add'}); _no_errors(root)
    com = (next(x for x in root.session_state['nodes'] if x['id'] == 'T1')['params'].get('communication') or [])
    assert com and com[0]['to'] == 'T2' and com[0]['transmissibility_m3d_bar'] == 77.0


def test_case_roundtrip_keeps_extras_and_forecast():
    from network.case_manager import new_case, CaseLibrary
    from ui.cases_view import current_extras
    s, n, e = _state(); ss = dict(s, pp_src='out["a"]=1', fluids={'X': {'name': 'X'}}); c = new_case('c', n, e, forecast=s['forecast'], extras=current_extras(ss))
    assert c['extras']['post_scripts'] and c['forecast']['wells'] and c['forecast']['tanks']
    lib = CaseLibrary(); lib.add(c); d = lib.duplicate(c['id']); assert d['extras'] == c['extras']
