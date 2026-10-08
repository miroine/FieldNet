"""Single source of truth for a producing well inside the network.

Before this module the network kernel, the nodal-analysis tab and the audit each had
their own well logic: the network ignored skin, artificial lift and the selected VLP
model, and solved IPR/VLP with a nested optimiser on every residual evaluation.

Canonical units: bar, m3/d (liquid at stock-tank conditions), Sm3/d gas, m, degC.
"""
from __future__ import annotations
import math
from physics.advanced_wells import skin_adjusted_pi
from physics.vlp import tubing_bhp_bar, DEFAULT_VLP_SEGMENTS

# Typical ln(re/rw)-0.75 for a vertical well (re/rw ~ 2000-3000). Used to convert a
# mechanical skin into a PI multiplier: J = J0 * C / (C + S).
DEFAULT_SKIN_REFERENCE = 7.0


def _f(p, key, default):
    try:
        v=float(p.get(key, default))
    except (TypeError, ValueError):
        return float(default)
    return float(default) if not math.isfinite(v) else v


def well_settings(prm: dict) -> dict:
    """Normalise a well's params dict into the quantities the well model needs."""
    p=prm or {}
    skin=_f(p,'skin',0.0); cref=_f(p,'skin_reference_factor',DEFAULT_SKIN_REFERENCE)
    pi0=max(_f(p,'pi_m3d_bar',10.0),0.0); qmax0=max(_f(p,'qmax_m3d',1500.0),0.0)
    mult=skin_adjusted_pi(1.0,skin,cref)
    # Calibration multiplier on productivity (forecast assumption; network/calibration.py). Scales PI, Vogel qmax and gas C.
    pm=min(max(_f(p,'productivity_multiplier',1.0),1e-3),1e3)
    # Darcy IPR (physics/darcy_ipr.py): PI / gas C computed from permeability, pay, drainage radius, geometry (vertical / deviated / horizontal) and layers.
    # The skin is then part of the Darcy denominator, so the empirical skin multiplier is not applied on top.
    iname=_ipr_name(p.get('ipr_model','PI')); darcy=None
    if p.get('darcy') in (True,'true','True',1):
        from physics.darcy_ipr import darcy_ipr
        darcy=darcy_ipr(p,max(_f(p,'reservoir_pressure_bar',200.0),0.0),'gas' if iname=='Gas' else 'oil')
        mult=1.0; pi0=darcy['pi']; qmax0=darcy['pi']*max(_f(p,'reservoir_pressure_bar',200.0),0.0)/1.8
    mult*=pm
    lift=str(p.get('lift_type','none') or 'none').lower().replace(' ','_')
    available=p.get('available',True)
    if isinstance(available,str): available=available.strip().lower() not in ('false','0','no','off')
    opening=_f(p,'opening_factor',1.0)
    max_rate=p.get('max_liquid_rate_m3d')
    try: max_rate=float(max_rate) if max_rate is not None else math.inf
    except (TypeError, ValueError): max_rate=math.inf
    if not math.isfinite(max_rate): max_rate=math.inf
    enforce=str(p.get('rate_limit_mode','enforce')).lower()!='report'
    # Per-phase well limits -> equivalent liquid-rate cap at the current water cut / GOR (tank updates change wc/gor, so the
    # liquid cap follows the phase mix). Same treatment as the liquid limit incl. rate_limit_mode='report'.
    wc_=min(max(_f(p,'water_cut',0.2),0.0),0.9999); gor_=max(_f(p,'gor_sm3sm3',100.0),0.0)
    for key,frac in (('max_oil_rate_m3d',1.0-wc_),('max_water_rate_m3d',wc_),('max_gas_rate_sm3d',(1.0-wc_)*gor_)):
        v=p.get(key)
        if v is None: continue
        try: v=float(v)
        except (TypeError, ValueError): continue
        if frac>1e-9 and math.isfinite(v) and v>=0: max_rate=min(max_rate,v/frac)
    if not enforce: max_rate=math.inf
    # Minimum stable rate per phase: below it the well is shut in (liquid loading / minimum facility turndown). Like the maxima, the
    # per-phase minima are converted to an equivalent liquid rate at the current water cut / GOR (water minimum needs wc>0, gas needs gor>0).
    min_liq=0.0
    for key,frac in (('min_liquid_rate_m3d',1.0),('min_oil_rate_m3d',1.0-wc_),('min_water_rate_m3d',wc_),('min_gas_rate_sm3d',(1.0-wc_)*gor_)):
        v=p.get(key)
        if v is None: continue
        try: v=float(v)
        except (TypeError, ValueError): continue
        if frac>1e-9 and math.isfinite(v) and v>0: min_liq=max(min_liq,v/frac)
    # Temporary cap written by the capacity-constraint enforcer (never by the user).
    net_cap=p.get('_network_cap_m3d')
    if net_cap is not None:
        try: max_rate=min(max_rate,max(float(net_cap),0.0))
        except (TypeError, ValueError): pass
    # Recovery assumptions (network/assumptions.py): tank target-RF / well EUR taper cap, written by the forecast only.
    ac=p.get('_assumption_cap_m3d')
    if ac is not None:
        try: max_rate=min(max_rate,max(float(ac),0.0))
        except (TypeError, ValueError): pass
    # Decline-curve / prediction-source potential (network/prediction_sources.py); same treatment as the network cap.
    pot_cap=p.get('_potential_cap_m3d')
    if pot_cap is not None:
        try: max_rate=min(max_rate,max(float(pot_cap),0.0))
        except (TypeError, ValueError): pass
    depth=max(_f(p,'depth_m',2000.0),1.0)
    geometry=None
    if p.get('trajectory') or p.get('completion'):
        from physics.trajectory import tubing_segments, well_total_depth
        try:
            geometry=tubing_segments(p); depth=max(float(well_total_depth(p)[1]),1.0)
        except Exception: geometry=None
    from physics.thermal import well_thermal_inputs
    tub_id=max(_f(p,'tubing_id_m',0.0762),1e-3)
    return {
        'pvt_prm':{'pvt':p.get('pvt')} if p.get('pvt') else None,
        'thermal':well_thermal_inputs(p,depth,tub_id),
        'geometry':geometry,
        'pr':max(_f(p,'reservoir_pressure_bar',200.0),0.0),
        'ipr_model':_ipr_name(p.get('ipr_model','PI')),
        'gas_c':(darcy['gas_c'] if darcy else max(_f(p,'gas_c_sm3d_bar2n',50.0),0.0))*pm, 'gas_n':(1.0 if darcy else min(max(_f(p,'gas_n',1.0),0.5),1.0)), 'darcy':darcy,
        'pi':pi0*mult, 'qmax':qmax0*mult,
        'depth':depth, 'tubing_id':max(_f(p,'tubing_id_m',0.0762),1e-3),
        'roughness':max(_f(p,'tubing_roughness_m',4.5e-5),0.0),
        'temperature':_f(p,'temperature_c',70.0),
        'bh_temperature':_f(p,'bottomhole_temperature_c',_f(p,'temperature_c',70.0)),
        'water_cut':min(max(_f(p,'water_cut',0.2),0.0),0.9999),
        'gor':max(_f(p,'gor_sm3sm3',100.0),0.0), 'api':_f(p,'api',35.0), 'gas_sg':_f(p,'gas_sg',0.75),
        'correlation':str(p.get('vlp_model',p.get('correlation','Beggs-Brill')) or 'Beggs-Brill'),
        'vlp_dp_multiplier':min(max(_f(p,'vlp_dp_multiplier',1.0),0.2),5.0),
        'lift_type':lift,
        'gas_lift_sm3d':max(_f(p,'gas_lift_injection_sm3d',0.0),0.0) if lift=='gas_lift' else 0.0,
        'gas_lift_depth':min(max(_f(p,'gas_lift_depth_m',depth),0.0),depth),
        'esp':{'rated_rate_m3d':max(_f(p,'esp_rated_rate_m3d',1000.0),1e-6),'shutoff_head_bar':max(_f(p,'esp_shutoff_head_bar',80.0),0.0),
               'speed_fraction':max(_f(p,'esp_speed_fraction',1.0),1e-3)} if lift=='esp' else None,
        'lift_assist_bar':max(_f(p,'lift_assist_bar',0.0),0.0),
        'segments':max(int(_f(p,'vlp_segments',DEFAULT_VLP_SEGMENTS)),1),
        'open':bool(available) and opening>0 and {'PI':pi0>0,'Vogel':qmax0>0,'Gas':(darcy['gas_c'] if darcy else _f(p,'gas_c_sm3d_bar2n',50.0))>0}[_ipr_name(p.get('ipr_model','PI'))],
        'max_rate':max_rate,
        'max_rate_reported':max_rate,
        'skin':skin,
        # Below this rate a producer cannot sustain stable flow (liquid loading / heading);
        # it is reported as shut in rather than as a numerically fragile trickle.
        'min_rate':max(max(_f(p,'min_rate_m3d',0.01 if _ipr_name(p.get('ipr_model','PI'))=='Gas' else 5.0),0.0),min_liq),
    }


