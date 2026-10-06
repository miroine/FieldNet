"""Correlation-based black-oil / gas / water PVT with contaminants (CO2, H2S, N2) and calibration to lab data.

Replaces the fixed screening model (``physics.pvt.simple_black_oil``: Pb = 150 bar and Rsb = 120 Sm3/Sm3 whatever the fluid)
for any element whose ``params['pvt']`` selects ``model: 'correlation'``. Everything here is plain ``math`` (no numpy) because
it sits inside the hydraulic inner loops.

Units at the API: p [bar], T [degC], Rs [Sm3/Sm3], densities [kg/m3], viscosities [Pa.s]. Correlations are evaluated in their
native field units (psia, degF, scf/STB, cP) and converted.

Correlation sets (``FluidSpec``):
  pb / Rs : standing | vasquez_beggs | glaso | petrosky_farshad
  Bo      : standing | vasquez_beggs | glaso | petrosky_farshad     (undersaturated: Vasquez-Beggs compressibility)
  mu_od   : beggs_robinson | glaso | egbogah | beal               (live: Beggs-Robinson, undersaturated: Vasquez-Beggs)
  Z       : dak (Dranchuk-Abou-Kassem) | hall_yarborough | papay   (Sutton pseudo-criticals, Kay mixing of CO2/H2S/N2,
                                                                    Wichert-Aziz sour correction)
  mu_g    : Lee-Gonzalez-Eakin with Carr-Kobayashi-Burrows N2/CO2/H2S corrections
  water   : McCain Bw, density and viscosity with salinity

Contaminants: ``co2``, ``h2s``, ``n2`` are mole fractions of the *associated gas* (``gas_sg`` is the SG of that total gas).
They change the gas Z / viscosity / density, and the bubble point through Standing's contaminant factors. They do NOT model CO2
dissolving in oil (swelling, viscosity reduction): for CO2-rich systems calibrate to lab data (swelling test) instead.

Calibration (``Calibration``): pb_a/pb_b (Pb' = a Pb + b), rs_shape, bo_mult, co_mult, mu_mult, z_mult, mug_mult, bw_mult.
``calibrate()`` fits them to lab data; ``rank_correlations()`` scores each correlation against the lab data."""
from __future__ import annotations
import contextlib, contextvars, json, math
from dataclasses import dataclass, field, asdict, replace
from functools import lru_cache
from physics.pvt import BlackOilState

PB_CORRS = ('standing', 'vasquez_beggs', 'glaso', 'petrosky_farshad')
BO_CORRS = PB_CORRS
VISC_CORRS = ('beggs_robinson', 'glaso', 'egbogah', 'beal')
Z_CORRS = ('dak', 'hall_yarborough', 'papay')
PSI = 14.503773773
SCF_STB = 5.614583          # (scf/STB) per (Sm3/Sm3)
LB_FT3 = 16.01846337        # kg/m3 per lb/ft3
CP = 1e-3                   # Pa.s per cP
MW_AIR = 28.96
CONTAMINANT_SG = {'co2': 44.01 / MW_AIR, 'h2s': 34.08 / MW_AIR, 'n2': 28.013 / MW_AIR}
CONTAMINANT_CRIT = {'co2': (547.6, 1071.0), 'h2s': (672.4, 1306.0), 'n2': (227.3, 493.1)}   # Tc [R], pc [psia]


# ----------------------------------------------------------------------------- specification
@dataclass(frozen=True)
class Calibration:
    pb_a: float = 1.0; pb_b: float = 0.0        # Pb' = a * Pb_corr + b   [psia domain in b -> stored in bar]
    rs_shape: float = 1.0                        # Rs/Rsb = (Rs_corr(x Pb)/Rsb) ** shape
    bo_mult: float = 1.0                         # Bo - 1 scaled
    co_mult: float = 1.0                         # undersaturated oil compressibility
    mu_mult: float = 1.0                         # dead-oil viscosity
    z_mult: float = 1.0
    mug_mult: float = 1.0
    bw_mult: float = 1.0

    def as_dict(self): return asdict(self)


