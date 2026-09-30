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
    lift=str(p.get('lift_type','none') or 'none').lower().replace(' ','_')
    available=p.get('available',True)
    if isinstance(available,str): available=available.strip().lower() not in ('false','0','no','off')
    opening=_f(p,'opening_factor',1.0)
    max_rate=p.get('max_liquid_rate_m3d')
    try: max_rate=float(max_rate) if max_rate is not None else math.inf
    except (TypeError, ValueError): max_rate=math.inf
    if not math.isfinite(max_rate): max_rate=math.inf
    enforce=str(p.get('rate_limit_mode','enforce')).lower()!='report'
    if not enforce: max_rate=math.inf
    # Temporary cap written by the capacity-constraint enforcer (never by the user).
    net_cap=p.get('_network_cap_m3d')
    if net_cap is not None:
        try: max_rate=min(max_rate,max(float(net_cap),0.0))
        except (TypeError, ValueError): pass
    depth=max(_f(p,'depth_m',2000.0),1.0)
    return {
        'pr':max(_f(p,'reservoir_pressure_bar',200.0),0.0),
        'ipr_model':'Vogel' if str(p.get('ipr_model','PI')).lower()=='vogel' else 'PI',
        'pi':pi0*mult, 'qmax':qmax0*mult,
        'depth':depth, 'tubing_id':max(_f(p,'tubing_id_m',0.0762),1e-3),
        'roughness':max(_f(p,'tubing_roughness_m',4.5e-5),0.0),
        'temperature':_f(p,'temperature_c',70.0),
        'bh_temperature':_f(p,'bottomhole_temperature_c',_f(p,'temperature_c',70.0)),
        'water_cut':min(max(_f(p,'water_cut',0.2),0.0),0.9999),
        'gor':max(_f(p,'gor_sm3sm3',100.0),0.0), 'api':_f(p,'api',35.0), 'gas_sg':_f(p,'gas_sg',0.75),
        'correlation':str(p.get('vlp_model',p.get('correlation','Beggs-Brill')) or 'Beggs-Brill'),
        'lift_type':lift,
        'gas_lift_sm3d':max(_f(p,'gas_lift_injection_sm3d',0.0),0.0) if lift=='gas_lift' else 0.0,
        'gas_lift_depth':min(max(_f(p,'gas_lift_depth_m',depth),0.0),depth),
        'esp':{'rated_rate_m3d':max(_f(p,'esp_rated_rate_m3d',1000.0),1e-6),'shutoff_head_bar':max(_f(p,'esp_shutoff_head_bar',80.0),0.0),
               'speed_fraction':max(_f(p,'esp_speed_fraction',1.0),1e-3)} if lift=='esp' else None,
        'lift_assist_bar':max(_f(p,'lift_assist_bar',0.0),0.0),
        'segments':max(int(_f(p,'vlp_segments',DEFAULT_VLP_SEGMENTS)),1),
        'open':bool(available) and opening>0 and (pi0>0 if str(p.get('ipr_model','PI')).lower()!='vogel' else qmax0>0),
        'max_rate':max_rate,
        'max_rate_reported':max_rate,
        'skin':skin,
        # Below this rate a producer cannot sustain stable flow (liquid loading / heading);
        # it is reported as shut in rather than as a numerically fragile trickle.
        'min_rate':max(_f(p,'min_rate_m3d',5.0),0.0),
    }


def ipr_rate(pwf, s):
    """Reservoir inflow [m3/d] at flowing bottom-hole pressure ``pwf``."""
    pr=s['pr']
    if pr<=0: return 0.0
    if s['ipr_model']=='Vogel':
        x=min(max(pwf/pr,0.0),1.0); return max(0.0,s['qmax']*(1-0.2*x-0.8*x*x))
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
    return pr - q/max(s['pi'],1e-9)


def esp_head_bar_simple(q, esp):
    if not esp: return 0.0
    s=esp['speed_fraction']; h0=esp['shutoff_head_bar']*s*s; qr=max(esp['rated_rate_m3d']*s,1e-9)
    return max(h0*(1.0-(max(q,0.0)/(1.35*qr))**2),0.0)


def vlp_bhp(q, whp, s):
    """Bottom-hole pressure required to produce q at wellhead pressure whp, including lift."""
    bhp,props=tubing_bhp_bar(max(q,0.0),whp,s['depth'],s['tubing_id'],s['roughness'],s['temperature'],s['water_cut'],s['gor'],s['api'],s['gas_sg'],
                             s['correlation'],segments=s['segments'],extra_gas_sm3d=s['gas_lift_sm3d'],gas_injection_depth_m=s['gas_lift_depth'],
                             bottomhole_temperature_c=s['bh_temperature'])
    assist=s['lift_assist_bar']+esp_head_bar_simple(q,s['esp'])
    return bhp-assist, props


def excess_bar(q, whp, s):
    """Pressure the reservoir can deliver above what the tubing needs at rate q.

    >0 : the well would flow faster; =0 : natural operating point; <0 : cannot sustain q.
    """
    return ipr_pwf(q,s) - vlp_bhp(q,whp,s)[0]


def rate_capacity(s):
    return s['pi']*s['pr'] if s['ipr_model']=='PI' else s['qmax']


def solve_well_rate(whp, s, points=24):
    """Stable operating rate at fixed wellhead pressure (largest stable IPR/VLP root).

    Returns (rate_m3d, status) with status in shut_in, dead, flowing, rate_limited.
    """
    if not s['open']: return 0.0, 'shut_in'
    qcap=rate_capacity(s)
    if qcap<=0: return 0.0, 'dead'
    grid=[qcap*i/points for i in range(points+1)]
    h=[excess_bar(q,whp,s) for q in grid]
    root=None
    for i in range(points-1,-1,-1):
        if h[i]>0 and h[i+1]<=0:
            a,b,fa,fb=grid[i],grid[i+1],h[i],h[i+1]
            for _ in range(60):
                m=0.5*(a+b); fm=excess_bar(m,whp,s)
                if fm>0: a,fa=m,fm
                else: b,fb=m,fm
                if b-a<1e-6*max(1.0,qcap): break
            root=a-fa*(b-a)/(fb-fa) if fb!=fa else 0.5*(a+b)
            break
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
            'liquid_holdup':props.get('liquid_holdup',1.0),'gas_fraction':props.get('gas_fraction',0.0),'status':status,
            'reservoir_pressure_bar':s['pr'],'vlp_model':s['correlation'],'lift_type':s['lift_type']}
