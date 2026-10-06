import copy, pytest
from network.examples import demo_field_case
from network.solve_options import normalize_compute, make_network_solver, make_step_solver
from network.forecast import run_forecast
from ui.graph_contract import normalize_graph, solver_input, run_solve, solve_status


def _case(cap=2500.0):
    n, e = demo_field_case(); n, e, _ = normalize_graph(n, e); next(x for x in n if x['id'] == 'SEP')['params']['max_liquid_rate_m3d'] = cap; return n, e


def _tot(d, k='oil_rate_m3d'): return sum(v[k] for v in d.values())


def test_honour_toggle():
    n, e = _case(); ns, es = solver_input(n, e)
    on = make_network_solver({'honour': True})(ns, es); off = make_network_solver({'honour': False})(ns, es)
    assert _tot(on[3], 'liquid_rate_m3d') <= 2500 * 1.01 and _tot(off[3], 'liquid_rate_m3d') > 2500 * 1.05 and off[2]['violations'] >= 1 and on[2]['violations'] == 0


def test_optimizer_beats_pro_rata_for_water_heavy_wells_and_keeps_quality_gate():
    n, e = _case(); w = [x for x in n if x['kind'] == 'well']; w[0]['params']['water_cut'] = 0.85
    ns, es = solver_input(n, e)
    pro = make_network_solver({'honour': True})(ns, es)
    opt = make_network_solver({'honour': True, 'optimizer': {'enabled': True, 'objective': {'preset': 'max_oil'}}})(ns, es)
    assert opt[2]['quality_gate'] == 'PASS' and _tot(opt[3], 'liquid_rate_m3d') <= 2500 * 1.01
    assert _tot(opt[3]) >= _tot(pro[3]) * 0.999 and 'optimizer' in opt[2]


def test_run_solve_with_optimizer_is_SOLVED():
    n, e = _case(); st = {'nodes': n, 'edges': e}
    run_solve(st, make_network_solver({'optimizer': {'enabled': True, 'objective': {'preset': 'max_oil'}}}), warm_start=None, attempts=2)
    assert solve_status(st)[0] == 'SOLVED'


def test_forecast_step_solver_hook():
    n, e = _case(); step = make_step_solver({'optimizer': {'enabled': True, 'objective': {'preset': 'max_oil'}}})
    fc = run_forecast(n, e, '2026-01-01', 0.25, 90, step_solver=step)
    assert all(r['Total liquid [m3/d]'] <= 2500 * 1.02 for r in fc['field']) and fc['field'][0]['Oil [m3/d]'] > 100


def test_invalid_objective_is_rejected_and_defaults():
    with pytest.raises(ValueError): normalize_compute({'optimizer': {'enabled': True, 'objective': {'preset': 'custom', 'expression': 'oil +'}}})
    assert normalize_compute(None)['honour'] is True and normalize_compute({'workers': 0})['workers'] == 1
