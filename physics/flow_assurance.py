"""FieldNet v19 flow-assurance screening layer.

Post-processes steady multiphase states. These calculations are screening/planning
checks and are deliberately separate from hydraulic convergence and operability.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, asdict
from physics.beggs_brill import beggs_brill_dp_bar

DAY=86400.0
G=9.80665

@dataclass(frozen=True)
class FlowAssuranceConfig:
    ambient_temperature_c: float = 4.0
    overall_u_w_m2k: float = 5.0
    fluid_cp_j_kgk: float = 2200.0
    hydrate_margin_c: float = 3.0
    wax_appearance_temperature_c: float = 25.0
    wax_margin_c: float = 3.0
    erosion_c_factor: float = 100.0
    surface_tension_dyn_cm: float = 20.0


def _finite_pos(x,name,allow_zero=False):
    x=float(x)
    if not math.isfinite(x) or (x < 0 if allow_zero else x <= 0): raise ValueError(f'{name} must be finite and {">= 0" if allow_zero else "> 0"}')
    return x


def exponential_temperature_c(t_in_c,t_ambient_c,u_w_m2k,outer_diameter_m,length_m,mass_flow_kg_s,cp_j_kgk):
    """Steady 1-D lumped heat-loss solution, no JT/phase-change heat effects."""
    u=_finite_pos(u_w_m2k,'U',True); d=_finite_pos(outer_diameter_m,'diameter'); L=_finite_pos(length_m,'length',True)
    m=_finite_pos(mass_flow_kg_s,'mass flow'); cp=_finite_pos(cp_j_kgk,'Cp')
    ntu=u*math.pi*d*L/(m*cp)
    return float(t_ambient_c)+(float(t_in_c)-float(t_ambient_c))*math.exp(-ntu)


def hydrate_equilibrium_temperature_c(pressure_bara, gas_sg=0.75):
    """Conservative generic hydrate screening envelope.

    Smooth pressure/gas-gravity proxy only; not a substitute for compositional
    hydrate software. Intended to trigger review/calibration.
    """
    p=max(_finite_pos(pressure_bara,'absolute pressure'),1.01325); sg=_finite_pos(gas_sg,'gas SG')
    # bounded generic envelope: rises logarithmically with pressure and modestly with gas gravity
    return max(-5.0,min(30.0, 4.0 + 7.0*math.log10(max(p,1.01325)/10.0) + 8.0*(sg-0.65)))


def api14e_erosional_velocity_ms(mixture_density_kgm3,c_factor=100.0):
    """API-RP-14E-style erosional velocity screen; C is configurable.

    Native empirical relation uses ft/s and lb/ft3: Ve=C/sqrt(rho).
    """
    rho=_finite_pos(mixture_density_kgm3,'mixture density'); c=_finite_pos(c_factor,'erosion C')
    rho_lbft3=rho/16.01846337396014
    return (c/math.sqrt(rho_lbft3))*0.3048


def turner_critical_velocity_ms(rho_g_kgm3,rho_l_kgm3=800.0,surface_tension_dyn_cm=20.0):
    """Turner-style liquid-loading critical gas velocity.

    Evaluates the common field-unit form and converts ft/s to m/s.
    """
    rg=_finite_pos(rho_g_kgm3,'gas density'); rl=_finite_pos(rho_l_kgm3,'liquid density'); sig=_finite_pos(surface_tension_dyn_cm,'surface tension')
    if rl <= rg: raise ValueError('liquid density must exceed gas density')
    rg_f=rg/16.01846337396014; rl_f=rl/16.01846337396014
    v_ft_s=1.593*(sig**0.25)*((rl_f-rg_f)**0.25)/(rg_f**0.5)
    return v_ft_s*0.3048


def _mass_flow_from_state(state):
    q=float(state['q_line_m3s']); rho=float(state['rho'])
    return max(abs(q)*max(rho,0.1),1e-9)


def pipeline_flow_assurance(liquid_rate_m3d,length_m,diameter_m,roughness_m,dz_m,p_in_bar,t_in_c,
                            water_cut=0.0,gor_sm3sm3=0.0,api=35.0,gas_sg=0.75,segments=30,
                            config:FlowAssuranceConfig|None=None):
    cfg=config or FlowAssuranceConfig(); segments=int(segments)
    if segments < 1: raise ValueError('segments must be >= 1')
    L=_finite_pos(length_m,'length',True); D=_finite_pos(diameter_m,'diameter')
    p=float(p_in_bar); t=float(t_in_c); rows=[]
    for i in range(segments):
        segL=L/segments; segdz=float(dz_m)/segments
        dp,s=beggs_brill_dp_bar(liquid_rate_m3d,segL,D,roughness_m,segdz,max(p,1.01325),t,water_cut,gor_sm3sm3,api,gas_sg)
        mdot=_mass_flow_from_state(s)
        tout=exponential_temperature_c(t,cfg.ambient_temperature_c,cfg.overall_u_w_m2k,D,segL,mdot,cfg.fluid_cp_j_kgk) if segL else t
        pout=p-dp; pmid=max((p+pout)/2,1.01325); tmid=(t+tout)/2
        hteq=hydrate_equilibrium_temperature_c(pmid,gas_sg); hydrate_margin=tmid-hteq
        wax_margin=tmid-cfg.wax_appearance_temperature_c
        ve=api14e_erosional_velocity_ms(s['rho'],cfg.erosion_c_factor); vm=abs(float(s['mixture_velocity_ms']))
        st=s['state']; rho_l=(1-water_cut)*st.oil_density_kgm3+water_cut*st.water_density_kgm3
        vc=turner_critical_velocity_ms(st.gas_density_kgm3,rho_l,cfg.surface_tension_dyn_cm)
        gas_fraction=max(0.0,min(1.0,1.0-float(s['liquid_holdup']))); vg=vm*gas_fraction
        froude=vm/math.sqrt(G*D)
        slug_flag=s['flow_regime']=='intermittent' or (s['flow_regime']=='segregated' and froude<1.5)
        rows.append({'segment':i+1,'x0_m':i*segL,'x1_m':(i+1)*segL,'p_in_bar':p,'p_out_bar':pout,
          'temperature_in_c':t,'temperature_out_c':tout,'hydrate_equilibrium_c':hteq,'hydrate_margin_c':hydrate_margin,
          'hydrate_risk':hydrate_margin < cfg.hydrate_margin_c,'wax_appearance_c':cfg.wax_appearance_temperature_c,
          'wax_margin_c':wax_margin,'wax_risk':wax_margin < cfg.wax_margin_c,'mixture_velocity_ms':vm,
          'erosional_velocity_ms':ve,'erosion_ratio':vm/ve,'erosion_risk':vm>ve,'gas_velocity_proxy_ms':vg,
          'turner_critical_velocity_ms':vc,'liquid_loading_ratio':vg/vc,'liquid_loading_risk':vg<vc,
          'flow_regime':s['flow_regime'],'froude':froude,'slugging_indicator':slug_flag,'liquid_holdup':s['liquid_holdup']})
        p,t=pout,tout
    def any_(k): return any(bool(r[k]) for r in rows)
    return {'model':'FieldNet v19 screening','segments':rows,'outlet_pressure_bar':p,'outlet_temperature_c':t,
      'minimum_temperature_c':min((r['temperature_out_c'] for r in rows),default=t),
      'minimum_hydrate_margin_c':min((r['hydrate_margin_c'] for r in rows),default=float('nan')),
      'minimum_wax_margin_c':min((r['wax_margin_c'] for r in rows),default=float('nan')),
      'maximum_erosion_ratio':max((r['erosion_ratio'] for r in rows),default=0.0),
      'minimum_liquid_loading_ratio':min((r['liquid_loading_ratio'] for r in rows),default=float('nan')),
      'hydrate_risk':any_('hydrate_risk'),'wax_risk':any_('wax_risk'),'erosion_risk':any_('erosion_risk'),
      'liquid_loading_risk':any_('liquid_loading_risk'),'slugging_indicator':any_('slugging_indicator'),
      'config':asdict(cfg),'limitations':['hydrate envelope is generic screening proxy','wax uses user-configured WAT','erosion uses API-RP-14E-style C-factor','liquid loading uses Turner-style screen','slugging indicator is not transient slug simulation','thermal model excludes JT and phase-change heat']}


def network_flow_assurance(nodes,edges,solve_result,config=None,segments=20):
    p,q,info,_=solve_result; reports=[]
    for e in edges:
        if e.get('kind','pipeline')!='pipeline' or e['id'] not in q or e['source'] not in p: continue
        prm=e.get('params',{}); cfg=config or FlowAssuranceConfig(
            ambient_temperature_c=float(prm.get('ambient_temperature_c',4.0)),overall_u_w_m2k=float(prm.get('overall_u_w_m2k',5.0)),
            wax_appearance_temperature_c=float(prm.get('wax_appearance_temperature_c',25.0)),erosion_c_factor=float(prm.get('erosion_c_factor',100.0)))
        r=pipeline_flow_assurance(q[e['id']],float(e['length_m']),float(e['diameter_m']),float(e.get('roughness_m',4.5e-5)),float(e.get('elevation_change_m',0)),float(p[e['source']]),float(prm.get('temperature_c',50)),float(prm.get('water_cut',.2)),float(prm.get('gor_sm3sm3',100)),float(prm.get('api',35)),float(prm.get('gas_sg',.75)),segments,cfg)
        r['edge_id']=e['id']; reports.append(r)
    return {'hydraulic_converged':bool(info.get('success')),'pipelines':reports,'risk_counts':{k:sum(bool(r[k]) for r in reports) for k in ('hydrate_risk','wax_risk','erosion_risk','liquid_loading_risk','slugging_indicator')}}
