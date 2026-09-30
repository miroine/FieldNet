"""Regression tests for the v30 audit fixes."""
import copy, math
from pathlib import Path
import pytest
from network.examples import demo_case
from solver.steady_state import solve_network
from solver.v21 import solve_v21, topology_precheck
from physics.vlp import tubing_bhp_bar
from physics.choke import choke_dp_bar
from physics.controls import control_valve_dp_bar
from physics.hydraulics import friction_factor
from physics.well_model import well_settings, solve_well_rate
from network.interchange_v27 import validate_project
from network.forecast import run_forecast
from ui.widgets import clean_num, clean_text, to_builtin
from ui.topology import validate_topology
from ui.uncertainty_v17 import parse_uncertainty_rows


def _rates(d): return {k: v['liquid_rate_m3d'] for k, v in d.items()}


def test_demo_kernel_converges_fast_and_consistently():
    n, e = demo_case(); p, q, info, d = solve_network(n, e)
    assert info['success'] and info['max_abs_residual'] < 1e-8
    assert abs(q['trunk'] - sum(_rates(d).values())) < 1e-6


def test_well_rate_matches_independent_nodal_solution():
    n, e = demo_case(); p, q, info, d = solve_v21(n, e)
    for w in n[:2]:
        qa, _ = solve_well_rate(p[w['id']], well_settings(w['params']))
        assert abs(qa - d[w['id']]['liquid_rate_m3d']) < 0.5


@pytest.mark.parametrize('sep', [20, 33.3, 53.3, 60, 120])
def test_back_pressure_sweep_passes_quality_gate(sep):
    n, e = demo_case(); n[3]['pressure_bar'] = sep
    p, q, info, d = solve_v21(n, e)
    assert info['quality_gate'] == 'PASS'
    assert all(v['liquid_rate_m3d'] >= 0 for v in d.values())


def test_rate_decreases_with_back_pressure():
    rates = []
    for sep in (20, 30, 40, 50):
        n, e = demo_case(); n[3]['pressure_bar'] = sep; rates.append(sum(_rates(solve_network(n, e)[3]).values()))
    assert rates == sorted(rates, reverse=True)


def test_sink_without_pressure_is_a_clear_topology_error_not_keyerror():
    n, e = demo_case(); n[3]['pressure_bar'] = None
    p, q, info, d = solve_v21(n, e)
    assert info['quality_gate'] == 'FAIL' and any(x['code'] in ('BOUNDARY_WITHOUT_PRESSURE', 'UNANCHORED_COMPONENT') for x in info['debug'])


def test_isolated_new_component_does_not_break_solve():
    n, e = demo_case(); n.append({'id': 'new', 'kind': 'well', 'name': 'NEW', 'pressure_bar': None, 'params': {'reservoir_pressure_bar': 200}})
    p, q, info, d = solve_v21(n, e)
    assert info['quality_gate'] == 'PASS' and 'new' not in p
    assert any(x['code'] == 'NOT_CONNECTED' for x in info['debug'])


def test_unavailable_vogel_well_is_shut_in():
    n, e = demo_case(); n[0]['params'].update({'ipr_model': 'Vogel', 'available': False})
    p, q, info, d = solve_network(n, e)
    assert d['w1']['liquid_rate_m3d'] == 0 and d['w1']['status'] == 'shut_in'


def test_skin_now_affects_network_rate():
    n, e = demo_case(); base = solve_network(n, e)[3]['w1']['liquid_rate_m3d']
    n[0]['params']['skin'] = 10.0; damaged = solve_network(n, e)[3]['w1']['liquid_rate_m3d']
    assert damaged < base


def test_gas_lift_and_esp_are_coupled_into_network():
    n, e = demo_case(); base = sum(_rates(solve_network(n, e)[3]).values())
    n1 = copy.deepcopy(n); n1[1]['params'].update({'lift_type': 'gas_lift', 'gas_lift_injection_sm3d': 60000})
    n2 = copy.deepcopy(n); n2[1]['params'].update({'lift_type': 'ESP', 'esp_rated_rate_m3d': 1200, 'esp_shutoff_head_bar': 80})
    assert sum(_rates(solve_network(n1, e)[3]).values()) > base
    assert sum(_rates(solve_network(n2, e)[3]).values()) > base


def test_well_rate_cap_is_enforced():
    n, e = demo_case(); n[0]['params']['max_liquid_rate_m3d'] = 200.0
    d = solve_network(n, e)[3]
    assert abs(d['w1']['liquid_rate_m3d'] - 200.0) < 1e-6 and d['w1']['status'] == 'rate_limited'


