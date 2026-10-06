"""Pump / compressor performance-curve import and operating-point checks (screening level).

Curves are parsed from CSV text (header row required, ``#`` comment lines allowed) into piecewise-linear curve objects
(same interpolation as ``physics.performance_maps``; ``points()`` returns the dict form that ``pump_map`` /
``compressor_map`` accept). Column names are matched case-insensitively with units in parentheses ignored:

pump        rate (rate_m3d, flow, q) [m3/d] | head (head_bar, dp_bar) [bar] or head_m [m, converted with ``rho_kgm3``] |
            efficiency (eff, eta) [fraction, or percent if > 1.5] | power (power_kw, kw) [kW, optional]
compressor  flow (inlet_flow, flow_sm3d, q) [Sm3/d, treated as the inlet flow expressed in Sm3/d] | head_bar (head, dp_bar) [bar pressure
            rise at the curve's reference suction pressure] and/or pressure_ratio (ratio, pr) | efficiency |
            speed (speed_rpm, rpm) [optional: several speeds -> several speed lines] | power (power_kw) [optional]

Honest limits: the minimum / maximum tabulated flow of a curve is used as the surge-or-minimum-flow and
runout limits - this is the curve's *tabulated range*, not a certified surge line. BEP is the tabulated/interpolated
maximum-efficiency flow. The preferred operating range (default 70-120 % of BEP for pumps, a commonly quoted rule of thumb) is advisory.
Off-speed compressor operation uses simple affinity scaling (flow ~ N, head ~ N^2) from the nearest tabulated speed line;
no gas-property, density or polytropic correction is made. ``check_network_equipment`` expects the solver's convention
that compressor gas flow = |liquid-equivalent rate| x ``gor_sm3sm3`` (override with ``params['gas_rate_sm3d']``).
"""
from __future__ import annotations
import csv
import io
import math
import re

import numpy as np

STATUS_OK, STATUS_WARN, STATUS_VIOL = 'OK', 'WARNING', 'VIOLATED'
POR_LOW_PCT, POR_HIGH_PCT = 70.0, 120.0     # advisory preferred operating range, % of BEP
HEAD_DEVIATION_TOL_PCT = 10.0

_ALIASES = {
    'rate': ('rate', 'rate_m3d', 'flow', 'flow_m3d', 'q', 'q_m3d', 'flow_rate', 'inlet_flow', 'inlet_flow_sm3d', 'flow_sm3d', 'q_sm3d', 'rate_sm3d'),
    'head': ('head', 'head_bar', 'dp', 'dp_bar', 'pressure_rise', 'pressure_rise_bar'),
    'head_m': ('head_m',),
    'ratio': ('pressure_ratio', 'ratio', 'pr'),
    'eff': ('efficiency', 'eff', 'eta', 'efficiency_pct', 'eff_pct'),
    'power': ('power', 'power_kw', 'kw'),
    'speed': ('speed', 'speed_rpm', 'rpm', 'speed_pct'),
}


def _norm(h):
    h = re.sub(r'\([^)]*\)', '', str(h)).strip().lower()
    return re.sub(r'[^a-z0-9]+', '_', h).strip('_')


def _read_rows(text):
    lines = [ln for ln in io.StringIO(str(text)).read().splitlines() if ln.strip() and not ln.strip().startswith('#')]
    if len(lines) < 3: raise ValueError('Curve CSV needs a header and at least two data rows')
    sample = lines[0]
    delim = ';' if sample.count(';') > sample.count(',') and sample.count(';') > sample.count('\t') else ('\t' if sample.count('\t') > sample.count(',') else ',')
    rdr = list(csv.reader(lines, delimiter=delim))
    header = [_norm(h) for h in rdr[0]]
    return header, rdr[1:]


def _col(header, key):
    for a in _ALIASES[key]:
        if a in header: return header.index(a)
    return None