def _ipr_name(x):
    x=str(x or 'PI').lower()
    if x.startswith('vogel'): return 'Vogel'
    if x.startswith('gas'): return 'Gas'
    return 'PI'


def _gas_per_liquid(s):
    return max(s['gor']*(1-s['water_cut']),1e-6)


def ipr_rate(pwf, s):
    """Reservoir inflow [m3/d] at flowing bottom-hole pressure ``pwf``."""
    pr=s['pr']
    if pr<=0: return 0.0
    if s['ipr_model']=='Vogel':
        x=min(max(pwf/pr,0.0),1.0); return max(0.0,s['qmax']*(1-0.2*x-0.8*x*x))
    if s['ipr_model']=='Gas':
        d=pr*pr-max(pwf,0.0)**2
        return 0.0 if d<=0 else s['gas_c']*d**s['gas_n']/_gas_per_liquid(s)
    return max(0.0,s['pi']*(pr-pwf))


def ipr_pwf(q, s):
    """Inverse IPR, continuously extended beyond the absolute-open-flow rate so the
    network equations stay smooth (negative values simply mean 'impossible')."""
    pr=s['pr']; q=float(q)
    if s['ipr_model']=='Vogel':
        qmax=max(s['qmax'],1e-9); y=q/qmax
        if y<=0: return pr - y*pr/1.8      # dpwf/dq at q=0 is -pr/(1.8 qmax)
        if y>=1: return -(y-1.0)*pr/0.2    # dpwf/dq at AOF is -pr/(0.2 qmax)
        return pr*(-0.2+math.sqrt(3.24-3.2*y))/1.6
    if s['ipr_model']=='Gas':
        # Backpressure deliverability q_g = C (pr^2 - pwf^2)^n, expressed per m3/d of liquid.
        qg=q*_gas_per_liquid(s); c=max(s['gas_c'],1e-12)
        if qg<=0: return pr - qg/c/max(pr,1.0)   # smooth extension below zero rate
        v=pr*pr-(qg/c)**(1.0/s['gas_n'])
        return math.sqrt(v) if v>=0 else -math.sqrt(-v)
    return pr - q/max(s['pi'],1e-9)


