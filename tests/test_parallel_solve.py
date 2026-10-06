import copy, pytest
from network.examples import demo_field_case, demo_case
from network.parallel_solve import split_components, solve_parallel, connected_components, compute_plan, cpu_count
from solver.v21 import solve_v21
from ui.graph_contract import normalize_graph, solver_input


def _two_systems():
    n, e = demo_field_case(); n, e, _ = normalize_graph(n, e)
    n2, e2 = copy.deepcopy(n), copy.deepcopy(e)
    for x in n2: x['id'] = 'B_' + x['id']; x['name'] = 'B ' + x['name']; (x['params'].get('reservoir_id') and x['params'].__setitem__('reservoir_id', 'B_' + x['params']['reservoir_id']))
    for x in e2: x['id'] = 'B_' + x['id']; x['source'] = 'B_' + x['source']; x['target'] = 'B_' + x['target']
    return solver_input(n + n2, e + e2)


def test_field_demo_has_production_and_injection_systems():
    n, e = demo_field_case(); ns, es = solver_input(*normalize_graph(n, e)[:2]); assert len([c for c in split_components(ns, es) if c[1]]) == 2


def test_components_found():
    ns, es = _two_systems(); assert len(connected_components(ns, es)) >= 2
    comps = split_components(ns, es); assert sum(len(c[1]) for c in comps) == len(es)


def test_parallel_equals_serial():
    ns, es = _two_systems()
    s = solve_v21(ns, es, enforce_constraints=True); par = solve_parallel(ns, es, solve_v21, workers=2, enforce_constraints=True)
    ts = sum(v['liquid_rate_m3d'] for v in s[3].values()); tp = sum(v['liquid_rate_m3d'] for v in par[3].values())
    assert tp == pytest.approx(ts, rel=1e-3) and set(par[0]) == set(s[0]) and par[2]['quality_gate'] == 'PASS' and par[2]['n_components'] >= 2
    ser = solve_parallel(ns, es, solve_v21, workers=1, enforce_constraints=True); assert sum(v['liquid_rate_m3d'] for v in ser[3].values()) == pytest.approx(tp, rel=1e-6)


def test_single_component_is_just_the_solver_and_plan_is_honest():
    n, e = demo_case(); ns, es = solver_input(*normalize_graph(n, e)[:2])
    p, q, i, d = solve_parallel(ns, es, solve_v21, workers=4); assert i['quality_gate'] == 'PASS' and 'n_components' not in i
    assert 'single core' in compute_plan(ns, es, 4)[0] and cpu_count() >= 1
