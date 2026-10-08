"""Feature catalogue: the single source of truth for what can be placed on the canvas.

Features are *grouped* (Wells, Equipment, Processing, ...). A feature has a ``kind`` (what the solver sees) plus
user-level attributes such as the *role* (producer/injector) and the *phase* (oil/gas/water/gas_condensate) of a
well or the *type* of a separator. Changing role/phase/type on an existing node migrates its parameters
(``set_well_role``, ``set_separator_type``) instead of forcing the user to delete and re-create it.
"""
from __future__ import annotations
import copy

PHASES = ('oil', 'gas', 'gas_condensate', 'water')
PHASE_LABEL = {'oil': 'Oil', 'gas': 'Gas', 'gas_condensate': 'Gas condensate', 'water': 'Water'}
WELL_KINDS = ('well', 'water_injector', 'gas_injector', 'injector')
SEPARATOR_TYPES = {
    'two_phase': 'Two-phase (oil / gas)',
    'three_phase': 'Three-phase (oil / gas / water)',
    'test': 'Test separator',
    'scrubber': 'Gas scrubber',
    'water_treatment': 'Water treatment',
}
SEPARATOR_KINDS = ('separator', 'separator_stage')
CAPACITY_KEYS = ('max_liquid_rate_m3d', 'max_oil_rate_m3d', 'max_water_rate_m3d', 'max_gas_rate_sm3d')


def _producer_defaults(phase):
    base = {'reservoir_pressure_bar': 220.0, 'ipr_model': 'PI', 'pi_m3d_bar': 10.0, 'qmax_m3d': 1500.0, 'initial_rate_m3d': 500.0, 'depth_m': 2000.0,
            'tubing_id_m': 0.0889, 'tubing_roughness_m': 4.5e-5, 'temperature_c': 70.0, 'water_cut': 0.2, 'gor_sm3sm3': 100.0, 'api': 35.0,
            'gas_sg': 0.75, 'vlp_model': 'Beggs-Brill', 'available': True, 'role': 'producer', 'phase': phase}
    if phase in ('gas', 'gas_condensate'):
        base.update({'ipr_model': 'Gas', 'gas_c_sm3d_bar2n': 50.0, 'gas_n': 1.0, 'water_cut': 0.0, 'gor_sm3sm3': 20000.0 if phase == 'gas_condensate' else 1e5})
    return base


def _injector_defaults(phase):
    return {'injection_fluid': 'gas' if phase == 'gas' else 'water', 'injectivity_m3d_bar': 10.0, 'reservoir_pressure_bar': 200.0, 'depth_m': 2000.0,
            'available': True, 'role': 'injector', 'phase': 'gas' if phase == 'gas' else 'water'}


def _node(kind, label, group, params=None, pressure=None, subtype=None):
    return {'group': group, 'label': label, 'kind': kind, 'pressure_bar': pressure, 'params': params or {}, 'subtype': subtype}


def palette():
    """Ordered list of {group, items:[...]} for the editor palette (JSON-serialisable)."""
    P = []
    def grp(name, items): P.append({'group': name, 'items': items})
    # One palette item per equipment type; the fluid / role / type is chosen in the element's settings (set_well_role, set_separator_type, tank fluid).
    grp('Reservoir', [_node('reservoir', 'Tank', 'Reservoir', {'fluid_phase': 'oil', 'reservoir_pressure_bar': 250.0, 'stoiip_sm3': 30e6, 'temperature_c': 90.0, 'min_pressure_bar': 20.0})])
    grp('Wells', [_node('well', 'Well', 'Wells', _producer_defaults('oil'), subtype='oil_producer')])
    grp('Connections', [_node('joint', 'Joint', 'Connections'), _node('manifold', 'Manifold', 'Connections')])
    grp('Equipment', [_node('choke', 'Choke', 'Equipment', {'cv': 80.0}),
                      _node('control_valve', 'Control valve', 'Equipment', {'cv': 80.0, 'opening': 1.0}),
                      _node('pump', 'Pump', 'Equipment', {'shutoff_head_bar': 35.0, 'rated_rate_m3d': 1500.0, 'efficiency': 0.75}),
                      _node('compressor', 'Compressor', 'Equipment', {'pressure_ratio': 1.8, 'max_discharge_bar': 250.0, 'efficiency': 0.75})])
    grp('Processing', [_node('separator', 'Separator', 'Processing', {'separator_type': 'two_phase'}, 35.0, 'two_phase')])
    grp('Boundaries', [_node('sink', 'Sink', 'Boundaries', pressure=35.0), _node('oil_export', 'Oil export', 'Boundaries', pressure=35.0),
                       _node('gas_export', 'Gas export', 'Boundaries', pressure=35.0), _node('water_disposal', 'Water disposal', 'Boundaries', pressure=35.0),
                       _node('water_source', 'Water source', 'Boundaries', pressure=180.0), _node('gas_source', 'Gas source', 'Boundaries', pressure=180.0)])
    return P


