"""FieldNet v20 engineering unit-system boundary.

The calculation model remains canonical: bar(a where thermodynamics require absolute
pressure), degC, m, m3/d liquid, Sm3/d standard gas, kg/m3, cP, kW.
UI/import/export layers may use Norwegian SI or Field units.  Standard volumes are
kept distinct from flowing/actual volumes.
"""
from dataclasses import dataclass
from math import isfinite
from physics.units import STB_TO_M3, SCF_TO_SM3

ATM_BAR = 1.01325
HP_TO_KW = 0.7456998715822702
LBFT3_TO_KGM3 = 16.01846337396014

@dataclass(frozen=True)
class UnitProfile:
    key: str; label: str; pressure: str; temperature: str; length: str; diameter: str
    liquid_rate: str; gas_rate: str; liquid_volume: str; gas_volume: str
    gor: str; density: str; power: str

NORWEGIAN_SI = UnitProfile('norwegian_si','Norwegian SI','bar','°C','m','mm','Sm³/d','Sm³/d','Sm³','Sm³','Sm³/Sm³','kg/m³','kW')
FIELD = UnitProfile('field','Field','psi','°F','ft','in','stb/d','Mscf/d','stb','MMscf','scf/stb','lb/ft³','hp')
PROFILES={x.key:x for x in (NORWEGIAN_SI,FIELD)}

# Canonical quantities. Liquid m3 at standard/stock-tank conditions is denoted Sm3
# at the UI boundary; hydraulics flowing m3 remains a separate quantity.
def _f(x):
    x=float(x)
    if not isfinite(x): raise ValueError('unit value must be finite')
    return x

def c_to_f(x): return _f(x)*9/5+32
def f_to_c(x): return (_f(x)-32)*5/9
def bar_to_psi(x): return _f(x)*14.503773773020923
def psi_to_bar(x): return _f(x)/14.503773773020923
def m_to_ft(x): return _f(x)/0.3048
def ft_to_m(x): return _f(x)*0.3048
def m_to_in(x): return _f(x)/0.0254
def in_to_m(x): return _f(x)*0.0254
def m3_to_stb(x): return _f(x)/STB_TO_M3
def stb_to_m3(x): return _f(x)*STB_TO_M3
def sm3_to_scf(x): return _f(x)/SCF_TO_SM3
def scf_to_sm3(x): return _f(x)*SCF_TO_SM3
def kgm3_to_lbft3(x): return _f(x)/LBFT3_TO_KGM3
def lbft3_to_kgm3(x): return _f(x)*LBFT3_TO_KGM3
def kw_to_hp(x): return _f(x)/HP_TO_KW
def hp_to_kw(x): return _f(x)*HP_TO_KW

def pressure_to_display(bar, profile): return _f(bar) if profile=='norwegian_si' else bar_to_psi(bar)
def pressure_from_display(v, profile): return _f(v) if profile=='norwegian_si' else psi_to_bar(v)
def temperature_to_display(c, profile): return _f(c) if profile=='norwegian_si' else c_to_f(c)
def temperature_from_display(v, profile): return _f(v) if profile=='norwegian_si' else f_to_c(v)
def length_to_display(m, profile): return _f(m) if profile=='norwegian_si' else m_to_ft(m)
def length_from_display(v, profile): return _f(v) if profile=='norwegian_si' else ft_to_m(v)
def diameter_to_display(m, profile): return _f(m)*1000 if profile=='norwegian_si' else m_to_in(m)
def diameter_from_display(v, profile): return _f(v)/1000 if profile=='norwegian_si' else in_to_m(v)
def liquid_rate_to_display(m3d, profile): return _f(m3d) if profile=='norwegian_si' else m3_to_stb(m3d)
def liquid_rate_from_display(v, profile): return _f(v) if profile=='norwegian_si' else stb_to_m3(v)
def gas_rate_to_display(sm3d, profile): return _f(sm3d) if profile=='norwegian_si' else sm3_to_scf(sm3d)/1000
def gas_rate_from_display(v, profile): return _f(v) if profile=='norwegian_si' else scf_to_sm3(_f(v)*1000)
def liquid_volume_to_display(sm3, profile): return _f(sm3) if profile=='norwegian_si' else m3_to_stb(sm3)
def liquid_volume_from_display(v, profile): return _f(v) if profile=='norwegian_si' else stb_to_m3(v)
def gas_volume_to_display(sm3, profile): return _f(sm3) if profile=='norwegian_si' else sm3_to_scf(sm3)/1e6
def gas_volume_from_display(v, profile): return _f(v) if profile=='norwegian_si' else scf_to_sm3(_f(v)*1e6)
def gor_to_display(sm3sm3, profile): return _f(sm3sm3) if profile=='norwegian_si' else sm3_to_scf(sm3sm3)/m3_to_stb(1.0)
def gor_from_display(v, profile): return _f(v) if profile=='norwegian_si' else scf_to_sm3(_f(v)*m3_to_stb(1.0))
def density_to_display(kgm3, profile): return _f(kgm3) if profile=='norwegian_si' else kgm3_to_lbft3(kgm3)
def density_from_display(v, profile): return _f(v) if profile=='norwegian_si' else lbft3_to_kgm3(v)
def power_to_display(kw, profile): return _f(kw) if profile=='norwegian_si' else kw_to_hp(kw)
def power_from_display(v, profile): return _f(v) if profile=='norwegian_si' else hp_to_kw(v)

