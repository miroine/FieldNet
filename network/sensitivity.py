"""One-at-a-time sensitivity (tornado) and two-parameter operating envelopes on a FieldNet graph.

SCREENING-LEVEL: each case is a full steady-state network solve (``solver.v21.solve_v21``) of a deep copy of the graph
with the parameter(s) changed; nothing is re-calibrated and depletion/time is not involved. Tornado = one-at-a-time
(interactions are ignored; the envelope grid shows two-way interaction for the chosen pair). Cases that fail to
converge are flagged (``converged`` False) and their metric is kept, so inspect the flag before trusting a row.

Parameter paths: ``"<node/edge id>.<dotted field>"`` (e.g. ``"P1.params.pi_m3d_bar"``, ``"SEP.pressure_bar"``,
``"TRUNK.diameter_m"``) or a group selector ``"kind:well.params.pi_m3d_bar"`` / ``"kind:pipeline|flowline.diameter_m"``
(node kinds and edge kinds). ``mode='abs'`` sets the value; ``mode='mult'`` multiplies each target's own current value
(the only sensible mode for groups); ``mode='add'`` adds an increment. Targets that lack the field are skipped.
``workers>1`` evaluates cases with ``network.uncertainty.parallel_map`` (process pool, serial fallback).
"""
from __future__ import annotations
import copy
from dataclasses import dataclass


@dataclass
class SensParam:
    label: str
    path: object          # "id.dotted.field" | "kind:a|b.dotted.field" | (getter(nodes, edges)->float, setter(nodes, edges, value))
    low: float = 0.0
    high: float = 0.0
    mode: str = 'abs'     # 'abs' | 'mult'


def _as_param(p):
    if isinstance(p, SensParam): return p
    p = tuple(p)
    if len(p) < 2: raise ValueError('parameter needs at least (label, path)')
    if len(p) == 3 and isinstance(p[2], str): return SensParam(p[0], p[1], 0.0, 0.0, p[2])   # (label, path, mode) form for envelopes
    lo = p[2] if len(p) > 2 else 0.0; hi = p[3] if len(p) > 3 else 0.0; mode = p[4] if len(p) > 4 else 'abs'
    return SensParam(p[0], p[1], lo, hi, mode)


# --- target resolution -------------------------------------------------------------------------
def _split_path(path):
    sel, _, field = str(path).partition('.')
    return sel, field


def _targets(nodes, edges, sel):
    if sel.startswith('kind:'):
        kinds = set(sel[5:].split('|')); return [o for o in list(nodes) + list(edges) if o.get('kind') in kinds]
    return [o for o in list(nodes) + list(edges) if o.get('id') == sel]


def _get(obj, field):
    cur = obj
    for k in field.split('.'):
        if not isinstance(cur, dict) or k not in cur or cur[k] is None: return None
        cur = cur[k]
    return cur


def _set(obj, field, value):
    ks = field.split('.'); cur = obj
    for k in ks[:-1]: cur = cur.setdefault(k, {})
    cur[ks[-1]] = value


def apply_value(nodes, edges, param, value):
    """Set ``param`` to ``value`` (abs) or scale by ``value`` (mult) in-place on (nodes, edges); returns number of targets changed."""
    param = _as_param(param)
    if isinstance(param.path, (tuple, list)) and callable(param.path[1]):
        param.path[1](nodes, edges, value); return 1
    sel, field = _split_path(param.path); n = 0
    for o in _targets(nodes, edges, sel):
        cur = _get(o, field)
        if param.mode in ('mult', 'add'):
            if not isinstance(cur, (int, float)): continue
            _set(o, field, float(cur) * float(value) if param.mode == 'mult' else float(cur) + float(value)); n += 1
        else:
            if cur is None and not isinstance(_get(o, field.rsplit('.', 1)[0]) if '.' in field else o, dict): continue
            _set(o, field, float(value)); n += 1
    return n


def base_value(nodes, edges, param):
    param = _as_param(param)
    if isinstance(param.path, (tuple, list)) and callable(param.path[0]): return param.path[0](nodes, edges)
    sel, field = _split_path(param.path); vals = [_get(o, field) for o in _targets(nodes, edges, sel)]
    vals = [float(v) for v in vals if isinstance(v, (int, float))]
    return (sum(vals) / len(vals)) if vals else None


