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


def flowline_segments(e):
    """Marching segments ``[(length_m, dz_m)]`` source -> target.

    With a bathymetry profile or a riser (``params['profile']`` / ``params['riser']``) the geometry from
    physics/flowline_profile.py is used (true length and local elevation change per segment); otherwise the
    line is cut uniformly with the straight-line ``elevation_change_m`` (legacy behaviour)."""
    prm=e.get('params',{}) or {}
    if prm.get('profile') or (prm.get('riser') or {}).get('enabled'):
        try:
            from physics.flowline_profile import profile_points, profile_segments
            segs=profile_segments(profile_points(e),max_segments=int(fnum(prm,'segments',24)) if prm.get('segments') else 24)
            if segs: return [(float(g['length_m']),float(g['dz_m'])) for g in segs]
        except Exception: pass
    L=max(fnum(e,'length_m',1000.0),0.0); dz=fnum(e,'elevation_change_m',0.0); n=_pipe_segments(e)
    return [(L/n,dz/n)]*n


def _flowline_fn(prm):
    name=str(prm.get('correlation','Beggs-Brill'))
    if name.lower().startswith('beggs'): return beggs_brill_dp_bar
    if name.lower().startswith('homog'): return homogeneous_dp_bar
    try:
        from physics.correlations import get_correlation
        return get_correlation(name)
    except Exception: return homogeneous_dp_bar


def _rho_liq(pr):
    st=pr.get('state'); return float(st.oil_density_kgm3) if st is not None else 800.0


def pipeline_march(e, q, ps, pt, fluid='production', t_in=None, profile=None):
    """Signed pressure drop Ps-Pt [bar] for a pipeline, marched along the line from the upstream end (source for q>=0, target for
    reverse flow). Returns ``(dp_bar, t_out_c)``. With ``params['thermal_model'] == 'heat_loss'`` the fluid temperature is marched too
    (heat loss, Joule-Thomson, elevation) and used for the PVT of every segment; otherwise the line is isothermal at ``temperature_c``.
    ``t_in`` overrides the inlet temperature; ``profile`` (list) receives {x_m, z_m, pressure_bar, temperature_c} rows in flow order."""
    from physics.pvt_model import fluid_scope
    from physics import thermal as th
    prm=e.get('params',{}) or {}
    D=fnum(e,'diameter_m',.154); eps=fnum(e,'roughness_m',4.5e-5)
    segs=flowline_segments(e)
    T=fnum(prm,'temperature_c',50.0) if t_in is None else float(t_in)
    if sum(g[0] for g in segs)<=0: return 0.0, T
    wc=1.0 if fluid=='water' else fnum(prm,'water_cut',.2); gor=0.0 if fluid=='water' else fnum(prm,'gor_sm3sm3',100.0)
    fn=_flowline_fn(prm)
    api=fnum(prm,'api',35.0); sg=fnum(prm,'gas_sg',.75)
    p=float(ps) if q>=0 else float(pt); total=0.0
    order=segs if q>=0 else list(reversed(segs))   # reverse flow marches from the target end
    thermal_on=th.mode(prm)=='heat_loss' and fluid!='water'
    if thermal_on:
        spec=th.line_spec(prm,D); pv=prm.get('pvt') or {}
        stream=th.Stream(q,wc,gor,api,sg,gas_cp_override=prm.get('gas_cp_jkgk'),co2=fnum(pv,'co2',0.0),h2s=fnum(pv,'h2s',0.0),n2=fnum(pv,'n2',0.0))
    x=0.0; z=0.0
    with fluid_scope(prm,gor,api,sg):
        for dl,dzs in order:
            if q<0: dzs=-dzs
            d1,pr1=fn(q,dl,D,eps,dzs if q>=0 else -dzs,max(p,1.0),T,wc,gor,api,sg)
            step=-d1 if q>=0 else d1  # pressure change moving with the flow
            pm=max(p+0.5*step,1.0); t_use=T
            if thermal_on:   # predictor: outlet temperature with the first pressure estimate, then evaluate the PVT at the segment mean
                t_pred=th.advance_segment(T,dl,dzs,step,spec['d_out'],spec['u'],spec['t_amb'],stream,p,pr1.get('free_gas_mass_fraction'),_rho_liq(pr1),spec['jt'],spec['elev'])
                t_use=0.5*(T+t_pred)
            d,prm_=fn(q,dl,D,eps,dzs if q>=0 else -dzs,pm,t_use,wc,gor,api,sg)
            total+=d; step2=-d if q>=0 else d; p=p-d if q>=0 else p+d
            if thermal_on:
                T=th.advance_segment(T,dl,dzs,step2,spec['d_out'],spec['u'],spec['t_amb'],stream,pm,prm_.get('free_gas_mass_fraction'),_rho_liq(prm_),spec['jt'],spec['elev'])
            if profile is not None:
                x+=dl; z+=dzs; profile.append({'x_m':x,'z_m':z,'pressure_bar':max(p,0.0),'temperature_c':T})
    return total, T


def pipeline_dp_bar(e, q, ps, pt, fluid='production'):
    """Signed pressure drop Ps-Pt [bar] for a pipeline (see :func:`pipeline_march`)."""
    return pipeline_march(e,q,ps,pt,fluid)[0]


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
