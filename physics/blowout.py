"""Uncontrolled-flow (blowout / worst-case discharge) screening for one well.

The reservoir inflow (IPR, optionally with the skin removed - there is no completion restriction in a blowout) flows to the surface against
atmospheric pressure (or the seabed hydrostatic head for a subsea well) through the tubing, the annulus, or both, with no choke and no lift.
The operating point is the intersection of the IPR with the vertical lift curve of the open flow path (the same multiphase correlations
as the nodal analysis). Exit choking is checked with the Wood mixture speed of sound at the top segment; when the exit flow would be sonic the
exit pressure is raised until it is just sonic.

Screening only - not a substitute for a transient wellbore/reservoir simulator or a regulatory worst-case-discharge study:
no transient inflow (steady IPR), annulus as an equal-area pipe, no gas-cap coning, no hole collapse / bridging, no relief-well intervention."""
from __future__ import annotations
import copy, math
import numpy as np
from physics.well_model import well_settings, ipr_pwf, ipr_rate, vlp_bhp, rate_capacity
from physics.vlp import tubing_bhp_bar
from physics.pvt_model import fluid_scope, gas_z

P_ATM = 1.01325; RHO_SW = 1025.0; G = 9.80665
PATHS = ('tubing', 'annulus', 'both')


def exit_pressure_bar(water_depth_m=0.0):
    return P_ATM + RHO_SW * G * max(float(water_depth_m), 0.0) / 1e5


def _paths_settings(prm, path, skin_removed=True):
    """well_settings for the open flow path(s). The annulus becomes an equal-flow-area pipe: D = sqrt(Dci^2 - Dto^2)."""
    p = copy.deepcopy(prm); p.update({'lift_type': 'none', 'available': True, 'opening_factor': 1.0, 'max_liquid_rate_m3d': None, 'max_oil_rate_m3d': None, 'max_water_rate_m3d': None,
                                      'max_gas_rate_sm3d': None, 'min_rate_m3d': 0.0, 'rate_limit_mode': 'report', 'lift_assist_bar': 0.0, '_network_cap_m3d': None, '_potential_cap_m3d': None})
    if skin_removed: p['skin'] = 0.0
    tid = float(p.get('tubing_id_m') or 0.0762); out = {}
    for name in ([path] if path != 'both' else ['tubing', 'annulus']):
        q = dict(p)
        if name == 'annulus':
            dci = float(p.get('casing_id_m') or 0.2205); dto = float(p.get('tubing_od_m') or tid * 1.15)
            if dci <= dto * 1.02: raise ValueError('casing ID must exceed tubing OD for annulus flow')
            q['tubing_id_m'] = math.sqrt(dci ** 2 - dto ** 2); q['trajectory'] = None; q['completion'] = None
        out[name] = well_settings(q)
    return out


def _vlp_curve(ws, whp, q):
    return np.array([vlp_bhp(float(x), whp, ws)[0] for x in q])


