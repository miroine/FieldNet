"""Constraint registry and evaluation, shared by the network solve, the forecast and development scenarios.

Every feature type can carry constraints in its ``params`` (all optional):

wells        max_liquid_rate_m3d, max_oil_rate_m3d, max_water_rate_m3d, max_gas_rate_sm3d, min_bhp_bar, max_drawdown_bar, min_whp_bar, max_whp_bar
separators / exports / sinks  max_liquid_rate_m3d, max_oil_rate_m3d, max_water_rate_m3d, max_gas_rate_sm3d, max_pressure_bar, min_pressure_bar
flowlines    max_rate_m3d (liquid), max_oil_rate_m3d, max_water_rate_m3d, max_gas_rate_sm3d, max_velocity_ms, max_erosional_ratio,
             max_pressure_bar (MAOP), max_dp_bar
equipment    max_rate_m3d, max_power_kw (pump / compressor)
junctions    max_pressure_bar, min_pressure_bar

Rows are ``{Component, ComponentId, Constraint, Value, Limit, Relation, Margin, Unit, Status}``. ``Constraint`` names listed in
``ENFORCEABLE`` scale (about) linearly with the upstream production, so ``solver.v21.enforce_capacity_constraints`` can honour them
by pro-rata choking of the contributing wells; the others are reported (``Status`` VIOLATED) only.
"""
from __future__ import annotations
import math

NODE_CAPACITY = (('max_liquid_rate_m3d', 'Liquid capacity', 'm3/d'), ('max_oil_rate_m3d', 'Oil capacity', 'm3/d'),
                 ('max_water_rate_m3d', 'Water capacity', 'm3/d'), ('max_gas_rate_sm3d', 'Gas capacity', 'Sm3/d'))
EDGE_RATES = (('max_rate_m3d', 'Maximum rate', 'm3/d'), ('max_oil_rate_m3d', 'Maximum oil rate', 'm3/d'),
              ('max_water_rate_m3d', 'Maximum water rate', 'm3/d'), ('max_gas_rate_sm3d', 'Maximum gas rate', 'Sm3/d'))
ENFORCEABLE = {'Liquid capacity', 'Oil capacity', 'Water capacity', 'Gas capacity', 'Maximum rate', 'Maximum oil rate', 'Maximum water rate',
               'Maximum gas rate', 'Maximum velocity', 'Maximum erosional ratio', 'Maximum power'}
CAPACITY_NODE_KINDS = ('separator', 'sink', 'separator_stage', 'water_disposal', 'gas_export', 'oil_export')

# Registry used by the UI (labels/units/where it applies) so every constraint editor shows the same list.
REGISTRY = [
    {'key': 'max_liquid_rate_m3d', 'label': 'Max liquid rate', 'unit': 'Sm3/d', 'applies': ('well', 'node')},
    {'key': 'max_oil_rate_m3d', 'label': 'Max oil rate', 'unit': 'Sm3/d', 'applies': ('well', 'node', 'edge')},
    {'key': 'max_water_rate_m3d', 'label': 'Max water rate', 'unit': 'm3/d', 'applies': ('well', 'node', 'edge')},
    {'key': 'max_gas_rate_sm3d', 'label': 'Max gas rate', 'unit': 'Sm3/d', 'applies': ('well', 'node', 'edge')},
    {'key': 'min_bhp_bar', 'label': 'Min BHP', 'unit': 'bar', 'applies': ('well',)},
    {'key': 'max_drawdown_bar', 'label': 'Max drawdown', 'unit': 'bar', 'applies': ('well',)},
    {'key': 'min_whp_bar', 'label': 'Min WHP', 'unit': 'bar', 'applies': ('well',)},
    {'key': 'max_whp_bar', 'label': 'Max WHP', 'unit': 'bar', 'applies': ('well',)},
    {'key': 'max_rate_m3d', 'label': 'Max liquid rate (line / equipment)', 'unit': 'm3/d', 'applies': ('edge',)},
    {'key': 'max_velocity_ms', 'label': 'Max velocity', 'unit': 'm/s', 'applies': ('edge',)},
    {'key': 'max_erosional_ratio', 'label': 'Max erosional ratio (v/Ve)', 'unit': '-', 'applies': ('edge',)},
    {'key': 'max_pressure_bar', 'label': 'Max pressure (MAOP)', 'unit': 'bar', 'applies': ('node', 'edge')},
    {'key': 'min_pressure_bar', 'label': 'Min pressure', 'unit': 'bar', 'applies': ('node',)},
    {'key': 'max_dp_bar', 'label': 'Max pressure drop', 'unit': 'bar', 'applies': ('edge',)},
    {'key': 'max_power_kw', 'label': 'Max power', 'unit': 'kW', 'applies': ('edge',)},
]


