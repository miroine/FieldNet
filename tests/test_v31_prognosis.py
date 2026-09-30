"""v31: reservoir tanks (in-place volume + phase), forecast, development planning, constraints editor."""
import copy
import pandas as pd
import pytest
from network.examples import demo_case, demo_field_case
from network.forecast import run_forecast
from network.reservoir_mb import Tank, tanks_from_nodes, apply_tank_links, z_factor
from network.prognosis import forecast_kpis, well_count_study, apply_scenario
from ui.graph_contract import normalize_graph, solver_input
from ui.constraints_view import constraint_table, apply_constraint_table
from physics.well_model import well_settings, solve_well_rate
from solver.v21 import solve_v21


def tank(**kw):
    p = {'fluid_phase': 'oil', 'reservoir_pressure_bar': 250.0, 'stoiip_sm3': 10e6, 'bubble_point_bar': 150.0}; p.update(kw)
    return {'id': 'T', 'kind': 'reservoir', 'name': 'T', 'pressure_bar': None, 'params': p}


def test_tank_pipe_to_well_is_converted_to_assignment_and_forecast_is_not_zero():
    n, e = demo_case(); n.append(tank(reservoir_pressure_bar=240.0, stoiip_sm3=20e6))
    e += [{'id': 'r1', 'source': 'T', 'target': 'w1'}, {'id': 'r2', 'source': 'T', 'target': 'w2'}]
    n, e, issues = normalize_graph(n, e)
    assert len(e) == 3 and len(issues) == 2 and n[0]['params']['reservoir_id'] == 'T'
    fc = run_forecast(n, e, '2026-01-01', 0.5, 30)
    assert fc['field'][0]['Oil [m3/d]'] > 100


def test_oil_tank_pore_volume_and_depletion():
    t = Tank(tank(swi=0.2, boi_rm3_sm3=1.25))
    assert t.pv == pytest.approx(10e6 * 1.25 / 0.8)
    p0 = t.p; t.step(10000, 0, 0, dt_days=30); assert t.p < p0
    t2 = Tank(tank(aquifer_pi_m3d_bar=1e5)); t2.p = 200
    t2.step(0, 0, 0, dt_days=30); assert t2.p > 200  # aquifer re-pressurises


def test_solution_gas_drive_slows_decline_below_bubble_point():
    above = Tank(tank(bubble_point_bar=100.0)); above.p = 180
    below = Tank(tank(bubble_point_bar=200.0)); below.p = 180
    above.step(20000, 0, 0, dt_days=30); below.step(20000, 0, 0, dt_days=30)
    assert (180 - below.p) < (180 - above.p)


def test_gas_tank_pz_material_balance():
    t = Tank({'id': 'G', 'kind': 'reservoir', 'params': {'fluid_phase': 'gas', 'giip_sm3': 1e9, 'reservoir_pressure_bar': 250.0, 'temperature_c': 90.0}})
    t.step(0, 0, 0.5e9, dt_days=365)
    zi = z_factor(250.0, 90.0); z = z_factor(t.p, 90.0)
    assert t.p / z == pytest.approx(0.5 * 250.0 / zi, rel=1e-3)


def test_gas_tank_wells_use_backpressure_ipr_and_flow():
    n, e = demo_case(); n.append({'id': 'G', 'kind': 'reservoir', 'name': 'G', 'pressure_bar': None,
                                  'params': {'fluid_phase': 'gas_condensate', 'giip_sm3': 3e9, 'cgr_sm3_per_msm3': 50.0, 'reservoir_pressure_bar': 260.0}})
    for w in n[:2]: w['params']['reservoir_id'] = 'G'
    ns = apply_tank_links(n)
    assert ns[0]['params']['ipr_model'] == 'Gas' and ns[0]['params']['gor_sm3sm3'] == pytest.approx(20000)
    fc = run_forecast(n, e, '2026-01-01', 2, 90)
    assert fc['field'][0]['Gas [Sm3/d]'] > 1e5 and fc['recovery'][0]['RF gas [%]'] > 0


def test_water_cut_rises_with_recovery():
    t = Tank(tank(water_breakthrough_rf=0.0, rf_at_max_water_cut=0.2, max_water_cut=0.8))
    t.np = 0.1 * t.n
    assert t.well_overrides({'water_cut': 0.1})['water_cut'] == pytest.approx(0.45, rel=1e-6)


