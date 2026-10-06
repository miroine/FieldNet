"""Temperature modelling: wellbore (Ramey) and flowline/riser energy balance with Joule-Thomson and elevation terms.

Default behaviour is unchanged: elements keep their fixed ``temperature_c`` unless ``params['thermal_model']`` selects

  'ramey'      wells (tubing): fluid temperature up the string from bottom-hole temperature, geothermal gradient and a relaxation
               distance that depends on mass rate (so wellhead temperature rises with rate). Ramey (1962), steady production.
  'heat_loss'  pipelines / risers: marching energy balance
                   mdot cp dT/dx = -U pi D_o (T - T_amb)  +  mdot cp mu_JT dP/dx  -  mdot g dz/dx
               with mixture cp, JT coefficient and elevation (potential-energy) term, using the pressure change of every segment.

Mass flow and cp come from the phase rates (oil / water / free gas at line conditions). Everything is screening-level:
constant U, no phase-change enthalpy (condensation / vaporisation), no friction-heating other than through the JT term.
"""
from __future__ import annotations
import math
from physics.pvt_model import gas_z, MW_AIR

G = 9.80665
R_GAS = 8.314462
DAY = 86400.0
THERMAL_KEYS = ('thermal_model', 'ambient_temperature_c', 'overall_u_w_m2k', 'geothermal_gradient_c_per_km', 'surface_temperature_c',
                'bottomhole_temperature_c', 'outer_diameter_m', 'insulation_note', 'include_jt', 'include_elevation', 'gas_cp_jkgk',
                'earth_conductivity_w_mk', 'flow_time_days')


def _f(d, k, default):
    v = (d or {}).get(k, default)
    try: v = float(v)
    except (TypeError, ValueError): return float(default)
    return v if math.isfinite(v) else float(default)


def mode(prm):
    m = str((prm or {}).get('thermal_model', 'fixed') or 'fixed').lower()
    return m if m in ('ramey', 'heat_loss') else 'fixed'


# ----------------------------------------------------------------------------- fluid thermal properties
def oil_cp(t_c, api):
    """Gambill: Btu/lb/F -> J/kg/K."""
    t_f = t_c * 1.8 + 32.0; return 4186.8 * (0.388 + 0.00045 * t_f) / math.sqrt(141.5 / (131.5 + api))


def water_cp(t_c, salinity_wt_pct=4.0): return max(3600.0, 4200.0 - 25.0 * salinity_wt_pct + 0.2 * t_c)


def gas_cp(p_bar, t_c, gas_sg, override=None):
    """Real-gas cp [J/kg/K]: ideal-gas value from molecular weight (Cp0/R = 4.3 + 0.154 (M-16)) with a pressure uplift. Screening only;
    pass ``gas_cp_jkgk`` on the element to override."""
    if override: return float(override)
    m = MW_AIR * gas_sg; tk = t_c + 273.15; cp0 = R_GAS * (4.3 + 0.154 * (m - 16.0)) / (m * 1e-3) * (1.0 + 0.0008 * (tk - 300.0))
    return cp0 * (1.0 + 0.0033 * min(max(p_bar, 0.0), 250.0) * (300.0 / tk) ** 3)


def jt_coefficient_gas_k_per_bar(p_bar, t_c, gas_sg, cp_gas, co2=0.0, h2s=0.0, n2=0.0):
    """mu_JT = R T^2/(P cp_molar) (dZ/dT)_P, from the same Z correlation as the PVT (finite difference). Typically 0.2-0.5 K/bar."""
    m = MW_AIR * gas_sg * 1e-3; dt = 1.0
    dz = (gas_z(p_bar, t_c + dt, gas_sg, co2, h2s, n2) - gas_z(p_bar, t_c - dt, gas_sg, co2, h2s, n2)) / (2 * dt)
    tk = t_c + 273.15
    return R_GAS * tk * tk * dz / (max(p_bar, 1.0) * 1e5 * cp_gas * m) * 1e5


