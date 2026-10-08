"""v32.6 round 4: min rate per phase, phase colours, symbol scale, batch editor, file import, tables in the Network tab."""
import copy, io, json, re
import pytest
from network.templates import build
from tests.support.app_harness import run_app


def _case(key='pure_depletion_oil'):
    b = build(key); return (b[0], b[1]) if isinstance(b, tuple) else (b['nodes'], b['edges'])


# ---- minimum rate per phase ---------------------------------------------------------------------------------------
def test_phase_minimum_rates_become_liquid_equivalent_minimum():
    from physics.well_model import well_settings
    base = {'water_cut': 0.5, 'gor_sm3sm3': 200.0}
    assert well_settings(base)['min_rate'] == pytest.approx(5.0)
    assert well_settings({**base, 'min_oil_rate_m3d': 50})['min_rate'] == pytest.approx(100.0)          # oil = liquid * (1 - wc)
    assert well_settings({**base, 'min_water_rate_m3d': 30})['min_rate'] == pytest.approx(60.0)
    assert well_settings({**base, 'min_gas_rate_sm3d': 10000})['min_rate'] == pytest.approx(10000 / (0.5 * 200))
    assert well_settings({**base, 'min_oil_rate_m3d': 50, 'min_water_rate_m3d': 80})['min_rate'] == pytest.approx(160.0)   # the highest governs
    assert well_settings({'water_cut': 0.0, 'min_water_rate_m3d': 30})['min_rate'] == pytest.approx(5.0)    # no water -> a water minimum cannot apply


def test_well_below_phase_minimum_is_shut_in():
    from physics.well_model import well_settings, solve_well_rate
    prm = {'reservoir_pressure_bar': 250.0, 'pi_m3d_bar': 10.0, 'water_cut': 0.2, 'gor_sm3sm3': 100.0, 'depth_m': 2000.0}
    q, st = solve_well_rate(40.0, well_settings(prm)); assert st in ('flowing', 'rate_limited') and q > 0
    prm['min_oil_rate_m3d'] = q * 0.8 * 2.0
    assert solve_well_rate(40.0, well_settings(prm)) == (0.0, 'below_min_rate')


# ---- phase colours and scale --------------------------------------------------------------------------------------------
def test_phase_colours_are_one_set_across_canvas_and_charts():
    from ui.shapes import PHASE_COLOR
    from ui import charts
    html = open('ui/fieldnet_canvas/build/index.html').read()
    assert json.loads(re.search(r'/\*phase-sync\*/const PHASE_COLOR=(\{.*?\});', html).group(1)) == PHASE_COLOR
    assert (charts.OIL, charts.GAS, charts.WATER) == (PHASE_COLOR['oil'], PHASE_COLOR['gas'], PHASE_COLOR['water'])
    assert PHASE_COLOR['gas'].lower() in ('#d93a3a',) and charts.series_color('Gas [MSm3/d]') == PHASE_COLOR['gas']
    assert charts.series_color('Water [m3/d]') == PHASE_COLOR['water'] and charts.series_color('Oil [m3/d]') == PHASE_COLOR['oil']
    assert charts.series_color('Cum Gas [GSm3]') == PHASE_COLOR['gas'] and charts.series_color('Well A', 0) not in (charts.GAS,)
    assert charts.GAS not in charts.CATEGORICAL                      # red is reserved for gas


def test_node_phase_rules():
    from ui.shapes import node_phase
    n, _ = _case('gas_condensate_tieback'); tank = next(x for x in n if x['kind'] == 'reservoir'); w = next(x for x in n if x['kind'] == 'well')
    assert node_phase(tank, n) == 'gas' and node_phase(w, n) == 'gas'
    assert node_phase({'kind': 'water_injector'}) == 'water' and node_phase({'kind': 'gas_injector'}) == 'gas'
    assert node_phase({'kind': 'reservoir', 'params': {'fluid_phase': 'oil'}}) == 'oil'
    assert node_phase({'kind': 'well', 'params': {'phase': 'gas_condensate'}}) == 'gas' and node_phase({'kind': 'manifold'}) is None


def test_svg_export_fills_phase_colours_and_honours_scale():
    import xml.dom.minidom as md
    from ui.svg_export import network_svg, _size
    n, e = _case('gas_condensate_tieback'); svg = network_svg(n, e); md.parseString(svg)
    assert '#d93a3a' in svg and 'linearGradient' in svg
    w = next(x for x in n if x['kind'] == 'well'); a = _size(w); w.setdefault('params', {})['scale'] = 2.0
    assert _size(w) == (2 * a[0], 2 * a[1]); md.parseString(network_svg(n, e))


def test_canvas_has_scale_support_and_scale_survives_the_graph_contract():
    html = open('ui/fieldnet_canvas/build/index.html').read()
    assert 'function nscale' in html and 'id="szm"' in html and 'id="szp"' in html and 'scale(' in html
    from ui.graph_contract import normalize_graph
    n, e = _case(); n[1].setdefault('params', {})['scale'] = 1.7
    nn, ee, _ = normalize_graph(n, e); assert next(x for x in nn if x['id'] == n[1]['id'])['params']['scale'] == 1.7