def test_separator_capacity_enforced_by_prorata_choking():
    n, e = demo_case(); n[3]['pressure_bar'] = 20.0; n[3]['params']['max_liquid_rate_m3d'] = 500.0
    p, q, info, d = solve_v21(n, e, enforce_constraints=True)
    assert info['quality_gate'] == 'PASS' and info['violations'] == 0
    assert q['trunk'] <= 500.0 + 1e-6 and info['constraint_actions']
    p2, q2, info2, d2 = solve_v21(n, e)
    assert info2['violations'] == 1  # report-only mode still reports it


def test_water_injection_subnetwork():
    nodes = [{'id': 'src', 'kind': 'water_source', 'name': 'SRC', 'pressure_bar': 5.0, 'params': {}},
             {'id': 'inj', 'kind': 'water_injector', 'name': 'I1', 'pressure_bar': None, 'params': {'injectivity_m3d_bar': 20, 'reservoir_pressure_bar': 230, 'depth_m': 2000}}]
    edges = [{'id': 'pmp', 'source': 'src', 'target': 'inj', 'kind': 'pump', 'params': {'shutoff_head_bar': 100, 'rated_rate_m3d': 5000}}]
    p, q, info, d = solve_v21(nodes, edges)
    assert info['quality_gate'] == 'PASS' and q['pmp'] > 0
    assert abs(info['injector_rates']['inj'] - q['pmp']) < 1e-6


def test_segmented_vlp_differs_from_single_average():
    a, _ = tubing_bhp_bar(500, 30, 2500, 0.1, 4.5e-5, 80, 0.1, 200, 35, 0.75)
    b, _ = tubing_bhp_bar(500, 30, 2500, 0.1, 4.5e-5, 80, 0.1, 200, 35, 0.75, segments=1)
    assert a > 30 and abs(a - b) > 0.5


def test_signed_valve_and_choke_drops():
    assert choke_dp_bar(-400) == pytest.approx(-choke_dp_bar(400))
    assert control_valve_dp_bar(-400, 80) == pytest.approx(-control_valve_dp_bar(400, 80))


def test_friction_factor_is_continuous_through_transition():
    assert abs(friction_factor(1999.9, 1e-4) - friction_factor(2000.1, 1e-4)) < 1e-4
    assert abs(friction_factor(3999.9, 1e-4) - friction_factor(4000.1, 1e-4)) < 1e-4


def test_control_valve_is_a_valid_topology_edge():
    n = [{'id': 'a', 'kind': 'manifold', 'name': 'A'}, {'id': 'b', 'kind': 'sink', 'name': 'B'}]
    e = [{'id': '1', 'source': 'a', 'target': 'b', 'kind': 'control_valve'}]
    assert not any(x['severity'] == 'error' for x in validate_topology(n, e))


def test_choke_with_zero_length_passes_interchange_validation():
    n, e = demo_case(); e[0].update({'kind': 'choke', 'length_m': 0.0})
    assert not any(x['severity'] == 'error' for x in validate_project(n, e))


def test_forecast_warm_start_and_capacity_option():
    n, e = demo_case(); n[3]['params']['max_liquid_rate_m3d'] = 400.0
    fc = run_forecast(n, e, '2026-01-01', 0.25, 30, enforce_constraints=True)
    assert all(r['Converged'] for r in fc['field'])
    assert all(r['Total liquid [m3/d]'] <= 400.0 + 1e-3 for r in fc['field'])


def test_data_editor_nan_cleaning():
    nan = float('nan')
    assert clean_num(nan, 5.0) == 5.0 and clean_num('', 1.0) == 1.0 and clean_num('2.5') == 2.5
    assert clean_text(nan) == '' and clean_text(' x ') == 'x'
    rows = parse_uncertainty_rows([{'name': 'PI', 'path': 'params.pi_m3d_bar', 'target_id': 'w1', 'low': nan, 'physical_max': nan, 'bound_policy': nan}])
    assert rows[0].low == 0.8 and rows[0].physical_max is None and rows[0].bound_policy == 'clip'
    assert parse_uncertainty_rows([{'name': nan, 'path': nan}]) == []


def test_numpy_values_are_made_json_safe():
    import json, numpy as np
    json.dumps(to_builtin({'a': np.int64(3), 'b': [np.float64(1.5)]}))


def test_canvas_sends_revision_and_selection():
    text = (Path(__file__).parents[1] / 'ui/fieldnet_canvas/build/index.html').read_text()
    assert 'rev:Date.now()' in text
    assert 'selected=n.id;render();send()' in text