def _lim(prm, key):
    v = prm.get(key)
    if v is None: return None
    try: v = float(v)
    except (TypeError, ValueError): return None
    return v if math.isfinite(v) else None


def evaluate_constraints(nodes, edges, pressures, flows, details, info=None):
    rows = []

    def add(component, constraint, value, limit, relation, unit, cid=None):
        if value is None: return
        margin = (limit - value) if relation == '<=' else (value - limit)
        rows.append({'Component': component, 'ComponentId': cid, 'Constraint': constraint, 'Value': float(value), 'Limit': float(limit), 'Relation': relation,
                     'Margin': float(margin), 'Unit': unit, 'Status': 'OK' if margin >= -1e-9 else 'VIOLATED'})

    needs_phase = False; needs_profile = False
    for n in nodes:
        prm = n.get('params') or {}
        if n.get('kind') in CAPACITY_NODE_KINDS and any(_lim(prm, k) is not None for k in ('max_oil_rate_m3d', 'max_water_rate_m3d', 'max_gas_rate_sm3d')): needs_phase = True
    for e in edges:
        prm = e.get('params') or {}
        if any(_lim(prm, k) is not None for k in ('max_oil_rate_m3d', 'max_water_rate_m3d', 'max_gas_rate_sm3d')): needs_phase = True
        if any(_lim(prm, k) is not None for k in ('max_velocity_ms', 'max_erosional_ratio')): needs_phase = needs_profile = True
    ph = node_in = None
    if needs_phase:
        from network.element_results import phase_flows
        ph, node_in = phase_flows(nodes, edges, flows, details, info)

    for n in nodes:
        prm = n.get('params') or {}; name = n.get('name', n['id']); nid = n['id']; p = pressures.get(nid)
        if p is not None:
            if _lim(prm, 'max_pressure_bar') is not None: add(name, 'Maximum pressure', p, _lim(prm, 'max_pressure_bar'), '<=', 'bar', nid)
            if _lim(prm, 'min_pressure_bar') is not None and n['kind'] != 'reservoir': add(name, 'Minimum pressure', p, _lim(prm, 'min_pressure_bar'), '>=', 'bar', nid)
        if n['kind'] == 'well' and nid in (details or {}):
            d = details[nid]
            for key, label, unit, val in (('max_liquid_rate_m3d', 'Maximum liquid rate', 'm3/d', d['liquid_rate_m3d']), ('max_oil_rate_m3d', 'Maximum oil rate', 'm3/d', d['oil_rate_m3d']),
                                          ('max_water_rate_m3d', 'Maximum water rate', 'm3/d', d['water_rate_m3d']), ('max_gas_rate_sm3d', 'Maximum gas rate', 'Sm3/d', d['gas_rate_sm3d'])):
                if _lim(prm, key) is not None: add(name, label, val, _lim(prm, key), '<=', unit, nid)
            if _lim(prm, 'min_bhp_bar') is not None: add(name, 'Minimum BHP', d['bhp_bar'], _lim(prm, 'min_bhp_bar'), '>=', 'bar', nid)
            if _lim(prm, 'max_drawdown_bar') is not None and d['liquid_rate_m3d'] > 1e-6: add(name, 'Maximum drawdown', d['reservoir_pressure_bar'] - d['bhp_bar'], _lim(prm, 'max_drawdown_bar'), '<=', 'bar', nid)
            if _lim(prm, 'min_whp_bar') is not None and p is not None: add(name, 'Minimum WHP', p, _lim(prm, 'min_whp_bar'), '>=', 'bar', nid)
            if _lim(prm, 'max_whp_bar') is not None and p is not None: add(name, 'Maximum WHP', p, _lim(prm, 'max_whp_bar'), '<=', 'bar', nid)
        if n['kind'] in CAPACITY_NODE_KINDS:
            incoming = sum(max(flows.get(e['id'], 0), 0) for e in edges if e['target'] == nid)
            for key, label, unit in NODE_CAPACITY:
                lim = _lim(prm, key)
                if lim is None: continue
                if key == 'max_liquid_rate_m3d': val = incoming
                else:
                    c = (node_in or {}).get(nid, {}); val = c.get({'max_oil_rate_m3d': 'oil', 'max_water_rate_m3d': 'water', 'max_gas_rate_sm3d': 'gas'}[key], 0.0)
                add(name, label, val, lim, '<=', unit, nid)

    for e in edges:
        prm = e.get('params') or {}; q = flows.get(e['id'], 0.0); name = e.get('name', e['id']); eid = e.get('id')
        for key, label, unit in EDGE_RATES:
            lim = _lim(prm, key)
            if lim is None: continue
            if key == 'max_rate_m3d': val = abs(q)
            else: val = (ph or {}).get(eid, {}).get({'max_oil_rate_m3d': 'oil', 'max_water_rate_m3d': 'water', 'max_gas_rate_sm3d': 'gas'}[key], 0.0)
            add(name, label, val, lim, '<=', unit, eid)
        a, b = pressures.get(e.get('source')), pressures.get(e.get('target'))
        if a is not None and b is not None:
            if _lim(prm, 'max_pressure_bar') is not None: add(name, 'Maximum pressure', max(a, b), _lim(prm, 'max_pressure_bar'), '<=', 'bar', eid)
            if _lim(prm, 'max_dp_bar') is not None: add(name, 'Maximum pressure drop', abs(a - b), _lim(prm, 'max_dp_bar'), '<=', 'bar', eid)
        if needs_profile and e.get('kind', 'pipeline') == 'pipeline' and a is not None and abs(q) > 1e-6 and (_lim(prm, 'max_velocity_ms') is not None or _lim(prm, 'max_erosional_ratio') is not None):
            from network.element_results import edge_summary
            s = edge_summary(e, q, pressures, info, (ph or {}).get(eid))
            if _lim(prm, 'max_velocity_ms') is not None and s['max_velocity_ms'] is not None: add(name, 'Maximum velocity', s['max_velocity_ms'], _lim(prm, 'max_velocity_ms'), '<=', 'm/s', eid)
            if _lim(prm, 'max_erosional_ratio') is not None and s['max_erosional_ratio'] is not None: add(name, 'Maximum erosional ratio', s['max_erosional_ratio'], _lim(prm, 'max_erosional_ratio'), '<=', '-', eid)
        if e.get('kind') in ('pump', 'compressor') and _lim(prm, 'max_power_kw') is not None and a is not None and b is not None and abs(q) > 1e-6:
            from physics.equipment import pump_power_kw, compressor_power_kw, pump_head_bar
            from solver.equations import fnum
            if e['kind'] == 'pump':
                h = pump_head_bar(q, fnum(prm, 'shutoff_head_bar', 35), fnum(prm, 'rated_rate_m3d', 1500), fnum(prm, 'min_head_bar', -1e9))
                kw = pump_power_kw(q, h, fnum(prm, 'rho_kgm3', 850), fnum(prm, 'efficiency', .75))
            else: kw = compressor_power_kw(abs(q) * fnum(prm, 'gor_sm3sm3', 100), a, b, fnum(prm, 'efficiency', .75))
            add(name, 'Maximum power', kw, _lim(prm, 'max_power_kw'), '<=', 'kW', eid)
    return rows


def active_constraints(rows, tolerance_fraction=0.05):
    """Return violated or near-active constraints, most limiting first."""
    out = []
    for r in rows:
        limit = abs(float(r.get('Limit', 0.0))); margin = float(r.get('Margin', 0.0)); frac = margin / max(limit, 1e-12)
        if r.get('Status') == 'VIOLATED' or frac <= float(tolerance_fraction):
            z = dict(r); z['MarginFraction'] = frac; out.append(z)
    return sorted(out, key=lambda x: x['MarginFraction'])
