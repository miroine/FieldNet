from physics.multiphase import homogeneous_dp_bar
from physics.beggs_brill import beggs_brill_dp_bar

def tubing_bhp_bar(q_liq_m3d, whp_bar, depth_m, tubing_id_m, roughness_m=4.5e-5,
                   temperature_c=70.0, water_cut=0.0, gor_sm3sm3=0.0, api=35.0, gas_sg=0.75, correlation='Beggs-Brill'):
    """Bottom-hole pressure required to lift q from depth to wellhead (vertical tubing)."""
    pavg=max(whp_bar+50.0,1.0)
    # fixed point on average pressure because fluid density depends on pressure
    dp=0.0; props={}
    for _ in range(12):
        fn=beggs_brill_dp_bar if correlation=='Beggs-Brill' else homogeneous_dp_bar
        dp,props=fn(q_liq_m3d,depth_m,tubing_id_m,roughness_m,depth_m,pavg,temperature_c,water_cut,gor_sm3sm3,api,gas_sg)
        bhp=whp_bar+dp; newavg=max((whp_bar+bhp)/2.0,1.0)
        if abs(newavg-pavg)<1e-5: break
        pavg=0.5*(pavg+newavg)
    return whp_bar+dp, props
