from network.features import palette, set_well_role, set_separator_type, role_phase, applicable_capacities
from ui.topology import validate_topology, VALID_KINDS
from solver.steady_state import solve_network


def test_palette_kinds_are_valid_and_grouped():
    pal = palette(); names = [g['group'] for g in pal]
    assert names[:3] == ['Reservoir', 'Wells', 'Connections'] and 'Equipment' in names
    for g in pal:
        for it in g['items']: assert it['kind'] in VALID_KINDS, it


def test_well_role_phase_migration_keeps_assignments():
    n = {'id': 'w', 'kind': 'well', 'name': 'W', 'params': {'phase': 'oil', 'depth_m': 3100.0, 'reservoir_id': 'T', 'available': False, 'skin': 2.0}}
    set_well_role(n, 'injector', 'water'); assert n['kind'] == 'water_injector' and n['params']['depth_m'] == 3100.0 and n['params']['reservoir_id'] == 'T'
    assert role_phase(n) == ('injector', 'water') and n['params']['available'] is False
    set_well_role(n, 'injector', 'gas'); assert n['kind'] == 'gas_injector' and n['params']['injection_fluid'] == 'gas'
    set_well_role(n, 'producer', 'gas'); assert n['kind'] == 'well' and n['params']['ipr_model'] == 'Gas' and n['params']['skin'] == 2.0
    assert role_phase(n) == ('producer', 'gas')


def test_separator_types_drive_capacities():
    s = {'id': 's', 'kind': 'separator', 'params': {'max_oil_rate_m3d': 1, 'max_water_rate_m3d': 2, 'max_gas_rate_sm3d': 3}}
    set_separator_type(s, 'water_treatment'); assert applicable_capacities(s) == ['max_water_rate_m3d'] and 'max_oil_rate_m3d' not in s['params']
    assert 'max_liquid_rate_m3d' in applicable_capacities({'kind': 'separator', 'params': {'separator_type': 'three_phase'}})


def test_joint_node_solves_like_a_manifold():
    n = [{'id': 'w', 'kind': 'well', 'name': 'w', 'params': {'reservoir_pressure_bar': 250, 'pi_m3d_bar': 20, 'depth_m': 2000, 'tubing_id_m': .1}},
         {'id': 'j', 'kind': 'joint', 'name': 'J', 'params': {}}, {'id': 's', 'kind': 'separator', 'name': 'S', 'pressure_bar': 30, 'params': {}}]
    e = [{'id': 'a', 'source': 'w', 'target': 'j', 'kind': 'pipeline', 'length_m': 500, 'diameter_m': .15}, {'id': 'b', 'source': 'j', 'target': 's', 'kind': 'pipeline', 'length_m': 500, 'diameter_m': .15}]
    assert not [i for i in validate_topology(n, e) if i['severity'] == 'error']
    p, q, info, d = solve_network(n, e); assert info['success'] and d['w']['liquid_rate_m3d'] > 100
