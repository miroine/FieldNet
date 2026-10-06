"""Flow-assurance checks ALONG each flowline (screening level).

Builds on ``network.element_results.edge_profile`` (pressure / velocity / density / holdup / regime along the
line, same march as the solver), ``physics.flow_assurance`` (heat loss, API RP14E velocity) and
``physics.flowline_profile`` (geometry, low/high points). The existing element profile has no temperature, so a
temperature profile is generated here with the lumped exponential heat-loss model of
``physics.flow_assurance.exponential_temperature_c`` (no Joule-Thomson, no phase-change heat, constant U);
the inlet temperature is the flowline ``temperature_c`` parameter (or a user override).

Checks (every row: Component, Check, Value, Limit, Margin, Status; Status in OK / Warning / Fail / n/a):

1. Hydrate margin = T - T_hydrate(P, gas SG) at each profile point, worst point reported. Default hydrate
   temperature: Towler & Mokhatab (2005) gas-gravity correlation,
       T[degF] = 13.47 ln P + 34.27 ln SG - 1.675 ln P ln SG - 20.35      (P in psia)
   stated by its authors for roughly 40-1000 psia (about 3-69 bar) and gas gravity ~0.55-1.0 (sweet natural
   gas, fresh water). HONESTY NOTE: the original article is paywalled and could not be retrieved while writing
   this module; the coefficients are reproduced from the author's recollection of the published form and were
   only sanity-checked (about 16 degC at 69 bar for SG 0.6, about 19 degC at 69 bar for SG 0.75, behaviour consistent
   with methane / natural-gas hydrate equilibrium curves), NOT checked against the paper or lab data.
   Outside the stated pressure range the result is an extrapolation and is flagged in ``Note``.
   Salinity, CO2/H2S and heavier-hydrocarbon (structure II) effects are ignored; use compositional hydrate
   software for design.  ``hydrate_method='v19_envelope'`` selects the older generic envelope from
   ``physics.flow_assurance`` and ``'conservative'`` takes the larger of the two.
2. Inhibitor dose suggestion by the Hammerschmidt equation, d[degC] = K W / (1.8 M (100 - W)), W in wt % of
   inhibitor in the aqueous phase, K = 2335 (methanol, M = 32.04) or 2700 (MEG, M = 62.07) (K and the validity
   limits W <= 25 % MeOH, W <= 70 % MEG as quoted in common field references, e.g. GPSA-type tables). Dose =
   W/(100-W) x water mass; losses of methanol to the gas / condensate and MEG regeneration are NOT modelled.
3. Wax margin = T - WAT at each point; WAT must be user supplied (``wat_c`` edge param or config); no default.
4. Erosional velocity ratio = v_mix / Ve with Ve = C / sqrt(rho) (API RP 14E form, C = 100 ft/s (lb/ft3)^0.5
   default for continuous service, converted to SI by ``physics.flow_assurance.api14e_erosional_velocity_ms``).
   Note: ``element_results.edge_profile`` evaluates C/sqrt(rho) with rho in kg/m3 directly, which gives a velocity
   about 18 % lower (more conservative) than the API unit conversion; this module recomputes the ratio itself.
5. Terrain / riser slugging screen: geometric trap / riser detection (``flowline_profile.slug_indicators``)
   combined with a mixture Froude criterion Fr = v_m / sqrt(g D) < ``froude_limit`` (default 1.5 - an implementation
   choice consistent with ``physics.flow_assurance``, NOT a published slug-onset boundary) or segregated flow at
   the site. This flags where a transient slugging study is warranted. It does not predict slug size or frequency.

None of this is validated against measured data.
"""
from __future__ import annotations
import copy
import math
from dataclasses import dataclass, field

from network.element_results import edge_profile, phase_flows
from physics.flow_assurance import exponential_temperature_c, api14e_erosional_velocity_ms, hydrate_equilibrium_temperature_c
from physics.flowline_profile import profile_points, summarize, slug_indicators, _extrema