@dataclass(frozen=True)
class FluidSpec:
    api: float = 35.0
    gas_sg: float = 0.75
    rsb_sm3sm3: float = 100.0            # solution GOR at the bubble point (= producing GOR for a black oil)
    pb_bar: float | None = None          # measured bubble point at reservoir T (overrides the correlation)
    water_sg: float = 1.03
    salinity_wt_pct: float | None = None  # NaCl wt %; None -> derived from water_sg
    co2: float = 0.0; h2s: float = 0.0; n2: float = 0.0
    pb_corr: str = 'standing'; bo_corr: str = 'standing'; visc_corr: str = 'beggs_robinson'; z_corr: str = 'dak'
    cal: Calibration = field(default_factory=Calibration)

    def validate(self):
        errs = []
        if not 5 <= self.api <= 70: errs.append('API gravity should be 5-70')
        if not 0.5 <= self.gas_sg <= 2.0: errs.append('gas SG should be 0.5-2.0')
        if self.rsb_sm3sm3 < 0: errs.append('Rsb must be >= 0')
        for k in ('co2', 'h2s', 'n2'):
            if not 0 <= getattr(self, k) < 1: errs.append(f'{k} mole fraction must be in [0,1)')
        if self.co2 + self.h2s + self.n2 >= 0.95: errs.append('contaminants add up to >= 95 mol%')
        for k, allowed in (('pb_corr', PB_CORRS), ('bo_corr', BO_CORRS), ('visc_corr', VISC_CORRS), ('z_corr', Z_CORRS)):
            if getattr(self, k) not in allowed: errs.append(f'{k} must be one of {allowed}')
        if self.pb_bar is not None and self.pb_bar <= 0: errs.append('measured Pb must be > 0')
        return errs

    def to_dict(self):
        d = asdict(self); d['cal'] = self.cal.as_dict(); return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {}); cal = Calibration(**{k: float(v) for k, v in (d.pop('cal', None) or {}).items() if k in Calibration.__dataclass_fields__})
        keep = {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and k != 'cal'}
        for k in ('api', 'gas_sg', 'rsb_sm3sm3', 'water_sg', 'co2', 'h2s', 'n2'):
            if k in keep and keep[k] is not None: keep[k] = float(keep[k])
        for k in ('pb_bar', 'salinity_wt_pct'):
            if keep.get(k) in ('', None): keep[k] = None
            elif k in keep: keep[k] = float(keep[k])
        return cls(cal=cal, **keep)


# ----------------------------------------------------------------------------- oil correlations (field units)
def _gamma_o(api): return 141.5 / (131.5 + api)


def pb_standing(rs, t_f, api, g): return 18.2 * ((rs / g) ** 0.83 * 10 ** (0.00091 * t_f - 0.0125 * api) - 1.4)
def rs_standing(p, t_f, api, g): return g * ((p / 18.2 + 1.4) * 10 ** (0.0125 * api - 0.00091 * t_f)) ** 1.2048


def _vb_c(api): return (0.0362, 1.0937, 25.724) if api <= 30 else (0.0178, 1.187, 23.931)
def rs_vasquez_beggs(p, t_f, api, g):
    c1, c2, c3 = _vb_c(api); return c1 * g * p ** c2 * math.exp(c3 * api / (t_f + 460.0))
def pb_vasquez_beggs(rs, t_f, api, g):
    c1, c2, c3 = _vb_c(api); return (rs / (c1 * g * math.exp(c3 * api / (t_f + 460.0)))) ** (1.0 / c2)


def pb_glaso(rs, t_f, api, g):
    pbs = (rs / g) ** 0.816 * t_f ** 0.172 / api ** 0.989; lg = math.log10(max(pbs, 1e-9))
    return 10 ** (1.7669 + 1.7447 * lg - 0.30218 * lg * lg)
def rs_glaso(p, t_f, api, g):
    a, b, c = -0.30218, 1.7447, 1.7669 - math.log10(max(p, 1e-6)); disc = max(b * b - 4 * a * c, 0.0)
    lg = (-b + math.sqrt(disc)) / (2 * a)
    return g * (10 ** lg * api ** 0.989 / t_f ** 0.172) ** (1 / 0.816)


def _pf_x(t_f, api): return 7.916e-4 * api ** 1.5410 - 4.561e-5 * t_f ** 1.3911
def pb_petrosky(rs, t_f, api, g): return 112.727 * (rs ** 0.577421 / (g ** 0.8439 * 10 ** _pf_x(t_f, api)) - 12.340)
def rs_petrosky(p, t_f, api, g): return ((p / 112.727 + 12.340) * g ** 0.8439 * 10 ** _pf_x(t_f, api)) ** 1.73184

_PB = {'standing': pb_standing, 'vasquez_beggs': pb_vasquez_beggs, 'glaso': pb_glaso, 'petrosky_farshad': pb_petrosky}
_RS = {'standing': rs_standing, 'vasquez_beggs': rs_vasquez_beggs, 'glaso': rs_glaso, 'petrosky_farshad': rs_petrosky}