def test_field_demo_forecast_is_physical():
    n, e = demo_field_case(); fc = run_forecast(n, e, '2026-01-01', 10, 180, enforce_constraints=True)
    k = forecast_kpis(fc)
    assert k['peak_oil_m3d'] > 2000 and 10 < k['rf_oil_pct'] < 60 and k['final_water_cut_pct'] > 20
    assert all(r['Converged'] for r in fc['field'])
    assert all(r['Total liquid [m3/d]'] <= 4500 * 1.001 for r in fc['field'])


def test_large_step_does_not_overshoot_depletion():
    n, e = demo_case(); n.append(tank(reservoir_pressure_bar=240.0, stoiip_sm3=5e6))
    for w in n[:2]: w['params']['reservoir_id'] = 'T'
    a = run_forecast(n, e, '2026-01-01', 3, 30)['field'][-1]['Cumulative oil [Sm3]']
    b = run_forecast(n, e, '2026-01-01', 3, 365)['field'][-1]['Cumulative oil [Sm3]']
    assert b == pytest.approx(a, rel=0.25)


def test_scenario_edits_are_isolated_and_explicit():
    n, e = demo_field_case()
    ns, es = apply_scenario(n, e, {'wells': 'P1,P2', 'in_place_mult': 2.0, 'separator_pressure_bar': 15.0, 'injection': False})
    assert n[0]['params']['stoiip_sm3'] == 30e6 and ns[0]['params']['stoiip_sm3'] == 60e6
    assert next(x for x in ns if x['id'] == 'P3')['params']['available'] is False
    assert next(x for x in ns if x['id'] == 'SEP')['pressure_bar'] == 15.0
    assert next(x for x in ns if x['id'] == 'I1')['params']['available'] is False


def test_well_count_study_recommends_a_count():
    n, e = demo_field_case()
    r = well_count_study(n, e, ['P1', 'P2', 'P3'], '2026-01-01', 5, 180)
    cums = [x['Cum oil [Sm3]'] for x in r['rows']]
    assert cums == sorted(cums) and 1 <= r['recommended_wells'] <= 3 and r['reason']


def test_constraint_bulk_editor_roundtrip():
    n, e = demo_field_case(); df = constraint_table(n, e)
    df.loc[df['ID'] == 'SEP', 'Liquid capacity / max rate [Sm3/d]'] = 2500.0
    df.loc[df['ID'] == 'P1', 'Min BHP [bar]'] = 150.0
    df.loc[df['ID'] == 'FL-A', 'Liquid capacity / max rate [Sm3/d]'] = 900.0
    assert apply_constraint_table(n, e, df) == 3
    assert next(x for x in n if x['id'] == 'SEP')['params']['max_liquid_rate_m3d'] == 2500.0
    assert next(x for x in n if x['id'] == 'P1')['params']['min_bhp_bar'] == 150.0
    assert next(x for x in e if x['id'] == 'FL-A')['params']['max_rate_m3d'] == 900.0
    df2 = constraint_table(n, e); df2.loc[df2['ID'] == 'SEP', 'Liquid capacity / max rate [Sm3/d]'] = None
    apply_constraint_table(n, e, df2); assert 'max_liquid_rate_m3d' not in next(x for x in n if x['id'] == 'SEP')['params']


def test_network_solve_uses_tank_pressure():
    n, e = demo_field_case(); ns, es = solver_input(n, e)
    assert all(x['params']['reservoir_pressure_bar'] == 290.0 for x in ns if x['kind'] == 'well')
    p, q, i, d = solve_v21(ns, es, enforce_constraints=True)
    assert i['quality_gate'] == 'PASS' and sum(v['liquid_rate_m3d'] for v in d.values()) > 2000


def test_solve_status_is_solved_for_tank_models():
    from ui.graph_contract import run_solve, solve_status, current_results
    n, e = demo_field_case(); st = {'nodes': n, 'edges': e}
    run_solve(st, solve_v21)
    assert solve_status(st)[0] == 'SOLVED' and current_results(st) is not None


def test_direct_solver_callers_use_tank_pressure():
    from solver.steady_state import solve_network
    n, e = demo_field_case()
    d = solve_network(n, e)[3]
    assert all(v['reservoir_pressure_bar'] == 290.0 for v in d.values())