def _num(s, what):
    try: v = float(str(s).strip())
    except ValueError: raise ValueError(f'Non-numeric value {s!r} in column {what}')
    if not math.isfinite(v): raise ValueError(f'Non-finite value in column {what}')
    return v


class _Line:
    """One speed line: arrays sorted by flow."""

    def __init__(self, q, head=None, ratio=None, eff=None, power=None, speed=None):
        q = np.asarray(q, float); order = np.argsort(q); self.q = q[order]
        if len(self.q) < 2 or len(set(self.q.tolist())) != len(self.q): raise ValueError('Curve needs >= 2 points with unique flows')
        if np.any(self.q < 0): raise ValueError('Flows must be >= 0')
        g = lambda a: None if a is None else np.asarray(a, float)[order]
        self.head, self.ratio, self.eff, self.power = g(head), g(ratio), g(eff), g(power); self.speed = speed
        if self.eff is not None and (np.any(self.eff <= 0) or np.any(self.eff > 1.0)): raise ValueError('Efficiency must be in (0,1] (or percent)')

    def _i(self, arr, q): return None if arr is None else float(np.interp(q, self.q, arr))

    def scaled(self, speed):
        """Affinity-scaled copy (flow ~ N, head ~ N^2, power ~ N^3); efficiency unchanged."""
        r = float(speed) / float(self.speed)
        return _Line(self.q * r, None if self.head is None else self.head * r * r, None if self.ratio is None else 1.0 + (self.ratio - 1.0) * r * r,
                     self.eff, None if self.power is None else self.power * r ** 3, speed)


class EquipmentCurve:
    """Pump or compressor curve (``kind`` 'pump' | 'compressor') with interpolation and BEP / range helpers."""

    def __init__(self, kind, lines, name='', flow_unit='m3/d'):
        self.kind, self.name, self.flow_unit = kind, name, flow_unit
        self.lines = sorted(lines, key=lambda l: (l.speed is None, l.speed or 0.0))
        self.speeds = [l.speed for l in self.lines if l.speed is not None]

    # ---- line selection
    def line(self, speed=None):
        if len(self.lines) == 1 and (speed is None or self.lines[0].speed is None or abs(float(speed) - self.lines[0].speed) < 1e-9): return self.lines[0]
        if speed is None: return self.lines[-1]           # highest tabulated speed = design line
        near = min(self.lines, key=lambda l: abs(l.speed - float(speed)))
        return near if abs(near.speed - float(speed)) < 1e-9 else near.scaled(speed)

    # ---- interpolation (clamped outside the tabulated range; use in_range())
    def head_at(self, q, speed=None): return self.line(speed)._i(self.line(speed).head, q)
    def ratio_at(self, q, speed=None): return self.line(speed)._i(self.line(speed).ratio, q)
    def efficiency_at(self, q, speed=None): return self.line(speed)._i(self.line(speed).eff, q)
    def power_at(self, q, speed=None): return self.line(speed)._i(self.line(speed).power, q)
    def q_min(self, speed=None): return float(self.line(speed).q[0])
    def q_max(self, speed=None): return float(self.line(speed).q[-1])
    def in_range(self, q, speed=None): return self.q_min(speed) - 1e-9 <= q <= self.q_max(speed) + 1e-9

    def bep(self, speed=None):
        """(flow, efficiency) of the highest interpolated efficiency on a fine grid; None without efficiency data."""
        ln = self.line(speed)
        if ln.eff is None: return None
        g = np.linspace(ln.q[0], ln.q[-1], 801); e = np.interp(g, ln.q, ln.eff); i = int(np.argmax(e))
        return float(g[i]), float(e[i])

    def points(self, speed=None):
        """List of dicts compatible with physics.performance_maps (pump_map / compressor_map)."""
        ln = self.line(speed); key = 'rate_m3d' if self.kind == 'pump' else 'rate_sm3d'; out = []
        for i, q in enumerate(ln.q):
            p = {key: float(q)}
            if ln.head is not None: p['head_bar'] = float(ln.head[i])
            if ln.ratio is not None: p['pressure_ratio'] = float(ln.ratio[i])
            if ln.eff is not None: p['efficiency'] = float(ln.eff[i])
            out.append(p)
        return out