def bo_sat(corr, rs, t_f, api, g):
    go = _gamma_o(api)
    if corr == 'standing': return 0.9759 + 0.00012 * (rs * math.sqrt(g / go) + 1.25 * t_f) ** 1.2
    if corr == 'vasquez_beggs':
        c1, c2, c3 = (4.677e-4, 1.751e-5, -1.811e-8) if api <= 30 else (4.670e-4, 1.100e-5, 1.337e-9)
        return 1.0 + c1 * rs + (t_f - 60.0) * (api / g) * (c2 + c3 * rs)
    if corr == 'glaso':
        bs = rs * (g / go) ** 0.526 + 0.968 * t_f; lg = math.log10(max(bs, 1e-9))
        return 1.0 + 10 ** (-6.58511 + 2.91329 * lg - 0.27683 * lg * lg)
    if corr == 'petrosky_farshad':
        return 1.0113 + 7.2046e-5 * (rs ** 0.3738 * (g ** 0.2914 / go ** 0.6265) + 0.24626 * t_f ** 0.5371) ** 3.0936
    raise ValueError(corr)


def co_vasquez_beggs(p, rsb, t_f, api, g):
    """Undersaturated oil compressibility [1/psi]."""
    return max((-1433.0 + 5.0 * rsb + 17.2 * t_f - 1180.0 * g + 12.61 * api) / (1e5 * max(p, 1.0)), 1e-7)


def mu_dead(corr, t_f, api):
    if corr == 'beggs_robinson': return 10 ** (t_f ** -1.163 * math.exp(6.9824 - 0.04658 * api)) - 1.0
    if corr == 'glaso': return 3.141e10 * t_f ** -3.444 * math.log10(api) ** (10.313 * math.log10(t_f) - 36.447)
    if corr == 'egbogah':
        x = 1.8653 - 0.025086 * api - 0.5644 * math.log10(t_f); return 10 ** (10 ** x) - 1.0
    if corr == 'beal':
        a = 10 ** (0.43 + 8.33 / api); return (0.32 + 1.8e7 / api ** 4.53) * (360.0 / (t_f + 200.0)) ** a
    raise ValueError(corr)


def mu_live(mu_od, rs):
    a = 10.715 * (rs + 100.0) ** -0.515; b = 5.44 * (rs + 150.0) ** -0.338; return a * mu_od ** b


def mu_undersat(mu_ob, p, pb):
    m = 2.6 * p ** 1.187 * math.exp(-11.513 - 8.98e-5 * p); return mu_ob * (p / pb) ** m


# ----------------------------------------------------------------------------- gas
def gas_pseudocritical(g_total, co2=0.0, h2s=0.0, n2=0.0, sour_correction=True):
    """(Tpc [R], ppc [psia]) of the associated gas. g_total is the SG of the whole gas (incl. contaminants)."""
    yc = co2 + h2s + n2
    g_hc = (g_total - co2 * CONTAMINANT_SG['co2'] - h2s * CONTAMINANT_SG['h2s'] - n2 * CONTAMINANT_SG['n2']) / max(1.0 - yc, 1e-6)
    g_hc = max(g_hc, 0.55)
    tpc_h = 169.2 + 349.5 * g_hc - 74.0 * g_hc ** 2; ppc_h = 756.8 - 131.0 * g_hc - 3.6 * g_hc ** 2        # Sutton
    tpc = (1 - yc) * tpc_h; ppc = (1 - yc) * ppc_h
    for k, y in (('co2', co2), ('h2s', h2s), ('n2', n2)):
        tc, pc = CONTAMINANT_CRIT[k]; tpc += y * tc; ppc += y * pc
    if sour_correction and (co2 + h2s) > 0:                                                                 # Wichert-Aziz
        a = co2 + h2s; b = h2s
        eps = 120.0 * (a ** 0.9 - a ** 1.6) + 15.0 * (b ** 0.5 - b ** 4)
        ppc = ppc * (tpc - eps) / (tpc + b * (1 - b) * eps); tpc = tpc - eps
    return tpc, ppc


_DAK = (0.3265, -1.0700, -0.5339, 0.01569, -0.05165, 0.5475, -0.7361, 0.1844, 0.1056, 0.6134, 0.7210)


