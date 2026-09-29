from dataclasses import dataclass

@dataclass
class BlackOilState:
    pressure_bar: float
    temperature_c: float
    oil_density_kgm3: float
    water_density_kgm3: float
    gas_density_kgm3: float
    oil_viscosity_pas: float
    water_viscosity_pas: float
    gas_viscosity_pas: float
    solution_gor_sm3sm3: float
    oil_fvf: float
    gas_z: float


def simple_black_oil(pressure_bar: float, temperature_c: float, api: float = 35.0,
                     gas_sg: float = 0.75, water_sg: float = 1.03,
                     bubblepoint_bar: float = 150.0, rsb_sm3sm3: float = 120.0) -> BlackOilState:
    """Transparent screening-level black-oil approximation for v2.
    It is deliberately isolated so validated Standing/Vasquez-Beggs/PVT tables can replace it later.
    """
    p=max(float(pressure_bar),1.0); t=float(temperature_c)
    rho_o_sc=141.5/(api+131.5)*999.016
    rs=rsb_sm3sm3*min(p/max(bubblepoint_bar,1e-6),1.0)**0.8
    bo=1.0 + 0.0007*rs + 3.5e-5*(t-15.0)
    rho_o=max(450.0, rho_o_sc/bo)
    rho_w=999.0*water_sg*(1.0+4.5e-5*p)
    z=max(0.65,min(1.2,0.90+0.00035*(t-60.0)+0.00018*(p-100.0)))
    rho_g=max(0.1,1.225*gas_sg*(p/1.01325)*(288.15/(t+273.15))/z)
    mu_o=max(0.0004,0.006*(35.0/max(api,10.0))**1.8*(1.0+0.002*p)*pow(0.985,max(t-20.0,0.0)))
    mu_w=max(0.0002,0.00105*pow(0.985,max(t-20.0,0.0)))
    mu_g=1.1e-5*(1.0+0.0015*p)
    return BlackOilState(p,t,rho_o,rho_w,rho_g,mu_o,mu_w,mu_g,rs,bo,z)