def jt_coefficient_liquid_k_per_bar(t_c, rho, cp, alpha=7e-4):
    """(T alpha - 1)/(rho cp) -> K/bar; negative for oil (liquid heats on pressure drop, i.e. friction heating)."""
    return ((t_c + 273.15) * alpha - 1.0) / (rho * cp) * 1e5


def phase_mass_rates(q_liq_m3d, water_cut, gor_sm3sm3, api, gas_sg, water_sg=1.03, extra_gas_sm3d=0.0):
    """(oil, water, gas) mass rates [kg/s] from standard-condition rates."""
    q = abs(float(q_liq_m3d)); wc = min(max(float(water_cut), 0.0), 1.0)
    rho_o = 141.5 / (131.5 + api) * 999.0; rho_w = 999.0 * water_sg; rho_g = 1.225 * gas_sg
    mo = q * (1 - wc) * rho_o / DAY; mw = q * wc * rho_w / DAY
    mg = (q * (1 - wc) * max(gor_sm3sm3, 0.0) + max(extra_gas_sm3d, 0.0)) * rho_g / DAY
    return mo, mw, mg


class Stream:
    """Mass rates and heat capacity of one stream (all phases flow together; dissolved gas is carried by the oil)."""

    def __init__(self, q_liq_m3d, water_cut, gor_sm3sm3, api, gas_sg, water_sg=1.03, extra_gas_sm3d=0.0, gas_cp_override=None,
                 co2=0.0, h2s=0.0, n2=0.0):
        self.mo, self.mw, self.mg = phase_mass_rates(q_liq_m3d, water_cut, gor_sm3sm3, api, gas_sg, water_sg, extra_gas_sm3d)
        self.api, self.gas_sg, self.gcp, self.co2, self.h2s, self.n2 = api, gas_sg, gas_cp_override, co2, h2s, n2
        self.mdot = self.mo + self.mw + self.mg

    def cp(self, p_bar, t_c):
        if self.mdot <= 0: return 2200.0
        return (self.mo * oil_cp(t_c, self.api) + self.mw * water_cp(t_c) + self.mg * gas_cp(p_bar, t_c, self.gas_sg, self.gcp)) / self.mdot

    def jt_k_per_bar(self, p_bar, t_c, free_gas_fraction=None, rho_liq=800.0):
        """Mass-and-cp weighted JT coefficient. ``free_gas_fraction`` = free gas mass fraction at line conditions (None: all produced gas free)."""
        if self.mdot <= 0: return 0.0
        mg = self.mg if free_gas_fraction is None else free_gas_fraction * self.mdot
        ml = self.mdot - mg
        cpg = gas_cp(p_bar, t_c, self.gas_sg, self.gcp); cpl = (self.mo * oil_cp(t_c, self.api) + self.mw * water_cp(t_c)) / max(self.mo + self.mw, 1e-12)
        jg = jt_coefficient_gas_k_per_bar(p_bar, t_c, self.gas_sg, cpg, self.co2, self.h2s, self.n2) if mg > 0 else 0.0
        jl = jt_coefficient_liquid_k_per_bar(t_c, rho_liq, cpl)
        num = mg * cpg * jg + ml * cpl * jl; den = mg * cpg + ml * cpl
        return num / den if den > 0 else 0.0


# ----------------------------------------------------------------------------- flowline marching
def advance_segment(t_in_c, dl_m, dz_m, dp_bar, d_out_m, u_w_m2k, t_amb_c, stream: Stream, p_mid_bar, free_gas_fraction=None,
                    rho_liq=800.0, include_jt=True, include_elevation=True):
    """Outlet temperature of one segment of length dl [m], elevation gain dz [m] along the flow, pressure change dp [bar] along the flow
    (negative = pressure falls). Exact solution of the linear ODE for a constant source over the segment."""
    if stream.mdot <= 0 or dl_m <= 0: return t_in_c
    cp = stream.cp(p_mid_bar, t_in_c)
    d_t_src = 0.0
    if include_jt: d_t_src += stream.jt_k_per_bar(p_mid_bar, t_in_c, free_gas_fraction, rho_liq) * dp_bar
    if include_elevation: d_t_src -= G * dz_m / cp
    if u_w_m2k <= 0 or d_out_m <= 0: return t_in_c + d_t_src
    a = stream.mdot * cp / (u_w_m2k * math.pi * d_out_m)       # relaxation length [m]
    e = math.exp(-dl_m / a)
    s = d_t_src / dl_m
    return t_in_c * e + (t_amb_c + a * s) * (1.0 - e)


