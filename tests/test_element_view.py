import pytest
from network.examples import demo_field_case
from network.forecast import run_forecast
from solver.v21 import solve_v21
from ui.graph_contract import normalize_graph, solver_input
from ui.element_view import element_choices, edge_profile_frame, well_profile_frame, element_series, series_variables


def _res():
    n, e = demo_field_case(); n, e, _ = normalize_graph(n, e); ns, es = solver_input(n, e); return ns, es, solve_v21(ns, es, enforce_constraints=True)


def test_profiles_frames():
    ns, es, r = _res()
    e = next(x for x in es if x['kind'] == 'pipeline' and r[1][x['id']] > 100); f = edge_profile_frame(e, r)
    assert len(f) >= 2 and {'x_m', 'pressure_bar', 'velocity_ms', 'z_m'} <= set(f.columns) and f['pressure_bar'].iloc[0] > f['pressure_bar'].iloc[-1]
    w = next(x for x in ns if x['kind'] == 'well' and r[3][x['id']]['liquid_rate_m3d'] > 10); wf = well_profile_frame(w, r)
    assert wf['tvd_m'].iloc[-1] > 1000 and wf['pressure_bar'].iloc[-1] > wf['pressure_bar'].iloc[0]
    assert edge_profile_frame(e, None).empty and len(element_choices(ns, es)) > 5


def test_forecast_element_series():
    n, e = demo_field_case(); fc = run_forecast(n, e, '2026-01-01', 0.5, 60)
    eid = next(x['id'] for x in e if x['kind'] == 'pipeline'); vs = series_variables(fc, eid)
    assert 'Flow [m3/d]' in vs; ts = element_series(fc, eid, 'Flow [m3/d]'); assert len(ts) == len(fc['field'])
    nid = next(x['id'] for x in n if x['kind'] == 'separator' or x['id'] == 'SEP'); assert not element_series(fc, nid, 'Pressure [bar]').empty


def test_render_smoke_with_fake_streamlit():
    from tests.support.fake_streamlit import FakeSt, install_fake_plotly
    install_fake_plotly()
    import importlib, ui.charts; importlib.reload(ui.charts)
    from ui.element_view import render_element_results
    ns, es, r = _res(); st = FakeSt(); n, e = demo_field_case(); fc = run_forecast(n, e, '2026-01-01', 0.25, 90)
    # fake tabs: st.tabs returns objects behaving like st
    st.tabs = lambda names: [st] * len(names)
    pipe = next(x for x in es if x['kind'] == 'pipeline' and r[1][x['id']] > 100); st.session_state['elem_sel'] = pipe['id']
    render_element_results(st, ns, es, r, fc)
    well = next(x for x in ns if x['kind'] == 'well'); st.session_state['elem_sel'] = well['id']; render_element_results(st, ns, es, r, fc)
    assert any(c[0] == 'plotly_chart' for c in st.calls)
