"""FieldNet v20 well-performance and artificial-lift screening layer.

Production-engineering models for nodal analysis and screening. Gas-lift and ESP
models are transparent engineering approximations, not vendor/design simulators.
Canonical units: bar, m3/d, m, degC, kW.
"""
from dataclasses import dataclass, asdict
import math
import numpy as np
from physics.ipr import ipr_rate_m3d
from physics.vlp import tubing_bhp_bar
from physics.well_completion import completion_deliverability

@dataclass
class NodalPoint:
    rate_m3d: float
    ipr_bhp_bar: float
    vlp_bhp_bar: float
    residual_bar: float


def _finite_positive(name, value, allow_zero=False):
    v=float(value)
    if not math.isfinite(v) or (v < 0 if allow_zero else v <= 0):
        raise ValueError(f'{name} must be finite and {"non-negative" if allow_zero else "positive"}')
    return v


def ipr_bhp_bar(rate_m3d, reservoir_pressure_bar, model='PI', pi_m3d_bar=10.0, qmax_m3d=1500.0):
    """Invert PI/Vogel IPR to pwf for nodal intersection."""
    q=max(float(rate_m3d),0.0); pr=_finite_positive('reservoir_pressure_bar',reservoir_pressure_bar)
    if str(model).lower()=='pi':
        j=_finite_positive('pi_m3d_bar',pi_m3d_bar)
        return max(pr-q/j,0.0)
    qmax=_finite_positive('qmax_m3d',qmax_m3d)
    # Vogel: q/qmax=1-0.2x-0.8x^2, x=pwf/pr. Positive physical root.
    y=min(max(q/qmax,0.0),1.0)
    x=(-0.2+math.sqrt(3.24-3.2*y))/1.6
    return pr*min(max(x,0.0),1.0)


def gas_lift_assist_bar(gas_injection_sm3d, depth_m, tubing_id_m, *, max_assist_bar=100.0,
                        design_rate_sm3d=30000.0, efficiency=0.75):
    """Screening gas-lift unloading assistance with diminishing returns.

    Assistance scales with hydrostatic opportunity, injection saturation and an
    explicit efficiency. It is intentionally not a valve/string mechanistic model.
    """
    q=max(float(gas_injection_sm3d),0.0); depth=_finite_positive('depth_m',depth_m)
    dia=_finite_positive('tubing_id_m',tubing_id_m); qd=_finite_positive('design_rate_sm3d',design_rate_sm3d)
    eff=min(max(float(efficiency),0.0),1.0)
    hydro=min(0.00980665*depth*0.70, max(float(max_assist_bar),0.0))
    saturation=1.0-math.exp(-q/qd)
    diameter_factor=min(max(0.0889/dia,0.6),1.5)
    return min(max(float(max_assist_bar),0.0), hydro*saturation*eff*diameter_factor)


def optimize_gas_lift(reservoir_pressure_bar, whp_bar, depth_m, tubing_id_m, *,
                      ipr_model='PI', pi_m3d_bar=10.0, qmax_m3d=1500.0,
                      max_injection_sm3d=60000.0, steps=31, **vlp_kwargs):
    """Screen gas-lift rates and return the maximum stable nodal liquid rate."""
    candidates=[]
    for inj in np.linspace(0,max(float(max_injection_sm3d),0.0),max(int(steps),2)):
        assist=gas_lift_assist_bar(inj,depth_m,tubing_id_m)
        r=nodal_operating_point(reservoir_pressure_bar,whp_bar,depth_m,tubing_id_m,
            ipr_model=ipr_model,pi_m3d_bar=pi_m3d_bar,qmax_m3d=qmax_m3d,lift_assist_bar=assist,**vlp_kwargs)
        candidates.append({'gas_injection_sm3d':float(inj),'assist_bar':assist,**r})
    feasible=[x for x in candidates if x['converged']]
    best=max(feasible,key=lambda x:x['rate_m3d']) if feasible else None
    return {'model':'gas_lift_screening_v20','best':best,'candidates':candidates,
            'limitations':['Screening optimization only; no valve-depth, injection-pressure or compressor-network model.']}


def esp_head_bar(rate_m3d, *, rated_rate_m3d=1000.0, shutoff_head_bar=120.0,
                 stages=1.0, speed_fraction=1.0):
    """Parabolic ESP head curve with affinity-law speed scaling."""
    q=max(float(rate_m3d),0.0); qr=_finite_positive('rated_rate_m3d',rated_rate_m3d)
    h0=_finite_positive('shutoff_head_bar',shutoff_head_bar); st=_finite_positive('stages',stages)
    s=_finite_positive('speed_fraction',speed_fraction)
    qr_s=qr*s; h_shut=h0*st*s*s
    return max(h_shut*(1.0-(q/max(1.35*qr_s,1e-12))**2),0.0)


