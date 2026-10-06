"""Smoke / logic tests for ui/properties.py with a fake Streamlit (the real UI could not be run in this environment)."""
import pytest
from tests.support.fake_streamlit import FakeSt
from ui import properties as pr
from network.features import palette


def W(st, key, value, model):
    """Simulate the user editing a synced widget: widget value differs from the model value mirrored at the last run."""
    st.session_state[key] = value; st.session_state['_model__' + key] = model


def test_constraint_keys_by_element():
    well = [k for k, _, _ in pr.constraint_keys_for('well')]; assert {'max_oil_rate_m3d', 'max_water_rate_m3d', 'max_gas_rate_sm3d', 'min_bhp_bar', 'max_drawdown_bar'} <= set(well)
    pipe = [k for k, _, _ in pr.constraint_keys_for('pipeline', 'edge')]; assert {'max_velocity_ms', 'max_erosional_ratio', 'max_rate_m3d', 'max_pressure_bar'} <= set(pipe) and 'max_power_kw' not in pipe
    comp = [k for k, _, _ in pr.constraint_keys_for('compressor', 'edge')]; assert 'max_power_kw' in comp and 'max_velocity_ms' not in comp
    sep = [k for k, _, _ in pr.constraint_keys_for('separator', 'node', {'separator_type': 'three_phase'})]; assert {'max_liquid_rate_m3d', 'max_oil_rate_m3d', 'max_water_rate_m3d', 'max_gas_rate_sm3d'} <= set(sep)
    assert [k for k, _, _ in pr.constraint_keys_for('separator', 'node', {'separator_type': 'water_treatment'})][0] == 'max_water_rate_m3d'


def test_constraint_editor_writes_only_enabled_and_removes_disabled():
    st = FakeSt(); node = {'id': 'w', 'kind': 'well', 'params': {'max_oil_rate_m3d': 500.0, 'min_bhp_bar': 80.0}}
    W(st, 'c_on_max_water_rate_m3d_w', True, False); W(st, 'c_v_max_water_rate_m3d_w', 250.0, 0.0)
    W(st, 'c_on_min_bhp_bar_w', False, True); W(st, 'c_on_max_oil_rate_m3d_w', False, True)
    pr.constraint_editor(st, node, None, 'well', 'w')
    p = node['params']; assert p['max_water_rate_m3d'] == 250.0 and 'min_bhp_bar' not in p and 'max_oil_rate_m3d' not in p
    assert 'rate_limit_mode' in p


def test_selecting_does_not_change_model():
    import copy
    st = FakeSt(); node = {'id': 'w', 'kind': 'well', 'params': {'depth_m': 2000.0}}; before = copy.deepcopy(node)
    pr.constraint_editor(st, node, None, 'well', 'w'); pr.trajectory_editor(st, node); pr.relperm_editor(st, {'id': 'r', 'kind': 'reservoir', 'params': {}})
    pr.prediction_source_editor(st, node, '2026-01-01'); assert node == before


def test_role_phase_converts_node():
    st = FakeSt(); node = {'id': 'w', 'kind': 'well', 'params': {'depth_m': 3000.0, 'phase': 'oil'}}
    W(st, 'wrolew', 'injector', 'producer')
    assert pr.role_phase_editor(st, node) is True and node['kind'] == 'water_injector' and node['params']['depth_m'] == 3000.0   # role first: phase defaults to water
    W(st, 'wphasew', 'gas', 'water')
    assert pr.role_phase_editor(st, node) is True and node['kind'] == 'gas_injector' and node['params']['depth_m'] == 3000.0


def test_separator_type_editor():
    st = FakeSt(); n = {'id': 's', 'kind': 'separator', 'params': {'max_oil_rate_m3d': 5}}; W(st, 'septypes', 'water_treatment', 'two_phase')
    pr.separator_type_editor(st, n); assert n['params']['separator_type'] == 'water_treatment' and 'max_oil_rate_m3d' not in n['params']


def test_trajectory_template_and_completion_roundtrip():
    st = FakeSt(); node = {'id': 'w', 'kind': 'well', 'params': {'depth_m': 2500.0}}
    W(st, 'tjmw', 'template', 'vertical'); st.buttons.add('tjapplyw'); pr.trajectory_editor(st, node)
    assert node['params']['trajectory'] and node['params']['depth_m'] == pytest.approx(2500.0, rel=0.02)
    xz = pr.survey_xz(node['params']['trajectory']); assert xz[-1][0] > 500 and xz[-1][1] == pytest.approx(2500.0, rel=0.02)


def test_riser_and_profile_editor():
    st = FakeSt(); e = {'id': 'e', 'kind': 'pipeline', 'length_m': 3000.0, 'diameter_m': 0.2, 'elevation_change_m': 0.0, 'params': {}}
    W(st, 'rone', True, False); W(st, 'rshe', 'catenary', 'vertical'); W(st, 'rwde', 350.0, 300.0)
    pr.flowline_profile_editor(st, e); r = e['params']['riser']; assert r['enabled'] and r['shape'] == 'catenary' and r['water_depth_m'] == 350.0 and 'profile' not in e['params']
    from solver.equations import flowline_segments
    assert sum(s[1] for s in flowline_segments(e)) == pytest.approx(350.0, rel=0.05)


def test_relperm_editor_enables_model():
    st = FakeSt(); n = {'id': 'r', 'kind': 'reservoir', 'params': {'fluid_phase': 'oil', 'stoiip_sm3': 1e7}}
    W(st, 'rponr', True, False); pr.relperm_editor(st, n)
    assert n['params']['relperm']['model'] == 'corey' and n['params']['water_cut_mode'] == 'relperm'
    from network.reservoir_mb import Tank; t = Tank(n); assert t.rp is not None


def test_prediction_source_decline_drives_forecast_cap():
    st = FakeSt(); node = {'id': 'P1', 'kind': 'well', 'params': {}}
    W(st, 'pstyP1', 'decline', 'none'); pr.prediction_source_editor(st, node, '2026-01-01')
    assert node['params']['prediction_source']['type'] == 'decline' and node['params']['prediction_source']['apply_as'] == 'rate_cap'


def test_palette_matches_panel_kinds():
    from ui.topology import VALID_KINDS
    assert all(it['kind'] in VALID_KINDS for g in palette() for it in g['items'])