def z_dak(tr, pr):
    a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, a11 = _DAK
    tr = max(tr, 1.05); c1 = a1 + a2 / tr + a3 / tr ** 3 + a4 / tr ** 4 + a5 / tr ** 5; c2 = a6 + a7 / tr + a8 / tr ** 2; c3 = a9 * (a7 / tr + a8 / tr ** 2); c4 = a10 / tr ** 3
    rho = 0.27 * pr / tr
    for _ in range(60):
        r2 = rho * rho; e = math.exp(-a11 * r2)
        f = (1 + c1 * rho + c2 * r2 - c3 * rho ** 5 + c4 * (1 + a11 * r2) * r2 * e) * rho - 0.27 * pr / tr
        # numerical derivative: robust and cheap enough
        h = 1e-6 * max(rho, 1e-3); r2h = (rho + h) ** 2; eh = math.exp(-a11 * r2h)
        fh = (1 + c1 * (rho + h) + c2 * r2h - c3 * (rho + h) ** 5 + c4 * (1 + a11 * r2h) * r2h * eh) * (rho + h) - 0.27 * pr / tr
        d = (fh - f) / h
        if abs(d) < 1e-12: break
        new = rho - f / d
        if new <= 0: new = rho / 2
        if abs(new - rho) < 1e-10: rho = new; break
        rho = new
    return 0.27 * pr / (rho * tr)


def z_hall_yarborough(tr, pr):
    t = 1.0 / max(tr, 1.05); a = 0.06125 * t * math.exp(-1.2 * (1 - t) ** 2); b = 14.76 * t - 9.76 * t * t + 4.58 * t ** 3
    c = 90.7 * t - 242.2 * t * t + 42.4 * t ** 3; d = 2.18 + 2.82 * t
    y = min(max(a * pr / 3.5, 1e-4), 0.6)
    for _ in range(80):
        f = -a * pr + (y + y * y + y ** 3 - y ** 4) / (1 - y) ** 3 - b * y * y + c * y ** d
        df = (1 + 4 * y + 4 * y * y - 4 * y ** 3 + y ** 4) / (1 - y) ** 4 - 2 * b * y + c * d * y ** (d - 1)
        new = y - f / df
        new = min(max(new, 1e-6), 0.95)
        if abs(new - y) < 1e-10: y = new; break
        y = new
    return a * pr / y


def z_papay(tr, pr):
    return 1.0 - 3.52 * pr / (10 ** (0.9813 * tr)) + 0.274 * pr * pr / (10 ** (0.8157 * tr))


_Z = {'dak': z_dak, 'hall_yarborough': z_hall_yarborough, 'papay': z_papay}


def gas_z(p_bar, t_c, gas_sg=0.75, co2=0.0, h2s=0.0, n2=0.0, method='dak'):
    """Real-gas Z for an associated gas with contaminants (stand-alone helper; also used by the thermal model)."""
    tpc, ppc = gas_pseudocritical(gas_sg, co2, h2s, n2)
    z = _Z[method]((t_c * 1.8 + 491.67) / tpc, p_bar * PSI / ppc)
    return min(max(z, 0.25), 2.0)


def gas_viscosity_cp(p_bar, t_c, z, gas_sg, co2=0.0, h2s=0.0, n2=0.0):
    t_r = t_c * 1.8 + 491.67; m = MW_AIR * gas_sg
    rho_gcc = (p_bar * PSI) * m / (10.732 * t_r * z) * 0.01601846   # lb/ft3 -> g/cc
    def lge(rho):
        k = (9.379 + 0.0160 * m) * t_r ** 1.5 / (209.2 + 19.26 * m + t_r); x = 3.448 + 986.4 / t_r + 0.01009 * m; y = 2.447 - 0.2224 * x
        return 1e-4 * k * math.exp(min(x * min(max(rho, 0.0), 1.2) ** y, 50.0))   # clamp: solver trial states can be far outside the correlation range
    base = lge(rho_gcc); atm = lge(0.0 + 1e-6 * m)
    lg = math.log10(max(gas_sg, 1e-3))
    corr = n2 * (8.48e-3 * lg + 9.59e-3) + co2 * (9.08e-3 * lg + 6.24e-3) + h2s * (8.49e-3 * lg + 3.73e-3)
    return (base / atm) * (atm + corr) if atm > 0 else base


# ----------------------------------------------------------------------------- water (McCain)
def salinity_from_sg(sg): return max(0.0, (sg - 1.0) / 0.0073)