def esp_head_bar_simple(q, esp):
    if not esp: return 0.0
    s=esp['speed_fraction']; h0=esp['shutoff_head_bar']*s*s; qr=max(esp['rated_rate_m3d']*s,1e-9)
    return max(h0*(1.0-(max(q,0.0)/(1.35*qr))**2),0.0)


def vlp_bhp(q, whp, s):
    """Bottom-hole pressure required to produce q at wellhead pressure whp, including lift."""
    from physics.pvt_model import fluid_scope
    with fluid_scope(s.get('pvt_prm'),s['gor'],s['api'],s['gas_sg']):
        bhp,props=tubing_bhp_bar(max(q,0.0),whp,s['depth'],s['tubing_id'],s['roughness'],s['temperature'],s['water_cut'],s['gor'],s['api'],s['gas_sg'],
                                 s['correlation'],segments=s['segments'],extra_gas_sm3d=s['gas_lift_sm3d'],gas_injection_depth_m=s['gas_lift_depth'],
                                 bottomhole_temperature_c=s['bh_temperature'],geometry=s.get('geometry'),thermal=s.get('thermal'))
    m=s.get('vlp_dp_multiplier',1.0)
    if m!=1.0: bhp=whp+m*(bhp-whp)          # matched tubing pressure-drop multiplier (well-test / flowing-gradient match)
    assist=s['lift_assist_bar']+esp_head_bar_simple(q,s['esp'])
    return bhp-assist, props