G = 9.80665
BAR_TO_PSI = 14.503773773
INHIBITORS = {'MEG': {'K': 2700.0, 'M': 62.07, 'max_wt_pct': 70.0, 'rho_kgm3': 1113.0},
              'MEOH': {'K': 2335.0, 'M': 32.04, 'max_wt_pct': 25.0, 'rho_kgm3': 792.0}}
TM_RANGE_BAR = (40.0 / BAR_TO_PSI, 1000.0 / BAR_TO_PSI)
TM_SG_RANGE = (0.55, 1.0)


@dataclass
class ProfileFAConfig:
    ambient_temperature_c: float = 4.0
    overall_u_w_m2k: float = 5.0
    fluid_cp_j_kgk: float = 2200.0
    hydrate_margin_c: float = 3.0
    hydrate_method: str = 'towler_mokhatab'      # | 'v19_envelope' | 'conservative'
    inhibitor: str = 'MEG'                       # | 'MeOH'
    wat_c: float | None = None                   # wax appearance temperature; None = skip wax check
    wax_margin_c: float = 3.0
    erosion_c_factor: float = 100.0
    erosion_warn_ratio: float = 0.8
    froude_limit: float = 1.5
    min_trap_depth_m: float = 5.0
    min_riser_height_m: float = 20.0
    min_water_cut: float = 1e-4
    inlet_temperature_c: dict = field(default_factory=dict)   # {edge_id: degC} overrides edge params


# ----------------------------------------------------------------------------- correlations
def towler_mokhatab_hydrate_temperature_c(pressure_bar, gas_sg):
    """Hydrate formation temperature [degC] (Towler & Mokhatab 2005 form, see module doc for provenance caveat)."""
    p_psia = max(float(pressure_bar), 1.0) * BAR_TO_PSI
    sg = max(float(gas_sg), 0.3)
    lp, lg = math.log(p_psia), math.log(sg)
    t_f = 13.47 * lp + 34.27 * lg - 1.675 * lp * lg - 20.35
    return (t_f - 32.0) / 1.8


def hydrate_temperature_c(pressure_bar, gas_sg, method='towler_mokhatab'):
    m = str(method).lower()
    if m in ('towler_mokhatab', 'tm'):
        return towler_mokhatab_hydrate_temperature_c(pressure_bar, gas_sg)
    if m in ('v19_envelope', 'v19'):
        return hydrate_equilibrium_temperature_c(max(float(pressure_bar), 1.01325), gas_sg)
    if m == 'conservative':
        return max(towler_mokhatab_hydrate_temperature_c(pressure_bar, gas_sg), hydrate_equilibrium_temperature_c(max(float(pressure_bar), 1.01325), gas_sg))
    raise ValueError(f'unknown hydrate_method {method!r}')


def hammerschmidt_depression_c(wt_pct, inhibitor='MEG'):
    """Hydrate temperature depression [degC] for ``wt_pct`` inhibitor in the aqueous phase."""
    i = INHIBITORS[_inh(inhibitor)]; w = float(wt_pct)
    if not 0 <= w < 100:
        raise ValueError('wt_pct must be in [0, 100)')
    return i['K'] * w / (1.8 * i['M'] * (100.0 - w))


def hammerschmidt_required_wt_pct(depression_c, inhibitor='MEG'):
    """Inverse of ``hammerschmidt_depression_c``: wt % inhibitor needed for ``depression_c``."""
    i = INHIBITORS[_inh(inhibitor)]; a = 1.8 * i['M'] * max(float(depression_c), 0.0)
    return 100.0 * a / (i['K'] + a)


def inhibitor_dose(depression_c, water_m3d, inhibitor='MEG'):
    """Dose for ``depression_c`` over ``water_m3d`` produced water: dict wt_pct, kg_d, l_d, within_limit, limit_wt_pct."""
    key = _inh(inhibitor); i = INHIBITORS[key]
    w = hammerschmidt_required_wt_pct(depression_c, key)
    kg = w / (100.0 - w) * max(float(water_m3d), 0.0) * 1000.0
    return {'inhibitor': key, 'wt_pct': w, 'kg_d': kg, 'l_d': kg / i['rho_kgm3'] * 1000.0,
            'within_limit': w <= i['max_wt_pct'], 'limit_wt_pct': i['max_wt_pct']}