def water_props(p_bar, t_c, salinity_wt_pct, bw_mult=1.0):
    """(Bw, rho_w [kg/m3], mu_w [Pa.s])."""
    t = t_c * 1.8 + 32.0; p = p_bar * PSI; s = salinity_wt_pct
    dvt = -1.0001e-2 + 1.33391e-4 * t + 5.50654e-7 * t * t
    dvp = -1.95301e-9 * p * t - 1.72834e-13 * p * p * t - 3.58922e-7 * p - 2.25341e-10 * p * p
    bw = 1.0 + bw_mult * ((1 + dvt) * (1 + dvp) - 1.0)
    rho_sc = (62.368 + 0.438603 * s + 1.60074e-3 * s * s) * LB_FT3
    a = 109.574 - 8.40564 * s + 0.313314 * s * s + 8.72213e-3 * s ** 3
    b = -1.12166 + 2.63951e-2 * s - 6.79461e-4 * s * s - 5.47119e-5 * s ** 3 + 1.55586e-6 * s ** 4
    mu1 = a * max(t, 40.0) ** b; mu = mu1 * (0.9994 + 4.0295e-5 * p + 3.1062e-9 * p * p)
    return bw, rho_sc / bw, max(mu, 0.05) * CP


# ----------------------------------------------------------------------------- fluid model
class FluidModel:
    """Evaluates the full PVT of one fluid at (p, T). Instances are immutable in practice and cached by ``fluid_from_dict``."""

    def __init__(self, spec: FluidSpec):
        errs = spec.validate()
        if errs: raise ValueError('; '.join(errs))
        self.spec = spec; self._tcache = {}

    # -- contaminants -> bubble-point factor (Standing 1981)
    def _pb_contaminant_factor(self, t_f):
        s = self.spec; api = s.api; c = 1.0
        if s.n2 > 0:
            c *= 1 + ((-2.65e-4 * api + 5.5e-3) * t_f + (0.0931 * api - 0.8295)) * s.n2 + ((1.954e-11 * api ** 4.699) * t_f + (0.027 * api - 2.366)) * s.n2 ** 2
        if s.co2 > 0: c *= 1 - 693.8 * s.co2 * t_f ** -1.553
        if s.h2s > 0: c *= 1 - (0.9035 + 0.0015 * api) * s.h2s + 0.019 * (45 - api) * s.h2s ** 2
        return max(c, 0.2)

    def _at_t(self, t_c):
        r = self._tcache.get(t_c)
        if r is not None: return r
        s = self.spec; t_f = t_c * 1.8 + 32.0; rsb = s.rsb_sm3sm3 * SCF_STB; g = s.gas_sg
        pb_std = max(_PB[s.pb_corr](rsb, t_f, s.api, g), 15.0) if rsb > 0 else 14.7      # psia
        pb_corr = pb_std * self._pb_contaminant_factor(t_f)
        if s.pb_bar is not None: pb = s.pb_bar * PSI
        else: pb = max(s.cal.pb_a * pb_corr + s.cal.pb_b * PSI, 15.0)
        bob_corr = bo_sat(s.bo_corr, rsb, t_f, s.api, g); bob = 1.0 + s.cal.bo_mult * (bob_corr - 1.0)
        mu_od = max(mu_dead(s.visc_corr, t_f, s.api), 0.05) * s.cal.mu_mult
        r = (t_f, rsb, pb_std, pb, bob, mu_od)
        if len(self._tcache) > 64: self._tcache.clear()
        self._tcache[t_c] = r; return r

    def bubble_point_bar(self, t_c): return self._at_t(t_c)[3] / PSI

    def rs(self, p_bar, t_c):
        t_f, rsb, pb_std, pb, _bob, _m = self._at_t(t_c); p = p_bar * PSI
        if rsb <= 0: return 0.0
        if p >= pb: return rsb / SCF_STB
        x = max(p / pb, 1e-6); h = _RS[self.spec.pb_corr](x * pb_std, t_f, self.spec.api, self.spec.gas_sg) / max(_RS[self.spec.pb_corr](pb_std, t_f, self.spec.api, self.spec.gas_sg), 1e-9)
        h = min(max(h, 0.0), 1.0)
        return rsb * h ** self.spec.cal.rs_shape / SCF_STB

    def _z(self, p_bar, t_c):
        s = self.spec; return min(max(gas_z(p_bar, t_c, s.gas_sg, s.co2, s.h2s, s.n2, s.z_corr) * s.cal.z_mult, 0.25), 2.0)

    def state(self, p_bar, t_c) -> BlackOilState:
        s = self.spec; p_bar = max(float(p_bar), 1.0); t_c = float(t_c)
        t_f, rsb, pb_std, pb, bob, mu_od = self._at_t(t_c); p = p_bar * PSI
        rs_sc = self.rs(p_bar, t_c); rs = rs_sc * SCF_STB; g = s.gas_sg; go = _gamma_o(s.api)
        if p < pb and rsb > 0:
            bo_corr = bo_sat(s.bo_corr, rs, t_f, s.api, g); bo = 1.0 + s.cal.bo_mult * (bo_corr - 1.0)
            mu = mu_live(mu_od, rs)
        else:
            co = co_vasquez_beggs(max(p, pb), rsb, t_f, s.api, g) * s.cal.co_mult
            bo = bob * math.exp(-min(max(co * (p - pb), 0.0), 2.0)); mu = mu_undersat(mu_live(mu_od, rsb), p, pb)
        rho_o = (62.4 * go + 0.0136 * rs * g) / max(bo, 0.2) * LB_FT3
        z = self._z(p_bar, t_c); m = MW_AIR * s.gas_sg
        rho_g = p_bar * 1e5 * m * 1e-3 / (z * 8.314462 * (t_c + 273.15))
        mu_g = gas_viscosity_cp(p_bar, t_c, z, g, s.co2, s.h2s, s.n2) * s.cal.mug_mult * CP
        sal = s.salinity_wt_pct if s.salinity_wt_pct is not None else salinity_from_sg(s.water_sg)
        bw, rho_w, mu_w = water_props(p_bar, t_c, sal, s.cal.bw_mult)
        return BlackOilState(p_bar, t_c, max(rho_o, 300.0), rho_w, max(rho_g, 0.05), max(mu * CP, 1e-4 * CP), mu_w, max(mu_g, 5e-6), rs_sc, max(bo, 1.0), z)

    def table(self, pressures_bar, t_c):
        rows = []
        for p in pressures_bar:
            st = self.state(p, t_c); pb = self.bubble_point_bar(t_c)
            bg = st.gas_z * (t_c + 273.15) / 288.15 * 1.01325 / p       # rm3/Sm3
            rows.append({'Pressure [bar]': p, 'Rs [Sm3/Sm3]': st.solution_gor_sm3sm3, 'Bo [rm3/Sm3]': st.oil_fvf, 'Oil density [kg/m3]': st.oil_density_kgm3,
                         'Oil viscosity [cP]': st.oil_viscosity_pas / CP, 'Z': st.gas_z, 'Bg [rm3/Sm3]': bg, 'Gas density [kg/m3]': st.gas_density_kgm3,
                         'Gas viscosity [cP]': st.gas_viscosity_pas / CP, 'Bw [rm3/Sm3]': water_props(p, t_c, self.spec.salinity_wt_pct if self.spec.salinity_wt_pct is not None else salinity_from_sg(self.spec.water_sg), self.spec.cal.bw_mult)[0],
                         'Water viscosity [cP]': st.water_viscosity_pas / CP, 'Pb [bar]': pb})
        return rows


