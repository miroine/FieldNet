"""Forecast recovery & rate assumptions.

Stored as node params so they travel with the case:
  tank   target_rf            fraction (0-1) of the primary-phase in-place volume (oil -> STOIIP, gas / condensate -> GIIP)
         rf_taper_days        e-folding time of the approach to the recoverable volume (default 365 d)
  well   eur_cap              primary-phase EUR cap [Sm3] (oil wells: oil; gas wells: gas)
         calibrate_rate       primary-phase rate the productivity should reproduce at start-up [m3/d | Sm3/d]
         productivity_multiplier  result of the calibration (applied to PI / Vogel qmax / gas C in physics.well_model)
         max_oil_rate_m3d, max_gas_rate_sm3d, ...  existing hard well limits

Behaviour is unchanged when none of these are set.

Formulation. Recoverable volume R = target_rf * in-place. Remaining R_rem = R - cumulative (Np or Gp). The tank may not be
produced faster than R_rem / tau (tau = taper days): the offtake tapers exponentially towards R, so the target is approached
smoothly instead of being hit by a cliff, and it is never overshot. Wells of the tank share the cap pro rata to their
network-solved rates (the network is re-solved with the resulting well caps so back-pressure effects are honoured).
When R_rem <= 0 the tank's wells are shut in. A per-well EUR cap works the same way on the well's own cumulative.
"""
from __future__ import annotations
import copy, math

DEFAULT_TAPER_DAYS = 365.0


def _f(v, d=None):
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except (TypeError, ValueError):
        return d


def target_rf_of(params):
    """Tank target RF as a fraction, or None. Values > 1 are read as percent."""
    v = _f((params or {}).get('target_rf'))
    if v is None or v <= 0: return None
    return min(v / 100.0 if v > 1.0 else v, 1.0)


def taper_days_of(params):
    return max(_f((params or {}).get('rf_taper_days'), DEFAULT_TAPER_DAYS) or DEFAULT_TAPER_DAYS, 1.0)


def has_assumptions(nodes):
    for n in nodes:
        p = n.get('params') or {}
        if n.get('kind') == 'reservoir' and target_rf_of(p) is not None: return True
        if n.get('kind') == 'well' and (_f(p.get('eur_cap'), 0) or 0) > 0: return True
    return False


def primary_phase(tk):
    return 'oil' if tk.phase == 'oil' else 'gas'


def tank_primary(tk):
    """(in-place, cumulative) of the tank's primary phase."""
    return (tk.n, tk.np) if tk.phase == 'oil' else (tk.g, tk.gp)


def _prim_factor(prm, phase):
    wc = min(max(_f(prm.get('water_cut'), 0.0), 0.0), 0.9999); gor = max(_f(prm.get('gor_sm3sm3'), 0.0), 0.0)
    f = (1.0 - wc) * (gor if phase == 'gas' else 1.0)
    return max(f, 1e-9)


def compute_caps(nn, details, tanks, state, extra_taper=None):
    """Liquid-rate caps [m3/d] per well from the tank target RF and the per-well EUR cap, given a solved network.

    Returns ``(caps, shut, notes)``; ``caps`` only has wells that must be limited, ``shut`` are wells to close."""
    caps = {}; shut = set(); notes = []
    by_tank = {}
    for n in nn:
        if n.get('kind') != 'well' or n['id'] not in details: continue
        prm = n.get('params') or {}; rid = prm.get('reservoir_id')
        liq = max(_f(details[n['id']].get('liquid_rate_m3d'), 0.0) or 0.0, 0.0)
        ph = primary_phase(tanks[rid]) if rid in tanks else ('gas' if str(prm.get('ipr_model', '')).lower().startswith('gas') else 'oil')
        f = _prim_factor(prm, ph); tau = taper_days_of(tanks[rid].params) if rid in tanks and hasattr(tanks[rid], 'params') else DEFAULT_TAPER_DAYS
        if rid in tanks and getattr(tanks[rid], 'taper_days', None): tau = tanks[rid].taper_days
        eur = _f(prm.get('eur_cap'), 0.0) or 0.0
        if eur > 0 and n['id'] in state:
            cum = state[n['id']]['cum_gas'] if ph == 'gas' else state[n['id']]['cum_oil']
            rem = eur - cum
            if rem <= 0: shut.add(n['id']); notes.append((n['id'], 'EUR cap reached'))
            elif liq > 0 and liq * f > rem / tau: caps[n['id']] = rem / tau / f; notes.append((n['id'], 'EUR taper'))
        if rid in tanks: by_tank.setdefault(rid, []).append((n['id'], liq, f))
    for rid, lst in by_tank.items():
        tk = tanks[rid]; trf = getattr(tk, 'target_rf', None)
        if not trf: continue
        inpl, cum = tank_primary(tk); rem = trf * inpl - cum
        tau = getattr(tk, 'taper_days', DEFAULT_TAPER_DAYS)
        if rem <= 0:
            for wid, _, _ in lst: shut.add(wid)
            notes.append((rid, 'target RF reached')); continue
        # tank offtake after the per-well caps already decided above
        q = sum(min(liq, caps.get(wid, math.inf)) * f for wid, liq, f in lst)
        qcap = rem / tau
        if q > qcap > 0:
            s = qcap / q
            for wid, liq, f in lst:
                if wid in shut or liq <= 0: continue
                caps[wid] = min(caps.get(wid, math.inf), min(liq, caps.get(wid, math.inf)) * s)
            notes.append((rid, 'RF taper'))
    return caps, shut, notes


