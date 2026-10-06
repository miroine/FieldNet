import copy, pytest
from network.examples import demo_field_case
from network.equipment import expand_inline_equipment, collapse_results, convert_edge_equipment_to_nodes, has_inline
from solver.steady_state import solve_network
from solver.v21 import solve_v21
from ui.graph_contract import normalize_graph


def _case_with_choke(cv=5.0):
    n, e = demo_field_case(); n, e, _ = normalize_graph(n, e)
    pipe = next(x for x in e if x['kind'] == 'pipeline'); pipe['kind'] = 'choke'; pipe['length_m'] = 0.0; pipe['params']['cv'] = cv
    return n, e, pipe['id']


def test_expansion_is_idempotent_and_non_mutating():
    n, e, eid = _case_with_choke(); n2, e2, mp = convert_edge_equipment_to_nodes(n, e, {eid}), None, None
    nn, ee = n2
    snap = copy.deepcopy((nn, ee)); xn, xe, m = expand_inline_equipment(nn, ee)
    assert (nn, ee) == snap and m and not has_inline(xn)
    yn, ye, m2 = expand_inline_equipment(xn, xe); assert not m2 and yn is xn


def test_inline_node_matches_legacy_edge_equipment():
    n, e, eid = _case_with_choke()
    p0, q0, i0, d0 = solve_network(n, e)
    nn, ee = convert_edge_equipment_to_nodes(n, e, {eid})
    assert any(x['kind'] == 'choke' for x in nn) and all(x['id'] != eid for x in ee)
    p1, q1, i1, d1 = solve_network(nn, ee)
    t0 = sum(v['liquid_rate_m3d'] for v in d0.values()); t1 = sum(v['liquid_rate_m3d'] for v in d1.values())
    assert t1 == pytest.approx(t0, rel=2e-3)
    ch = next(x for x in nn if x['kind'] == 'choke')
    row = i1['inline_equipment'][ch['id']]
    assert row['dp_bar'] > 0 and row['rate_m3d'] > 0 and ch['id'] in p1 and ch['id'] in q1
    assert not any('::' in k for k in list(p1) + list(q1))


def test_v21_with_inline_pump_and_choke_honours_constraints():
    n, e, eid = _case_with_choke(cv=40.0)
    nn, ee = convert_edge_equipment_to_nodes(n, e, {eid})
    p, q, info, d = solve_v21(nn, ee, enforce_constraints=True)
    assert info['quality_gate'] == 'PASS' and sum(v['liquid_rate_m3d'] for v in d.values()) > 100
    assert any(r['kind'] == 'choke' for r in info['inline_equipment'].values())


def test_equipment_cap_is_a_constraint_on_the_node():
    n, e, eid = _case_with_choke(cv=200.0)
    nn, ee = convert_edge_equipment_to_nodes(n, e, {eid}); ch = next(x for x in nn if x['kind'] == 'choke')
    ch['params']['max_rate_m3d'] = 300.0
    p, q, info, d = solve_v21(nn, ee, enforce_constraints=True)
    rows = [r for r in info['constraints'] if r.get('Constraint') == 'Maximum rate' and r.get('ComponentId') == ch['id']]
    assert rows, 'equipment max rate constraint must be reported against the node id'
    assert q[ch['id']] <= 300.0 * 1.01