def gauge_to_absolute_bar(p_gauge_bar, atmospheric_bar=ATM_BAR): return _f(p_gauge_bar)+_f(atmospheric_bar)
def absolute_to_gauge_bar(p_abs_bar, atmospheric_bar=ATM_BAR): return _f(p_abs_bar)-_f(atmospheric_bar)

def pi_to_display(pi_m3d_bar, profile):
    if profile=='norwegian_si': return _f(pi_m3d_bar)
    return m3_to_stb(pi_m3d_bar)/bar_to_psi(1.0)
def pi_from_display(v, profile):
    if profile=='norwegian_si': return _f(v)
    return stb_to_m3(_f(v)*bar_to_psi(1.0))

def labels(profile):
    p=PROFILES[profile]
    return {k:getattr(p,k) for k in ('pressure','temperature','length','diameter','liquid_rate','gas_rate','liquid_volume','gas_volume','gor','density','power')}

STANDARD_CONDITIONS={
    'standard_temperature_c':15.0,
    'standard_pressure_bara':1.01325,
    'note':'FieldNet standard gas/liquid volumes use 15 °C and 1.01325 bara as the reporting reference. Correlations may retain their documented internal reference basis.'
}

# Explicit key registry for canonical project fields. This is intentionally conservative:
# unknown/custom keys are left untouched rather than guessed from their numeric value.
PROJECT_FIELD_QUANTITIES = {
 'pressure_bar':'pressure','reservoir_pressure_bar':'pressure','initial_pressure_bar':'pressure','min_pressure_bar':'pressure','max_pressure_bar':'pressure','min_bhp_bar':'pressure','lift_assist_bar':'pressure','shutoff_head_bar':'pressure','min_head_bar':'pressure','max_discharge_bar':'pressure',
 'length_m':'length','depth_m':'length','elevation_change_m':'length','diameter_m':'diameter','tubing_id_m':'diameter',
 'temperature_c':'temperature','pi_m3d_bar':'pi','qmax_m3d':'liquid_rate','initial_rate_m3d':'liquid_rate','rated_rate_m3d':'liquid_rate','gor_sm3sm3':'gor','rated_gas_rate_sm3d':'gas_rate','rho_kgm3':'density'
}
_TO={'pressure':pressure_to_display,'temperature':temperature_to_display,'length':length_to_display,'diameter':diameter_to_display,'liquid_rate':liquid_rate_to_display,'gas_rate':gas_rate_to_display,'gor':gor_to_display,'density':density_to_display,'power':power_to_display,'pi':pi_to_display}
_FROM={'pressure':pressure_from_display,'temperature':temperature_from_display,'length':length_from_display,'diameter':diameter_from_display,'liquid_rate':liquid_rate_from_display,'gas_rate':gas_rate_from_display,'gor':gor_from_display,'density':density_from_display,'power':power_from_display,'pi':pi_from_display}

def convert_mapping_units(obj, profile, direction='to_display'):
    """Deep-copy a project-like mapping, converting only registered engineering keys."""
    if profile not in PROFILES: raise ValueError(f'unknown unit profile: {profile}')
    if direction not in ('to_display','from_display'): raise ValueError('direction must be to_display or from_display')
    funcs=_TO if direction=='to_display' else _FROM
    if isinstance(obj,list): return [convert_mapping_units(x,profile,direction) for x in obj]
    if not isinstance(obj,dict): return obj
    out={}
    for k,v in obj.items():
        if isinstance(v,(dict,list)): out[k]=convert_mapping_units(v,profile,direction)
        elif k in PROJECT_FIELD_QUANTITIES and v is not None: out[k]=funcs[PROJECT_FIELD_QUANTITIES[k]](v,profile)
        else: out[k]=v
    return out

# v19 flow-assurance display quantities
def velocity_to_display(ms, profile): return _f(ms) if profile=='norwegian_si' else m_to_ft(ms)
def velocity_from_display(v, profile): return _f(v) if profile=='norwegian_si' else ft_to_m(v)
def heat_transfer_u_to_display(w_m2k, profile):
    # 1 W/(m2 K) = 0.1761101838 Btu/(h ft2 degF)
    return _f(w_m2k) if profile=='norwegian_si' else _f(w_m2k)*0.1761101838
def heat_transfer_u_from_display(v, profile): return _f(v) if profile=='norwegian_si' else _f(v)/0.1761101838