# ---- batch editor and file import -----------------------------------------------------------------------------------------
def test_element_table_and_apply_only_changes_edited_cells():
    from network import batch_io as B
    n, e = _case(); df, cols = B.element_table(n, e, 'Wells', extra_cols=['min_oil_rate_m3d'])
    assert 'min_oil_rate_m3d' in df.columns and len(df) == sum(1 for x in n if x['kind'] == 'well')
    new = df.copy(); new.loc[0, 'min_oil_rate_m3d'] = 25.0; new.loc[1, 'Name'] = 'Renamed'; new.loc[2, 'water_cut'] = 0.4
    before = copy.deepcopy(n)
    assert B.apply_table(n, e, 'Wells', new, df) == 3
    w0 = next(x for x in n if x['id'] == df.loc[0, 'ID']); assert w0['params']['min_oil_rate_m3d'] == 25.0
    assert next(x for x in n if x['id'] == df.loc[1, 'ID'])['name'] == 'Renamed' and next(x for x in n if x['id'] == df.loc[2, 'ID'])['params']['water_cut'] == 0.4
    cleared = new.copy(); cleared.loc[0, 'min_oil_rate_m3d'] = None
    B.apply_table(n, e, 'Wells', cleared, new); assert 'min_oil_rate_m3d' not in w0['params']
    assert [x['params'].get('depth_m') for x in n if x['kind'] == 'well'] == [x['params'].get('depth_m') for x in before if x['kind'] == 'well']   # untouched cells stay


def test_flowline_table_edits_core_fields():
    from network import batch_io as B
    n, e = _case(); df, _ = B.element_table(n, e, 'Flowlines'); new = df.copy(); new.loc[0, 'diameter_m'] = 0.25
    assert B.apply_table(n, e, 'Flowlines', new, df) == 1 and e[[x['id'] for x in e].index(df.loc[0, 'ID'])]['diameter_m'] == 0.25


@pytest.mark.parametrize('fmt', ['xlsx', 'csv', 'json', 'yaml'])
def test_import_formats_merge_by_id(fmt):
    import pandas as pd
    from network import batch_io as B
    n, e = _case(); wid = [x['id'] for x in n if x['kind'] == 'well'][:2]
    recs = [{'ID': wid[0], 'min_gas_rate_sm3d': 12345, 'available': False, 'Name': 'ABC'}, {'ID': wid[1], 'max_oil_rate_m3d': 321.0}, {'ID': 'nope', 'skin': 1}]
    if fmt == 'xlsx':
        buf = io.BytesIO(); pd.DataFrame(recs).to_excel(buf, index=False); data = buf.getvalue()
    elif fmt == 'csv': data = pd.DataFrame(recs).to_csv(index=False).encode()
    elif fmt == 'json': data = json.dumps(recs).encode()
    else:
        import yaml; data = yaml.safe_dump(recs).encode()
    doc = B.read_file('f.' + fmt, data); assert doc['project'] is None
    rep = B.merge_sheets(n, e, doc['sheets']); assert rep['matched'] == 2 and rep['unknown'] == ['nope']
    w0 = next(x for x in n if x['id'] == wid[0]); w1 = next(x for x in n if x['id'] == wid[1])
    assert w0['params']['min_gas_rate_sm3d'] == 12345 and w0['params']['available'] is False and w0['name'] == 'ABC' and w1['params']['max_oil_rate_m3d'] == 321.0


def test_import_project_and_id_keyed_mapping_and_errors():
    from network import batch_io as B
    n, e = _case()
    assert B.read_file('p.json', json.dumps({'nodes': n, 'edges': e}).encode())['project']['nodes'][0]['id'] == n[0]['id']
    import yaml
    assert B.read_file('p.yaml', B.to_yaml(n, e).encode())['project'] is not None
    d = B.read_file('m.json', json.dumps({n[1]['id']: {'skin': 3}}).encode()); rep = B.merge_sheets(n, e, d['sheets']); assert rep['matched'] == 1 and n[1]['params']['skin'] == 3
    for bad in (('x.txt2', b'abc'), ('x.json', b'{not json'), ('x.csv', b'')):
        with pytest.raises(ValueError): B.read_file(*bad)


def test_workbook_round_trip_is_lossless_for_scalar_inputs():
    from network import batch_io as B
    n, e = _case('onshore_gas_gathering'); n0 = copy.deepcopy(n); e0 = copy.deepcopy(e)
    doc = B.read_file('i.xlsx', B.to_workbook(n, e)); B.merge_sheets(n, e, doc['sheets'])
    for a, b in zip(n0, n):
        for k, v in (a.get('params') or {}).items():
            if isinstance(v, (int, float, str)) and not isinstance(v, bool): assert b['params'][k] == pytest.approx(v) if not isinstance(v, str) else b['params'][k] == v, (a['id'], k)


# ---- UI ---------------------------------------------------------------------------------------------------------------------------
def test_network_tab_holds_all_data_tables_and_old_tabs_point_there():
    n, e = _case(); root = run_app('app.py', {'nodes': n, 'edges': e})
    assert ('subheader', 'Data tables — edit all inputs in one place') in root.calls
    assert ('form_submit_button', 'Apply changes to the model') in root.calls and ('form_submit_button', 'Apply constraints') in root.calls   # tables are forms (round 6)
    assert any(c[0] == 'info' and 'moved to the **Network** tab' in str(c[1]) for c in root.calls)
    assert any(c[0] == 'button' and 'Fill typical uptimes' in c[1] for c in root.calls)           # the uptime table now renders from the Network tab
