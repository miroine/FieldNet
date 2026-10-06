import copy
import pytest
from network.fluid_blend import blend_streams, blend_by_node, blend_by_edge, propagate_blend_to_edges, api_to_sg
from network.element_results import phase_flows


def _synthetic():
    """Two wells (different fluids) -> manifold -> separator, hand-made flows/details (no solver)."""
    nodes = [{'id': 'W1', 'kind': 'well', 'name': 'W1', 'params': {'api': 30.0, 'gas_sg': 0.70}},
             {'id': 'W2', 'kind': 'well', 'name': 'W2', 'params': {'api': 40.0, 'gas_sg': 0.90}},
             {'id': 'M', 'kind': 'manifold', 'name': 'M', 'params': {}}, {'id': 'S', 'kind': 'separator', 'name': 'S', 'params': {}}]
    edges = [{'id': 'E1', 'source': 'W1', 'target': 'M', 'kind': 'pipeline', 'length_m': 1000, 'diameter_m': .15, 'params': {}},
             {'id': 'E2', 'source': 'W2', 'target': 'M', 'kind': 'pipeline', 'length_m': 1000, 'diameter_m': .15, 'params': {}},
             {'id': 'T', 'source': 'M', 'target': 'S', 'kind': 'pipeline', 'length_m': 5000, 'diameter_m': .3, 'params': {'api': 1.0}}]
    d = {'W1': {'oil_rate_m3d': 1000.0, 'water_rate_m3d': 250.0, 'gas_rate_sm3d': 100000.0, 'liquid_rate_m3d': 1250.0},
         'W2': {'oil_rate_m3d': 500.0, 'water_rate_m3d': 50.0, 'gas_rate_sm3d': 400000.0, 'liquid_rate_m3d': 550.0}}
    flows = {'E1': 1250.0, 'E2': 550.0, 'T': 1800.0}
    p = {'W1': 40.0, 'W2': 40.0, 'M': 30.0, 'S': 20.0}
    return nodes, edges, (p, flows, {}, d), d, flows


def test_blend_streams_gor_wc_and_conservation():
    b = blend_streams([{'oil': 1000, 'water': 250, 'gas': 1e5}, {'oil': 500, 'water': 50, 'gas': 4e5}])
    assert b['oil'] == 1500 and b['water'] == 300 and b['gas'] == 5e5
    assert b['gor'] == pytest.approx(5e5 / 1500) and b['wc'] == pytest.approx(300 / 1800)


def test_blend_streams_api_volume_weighted_sg_and_gas_sg():
    b = blend_streams([{'oil': 1000, 'water': 0, 'gas': 100, 'api': 30, 'gas_sg': 0.7}, {'oil': 500, 'water': 0, 'gas': 300, 'api': 40, 'gas_sg': 0.9}])
    sg = (1000 * api_to_sg(30) + 500 * api_to_sg(40)) / 1500
    assert b['oil_sg'] == pytest.approx(sg) and b['api'] == pytest.approx(141.5 / sg - 131.5)
    assert 30 < b['api'] < 40 and b['api'] < 35          # volume weighting favours the heavier, larger stream
    assert b['gas_sg'] == pytest.approx((100 * .7 + 300 * .9) / 400)
    # mass conservation of stock-tank oil
    m = 1000 * api_to_sg(30) * 999.0 + 500 * api_to_sg(40) * 999.0
    assert b['oil_kg_d'] == pytest.approx(m)


def test_blend_streams_empty_and_undefined_properties():
    b = blend_streams([{'oil': 0, 'water': 100, 'gas': 0}])
    assert b['wc'] == 1.0 and b['gor'] is None and b['api'] is None and b['gas_sg'] is None
    assert blend_streams([])['liquid'] == 0.0


def test_node_blend_synthetic_conservation_and_gor():
    nodes, edges, res, d, flows = _synthetic()
    rows = {r['node_id']: r for r in blend_by_node(nodes, edges, res)}
    assert set(rows) == {'M', 'S'}
    for nid in ('M', 'S'):
        r = rows[nid]
        assert r['oil_sm3d'] == pytest.approx(1500) and r['water_sm3d'] == pytest.approx(300) and r['gas_sm3d'] == pytest.approx(5e5)
        assert r['gor_sm3sm3'] == pytest.approx(r['gas_sm3d'] / r['oil_sm3d'])
        assert r['wc'] == pytest.approx(r['water_sm3d'] / (r['oil_sm3d'] + r['water_sm3d']))
        assert r['n_sources'] == 2
    assert rows['M']['gas_sg'] == pytest.approx((1e5 * .7 + 4e5 * .9) / 5e5)


