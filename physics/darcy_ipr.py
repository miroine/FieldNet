"""Darcy-law inflow performance from reservoir properties: vertical, deviated and horizontal wells, single or commingled layers.

All inputs SI-ish: permeability in mD, lengths in m, viscosity in cP, pressure in bar, temperature in degC.
Outputs are what the well model uses: an oil productivity index ``pi`` [Sm3/d/bar] or a gas back-pressure coefficient
``gas_c`` [Sm3/d/bar^2] (n = 1, p^2 form).

Oil, steady / pseudo-steady radial flow (vertical)::

    PI = 2 pi k h / (mu B (ln(re/rw) + c + s)) ,     c = -3/4 (pseudo-steady) or 0 (steady state)

Horizontal well, Joshi (1988) steady-state with vertical-to-horizontal anisotropy::

    PI = 2 pi kh h / (mu B [ ln((a + sqrt(a^2 - (L/2)^2)) / (L/2)) + (Iani h / L) (ln(Iani h / (rw (Iani + 1))) + s) ])
    a = (L/2) sqrt(0.5 + sqrt(0.25 + (2 reh / L)^4)) ,   Iani = sqrt(kh / kv)

Deviated well: vertical PI with the Cinco-Ley, Ramey & Miller (1975) pseudo-skin for inclinations up to 75 deg.
Gas: the same geometry factor in the p^2 form, ``C = pi k h Tsc / (psc T mu z D)`` with D the denominator above.
Commingled layers share one bottom-hole pressure, so their PI (or C) add.
"""
from __future__ import annotations
import math

MD = 9.869233e-16            # m2 per mD
CP = 1.0e-3                  # Pa s per cP
DAY = 86400.0
BAR = 1.0e5
T_SC, P_SC = 288.15, 101325.0
DEFAULTS = {'orientation': 'vertical', 'perm_md': 100.0, 'kv_kh': 0.5, 'net_pay_m': 30.0, 'drainage_radius_m': 500.0, 'wellbore_radius_m': 0.108,
            'lateral_length_m': 1000.0, 'inclination_deg': 0.0, 'regime': 'pseudo', 'visc_cp': 1.0, 'bo': 1.2}
ORIENTATIONS = ('vertical', 'deviated', 'horizontal')


def _g(p, key, default=None):
    v = p.get('darcy_' + key)
    if v is None or (isinstance(v, str) and not v.strip()): return DEFAULTS.get(key, default) if default is None else default
    try:
        f = float(v)
        return f if math.isfinite(f) else (DEFAULTS.get(key, default) if default is None else default)
    except (TypeError, ValueError):
        return v if isinstance(v, str) else (DEFAULTS.get(key, default) if default is None else default)


def deviation_skin(inc_deg, h, rw, kh, kv):
    """Cinco-Ley, Ramey & Miller pseudo-skin of a deviated well (negative = more productive)."""
    if inc_deg <= 0: return 0.0
    inc = min(inc_deg, 75.0); ratio = math.sqrt(max(kv / max(kh, 1e-300), 1e-8))
    th = math.degrees(math.atan(ratio * math.tan(math.radians(inc))))
    hd = (h / rw) / ratio
    return -((th / 41.0) ** 2.06) - ((th / 56.0) ** 1.865) * math.log10(max(hd / 100.0, 1e-6))


def geometry_denominator(orientation, kh, kv, h, re, rw, skin, regime='pseudo', length=0.0, inc_deg=0.0):
    """The dimensionless denominator D in q ~ 2 pi kh h dp / (mu B D)."""
    if orientation == 'horizontal':
        L = max(length, 1.0); reh = max(re, 0.6 * L)                # Joshi needs L/2 < reh
        iani = math.sqrt(max(kh, 1e-300) / max(kv, 1e-300)); a = (L / 2.0) * math.sqrt(0.5 + math.sqrt(0.25 + (2.0 * reh / L) ** 4))
        t1 = math.log((a + math.sqrt(max(a * a - (L / 2.0) ** 2, 0.0))) / (L / 2.0))
        t2 = (iani * h / L) * (math.log(max(iani * h / (rw * (iani + 1.0)), 1.0000001)) + skin)
        return max(t1 + t2, 0.05)
    c = -0.75 if regime == 'pseudo' else 0.0
    s = skin + (deviation_skin(inc_deg, h, rw, kh, kv) if orientation == 'deviated' else 0.0)
    return max(math.log(max(re, rw * 1.01) / rw) + c + s, 0.05)