def _eff(vals):
    v = np.asarray(vals, float)
    return v / 100.0 if np.nanmax(v) > 1.5 else v


def parse_pump_curve_csv(text, name='', rho_kgm3=850.0):
    """Parse pump curve CSV (see module docstring) -> EquipmentCurve('pump')."""
    header, rows = _read_rows(text)
    iq, ih, ihm, ie, ip = _col(header, 'rate'), _col(header, 'head'), _col(header, 'head_m'), _col(header, 'eff'), _col(header, 'power')
    if iq is None or (ih is None and ihm is None): raise ValueError(f'Pump curve CSV needs rate and head columns; found {header}')
    q = [_num(r[iq], 'rate') for r in rows]
    head = [_num(r[ih], 'head') for r in rows] if ih is not None else [_num(r[ihm], 'head_m') * float(rho_kgm3) * 9.80665 / 1e5 for r in rows]
    eff = _eff([_num(r[ie], 'efficiency') for r in rows]) if ie is not None else None
    pw = [_num(r[ip], 'power') for r in rows] if ip is not None else None
    return EquipmentCurve('pump', [_Line(q, head=head, eff=eff, power=pw)], name, 'm3/d')


def parse_compressor_curve_csv(text, name=''):
    """Parse compressor curve CSV (optionally several speeds) -> EquipmentCurve('compressor')."""
    header, rows = _read_rows(text)
    iq, ih, ir, ie, ip, isp = (_col(header, k) for k in ('rate', 'head', 'ratio', 'eff', 'power', 'speed'))
    if iq is None or (ih is None and ir is None): raise ValueError(f'Compressor curve CSV needs flow and head or pressure_ratio columns; found {header}')
    groups = {}
    for r in rows: groups.setdefault(_num(r[isp], 'speed') if isp is not None else None, []).append(r)
    lines = []
    for sp, rs in groups.items():
        g = lambda i, w: None if i is None else [_num(r[i], w) for r in rs]
        lines.append(_Line(g(iq, 'flow'), head=g(ih, 'head'), ratio=g(ir, 'pressure_ratio'), eff=None if ie is None else _eff(g(ie, 'efficiency')), power=g(ip, 'power'), speed=sp))
    return EquipmentCurve('compressor', lines, name, 'Sm3/d')


def parse_curve_csv(text, kind, name='', **kw):
    if kind == 'pump': return parse_pump_curve_csv(text, name, **kw)
    if kind == 'compressor': return parse_compressor_curve_csv(text, name)
    raise ValueError(f'Unknown equipment kind {kind!r}')


