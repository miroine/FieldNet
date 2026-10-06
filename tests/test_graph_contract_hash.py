"""Moving, selecting or unit-round-tripping an object must not invalidate a solve; a real edit must."""
import copy
from network.examples import demo_field_case
from network.reservoir_mb import Tank
from physics.well_model import well_settings
from solver.v21 import solve_v21
from ui.graph_contract import (normalize_graph, graph_hash, run_solve, solve_status, accept_canvas_payload,
                               GRAPH_SCHEMA, set_edge_kind, HASH_DEFAULTS)


def _state():
    n, e = demo_field_case(); n, e, _ = normalize_graph(n, e); st = {'nodes': n, 'edges': e}
    run_solve(st, solve_v21); return st


def test_move_keeps_solution():
    st = _state(); assert solve_status(st)[0] == 'SOLVED'
    pl = {'schema': GRAPH_SCHEMA, 'rev': 'm1', 'nodes': copy.deepcopy(st['nodes']), 'edges': copy.deepcopy(st['edges']), 'selected': None}
    pl['nodes'][0]['x'] += 40; pl['nodes'][1]['name'] = 'renamed'
    assert accept_canvas_payload(st, pl) == 'graph'
    assert solve_status(st)[0] == 'SOLVED'


def test_panel_default_injection_and_float_noise_keep_solution():
    st = _state()
    w = next(n for n in st['nodes'] if n['kind'] == 'well')
    for k, v in HASH_DEFAULTS['well'].items(): w['params'].setdefault(k, v)
    w['params']['pi_m3d_bar'] = w['params']['pi_m3d_bar'] * (1 + 1e-14)
    e = st['edges'][0]
    for k, v in HASH_DEFAULTS['edge'].items(): e['params'].setdefault(k, v)
    assert solve_status(st)[0] == 'SOLVED'


def test_real_edit_invalidates():
    st = _state(); w = next(n for n in st['nodes'] if n['kind'] == 'well'); w['params']['pi_m3d_bar'] = w['params'].get('pi_m3d_bar', 10) * 1.1
    assert solve_status(st)[0] == 'UNSOLVED'
    st = _state(); st['edges'][0]['diameter_m'] *= 1.1; assert solve_status(st)[0] == 'UNSOLVED'
    st = _state(); pipe = next(x for x in st['edges'] if x['kind'] == 'pipeline'); set_edge_kind(pipe, 'choke'); assert solve_status(st)[0] == 'UNSOLVED'


def test_edge_kind_roundtrip_length():
    st = _state(); pipe = next(x for x in st['edges'] if x['kind'] == 'pipeline')
    set_edge_kind(pipe, 'choke'); assert pipe['length_m'] == 0.0
    set_edge_kind(pipe, 'pipeline'); assert pipe['length_m'] > 0.0


def test_hash_defaults_match_physics_defaults():
    ws = well_settings(HASH_DEFAULTS['well']); bare = well_settings({})
    for k in ('pr', 'pi', 'qmax', 'depth', 'tubing_id', 'water_cut', 'gor', 'temperature', 'skin'):
        assert ws[k] == bare[k], k
    t0 = Tank({'id': 'T', 'kind': 'reservoir', 'params': {'stoiip_sm3': 1e7}}); t1 = Tank({'id': 'T', 'kind': 'reservoir', 'params': {'stoiip_sm3': 1e7, **HASH_DEFAULTS['reservoir']}})
    assert (t0.pv, t0.pb, t0.boi, t0.p) == (t1.pv, t1.pb, t1.boi, t1.p)
