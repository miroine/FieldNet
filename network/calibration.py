"""Calibrate well productivity to a target rate (forecast assumption).

The user states, per well, the primary-phase rate the well should deliver at start-up (``calibrate_rate``: oil m3/d for oil
wells, gas Sm3/d for gas wells). The wells are coupled through the network back-pressure, so the productivity multiplier
(applied to PI / Vogel qmax / gas C, see physics.well_model) is found by a joint, damped fixed-point iteration on full
network solves at the start-up conditions: m_i <- m_i * (target_i / achieved_i), per-iteration change limited to x0.25..x4.

The calibration is done on the *unrestricted* potential: the wells' own hard rate limits are lifted for the calibration
solves (they still apply in the forecast) and facility capacity enforcement is off, so that the multiplier describes the
reservoir/near-well deliverability, not a choke setting. Wells whose target cannot be reached through this network (the
rate stops responding to productivity, or the multiplier hits its bounds) are reported rather than forced.
"""
from __future__ import annotations
import copy, math

MULT_MIN, MULT_MAX = 0.01, 100.0
_LIMIT_KEYS = ('max_liquid_rate_m3d', 'max_oil_rate_m3d', 'max_water_rate_m3d', 'max_gas_rate_sm3d', '_network_cap_m3d',
               '_potential_cap_m3d', '_assumption_cap_m3d')


def targets_from_nodes(nodes):
    out = {}
    for n in nodes:
        if n.get('kind') != 'well': continue
        v = (n.get('params') or {}).get('calibrate_rate')
        try: v = float(v)
        except (TypeError, ValueError): continue
        if math.isfinite(v) and v > 0: out[n['id']] = v
    return out


def _primary(prm, liq, phase):
    wc = min(max(float(prm.get('water_cut', 0.0) or 0.0), 0.0), 0.9999); gor = max(float(prm.get('gor_sm3sm3', 0.0) or 0.0), 0.0)
    return liq * (1 - wc) * (gor if phase == 'gas' else 1.0)