def _inh(name):
    k = str(name).upper().replace('-', '').replace(' ', '')
    k = {'METHANOL': 'MEOH', 'MEOH': 'MEOH', 'MEG': 'MEG', 'GLYCOL': 'MEG'}.get(k, k)
    if k not in INHIBITORS:
        raise ValueError(f'unknown inhibitor {name!r} (use MEG or MeOH)')
    return k


# ----------------------------------------------------------------------------- helpers
def _f(prm, key, default):
    try:
        v = float(prm.get(key, default))
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _status_margin(margin, required):
    return 'Fail' if margin < 0 else ('Warning' if margin < required else 'OK')


def _row(comp, check, value, limit, margin, status, unit='', x=None, note=''):
    return {'Component': comp, 'Check': check, 'Value': value, 'Limit': limit, 'Margin': margin, 'Status': status,
            'Unit': unit, 'Location [m]': x, 'Note': note}


def _flow_order_points(e, q):
    pts = profile_points(e)
    if q < 0:
        X = pts[-1][0]
        pts = [(X - x, z) for x, z in reversed(pts)]
    return pts


def _cum_len(pts):
    c = [0.0]
    for (x0, z0), (x1, z1) in zip(pts[:-1], pts[1:]):
        c.append(c[-1] + math.hypot(x1 - x0, z1 - z0))
    return c


def temperature_profile(rows, t_in_c, t_amb_c, u, d_m, cp):
    """Temperature [degC] at every profile row (flow order) from lumped heat loss; mass flow = rho v A per segment."""
    area = math.pi * d_m ** 2 / 4.0
    t = [float(t_in_c)]
    for i in range(1, len(rows)):
        r = rows[i]; dx = rows[i]['x_m'] - rows[i - 1]['x_m']
        mdot = (r.get('rho_kgm3') or 0.0) * (r.get('velocity_ms') or 0.0) * area
        if dx <= 0 or mdot <= 0:
            t.append(t[-1]); continue
        t.append(exponential_temperature_c(t[-1], t_amb_c, u, d_m, dx, mdot, cp))
    return t


def thermal_energy_profile(rows, t_in_c, prm, d_m, q, wc, ph=None):
    """Temperature at every profile row from the energy balance of ``physics.thermal`` (heat loss + JT + elevation)."""
    from physics import thermal as th
    pv = prm.get('pvt') or {}; api = _f(prm, 'api', 35.0); sg = _f(prm, 'gas_sg', 0.75); gor = _f(prm, 'gor_sm3sm3', 100.0)
    stream = th.Stream(q, wc, gor, api, sg, gas_cp_override=prm.get('gas_cp_jkgk'), co2=_f(pv, 'co2', 0.0), h2s=_f(pv, 'h2s', 0.0), n2=_f(pv, 'n2', 0.0))
    spec = th.line_spec(prm, d_m); t = [float(t_in_c)]
    for i in range(1, len(rows)):
        a, b = rows[i - 1], rows[i]; dx = b['x_m'] - a['x_m']
        if dx <= 0: t.append(t[-1]); continue
        dz = (b.get('z_m') or 0.0) - (a.get('z_m') or 0.0); dp = (b['pressure_bar'] or 0.0) - (a['pressure_bar'] or 0.0)
        t.append(th.advance_segment(t[-1], dx, dz, dp, spec['d_out'], spec['u'], spec['t_amb'], stream, max(0.5 * (a['pressure_bar'] + b['pressure_bar']), 1.0), None, 800.0, spec['jt'], spec['elev']))
    return t


