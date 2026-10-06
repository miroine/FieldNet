from physics.multiphase import homogeneous_dp_bar
from physics.beggs_brill import beggs_brill_dp_bar

DEFAULT_VLP_SEGMENTS = 12


def _corr_fn(correlation):
    """Correlation from the registry (physics/correlations.py); unknown names keep the legacy behaviour."""
    if str(correlation).lower().startswith('beggs'): return beggs_brill_dp_bar
    try:
        from physics.correlations import get_correlation
        return get_correlation(correlation)
    except Exception:
        return homogeneous_dp_bar


def tubing_bhp_bar(q_liq_m3d, whp_bar, depth_m, tubing_id_m, roughness_m=4.5e-5,
                   temperature_c=70.0, water_cut=0.0, gor_sm3sm3=0.0, api=35.0, gas_sg=0.75, correlation='Beggs-Brill',
                   *, segments=DEFAULT_VLP_SEGMENTS, extra_gas_sm3d=0.0, gas_injection_depth_m=None,
                   bottomhole_temperature_c=None, geometry=None, profile=None, thermal=None):
    """Bottom-hole pressure required to lift q from depth to wellhead (vertical tubing).

    The column is marched from the wellhead down in ``segments`` steps with a
    predictor-corrector on the segment mean pressure. The previous implementation used
    one average pressure for the whole string, so gas liberation near the wellhead was
    ignored and the column behaved almost like dead liquid (wells died far too early).

    ``extra_gas_sm3d`` is gas-lift gas present above ``gas_injection_depth_m`` (default:
    full depth). Temperature varies linearly from ``temperature_c`` at the wellhead to
    ``bottomhole_temperature_c`` (default: same value).

    ``geometry`` (optional) is the wellhead->bottom segment list from ``physics.trajectory.tubing_segments``
    (deviated trajectory + completion diameters); the straight vertical string is used when it is None.
    ``thermal`` (from ``physics.thermal.well_thermal_inputs``) replaces the linear temperature by the Ramey wellbore profile; the wellhead
    temperature is then a result (``props['wellhead_temperature_c']``).
    ``profile`` (a list) receives one row per segment {md_m, tvd_m, pressure_bar, velocity_ms, ...} when given.
    """
    depth=max(float(depth_m),0.0); n=max(int(segments),1); fn=_corr_fn(correlation)
    if depth<=0: return float(whp_bar), {'liquid_holdup':1.0,'gas_fraction':0.0}
    t_top=float(temperature_c); t_bot=float(temperature_c if bottomhole_temperature_c is None else bottomhole_temperature_c)
    inj=depth if gas_injection_depth_m is None else min(max(float(gas_injection_depth_m),0.0),depth)
    if geometry:
        segs=[(g['length_m'],g['dz_m'],g['id_m'],g.get('roughness_m',roughness_m)) for g in geometry]; n=len(segs)
        tvd_total=max(sum(sg[1] for sg in segs),1e-9)
    else:
        dl=depth/n; segs=[(dl,dl,tubing_id_m,roughness_m)]*n; tvd_total=depth
    p=float(whp_bar); hold=0.0; gasf=0.0; props={}; z=0.0; md=0.0
    t_of_z=None; t_wh=t_top
    if thermal:
        from physics.thermal import Stream, ramey_profile
        stream=Stream(q_liq_m3d,water_cut,gor_sm3sm3,api,gas_sg,extra_gas_sm3d=extra_gas_sm3d,gas_cp_override=thermal.get('gas_cp'))
        th=dict(thermal); th['depth']=tvd_total; t_of_z,t_wh=ramey_profile(th,stream,whp_bar)
    for (dl,dz,idm,rough) in segs:
        z_mid=z+0.5*dz; z+=dz
        tt=t_of_z(z_mid) if t_of_z is not None else t_top+(t_bot-t_top)*z_mid/tvd_total
        g_extra=float(extra_gas_sm3d) if z_mid<=inj else 0.0
        # Positive liquid flow is upward; marching downward, the pressure rises by dp.
        dp1,_=fn(q_liq_m3d,dl,idm,rough,dz,max(p,1.0),tt,water_cut,gor_sm3sm3,api,gas_sg,extra_free_gas_sm3d=g_extra)
        pm=max(p+0.5*dp1,1.0)
        dp,props=fn(q_liq_m3d,dl,idm,rough,dz,pm,tt,water_cut,gor_sm3sm3,api,gas_sg,extra_free_gas_sm3d=g_extra)
        p+=dp; hold+=props['liquid_holdup']; gasf+=props.get('gas_fraction',0.0); md+=dl
        if profile is not None: profile.append({'md_m':md,'tvd_m':z,'pressure_bar':p,'velocity_ms':props.get('mixture_velocity_ms'),'liquid_holdup':props.get('liquid_holdup'),'rho_kgm3':props.get('rho'),'regime':props.get('flow_regime')})
    out=dict(props); out['wellhead_temperature_c']=t_wh; out['liquid_holdup']=hold/n; out['gas_fraction']=gasf/n; out['segments']=n
    return p, out