def role_phase(node):
    """(role, phase) of a well/injector node, derived from kind + params for legacy nodes."""
    p = node.get('params') or {}; k = node.get('kind')
    if k == 'well':
        ph = p.get('phase') or ('gas' if str(p.get('ipr_model', '')).lower().startswith('gas') else 'oil')
        return 'producer', ph if ph in PHASES else 'oil'
    if k in ('water_injector', 'gas_injector', 'injector'):
        ph = 'gas' if (k == 'gas_injector' or p.get('injection_fluid') == 'gas') else 'water'
        return 'injector', ph
    return None, None


def set_well_role(node, role, phase):
    """Change a well between producer/injector and set its phase, keeping what still applies (depth, reservoir tank,
    availability, trajectory/completion, constraints). Returns the (mutated) node."""
    p = node.setdefault('params', {}); keep = {k: v for k, v in p.items()}
    if role == 'producer':
        phase = phase if phase in ('oil', 'gas', 'gas_condensate') else 'oil'
        node['kind'] = 'well'
        newp = _producer_defaults(phase)
        for k in ('depth_m', 'reservoir_pressure_bar', 'available', 'reservoir_id', 'trajectory', 'completion', 'skin', 'lift_type', 'max_liquid_rate_m3d',
                  'max_oil_rate_m3d', 'max_gas_rate_sm3d', 'max_water_rate_m3d', 'min_bhp_bar', 'tubing_id_m', 'tubing_roughness_m', 'temperature_c'):
            if k in keep: newp[k] = keep[k]
        if phase == 'oil' and keep.get('ipr_model') in ('PI', 'Vogel'): newp['ipr_model'] = keep['ipr_model']
        for k in ('pi_m3d_bar', 'qmax_m3d', 'water_cut', 'api', 'gas_sg', 'vlp_model', 'gas_c_sm3d_bar2n', 'gas_n', 'gor_sm3sm3'):
            if k in keep and ((phase == 'oil') == (not str(keep.get('ipr_model', 'PI')).lower().startswith('gas')) or k in ('api', 'gas_sg', 'vlp_model')): newp[k] = keep[k]
    else:
        phase = 'gas' if phase == 'gas' else 'water'
        node['kind'] = 'gas_injector' if phase == 'gas' else 'water_injector'
        newp = _injector_defaults(phase)
        for k in ('depth_m', 'reservoir_pressure_bar', 'available', 'reservoir_id', 'trajectory', 'completion', 'injectivity_m3d_bar', 'max_rate_m3d', 'tubing_id_m', 'skin'):
            if k in keep: newp[k] = keep[k]
    node['params'] = newp
    return node


def set_separator_type(node, sep_type):
    """Separator type controls which phase handling capacities are meaningful; unused capacities are dropped."""
    if sep_type not in SEPARATOR_TYPES: return node
    p = node.setdefault('params', {}); p['separator_type'] = sep_type
    if sep_type == 'water_treatment':
        for k in ('max_oil_rate_m3d', 'max_gas_rate_sm3d', 'max_liquid_rate_m3d'): p.pop(k, None)
    elif sep_type == 'scrubber':
        for k in ('max_oil_rate_m3d', 'max_water_rate_m3d', 'max_liquid_rate_m3d'): p.pop(k, None)
    elif sep_type == 'two_phase': p.pop('max_water_rate_m3d', None)
    return node


def applicable_capacities(node):
    """Which handling capacities the UI should offer for a node."""
    k = node.get('kind')
    if k in SEPARATOR_KINDS:
        t = (node.get('params') or {}).get('separator_type', 'two_phase')
        return {'water_treatment': ['max_water_rate_m3d'], 'scrubber': ['max_gas_rate_sm3d'], 'two_phase': ['max_liquid_rate_m3d', 'max_oil_rate_m3d', 'max_gas_rate_sm3d']}.get(
            t, list(CAPACITY_KEYS))
    if k in ('sink', 'oil_export', 'gas_export', 'water_disposal'): return {'oil_export': ['max_oil_rate_m3d'], 'gas_export': ['max_gas_rate_sm3d'], 'water_disposal': ['max_water_rate_m3d']}.get(k, list(CAPACITY_KEYS))
    return []