def relaxation_length_m(stream: Stream, u_w_m2k, d_out_m, p_bar=50.0, t_c=50.0):
    if u_w_m2k <= 0 or d_out_m <= 0: return math.inf
    return stream.mdot * stream.cp(p_bar, t_c) / (u_w_m2k * math.pi * d_out_m)


def line_spec(e_params, d_m):
    """Thermal inputs of a pipeline/riser."""
    return {'u': _f(e_params, 'overall_u_w_m2k', 5.0), 't_amb': _f(e_params, 'ambient_temperature_c', 4.0),
            'd_out': _f(e_params, 'outer_diameter_m', d_m * 1.15), 'jt': bool(e_params.get('include_jt', True)), 'elev': bool(e_params.get('include_elevation', True))}


# ----------------------------------------------------------------------------- wellbore (Ramey)
def ramey_relaxation_m(mdot, cp, r_to, u_w_m2k, k_e=2.0, alpha_e=1.0e-6, t_s=3.15e7, r_w=0.15):
    """Ramey (1962) relaxation distance A [m] = mdot cp (k_e + r_to U f(t)) / (2 pi r_to U k_e)."""
    f = max(math.log(2.0 * math.sqrt(alpha_e * t_s) / r_w) - 0.290, 0.1)
    return mdot * cp * (k_e + r_to * u_w_m2k * f) / (2.0 * math.pi * r_to * u_w_m2k * k_e)


def well_thermal_inputs(p, depth_m, tubing_id_m):
    """Dict of thermal inputs for a well (None when thermal_model != 'ramey')."""
    if mode(p) != 'ramey': return None
    t_bh = _f(p, 'bottomhole_temperature_c', _f(p, 'temperature_c', 70.0) + 20.0)
    t_s = _f(p, 'surface_temperature_c', _f(p, 'ambient_temperature_c', 4.0))
    grad = p.get('geothermal_gradient_c_per_km')
    g = _f(p, 'geothermal_gradient_c_per_km', 0.0) / 1000.0 if grad not in (None, '') else (t_bh - t_s) / max(depth_m, 1.0)
    return {'t_bh': t_bh, 't_s': t_s, 'grad': g, 'u': _f(p, 'overall_u_w_m2k', 10.0), 'k_e': _f(p, 'earth_conductivity_w_mk', 2.0),
            'r_to': 0.5 * _f(p, 'outer_diameter_m', tubing_id_m * 1.15), 't_flow_s': _f(p, 'flow_time_days', 365.0) * DAY,
            'gas_cp': p.get('gas_cp_jkgk'), 'depth': depth_m}


def ramey_profile(th, stream: Stream, p_wh_bar=30.0):
    """Returns f(z) -> fluid temperature [degC] at TVD z below the wellhead (flowing upward), plus the wellhead temperature."""
    depth = th['depth']; t_mid = 0.5 * (th['t_bh'] + th['t_s'])
    cp = stream.cp(p_wh_bar, t_mid) if stream.mdot > 0 else 2200.0
    if stream.mdot <= 0:
        return (lambda z: th['t_s'] + th['grad'] * z), th['t_s']
    A = ramey_relaxation_m(stream.mdot, cp, th['r_to'], max(th['u'], 1e-6), th['k_e'], 1.0e-6, th['t_flow_s'])
    gG = th['grad']; t_e_bh = th['t_s'] + gG * depth
    lag = gG * A

    def temp(z):
        z = min(max(z, 0.0), depth)
        return th['t_s'] + gG * z + lag + (th['t_bh'] - t_e_bh - lag) * math.exp(-(depth - z) / A)
    return temp, temp(0.0)