def esp_performance(rate_m3d, *, rated_rate_m3d=1000.0, shutoff_head_bar=120.0,
                    stages=1.0, speed_fraction=1.0, bep_fraction=1.0,
                    min_rate_fraction=0.6, max_rate_fraction=1.2,
                    fluid_density_kgm3=850.0, efficiency=0.65, available_npsh_m=None,
                    required_npsh_m=5.0):
    q=max(float(rate_m3d),0.0); qr=_finite_positive('rated_rate_m3d',rated_rate_m3d)*_finite_positive('speed_fraction',speed_fraction)
    head=esp_head_bar(q,rated_rate_m3d=rated_rate_m3d,shutoff_head_bar=shutoff_head_bar,stages=stages,speed_fraction=speed_fraction)
    rho=_finite_positive('fluid_density_kgm3',fluid_density_kgm3); eff=_finite_positive('efficiency',efficiency)
    power_kw=(q/86400.0)*(head*1e5)/max(eff,1e-9)/1000.0
    lo=qr*float(min_rate_fraction); hi=qr*float(max_rate_fraction)
    warnings=[]
    if q<lo: warnings.append('below_recommended_operating_rate')
    if q>hi: warnings.append('above_recommended_operating_rate')
    npsh_ok=True if available_npsh_m is None else float(available_npsh_m)>=float(required_npsh_m)
    if not npsh_ok: warnings.append('insufficient_npsh_margin')
    return {'model':'esp_screening_v20','head_bar':head,'hydraulic_power_kw':power_kw,
            'recommended_min_rate_m3d':lo,'recommended_max_rate_m3d':hi,'within_rate_envelope':lo<=q<=hi,
            'npsh_ok':npsh_ok,'warnings':warnings,
            'limitations':['Generic parabolic/affinity-law ESP screening; use calibrated vendor curves for design.']}


def vlp_bhp_with_lift(rate_m3d, whp_bar, depth_m, tubing_id_m, *, vlp_model='Beggs-Brill',
                      lift_type='none', gas_injection_sm3d=0.0, esp=None, lift_assist_bar=0.0, **kwargs):
    corr='Beggs-Brill' if str(vlp_model).lower().startswith('beggs') else 'Homogeneous'
    natural,props=tubing_bhp_bar(rate_m3d,whp_bar,depth_m,tubing_id_m,correlation=corr,**kwargs)
    lt=str(lift_type).lower(); assist=max(float(lift_assist_bar),0.0); lift_detail={'type':lt}
    if lt in ('gas_lift','gas lift'):
        assist=max(assist,gas_lift_assist_bar(gas_injection_sm3d,depth_m,tubing_id_m))
        lift_detail.update({'gas_injection_sm3d':float(gas_injection_sm3d),'assist_bar':assist})
    elif lt=='esp':
        ep=esp_performance(rate_m3d,**(esp or {})); assist=max(assist,ep['head_bar']); lift_detail.update(ep); lift_detail['assist_bar']=assist
    return max(natural-assist,0.0),props,lift_detail


def nodal_operating_point(reservoir_pressure_bar, whp_bar, depth_m, tubing_id_m, *,
                          ipr_model='PI', pi_m3d_bar=10.0, qmax_m3d=1500.0,
                          vlp_model='Beggs-Brill', lift_type='none', gas_injection_sm3d=0.0,
                          esp=None, lift_assist_bar=0.0, points=120, **vlp_kwargs):
    pr=_finite_positive('reservoir_pressure_bar',reservoir_pressure_bar)
    qcap=(pr*pi_m3d_bar if str(ipr_model).lower()=='pi' else qmax_m3d)
    qgrid=np.linspace(0,max(float(qcap),1e-9),max(int(points),20))
    rows=[]
    for q in qgrid:
        ip=ipr_bhp_bar(q,pr,ipr_model,pi_m3d_bar,qmax_m3d)
        vp,_,ld=vlp_bhp_with_lift(q,whp_bar,depth_m,tubing_id_m,vlp_model=vlp_model,lift_type=lift_type,
            gas_injection_sm3d=gas_injection_sm3d,esp=esp,lift_assist_bar=lift_assist_bar,**vlp_kwargs)
        rows.append(NodalPoint(float(q),ip,vp,ip-vp))
    roots=[]
    for a,b in zip(rows[:-1],rows[1:]):
        if a.residual_bar==0 or a.residual_bar*b.residual_bar<0:
            den=(a.residual_bar-b.residual_bar); f=0.0 if abs(den)<1e-12 else a.residual_bar/den
            roots.append(a.rate_m3d+f*(b.rate_m3d-a.rate_m3d))
    if not roots:
        return {'converged':False,'rate_m3d':0.0,'bhp_bar':None,'residual_bar':min(abs(x.residual_bar) for x in rows),'curve':[asdict(x) for x in rows],'warnings':['no_nodal_intersection']}
    q=float(max(roots)); bhp=ipr_bhp_bar(q,pr,ipr_model,pi_m3d_bar,qmax_m3d)
    return {'converged':True,'rate_m3d':q,'bhp_bar':bhp,'residual_bar':0.0,'curve':[asdict(x) for x in rows],'warnings':[]}


def well_performance_qa(result, reservoir_pressure_bar, *, min_bhp_bar=1.0, max_drawdown_fraction=0.8):
    warnings=[]
    if not result.get('converged'): warnings.append('nodal_solution_not_found')
    bhp=result.get('bhp_bar')
    if bhp is not None:
        if bhp<float(min_bhp_bar): warnings.append('below_minimum_bhp')
        if (float(reservoir_pressure_bar)-bhp)/max(float(reservoir_pressure_bar),1e-9)>float(max_drawdown_fraction): warnings.append('extreme_drawdown')
    return {'acceptable':not warnings,'warnings':warnings,'checks':['nodal_intersection','minimum_bhp','drawdown_fraction']}