def check_operating_point(curve, rate, *, speed=None, p_suction_bar=None, p_discharge_bar=None, dp_bar=None,
                          por_low_pct=POR_LOW_PCT, por_high_pct=POR_HIGH_PCT):
    """Compare an operating flow with the curve (flow in the curve's unit: m3/d for pumps, Sm3/d for compressors).

    Returns dict: ``rate``, ``q_min``, ``q_max``, ``q_bep``, ``pct_of_bep`` (None without efficiency data),
    ``margin_to_min_pct`` = (q - q_min)/q_min*100 (surge / minimum-flow margin), ``margin_to_max_pct`` = (q_max - q)/q_max*100
    (runout margin), ``curve_head_bar`` / ``curve_pressure_ratio``, ``efficiency``, ``power_kw`` (curve power or None),
    ``head_deviation_pct`` (solved dP or pressure ratio vs curve, when supplied), ``status`` and ``messages``.
    Status: VIOLATED if outside [q_min, q_max]; WARNING if outside the advisory 70-120 % BEP range, within 10 % of a
    flow limit or the solved head deviates > 10 % from the curve; else OK.
    """
    q = abs(float(rate)); qmin, qmax = curve.q_min(speed), curve.q_max(speed); msgs = []
    bep = curve.bep(speed); pct = None if bep is None else 100.0 * q / max(bep[0], 1e-12)
    m_min = 100.0 * (q - qmin) / max(qmin, 1e-12) if qmin > 0 else math.inf; m_max = 100.0 * (qmax - q) / max(qmax, 1e-12)
    status = STATUS_OK
    if q < qmin - 1e-9: status = STATUS_VIOL; msgs.append(f'flow {q:.4g} below curve minimum {qmin:.4g} (surge / minimum-flow side)')
    elif q > qmax + 1e-9: status = STATUS_VIOL; msgs.append(f'flow {q:.4g} above curve maximum {qmax:.4g} (runout side)')
    else:
        if m_min < 10.0: status = STATUS_WARN; msgs.append(f'within {m_min:.1f} % of minimum flow')
        if m_max < 10.0: status = STATUS_WARN; msgs.append(f'within {m_max:.1f} % of maximum flow')
    if pct is not None and status != STATUS_VIOL and not (por_low_pct <= pct <= por_high_pct):
        status = STATUS_WARN; msgs.append(f'{pct:.0f} % of BEP is outside the advisory {por_low_pct:.0f}-{por_high_pct:.0f} % range')
    head = curve.head_at(q, speed) if curve.line(speed).head is not None else None
    ratio = curve.ratio_at(q, speed) if curve.line(speed).ratio is not None else None
    dev = None
    if curve.kind == 'pump' and dp_bar is not None and head:
        dev = 100.0 * (float(dp_bar) - head) / max(abs(head), 1e-9)
    elif curve.kind == 'compressor' and p_suction_bar and p_discharge_bar:
        if ratio is not None: dev = 100.0 * (float(p_discharge_bar) / float(p_suction_bar) - ratio) / ratio
        elif head: dev = 100.0 * ((float(p_discharge_bar) - float(p_suction_bar)) - head) / max(abs(head), 1e-9)
    if dev is not None and abs(dev) > HEAD_DEVIATION_TOL_PCT and status == STATUS_OK:
        status = STATUS_WARN; msgs.append(f'solved head differs from the curve by {dev:+.1f} %')
    elif dev is not None and abs(dev) > HEAD_DEVIATION_TOL_PCT: msgs.append(f'solved head differs from the curve by {dev:+.1f} %')
    return {'rate': q, 'q_min': qmin, 'q_max': qmax, 'q_bep': None if bep is None else bep[0], 'pct_of_bep': pct, 'margin_to_min_pct': m_min,
            'margin_to_max_pct': m_max, 'curve_head_bar': head, 'curve_pressure_ratio': ratio,
            'efficiency': curve.efficiency_at(q, speed) if curve.line(speed).eff is not None else None,
            'power_kw': curve.power_at(q, speed) if curve.line(speed).power is not None else None, 'head_deviation_pct': dev,
            'in_range': qmin - 1e-9 <= q <= qmax + 1e-9, 'status': status, 'messages': msgs}


def _unpack(res):
    if isinstance(res, dict): return res.get('pressures') or {}, res.get('flows') or {}, res.get('info') or {}, res.get('details') or {}
    p, q, info, d = res; return p or {}, q or {}, info or {}, d or {}


