import math
from physics.units import pa_to_bar
G=9.80665

def friction_factor(re, rel_rough):
    if re <= 0: return 0.0
    if re < 2300: return 64.0/re
    return 0.25/(math.log10(rel_rough/3.7 + 5.74/(re**0.9))**2)

def pipe_dp_bar(q_m3s, length_m, diameter_m, roughness_m, rho=850.0, mu_pa_s=0.005, dz_m=0.0):
    if diameter_m <= 0: raise ValueError('Diameter must be positive')
    area=math.pi*diameter_m**2/4
    v=abs(q_m3s)/area
    re=rho*v*diameter_m/max(mu_pa_s,1e-12)
    f=friction_factor(re,roughness_m/diameter_m)
    dynamic=f*(length_m/diameter_m)*(rho*v*v/2)
    hydro=rho*G*dz_m
    sign=1 if q_m3s>=0 else -1
    return pa_to_bar(sign*dynamic+hydro)