def _layer(p, lay):
    g = lambda k: (lay.get(k) if lay.get(k) not in (None, '') else _g(p, k))
    return {'kh': max(float(g('perm_md')), 1e-9), 'h': max(float(g('net_pay_m')), 1e-3), 'kvkh': min(max(float(g('kv_kh')), 1e-4), 1.0),
            'skin': float(lay.get('skin') if lay.get('skin') not in (None, '') else p.get('skin') or 0.0)}


def gas_mu_z(pr_bar, t_c, gas_sg, visc_cp=None, z=None):
    from physics.pvt_model import gas_z, gas_viscosity_cp
    pa = max(0.75 * pr_bar, 1.0)
    zz = float(z) if z else float(gas_z(pa, t_c, gas_sg))
    mu = float(visc_cp) if visc_cp else float(gas_viscosity_cp(pa, t_c, zz, gas_sg))
    return mu, zz


def darcy_ipr(p, pr_bar, fluid='oil'):
    """Productivity from reservoir properties in ``p`` (keys ``darcy_*``, plus ``skin``, ``temperature_c``/``bottomhole_temperature_c``, ``gas_sg``).
    ``fluid`` 'oil' -> returns ``pi`` [Sm3/d/bar]; 'gas' -> ``gas_c`` [Sm3/d/bar^2]. Returns {'pi','gas_c','denominator','warnings','layers'}."""
    orient = str(_g(p, 'orientation', 'vertical')).lower(); orient = orient if orient in ORIENTATIONS else 'vertical'
    regime = str(_g(p, 'regime', 'pseudo')).lower(); regime = regime if regime in ('pseudo', 'steady') else 'pseudo'
    re = max(float(_g(p, 'drainage_radius_m')), 1.0); rw = min(max(float(_g(p, 'wellbore_radius_m')), 0.01), re * 0.5)
    L = max(float(_g(p, 'lateral_length_m')), 0.0); inc = min(max(float(_g(p, 'inclination_deg')), 0.0), 90.0)
    layers = [l for l in (p.get('darcy_layers') or []) if isinstance(l, dict) and (l.get('perm_md') not in (None, '') or l.get('net_pay_m') not in (None, ''))]
    layers = layers or [{}]
    warns = []
    if orient == 'horizontal':
        if L <= 0: warns.append('Horizontal well without a lateral length.')
        if L / 2.0 >= re: warns.append('Lateral half-length is not smaller than the drainage radius: the drainage radius was raised to 0.6 L (Joshi validity).')
    if orient == 'deviated' and inc > 75: warns.append('Deviation above 75 deg is outside the Cinco-Ley correlation; 75 deg was used.')
    mu_o = max(float(_g(p, 'visc_cp')), 1e-3); bo = max(float(_g(p, 'bo')), 0.5)
    t_c = float(p.get('bottomhole_temperature_c', p.get('temperature_c', 80.0)) or 80.0); tk = t_c + 273.15
    if fluid == 'gas':
        vis = p.get('darcy_gas_visc_cp'); mu, z = gas_mu_z(pr_bar, t_c, float(p.get('gas_sg', 0.7) or 0.7), float(vis) if vis not in (None, '', 0) else None, p.get('darcy_z') or None)
    pi_tot = c_tot = 0.0; dens = []
    for lay in layers:
        d = _layer(p, lay); kv = d['kh'] * d['kvkh']
        D = geometry_denominator(orient, d['kh'] * MD, kv * MD, d['h'], re, rw, d['skin'], regime, L, inc); dens.append(D)
        if fluid == 'gas':
            c_si = math.pi * d['kh'] * MD * d['h'] * T_SC / (P_SC * tk * mu * CP * z * D)       # m3/s per Pa^2
            c_tot += c_si * (BAR ** 2) * DAY
        else:
            pi_si = 2.0 * math.pi * d['kh'] * MD * d['h'] / (mu_o * CP * bo * D)                 # m3/s per Pa
            pi_tot += pi_si * BAR * DAY
    return {'pi': pi_tot, 'gas_c': c_tot, 'gas_n': 1.0, 'denominator': dens[0] if len(dens) == 1 else dens, 'warnings': warns, 'layers': len(layers),
            'orientation': orient, 'fluid': fluid, **({'mu_cp': mu, 'z': z} if fluid == 'gas' else {'mu_cp': mu_o, 'bo': bo})}
