"""Multiphase pressure-drop correlation registry for FieldNet (tubing VLP and flowlines).

Every correlation shares the signature of ``beggs_brill_dp_bar``::

    fn(q_liq_m3d, L_m, D_m, eps_m, dz_m, p_bar, T_c, water_cut, gor_sm3sm3, api, gas_sg,
       water_sg=1.03, extra_free_gas_sm3d=0.0) -> (dp_bar, props)

(``water_sg`` is kept in the 12th position so that the functions are drop-in replacements for
``beggs_brill_dp_bar`` / ``homogeneous_dp_bar``; pass ``extra_free_gas_sm3d`` by keyword.)

Conventions (identical to Beggs-Brill in ``physics/beggs_brill.py``):
 * ``q_liq`` > 0 is flow along the segment direction; ``q_liq`` < 0 reverses the flow (friction sign
   flips, inclination is taken relative to the flow direction, the gravity term still uses the given
   ``dz``). ``q_liq`` == 0 returns the (almost) static-column gravity term without error.
 * ``dz`` is the elevation GAIN along the segment direction (tubing marched downward: dz = +L).
 * ``dp_bar`` is the pressure LOSS along the flow direction: friction + gravity, acceleration ignored.
 * PVT, gas/liquid rates and surface tension (0.025 N/m) come from the same helpers as Beggs-Brill, so
   all correlations see identical fluids.

All functions are pure-float/``math`` code (no numpy) for speed. Smoothing between flow regimes is
done with smoothstep blends (documented per correlation) so least-squares network solves see a
continuous residual. Constants labelled "implementation choice" are NOT published values.

Validation status (see tests/test_correlations.py): none of the correlations could be checked against a
published worked numerical example in this environment (no access to the original papers/books); they are
checked for physical limits, monotonicity, robustness and cross-correlation consistency only.
"""
import math
from physics.multiphase import mixture_properties, homogeneous_dp_bar
from physics.beggs_brill import beggs_brill_dp_bar
from physics.hydraulics import friction_factor, G
from physics.units import pa_to_bar

_SIGMA = 0.025          # N/m gas/liquid surface tension (same screening constant as beggs_brill.py)
_PA_BAR = 1.01325       # atmospheric pressure [bar]
_FT = 0.3048
_HW = 0.35   # half-width (in ln of the controlling variable) of the Hasan-Kabir regime blends
_pi = math.pi
_sqrt = math.sqrt
_log = math.log
_exp = math.exp