def _solve_curves(wss, whp, points=41):
    """Intersect the IPR with the (sum-of-paths) VLP. Returns (q_total, pwf, {path: q_path}, grid dict)."""
    ref = next(iter(wss.values())); cap = max(rate_capacity(ref), 1.0); q = np.linspace(0.0, cap, points)
    ipr = np.array([ipr_pwf(float(x), ref) for x in q]); vl = {k: _vlp_curve(w, whp, q) for k, w in wss.items()}
    if len(wss) == 1:
        v = next(iter(vl.values())); g = ipr - v; idx = [i for i in range(len(q) - 1) if g[i] > 0 >= g[i + 1]]
        if g[0] <= 0: return 0.0, float(ipr[0]), {k: 0.0 for k in wss}, {'q': q, 'ipr': ipr, 'vlp': vl}
        if idx:
            i = idx[-1]; f = g[i] / (g[i] - g[i + 1]); qs = q[i] + f * (q[i + 1] - q[i])
        else: qs = q[-1]
        qs = float(qs); return qs, float(ipr_pwf(qs, ref)), {k: qs for k in wss}, {'q': q, 'ipr': ipr, 'vlp': vl}
    # several paths share one bottom-hole pressure: add rates at equal pwf (right-hand branch of every VLP)
    pw = np.linspace(max(min(v.min() for v in vl.values()), 1.0), float(ref['pr']), 400); qp = {}
    for k, v in vl.items():
        i0 = int(np.argmin(v)); qq, vv = q[i0:], v[i0:]
        qp[k] = np.where(pw >= vv[0], np.interp(pw, vv, qq), 0.0)
    tot = sum(qp.values()); qi = np.array([ipr_rate(float(x), ref) for x in pw]); g = qi - tot
    idx = [i for i in range(len(pw) - 1) if g[i] > 0 >= g[i + 1] or g[i] >= 0 > g[i + 1]]
    if not idx: return 0.0, float(ref['pr']), {k: 0.0 for k in wss}, {'q': q, 'ipr': ipr, 'vlp': vl}
    i = idx[0]; f = g[i] / (g[i] - g[i + 1]); pwf = pw[i] + f * (pw[i + 1] - pw[i])
    per = {k: float(np.interp(pwf, pw, qp[k])) for k in qp}
    return float(sum(per.values())), float(pwf), per, {'q': q, 'ipr': ipr, 'vlp': vl}


def _wood_mach(ws, q, whp, rate_share=1.0):
    """Exit Mach number from the top tubing segment: mixture velocity / Wood speed of sound (screening)."""
    if q <= 0: return 0.0, {}
    prof = []
    with fluid_scope(ws.get('pvt_prm'), ws['gor'], ws['api'], ws['gas_sg']):
        tubing_bhp_bar(q, whp, ws['depth'], ws['tubing_id'], ws['roughness'], ws['temperature'], ws['water_cut'], ws['gor'], ws['api'], ws['gas_sg'], ws['correlation'],
                       segments=ws['segments'], bottomhole_temperature_c=ws['bh_temperature'], geometry=ws.get('geometry'), thermal=ws.get('thermal'), profile=prof)
    if not prof: return 0.0, {}
    r = prof[0]; v = float(r.get('velocity_ms') or 0.0); p = max(float(r['pressure_bar']), 1.0); hl = min(max(float(r.get('liquid_holdup') or 1.0), 0.0), 1.0)
    rho_m = float(r.get('rho_kgm3') or 0.0) or 500.0; a_g = 1.0 - hl
    t_k = ws['temperature'] + 273.15; rho_g = p * 1e5 * 28.97e-3 * ws['gas_sg'] / (max(gas_z(p, ws['temperature'], ws['gas_sg']), 0.2) * 8.314 * t_k)
    rho_l = max((rho_m - a_g * rho_g) / max(1 - a_g, 1e-3), 400.0) if a_g < 0.999 else 800.0
    if a_g < 1e-4: c = 1200.0
    else:
        inv = a_g / (1.3 * p * 1e5) + (1 - a_g) / (rho_l * 1200.0 ** 2); c = math.sqrt(1.0 / max(rho_m * inv, 1e-30))
    return v / c, {'velocity_ms': v, 'sound_speed_ms': c, 'void_fraction': a_g, 'top_pressure_bar': p}


def solve(prm, path='tubing', water_depth_m=0.0, skin_removed=True, check_choking=True, points=41):
    """Blowout rate. Returns a dict (rates, pwf, path split, exit pressure, Mach / choked flag, curves for plotting)."""
    if path not in PATHS: raise ValueError(f'path must be one of {PATHS}')
    wss = _paths_settings(prm, path, skin_removed); whp = exit_pressure_bar(water_depth_m)
    q, pwf, per, grid = _solve_curves(wss, whp, points); mach, info, choked = 0.0, {}, False
    if check_choking and q > 0:
        k0 = next(iter(wss)); mach, info = _wood_mach(wss[k0], per[k0], whp)
        if mach > 1.0:
            lo, hi = whp, max(whp * 4, 200.0)
            for _ in range(14):
                mid = 0.5 * (lo + hi); qm, _p, pm, _g = _solve_curves(wss, mid, points); mm = _wood_mach(wss[k0], pm[k0], mid)[0]
                if mm > 1.0: lo = mid
                else: hi = mid
            whp = hi; q, pwf, per, grid = _solve_curves(wss, whp, points); mach, info = _wood_mach(wss[k0], per[k0], whp); choked = True
    ref = next(iter(wss.values())); wc = ref['water_cut']; oil = q * (1 - wc)
    return {'path': path, 'q_liq': q, 'q_oil': oil, 'q_water': q * wc, 'q_gas': oil * ref['gor'], 'pwf': pwf, 'exit_pressure_bar': whp, 'mach': mach, 'choked': choked, 'path_rates': per,
            'skin_removed': skin_removed, 'water_depth_m': water_depth_m, 'exit': info, 'curves': grid, 'aof_m3d': rate_capacity(ref), 'pr': ref['pr']}