# ----------------------------------------------------------------------------- element integration
_active: contextvars.ContextVar = contextvars.ContextVar('fieldnet_active_fluid', default=None)


def current_fluid(): return _active.get()


@lru_cache(maxsize=256)
def _fluid_from_json(js: str) -> FluidModel:
    return FluidModel(FluidSpec.from_dict(json.loads(js)))


def fluid_from_params(prm, gor_sm3sm3=None, api=None, gas_sg=None):
    """FluidModel for an element's params (``params['pvt']``), or None for the legacy screening model."""
    cfg = (prm or {}).get('pvt')
    if not isinstance(cfg, dict) or str(cfg.get('model', 'legacy')).lower() != 'correlation': return None
    d = dict(cfg); d.pop('model', None)
    if d.get('rsb_sm3sm3') in (None, ''): d['rsb_sm3sm3'] = float(gor_sm3sm3 if gor_sm3sm3 is not None else (prm or {}).get('gor_sm3sm3', 100.0))
    if d.get('api') in (None, ''): d['api'] = float(api if api is not None else (prm or {}).get('api', 35.0))
    if d.get('gas_sg') in (None, ''): d['gas_sg'] = float(gas_sg if gas_sg is not None else (prm or {}).get('gas_sg', 0.75))
    try: return _fluid_from_json(json.dumps(d, sort_keys=True, default=float))
    except ValueError: return None


@contextlib.contextmanager
def use_fluid(fluid):
    """Make ``fluid`` the PVT used by ``physics.multiphase.mixture_properties`` inside the block (None keeps the legacy model)."""
    tok = _active.set(fluid)
    try: yield fluid
    finally: _active.reset(tok)


def fluid_scope(prm, gor_sm3sm3=None, api=None, gas_sg=None):
    """Context manager for an element: legacy (no-op) unless its params select the correlation model."""
    if not (prm or {}).get('pvt'): return contextlib.nullcontext()
    return use_fluid(fluid_from_params(prm, gor_sm3sm3, api, gas_sg))


