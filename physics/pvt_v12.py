from dataclasses import dataclass
import numpy as np
from .pvt import BlackOilState, simple_black_oil

def standing_solution_gor(pressure_bar, temperature_c, api=35.0, gas_sg=0.75, bubblepoint_bar=150.0, rsb_sm3sm3=120.0):
    """Standing-style Rs screening correlation, normalized to user-supplied Rsb at Pb.
    Intended for sensitivity/planning, not custody/design calculations.
    """
    p=max(float(pressure_bar),1.0); pb=max(float(bubblepoint_bar),1.0)
    if p >= pb: return float(rsb_sm3sm3)
    # normalized Standing pressure exponent; ensures Rs(Pb)=Rsb and monotonicity.
    return float(rsb_sm3sm3)*(p/pb)**1.2048

def standing_black_oil(pressure_bar, temperature_c, api=35.0, gas_sg=0.75, water_sg=1.03, bubblepoint_bar=150.0, rsb_sm3sm3=120.0):
    p=max(float(pressure_bar),1.0); t=float(temperature_c)
    rs=standing_solution_gor(p,t,api,gas_sg,bubblepoint_bar,rsb_sm3sm3)
    rho_o_sc=141.5/(api+131.5)*999.016
    # Standing-style Bo functional form, SI inputs converted internally for a stable screening implementation.
    t_f=t*9/5+32; rs_scfstb=rs*5.614583; gamma_o=141.5/(api+131.5)
    f=rs_scfstb*(gas_sg/max(gamma_o,1e-6))**0.5 + 1.25*t_f
    bo=max(0.9, 0.972 + 1.47e-4*(max(f,0.0)**1.175))
    rho_o=max(450.0,rho_o_sc/bo)
    rho_w=999.0*water_sg*(1.0+4.5e-5*p)
    z=max(.65,min(1.2,.90+.00035*(t-60)+.00018*(p-100)))
    rho_g=max(.1,1.225*gas_sg*(p/1.01325)*(288.15/(t+273.15))/z)
    mu_o=max(.0004,.006*(35/max(api,10))**1.8*(1+.002*p)*pow(.985,max(t-20,0)))
    mu_w=max(.0002,.00105*pow(.985,max(t-20,0))); mu_g=1.1e-5*(1+.0015*p)
    return BlackOilState(p,t,rho_o,rho_w,rho_g,mu_o,mu_w,mu_g,rs,bo,z)

def tabulated_black_oil(pressure_bar, temperature_c, table):
    """Interpolate user/calibrated PVT rows. Required keys: pressure_bar plus BlackOilState properties."""
    p=float(pressure_bar); rows=sorted(table,key=lambda r:r['pressure_bar']); xp=np.array([r['pressure_bar'] for r in rows],float)
    def v(k): return float(np.interp(p,xp,np.array([r[k] for r in rows],float)))
    return BlackOilState(p,float(temperature_c),v('oil_density_kgm3'),v('water_density_kgm3'),v('gas_density_kgm3'),v('oil_viscosity_pas'),v('water_viscosity_pas'),v('gas_viscosity_pas'),v('solution_gor_sm3sm3'),v('oil_fvf'),v('gas_z'))

def black_oil(model='simple', **kwargs):
    m=str(model).lower()
    if m in ('standing','standing-style'): return standing_black_oil(**kwargs)
    if m in ('table','tabulated'):
        table=kwargs.pop('table'); return tabulated_black_oil(table=table,**kwargs)
    return simple_black_oil(**kwargs)
