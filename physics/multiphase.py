import math
from physics.hydraulics import friction_factor, G
from physics.units import DAY_TO_S as DAY, pa_to_bar
from physics.pvt import simple_black_oil


def mixture_properties(liquid_rate_m3d, water_cut, gor_sm3sm3, pressure_bar, temperature_c, api=35.0, gas_sg=0.75, water_sg=1.03,
                       extra_free_gas_sm3d=0.0):
    """No-slip in-situ mixture properties.

    ``extra_free_gas_sm3d`` adds free gas that is not tied to the oil rate (e.g. gas-lift
    injection gas). It is always free gas at line conditions and is independent of the
    liquid rate, so an aerated column stays light even at low liquid rates.
    """
    wc=min(max(float(water_cut),0.0),0.9999); ql=max(abs(float(liquid_rate_m3d)),1e-12)
    st=simple_black_oil(pressure_bar,temperature_c,api,gas_sg,water_sg)
    qo_sc=ql*(1-wc); qw_sc=ql*wc
    free_gas_sc=max(0.0, qo_sc*(max(gor_sm3sm3,0.0)-st.solution_gor_sm3sm3)) + max(float(extra_free_gas_sm3d),0.0)
    q_o=qo_sc*st.oil_fvf/DAY; q_w=qw_sc/DAY
    # convert standard gas volume to line volume by real-gas scaling with z
    q_g=free_gas_sc/DAY * st.gas_z*(temperature_c+273.15)/288.15 * 1.01325/max(pressure_bar,1.0)
    qtot=q_o+q_w+q_g
    lam_o=q_o/qtot; lam_w=q_w/qtot; lam_g=q_g/qtot
    rho=lam_o*st.oil_density_kgm3+lam_w*st.water_density_kgm3+lam_g*st.gas_density_kgm3
    mu=lam_o*st.oil_viscosity_pas+lam_w*st.water_viscosity_pas+lam_g*st.gas_viscosity_pas
    return {'rho':rho,'mu':max(mu,1e-6),'liquid_holdup':lam_o+lam_w,'gas_fraction':lam_g,'q_line_m3s':qtot,'state':st}

def homogeneous_dp_bar(liquid_rate_m3d,length_m,diameter_m,roughness_m,dz_m,pressure_bar,temperature_c,water_cut=0.0,gor_sm3sm3=0.0,api=35.0,gas_sg=0.75,water_sg=1.03,
                       extra_free_gas_sm3d=0.0):
    """Homogeneous no-slip multiphase pressure drop. Used as v2's auditable baseline correlation."""
    if diameter_m<=0 or length_m<0: raise ValueError('Invalid pipe geometry')
    props=mixture_properties(liquid_rate_m3d,water_cut,gor_sm3sm3,pressure_bar,temperature_c,api,gas_sg,water_sg,extra_free_gas_sm3d)
    area=math.pi*diameter_m**2/4; v=props['q_line_m3s']/area
    re=props['rho']*abs(v)*diameter_m/props['mu']; f=friction_factor(re,roughness_m/diameter_m)
    friction=f*(length_m/diameter_m)*(props['rho']*v*v/2.0)
    hydro=props['rho']*G*dz_m
    sign=1 if liquid_rate_m3d>=0 else -1
    return pa_to_bar(sign*friction+hydro), {**props,'mixture_velocity_ms':v,'reynolds':re,'friction_factor':f,'flow_regime':'homogeneous'}