# ----------------------------------------------------------------------------- single line
def assess_line(e, q, p_up, info=None, ph=None, cfg=None, name=None):
    """Assess one flowline. ``q`` liquid rate [m3/d] (sign = flow direction), ``p_up`` pressure [bara] at the
    upstream end of the flow, ``ph`` optional phase-rate dict (oil / water / gas std rates) of the stream.
    Returns ``{'rows': [...], 'worst': {...}, 'profile': [...]}`` (profile rows are DataFrame ready)."""
    cfg = cfg or ProfileFAConfig(); prm = e.get('params') or {}
    comp = name or e.get('name') or e['id']
    prof = edge_profile(e, q, p_up, info, ph)
    if len(prof) < 2 or abs(q) < 1e-6:
        return {'rows': [_row(comp, 'All checks', None, None, None, 'n/a', note='no flow or not a flowline')], 'worst': {}, 'profile': []}
    D = _f(e, 'diameter_m', 0.154); sg = _f(prm, 'gas_sg', 0.75)
    wc = _f(prm, 'water_cut', 0.2)
    water = None
    if ph and (ph.get('oil', 0) + ph.get('water', 0)) > 1e-9:
        wc = ph['water'] / (ph['oil'] + ph['water']); water = ph['water']
    if water is None:
        water = wc * abs(q)
    t_net = ((((info or {}).get('thermal') or {}).get('edge') or {}).get(e['id']) or {}).get('t_in')   # network thermal pass (wells -> flowlines)
    t_in = cfg.inlet_temperature_c.get(e['id'], t_net if t_net is not None else _f(prm, 'temperature_c', 50.0))
    t_amb = _f(prm, 'ambient_temperature_c', cfg.ambient_temperature_c); u = _f(prm, 'overall_u_w_m2k', cfg.overall_u_w_m2k)
    from physics import thermal as _th
    if _th.mode(prm) == 'heat_loss':   # energy balance: mixture cp, Joule-Thomson, elevation
        temps = thermal_energy_profile(prof, t_in, prm, D, q, wc, ph)
    else: temps = temperature_profile(prof, t_in, t_amb, u, D, cfg.fluid_cp_j_kgk)
    C = _f(prm, 'erosion_c_factor', cfg.erosion_c_factor)
    method = prm.get('hydrate_method', cfg.hydrate_method)
    inh = prm.get('inhibitor', cfg.inhibitor)
    wat = prm.get('wat_c', prm.get('wax_appearance_temperature_c', cfg.wat_c))
    wat = None if wat in (None, '') else float(wat)
    pts = _flow_order_points(e, q); cum = _cum_len(pts)
    table = []
    for r, t in zip(prof, temps):
        p = r['pressure_bar']; th = hydrate_temperature_c(p, sg, method)
        v = r['velocity_ms']; rho = r['rho_kgm3']
        ve = api14e_erosional_velocity_ms(rho, C) if rho and rho > 0 else None
        table.append({'x_m': r['x_m'], 'z_m': r['z_m'], 'pressure_bar': p, 'temperature_c': t, 'hydrate_temperature_c': th, 'hydrate_margin_c': t - th,
                      'wax_margin_c': (t - wat) if wat is not None else None, 'velocity_ms': v, 'rho_kgm3': rho,
                      'erosional_velocity_ms': ve, 'erosional_ratio': (v / ve) if (ve and v is not None) else None,
                      'froude': (v / math.sqrt(G * D)) if v is not None else None, 'regime': r['regime'], 'holdup': r['holdup']})
    rows = []; worst = {}
    # 1+2 hydrate and inhibitor
    wet = wc >= cfg.min_water_cut
    if not wet:
        rows.append(_row(comp, 'Hydrate margin', None, cfg.hydrate_margin_c, None, 'n/a', 'degC', note='no free water (water cut below threshold)'))
    else:
        w = min(table, key=lambda r: r['hydrate_margin_c'])
        note = f"T={w['temperature_c']:.1f} C, P={w['pressure_bar']:.1f} bar, T_hyd={w['hydrate_temperature_c']:.1f} C ({method})"
        pmin, pmax = min(r['pressure_bar'] for r in table), max(r['pressure_bar'] for r in table)
        if str(method).lower() in ('towler_mokhatab', 'tm', 'conservative') and (pmax > TM_RANGE_BAR[1] or pmin < TM_RANGE_BAR[0] or not TM_SG_RANGE[0] <= sg <= TM_SG_RANGE[1]):
            note += '; outside stated Towler-Mokhatab range (40-1000 psia, SG 0.55-1.0): extrapolation'
        m = w['hydrate_margin_c']
        rows.append(_row(comp, 'Hydrate margin', m, cfg.hydrate_margin_c, m - cfg.hydrate_margin_c, _status_margin(m, cfg.hydrate_margin_c), 'degC', w['x_m'], note))
        worst['hydrate'] = {'x_m': w['x_m'], 'margin_c': m, 'temperature_c': w['temperature_c'], 'pressure_bar': w['pressure_bar']}
        need = max(cfg.hydrate_margin_c - r['hydrate_margin_c'] for r in table)
        if need > 0:
            dose = inhibitor_dose(need, water, inh)
            st = 'Warning' if dose['within_limit'] else 'Fail'
            rows.append(_row(comp, f"{dose['inhibitor']} dose (Hammerschmidt)", dose['wt_pct'], dose['limit_wt_pct'], dose['limit_wt_pct'] - dose['wt_pct'], st, 'wt% in water', w['x_m'],
                             f"{dose['kg_d']:.0f} kg/d ({dose['l_d']:.0f} L/d) for {need:.1f} C depression over {water:.0f} m3/d water; excludes losses to hydrocarbon phases"))
            worst['inhibitor'] = {**dose, 'required_depression_c': need, 'water_m3d': water}
        else:
            rows.append(_row(comp, f"{_inh(inh)} dose (Hammerschmidt)", 0.0, INHIBITORS[_inh(inh)]['max_wt_pct'], INHIBITORS[_inh(inh)]['max_wt_pct'], 'OK', 'wt% in water', None, 'no inhibitor required at this operating point'))
    # 3 wax
    if wat is not None:
        w = min(table, key=lambda r: r['wax_margin_c']); m = w['wax_margin_c']
        rows.append(_row(comp, 'Wax margin (T - WAT)', m, cfg.wax_margin_c, m - cfg.wax_margin_c, _status_margin(m, cfg.wax_margin_c), 'degC', w['x_m'], f"WAT={wat:.1f} C (user supplied), T={w['temperature_c']:.1f} C"))
        worst['wax'] = {'x_m': w['x_m'], 'margin_c': m, 'temperature_c': w['temperature_c']}
    # 4 erosion
    er = [r for r in table if r['erosional_ratio'] is not None]
    if er:
        w = max(er, key=lambda r: r['erosional_ratio']); ratio = w['erosional_ratio']
        st = 'Fail' if ratio > 1.0 else ('Warning' if ratio > cfg.erosion_warn_ratio else 'OK')
        rows.append(_row(comp, 'Erosional velocity ratio (API RP14E)', ratio, 1.0, 1.0 - ratio, st, '-', w['x_m'], f"v={w['velocity_ms']:.2f} m/s, Ve={w['erosional_velocity_ms']:.2f} m/s, C={C:g}"))
        worst['erosion'] = {'x_m': w['x_m'], 'ratio': ratio, 'velocity_ms': w['velocity_ms']}
    # 5 slugging
    try:
        si = slug_indicators(pts, cfg.min_trap_depth_m, cfg.min_riser_height_m)
        sm = summarize(pts)
    except Exception as ex:      # pragma: no cover - defensive
        si = None; sm = None; rows.append(_row(comp, 'Terrain slugging screen', None, None, None, 'n/a', note=f'profile unusable: {ex}'))
    if si is not None:
        def at_len(dist):
            return min(table, key=lambda r: abs(r['x_m'] - dist))
        def hx(xh):
            idx = min(range(len(pts)), key=lambda i: abs(pts[i][0] - xh)); return cum[idx]
        slow = lambda r: r['froude'] is not None and (r['froude'] < cfg.froude_limit or r['regime'] == 'segregated')
        if si['terrain_slugging_flag']:
            traps = [lp for lp in sm['low_points'] if lp['depth_m'] >= cfg.min_trap_depth_m]
            cands = [at_len(hx(lp['x_m'])) for lp in traps]
            w = min([c for c in cands if c['froude'] is not None], key=lambda r: r['froude'], default=None)
            if w:
                fl = slow(w)
                rows.append(_row(comp, 'Terrain slugging screen', w['froude'], cfg.froude_limit, w['froude'] - cfg.froude_limit, 'Warning' if fl else 'OK', 'Fr [-]', w['x_m'],
                                 f"{si['n_traps']} trap(s), deepest {si['max_trap_depth_m']:.0f} m; regime={w['regime']}; screening only"))
                worst['terrain_slugging'] = {'x_m': w['x_m'], 'froude': w['froude'], 'flag': fl, 'n_traps': si['n_traps']}
        else:
            rows.append(_row(comp, 'Terrain slugging screen', 0.0, cfg.min_trap_depth_m, cfg.min_trap_depth_m, 'OK', 'trap depth [m]', None, 'no intermediate low point deeper than the threshold'))
        if si['riser_slugging_flag'] == 'possible':
            ex = _extrema(pts); lows = [i for i, k in ex if k == 'low']
            base = lows[-1] if lows else min(range(len(pts)), key=lambda i: pts[i][1])
            w = at_len(cum[base])
            if w['froude'] is not None:
                fl = slow(w)
                rows.append(_row(comp, 'Riser-base slugging screen', w['froude'], cfg.froude_limit, w['froude'] - cfg.froude_limit, 'Warning' if fl else 'OK', 'Fr [-]', w['x_m'],
                                 f"{si['riser_height_m']:.0f} m riser fed by {si['downslope_feed_length_m']:.0f} m downhill run; regime={w['regime']}; screening only"))
                worst['riser_slugging'] = {'x_m': w['x_m'], 'froude': w['froude'], 'flag': fl, 'riser_height_m': si['riser_height_m']}
        elif si['riser_height_m'] >= cfg.min_riser_height_m:
            rows.append(_row(comp, 'Riser-base slugging screen', None, None, None, 'OK', note=si['notes'][0] if si['notes'] else 'riser not fed by a downhill run'))
    return {'rows': rows, 'worst': worst, 'profile': table}


