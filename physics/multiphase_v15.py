"""v15 multiphase diagnostics layered on the existing Beggs-Brill screening kernel."""
from physics.beggs_brill import beggs_brill_dp_bar

def beggs_brill_components_bar(*args, **kwargs):
    """Return pressure-loss decomposition.

    Hydrostatic and friction terms are reconstructed using the same solved local
    state as the v14 kernel. Acceleration is reported explicitly as zero because
    the current steady screening kernel does not yet solve a compressible momentum
    acceleration term; this avoids falsely attributing residual pressure loss.
    """
    dp, state=beggs_brill_dp_bar(*args, **kwargs)
    # Re-run zero-elevation case to isolate friction using identical local state inputs.
    a=list(args)
    if len(a) >= 5:
        dz=a[4]; a[4]=0.0
        fric,_=beggs_brill_dp_bar(*a, **kwargs)
    else:
        dz=kwargs.get('dz_m',0.0); kw=dict(kwargs); kw['dz_m']=0.0
        fric,_=beggs_brill_dp_bar(*args, **kw)
    hydro=dp-fric
    return {'total_dp_bar':dp,'friction_dp_bar':fric,'hydrostatic_dp_bar':hydro,
            'acceleration_dp_bar':0.0,'acceleration_model':'not_in_screening_kernel',**state}

def detailed_pressure_profile(q,length_m,diameter_m,roughness_m,dz_m,p_in_bar,temperature_c,
                              water_cut=0,gor_sm3sm3=0,api=35,gas_sg=.75,segments=30):
    if segments < 1: raise ValueError('segments must be >=1')
    rows=[]; p=float(p_in_bar)
    for i in range(segments):
        x0=length_m*i/segments; x1=length_m*(i+1)/segments
        r=beggs_brill_components_bar(q,length_m/segments,diameter_m,roughness_m,dz_m/segments,
                                     max(p,1.0),temperature_c,water_cut,gor_sm3sm3,api,gas_sg)
        pout=p-r['total_dp_bar']
        rows.append({'segment':i+1,'x0_m':x0,'x1_m':x1,'p_in_bar':p,'p_out_bar':pout,
                     'dp_total_bar':r['total_dp_bar'],'dp_friction_bar':r['friction_dp_bar'],
                     'dp_hydrostatic_bar':r['hydrostatic_dp_bar'],'dp_acceleration_bar':0.0,
                     'liquid_holdup':r['liquid_holdup'],'mixture_velocity_ms':r['mixture_velocity_ms'],
                     'reynolds':r['reynolds'],'flow_regime':r['flow_regime']})
        p=pout
    return {'segments':rows,'outlet_pressure_bar':p,
            'total_dp_bar':float(p_in_bar)-p,
            'friction_dp_bar':sum(x['dp_friction_bar'] for x in rows),
            'hydrostatic_dp_bar':sum(x['dp_hydrostatic_bar'] for x in rows),
            'acceleration_dp_bar':0.0}