def units(r):
    """Reporting conversions."""
    return {'Oil [Sm3/d]': r['q_oil'], 'Oil [bbl/d]': r['q_oil'] * 6.28981, 'Gas [Sm3/d]': r['q_gas'], 'Gas [MMscf/d]': r['q_gas'] * 35.3147e-6, 'Water [m3/d]': r['q_water'],
            'Oil equivalent [Sm3 o.e./d]': r['q_oil'] + r['q_gas'] / 1000.0}


def scenario_table(prm, water_depth_m=0.0):
    import pandas as pd
    rows = []
    for path in PATHS:
        for sk in (True, False):
            try: r = solve(prm, path, water_depth_m, sk)
            except ValueError as e: rows.append({'Flow path': path, 'Skin': 'removed' if sk else 'kept', 'Note': str(e)}); continue
            u = units(r); rows.append({'Flow path': path, 'Skin': 'removed' if sk else 'kept', **{k: v for k, v in u.items() if k in ('Oil [Sm3/d]', 'Oil [bbl/d]', 'Gas [MMscf/d]', 'Water [m3/d]')},
                                       'Flowing BHP [bar]': r['pwf'], 'Exit pressure [bar]': r['exit_pressure_bar'], 'Exit Mach': r['mach'], 'Choked': r['choked']})
    return pd.DataFrame(rows)


def depletion_profile(prm, path='tubing', water_depth_m=0.0, skin_removed=True, connected_pv_m3=None, ct_1bar=1.5e-4, days=90, steps=18):
    """Rate versus time while the connected volume depletes (no aquifer / gas cap / injection support). Time steps are geometric so the early peak is resolved.
    Needs the connected pore volume [m3]; without it the profile is skipped (the caller should say why)."""
    import pandas as pd
    if not connected_pv_m3 or connected_pv_m3 <= 0: raise ValueError('connected pore volume is required for a depletion profile')
    p = copy.deepcopy(prm); bo = float(p.get('boi_rm3_sm3') or 1.25); t_prev = 0.0; cum_o = cum_g = cum_w = 0.0; rows = []
    edges = np.unique(np.concatenate([[0.0], np.geomspace(0.1, float(days), int(steps))]))
    for t in edges[1:]:
        r = solve(p, path, water_depth_m, skin_removed, check_choking=False); dt = t - t_prev
        rows.append({'Day': float(t), 'Reservoir pressure [bar]': p['reservoir_pressure_bar'], 'Oil [Sm3/d]': r['q_oil'], 'Gas [Sm3/d]': r['q_gas'], 'Water [m3/d]': r['q_water'], 'Cum oil [Sm3]': cum_o + r['q_oil'] * dt,
                     'Cum gas [Sm3]': cum_g + r['q_gas'] * dt, 'Cum water [m3]': cum_w + r['q_water'] * dt})
        cum_o += r['q_oil'] * dt; cum_g += r['q_gas'] * dt; cum_w += r['q_water'] * dt
        void = (r['q_oil'] * bo + r['q_water']) * dt; p['reservoir_pressure_bar'] = max(p['reservoir_pressure_bar'] - void / (connected_pv_m3 * ct_1bar), P_ATM); t_prev = t
    return pd.DataFrame(rows)
