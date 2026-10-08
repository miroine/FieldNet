"""Input consistency check: ranges, likely unit mistakes, pressure conflicts and missing links. Pure function, no UI.

``check_inputs(nodes, edges, events=None)`` returns a list of ``{'severity','element','id','field','message'}`` (severity ``error`` / ``warning`` /
``info``), worst first. Limits come from ``network.param_registry`` (the same table the schedule builder uses), so an input that cannot be set by a
schedule event is also not accepted here. Everything is checked in CANONICAL units (bar, m, m3/d, degC, fraction), the way the model stores it.
"""
from __future__ import annotations
import math
from network import param_registry as pr

# (factor that turns the stored number into the canonical one, label). Tried when a value is out of range, to name the likely unit slip.
_SLIPS = {
    'diameter': [(0.001, 'mm'), (0.0254, 'inch')],
    'length': [(0.3048, 'ft'), (1000.0, 'km')],
    'pressure': [(1 / 14.503773773, 'psi'), (0.01, 'kPa')],
    'liquid_rate': [(0.158987, 'stb/d')],
    'gas_rate': [(1000.0, 'thousand Sm³/d'), (0.0283168, 'scf/d')],
    'fraction': [(0.01, '%')],
    'pi': [(0.158987 / 0.0689476, 'stb/d/psi')],
}
_RANK = {'error': 0, 'warning': 1, 'info': 2}


