import pytest
from network.examples import demo_field_case
from network.element_results import phase_flows, element_rows, edge_profile, well_profile
from solver.v21 import solve_v21
from ui.graph_contract import normalize_graph, solver_input


def _solved():
    n, e = demo_field_case(); n, e, _ = normalize_graph(n, e); ns, es = solver_input(n, e)
    p, q, info, d = solve_v21(ns, es, enforce_constraints=True); return ns, es, p, q, info, d


def test_phase_conservation_into_sinks():
    ns, es, p, q, info, d = _solved(); ph, node_in = phase_flows(ns, es, q, d, info)
    prod_oil = sum(v['oil_rate_m3d'] for v in d.values()); prod_wat = sum(v['water_rate_m3d'] for v in d.values()); prod_gas = sum(v['gas_rate_sm3d'] for v in d.values())
    sinks = [n for n in ns if n['kind'] in ('separator', 'sink', 'oil_export', 'gas_export', 'water_disposal', 'separator_stage') and not any(e['source'] == n['id'] for e in es)]
    got = {k: sum(node_in[s['id']][k] for s in sinks) for k in ('oil', 'water', 'gas')}
    assert got['oil'] == pytest.approx(prod_oil, rel=1e-6) and got['water'] == pytest.approx(prod_wat, rel=1e-6) and got['gas'] == pytest.approx(prod_gas, rel=1e-6)


def test_edge_profile_ends_at_target_pressure_and_has_velocity():
    ns, es, p, q, info, d = _solved()
    e = next(x for x in es if x['kind'] == 'pipeline' and q[x['id']] > 100)
    rows = edge_profile(e, q[e['id']], p[e['source']], info)
    assert rows[0]['pressure_bar'] == pytest.approx(p[e['source']]) and rows[-1]['pressure_bar'] == pytest.approx(p[e['target']], abs=0.05)
    assert all(r['velocity_ms'] > 0 for r in rows[1:])


def test_well_profile_reaches_bhp():
    ns, es, p, q, info, d = _solved(); w = next(n for n in ns if n['kind'] == 'well' and d[n['id']]['liquid_rate_m3d'] > 10)
    rows = well_profile(w, d[w['id']]['liquid_rate_m3d'], p[w['id']])
    assert rows[-1]['pressure_bar'] == pytest.approx(d[w['id']]['vlp_bhp_bar'], rel=1e-6) and rows[-1]['tvd_m'] > 0


def test_element_rows_shape():
    ns, es, p, q, info, d = _solved(); nr, er = element_rows(ns, es, p, q, d, info, '2026-01-01')
    assert len(nr) == len(ns) and len(er) == len(q) and er[0]['Date'] == '2026-01-01'
    assert any(r.get('Max velocity [m/s]') for r in er)
