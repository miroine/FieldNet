"""Shared network equations (links, boundaries, injectors).

Used by both the steady-state kernel and the post-solve physical audit so the two can
never silently diverge (they were copy-pasted before and had already started to drift).
"""
from __future__ import annotations
import math
from collections import defaultdict, deque
from physics.multiphase import homogeneous_dp_bar
from physics.beggs_brill import beggs_brill_dp_bar
from physics.choke import choke_dp_bar
from physics.equipment import pump_head_bar
from physics.controls import control_valve_dp_bar, compressor_map_ratio

LINK_KINDS=('pipeline','choke','control_valve','pump','compressor')
BOUNDARY_KINDS=('sink','separator','separator_stage','oil_export','gas_export','water_disposal','water_source','gas_source')
SOURCE_KINDS=('water_source','gas_source')
INJECTOR_KINDS=('water_injector','gas_injector','injector')
G=9.80665


def fnum(d, key, default):
    """Float from a params dict that tolerates None/NaN/strings coming from data editors."""
    try: v=float(d.get(key, default))
    except (TypeError, ValueError): return float(default)
    return v if math.isfinite(v) else float(default)


def links_of(edges):
    return [e for e in edges if e.get('kind','pipeline') in LINK_KINDS]


def fixed_pressure(n):
    if n.get('pressure_bar') is not None:
        try:
            v=float(n['pressure_bar'])
            if math.isfinite(v): return v
        except (TypeError, ValueError): pass
    if n.get('kind')=='reservoir': return fnum(n.get('params',{}) or {},'reservoir_pressure_bar',200.0)
    return None


def boundary_issues(nodes, edges):
    """Terminal boundary nodes (sinks/separators/exports) without a pressure make the
    system singular; the old kernel crashed with a bare KeyError on them."""
    out=defaultdict(int)
    for e in links_of(edges): out[e.get('source')]+=1
    issues=[]
    for n in nodes:
        if n.get('kind') in BOUNDARY_KINDS and fixed_pressure(n) is None and out[n.get('id')]==0:
            issues.append({'severity':'error','code':'BOUNDARY_WITHOUT_PRESSURE','component':n.get('id'),
                           'message':f"Boundary '{n.get('name',n.get('id'))}' ({n.get('kind')}) has no pressure set. Terminal sinks/separators need a fixed pressure."})
    return issues


def edge_fluids(nodes, edges):
    """Detect injection sub-networks: an edge fed only by water sources carries water."""
    byid={n['id']:n for n in nodes}; up=defaultdict(list)
    for e in links_of(edges): up[e['target']].append(e['source'])
    memo={}
    def origins(nid):
        if nid in memo: return memo[nid]
        memo[nid]=set(); seen={nid}; dq=deque([nid]); kinds=set()
        while dq:
            x=dq.popleft(); k=byid.get(x,{}).get('kind')
            if k in ('well',)+SOURCE_KINDS: kinds.add(k)
            for y in up[x]:
                if y not in seen: seen.add(y); dq.append(y)
        memo[nid]=kinds; return kinds
    out={}
    for e in links_of(edges):
        prm=e.get('params',{}) or {}
        if prm.get('fluid') in ('water','production'):
            out[e['id']]='water' if prm.get('fluid')=='water' else 'production'; continue
        k=origins(e['source'])
        out[e['id']]='water' if k=={'water_source'} else 'production'
    return out


def _pipe_segments(e):
    prm=e.get('params',{}) or {}
    if prm.get('segments') is not None: return max(1,int(fnum(prm,'segments',1)))
    L=fnum(e,'length_m',1000.0)
    return int(min(8,max(1,math.ceil(L/1500.0))))


def pipeline_dp_bar(e, q, ps, pt, fluid='production'):
    """Signed pressure drop Ps-Pt [bar] for a pipeline, marched along the line from the
    upstream end (source for q>=0, target for reverse flow)."""
    prm=e.get('params',{}) or {}
    L=max(fnum(e,'length_m',1000.0),0.0); D=fnum(e,'diameter_m',.154); eps=fnum(e,'roughness_m',4.5e-5); dz=fnum(e,'elevation_change_m',0.0)
    if L<=0: return 0.0
    wc=1.0 if fluid=='water' else fnum(prm,'water_cut',.2); gor=0.0 if fluid=='water' else fnum(prm,'gor_sm3sm3',100.0)
    fn=beggs_brill_dp_bar if str(prm.get('correlation','Beggs-Brill')).lower().startswith('beggs') else homogeneous_dp_bar
    T=fnum(prm,'temperature_c',50.0); api=fnum(prm,'api',35.0); sg=fnum(prm,'gas_sg',.75)
    n=_pipe_segments(e); dl=L/n; dzs=dz/n
    p=float(ps) if q>=0 else float(pt); total=0.0
    for _ in range(n):
        d1,_=fn(q,dl,D,eps,dzs,max(p,1.0),T,wc,gor,api,sg)
        step=-d1 if q>=0 else d1  # pressure change moving with the flow
        pm=max(p+0.5*step,1.0)
        d,_=fn(q,dl,D,eps,dzs,pm,T,wc,gor,api,sg)
        total+=d; p=p-d if q>=0 else p+d
    return total