def _num(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def unit_hint(prm, value):
    """Name a plausible unit slip for an out-of-range number (or None). Temperature: a °F value that is valid as °F."""
    if prm.unit == 'temperature':
        c = (value - 32.0) * 5.0 / 9.0
        if (prm.min is None or c >= prm.min) and (prm.max is None or c <= prm.max) and not (prm.min <= value <= prm.max):
            return f'{value:g} looks like °F (= {c:.1f} °C)'
        return None
    for factor, name in _SLIPS.get(prm.unit, []):
        c = value * factor
        if (prm.min is None or c >= prm.min) and (prm.max is None or c <= prm.max):
            return f'{value:g} looks like {name} (= {c:.4g} in the model unit)'
    return None


def _add(out, sev, el, field, msg):
    out.append({'severity': sev, 'element': el.get('name') or el.get('id'), 'id': el.get('id'), 'field': field, 'message': msg})


def check_value(prm, value):
    """(severity, message) for one registry parameter value, or None when fine."""
    if prm.kind == 'bool':
        return None
    if prm.kind == 'choice':
        if value not in [c for c, _ in prm.choices]:
            return ('error', f'{value!r} is not one of {[c for c, _ in prm.choices]}')
        return None
    x = _num(value)
    if x is None:
        return ('error', f'{value!r} is not a number')
    bad = (prm.min is not None and x < prm.min) or (prm.max is not None and x > prm.max)
    if bad:
        hint = unit_hint(prm, x)
        rng = f'{prm.min if prm.min is not None else "-inf"} … {prm.max if prm.max is not None else "inf"}'
        return ('error', f'{x:g} is outside the valid range ({rng}, model units)' + (f' — {hint}' if hint else ''))
    if (prm.warn_min is not None and x < prm.warn_min) or (prm.warn_max is not None and x > prm.warn_max):
        return ('warning', f'{x:g} is unusual (typical {prm.warn_min if prm.warn_min is not None else "…"} … {prm.warn_max if prm.warn_max is not None else "…"})')
    return None


def _element_values(el):
    p = el.get('params') or {}
    for k, v in p.items():
        yield k, v, 'params.' + k
    for k in ('pressure_bar', 'length_m', 'roughness_m', 'elevation_change_m'):
        if k in el:
            yield k, el[k], k


def check_inputs(nodes, edges, events=None):
    out = []
    nodes = nodes or []
    edges = edges or []
    by_id = {n['id']: n for n in nodes}
    tanks = {n['id']: n for n in nodes if n.get('kind') == 'reservoir'}
    # ---- 1. ranges / unit slips for every registry parameter
    for el, is_edge in [(n, False) for n in nodes] + [(e, True) for e in edges]:
        for key, val, path in _element_values(el):
            if val is None or val == '':
                continue
            if key in ('pressure_bar', 'length_m', 'roughness_m', 'elevation_change_m') and not path.startswith('params.') and False:
                continue
            prm = pr.lookup(key, el, is_edge)
            if prm is None or prm.top_level != (not path.startswith('params.')):
                continue
            r = check_value(prm, val)
            if r:
                _add(out, r[0], el, path, f'{prm.label}: {r[1]}')
        if is_edge:
            d = _num(el.get('diameter_m')); L = _num(el.get('length_m')); rough = _num(el.get('roughness_m'))
            if d is not None and not (0.01 <= d <= 3.0):
                _add(out, 'error', el, 'diameter_m', f'diameter {d:g} m is outside 0.01 … 3 m' + (' — looks like mm' if 10 <= d <= 3000 else (' — looks like inches' if 0.4 < d < 120 else '')))
            if d and rough and rough >= d / 2:
                _add(out, 'error', el, 'roughness_m', 'roughness is as large as the pipe radius')
            if L is not None and L <= 0 and str(el.get('kind')) == 'pipeline':
                _add(out, 'warning', el, 'length_m', 'flowline length is zero')
    # ---- 2. tanks
    for t in tanks.values():
        p = t.get('params') or {}
        ph = str(p.get('fluid_phase', 'oil')).lower()
        pi, pmin = _num(p.get('reservoir_pressure_bar')), _num(p.get('min_pressure_bar'))
        if pi is None or not (1.0 <= pi <= 1500.0):
            r = pr.Param('reservoir_pressure_bar', 'initial pressure', ('tank',), unit='pressure', min=1.0, max=1500.0)
            _add(out, 'error', t, 'params.reservoir_pressure_bar', 'initial pressure missing or outside 1 … 1500 bar' + (f" — {unit_hint(r, pi)}" if pi is not None and unit_hint(r, pi) else ''))
        elif pmin is not None and pmin >= pi:
            _add(out, 'error', t, 'params.min_pressure_bar', f'abandonment pressure {pmin:g} bar is not below the initial pressure {pi:g} bar')
        if ph == 'oil':
            if (_num(p.get('stoiip_sm3')) or 0) <= 0:
                _add(out, 'error', t, 'params.stoiip_sm3', 'oil tank without STOIIP')
            pb = _num(p.get('bubble_point_bar'))
            if pb is not None and pi is not None and pb > pi + 1e-6:
                _add(out, 'warning', t, 'params.bubble_point_bar', f'bubble point {pb:g} bar is above the initial pressure {pi:g} bar (saturated reservoir; the model caps it at the initial pressure)')
            boi = _num(p.get('boi_rm3_sm3'))
            if boi is not None and not (1.0 <= boi <= 5.0):
                _add(out, 'error', t, 'params.boi_rm3_sm3', f'Boi {boi:g} rm³/Sm³ should be 1 … 5')
            rf = _num(p.get('target_rf')); bt = _num(p.get('water_breakthrough_rf')); rfw = _num(p.get('rf_at_max_water_cut'))
            if bt is not None and rfw is not None and rfw <= bt:
                _add(out, 'warning', t, 'params.rf_at_max_water_cut', 'recovery factor at maximum water cut is not above the water-breakthrough recovery factor')
        elif (_num(p.get('giip_sm3')) or 0) <= 0:
            _add(out, 'error', t, 'params.giip_sm3', 'gas tank without GIIP')
        swi = _num(p.get('swi'))
        if swi is not None and not (0.0 <= swi < 0.9):
            _add(out, 'error', t, 'params.swi', f'connate water saturation {swi:g} must be a fraction 0 … 0.9')
    # ---- 3. wells / injectors: link, duplicated pressure, Darcy, WHP window
    for n in nodes:
        k = n.get('kind'); p = n.get('params') or {}
        if k in ('well', 'water_injector', 'gas_injector', 'injector'):
            rid = p.get('reservoir_id')
            if rid and rid not in tanks:
                _add(out, 'error', n, 'params.reservoir_id', f'points to tank {rid!r}, which does not exist')
            elif rid:
                own = _num(p.get('reservoir_pressure_bar')); tp = _num((tanks[rid].get('params') or {}).get('reservoir_pressure_bar'))
                if own is not None and tp is not None and abs(own - tp) > 1.0:
                    _add(out, 'info', n, 'params.reservoir_pressure_bar', f'stored own pressure {own:g} bar is ignored; the tank ({tp:g} bar) is the only pressure input')
            elif tanks:
                _add(out, 'warning', n, 'params.reservoir_id', 'no tank assigned: the element uses its own fixed reservoir pressure (no depletion)')
        if k == 'well':
            lo, hi = _num(p.get('min_whp_bar')), _num(p.get('max_whp_bar'))
            if lo is not None and hi is not None and lo > hi:
                _add(out, 'error', n, 'params.min_whp_bar', f'minimum WHP {lo:g} bar is above maximum WHP {hi:g} bar')
            if p.get('darcy') in (True, 'true', 'True', 1):
                try:
                    from physics.darcy_ipr import darcy_ipr
                    tp = _num((tanks.get(p.get('reservoir_id'), {}).get('params') or {}).get('reservoir_pressure_bar')) or _num(p.get('reservoir_pressure_bar')) or 200.0
                    for w in darcy_ipr(p, tp, 'gas' if str(p.get('ipr_model')) == 'Gas' else 'oil').get('warnings', []):
                        _add(out, 'warning', n, 'params.darcy', str(w))
                except Exception as exc:
                    _add(out, 'error', n, 'params.darcy', f'Darcy inflow cannot be evaluated: {exc}')
                if _num(p.get('pi_m3d_bar')) not in (None, 0.0):
                    _add(out, 'info', n, 'params.pi_m3d_bar', 'direct PI is ignored while the Darcy inflow is on')
            td = _num(p.get('tubing_id_m')); dep = _num(p.get('depth_m'))
            if td and dep and td > 0 and dep / td > 1e5:
                _add(out, 'warning', n, 'params.tubing_id_m', 'depth / tubing diameter ratio is extreme — check units')
    for e in edges:
        pass
    # ---- 4. pressure ordering on separators / boundaries / manifolds
    for n in nodes:
        p = n.get('params') or {}
        lo, hi = _num(p.get('min_pressure_bar')), _num(p.get('max_pressure_bar'))
        if n.get('kind') != 'reservoir' and lo is not None and hi is not None and lo > hi:
            _add(out, 'error', n, 'params.min_pressure_bar', f'minimum pressure {lo:g} bar is above maximum pressure {hi:g} bar')
    # ---- 5. schedule events
    for ev in events or []:
        ev = ev if isinstance(ev, dict) else getattr(ev, '__dict__', {})
        tid, field, val = ev.get('target_id'), str(ev.get('field') or ''), ev.get('value')
        el = by_id.get(tid) or next((e for e in edges if e.get('id') == tid), None)
        if el is None:
            out.append({'severity': 'error', 'element': str(tid), 'id': tid, 'field': field, 'message': f'event on {ev.get("date")} targets an element that does not exist'}); continue
        key = field.split('.', 1)[1] if field.startswith('params.') else field
        prm = pr.lookup(key, el, el not in nodes)
        if prm is None or prm.top_level != (not field.startswith('params.')):
            continue
        r = check_value(prm, val)
        if r:
            _add(out, r[0], el, field, f'event {ev.get("date")}: {prm.label}: {r[1]}')
    out.sort(key=lambda d: (_RANK[d['severity']], str(d['element'])))
    return out


def summarize(findings):
    c = {'error': 0, 'warning': 0, 'info': 0}
    for f in findings:
        c[f['severity']] += 1
    return c