# --- metrics & solving -------------------------------------------------------------------------
def total_oil_rate(result):
    """Default metric: total oil rate [m3/d] over all wells in a solve result (p, q, info, details)."""
    d = result[3] or {}
    return float(sum(max(float(v.get('oil_rate_m3d', 0.0) or 0.0), 0.0) for v in d.values() if isinstance(v, dict) and 'oil_rate_m3d' in v))


def _solve(nodes, edges, enforce):
    from solver.v21 import solve_v21
    return solve_v21(nodes, edges, enforce_constraints=enforce)


def _case(args):
    nodes, edges, changes, metric_fn, enforce = args
    ns = copy.deepcopy(nodes); es = copy.deepcopy(edges)
    for prm, val in changes: apply_value(ns, es, prm, val)
    try:
        r = _solve(ns, es, enforce)
    except Exception as exc:   # a failing case must not kill the study
        return {'metric': float('nan'), 'converged': False, 'active': None, 'error': str(exc)}
    info = r[2] or {}
    act = None
    try:
        from solver.constraints import active_constraints
        rows = active_constraints(info.get('constraints', []))
        if rows: act = rows[0]
    except Exception: pass
    return {'metric': float(metric_fn(r)), 'converged': bool(info.get('success')) and info.get('quality_gate', 'PASS') == 'PASS', 'active': act}


def _run(cases, nodes, edges, metric_fn, enforce, workers, progress=None):
    """Run all cases. ``progress(done, total)`` is called after each case (serial runs only; parallel runs report once at the end)."""
    from network.uncertainty import parallel_map
    if progress is not None and int(workers or 1) <= 1:
        out = []
        for i, ch in enumerate(cases):
            out.append(_case((nodes, edges, ch, metric_fn, enforce))); progress(i + 1, len(cases))
        return out
    res = _run_parallel(cases, nodes, edges, metric_fn, enforce, workers)
    if progress is not None: progress(len(cases), len(cases))
    return res


def _run_parallel(cases, nodes, edges, metric_fn, enforce, workers):
    from network.uncertainty import parallel_map
    return parallel_map(_case, [(nodes, edges, ch, metric_fn, enforce) for ch in cases], workers)


# --- default parameter set -----------------------------------------------------------------------
def default_parameters(nodes, edges, rel=0.2):
    """Parameter set discoverable from the graph (only items present are returned).

    Reservoir pressure (+-``rel`` of tank/well value), PI, skin (+-1 absolute, wells that define it), water cut
    (x0.5 / x1.5, capped), GOR, separator pressure, flowline diameter (+-10 %) and roughness (x0.5 / x2), and every
    ``max_*`` capacity limit on separators (+-``rel``). Ranges are generic placeholders - replace with your own
    uncertainty ranges for real work.
    """
    out = []; lo, hi = 1 - rel, 1 + rel
    def any_(kind_sel, field):
        return any(isinstance(_get(o, field), (int, float)) for o in _targets(nodes, edges, kind_sel))
    if any_('kind:reservoir', 'params.reservoir_pressure_bar'): out.append(SensParam('Reservoir pressure', 'kind:reservoir.params.reservoir_pressure_bar', lo, hi, 'mult'))
    elif any_('kind:well', 'params.reservoir_pressure_bar'): out.append(SensParam('Reservoir pressure', 'kind:well.params.reservoir_pressure_bar', lo, hi, 'mult'))
    if any_('kind:well', 'params.pi_m3d_bar'): out.append(SensParam('Productivity index', 'kind:well.params.pi_m3d_bar', lo, hi, 'mult'))
    if any_('kind:well', 'params.skin'): out.append(SensParam('Skin (+-1)', 'kind:well.params.skin', -1.0, 1.0, 'add'))
    if any_('kind:well', 'params.water_cut'): out.append(SensParam('Water cut', 'kind:well.params.water_cut', 0.5, 1.5, 'mult'))
    if any_('kind:well', 'params.gor_sm3sm3'): out.append(SensParam('GOR', 'kind:well.params.gor_sm3sm3', 0.7, 1.3, 'mult'))
    if any_('kind:separator', 'pressure_bar'): out.append(SensParam('Separator pressure', 'kind:separator.pressure_bar', lo, hi, 'mult'))
    pk = sorted({e.get('kind') for e in edges if e.get('kind') in ('pipeline', 'flowline', 'riser')})
    if pk:
        sel = 'kind:' + '|'.join(pk)
        if any_(sel, 'diameter_m'): out.append(SensParam('Flowline diameter', sel + '.diameter_m', 0.9, 1.1, 'mult'))
        if any_(sel, 'roughness_m'): out.append(SensParam('Flowline roughness', sel + '.roughness_m', 0.5, 2.0, 'mult'))
    for key in sorted({k for n in nodes if n.get('kind') == 'separator' for k in (n.get('params') or {}) if str(k).startswith('max_')}):
        out.append(SensParam(f'Separator {key}', f'kind:separator.params.{key}', lo, hi, 'mult'))
    return out