# ----------------------------------------------------------------------------- network
def _unpack(res):
    if isinstance(res, dict):
        return res.get('pressures') or {}, res.get('flows') or {}, res.get('info') or {}, res.get('details') or {}
    return res[0], res[1], res[2], res[3]


def assess_network(nodes, edges, solve_result, cfg=None, edge_ids=None):
    """Run ``assess_line`` for every pipeline carrying flow. Returns ``{'rows': [...], 'lines': {edge_id: worst},
    'profiles': {edge_id: [...]}}``; ``rows`` is ready for ``pandas.DataFrame``."""
    cfg = cfg or ProfileFAConfig()
    p, q, info, d = _unpack(solve_result)
    ph, _ = phase_flows(nodes, edges, q, d, info)
    names = {n['id']: n.get('name', n['id']) for n in nodes}
    rows, lines, profiles = [], {}, {}
    for e in edges:
        if e.get('kind', 'pipeline') != 'pipeline' or e['id'] not in q or (edge_ids is not None and e['id'] not in edge_ids):
            continue
        qq = q[e['id']]; pu = p.get(e['source'] if qq >= 0 else e['target'])
        if pu is None:
            continue
        nm = e.get('name') or f"{e['id']} ({names.get(e['source'])} to {names.get(e['target'])})"
        try:
            r = assess_line(e, qq, pu, info, ph.get(e['id']), cfg, nm)
        except Exception as ex:
            r = {'rows': [_row(nm, 'All checks', None, None, None, 'n/a', note=f'not assessed: {ex}')], 'worst': {}, 'profile': []}
        rows += r['rows']; lines[e['id']] = r['worst']; profiles[e['id']] = r['profile']
    return {'rows': rows, 'lines': lines, 'profiles': profiles}