def link_dp_bar(e, q, ps, pt, fluid='production'):
    """Signed pressure difference Ps - Pt required by link ``e`` carrying flow q [m3/d]."""
    prm=e.get('params',{}) or {}; kind=e.get('kind','pipeline')
    if kind=='choke': return choke_dp_bar(q,fnum(prm,'cv',80.0),fnum(prm,'rho_kgm3',1000.0 if fluid=='water' else 850.0))
    if kind=='control_valve': return control_valve_dp_bar(q,fnum(prm,'cv',80.0),fnum(prm,'rho_kgm3',1000.0 if fluid=='water' else 850.0),fnum(prm,'opening',1.0))
    if kind=='pump':
        # Beyond run-out the curve keeps falling (the pump becomes a restriction) unless the
        # user sets min_head_bar. Clipping at zero head made the equation flat above the
        # rated rate, so the solver could stall with an undetermined pump flow.
        return -pump_head_bar(q,fnum(prm,'shutoff_head_bar',35.0),fnum(prm,'rated_rate_m3d',1500.0),fnum(prm,'min_head_bar',-1e9))
    if kind=='compressor':
        qg=abs(q)*fnum(prm,'gor_sm3sm3',100.0)
        ratio=compressor_map_ratio(qg,fnum(prm,'rated_gas_rate_sm3d',150000.0),fnum(prm,'pressure_ratio',1.8),fnum(prm,'speed_fraction',1.0)) if prm.get('map_enabled',False) else max(fnum(prm,'pressure_ratio',1.8),1.0)
        target=min(float(ps)*ratio,fnum(prm,'max_discharge_bar',250.0)); return float(ps)-target
    # Beggs-Brill holdup/inclination switch branch when the flow changes sign, so the raw
    # correlation is discontinuous at q=0. Dead wells park their flowlines exactly there,
    # which made the solver oscillate for thousands of evaluations. Bridge |q|<Q0 linearly.
    q0=Q_ZERO_BAND
    if abs(q)<q0:
        a=pipeline_dp_bar(e,-q0,ps,pt,fluid); b=pipeline_dp_bar(e,q0,ps,pt,fluid)
        return a+(q+q0)/(2*q0)*(b-a)
    return pipeline_dp_bar(e,q,ps,pt,fluid)


Q_ZERO_BAND=0.5  # m3/d


def injector_settings(n):
    p=n.get('params',{}) or {}; gas=n.get('kind')=='gas_injector' or p.get('injection_fluid')=='gas'
    mx=p.get('max_rate_m3d'); mx=fnum(p,'max_rate_m3d',math.inf) if mx is not None else math.inf
    avail=p.get('available',True)
    if isinstance(avail,str): avail=avail.strip().lower() not in ('false','0','no','off')
    return {'ii':max(fnum(p,'injectivity_m3d_bar',10.0),0.0),'pr':fnum(p,'reservoir_pressure_bar',200.0),
            'depth':max(fnum(p,'depth_m',2000.0),0.0),'rho':fnum(p,'fluid_density_kgm3',180.0 if gas else 1030.0),
            'max_rate':mx,'open':bool(avail) and fnum(p,'injectivity_m3d_bar',10.0)>0,'fluid':'gas' if gas else 'water'}


def injector_excess_bar(q, whp, s):
    """Injection-well analogue of the producer excess: >0 means the well would take more."""
    bhp=float(whp)+s['rho']*G*s['depth']/1e5
    return bhp-s['pr']-q/max(s['ii'],1e-9)


def mid(a, b, c):
    """Median of three - the natural residual for a box-constrained complementarity
    condition: lo<=q<=hi with F(q) = 0 inside, F>=0 at lo and F<=0 at hi."""
    return sorted((a,b,c))[1]