def apply_caps(nn, caps, shut):
    out = copy.deepcopy(nn)
    for n in out:
        if n.get('kind') != 'well': continue
        p = n.setdefault('params', {})
        if n['id'] in shut: p['available'] = False
        elif n['id'] in caps: p['_assumption_cap_m3d'] = max(float(caps[n['id']]), 0.0)
    return out


def duty_cycle(nn, caps, shut, details):
    """Realise a cap that cannot be held as a steady choked rate as a producing-time fraction instead: the network keeps its
    natural operating point and each capped well's availability factor becomes cap / natural rate (volume is the same)."""
    out = copy.deepcopy(nn)
    for n in out:
        if n.get('kind') != 'well': continue
        p = n.setdefault('params', {})
        if n['id'] in shut: p['available'] = False
        elif n['id'] in caps:
            liq = max(_f((details.get(n['id']) or {}).get('liquid_rate_m3d'), 0.0) or 0.0, 1e-9)
            p['availability_factor'] = min(max(_f(p.get('availability_factor'), 1.0) or 1.0, 0.0), 1.0) * min(caps[n['id']] / liq, 1.0)
    return out


def abandonment_pressure_for_rf(params, rf):
    """Gas tank: abandonment pressure [bar] that gives recovery factor ``rf`` by p/z (no aquifer): (p/z)ab = (p/z)i (1-RF)."""
    from network.reservoir_mb import z_factor
    pi = max(_f(params.get('reservoir_pressure_bar'), 250.0), 1.0); t = _f(params.get('temperature_c'), 90.0); sg = _f(params.get('gas_sg'), 0.7)
    target = pi / z_factor(pi, t, sg) * (1.0 - min(max(rf, 0.0), 0.99))
    lo, hi = 1.0, pi
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if mid / z_factor(mid, t, sg) > target: hi = mid
        else: lo = mid
    return 0.5 * (lo + hi)


def rf_for_abandonment_pressure(params, pab):
    from network.reservoir_mb import z_factor
    pi = max(_f(params.get('reservoir_pressure_bar'), 250.0), 1.0); t = _f(params.get('temperature_c'), 90.0); sg = _f(params.get('gas_sg'), 0.7)
    return max(0.0, 1.0 - (pab / z_factor(pab, t, sg)) / (pi / z_factor(pi, t, sg)))


def annotate_recovery(rows, tanks):
    """Add target vs achieved columns to tank recovery rows (only for tanks with a target)."""
    for r in rows:
        tk = tanks.get(r.get('Tank ID'))
        trf = getattr(tk, 'target_rf', None) if tk is not None else None
        if not trf: continue
        inpl, cum = tank_primary(tk); ach = cum / max(inpl, 1.0)
        r['Target RF [%]'] = 100 * trf; r['Primary RF [%]'] = 100 * ach
        r['Target status'] = 'target reached' if ach >= trf - 0.002 else 'below target (%.1f %% short)' % (100 * (trf - ach))
    return rows


# ---- table helpers used by the UI (kept here so they are testable without Streamlit) --------------------------------------
UNITS = {'oil': {'rate': 'Sm³/d', 'rate_scale': 1.0, 'eur': 'MSm³', 'eur_scale': 1e6, 'inplace': 'MSm³', 'inplace_scale': 1e6},
         'gas': {'rate': 'MSm³/d', 'rate_scale': 1e6, 'eur': 'GSm³', 'eur_scale': 1e9, 'inplace': 'GSm³', 'inplace_scale': 1e9}}