def excess_bar(q, whp, s):
    """Pressure the reservoir can deliver above what the tubing needs at rate q.

    >0 : the well would flow faster; =0 : natural operating point; <0 : cannot sustain q.
    """
    return ipr_pwf(q,s) - vlp_bhp(q,whp,s)[0]


def rate_capacity(s):
    if s['ipr_model']=='Gas': return s['gas_c']*(s['pr']**2)**s['gas_n']/_gas_per_liquid(s)
    return s['pi']*s['pr'] if s['ipr_model']=='PI' else s['qmax']


def solve_well_rate(whp, s, points=24):
    """Stable operating rate at fixed wellhead pressure (largest stable IPR/VLP root).

    Returns (rate_m3d, status) with status in shut_in, dead, flowing, rate_limited.
    """
    if not s['open']: return 0.0, 'shut_in'
    qcap=rate_capacity(s)
    if qcap<=0: return 0.0, 'dead'
    grid=[qcap*i/points for i in range(points+1)]
    # Scan from the highest rate downwards and stop at the first (largest) stable root: the lower grid points are
    # only evaluated when needed. Root refinement uses Brent's method (same root as the old bisection, ~3x fewer VLP evaluations).
    root=None; f_hi=excess_bar(grid[points],whp,s)
    for i in range(points-1,-1,-1):
        f_lo=excess_bar(grid[i],whp,s)
        if f_lo>0 and f_hi<=0:
            from scipy.optimize import brentq
            try: root=brentq(lambda x: excess_bar(x,whp,s),grid[i],grid[i+1],xtol=1e-6*max(1.0,qcap),rtol=1e-10,maxiter=60)
            except (ValueError, RuntimeError): root=0.5*(grid[i]+grid[i+1])
            break
        f_hi=f_lo
    if root is None: return 0.0, 'dead'
    if root<s.get('min_rate',0.0): return 0.0, 'below_min_rate'
    if root>s['max_rate']: return s['max_rate'], 'rate_limited'
    return root, 'flowing'


def well_state(q, whp, s):
    """Diagnostics for a solved well rate."""
    bhp,props=vlp_bhp(q,whp,s); pwf=ipr_pwf(q,s)
    wc=s['water_cut']; oil=q*(1-wc)
    status=s.get('status_override') or ('shut_in' if not s['open'] else ('dead' if q<=1e-6 else ('rate_limited' if q>=s['max_rate']-1e-6 else 'flowing')))
    return {'liquid_rate_m3d':q,'oil_rate_m3d':oil,'water_rate_m3d':q*wc,'gas_rate_sm3d':oil*s['gor'],
            'gas_lift_sm3d':s['gas_lift_sm3d'],'bhp_bar':max(pwf,bhp) if q>1e-9 else bhp,'vlp_bhp_bar':bhp,'ipr_pwf_bar':pwf,
            'whp_bar':whp,'choke_dp_equivalent_bar':max(pwf-bhp,0.0) if q>1e-9 else 0.0,
            'wellhead_temperature_c':props.get('wellhead_temperature_c',s['temperature']),
            'liquid_holdup':props.get('liquid_holdup',1.0),'gas_fraction':props.get('gas_fraction',0.0),'status':status,
            'reservoir_pressure_bar':s['pr'],'vlp_model':s['correlation'],'lift_type':s['lift_type']}