def check_network_equipment(nodes, edges, solve_result):
    """Check every inline pump / compressor node that stores ``params['curve_csv']`` against its solved flow.

    ``solve_result`` is the ``solve_v21`` tuple ``(pressures, flows, info, details)`` or a dict with those keys.
    Returns rows ``{Component, ComponentId, Constraint, Value, Limit, Relation, Margin, Unit, Status}`` as in
    ``solver.constraints.evaluate_constraints`` (Status 'OK' / 'VIOLATED'), plus ``Severity`` ('hard' for flow-range limits and the
    node's ``max_power_kw``, 'advisory' for BEP-range and curve-head deviation) and ``Note``. Nodes without a (parsable) curve are
    skipped, unparsable ones yield a ``Curve parse`` row with Status 'VIOLATED' and Severity 'advisory'.
    """
    pr, fl, info, _ = _unpack(solve_result); inline = info.get('inline_equipment') or {}; rows = []

    def add(n, constraint, value, limit, rel, unit, sev='hard', note=''):
        if value is None or limit is None: return
        margin = (limit - value) if rel == '<=' else (value - limit)
        rows.append({'Component': n.get('name', n['id']), 'ComponentId': n['id'], 'Constraint': constraint, 'Value': float(value), 'Limit': float(limit), 'Relation': rel,
                     'Margin': float(margin), 'Unit': unit, 'Status': 'OK' if margin >= -1e-9 else 'VIOLATED', 'Severity': sev, 'Note': note})

    for n in nodes:
        if n.get('kind') not in ('pump', 'compressor'): continue
        prm = n.get('params') or {}; text = prm.get('curve_csv')
        if not text or not str(text).strip(): continue
        nid = n['id']; row = inline.get(nid) or {}
        q = row.get('rate_m3d', fl.get(nid))
        if q is None: continue
        try: curve = parse_curve_csv(text, n['kind'], n.get('name', nid), rho_kgm3=float(prm.get('rho_kgm3', 850.0))) if n['kind'] == 'pump' else parse_curve_csv(text, 'compressor', n.get('name', nid))
        except ValueError as ex:
            rows.append({'Component': n.get('name', nid), 'ComponentId': nid, 'Constraint': 'Curve parse', 'Value': 0.0, 'Limit': 0.0, 'Relation': '>=', 'Margin': -1.0,
                         'Unit': '-', 'Status': 'VIOLATED', 'Severity': 'advisory', 'Note': str(ex)}); continue
        pin, pout = row.get('p_in_bar', pr.get(nid)), row.get('p_out_bar')
        if n['kind'] == 'pump':
            flow, unit = abs(float(q)), 'm3/d'; dp = (pin - pout) if (pin is not None and pout is not None) else None
            # the solver reports p_in - p_out; for a pump this is negative (pressure rise), so use the magnitude
            res = check_operating_point(curve, flow, dp_bar=None if dp is None else abs(dp))
        else:
            gas = prm.get('gas_rate_sm3d'); flow = abs(float(gas)) if gas is not None else abs(float(q)) * float(prm.get('gor_sm3sm3', 100.0)); unit = 'Sm3/d'
            speed = prm.get('speed_rpm')
            res = check_operating_point(curve, flow, speed=None if speed is None else float(speed), p_suction_bar=pin, p_discharge_bar=pout)
        note = '; '.join(res['messages'])
        add(n, 'Minimum flow (surge)' if n['kind'] == 'compressor' else 'Minimum flow', res['rate'], res['q_min'], '>=', unit, 'hard', note)
        add(n, 'Maximum flow (runout)', res['rate'], res['q_max'], '<=', unit, 'hard', note)
        if res['pct_of_bep'] is not None:
            add(n, 'Minimum % of BEP', res['pct_of_bep'], POR_LOW_PCT, '>=', '%', 'advisory', note)
            add(n, 'Maximum % of BEP', res['pct_of_bep'], POR_HIGH_PCT, '<=', '%', 'advisory', note)
        if res['head_deviation_pct'] is not None: add(n, 'Head deviation from curve', abs(res['head_deviation_pct']), HEAD_DEVIATION_TOL_PCT, '<=', '%', 'advisory', note)
        lim_pw = prm.get('max_power_kw')
        if lim_pw is not None and res['power_kw'] is not None: add(n, 'Maximum power', res['power_kw'], float(lim_pw), '<=', 'kW', 'hard', 'power from curve')
    return rows