# --- tornado -------------------------------------------------------------------------------------
def tornado(nodes, edges, parameters=None, metric_fn=total_oil_rate, enforce_constraints=True, workers=1, progress=None):
    """One-at-a-time low/high study. ``parameters``: list of ``SensParam`` or ``(label, path, low, high[, mode])`` tuples
    (default: ``default_parameters``). ``metric_fn(result_tuple)`` must be a top-level function when ``workers>1``.

    Returns rows sorted by swing (largest first): ``{'parameter','base_value','low','high','metric_base','metric_low',
    'metric_high','swing','delta_low','delta_high','converged'}``. ``low``/``high`` are the inputs (multipliers for
    'mult', increments for 'add', absolute values for 'abs'); ``base_value`` is the mean current value of the targets.
    """
    params = [_as_param(p) for p in (parameters if parameters is not None else default_parameters(nodes, edges))]
    cases = [[]] + [[(p, p.low)] for p in params] + [[(p, p.high)] for p in params]
    res = _run(cases, nodes, edges, metric_fn, enforce_constraints, workers, progress)
    base = res[0]; n = len(params); rows = []
    for i, p in enumerate(params):
        a, b = res[1 + i], res[1 + n + i]
        rows.append({'parameter': p.label, 'base_value': base_value(nodes, edges, p), 'mode': p.mode, 'low': p.low, 'high': p.high,
                     'metric_base': base['metric'], 'metric_low': a['metric'], 'metric_high': b['metric'],
                     'delta_low': a['metric'] - base['metric'], 'delta_high': b['metric'] - base['metric'],
                     'swing': abs(b['metric'] - a['metric']), 'converged': bool(base['converged'] and a['converged'] and b['converged'])})
    rows.sort(key=lambda r: -(r['swing'] if r['swing'] == r['swing'] else -1))
    return rows


# --- envelope ------------------------------------------------------------------------------------
def _label(c):
    return '' if not c else f"{c.get('Component', '')}: {c.get('Constraint', '')}"


def envelope(nodes, edges, x_param, y_param, grid, metric_fn=total_oil_rate, enforce_constraints=True, workers=1, progress=None):
    """Two-parameter operating envelope. ``x_param``/``y_param``: ``SensParam`` / ``(label, path[, mode])`` (low/high unused);
    ``grid = (xs, ys)`` or ``{'x': xs, 'y': ys}`` with values in each parameter's own mode (absolute, multiplier or increment).

    Returns ``{'x_label','y_label','x','y','metric'[iy][ix],'active'[iy][ix] (label of the most limiting active/violated
    constraint from ``solver.constraints.active_constraints``, '' if none),'margin_fraction'[iy][ix],'converged'[iy][ix],
    'constraints': sorted distinct labels, 'active_index'[iy][ix] (index into 'constraints', -1 none)}``.
    Note: with enforce_constraints=True choked wells sit exactly at the limit, so the limiting constraint is reported
    as active (tolerance 5 % of limit).
    """
    xp, yp = _as_param(x_param), _as_param(y_param)
    xs, ys = (grid['x'], grid['y']) if isinstance(grid, dict) else grid
    xs = [float(v) for v in xs]; ys = [float(v) for v in ys]
    cases = [[(xp, x), (yp, y)] for y in ys for x in xs]
    res = _run(cases, nodes, edges, metric_fn, enforce_constraints, workers, progress)
    nx = len(xs); M = []; A = []; F = []; C = []
    for iy in range(len(ys)):
        row = res[iy * nx:(iy + 1) * nx]
        M.append([r['metric'] for r in row]); A.append([_label(r['active']) for r in row])
        F.append([(r['active'] or {}).get('MarginFraction') for r in row]); C.append([r['converged'] for r in row])
    names = sorted({a for r in A for a in r if a})
    idx = [[names.index(a) if a else -1 for a in r] for r in A]
    return {'x_label': xp.label, 'y_label': yp.label, 'x': xs, 'y': ys, 'metric': M, 'active': A, 'margin_fraction': F,
            'converged': C, 'constraints': names, 'active_index': idx}
