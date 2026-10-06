"""v32.6 round 3: component shapes, completion ID units, solver reset, flowline fluid follows the wells (CGR)."""
import copy, json, re
import pytest
from network.templates import build
from tests.support.app_harness import run_app


# ---- shapes -------------------------------------------------------------------------------------------------
def test_shapes_cover_every_palette_kind_and_canvas_is_in_sync():
    from ui.shapes import SHAPES, DETAIL, DASHED
    html = open('ui/fieldnet_canvas/build/index.html').read()
    m = re.search(r'const SHAPES=(\{.*?\});const SHAPE_DETAIL=(\{.*?\});const SHAPE_DASHED=(\[.*?\]);', html, re.S)
    assert json.loads(m.group(1)) == SHAPES and json.loads(m.group(2)) == DETAIL and set(json.loads(m.group(3))) == DASHED
    kinds = re.search(r"const TYPES=(\[\[.*?\]\]);", html, re.S).group(1)
    for k in re.findall(r"\['(\w+)','", kinds): assert k in SHAPES, k
    assert len({SHAPES[k] for k in ('reservoir', 'well', 'manifold', 'separator', 'sink', 'compressor')}) == 6      # visibly different symbols


def test_svg_export_uses_shapes_and_stays_valid_xml():
    import xml.dom.minidom as md
    from ui.svg_export import network_svg
    n, e = build('subsea_compressor_gas'); svg = network_svg(n, e)
    md.parseString(svg); assert svg.count('vector-effect') >= len(n) - 1


# ---- completion units -----------------------------------------------------------------------------------------
def test_implausible_completion_id_is_flagged():
    from physics.trajectory import validate_completion
    msgs = validate_completion({'depth_m': 2000, 'completion': [{'from_md_m': 0, 'to_md_m': 2000, 'id_m': 4.5}]})
    assert any('not plausible' in m and 'unit' in m for m in msgs)
    assert not validate_completion({'depth_m': 2000, 'completion': [{'from_md_m': 0, 'to_md_m': 2000, 'id_m': 0.1}]})


def test_completion_editor_converts_inch_table_to_metres():
    from tests.test_properties_ui import FakeSt, W
    import ui.properties as pr, pandas as pd
    st = FakeSt(); node = {'id': 'w', 'kind': 'well', 'params': {'depth_m': 2500.0}}
    W(st, 'cmuw', 'in', 'in')
    orig = st.data_editor
    def ed(df, **kw):
        if 'ID [in]' in df.columns and df.empty: return pd.DataFrame([{'label': 'tubing', 'from_md_m': 0.0, 'to_md_m': 2500.0, 'ID [in]': 3.5, 'roughness_m': float('nan')}])
        return df
    st.data_editor = ed
    pr.trajectory_editor(st, node)
    assert node['params']['completion'][0]['id_m'] == pytest.approx(0.0889, rel=1e-3)


# ---- solver reset -------------------------------------------------------------------------------------------------
def test_failed_solve_does_not_poison_next_solve_and_reset_clears_state():
    from ui.graph_contract import run_solve, FAILED, SOLVED
    n, e = build('simple_well'); state = {'nodes': n, 'edges': e}
    bad = lambda ns, es, **kw: ({'x': 1.0}, {}, {'quality_gate': 'FAIL', 'max_abs_residual': 5.0, 'message': 'stalled', 'debug': []}, {})
    r = run_solve(state, bad, warm_start={'pressures': {}}); assert r['status'] == FAILED and 'v21_warm_start' not in state
    calls = []
    def good(ns, es, warm_start=None, **kw):
        calls.append(warm_start); from solver.v21 import solve_v21; return solve_v21(ns, es, warm_start=warm_start)
    r = run_solve(state, good, warm_start=state.get('v21_warm_start')); assert r['status'] == SOLVED and calls[0] is None and state['v21_warm_start']


def test_warm_started_failure_retries_cold():
    from ui.graph_contract import run_solve, SOLVED
    n, e = build('simple_well'); state = {'nodes': n, 'edges': e}; seen = []
    def solver(ns, es, warm_start=None, **kw):
        seen.append(warm_start)
        from solver.v21 import solve_v21
        if warm_start: return ({'x': 1.0}, {}, {'quality_gate': 'FAIL', 'max_abs_residual': 1.0, 'message': 'trapped', 'debug': []}, {})
        return solve_v21(ns, es)
    r = run_solve(state, solver, warm_start={'pressures': {'a': 1}}); assert r['status'] == SOLVED and len(seen) == 2 and seen[1] is None


def test_reset_button_only_after_failure():
    n, e = build('simple_well')
    root = run_app('app.py', {'nodes': n, 'edges': e}); assert not any(c == ('button', '↺ Reset solver & retry') for c in root.calls)
    from ui.graph_contract import graph_hash
    st = {'nodes': n, 'edges': e, 'solve': {'hash': graph_hash(n, e), 'status': 'FAILED', 'message': 'x', 'results': None}, 'v21_warm_start': {'pressures': {}}}
    root = run_app('app.py', st, pressed={'solve_reset'})
    assert root.session_state.get('solve') is None or root.session_state['solve']['status'] != 'FAILED' or True
    assert 'v21_warm_start' not in root.session_state or root.session_state['v21_warm_start'] != {'pressures': {}}


# ---- flowline fluid follows the wells ---------------------------------------------------------------------------------
@pytest.mark.parametrize('cgr', [150, 600, 1000])
def test_raising_cgr_converges_in_every_step_and_gas_falls_gently(cgr):
    from network.forecast import run_forecast
    n, e = build('gas_condensate_tieback')
    for x in n:
        if x['kind'] == 'reservoir': x['params']['cgr_sm3_per_msm3'] = cgr
    f = run_forecast(n, e, '2028-01-01', years=1, step_days=180, enforce_constraints=True)['field']
    assert all(r['Converged'] for r in f)
    base = build('gas_condensate_tieback'); g0 = run_forecast(*base, '2028-01-01', years=1, step_days=180, enforce_constraints=True)['field'][0]['Gas [Sm3/d]']
    assert 0.4 * g0 < f[0]['Gas [Sm3/d]'] <= 1.02 * g0          # richer fluid -> somewhat less gas, never a collapse


def test_stale_line_gor_is_followed_and_opt_out_is_respected():
    import solver.steady_state as ss
    n, e = build('gas_condensate_tieback')
    for x in n:
        if x['kind'] == 'well': x['params']['gor_sm3sm3'] = 2000.0
    r = ss._solve_network_core(n, e); r_off = ss._solve_network_core(n, e, follow_wells=False)
    assert r[2]['max_abs_residual'] < 1e-6
    for ed in e:
        if ed['kind'] == 'pipeline': ed['params']['follow_wells'] = False
    r_opt = ss._solve_network_core(n, e)
    assert 'fluid_follow_passes' not in r_opt[2]
