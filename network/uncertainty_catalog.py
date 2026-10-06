"""Friendly catalogue for building Monte Carlo uncertainty parameters from drop-down choices (no paths or ids to type)."""
from __future__ import annotations

# (label, path, operation, (low, mode, high), physical_min, physical_max, applies to node kinds / edge kinds)
WELL = ('well',)
SPECS = [
    ('Reservoir pressure', 'params.reservoir_pressure_bar', 'multiply', (0.9, 1.0, 1.1), 1.0, None, ('well', 'reservoir')),
    ('Productivity index (PI)', 'params.pi_m3d_bar', 'multiply', (0.7, 1.0, 1.3), 0.0, None, WELL),
    ('Water cut', 'params.water_cut', 'multiply', (0.6, 1.0, 1.5), 0.0, 0.99, WELL),
    ('GOR', 'params.gor_sm3sm3', 'multiply', (0.8, 1.0, 1.3), 0.0, None, WELL),
    ('Tubing roughness', 'params.tubing_roughness_m', 'multiply', (0.5, 1.0, 2.0), 1e-7, None, WELL),
    ('In-place oil (STOIIP)', 'params.stoiip_sm3', 'multiply', (0.75, 1.0, 1.3), 0.0, None, ('reservoir',)),
    ('In-place gas (GIIP)', 'params.giip_sm3', 'multiply', (0.75, 1.0, 1.3), 0.0, None, ('reservoir',)),
    ('Aquifer strength', 'params.aquifer_pi_m3d_bar', 'multiply', (0.5, 1.0, 2.0), 0.0, None, ('reservoir',)),
    ('Rock + fluid compressibility', 'params.ct_1bar', 'multiply', (0.7, 1.0, 1.4), 1e-9, None, ('reservoir',)),
    ('Boundary / separator pressure', 'pressure_bar', 'multiply', (0.9, 1.0, 1.15), 0.5, None, ('separator', 'sink', 'oil_export', 'gas_export', 'water_disposal', 'separator_stage')),
    ('Liquid capacity', 'params.max_liquid_rate_m3d', 'multiply', (0.8, 1.0, 1.2), 0.0, None, ('separator', 'sink', 'oil_export', 'separator_stage')),
    ('Gas capacity', 'params.max_gas_rate_sm3d', 'multiply', (0.8, 1.0, 1.2), 0.0, None, ('separator', 'sink', 'gas_export', 'separator_stage')),
    ('Water capacity', 'params.max_water_rate_m3d', 'multiply', (0.8, 1.0, 1.2), 0.0, None, ('separator', 'sink', 'water_disposal', 'separator_stage')),
    ('Flowline roughness', 'roughness_m', 'multiply', (0.5, 1.0, 2.0), 1e-7, None, ('edge:pipeline',)),
    ('Flowline diameter', 'diameter_m', 'multiply', (0.95, 1.0, 1.05), 0.01, None, ('edge:pipeline',)),
]
DISTRIBUTIONS = {'triangular': 'Triangular (low – most likely – high)', 'uniform': 'Uniform (all values equally likely)', 'normal': 'Normal (low/high = P10/P90)', 'lognormal': 'Lognormal (low/high = P10/P90)'}
_Z = 1.2815515655446004   # P10 / P90 of a standard normal


def _get(obj, path):
    cur = obj
    for part in path.split('.'):
        if not isinstance(cur, dict) or part not in cur: return None
        cur = cur[part]
    return cur if isinstance(cur, (int, float)) and not isinstance(cur, bool) else None


def target_options(nodes, edges):
    """[(target_id, label)] — groups first (one shared factor for all members), then single elements that carry at least one catalogue parameter."""
    out = []
    kinds = {}
    for n in nodes: kinds.setdefault(n.get('kind'), []).append(n)
    for k, label in (('well', 'All wells (same factor)'), ('reservoir', 'All reservoir tanks (same factor)')):
        if len(kinds.get(k, [])) > 1: out.append((f'kind:{k}', label))
    if sum(1 for e in edges if e.get('kind', 'pipeline') == 'pipeline') > 1: out.append(('edges:pipeline', 'All flowlines (same factor)'))
    for n in nodes:
        if parameters_for(nodes, edges, n['id']): out.append((n['id'], f"{n.get('name', n['id'])} ({n.get('kind')})"))
    for e in edges:
        if e.get('kind', 'pipeline') == 'pipeline' and parameters_for(nodes, edges, e['id']): out.append((e['id'], f"{e.get('name') or e['id']} (flowline)"))
    return out


def _members(nodes, edges, target_id):
    t = str(target_id)
    if t.startswith('kind:'): return [n for n in nodes if n.get('kind') == t[5:]]
    if t == 'edges:pipeline': return [e for e in edges if e.get('kind', 'pipeline') == 'pipeline']
    return [x for x in [*nodes, *edges] if str(x.get('id')) == t]


def parameters_for(nodes, edges, target_id):
    """Catalogue entries that exist (numeric) on the target (any member for a group)."""
    mem = _members(nodes, edges, target_id); res = []
    for spec in SPECS:
        label, path, op, rng, pmin, pmax, kinds = spec
        for m in mem:
            kind = m.get('kind') if 'source' not in m else 'edge:' + str(m.get('kind', 'pipeline'))
            if kind in kinds and _get(m, path) is not None:
                res.append({'label': label, 'path': path, 'operation': op, 'low': rng[0], 'mode': rng[1], 'high': rng[2], 'physical_min': pmin, 'physical_max': pmax}); break
    return res


def make_parameter(target_id, target_label, spec, low, mode, high, distribution='triangular'):
    """Row in the ``PARAM_COLS`` schema used by ``ui.uncertainty_v17.parse_uncertainty_rows``. Normal / lognormal: low/high are P10/P90 factors."""
    low, mode, high = float(low), float(mode), float(high)
    if not low <= mode <= high: raise ValueError('Need low ≤ most likely ≤ high')
    mean = mode; std = max((high - low) / (2 * _Z), 1e-9)
    if distribution == 'lognormal': mean = max(mode, 1e-9)
    return {'name': f"{spec['label']} — {target_label}", 'target_id': target_id, 'path': spec['path'], 'operation': spec.get('operation', 'multiply'), 'distribution': distribution,
            'low': low, 'mode': mode, 'high': high, 'mean': mean, 'std': std, 'physical_min': spec.get('physical_min'), 'physical_max': spec.get('physical_max'), 'bound_policy': 'clip'}