def _well_phase(n, tanks_by_id):
    prm = n.get('params') or {}; t = tanks_by_id.get(prm.get('reservoir_id'))
    if t is not None: return 'oil' if str((t.get('params') or {}).get('fluid_phase', 'oil')).lower() == 'oil' else 'gas'
    return 'gas' if str(prm.get('ipr_model', '')).lower().startswith('gas') else 'oil'


def tank_table(nodes):
    rows = []
    for t in nodes:
        if t.get('kind') != 'reservoir': continue
        p = t.get('params') or {}; ph = 'oil' if str(p.get('fluid_phase', 'oil')).lower() == 'oil' else 'gas'; u = UNITS[ph]
        inpl = _f(p.get('stoiip_sm3'), 20e6) if ph == 'oil' else _f(p.get('giip_sm3'), 5e9)
        trf = target_rf_of(p)
        row = {'Tank ID': t['id'], 'Tank': t.get('name', t['id']), 'Primary phase': ph, 'In place [%s]' % u['inplace']: inpl / u['inplace_scale'],
               'Target RF [%]': None if trf is None else 100 * trf}
        row['Implied abandonment p [bar]'] = abandonment_pressure_for_rf(p, trf) if (ph == 'gas' and trf) else None
        rows.append(row)
    return rows


def well_table(nodes):
    tb = {n['id']: n for n in nodes if n.get('kind') == 'reservoir'}; rows = []
    for n in nodes:
        if n.get('kind') != 'well': continue
        p = n.get('params') or {}; ph = _well_phase(n, tb); u = UNITS[ph]
        mx = _f(p.get('max_oil_rate_m3d' if ph == 'oil' else 'max_gas_rate_sm3d'))
        cal = _f(p.get('calibrate_rate')); eur = _f(p.get('eur_cap'))
        rows.append({'Well ID': n['id'], 'Well': n.get('name', n['id']), 'Tank': (tb.get(p.get('reservoir_id')) or {}).get('name', '—'), 'Primary phase': ph,
                     'Max rate [%s]' % u['rate']: None if mx is None else mx / u['rate_scale'],
                     'Calibrate to rate [%s]' % u['rate']: None if cal is None else cal / u['rate_scale'],
                     'EUR cap [%s]' % u['eur']: None if eur is None else eur / u['eur_scale'],
                     'Productivity ×': _f(p.get('productivity_multiplier'), 1.0)})
    return rows


def _col(row, prefix):
    for k, v in row.items():
        if str(k).startswith(prefix): return v
    return None


def apply_tank_rows(nodes, rows, taper_days=None, set_gas_pmin=False):
    """Write edited tank-table rows back to the tanks (in place). Blank / 0 target removes the target. Returns #tanks changed."""
    byid = {n['id']: n for n in nodes}; k = 0
    for r in rows:
        n = byid.get(r.get('Tank ID'))
        if n is None: continue
        p = n.setdefault('params', {}); v = _f(r.get('Target RF [%]'))
        before = (p.get('target_rf'), p.get('rf_taper_days'))
        if v is None or v <= 0:
            p.pop('target_rf', None)
        else:
            p['target_rf'] = min(v, 100.0) / 100.0
            ph = 'oil' if str(p.get('fluid_phase', 'oil')).lower() == 'oil' else 'gas'
            if set_gas_pmin and ph == 'gas': p['min_pressure_bar'] = round(abandonment_pressure_for_rf(p, p['target_rf']), 2)
        if taper_days is not None and p.get('target_rf'): p['rf_taper_days'] = float(taper_days)
        k += (before != (p.get('target_rf'), p.get('rf_taper_days')))
    return k


def apply_well_rows(nodes, rows):
    """Write edited well-table rows (max rate, calibrate-to rate, EUR cap) back to the wells. Returns #wells changed."""
    byid = {n['id']: n for n in nodes}; k = 0
    tb = {n['id']: n for n in nodes if n.get('kind') == 'reservoir'}
    for r in rows:
        n = byid.get(r.get('Well ID'))
        if n is None: continue
        p = n.setdefault('params', {}); ph = _well_phase(n, tb); u = UNITS[ph]; before = dict(p)
        for prefix, key, scale in (('Max rate', 'max_oil_rate_m3d' if ph == 'oil' else 'max_gas_rate_sm3d', u['rate_scale']),
                                   ('Calibrate to rate', 'calibrate_rate', u['rate_scale']), ('EUR cap', 'eur_cap', u['eur_scale'])):
            v = _f(_col(r, prefix))
            if v is None or v <= 0: p.pop(key, None)
            else: p[key] = v * scale
        k += (before != p)
    return k
