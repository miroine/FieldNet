from physics.multiphase import homogeneous_dp_bar
from physics.beggs_brill import beggs_brill_dp_bar

DEFAULT_VLP_SEGMENTS = 12


def _corr_fn(correlation):
    return beggs_brill_dp_bar if str(correlation).lower().startswith('beggs') else homogeneous_dp_bar


def tubing_bhp_bar(q_liq_m3d, whp_bar, depth_m, tubing_id_m, roughness_m=4.5e-5,
                   temperature_c=70.0, water_cut=0.0, gor_sm3sm3=0.0, api=35.0, gas_sg=0.75, correlation='Beggs-Brill',
                   *, segments=DEFAULT_VLP_SEGMENTS, extra_gas_sm3d=0.0, gas_injection_depth_m=None,
                   bottomhole_temperature_c=None):
    """Bottom-hole pressure required to lift q from depth to wellhead (vertical tubing).

    The column is marched from the wellhead down in ``segments`` steps with a
    predictor-corrector on the segment mean pressure. The previous implementation used
    one average pressure for the whole string, so gas liberation near the wellhead was
    ignored and the column behaved almost like dead liquid (wells died far too early).

    ``extra_gas_sm3d`` is gas-lift gas present above ``gas_injection_depth_m`` (default:
    full depth). Temperature varies linearly from ``temperature_c`` at the wellhead to
    ``bottomhole_temperature_c`` (default: same value).
    """
    depth=max(float(depth_m),0.0); n=max(int(segments),1); fn=_corr_fn(correlation)
    if depth<=0: return float(whp_bar), {'liquid_holdup':1.0,'gas_fraction':0.0}
    t_top=float(temperature_c); t_bot=float(temperature_c if bottomhole_temperature_c is None else bottomhole_temperature_c)
    inj=depth if gas_injection_depth_m is None else min(max(float(gas_injection_depth_m),0.0),depth)
    dl=depth/n; p=float(whp_bar); hold=0.0; gasf=0.0; props={}
    for i in range(n):
        z_mid=(i+0.5)*dl
        tt=t_top+(t_bot-t_top)*z_mid/depth
        g_extra=float(extra_gas_sm3d) if z_mid<=inj else 0.0
        # Positive liquid flow is upward; marching downward, the pressure rises by dp.
        dp1,_=fn(q_liq_m3d,dl,tubing_id_m,roughness_m,dl,max(p,1.0),tt,water_cut,gor_sm3sm3,api,gas_sg,extra_free_gas_sm3d=g_extra)
        pm=max(p+0.5*dp1,1.0)
        dp,props=fn(q_liq_m3d,dl,tubing_id_m,roughness_m,dl,pm,tt,water_cut,gor_sm3sm3,api,gas_sg,extra_free_gas_sm3d=g_extra)
        p+=dp; hold+=props['liquid_holdup']; gasf+=props.get('gas_fraction',0.0)
    out=dict(props); out['liquid_holdup']=hold/n; out['gas_fraction']=gasf/n; out['segments']=n
    return p, out