# ----------------------------------------------------------------------------- calibration
POINT_KEYS = {'rs': 'Rs [Sm3/Sm3]', 'bo': 'Bo [rm3/Sm3]', 'mu_o': 'Oil viscosity [cP]', 'z': 'Z', 'mu_g': 'Gas viscosity [cP]'}


def _mape(pairs):
    pairs = [(m, l) for m, l in pairs if l is not None and l == l and l != 0]
    return (100.0 * sum(abs(m - l) / abs(l) for m, l in pairs) / len(pairs)) if pairs else None


def lab_points(lab):
    """Normalise lab data -> list of {'p_bar', 'rs','bo','mu_o','z','mu_g'} dicts (missing = None) plus single-value Pb / Bo@Pb / mu@Pb."""
    rows = []
    for r in (lab.get('table') or []):
        p = r.get('p_bar', r.get('Pressure [bar]'))
        if p in (None, '') or p != p: continue
        row = {'p_bar': float(p)}
        for k, col in POINT_KEYS.items():
            v = r.get(k, r.get(col)); row[k] = None if v in (None, '') or v != v else float(v)
        rows.append(row)
    pb = lab.get('pb_bar')
    if pb not in (None, ''):
        pb = float(pb); extra = {'p_bar': pb, 'rs': lab.get('rsb'), 'bo': lab.get('bo_pb'), 'mu_o': lab.get('mu_o_pb_cp'), 'z': None, 'mu_g': None}
        extra = {k: (None if v in (None, '') else float(v)) for k, v in extra.items()}
        if not any(abs(r['p_bar'] - pb) < 1e-6 for r in rows): rows.append(extra)
        else:
            for r in rows:
                if abs(r['p_bar'] - pb) < 1e-6:
                    for k, v in extra.items():
                        if r.get(k) is None and v is not None: r[k] = v
    return sorted(rows, key=lambda r: r['p_bar'])


def evaluate(spec: FluidSpec, lab, t_c):
    """Per-property mean absolute % error of ``spec`` against lab data."""
    fm = FluidModel(spec); pts = lab_points(lab); out = {}
    for k in POINT_KEYS:
        pairs = []
        for r in pts:
            if r.get(k) is None: continue
            st = fm.state(r['p_bar'], t_c)
            m = {'rs': st.solution_gor_sm3sm3, 'bo': st.oil_fvf, 'mu_o': st.oil_viscosity_pas / CP, 'z': st.gas_z, 'mu_g': st.gas_viscosity_pas / CP}[k]
            pairs.append((m, r[k]))
        out[k] = (_mape(pairs), len(pairs))
    if lab.get('pb_bar') not in (None, ''):
        out['pb'] = (_mape([(fm.bubble_point_bar(t_c), float(lab['pb_bar']))]), 1)
    return out