def test_node_blend_matches_phase_flows_totals():
    nodes, edges, res, d, flows = _synthetic()
    _, node_in = phase_flows(nodes, edges, flows, d, {})
    for r in blend_by_node(nodes, edges, res):
        c = node_in[r['node_id']]
        assert r['oil_sm3d'] == pytest.approx(c['oil']) and r['water_sm3d'] == pytest.approx(c['water']) and r['gas_sm3d'] == pytest.approx(c['gas'])


def test_split_downstream_keeps_composition_and_conserves():
    nodes, edges, res, d, flows = _synthetic()
    # split the manifold outflow into two parallel lines to two sinks
    nodes = nodes + [{'id': 'S2', 'kind': 'sink', 'name': 'S2', 'params': {}}]
    edges = [e for e in edges if e['id'] != 'T'] + [
        {'id': 'TA', 'source': 'M', 'target': 'S', 'kind': 'pipeline', 'length_m': 5000, 'diameter_m': .3, 'params': {}},
        {'id': 'TB', 'source': 'M', 'target': 'S2', 'kind': 'pipeline', 'length_m': 5000, 'diameter_m': .3, 'params': {}}]
    flows = {'E1': 1250.0, 'E2': 550.0, 'TA': 1200.0, 'TB': 600.0}
    res = (res[0], flows, {}, d)
    be = blend_by_edge(nodes, edges, res)
    assert be['TA']['gor'] == pytest.approx(be['TB']['gor']) == pytest.approx(5e5 / 1500)
    assert be['TA']['oil'] + be['TB']['oil'] == pytest.approx(1500) and be['TA']['gas'] + be['TB']['gas'] == pytest.approx(5e5)
    assert be['TA']['oil'] == pytest.approx(1500 * 2 / 3)
    rows = {r['node_id']: r for r in blend_by_node(nodes, edges, res)}
    assert rows['S']['oil_sm3d'] + rows['S2']['oil_sm3d'] == pytest.approx(1500)


def test_propagate_to_edges_copy_semantics():
    nodes, edges, res, d, flows = _synthetic()
    before = copy.deepcopy(edges)
    new = propagate_blend_to_edges(nodes, edges, res)
    assert edges == before                       # inputs untouched
    t = next(e for e in new if e['id'] == 'T')['params']
    assert t['gor_sm3sm3'] == pytest.approx(5e5 / 1500) and t['water_cut'] == pytest.approx(300 / 1800) and t['fluid_blend'] is True
    e1 = next(e for e in new if e['id'] == 'E1')['params']       # single-source edge keeps well fluid
    assert e1['gor_sm3sm3'] == pytest.approx(100.0) and e1['api'] == pytest.approx(30.0) and e1['water_cut'] == pytest.approx(0.2)
    skip = propagate_blend_to_edges(nodes, edges, res, skip_single_source=True)
    assert 'fluid_blend' not in next(e for e in skip if e['id'] == 'E1')['params']


def test_pvt_table_insitu_columns():
    from physics.pvt_table import PVTTable
    nodes, edges, res, d, flows = _synthetic()
    tab = PVTTable.screening_default('oil')
    rows = blend_by_node(nodes, edges, res, source_fluids={'W1': {'pvt_table': tab}, 'W2': {'pvt_table': tab}})
    r = next(x for x in rows if x['node_id'] == 'M')
    assert r['insitu_oil_rm3d'] > 0 and 0.0 <= r['insitu_gas_fraction'] <= 1.0
    assert r['free_gas_sm3d'] <= r['gas_sm3d'] + 1e-6
    # standard-condition blend unaffected by the table
    assert r['gor_sm3sm3'] == pytest.approx(5e5 / 1500)


def test_demo_field_case_blend_conserves_total():
    from network.examples import demo_field_case
    from solver.v21 import solve_v21
    from ui.graph_contract import normalize_graph, solver_input
    n, e = demo_field_case(); n, e, _ = normalize_graph(n, e); ns, es = solver_input(n, e)
    res = solve_v21(ns, es, enforce_constraints=True); d = res[3]
    rows = {r['node_id']: r for r in blend_by_node(ns, es, res)}
    tot = {k: sum(v[x] for v in d.values()) for k, x in (('oil', 'oil_rate_m3d'), ('water', 'water_rate_m3d'), ('gas', 'gas_rate_sm3d'))}
    s = rows['SEP']
    assert s['oil_sm3d'] == pytest.approx(tot['oil'], rel=1e-6) and s['water_sm3d'] == pytest.approx(tot['water'], rel=1e-6)
    assert s['gas_sm3d'] == pytest.approx(tot['gas'], rel=1e-6) and s['gor_sm3sm3'] == pytest.approx(tot['gas'] / tot['oil'], rel=1e-6)
    new = propagate_blend_to_edges(ns, es, res)
    assert len(new) == len(es)