def _ss(x):
    """Smoothstep 0..1."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return x * x * (3.0 - 2.0 * x)


def _ctx(q, L, D, dz, p, T, wc, gor, api, gsg, wsg, extra):
    """Shared prelude: PVT, liquid properties, velocities, inclination relative to the flow."""
    if D <= 0 or L < 0:
        raise ValueError('Invalid pipe geometry')
    pr = mixture_properties(q, wc, gor, p, T, api, gsg, wsg, extra)
    st = pr['state']
    wcc = min(max(float(wc), 0.0), 0.9999)
    qo = (1.0 - wcc) * st.oil_fvf
    fw = wcc / (qo + wcc)
    rho_l = (1.0 - fw) * st.oil_density_kgm3 + fw * st.water_density_kgm3
    mu_l = (1.0 - fw) * st.oil_viscosity_pas + fw * st.water_viscosity_pas
    vm = pr['q_line_m3s'] / (_pi * D * D / 4.0)
    lam_l = min(1.0, max(0.0, pr['liquid_holdup']))
    lam_g = 1.0 - lam_l
    if L > 0:
        s = dz / L
        theta = math.asin(1.0 if s > 1.0 else (-1.0 if s < -1.0 else s))
    else:
        theta = 0.0
    if q < 0:
        theta = -theta
    return pr, st, rho_l, mu_l, vm, lam_l, lam_g, theta


def _out(q, L, D, eps, dz, pr, st, vm, hl, regime, rho_fric, rho_re, mu_re, lam_l, extra=None, rho_l=None):
    """Assemble dp and props from holdup and the density to use in the friction term."""
    hl = min(1.0, max(1e-9, hl))
    rho_s = hl * rho_l + (1.0 - hl) * st.gas_density_kgm3
    re = max(1.0, rho_re * abs(vm) * D / mu_re)
    f = friction_factor(re, eps / D)
    fr = f * (L / D) * (rho_fric * vm * vm / 2.0)
    hydro = rho_s * G * dz
    dp = pa_to_bar((fr if q >= 0 else -fr) + hydro)
    if dp != dp or dp in (math.inf, -math.inf):  # last-resort guard: never hand NaN to the solver
        return homogeneous_dp_bar(q, L, D, eps, dz, st.pressure_bar, st.temperature_c)
    props = {**pr, 'liquid_holdup': hl, 'no_slip_holdup': lam_l, 'void_fraction': 1.0 - hl, 'flow_regime': regime,
             'mixture_velocity_ms': vm, 'reynolds': re, 'friction_factor': f}
    if extra:
        props.update(extra)
    return dp, props


# --------------------------------------------------------------------------------------------------
# 1. Hagedorn-Brown (modified, Brill-Mukherjee curve fits + Griffith bubble flow)
# --------------------------------------------------------------------------------------------------
def _hb_holdup(vsl, vsg, D, p_bar, rho_l, mu_l):
    """Hagedorn-Brown liquid holdup from the curve-fit charts (SI evaluation of the field-unit groups)."""
    nlv = vsl * (rho_l / (G * _SIGMA)) ** 0.25
    ngv = max(vsg * (rho_l / (G * _SIGMA)) ** 0.25, 1e-12)
    nd = D * _sqrt(rho_l * G / _SIGMA)
    nl = mu_l * (G / (rho_l * _SIGMA ** 3)) ** 0.25
    nlc = min(max(nl, 0.002), 0.4)
    cnl = 0.0019 + 0.0505 * nlc - 0.0929 * nlc ** 2 + 0.061 * nlc ** 3
    x = max(nlv / ngv ** 0.575 * (p_bar / _PA_BAR) ** 0.1 * cnl / nd, 1e-12)
    hl_psi = _sqrt((0.0047 + 1123.32 * x + 729489.64 * x * x) / (1.0 + 1097.1566 * x + 722153.97 * x * x))
    b = ngv * max(nlv, 1e-12) ** 0.38 / nd ** 2.14
    if b <= 0.025:
        psi = 27170.0 * b ** 3 - 317.52 * b ** 2 + 0.5472 * b + 0.9999
    elif b <= 0.055:
        psi = -533.33 * b * b + 58.524 * b + 0.1171
    else:
        psi = 2.5714 * b + 1.5962
    return hl_psi * psi


def hagedorn_brown_dp_bar(liquid_rate_m3d, length_m, diameter_m, roughness_m, dz_m, pressure_bar, temperature_c,
                          water_cut=0.0, gor_sm3sm3=0.0, api=35.0, gas_sg=0.75, water_sg=1.03,
                          extra_free_gas_sm3d=0.0):
    """Modified Hagedorn-Brown (1965) with Griffith (1962) bubble-flow criterion.

    References: Hagedorn & Brown, JPT 17(4):475-484 (1965); Griffith & Wallis (1961) / Griffith (1962) for
    bubble flow; curve-fit chart correlations (CNL, HL/psi, psi) as tabulated in Brill & Mukherjee,
    "Multiphase Flow in Wells", SPE Monograph 17 (1999), ch. 3, as reproduced by Pengtools/whitson wiki.

    Method: bubble flow if lambda_g < LB = max(1.071 - 0.2218 vm^2/D [ft], 0.13) -> Griffith slip-velocity
    holdup (vs = 0.8 ft/s), friction on the liquid velocity; otherwise HB holdup with the modified
    condition HL >= lambda_L, friction gradient f * rho_ns^2 vm^2 / (2 D rho_s). Acceleration neglected.
    The two methods are blended over lambda_g in [0.9, 1.1] LB (implementation choice) to keep the residual
    continuous.

    Inclination: HB is a vertical-flow correlation. Uphill and horizontal segments use the vertical holdup
    unchanged (as most commercial codes do); downhill segments (flow direction downward) fall back to the
    no-slip holdup. CNL is the cubic fit clamped to NL in [0.002, 0.4].

    Status: sanity-checked only (curve-fit transcription checked for continuity of the psi pieces),
    not validated against published data.
    """
    q = liquid_rate_m3d
    pr, st, rho_l, mu_l, vm, lam_l, lam_g, theta = _ctx(q, length_m, diameter_m, dz_m, pressure_bar, temperature_c,
                                                         water_cut, gor_sm3sm3, api, gas_sg, water_sg,
                                                         extra_free_gas_sm3d)
    D = diameter_m
    L = length_m
    rho_g = st.gas_density_kgm3
    mu_g = st.gas_viscosity_pas
    vsl = lam_l * vm
    vsg = lam_g * vm
    lb = max(1.071 - 0.2218 * (vm / _FT) ** 2 / (D / _FT), 0.13)
    wb = 1.0 - _ss((lam_g - 0.9 * lb) / (0.2 * lb))  # weight of the Griffith (bubble) branch
    hl_b = hl_h = None
    if wb > 0.0:
        vs = 0.8 * _FT
        a = 1.0 + vm / vs
        hl_b = 1.0 - 0.5 * (a - _sqrt(max(a * a - 4.0 * vsg / vs, 0.0)))
        if theta < 0:
            hl_b = lam_l
        hl_b = min(1.0, max(lam_l, hl_b, 1e-9))
    if wb < 1.0:
        hl_h = _hb_holdup(vsl, vsg, D, pressure_bar, rho_l, mu_l)
        if theta < 0:
            hl_h = lam_l
        hl_h = min(1.0, max(lam_l, hl_h, 1e-9))
    rho_ns = pr['rho']
    mu_ns = pr['mu']
    reg = 'bubble(Griffith)' if wb >= 0.5 else 'Hagedorn-Brown'

    def part(hl, griffith):
        rho_s = hl * rho_l + (1.0 - hl) * rho_g
        if griffith:
            vl = vsl / hl
            re = max(1.0, rho_l * vl * D / mu_l)
            f = friction_factor(re, roughness_m / D)
            fr = f * (L / D) * rho_l * vl * vl / 2.0
        else:
            re = max(1.0, rho_ns * vm * D / (mu_l ** hl * mu_g ** (1.0 - hl)))
            f = friction_factor(re, roughness_m / D)
            fr = f * (L / D) * rho_ns * rho_ns * vm * vm / (2.0 * rho_s)
        return fr, rho_s * G * dz_m, f, re

    if hl_b is not None and hl_h is not None:
        fb, hb_, f1, r1 = part(hl_b, True)
        fh, hh_, f2, r2 = part(hl_h, False)
        fr = wb * fb + (1 - wb) * fh
        hyd = wb * hb_ + (1 - wb) * hh_
        hl = wb * hl_b + (1 - wb) * hl_h
        f = wb * f1 + (1 - wb) * f2
        re = wb * r1 + (1 - wb) * r2
    elif hl_b is not None:
        fr, hyd, f, re = part(hl_b, True)
        hl = hl_b
    else:
        fr, hyd, f, re = part(hl_h, False)
        hl = hl_h
    dp = pa_to_bar((fr if q >= 0 else -fr) + hyd)
    if dp != dp or dp in (math.inf, -math.inf):
        return homogeneous_dp_bar(q, L, D, roughness_m, dz_m, pressure_bar, temperature_c)
    return dp, {**pr, 'liquid_holdup': hl, 'no_slip_holdup': lam_l, 'void_fraction': 1.0 - hl, 'flow_regime': reg,
                'mixture_velocity_ms': vm, 'reynolds': re, 'friction_factor': f}


# --------------------------------------------------------------------------------------------------
# 2. Gray (1974)
# --------------------------------------------------------------------------------------------------
def gray_dp_bar(liquid_rate_m3d, length_m, diameter_m, roughness_m, dz_m, pressure_bar, temperature_c,
                water_cut=0.0, gor_sm3sm3=0.0, api=35.0, gas_sg=0.75, water_sg=1.03,
                extra_free_gas_sm3d=0.0):
    """Gray (1974) correlation for vertical gas/condensate wells with small liquid loading.

    References: H.E. Gray, "Vertical Flow Correlation in Gas Wells", in User Manual for API 14B Subsurface
    Controlled Safety Valve Sizing Computer Program, API (1974); Brill & Mukherjee (1999) sec. 3.x. Equations as
    tabulated by Pengtools/IHS-Piper documentation:
      Nv = rho_ns^2 vm^4 /(g sigma (rho_L-rho_g)),  ND = g (rho_L-rho_g) D^2 / sigma,  R = vsl/vsg
      B  = 0.0814 [1 - 0.0554 ln(1 + 730 R/(R+1))],  A = -2.314 [Nv (1+205/ND)]^B
      Hg = (1 - exp(A)) / (R+1),  HL = 1 - Hg
      k0 = 28.5 sigma/(rho_ns vm^2);  ke = k0 for R >= 0.007, else k + R (k0-k)/0.007; ke >= 2.77e-5 ft
      gravity with slip density, friction with the no-slip density.
    The leading coefficient of A is printed as 2.314 in the classical references; one on-line transcription shows
    2.2314 (obvious typo) and a different grouping with 0.2314 - 2.314 is used here. The ke minimum is assumed to
    be in ft (8.4e-6 m).

    Implementation choices (not in Gray's paper): (i) HL is faded to the no-slip value as lambda_L -> 0 over
    lambda_L < 2e-4 (Gray's formula returns exp(A) ~ 0.4% holdup even with no liquid); (ii) the pseudo roughness
    is blended back to the pipe roughness as lambda_g -> 0 over lambda_g < 0.1 so the single-phase-liquid limit is
    plain Darcy-Weisbach; (iii) downhill segments use the no-slip holdup. Relative roughness is capped at 0.05.
    Valid range per Gray: vm < 50 ft/s (15 m/s), D < 3.5 in, condensate < 50 bbl/MMscf, water < 5 bbl/MMscf; outside it
    the results are an extrapolation.

    Status: sanity-checked only, not validated against published data.
    """
    q = liquid_rate_m3d
    pr, st, rho_l, mu_l, vm, lam_l, lam_g, theta = _ctx(q, length_m, diameter_m, dz_m, pressure_bar, temperature_c,
                                                         water_cut, gor_sm3sm3, api, gas_sg, water_sg,
                                                         extra_free_gas_sm3d)
    D = diameter_m
    L = length_m
    rho_g = st.gas_density_kgm3
    rho_ns = pr['rho']
    drho = max(rho_l - rho_g, 1.0)
    vsl = lam_l * vm
    vsg = lam_g * vm
    nv = rho_ns * rho_ns * vm ** 4 / (G * _SIGMA * drho)
    nd = G * drho * D * D / _SIGMA
    R = vsl / max(vsg, 1e-30)
    Rr = min(R, 1e12)
    B = 0.0814 * (1.0 - 0.0554 * _log(1.0 + 730.0 * Rr / (Rr + 1.0)))
    A = -2.314 * (nv * (1.0 + 205.0 / nd)) ** B
    hl = 1.0 - (1.0 - _exp(A)) * lam_g
    hl = lam_l + (hl - lam_l) * _ss(lam_l / 2e-4)
    if theta < 0:
        hl = lam_l
    hl = min(1.0, max(lam_l, hl))
    k0 = 28.5 * _SIGMA / (rho_ns * vm * vm) if vm > 1e-12 else 1e3
    ke = k0 if R >= 0.007 else roughness_m + R * (k0 - roughness_m) / 0.007
    ke = max(ke, 2.77e-5 * _FT)
    ke = roughness_m + _ss(lam_g / 0.1) * (ke - roughness_m)
    ke = min(ke, 0.05 * D)
    reg = 'gray' if lam_g > 0.5 else 'liquid-dominated(Gray extrapolated)'
    return _out(q, L, D, ke, dz_m, pr, st, vm, hl, reg, rho_ns, rho_ns, pr['mu'], lam_l, rho_l=rho_l)


# --------------------------------------------------------------------------------------------------
# 3 & 4. Drift-flux (Zuber-Findlay / Harmathy) and Hasan-Kabir
# --------------------------------------------------------------------------------------------------
def _drift_terms(rho_l, rho_g, D, theta):
    drho = max(rho_l - rho_g, 1.0)
    vinf = 1.53 * (G * _SIGMA * drho / (rho_l * rho_l)) ** 0.25          # Harmathy (1960)
    s = math.sin(theta)
    c = math.cos(theta)
    if s > 0:
        vb = vinf * _sqrt(s) * (1.0 + c) ** 1.2                           # Hasan & Kabir deviated-well factor
    else:
        vb = vinf * s                                                      # bubbles rise against downflow
    vt = _sqrt(G * D * drho / rho_l) * (0.35 * s + 0.54 * c)               # Taylor bubble, Bendiksen (1984)
    return vinf, vb, vt


def drift_flux_dp_bar(liquid_rate_m3d, length_m, diameter_m, roughness_m, dz_m, pressure_bar, temperature_c,
                      water_cut=0.0, gor_sm3sm3=0.0, api=35.0, gas_sg=0.75, water_sg=1.03,
                      extra_free_gas_sm3d=0.0):
    """Steady-state drift-flux (Zuber & Findlay 1965) with Harmathy bubble-rise velocity, any inclination.

    References: Zuber & Findlay, J. Heat Transfer 87:453 (1965); Harmathy, AIChE J. 6:281 (1960);
    Ishii, ANL-77-47 (1977) for C0 = 1.2 - 0.2 sqrt(rho_g/rho_L); Bendiksen, Int. J. Multiphase Flow 10:467 (1984)
    for the Taylor-bubble drift 0.35 sin(th) + 0.54 cos(th); Hasan & Kabir (1988) for the bubble-drift
    inclination factor sin^0.5(th)(1+cos th)^1.2 (recalled, not re-verified).
      vg = vsg/alpha = C0 vm + vd ;  vd = vb + w(vt - vb), w = smoothstep((lambda_g-0.15)/0.2)
      vb = 1.53 [g sigma (rho_L-rho_g)/rho_L^2]^0.25 ;  vt = sqrt(g D (rho_L-rho_g)/rho_L) (0.35 sin th + 0.54 cos th)
    Downward flow: vb = vb_inf sin(th) < 0 (bubbles rise against the flow); alpha is limited to
    max(lambda_g, 0.9) so counter-current flooding cannot give zero holdup.

    Implementation choices (not published coefficients): C0 and vd are faded to 1 and 0 as lambda_g -> 1 with
    smoothstep((lambda_g-0.7)/0.3), which makes the gas-only limit exactly homogeneous and removes the
    unrealistic holdup of C0 = 1.2 at high void; the bubble/Taylor blend window is mine. Friction: Moody f with the
    no-slip Re and the slip density rho_s. Acceleration neglected. Not suited to wet-gas film holdup (use Gray).

    Status: sanity-checked only, not validated against published data.
    """
    q = liquid_rate_m3d
    pr, st, rho_l, mu_l, vm, lam_l, lam_g, theta = _ctx(q, length_m, diameter_m, dz_m, pressure_bar, temperature_c,
                                                         water_cut, gor_sm3sm3, api, gas_sg, water_sg,
                                                         extra_free_gas_sm3d)
    rho_g = st.gas_density_kgm3
    vsg = lam_g * vm
    _, vb, vt = _drift_terms(rho_l, rho_g, diameter_m, theta)
    w = _ss((lam_g - 0.15) / 0.2)
    vd = vb + w * (vt - vb)
    c0b = 1.2 - 0.2 * _sqrt(rho_g / rho_l)
    fade = _ss((lam_g - 0.7) / 0.3)
    c0 = c0b + (1.0 - c0b) * fade
    vd *= (1.0 - fade)
    vg = max(c0 * vm + vd, vsg, 1e-12)
    alpha = min(vsg / vg, max(lam_g, 0.9)) if vsg > 0 else 0.0
    hl = 1.0 - alpha
    rho_s = hl * rho_l + (1.0 - hl) * rho_g
    if lam_g <= 0.0:
        reg = 'liquid'
    elif w < 0.5:
        reg = 'bubble'
    elif fade < 0.5:
        reg = 'slug/churn'
    else:
        reg = 'gas-dominated'
    return _out(q, length_m, diameter_m, roughness_m, dz_m, pr, st, vm, hl, reg, rho_s, pr['rho'], pr['mu'], lam_l,
                rho_l=rho_l)


def hasan_kabir_dp_bar(liquid_rate_m3d, length_m, diameter_m, roughness_m, dz_m, pressure_bar, temperature_c,
                       water_cut=0.0, gor_sm3sm3=0.0, api=35.0, gas_sg=0.75, water_sg=1.03,
                       extra_free_gas_sm3d=0.0):
    """Hasan-Kabir style mechanistic drift-flux model: bubble / dispersed bubble / slug / churn / annular.

    References: Hasan & Kabir, SPE Production Engineering 3(2):263-272 (1988) ("A study of multiphase flow
    behavior in vertical wells") and SPEPE 3(4):474-482 (1988) (deviated wells); Taitel, Barnea & Dukler,
    AIChE J. 26:345 (1980) (dispersed-bubble and annular transitions); Butterworth (1975) fit of the
    Lockhart-Martinelli void fraction (annular holdup).
    Elements used (gas velocity vg = C0 vm + vd):
      * bubble: C0 = 1.2, vd = Harmathy vb with inclination factor; slug: C0 = 1.2, vd = Taylor bubble
        sqrt(g D drho/rho_L)(0.35 sin th + 0.54 cos th); churn: C0 = 1.15, vd = Taylor.
      * bubble->slug when vsg = 0.429 vsl + 0.357 vb (void 0.25 with C0 = 1.2).
      * slug->churn when the slug void vsg/(1.2 vm + vt) reaches 0.52.
      * churn->annular when vsg = 3.1 [g sigma drho/rho_g^2]^0.25 (Taitel-Dukler / Hasan-Kabir).
      * dispersed bubble when the Hinze/Barnea maximum bubble size is below the Taitel critical size.
    Implementation choices / gaps (so NOT a faithful reproduction of the SPE papers): annular holdup uses the
    Butterworth Lockhart-Martinelli void fraction alpha = 1/(1 + 0.28 X_tt^0.71) (the papers use an entrainment
    model I could not retrieve) and is only allowed when lambda_g > ~0.5; every regime boundary is blended with
    smoothstep over about +/-35% of the controlling variable; holdup is faded to no-slip for lambda_g -> 1
    (fade window 0.9-1.0) so the gas-only limit is homogeneous; the churn C0 = 1.15 and the 0.52 churn criterion
    are quoted from memory/secondary sources; the slug void is floored at the bubble-slug limit 0.25 so holdup is
    monotone in gas rate (the original bubble/Taylor drift velocities would make it jump down). Friction: Moody f
    with the no-slip Re and the slip density. The regime blends are deliberately wide (+/-35% in the controlling
    velocity, _HW) because narrower blends make dp(q) dip at the churn->annular switch (holdup falls faster than
    vm^2 rises); with the wide blend dp was monotone in q over a 7x4x3x3 grid of GOR, D, p, water cut.
    Downhill segments (flow direction downward) are handled by the drift-flux kernel above (HK is an upflow
    model); horizontal segments use sin(th)=0 (vb = 0, Taylor drift 0.54 sqrt(g D drho/rho_L)).

    Status: sanity-checked only, not validated against published data.
    """
    q = liquid_rate_m3d
    if length_m > 0 and dz_m / max(length_m, 1e-12) * (1 if q >= 0 else -1) < 0:
        dp, props = drift_flux_dp_bar(q, length_m, diameter_m, roughness_m, dz_m, pressure_bar, temperature_c,
                                      water_cut, gor_sm3sm3, api, gas_sg, water_sg, extra_free_gas_sm3d)
        props['flow_regime'] = 'downflow(drift-flux)'
        return dp, props
    pr, st, rho_l, mu_l, vm, lam_l, lam_g, theta = _ctx(q, length_m, diameter_m, dz_m, pressure_bar, temperature_c,
                                                         water_cut, gor_sm3sm3, api, gas_sg, water_sg,
                                                         extra_free_gas_sm3d)
    D = diameter_m
    rho_g = st.gas_density_kgm3
    drho = max(rho_l - rho_g, 1.0)
    vsl = lam_l * vm
    vsg = lam_g * vm
    if vsg <= 0.0 or vm <= 0.0:
        return _out(q, length_m, D, roughness_m, dz_m, pr, st, vm, lam_l, 'liquid', pr['rho'], pr['rho'], pr['mu'],
                    lam_l, rho_l=rho_l)
    _, vb, vt = _drift_terms(rho_l, rho_g, D, theta)
    a_b = vsg / (1.2 * vm + vb)
    a_s = max(vsg / (1.2 * vm + vt), 0.25)   # slug void never below the bubble limit 0.25 -> alpha monotone in vsg
    a_c = vsg / (1.15 * vm + vt)
    # bubble -> slug
    vsg_bs = 0.429 * vsl + 0.357 * vb
    w_bs = _ss((_log(vsg / vsg_bs) + _HW) / (2 * _HW)) if vsg_bs > 1e-12 else 1.0
    alpha = a_b + w_bs * (a_s - a_b)
    # slug -> churn
    w_sc = _ss((_log(max(a_s, 1e-12) / 0.52) + _HW) / (2 * _HW))
    alpha += w_sc * (a_c - alpha)
    # churn -> annular (Butterworth/Lockhart-Martinelli void)
    vsg_a = 3.1 * (_SIGMA * G * drho / (rho_g * rho_g)) ** 0.25
    w_ca = _ss((_log(vsg / vsg_a) + _HW) / (2 * _HW)) * _ss((lam_g - 0.5) / 0.2)
    if w_ca > 0.0:
        xq = rho_g * vsg / (rho_g * vsg + rho_l * vsl)
        xtt = ((1.0 - xq) / max(xq, 1e-12)) ** 0.9 * _sqrt(rho_g / rho_l) * (mu_l / st.gas_viscosity_pas) ** 0.1
        a_a = min(lam_g, 1.0 / (1.0 + 0.28 * xtt ** 0.71))
        alpha += w_ca * (a_a - alpha)
    # dispersed bubble (Barnea / Taitel et al.)
    re_ns = max(1.0, pr['rho'] * vm * D / pr['mu'])
    f_fanning = friction_factor(re_ns, roughness_m / D) / 4.0
    lhs = 2.0 * _sqrt(0.4 * _SIGMA / (drho * G)) * (rho_l / _SIGMA) ** 0.6 * (2.0 * f_fanning / D) ** 0.4 * vm ** 1.2
    rd = lhs / (0.725 + 4.15 * _sqrt(lam_g))
    w_d = _ss((_log(rd) + _HW) / (2 * _HW)) * (1.0 - _ss((lam_g - 0.45) / 0.1))
    alpha += w_d * (a_b - alpha)
    # dry-gas limit: fade to no-slip
    fade = _ss((lam_g - 0.9) / 0.1)
    alpha += fade * (lam_g - alpha)
    alpha = min(max(alpha, 0.0), 1.0)
    hl = 1.0 - alpha
    hl = min(1.0, max(lam_l, hl))
    if w_ca > 0.5:
        reg = 'annular'
    elif w_d > 0.5:
        reg = 'dispersed-bubble'
    elif w_sc > 0.5:
        reg = 'churn'
    elif w_bs > 0.5:
        reg = 'slug'
    else:
        reg = 'bubble'
    rho_s = hl * rho_l + (1.0 - hl) * rho_g
    return _out(q, length_m, D, roughness_m, dz_m, pr, st, vm, hl, reg, rho_s, pr['rho'], pr['mu'], lam_l,
                rho_l=rho_l)


# --------------------------------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------------------------------
CORRELATIONS = {
    'Beggs-Brill': beggs_brill_dp_bar,
    'Homogeneous': homogeneous_dp_bar,
    'Hagedorn-Brown': hagedorn_brown_dp_bar,
    'Gray': gray_dp_bar,
    'Drift-flux': drift_flux_dp_bar,
    'Hasan-Kabir': hasan_kabir_dp_bar,
}

CORRELATION_INFO = {
    'Beggs-Brill': {
        'label': 'Beggs & Brill (1973/77)',
        'reference': 'Beggs & Brill, JPT 25(5):607 (1973); Brill & Mukherjee (1999)',
        'applicable': ('tubing', 'flowline'),
        'best_for': 'Any inclination incl. horizontal flowlines and downhill; screening of oil wells',
        'limitations': 'Fixed surface tension; tends to over-predict holdup in vertical wells; smoothed regime map',
    },
    'Homogeneous': {
        'label': 'Homogeneous (no-slip)',
        'reference': 'Standard homogeneous mixture model (no slip, Darcy-Weisbach)',
        'applicable': ('tubing', 'flowline'),
        'best_for': 'Baseline / dispersed or high-velocity flow, check calculations',
        'limitations': 'No slip: over-predicts friction, under-predicts gravity loss in slugging/low-velocity flow',
    },
    'Hagedorn-Brown': {
        'label': 'Hagedorn & Brown (modified, Griffith bubble flow)',
        'reference': 'Hagedorn & Brown, JPT 17(4):475 (1965); Griffith (1962); Brill & Mukherjee (1999) curve fits',
        'applicable': ('tubing',),
        'best_for': 'Vertical oil wells, medium-high liquid rates, GOR < ~1000 Sm3/Sm3, tubing 2-4 in',
        'limitations': 'Vertical-flow correlation; no flow-regime map beyond bubble; downhill falls back to no-slip; '
                       'poor for very low rates and for condensate/wet gas',
    },
    'Gray': {
        'label': 'Gray (1974)',
        'reference': 'Gray, API 14B user manual (1974); Brill & Mukherjee (1999)',
        'applicable': ('tubing',),
        'best_for': 'Vertical gas / gas-condensate wells, vm < 15 m/s, condensate < ~50 bbl/MMscf, D < 3.5 in',
        'limitations': 'Only for high gas fraction (liquid-dominated flow is extrapolation); vertical-flow correlation',
    },
    'Drift-flux': {
        'label': 'Drift-flux (Zuber-Findlay / Harmathy)',
        'reference': 'Zuber & Findlay (1965); Harmathy (1960); Ishii (1977); Bendiksen (1984)',
        'applicable': ('tubing', 'flowline'),
        'best_for': 'Any inclination incl. horizontal and downward; smooth, simulator-style steady-state kernel',
        'limitations': 'Simplified closure (smooth bubble/Taylor blend, high-void fade); no film holdup in wet gas; '
                       'downflow stratified flow only approximated',
    },
    'Hasan-Kabir': {
        'label': 'Hasan & Kabir (mechanistic drift-flux)',
        'reference': 'Hasan & Kabir, SPEPE 3(2) 263 (1988); SPEPE 3(4) 474 (1988); Taitel et al. (1980)',
        'applicable': ('tubing', 'flowline'),
        'best_for': 'Vertical and upward deviated wells, bubble/slug/churn/annular transitions',
        'limitations': 'Annular holdup via Butterworth LM fit (not HK entrainment model); downhill uses drift-flux; '
                       'transition criteria partly from secondary sources',
    },
}

_ALIASES = {
    'bb': 'Beggs-Brill', 'beggsbrill': 'Beggs-Brill', 'beggsandbrill': 'Beggs-Brill',
    'homogeneous': 'Homogeneous', 'homo': 'Homogeneous', 'noslip': 'Homogeneous', 'homogenous': 'Homogeneous',
    'hb': 'Hagedorn-Brown', 'hagedornbrown': 'Hagedorn-Brown', 'hagedornandbrown': 'Hagedorn-Brown',
    'modifiedhagedornbrown': 'Hagedorn-Brown', 'mhb': 'Hagedorn-Brown', 'hbm': 'Hagedorn-Brown',
    'gray': 'Gray', 'grey': 'Gray',
    'driftflux': 'Drift-flux', 'df': 'Drift-flux', 'zuberfindlay': 'Drift-flux', 'dfm': 'Drift-flux',
    'hk': 'Hasan-Kabir', 'hasankabir': 'Hasan-Kabir', 'hasanandkabir': 'Hasan-Kabir',
}


def _norm(name):
    return ''.join(ch for ch in str(name).lower() if ch.isalnum())


def _canonical(name):
    n = _norm(name)
    if n in _ALIASES:
        return _ALIASES[n]
    if n:
        hits = {v for k, v in _ALIASES.items() if k.startswith(n) and len(n) >= 3}
        if len(hits) == 1:
            return next(iter(hits))
    raise ValueError("Unknown correlation %r; valid names: %s" % (name, ', '.join(CORRELATIONS)))


def get_correlation(name):
    """Return the correlation function for ``name`` (case/space/hyphen tolerant; 'HB', 'Hasan Kabir', 'beggs' ...)."""
    return CORRELATIONS[_canonical(name)]


def canonical_name(name):
    """Return the registry key for ``name`` (raises ValueError for unknown names)."""
    return _canonical(name)


def list_correlations(kind=None):
    """Names of the registered correlations, optionally only those applicable to 'tubing' or 'flowline'."""
    if kind is None:
        return list(CORRELATIONS)
    k = str(kind).lower()
    return [n for n in CORRELATIONS if k in CORRELATION_INFO[n]['applicable']]