def calibrate(spec: FluidSpec, lab: dict, t_c: float):
    """Fit the calibration multipliers to lab data. Returns (calibrated FluidSpec, report rows, notes).

    lab = {'pb_bar', 'rsb' (Sm3/Sm3), 'bo_pb', 'mu_o_pb_cp', 'table': [{p_bar, rs, bo, mu_o, z, mu_g}, ...]} - all optional.
    Order: Rsb and Pb -> Rs shape -> Bo -> undersaturated compressibility -> viscosity -> Z -> gas viscosity. Measured values are never
    modified; the correlation is shifted onto them."""
    from scipy.optimize import minimize_scalar
    notes = []; pts = lab_points(lab); before = evaluate(spec, lab, t_c)
    s = replace(spec, cal=Calibration())
    if lab.get('rsb') not in (None, ''): s = replace(s, rsb_sm3sm3=float(lab['rsb'])); notes.append(f"Rsb set to measured {float(lab['rsb']):.1f} Sm3/Sm3")
    cal = {}
    if lab.get('pb_bar') not in (None, ''):
        pb_corr = FluidModel(replace(s, pb_bar=None))._at_t(t_c)[3] / PSI
        cal['pb_a'] = float(lab['pb_bar']) / pb_corr; notes.append(f"Pb: correlation {pb_corr:.1f} bar -> measured {float(lab['pb_bar']):.1f} bar (multiplier {cal['pb_a']:.3f})")
        s = replace(s, cal=Calibration(**cal))
    def refit(name, lo, hi, loss):
        nonlocal s
        res = minimize_scalar(lambda v: loss(replace(s, cal=Calibration(**{**s.cal.as_dict(), name: v}))), bounds=(lo, hi), method='bounded', options={'xatol': 1e-5})
        cal[name] = float(res.x); s = replace(s, cal=Calibration(**{**s.cal.as_dict(), name: float(res.x)}))
    def loss_for(key):
        def f(sp):
            fm = FluidModel(sp); tot = 0.0; n = 0
            for r in pts:
                if r.get(key) is None: continue
                st = fm.state(r['p_bar'], t_c)
                m = {'rs': st.solution_gor_sm3sm3, 'bo': st.oil_fvf, 'mu_o': st.oil_viscosity_pas / CP, 'z': st.gas_z, 'mu_g': st.gas_viscosity_pas / CP}[key]
                tot += ((m - r[key]) / r[key]) ** 2; n += 1
            return tot / max(n, 1)
        return f
    has = lambda k: any(r.get(k) is not None for r in pts)
    if sum(1 for r in pts if r.get('rs') is not None) >= 3: refit('rs_shape', 0.4, 2.5, loss_for('rs')); notes.append(f"Rs(p) curve shape {cal['rs_shape']:.3f}")
    if has('bo'):
        refit('bo_mult', 0.3, 3.0, loss_for('bo')); notes.append(f"Bo - 1 multiplier {cal['bo_mult']:.3f}")
        pb_now = FluidModel(s).bubble_point_bar(t_c)
        if sum(1 for r in pts if r.get('bo') is not None and r['p_bar'] > pb_now * 1.02) >= 2:
            refit('co_mult', 0.2, 5.0, loss_for('bo')); notes.append(f"Undersaturated compressibility multiplier {cal['co_mult']:.3f}")
    if has('mu_o'): refit('mu_mult', 0.1, 10.0, loss_for('mu_o')); notes.append(f"Dead-oil viscosity multiplier {cal['mu_mult']:.3f}")
    if has('z'): refit('z_mult', 0.7, 1.3, loss_for('z')); notes.append(f"Z multiplier {cal['z_mult']:.3f}")
    if has('mu_g'): refit('mug_mult', 0.3, 3.0, loss_for('mu_g')); notes.append(f"Gas viscosity multiplier {cal['mug_mult']:.3f}")
    after = evaluate(s, lab, t_c)
    names = {'pb': 'Bubble point', 'rs': 'Rs', 'bo': 'Bo', 'mu_o': 'Oil viscosity', 'z': 'Z factor', 'mu_g': 'Gas viscosity'}
    rows = [{'Property': names[k], 'Points': before[k][1], 'Error before [%]': before[k][0], 'Error after [%]': after[k][0]} for k in names if k in before and before[k][1]]
    if not rows: notes.append('No lab values supplied - nothing was calibrated.')
    return s, rows, notes


def rank_correlations(spec: FluidSpec, lab: dict, t_c: float):
    """Score every correlation against the lab data (uncalibrated). Returns a list of rows sorted by error within each property."""
    base = replace(spec, cal=Calibration()); pts = lab_points(lab); rows = []
    if lab.get('rsb') not in (None, ''): base = replace(base, rsb_sm3sm3=float(lab['rsb']))
    if lab.get('pb_bar') not in (None, ''):
        for c in PB_CORRS:
            e = _mape([(FluidModel(replace(base, pb_corr=c, pb_bar=None)).bubble_point_bar(t_c), float(lab['pb_bar']))]); rows.append({'Property': 'Bubble point', 'Correlation': c, 'Error [%]': e})
    pinned = replace(base, pb_bar=float(lab['pb_bar'])) if lab.get('pb_bar') not in (None, '') else base
    for prop, key, attr, corrs in (('Bo', 'bo', 'bo_corr', BO_CORRS), ('Rs', 'rs', 'pb_corr', PB_CORRS), ('Oil viscosity', 'mu_o', 'visc_corr', VISC_CORRS), ('Z factor', 'z', 'z_corr', Z_CORRS)):
        if not any(r.get(key) is not None for r in pts): continue
        for c in corrs:
            sp = replace(pinned, **{attr: c}); e = evaluate(sp, lab, t_c).get(key, (None, 0))[0]; rows.append({'Property': prop, 'Correlation': c, 'Error [%]': e})
    order = {}
    for r in rows: order.setdefault(r['Property'], []).append(r)
    out = []
    for prop, lst in order.items():
        lst = sorted(lst, key=lambda r: (r['Error [%]'] is None, r['Error [%]'] if r['Error [%]'] is not None else 0));
        for i, r in enumerate(lst): out.append({**r, 'Rank': i + 1})
    return out