def calibrate_wells(nodes, edges, targets=None, max_iter=12, tol=0.02, progress=None):
    """Returns ``{'multipliers': {wid: m}, 'rows': [...], 'converged': bool, 'iterations': int}`` (rows = per-well report)."""
    from network.reservoir_mb import tanks_from_nodes, apply_tank_links
    from network.forecast import solve_step
    from network.assumptions import primary_phase
    base = copy.deepcopy(nodes); targets = dict(targets if targets is not None else targets_from_nodes(base))
    rows = []
    if not targets: return {'multipliers': {}, 'rows': rows, 'converged': True, 'iterations': 0}
    tanks = tanks_from_nodes(base); byid = {n['id']: n for n in base}
    mult = {w: min(max(float((byid[w].get('params') or {}).get('productivity_multiplier', 1.0) or 1.0), MULT_MIN), MULT_MAX) for w in targets if w in byid}
    targets = {w: t for w, t in targets.items() if w in mult}
    limits = {w: {k: byid[w]['params'].get(k) for k in _LIMIT_KEYS if byid[w]['params'].get(k) is not None} for w in targets}
    best = {w: (0.0, mult[w]) for w in targets}   # (achieved, multiplier) at the best point
    hist = {w: [] for w in targets}; guess = None; ach = {}; it = 0; ok = False; phases = {}; snaps = []
    for it in range(1, max_iter + 1):
        nn = copy.deepcopy(base)
        for n in nn:
            if n['kind'] != 'well': continue
            p = n.setdefault('params', {})
            for k in _LIMIT_KEYS: p.pop(k, None)
            if n['id'] in mult: p['productivity_multiplier'] = mult[n['id']]
        nn = apply_tank_links(nn, tanks)
        from network.forecast import next_guess
        pp, qq, info, det = solve_step(nn, edges, guess, False); guess = next_guess(pp, qq, info)
        for n in nn:
            if n['id'] not in targets: continue
            prm = n.get('params') or {}; rid = prm.get('reservoir_id')
            ph = primary_phase(tanks[rid]) if rid in tanks else ('gas' if str(prm.get('ipr_model', '')).lower().startswith('gas') else 'oil')
            phases[n['id']] = ph
            ach[n['id']] = _primary(prm, float((det.get(n['id']) or {}).get('liquid_rate_m3d', 0.0)), ph)
            hist[n['id']].append((mult[n['id']], ach[n['id']]))
            if abs(ach[n['id']] - targets[n['id']]) < abs(best[n['id']][0] - targets[n['id']]) or it == 1: best[n['id']] = (ach[n['id']], mult[n['id']])
        snaps.append((max(abs(ach[w] / targets[w] - 1.0) for w in targets), dict(mult), dict(ach)))
        if progress: progress(it, max_iter, dict(ach))
        ok = all(abs(ach[w] / targets[w] - 1.0) <= tol for w in targets)
        if ok: break
        for w in targets:
            if abs(ach[w] / targets[w] - 1.0) <= tol: continue
            r = targets[w] / ach[w] if ach[w] > 1e-9 else 4.0
            if it > 3: r = r ** 0.7     # damp once the easy part is done (network back-pressure couples the wells)
            new = mult[w] * min(max(r, 0.25), 4.0)
            # the network response can jump (line loading); once the target is bracketed, bisect (in log m) instead of extrapolating
            lo = [m for m, a in hist[w] if a < targets[w]]; hi = [m for m, a in hist[w] if a >= targets[w]]
            if lo and hi and max(lo) < min(hi) and it >= 3: new = math.sqrt(max(lo) * min(hi))
            mult[w] = min(max(new, MULT_MIN), MULT_MAX)
    err, mult, ach = min(snaps, key=lambda x: x[0])   # best joint point, not the last (possibly oscillating) one
    for w, tgt in targets.items():
        a, m = ach.get(w, 0.0), mult[w]
        h = hist[w]; sat = len(h) >= 2 and h[-1][0] > 1.2 * h[-2][0] and h[-1][1] < 1.05 * h[-2][1] and h[-1][1] < 0.98 * tgt
        if abs(a / tgt - 1.0) <= tol: st = 'calibrated'
        elif a < tgt and (m >= MULT_MAX * 0.999 or sat): st = 'network-limited: cannot deliver target (max %.3g)' % max(x[1] for x in h)
        elif any(x[1] < tgt for x in h) and any(x[1] > tgt for x in h) and (lambda lo, hi: hi / lo < 1.03)(max(x[0] for x in h if x[1] < tgt), min(x[0] for x in h if x[1] >= tgt)):
            st = 'target falls in an unstable network region: rate jumps %.4g -> %.4g (line loading); choose a rate outside it' % (
                max(x[1] for x in h if x[1] < tgt), min(x[1] for x in h if x[1] >= tgt))
        elif m <= MULT_MIN * 1.001 and a > tgt: st = 'target below minimum productivity'
        else: st = 'not converged (%+.1f %%)' % (100 * (a / tgt - 1))
        lim = limits.get(w) or {}
        note = '; well rate limit applies in the forecast' if lim else ''
        rows.append({'Well ID': w, 'Well': byid[w].get('name', w), 'Phase': phases.get(w, 'oil'), 'Target rate': tgt, 'Achieved rate': a,
                     'Productivity multiplier': m, 'Status': st + note})
    ok = all(abs(ach[w] / targets[w] - 1.0) <= tol for w in targets)
    return {'multipliers': {w: mult[w] for w in targets}, 'rows': rows, 'converged': bool(ok), 'iterations': it}


def apply_calibration(nodes, result):
    """Write the multipliers into the nodes' params (in place); returns the number of wells changed."""
    k = 0
    byid = {n['id']: n for n in nodes}
    for w, m in (result.get('multipliers') or {}).items():
        if w in byid: byid[w].setdefault('params', {})['productivity_multiplier'] = float(m); k += 1
    return k


def clear_calibration(nodes):
    k = 0
    for n in nodes:
        if n.get('kind') == 'well' and (n.get('params') or {}).pop('productivity_multiplier', None) is not None: k += 1
    return k
