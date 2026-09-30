# --- Stale-module guard -----------------------------------------------------------------
# Streamlit Cloud re-runs app.py after a git pull but can keep previously imported project
# modules cached. After an update spread over several commits, a new app.py then meets an
# old module (ImportError: cannot import name ...). If any project source file changed since
# the last run, drop the cached project modules so everything is imported fresh.
import os as _os, sys as _sys
_ROOT=_os.path.dirname(_os.path.abspath(globals().get('__file__') or 'app.py'))
def _code_signature():
    sig=[]
    for _d,_,_fs in _os.walk(_ROOT):
        if '.git' in _d or '__pycache__' in _d: continue
        for _f in _fs:
            if _f.endswith('.py'):
                try: sig.append((_os.path.join(_d,_f),_os.path.getmtime(_os.path.join(_d,_f))))
                except OSError: pass
    return hash(tuple(sorted(sig)))
_sig=_code_signature()
if _sys.modules.get('_fieldnet_code_sig') is not None and getattr(_sys.modules['_fieldnet_code_sig'],'value',None)!=_sig:
    for _name,_mod in list(_sys.modules.items()):
        _file=getattr(_mod,'__file__',None) or ''
        if _name!='__main__' and _file.startswith(_ROOT): del _sys.modules[_name]
import types as _types
_sys.modules['_fieldnet_code_sig']=_types.SimpleNamespace(value=_sig)
# ------------------------------------------------------------------------------------------
import json, uuid
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from network.examples import demo_case
from solver.v21 import solve_v21
from optimization.debottleneck import debottleneck_screen
from solver.audit_report import calculation_audit
from physics.beggs_brill import pressure_profile
from optimization.integrated_v22 import optimize_integrated
from optimization.calibration_v23 import Observation, calibrate
from optimization.scenarios import run_sensitivity
from ui.editor_component import network_editor
from ui.graph_contract import (accept_canvas_payload, solve_status, current_results, run_solve, normalize_graph,
                               UNSOLVED, SOLVING, SOLVED, FAILED)
from ui.history import normalize_project
from ui.topology import validate_topology, auto_layout
from ui.widgets import synced_number, synced_slider, synced_select, synced_text, synced_checkbox, clean_num, clean_text, to_builtin
from network.forecast import run_forecast
from network.field_development import _coerce_value
from network.planning import run_scenarios, uncertainty_cases
from ui.theme import THEMES, apply_theme
from solver.diagnostics import solver_diagnostics
from physics.well_model import well_settings
from ui.forecast_view import render_forecast
from ui.development_view import render_schedule, render_scenarios
from ui.constraints_view import render_constraint_editor
from ui import charts
from network.reservoir_mb import apply_tank_links, tank_summary, TANK_DEFAULTS
from network.examples import demo_field_case
from ui.interchange_v27 import render_interchange_v27
from ui.scenario_v29 import render_scenario_v29
from solver.model_assurance_v28 import model_quality_report
from physics.unit_system import (PROFILES, labels as unit_labels, STANDARD_CONDITIONS, pressure_to_display, pressure_from_display, temperature_to_display, temperature_from_display, length_to_display, length_from_display, diameter_to_display, diameter_from_display, liquid_rate_to_display, liquid_rate_from_display, gor_to_display, gor_from_display, pi_to_display, pi_from_display, velocity_to_display, heat_transfer_u_to_display, heat_transfer_u_from_display)
from ui.uncertainty_v17 import render_uncertainty
from physics.flow_assurance import network_flow_assurance
from physics.well_performance_v20 import nodal_operating_point, optimize_gas_lift, esp_performance, well_performance_qa
from network.reliability_v24 import ReliabilitySpec, ReliabilityStudy, run_reliability
from network.reservoir_v25 import AquiferSpec, CommunicationLink, InjectorConnection
from network.coupled_forecast_v25 import run_coupled_forecast_v25

BOUNDARY_KINDS=('sink','separator','separator_stage','oil_export','gas_export','water_disposal','water_source','gas_source')
LINK_TYPES=['pipeline','choke','control_valve','pump','compressor']

st.set_page_config(page_title='FieldNet v31',page_icon='⛽',layout='wide')
if 'theme_name' not in st.session_state: st.session_state.theme_name='Equinor-inspired Light'
if 'unit_profile' not in st.session_state: st.session_state.unit_profile='norwegian_si'
with st.sidebar:
    st.session_state.theme_name=st.selectbox('Theme',list(THEMES),index=list(THEMES).index(st.session_state.theme_name))
    st.session_state.unit_profile=st.selectbox('Engineering units',list(PROFILES),index=list(PROFILES).index(st.session_state.unit_profile),format_func=lambda k: PROFILES[k].label)
    ul=unit_labels(st.session_state.unit_profile)
    st.caption(f"Display/input profile: {PROFILES[st.session_state.unit_profile].label}. Internal solver remains canonical SI/bar.")
    with st.expander('Unit reference conditions'):
        st.write(f"Standard volumes: {STANDARD_CONDITIONS['standard_temperature_c']:.0f} °C and {STANDARD_CONDITIONS['standard_pressure_bara']:.5f} bara. Pressure-dependent PVT uses absolute pressure.")
apply_theme(st,st.session_state.theme_name)
st.markdown("<div class='fieldnet-brand'><h2>FieldNet v31 — Production Network &amp; Prognosis</h2><p>Made by Merouane Hamdani · For non-commercial use · Independent engineering prototype</p></div>",unsafe_allow_html=True)
st.caption('Equinor-inspired themes are unofficial and are not affiliated with, endorsed by, or sponsored by Equinor ASA. Validate engineering correlations before operational use.')
if 'nodes' not in st.session_state: st.session_state.nodes,st.session_state.edges=demo_field_case()
if 'solve' not in st.session_state: st.session_state.solve=None
if 'layout_checked' not in st.session_state:
    # Demo/imported cases often have every node at (0,0); lay them out once so the editor is usable.
    if len({(n.get('x',0),n.get('y',0)) for n in st.session_state.nodes})<=1 and len(st.session_state.nodes)>1:
        st.session_state.nodes=auto_layout(st.session_state.nodes,st.session_state.edges)
    st.session_state.layout_checked=True
PROFILE=st.session_state.unit_profile


def unit_input(label, canonical, to_display, from_display, key, min_value=None, max_value=None, step=None, fmt=None):
    """Unit-aware number input that stays synchronised with the case model."""
    k=f'{key}_{PROFILE}'
    lo=float(to_display(min_value,PROFILE)) if min_value is not None else None
    hi=float(to_display(max_value,PROFILE)) if max_value is not None else None
    if lo is not None and hi is not None and lo>hi: lo,hi=hi,lo
    shown=synced_number(st,label,float(to_display(canonical,PROFILE)),k,lo,hi,step,fmt)
    return float(from_display(shown,PROFILE))


def pick(label, options, key, format_func=str):
    """Keyed selectbox that survives its current value disappearing from the options
    (e.g. a pipeline changed to a choke or a well deleted)."""
    options=list(options)
    if key in st.session_state and st.session_state[key] not in options: del st.session_state[key]
    return st.selectbox(label,options,key=key,format_func=format_func)


def solved():
    """Results for the *current* graph (graph-hash match), or None. See ui/graph_contract.py."""
    return current_results(st.session_state)


def reset_solve():
    st.session_state.solve=None; st.session_state.pop('solve_request',None); st.session_state.pop('v21_warm_start',None)


def request_solve():
    """Mark the model SOLVING and rerun; the solve itself runs right after the editor has been
    drawn with the SOLVING badge (see the Network tab)."""
    st.session_state.solve_request=True; st.rerun()


with st.sidebar:
    st.header('Component palette')
    kind=st.selectbox('Component',['reservoir','well','manifold','separator','separator_stage','water_source','gas_source','water_injector','gas_injector','oil_export','gas_export','water_disposal','sink']); name=st.text_input('Name',f'{kind.upper()}-{len(st.session_state.nodes)+1:02d}')
    if st.button('Add component',use_container_width=True):
        nid=str(uuid.uuid4())[:8]; pressure=None; prm={}
        if kind=='reservoir': prm={'reservoir_pressure_bar':250.0,'pore_volume_m3':2000000.0,'total_compressibility_1bar':8e-5,'min_pressure_bar':20.0}
        if kind=='well': prm={'reservoir_pressure_bar':220.0,'ipr_model':'PI','pi_m3d_bar':10.0,'qmax_m3d':1500.0,'initial_rate_m3d':500.0,'depth_m':2000.0,'tubing_id_m':0.0889,'tubing_roughness_m':4.5e-5,'temperature_c':70.0,'water_cut':0.2,'gor_sm3sm3':100.0,'api':35.0,'gas_sg':0.75,'vlp_model':'Beggs-Brill','available':True}
        if kind in ('sink','separator','separator_stage','oil_export','gas_export','water_disposal'): pressure=35.0
        if kind in ('water_source','gas_source'): pressure=180.0
        if kind in ('water_injector','gas_injector'): prm={'injection_fluid':'water' if kind=='water_injector' else 'gas','injectivity_m3d_bar':10.0,'reservoir_pressure_bar':200.0,'depth_m':2000.0,'available':True}
        n_existing=len(st.session_state.nodes)
        st.session_state.nodes.append({'id':nid,'kind':kind,'name':name,'pressure_bar':pressure,'x':60+(n_existing%4)*200,'y':60+(n_existing//4)*120,'params':prm}); st.rerun()
    st.caption('Connect components in the editor: drag from an OUT port onto another component’s IN port.')
    if st.button('Load demo field',use_container_width=True): st.session_state.nodes,st.session_state.edges=demo_field_case(); reset_solve(); [st.session_state.pop(k,None) for k in ('forecast','sched_result','scn_results','wc_result')]; st.rerun()

G=st.tabs(['🗺️ Network','🛢️ Wells & reservoirs','📊 Network results','📈 Forecast & development','⚙️ Optimization','🎲 Uncertainty & risk','🗂️ Data & QA'])
tab_net=G[0]
with G[1]: tab_nodal,tab_tanks=st.tabs(['Nodal analysis','Reservoir tanks'])
with G[2]: tab_results,tab_constraints,tab_diag,tab_fa=st.tabs(['Summary','Constraints & equipment','Hydraulic profiles','Flow assurance'])
with G[3]: tab_forecast,tab_dev26,tab_development,tab_res25=st.tabs(['Production forecast','Development schedule','Scenarios & well count','Multi-tank coupling (advanced)'])
with G[4]: tab_ops,tab_cal=st.tabs(['Optimization & sensitivity','Calibration'])
with G[5]: tab_uncertainty,tab_rel=st.tabs(['Monte Carlo','Reliability'])
with G[6]: tab_qa28,tab_io27,tab_scen29=st.tabs(['Model assurance','Import / export','Snapshots'])
with tab_net:
    canvas,props=st.columns([3.2,1])
    with canvas:
        status,status_msg=solve_status(st.session_state)
        edit=network_editor(st.session_state.nodes, st.session_state.edges, solved(), key='network-v14', height=860,
                            status=status, status_message=status_msg, selected=st.session_state.get('selected'))
        # One contract (ui/graph_contract.py): only a new canvas revision is an edit; stale replays are ignored.
        if accept_canvas_payload(st.session_state, edit)=='graph': st.rerun()
        for msg in st.session_state.pop('graph_issues',[]) or []: st.warning(msg)
        if st.session_state.pop('solve_request',False):
            # The editor above has already been sent with the SOLVING badge.
            with st.spinner('Solving network...'):
                run_solve(st.session_state, solve_v21, warm_start=st.session_state.get('v21_warm_start'), attempts=3,
                          enforce_constraints=bool(st.session_state.get('enforce_caps',True)))
            st.rerun()
        badge={UNSOLVED:'⚪',SOLVING:'🟡',SOLVED:'🟢',FAILED:'🔴'}[status]
        st.markdown(f"**Model state:** {badge} {status}" + (f" — {status_msg}" if status_msg else ''))
        selected=st.session_state.get('selected')
        issues=validate_topology(st.session_state.nodes,st.session_state.edges)
        ca,cb=st.columns(2)
        if ca.button('Auto-layout network',use_container_width=True):
            st.session_state.nodes=auto_layout(st.session_state.nodes,st.session_state.edges); st.rerun()
        cb.metric('Topology issues',len(issues))
        if issues:
            with st.expander('Topology validation',expanded=any(i['severity']=='error' for i in issues)):
                for i in issues: st.write(('🔴' if i['severity']=='error' else '🟠'),i['message'])
        st.caption('Canvas: add equipment from its palette, drag nodes, select objects, create links by clicking OUT then IN, delete/copy, undo/redo. Every edit is synchronised into the Python case model.')
    with props:
        ids=[n['id'] for n in st.session_state.nodes]; edge_ids=[e['id'] for e in st.session_state.edges]
        pick_opts=ids+edge_ids
        if pick_opts:
            names={**{n['id']:f"{n['name']} ({n['kind']})" for n in st.session_state.nodes},**{e['id']:f"{e['id']} ({e.get('kind','pipeline')})" for e in st.session_state.edges}}
            default_sel=selected if selected in pick_opts else pick_opts[0]
            selected=synced_select(st,'Edit component',pick_opts,default_sel,'prop_pick',format_func=lambda k: names.get(k,k))
            st.session_state.selected=selected
        sid=selected if selected in ids else None
        if selected in edge_ids:
            e=next(x for x in st.session_state.edges if x['id']==selected); eid=e['id']
            st.subheader('Selected connection')
            e['kind']=synced_select(st,'Type',LINK_TYPES,e.get('kind','pipeline'),'ek'+eid)
            ep=e.setdefault('params',{})
            if e['kind']=='pipeline':
                e['length_m']=unit_input(f"Length [{ul['length']}]",float(clean_num(e.get('length_m'),0.0)),length_to_display,length_from_display,'el'+eid,0.0,1e7)
                e['diameter_m']=unit_input(f"ID [{ul['diameter']}]",float(clean_num(e.get('diameter_m'),.154)),diameter_to_display,diameter_from_display,'ed'+eid,0.001,5.0,fmt='%.4f')
                e['elevation_change_m']=unit_input(f"Elevation change (outlet − inlet) [{ul['length']}]",float(clean_num(e.get('elevation_change_m'),0.0)),length_to_display,length_from_display,'ez'+eid,-5000.,5000.)
                ep['correlation']=synced_select(st,'Multiphase correlation',['Beggs-Brill','Homogeneous'],ep.get('correlation','Beggs-Brill'),'ecor'+eid)
                ep['temperature_c']=unit_input(f"Inlet temperature [{ul['temperature']}]",float(clean_num(ep.get('temperature_c'),50)),temperature_to_display,temperature_from_display,'et'+eid,-20.,250.)
                ep['water_cut']=synced_slider(st,'Water cut (line fluid)',0.,0.9999,float(clean_num(ep.get('water_cut'),.2)),'ewc'+eid)
                ep['gor_sm3sm3']=unit_input(f"GOR (line fluid) [{ul['gor']}]",float(clean_num(ep.get('gor_sm3sm3'),100)),gor_to_display,gor_from_display,'egor'+eid,0.,20000.)
                with st.expander('Flow-assurance inputs'):
                    ep['ambient_temperature_c']=unit_input(f"Ambient temperature [{ul['temperature']}]",float(clean_num(ep.get('ambient_temperature_c'),4)),temperature_to_display,temperature_from_display,'ea'+eid,-50.,100.)
                    ep['overall_u_w_m2k']=unit_input('Overall U [W/m²/K]' if PROFILE=='norwegian_si' else 'Overall U [Btu/h/ft²/°F]',float(clean_num(ep.get('overall_u_w_m2k'),5)),heat_transfer_u_to_display,heat_transfer_u_from_display,'eu'+eid,0.,500.)
                    ep['wax_appearance_temperature_c']=unit_input(f"Wax appearance temperature [{ul['temperature']}]",float(clean_num(ep.get('wax_appearance_temperature_c'),25)),temperature_to_display,temperature_from_display,'ew'+eid,-20.,120.)
                    ep['erosion_c_factor']=synced_number(st,'API-14E erosion C-factor',float(clean_num(ep.get('erosion_c_factor'),100.0)),'ec'+eid,1.0,500.0)
            elif e['kind'] in ('choke','control_valve'):
                ep['cv']=synced_number(st,'Flow coefficient Cv (model)',float(clean_num(ep.get('cv'),80.0)),'ecv'+eid,0.01,1e6)
                if e['kind']=='control_valve': ep['opening']=synced_slider(st,'Valve opening',0.01,1.0,float(clean_num(ep.get('opening'),1.0)),'eop'+eid)
            elif e['kind']=='pump':
                ep['shutoff_head_bar']=unit_input(f"Shut-off head [{ul['pressure']}]",float(clean_num(ep.get('shutoff_head_bar'),35.0)),pressure_to_display,pressure_from_display,'eph'+eid,0.,1000.)
                ep['rated_rate_m3d']=unit_input(f"Rated (run-out) rate [{ul['liquid_rate']}]",float(clean_num(ep.get('rated_rate_m3d'),1500.0)),liquid_rate_to_display,liquid_rate_from_display,'epr'+eid,1.,1e7)
                ep['efficiency']=synced_number(st,'Efficiency [-]',float(clean_num(ep.get('efficiency'),.75)),'epe'+eid,0.05,1.0)
            elif e['kind']=='compressor':
                ep['pressure_ratio']=synced_number(st,'Pressure ratio [-]',float(clean_num(ep.get('pressure_ratio'),1.8)),'ecr'+eid,1.0,10.0)
                ep['max_discharge_bar']=unit_input(f"Max discharge [{ul['pressure']}]",float(clean_num(ep.get('max_discharge_bar'),250.0)),pressure_to_display,pressure_from_display,'ecd'+eid,1.,2000.)
            cap=unit_input(f"Maximum rate (0 = none) [{ul['liquid_rate']}]",float(clean_num(ep.get('max_rate_m3d'),0.0)),liquid_rate_to_display,liquid_rate_from_display,'emx'+eid,0.,1e8)
            if cap>0: ep['max_rate_m3d']=cap
            else: ep.pop('max_rate_m3d',None)
            if st.button('Delete selected connection'):
                st.session_state.edges=[x for x in st.session_state.edges if x['id']!=eid]; st.session_state.selected=None; st.rerun()
        if sid:
            n=next(x for x in st.session_state.nodes if x['id']==sid); n['name']=synced_text(st,'Name',n['name'],'nm'+sid)
            p=n.setdefault('params',{})
            if n['kind'] in BOUNDARY_KINDS:
                has_p=synced_checkbox(st,'Fixed pressure boundary',n.get('pressure_bar') is not None,'bpf'+sid)
                if has_p: n['pressure_bar']=unit_input(f"Boundary pressure [{ul['pressure']}]",float(clean_num(n.get('pressure_bar'),35.0)),pressure_to_display,pressure_from_display,'bp'+sid,0.1,1000.0)
                else: n['pressure_bar']=None
                if n['kind'] not in ('water_source','gas_source'):
                    cap=unit_input(f"Liquid handling capacity (0 = none) [{ul['liquid_rate']}]",float(clean_num(p.get('max_liquid_rate_m3d'),0.0)),liquid_rate_to_display,liquid_rate_from_display,'bcap'+sid,0.,1e8)
                    if cap>0: p['max_liquid_rate_m3d']=cap
                    else: p.pop('max_liquid_rate_m3d',None)
            if n['kind']=='reservoir':
                PH={'oil':'Oil (black oil)','gas':'Dry gas','gas_condensate':'Gas condensate'}
                p['fluid_phase']=synced_select(st,'Fluid phase',list(PH),p.get('fluid_phase','oil') if p.get('fluid_phase') in PH else 'oil','rph'+sid,format_func=PH.get)
                if p['fluid_phase']=='oil':
                    v=synced_number(st,'STOIIP — oil in place [MSm³]',float(clean_num(p.get('stoiip_sm3'),TANK_DEFAULTS['stoiip_sm3']))/1e6,'rst'+sid,0.001,1e6,fmt='%.3f'); p['stoiip_sm3']=v*1e6
                else:
                    v=synced_number(st,'GIIP — gas in place [GSm³]',float(clean_num(p.get('giip_sm3'),TANK_DEFAULTS['giip_sm3']))/1e9,'rgi'+sid,0.0001,1e5,fmt='%.4f'); p['giip_sm3']=v*1e9
                p['reservoir_pressure_bar']=unit_input(f"Initial pressure [{ul['pressure']}]",float(clean_num(p.get('reservoir_pressure_bar'),250.0)),pressure_to_display,pressure_from_display,'rpr'+sid,1.0,1500.0)
                p['temperature_c']=unit_input(f"Reservoir temperature [{ul['temperature']}]",float(clean_num(p.get('temperature_c'),90.0)),temperature_to_display,temperature_from_display,'rtc'+sid,0.,250.)
                with st.expander('Fluid & rock properties'):
                    if p['fluid_phase']=='oil':
                        p['boi_rm3_sm3']=synced_number(st,'Initial Bo [rm³/Sm³]',float(clean_num(p.get('boi_rm3_sm3'),1.25)),'rbo'+sid,1.0,3.0)
                        p['rsi_sm3_sm3']=synced_number(st,'Initial solution GOR Rsi [Sm³/Sm³]',float(clean_num(p.get('rsi_sm3_sm3'),100.0)),'rrs'+sid,0.0,2000.0)
                        p['bubble_point_bar']=unit_input(f"Bubble-point pressure [{ul['pressure']}]",float(clean_num(p.get('bubble_point_bar'),150.0)),pressure_to_display,pressure_from_display,'rpb'+sid,1.,1500.)
                        p['ct_1bar']=synced_number(st,'Total compressibility above Pb [1/bar]',float(clean_num(p.get('ct_1bar'),1.5e-4)),'rct'+sid,1e-6,1e-2,fmt='%.2e')
                    else:
                        p['gas_sg']=synced_number(st,'Gas specific gravity [-]',float(clean_num(p.get('gas_sg'),0.7)),'rsg'+sid,0.55,1.5)
                        if p['fluid_phase']=='gas_condensate': p['cgr_sm3_per_msm3']=synced_number(st,'Condensate-gas ratio [Sm³/MSm³]',float(clean_num(p.get('cgr_sm3_per_msm3'),100.0)),'rcg'+sid,0.0,2000.0)
                    p['swi']=synced_number(st,'Initial water saturation Swi [-]',float(clean_num(p.get('swi'),0.2)),'rsw'+sid,0.0,0.9)
                    p['min_pressure_bar']=unit_input(f"Abandonment pressure [{ul['pressure']}]",float(clean_num(p.get('min_pressure_bar'),20.0)),pressure_to_display,pressure_from_display,'rmp'+sid,0.0,1500.0)
                with st.expander('Drive & water/GOR development'):
                    p['aquifer_pi_m3d_bar']=synced_number(st,'Aquifer productivity [m³/d/bar] (0 = none)',float(clean_num(p.get('aquifer_pi_m3d_bar'),0.0)),'raq'+sid,0.0,1e5)
                    if p['fluid_phase']=='oil':
                        p['water_breakthrough_rf']=synced_number(st,'Water breakthrough at recovery factor [-]',float(clean_num(p.get('water_breakthrough_rf'),0.05)),'rwb'+sid,0.0,0.9)
                        p['rf_at_max_water_cut']=synced_number(st,'Recovery factor at maximum water cut [-]',float(clean_num(p.get('rf_at_max_water_cut'),0.40)),'rwm'+sid,0.01,0.95)
                        p['max_water_cut']=synced_number(st,'Maximum water cut [-]',float(clean_num(p.get('max_water_cut'),0.9)),'rwx'+sid,0.0,0.99)
                        p['gor_rise_factor']=synced_number(st,'GOR rise below Pb [× per fraction of Pb]',float(clean_num(p.get('gor_rise_factor'),3.0)),'rgr'+sid,0.0,50.0)
                from network.reservoir_mb import Tank
                t_=Tank(n); linked_=[w['name'] for w in st.session_state.nodes if (w.get('params') or {}).get('reservoir_id')==sid]
                st.caption(f"Pore volume {t_.pv/1e6:,.2f} MSm³ (reservoir) · drains to: {', '.join(linked_) or 'no wells yet — drag this tank onto its wells'}")
            if n['kind'] in ('water_injector','gas_injector'):
                p['injectivity_m3d_bar']=synced_number(st,'Injectivity index [m³/d/bar]',float(clean_num(p.get('injectivity_m3d_bar'),10.0)),'ii'+sid,0.0,1e5)
                p['reservoir_pressure_bar']=unit_input(f"Reservoir pressure [{ul['pressure']}]",float(clean_num(p.get('reservoir_pressure_bar'),200.0)),pressure_to_display,pressure_from_display,'ipr'+sid,1.,1500.)
                p['depth_m']=unit_input(f"TVD [{ul['length']}]",float(clean_num(p.get('depth_m'),2000.0)),length_to_display,length_from_display,'idp'+sid,0.,10000.)
                p['available']=synced_checkbox(st,'Injector available',p.get('available',True) not in (False,'false','False',0),'iav'+sid)
                tanks_={t['id']:t['name'] for t in st.session_state.nodes if t.get('kind')=='reservoir'}
                if tanks_:
                    cur=p.get('reservoir_id') if p.get('reservoir_id') in tanks_ else ''
                    sel=synced_select(st,'Drains reservoir tank',['']+list(tanks_),cur,'rid'+sid,format_func=lambda k: tanks_.get(k,'— none (own reservoir pressure) —'))
                    if sel: p['reservoir_id']=sel
                    else: p.pop('reservoir_id',None)
            if n['kind']=='well':
                p['available']=synced_checkbox(st,'Well open / available',p.get('available',True) not in (False,'false','False',0),'wav'+sid)
                tanks_={t['id']:t['name'] for t in st.session_state.nodes if t.get('kind')=='reservoir'}
                if tanks_:
                    cur=p.get('reservoir_id') if p.get('reservoir_id') in tanks_ else ''
                    sel=synced_select(st,'Drains reservoir tank',['']+list(tanks_),cur,'rid'+sid,format_func=lambda k: tanks_.get(k,'— none (own reservoir pressure) —'))
                    if sel: p['reservoir_id']=sel
                    else: p.pop('reservoir_id',None)
                if p.get('reservoir_id') in tanks_: st.caption('Reservoir pressure comes from the tank (material balance); the value below is only used without a tank.')
                p['reservoir_pressure_bar']=unit_input(f"Reservoir pressure [{ul['pressure']}]",float(clean_num(p.get('reservoir_pressure_bar'),220)),pressure_to_display,pressure_from_display,'pr'+sid,1.,1500.)
                p['ipr_model']=synced_select(st,'IPR',['PI','Vogel','Gas'],p.get('ipr_model','PI') if p.get('ipr_model','PI') in ('PI','Vogel','Gas') else 'PI','im'+sid,format_func={'PI':'Productivity index (oil)','Vogel':'Vogel (solution gas)','Gas':'Gas backpressure'}.get)
                if p['ipr_model']=='Gas':
                    p['gas_c_sm3d_bar2n']=synced_number(st,'Backpressure C [Sm³/d/bar²ⁿ]',float(clean_num(p.get('gas_c_sm3d_bar2n'),50.0)),'gc'+sid,0.0001,1e7)
                    p['gas_n']=synced_number(st,'Backpressure exponent n [-]',float(clean_num(p.get('gas_n'),1.0)),'gn'+sid,0.5,1.0)
                elif p['ipr_model']=='PI': p['pi_m3d_bar']=unit_input('PI [m³/d/bar]' if PROFILE=='norwegian_si' else 'PI [stb/d/psi]',float(clean_num(p.get('pi_m3d_bar'),10)),pi_to_display,pi_from_display,'pi'+sid,0.001,10000.)
                else: p['qmax_m3d']=unit_input(f"Vogel qmax [{ul['liquid_rate']}]",float(clean_num(p.get('qmax_m3d'),1500)),liquid_rate_to_display,liquid_rate_from_display,'qm'+sid,0.1,1e7)
                p['depth_m']=unit_input(f"TVD [{ul['length']}]",float(clean_num(p.get('depth_m'),2000)),length_to_display,length_from_display,'de'+sid,1.,10000.); p['tubing_id_m']=unit_input(f"Tubing ID [{ul['diameter']}]",float(clean_num(p.get('tubing_id_m'),.0889)),diameter_to_display,diameter_from_display,'ti'+sid,0.01,1.,fmt='%.4f')
                p['water_cut']=synced_slider(st,'Water cut',0.,0.99,float(clean_num(p.get('water_cut'),.2)),'wc'+sid); p['gor_sm3sm3']=unit_input(f"Producing GOR [{ul['gor']}]",float(clean_num(p.get('gor_sm3sm3'),100)),gor_to_display,gor_from_display,'go'+sid,0.,5000.); p['temperature_c']=unit_input(f"Tubing temperature [{ul['temperature']}]",float(clean_num(p.get('temperature_c'),70)),temperature_to_display,temperature_from_display,'te'+sid,-10.,250.)
                p['skin']=synced_number(st,'Completion skin [-]',float(clean_num(p.get('skin'),0.0)),'sk'+sid,-6.0,100.0)
                st.caption(f"PI multiplier from skin: {well_settings(p)['pi']/max(float(clean_num(p.get('pi_m3d_bar'),10)),1e-9):.2f} (J = J₀·C/(C+S), C = {float(clean_num(p.get('skin_reference_factor'),7.0)):.1f})")
                p['vlp_model']=synced_select(st,'VLP model',['Beggs-Brill','Homogeneous'],p.get('vlp_model',p.get('correlation','Beggs-Brill')),'vm'+sid); p['correlation']=p['vlp_model']
                p['lift_type']=synced_select(st,'Artificial lift',['none','ESP','gas_lift'],p.get('lift_type','none') if p.get('lift_type','none') in ('none','ESP','gas_lift') else 'none','lt'+sid)
                if p['lift_type']=='gas_lift':
                    p['gas_lift_injection_sm3d']=synced_number(st,'Gas-lift injection [Sm³/d]',float(clean_num(p.get('gas_lift_injection_sm3d'),30000.0)),'gli'+sid,0.0,2000000.0)
                    p['gas_lift_depth_m']=unit_input(f"Injection depth [{ul['length']}]",float(clean_num(p.get('gas_lift_depth_m'),p['depth_m'])),length_to_display,length_from_display,'gld'+sid,0.,float(p['depth_m']))
                if p['lift_type']=='ESP':
                    p['esp_rated_rate_m3d']=unit_input(f"ESP rated liquid rate [{ul['liquid_rate']}]",float(clean_num(p.get('esp_rated_rate_m3d'),1000.0)),liquid_rate_to_display,liquid_rate_from_display,'er'+sid,1.,1e7); p['esp_shutoff_head_bar']=unit_input(f"ESP shutoff head [{ul['pressure']}]",float(clean_num(p.get('esp_shutoff_head_bar'),80.0)),pressure_to_display,pressure_from_display,'eh'+sid,1.,500.); p['esp_speed_fraction']=synced_number(st,'ESP speed fraction',float(clean_num(p.get('esp_speed_fraction'),1.0)),'es'+sid,0.5,1.5)
                p['lift_assist_bar']=unit_input(f"Legacy/manual lift assistance [{ul['pressure']}]",float(clean_num(p.get('lift_assist_bar'),0.0)),pressure_to_display,pressure_from_display,'la'+sid,0.,150.)
                mx=unit_input(f"Maximum liquid rate (0 = none) [{ul['liquid_rate']}]",float(clean_num(p.get('max_liquid_rate_m3d'),0.0)),liquid_rate_to_display,liquid_rate_from_display,'wmx'+sid,0.,1e7)
                if mx>0: p['max_liquid_rate_m3d']=mx
                else: p.pop('max_liquid_rate_m3d',None)
            if st.button('Delete selected node'):
                st.session_state.nodes=[x for x in st.session_state.nodes if x['id']!=sid]; st.session_state.edges=[e for e in st.session_state.edges if e['source']!=sid and e['target']!=sid]; st.session_state.selected=None; st.rerun()
    st.subheader('Flowlines / pipelines')
    if st.session_state.edges:
        rows=[]
        for e in st.session_state.edges: rows.append({**{k:e.get(k) for k in ['id','source','target','length_m','diameter_m','roughness_m','elevation_change_m']},**{k:(e.get('params',{}) or {}).get(k) for k in ['temperature_c','water_cut','gor_sm3sm3']}})
        ed=st.data_editor(pd.DataFrame(rows),hide_index=True,use_container_width=True,disabled=['id','source','target'],key='flowline_table_'+str(abs(hash(json.dumps(rows,sort_keys=True,default=str))))) 
        for row in ed.to_dict('records'):
            e=next((x for x in st.session_state.edges if x['id']==row['id']),None)
            if e is None: continue
            for k in ['length_m','diameter_m','roughness_m','elevation_change_m']:
                v=clean_num(row.get(k))
                if v is not None: e[k]=v
            for k in ['temperature_c','water_cut','gor_sm3sm3']:
                v=clean_num(row.get(k))
                if v is not None: e.setdefault('params',{})[k]=v
    c0,c1,c2=st.columns([1.2,1,1])
    st.session_state.enforce_caps=c0.checkbox('Honour capacity limits (pro-rata well choking)',value=st.session_state.get('enforce_caps',True),help='Separator/export liquid capacities and connection max rates are enforced by choking upstream wells pro-rata, as a GAP-style constraint. Untick to only report violations.')
    if c1.button('▶ Solve network',type='primary',use_container_width=True,disabled=status==SOLVING): request_solve()
    payload=json.dumps(to_builtin({'version':'30','application':'FieldNet v30','storage_units':'canonical','display_unit_profile':PROFILE,'standard_conditions':STANDARD_CONDITIONS,'nodes':st.session_state.nodes,'edges':st.session_state.edges}),indent=2,default=str); c2.download_button('Export case JSON',payload,'fieldnet_v30_case.json','application/json',use_container_width=True)
    uploaded=st.file_uploader('Load FieldNet project JSON',type=['json'],key='project_upload')
    if uploaded is not None and st.button('Load project',use_container_width=True):
        try:
            nn,ee=normalize_project(json.load(uploaded)); nn,ee,gi=normalize_graph(nn,ee); st.session_state.nodes,st.session_state.edges=(auto_layout(nn,ee) if len({(n['x'],n['y']) for n in nn})<=1 else nn),ee; reset_solve(); st.session_state.graph_issues=gi; st.success('Project loaded'); st.rerun()
        except Exception as exc: st.error(f'Invalid project: {exc}')
    r=solved()
    if r:
        _p,_q,_i,_d=r
        g1,g2,g3,g4=st.columns(4); g1.metric('Quality gate',_i.get('quality_gate','N/A')); g2.metric('Total liquid',f"{liquid_rate_to_display(sum(v['liquid_rate_m3d'] for v in _d.values()),PROFILE):,.0f} {ul['liquid_rate']}")
        g3.metric('Flowing wells',f"{sum(1 for v in _d.values() if v['liquid_rate_m3d']>1e-6)}/{len(_d)}"); g4.metric('Constraint violations',_i.get('violations',0))
        for a in _i.get('constraint_actions',[])[-5:]: st.info(a['message'])
        for w in _i.get('well_warnings',[]): st.warning(w['message'])

with tab_nodal:
    wells=[n for n in st.session_state.nodes if n['kind']=='well']
    if wells:
        wmap={w['id']:w for w in wells}
        wid=pick('Well',list(wmap),'nodal_well',format_func=lambda k: wmap[k]['name'])
        # Same well model and tank link as the network solve, so both always agree.
        linked_nodes=apply_tank_links(st.session_state.nodes); w=next(x for x in linked_nodes if x['id']==wid); prm=dict(w['params'])
        tank=next((t for t in st.session_state.nodes if t['id']==prm.get('reservoir_id')),None)
        r=solved()
        fixed=[float(n['pressure_bar']) for n in st.session_state.nodes if n.get('pressure_bar') is not None and n['kind'] not in ('well','reservoir')]
        default_whp=r[0][wid] if (r and wid in r[0]) else (w.get('pressure_bar') or (min(fixed)+2.0 if fixed else 30.0))
        with st.container(border=True):
            c0,c1,c2=st.columns(3)
            with c0: whp=unit_input(f"Wellhead pressure [{ul['pressure']}]",float(default_whp),pressure_to_display,pressure_from_display,'nodal_whp_'+wid,1.,1000.)
            prm['vlp_model']=prm['correlation']=c1.selectbox('Tubing correlation',['Beggs-Brill','Homogeneous'],index=0 if str(prm.get('vlp_model',prm.get('correlation','Beggs-Brill'))).lower().startswith('beggs') else 1,key='nodal_vlp_'+wid)
            prm['lift_type']=c2.selectbox('Lift case',['none','gas_lift','ESP'],index=['none','gas_lift','ESP'].index(prm.get('lift_type','none')) if prm.get('lift_type','none') in ('none','gas_lift','ESP') else 0,key='nodal_lift_'+wid)
            if prm['lift_type']=='gas_lift': prm['gas_lift_injection_sm3d']=st.number_input('Gas-lift injection [Sm³/d]',0.0,2000000.0,float(clean_num(prm.get('gas_lift_injection_sm3d'),30000.0)),key='nodal_gl_'+wid)
            if prm['lift_type']=='ESP':
                e1,e2=st.columns(2)
                with e1: prm['esp_rated_rate_m3d']=unit_input(f"ESP rated rate [{ul['liquid_rate']}]",float(clean_num(prm.get('esp_rated_rate_m3d'),1000.0)),liquid_rate_to_display,liquid_rate_from_display,'nodal_er_'+wid,1.,1e7)
                with e2: prm['esp_shutoff_head_bar']=unit_input(f"ESP shutoff head [{ul['pressure']}]",float(clean_num(prm.get('esp_shutoff_head_bar'),80.0)),pressure_to_display,pressure_from_display,'nodal_eh_'+wid,1.,500.)
            st.caption(('Reservoir pressure from tank **'+tank['name']+'** (material balance). ' if tank else 'Reservoir pressure from the well input. ')+'WHP defaults to the solved network value when a solution exists.')
        ws=well_settings(prm)
        from physics.well_model import ipr_pwf, vlp_bhp, solve_well_rate, rate_capacity
        qcap=max(rate_capacity(ws),1.0); qs=[qcap*i/80 for i in range(81)]
        curve=pd.DataFrame({'q':qs,'IPR':[ipr_pwf(q,ws) for q in qs],'VLP':[vlp_bhp(q,whp,ws)[0] for q in qs]})
        curve=curve[curve['IPR']>=0]
        qop,stat=solve_well_rate(whp,ws)
        fig=go.Figure(); X=[liquid_rate_to_display(v,PROFILE) for v in curve['q']]
        fig.add_scatter(x=X,y=[pressure_to_display(v,PROFILE) for v in curve['IPR']],name='Inflow (IPR)',mode='lines',line=dict(color=charts.OIL,width=2))
        fig.add_scatter(x=X,y=[pressure_to_display(v,PROFILE) for v in curve['VLP']],name=f"Outflow (VLP, {prm['vlp_model']})",mode='lines',line=dict(color=charts.CATEGORICAL[0],width=2))
        if qop>0:
            bop=ipr_pwf(qop,ws); fig.add_scatter(x=[liquid_rate_to_display(qop,PROFILE)],y=[pressure_to_display(bop,PROFILE)],name='Operating point',mode='markers',marker=dict(size=11,color=charts.GAS,line=dict(width=2,color='white')))
        st.plotly_chart(charts.style(fig,f"{w['name']} — nodal analysis",f"Bottom-hole pressure [{ul['pressure']}]",f"Liquid rate [{ul['liquid_rate']}]",420),use_container_width=True)
        a,b,c,d=st.columns(4)
        a.metric('Liquid rate',f"{liquid_rate_to_display(qop,PROFILE):,.1f} {ul['liquid_rate']}")
        b.metric('Oil rate',f"{liquid_rate_to_display(qop*(1-ws['water_cut']),PROFILE):,.1f} {ul['liquid_rate']}")
        c.metric('Gas rate',f"{qop*(1-ws['water_cut'])*ws['gor']:,.0f} Sm³/d")
        d.metric('Status',{'flowing':'Flowing','rate_limited':'Rate-limited','dead':'Cannot flow','below_min_rate':'Below min. rate','shut_in':'Shut in'}.get(stat,stat))
        if qop<=0: st.warning(f"No stable IPR/VLP intersection at {pressure_to_display(whp,PROFILE):.1f} {ul['pressure']} WHP — the well cannot flow against this back-pressure. Lower the WHP or add lift.")
        if prm['lift_type']=='ESP' and qop>0:
            ep=esp_performance(qop,rated_rate_m3d=ws['esp']['rated_rate_m3d'],shutoff_head_bar=ws['esp']['shutoff_head_bar'],speed_fraction=ws['esp']['speed_fraction'])
            st.info(f"ESP head {pressure_to_display(ep['head_bar'],PROFILE):.1f} {ul['pressure']} · hydraulic power {ep['hydraulic_power_kw']:.0f} kW · {'within' if ep['within_rate_envelope'] else 'OUTSIDE'} recommended rate envelope")
        with st.expander('Gas-lift performance curve'):
            gmax=st.number_input('Maximum injection to screen [Sm³/d]',10000.0,2000000.0,max(3*float(clean_num(prm.get('gas_lift_injection_sm3d'),30000.0)),90000.0),10000.0,key='nodal_glmax_'+wid)
            if st.button('Compute gas-lift curve',key='nodal_glbtn_'+wid):
                rows=[]
                for inj in [gmax*i/12 for i in range(13)]:
                    pp=dict(prm,lift_type='gas_lift',gas_lift_injection_sm3d=inj); q_,_=solve_well_rate(whp,well_settings(pp)); rows.append({'Gas-lift injection [Sm3/d]':inj,'Liquid rate [m3/d]':q_})
                gdf=pd.DataFrame(rows); best=gdf.loc[gdf['Liquid rate [m3/d]'].idxmax()]
                st.plotly_chart(charts.lines(gdf,'Gas-lift injection [Sm3/d]',['Liquid rate [m3/d]'],'Gas-lift performance curve','Sm³/d liquid',colors={'Liquid rate [m3/d]':charts.OIL}),use_container_width=True)
                st.success(f"Maximum liquid {best['Liquid rate [m3/d]']:,.0f} Sm³/d at {best['Gas-lift injection [Sm3/d]']:,.0f} Sm³/d injection (screening — no valve or compressor model).")
        st.caption('Artificial-lift outputs are screening calculations. Use calibrated VLP and vendor ESP/gas-lift design models before equipment selection.')
    else: st.info('Add a well to run nodal analysis.')

with tab_tanks:
    st.subheader('Reservoir tanks')
    st.caption('Tanks hold the in-place volume and fluid phase. Drag a tank onto a well (or injector) in the editor to assign it; linked wells take the tank pressure, and the forecast depletes the tank by material balance.')
    ts=tank_summary(st.session_state.nodes)
    if ts:
        tdf=pd.DataFrame(ts); st.dataframe(tdf.style.format({'Pi [bar]':'{:.0f}','STOIIP [MSm3]':'{:,.2f}','GIIP [GSm3]':'{:,.3f}','Pore volume [MSm3 res]':'{:,.2f}'},na_rep='—'),hide_index=True,use_container_width=True)
        unl=[w['name'] for w in st.session_state.nodes if w.get('kind')=='well' and not (w.get('params') or {}).get('reservoir_id')]
        if unl: st.warning('Producers without a tank (they use per-well decline in forecasts): '+', '.join(unl))
    else:
        st.info('No reservoir tank yet. Add **Reservoir tank** from the editor palette, set its in-place volume and fluid phase in the property panel, then drag it onto the wells it drains.')

with tab_diag:
    pipes=[e for e in st.session_state.edges if e.get('kind','pipeline')=='pipeline']
    r=solved()
    if not r: st.info('Solve the network first to generate pressure profiles.')
    elif pipes:
        pm={e['id']:e for e in pipes}
        pid=pick('Pipeline',list(pm),'diag_pipe'); e=pm[pid]; p,q,info,d=r; prm=e.get('params',{}) or {}
        qq=float(q[e['id']]); inlet=p[e['source']] if qq>=0 else p[e['target']]
        L=float(clean_num(e.get('length_m'),1000.0)); dz=float(clean_num(e.get('elevation_change_m'),0.0))
        prof=pressure_profile(abs(qq),L,float(clean_num(e.get('diameter_m'),.154)),float(clean_num(e.get('roughness_m'),4.5e-5)),dz if qq>=0 else -dz,inlet,float(clean_num(prm.get('temperature_c'),50)),float(clean_num(prm.get('water_cut'),.2)),float(clean_num(prm.get('gor_sm3sm3'),100)),float(clean_num(prm.get('api'),35)),float(clean_num(prm.get('gas_sg'),.75)))
        xcol=f"Distance from inlet [{ul['length']}]"; ppcol=f"Pressure [{ul['pressure']}]"; dfp=pd.DataFrame({xcol:[length_to_display(x,PROFILE) for x in prof['distance_m']],ppcol:[pressure_to_display(x,PROFILE) for x in prof['pressure_bar']]}); st.plotly_chart(charts.style(px.line(dfp,x=xcol,y=ppcol,title=f"{e['id']} pressure profile"+(' (reverse flow)' if qq<0 else ''))),use_container_width=True)
        outlet_node=e['target'] if qq>=0 else e['source']
        st.write('Profile outlet pressure:',f"{pressure_to_display(prof['pressure_bar'][-1],PROFILE):.2f} {ul['pressure']}",' | Network node:',f"{pressure_to_display(p[outlet_node],PROFILE):.2f} {ul['pressure']}")
        if prof['regime']: st.dataframe(pd.DataFrame({'Segment':range(1,len(prof['regime'])+1),'Liquid holdup':prof['holdup'],'Flow regime':prof['regime']}),hide_index=True,use_container_width=True)

with tab_fa:
    st.subheader('Flow assurance')
    st.caption('Post-solve screening layer. Thermal, hydrate, wax, erosion, liquid-loading and slugging indicators do not alter hydraulic convergence and are not substitutes for compositional or transient flow-assurance simulation.')
    r=solved()
    if not r:
        st.info('Solve the network first to evaluate flow assurance.')
    else:
        fa=network_flow_assurance(st.session_state.nodes,st.session_state.edges,r,segments=20)
        rc=fa['risk_counts']; cols=st.columns(5)
        for c,(k,label) in zip(cols,[('hydrate_risk','Hydrate'),('wax_risk','Wax'),('erosion_risk','Erosion'),('liquid_loading_risk','Liquid loading'),('slugging_indicator','Slugging indicator')]): c.metric(label,rc[k])
        pipes=fa['pipelines']
        if not pipes: st.info('No solved pipeline edges available.')
        else:
            fam={x['edge_id']:x for x in pipes}
            choice=fam[pick('Pipeline flow-assurance report',list(fam),'fa_pipe')]
            a1,b1,c1,d1=st.columns(4); a1.metric('Outlet temperature',f"{temperature_to_display(choice['outlet_temperature_c'],PROFILE):.1f} {ul['temperature']}"); b1.metric('Min hydrate margin',f"{choice['minimum_hydrate_margin_c']:.1f} °C"); c1.metric('Max erosion ratio',f"{choice['maximum_erosion_ratio']:.2f}"); d1.metric('Min loading ratio',f"{choice['minimum_liquid_loading_ratio']:.2f}")
            rows=[]
            for rr in choice['segments']:
                rows.append({'Segment':rr['segment'],f"Distance [{ul['length']}]":length_to_display(rr['x1_m'],PROFILE),f"Pressure [{ul['pressure']}]":pressure_to_display(rr['p_out_bar'],PROFILE),f"Temperature [{ul['temperature']}]":temperature_to_display(rr['temperature_out_c'],PROFILE),'Hydrate margin [°C]':rr['hydrate_margin_c'],'Wax margin [°C]':rr['wax_margin_c'],('Velocity [m/s]' if PROFILE=='norwegian_si' else 'Velocity [ft/s]'):velocity_to_display(rr['mixture_velocity_ms'],PROFILE),'Erosion ratio':rr['erosion_ratio'],'Loading ratio':rr['liquid_loading_ratio'],'Regime':rr['flow_regime'],'Slug indicator':rr['slugging_indicator']})
            fdf=pd.DataFrame(rows); st.dataframe(fdf,hide_index=True,use_container_width=True)
            st.plotly_chart(charts.style(px.line(fdf,x=f"Distance [{ul['length']}]",y=f"Temperature [{ul['temperature']}]",title='Thermal profile')),use_container_width=True)
            with st.expander('Model limitations / interpretation'):
                for item in choice['limitations']: st.write('•',item)
            st.download_button('Export flow-assurance JSON',json.dumps(to_builtin(fa),indent=2,default=str),'fieldnet_flow_assurance.json','application/json',use_container_width=True)

with tab_results:
    r=solved()
    if not r:
        stt,msg=solve_status(st.session_state); rec=st.session_state.get('solve') or {}
        if stt==FAILED:
            st.error('Last solve failed before producing a solution: '+msg)
            for x in ((rec.get('results') or ({},{},{},{}))[2] or {}).get('debug',[]): st.write(x.get('severity','').upper(),x.get('code',''),'—',x.get('message',''))
        else: st.info(f'{stt}: {msg} Solve the network to populate results.')
    else:
        p,q,info,d=r; nm={n['id']:n['name'] for n in st.session_state.nodes}
        k1,k2,k3,k4,k5=st.columns(5)
        k1.metric('Oil',f"{sum(v.get('oil_rate_m3d',0) for v in d.values()):,.0f} Sm³/d"); k2.metric('Water',f"{sum(v.get('water_rate_m3d',0) for v in d.values()):,.0f} Sm³/d")
        k3.metric('Gas',f"{sum(v.get('gas_rate_sm3d',0) for v in d.values())/1e3:,.0f} kSm³/d"); k4.metric('Wells flowing',f"{sum(1 for v in d.values() if v['liquid_rate_m3d']>1e-6)}/{len(d)}")
        k5.metric('Water injection',f"{sum((info.get('injector_rates') or {}).values()):,.0f} m³/d")
        a,b,c,dcol=st.columns(4); a.metric('Converged','Yes' if info['success'] else 'No'); b.metric('Max residual',f"{info['max_abs_residual']:.2e}"); c.metric('Quality gate',info.get('quality_gate','—')); dcol.metric('Constraint violations',info.get('violations',0))
        if info.get('quality_gate')!='PASS': st.warning('Quality gate FAIL. Review the solver debugger below (topology, boundary conditions, dead/unstable wells).')
        with st.expander('Solver diagnostics'):
            for item in solver_diagnostics(info,p,q): st.write(item['severity'].upper(), item['code'], '—', item['message'])
        with st.expander('Solver debugger',expanded=info.get('quality_gate')=='FAIL'):
            st.write('Mode:',info.get('solver_mode','legacy')); st.write('Quality gate:',info.get('quality_gate','N/A')); st.write('Function evaluations:',info.get('nfev','N/A')); st.write('Jacobian condition:',info.get('jacobian_condition','N/A')); st.write('Warm start used:',info.get('warm_start_used',False))
            if info.get('attempt_history'): st.dataframe(pd.DataFrame(info['attempt_history']),hide_index=True,use_container_width=True)
            if info.get('debug'): st.dataframe(pd.DataFrame(info['debug']),hide_index=True,use_container_width=True)
            audit=info.get('physical_residual_audit',{})
            if audit.get('edge_residuals'): st.caption('Pressure-equation residuals [bar]'); st.dataframe(pd.DataFrame(audit['edge_residuals']),hide_index=True,use_container_width=True)
            if audit.get('node_residuals'): st.caption('Node mass-balance residuals [m³/d]'); st.dataframe(pd.DataFrame(audit['node_residuals']),hide_index=True,use_container_width=True)
        pcol=f"Pressure [{ul['pressure']}]"; qcol=f"Liquid rate [{ul['liquid_rate']}]"
        rdf=pd.DataFrame([{'Component':nm.get(nid,nid),pcol:pressure_to_display(v,PROFILE)} for nid,v in p.items()]); qdf=pd.DataFrame([{'Connection':eid,qcol:liquid_rate_to_display(v,PROFILE)} for eid,v in q.items()])
        wdf=pd.DataFrame([{'Well':nm.get(nid,nid),'Status':v.get('status'),qcol:liquid_rate_to_display(v['liquid_rate_m3d'],PROFILE),f"Oil [{ul['liquid_rate']}]":liquid_rate_to_display(v.get('oil_rate_m3d',0.0),PROFILE),'Gas [Sm³/d]':v.get('gas_rate_sm3d',0.0),f"WHP [{ul['pressure']}]":pressure_to_display(v['whp_bar'],PROFILE),f"BHP [{ul['pressure']}]":pressure_to_display(v['bhp_bar'],PROFILE),f"Reservoir [{ul['pressure']}]":pressure_to_display(v.get('reservoir_pressure_bar',0.0),PROFILE),'Lift':v.get('lift_type'),'Holdup':v.get('liquid_holdup')} for nid,v in d.items()])
        st.dataframe(wdf,use_container_width=True,hide_index=True)
        if info.get('injectors'): st.caption('Injectors'); st.dataframe(pd.DataFrame(info['injectors']),hide_index=True,use_container_width=True)
        l,rr_=st.columns(2); l.plotly_chart(px.bar(rdf,x='Component',y=pcol,title='Node pressures'),use_container_width=True); rr_.plotly_chart(px.bar(qdf,x='Connection',y=qcol,title='Connection rates'),use_container_width=True)

with tab_constraints:
    with st.container(border=True): render_constraint_editor(st, st.session_state.nodes, st.session_state.edges)
    r=solved()
    if not r:
        st.info('Solve the network to check the constraints above against the operating point.')
    else:
        p,q,info,d=r
        c1,c2,c3=st.columns(3); c1.metric('Constraint checks',len(info.get('constraints',[]))); c2.metric('Violations',info.get('violations',0)); c3.metric('Equipment items',len(info.get('equipment',[])))
        if info.get('violations',0): st.error(f"{info['violations']} operating constraint(s) violated. Hydraulic convergence does not imply an operable case.")
        elif info.get('constraints'): st.success('All configured operating constraints are satisfied.')
        if info.get('constraint_actions'):
            st.subheader('Constraint enforcement actions'); st.dataframe(pd.DataFrame(info['constraint_actions']),hide_index=True,use_container_width=True)
        if info.get('constraints'): st.dataframe(pd.DataFrame(info['constraints']),hide_index=True,use_container_width=True)
        else: st.caption('No explicit operating constraints are configured. Set separator liquid capacity, connection maximum rate or well maximum rate in the property panel.')
        if info.get('equipment'):
            st.subheader('Rotating equipment')
            st.dataframe(pd.DataFrame(info['equipment']),hide_index=True,use_container_width=True)

with tab_ops:
    st.subheader('Network solver & debottlenecking')
    st.caption('Topology prechecks, warm starts, retry orchestration, physical residual reconstruction and equation-level failure diagnostics.')
    if st.button('Run network solver',use_container_width=True): request_solve()
    r=solved()
    if r:
        _p,_q,_i,_d=r
        a,b,c=st.columns(3); a.metric('Quality gate',_i.get('quality_gate','N/A')); b.metric('Normalized residual',f"{_i.get('normalized_residual_score',0):.3g}"); c.metric('Active constraints',len(_i.get('active_constraints',[])))
        if st.button('Screen +10% capacity debottlenecks',use_container_width=True): st.session_state.v14_debottleneck=debottleneck_screen(st.session_state.nodes,st.session_state.edges,0.10)
        if st.session_state.get('v14_debottleneck') is not None:
            if st.session_state.v14_debottleneck: st.dataframe(pd.DataFrame(st.session_state.v14_debottleneck),hide_index=True,use_container_width=True)
            else: st.caption('No capacity limits are configured, so there is nothing to debottleneck.')
        audit=calculation_audit(st.session_state.nodes,st.session_state.edges,_i,_d)
        st.download_button('Download calculation audit JSON',json.dumps(to_builtin(audit),indent=2,default=str),'fieldnet_audit.json','application/json',use_container_width=True)
    st.divider()
    st.subheader('Integrated production optimization')
    st.caption('Optimizes solver-active controls jointly against network and facility constraints. Feasibility is reported separately from production objective; global optimality is not guaranteed.')
    oc1,oc2=st.columns(2); opt_iter=oc1.slider('Optimization iterations',1,40,8); opt_seed=oc2.number_input('Optimization seed',0,999999,22)
    if st.button('Optimize integrated production', type='primary'):
        with st.spinner('Optimizing wells and solver-active equipment controls...'):
            try: st.session_state.opt_v22=optimize_integrated(st.session_state.nodes,st.session_state.edges,maxiter=int(opt_iter),seed=int(opt_seed))
            except Exception as exc: st.error(f'Optimization failed: {exc}')
    if st.session_state.get('opt_v22'):
        o=st.session_state.opt_v22; m1,m2,m3=st.columns(3); m1.metric('Optimized liquid',f"{o.get('best_rate_m3d',0):.1f} m³/d"); m2.metric('Gain',f"{o.get('production_gain_m3d',0):+.1f} m³/d"); m3.metric('Feasible','YES' if o.get('feasible') else 'NO')
        if o.get('decisions'):
            rows=[]
            for dd in o['decisions']: rows.append({'Component':dd['component_name'],'Control':dd['kind'],'Lower':dd['lower'],'Optimized':o['controls'].get(dd['key']),'Upper':dd['upper']})
            st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)
        if o.get('unsupported'):
            for msg in o['unsupported']: st.warning(msg)
        st.caption(f"Method: {o.get('method','')} · Global optimum guaranteed: {o.get('global_optimum_guaranteed',False)} · Seed: {o.get('seed')}")
        if (o.get('best') or {}).get('violations'): st.dataframe(pd.DataFrame(o['best']['violations']),hide_index=True,use_container_width=True)
        st.download_button('Download optimization JSON',json.dumps(to_builtin({k:v for k,v in o.items() if k!='best'}),indent=2,default=str),'fieldnet_optimization.json','application/json',use_container_width=True)
    st.divider(); st.subheader('One-variable sensitivity')
    candidates=[n for n in st.session_state.nodes if n['kind'] in BOUNDARY_KINDS and n.get('pressure_bar') is not None]
    if candidates:
        cm={n['id']:n for n in candidates}
        sn=cm[pick('Boundary component',list(cm),'sensnode',format_func=lambda k: cm[k]['name'])]
        lo=unit_input(f"Start pressure [{ul['pressure']}]",20.0,pressure_to_display,pressure_from_display,'sens_lo',0.1,1000.0); hi=unit_input(f"End pressure [{ul['pressure']}]",60.0,pressure_to_display,pressure_from_display,'sens_hi',0.1,1000.0); steps=st.slider('Cases',3,15,7)
        if st.button('Run pressure sensitivity'):
            st.session_state.sens=run_sensitivity(st.session_state.nodes,st.session_state.edges,'node',sn['id'],'pressure_bar',np.linspace(lo,hi,steps))
        if st.session_state.get('sens'):
            sdf=pd.DataFrame(st.session_state.sens); st.dataframe(sdf,hide_index=True,use_container_width=True); st.plotly_chart(charts.style(px.line(sdf,x='Value',y='Total liquid [m3/d]',markers=True,title='Production sensitivity to boundary pressure [bar]')),use_container_width=True)
    else: st.caption('Add a fixed-pressure boundary to run a sensitivity.')


with tab_cal:
    st.subheader('Calibration & history matching')
    st.caption('Bounded weighted least-squares against measured free-node pressures (wellheads, manifolds) and connection liquid rates. Fit quality does not imply parameter uniqueness or physical correctness.')
    r=solved(); rows=[]
    # Only FREE pressures are observable: a fixed boundary pressure is an input, so matching
    # it (as the previous table offered) had zero sensitivity to every parameter.
    for n in st.session_state.nodes:
        if n.get('pressure_bar') is None and n['kind']!='reservoir' and (not r or n['id'] in r[0]):
            rows.append({'enabled':False,'kind':'node_pressure_bar','target_id':n['id'],'name':n.get('name',n['id']),'value':float(r[0][n['id']]) if r else 0.0,'sigma':1.0})
    for e in st.session_state.edges:
        if e.get('kind','pipeline') in LINK_TYPES: rows.append({'enabled':False,'kind':'edge_rate_m3d','target_id':e['id'],'name':e.get('name',e['id']),'value':float(r[1][e['id']]) if r else float(clean_num((e.get('params') or {}).get('initial_rate_m3d'),500.0)),'sigma':10.0})
    st.caption('Measured values default to the current solution; overwrite them with field data and tick "enabled".')
    obsdf=st.data_editor(pd.DataFrame(rows),use_container_width=True,key='v23_obs_'+str(len(rows)))
    maxeval=st.slider('Maximum calibration evaluations',5,150,40,key='v23_maxeval')
    if st.button('Run calibration',use_container_width=True):
        try:
            obs=[Observation(str(rw['kind']),str(rw['target_id']),float(clean_num(rw['value'],0.0)),float(clean_num(rw['sigma'],1.0)),clean_text(rw.get('name'))) for rw in obsdf.to_dict('records') if bool(rw.get('enabled')) and clean_num(rw.get('value')) is not None]
            st.session_state.cal_v23=calibrate(st.session_state.nodes,st.session_state.edges,obs,max_nfev=maxeval)
        except Exception as exc: st.error(str(exc))
    if st.session_state.get('cal_v23'):
        c=st.session_state.cal_v23; a,b,dd=st.columns(3); a.metric('Weighted RMSE',f"{c['weighted_rmse']:.3f}"); b.metric('Jacobian rank',str(c['jacobian_rank'])); dd.metric('Locally identifiable','YES' if c['identifiable_linearized'] else 'NO')
        st.dataframe(pd.DataFrame([{'parameter':k,'value':v,'std':(c.get('parameter_std') or {}).get(k),'at_bound':c['at_bounds'].get(k)} for k,v in c['values'].items()]),use_container_width=True)
        st.dataframe(pd.DataFrame([{'measurement':o.get('name') or o['target_id'],'kind':o['kind'],'observed':o['value'],'predicted':pv,'normalized_residual':rv} for o,pv,rv in zip(c['observations'],c['predicted'],c['normalized_residuals'])]),use_container_width=True)
        if st.button('Apply calibrated parameters to the case'):
            st.session_state.nodes=to_builtin(c['calibrated_nodes']); st.session_state.edges=to_builtin(c['calibrated_edges']); st.success('Calibrated parameters applied. Re-solve the network.'); st.rerun()
        export={k:v for k,v in c.items() if k not in ('calibrated_nodes','calibrated_edges','solver_info')}
        st.download_button('Download calibration JSON',json.dumps(to_builtin(export),indent=2,default=str),'fieldnet_calibration.json','application/json',use_container_width=True)

with tab_forecast:
    render_forecast(st, st.session_state.nodes, st.session_state.edges)

with tab_dev26:
    render_schedule(st, st.session_state.nodes, st.session_state.edges)

with tab_development:
    render_scenarios(st, st.session_state.nodes, st.session_state.edges)

with tab_uncertainty:
    render_uncertainty(st, st.session_state.nodes, st.session_state.edges)


with tab_rel:
    st.subheader('Reliability & availability')
    st.caption('Screening reliability Monte Carlo. Failure/repair availability is separate from hydraulic convergence; exponential MTBF/MTTR assumptions should be replaced with asset data when available.')
    candidates=[x for x in [*st.session_state.nodes,*st.session_state.edges] if x.get('kind') in ('well','pump','compressor','separator','separator_stage','pipeline')]
    rows=[{'enabled':False,'target_id':x['id'],'name':x.get('name',x['id']),'kind':x.get('kind'),'mtbf_days':365.0,'mttr_days':3.0,'redundancy_group':'','required_online':1} for x in candidates]
    rdf=st.data_editor(pd.DataFrame(rows),use_container_width=True,key='v24_rel_specs')
    a,b,c,d=st.columns(4); ryears=a.number_input('Reliability years',0.1,30.0,1.0,0.5); rstep=b.selectbox('Reliability step [days]',[1,7,14,30]); rn=c.number_input('Realizations',10,5000,200,10); rseed=d.number_input('Seed',0,999999,2401,1)
    base_rate=st.number_input('Reference production [m³/d]',0.0,1e7,1000.0,100.0)
    if st.button('Run reliability study',type='primary',use_container_width=True):
        try:
            specs=[ReliabilitySpec(clean_text(rw.get('target_id')),clean_num(rw.get('mtbf_days'),365.0),clean_num(rw.get('mttr_days'),3.0),(),clean_text(rw.get('redundancy_group')),int(clean_num(rw.get('required_online'),1))) for rw in rdf.to_dict('records') if bool(rw.get('enabled'))]
            if not specs: raise ValueError('Tick "enabled" for at least one component.')
            st.session_state.rel_v24=run_reliability(ReliabilityStudy(float(ryears),int(rstep),int(rn),int(rseed),specs),float(base_rate))
        except Exception as exc: st.error(str(exc))
    if st.session_state.get('rel_v24'):
        rr=st.session_state.rel_v24; sm=rr['summary']; a,b,c,d=st.columns(4); a.metric('Mean availability',f"{sm['mean_availability']:.1%}"); b.metric('P90 availability',f"{sm['p90_availability']:.1%}"); c.metric('Mean deferred',f"{sm['mean_deferred_m3']:,.0f} m³"); d.metric('P(A<90%)',f"{sm['probability_below_90pct_availability']:.1%}")
        rrf=pd.DataFrame(rr['realizations']); st.plotly_chart(charts.style(px.histogram(rrf,x='availability',title='Availability distribution')),use_container_width=True); st.dataframe(rrf,hide_index=True,use_container_width=True)
        st.download_button('Download reliability JSON',json.dumps(to_builtin(rr),indent=2,default=str),'fieldnet_v24_reliability.json','application/json',use_container_width=True)

with tab_res25:
    st.subheader('Multi-tank reservoir coupling')
    st.caption('Reduced-order quasi-steady material balance coupled to the production network. Communicating tanks, aquifer influx and injector connectivity are planning models—not a 3-D reservoir simulator.')
    wells25=[n for n in st.session_state.nodes if n.get('kind')=='well']
    default_tanks=[]
    canvas_tanks=[n for n in st.session_state.nodes if n.get('kind')=='reservoir']
    if canvas_tanks:
        for n in canvas_tanks:
            rp=n.get('params',{}) or {}
            default_tanks.append({'id':n['id'],'name':n.get('name',n['id']),'pressure_bar':float(clean_num(rp.get('reservoir_pressure_bar'),250.0)),'pore_volume_m3':float(clean_num(rp.get('pore_volume_m3'),2e6)),'total_compressibility_1bar':float(clean_num(rp.get('total_compressibility_1bar'),8e-5)),'min_pressure_bar':float(clean_num(rp.get('min_pressure_bar'),20.0))})
    else:
        for i,w in enumerate(wells25):
            rp=float(clean_num(w.get('params',{}).get('reservoir_pressure_bar'),220.0))
            default_tanks.append({'id':f'T{i+1}','name':f'Tank {i+1}','pressure_bar':rp,'pore_volume_m3':2e6,'total_compressibility_1bar':8e-5,'min_pressure_bar':20.0})
    tdf=st.data_editor(pd.DataFrame(default_tanks),num_rows='dynamic',use_container_width=True,key='v25_tanks')
    maprows=[]
    tids=[clean_text(x) for x in tdf.get('id',pd.Series(dtype=str)).tolist() if clean_text(x)]
    for i,w in enumerate(wells25): maprows.append({'well_id':w['id'],'well':w.get('name',w['id']),'tank_id':tids[min(i,len(tids)-1)] if tids else ''})
    mdf=st.data_editor(pd.DataFrame(maprows),use_container_width=True,key='v25_mapping')
    st.markdown('**Tank communication**')
    ldf=st.data_editor(pd.DataFrame(columns=['tank_a','tank_b','transmissibility_m3d_bar','max_transfer_m3d']),num_rows='dynamic',use_container_width=True,key='v25_links')
    st.markdown('**Aquifer support**')
    adf=st.data_editor(pd.DataFrame(columns=['tank_id','productivity_m3d_bar','reference_pressure_bar','max_influx_m3d']),num_rows='dynamic',use_container_width=True,key='v25_aquifers')
    st.markdown('**Injector connectivity and schedule**')
    cdf25=st.data_editor(pd.DataFrame(columns=['injector_id','tank_id','weight']),num_rows='dynamic',use_container_width=True,key='v25_conn')
    sdf25=st.data_editor(pd.DataFrame(columns=['date','injector_id','rate_m3d']),num_rows='dynamic',use_container_width=True,key='v25_injsched')
    a,b,c=st.columns(3); rstart=a.date_input('Coupled forecast start',key='v25_start').isoformat(); ryears=b.number_input('Coupled years',0.02,50.0,1.0,0.25,key='v25_years'); rstep=c.selectbox('Coupled timestep [days]',[7,14,30,60,90],index=2,key='v25_step')
    if st.button('Run coupled forecast',type='primary',use_container_width=True):
        try:
            tanks=[]
            for rw in tdf.to_dict('records'):
                tid=clean_text(rw.get('id'))
                if not tid: continue
                pb=clean_num(rw.get('pressure_bar'))
                if pb is None: raise ValueError(f'Tank {tid}: pressure is required')
                tanks.append({'id':tid,'name':clean_text(rw.get('name'),tid),'initial_pressure_bar':pb,'pressure_bar':pb,'pore_volume_m3':clean_num(rw.get('pore_volume_m3'),2e6),'total_compressibility_1bar':clean_num(rw.get('total_compressibility_1bar'),8e-5),'min_pressure_bar':clean_num(rw.get('min_pressure_bar'),20.0)})
            mapping={clean_text(rw.get('well_id')):clean_text(rw.get('tank_id')) for rw in mdf.to_dict('records') if clean_text(rw.get('well_id')) and clean_text(rw.get('tank_id'))}
            links=[CommunicationLink(clean_text(rw.get('tank_a')),clean_text(rw.get('tank_b')),clean_num(rw.get('transmissibility_m3d_bar'),0.0),clean_num(rw.get('max_transfer_m3d'),1e30)) for rw in ldf.to_dict('records') if clean_text(rw.get('tank_a')) and clean_text(rw.get('tank_b'))]
            aquifers=[AquiferSpec(clean_text(rw.get('tank_id')),clean_num(rw.get('productivity_m3d_bar'),0.0),clean_num(rw.get('reference_pressure_bar'),250.0),clean_num(rw.get('max_influx_m3d'),1e30)) for rw in adf.to_dict('records') if clean_text(rw.get('tank_id'))]
            conns=[InjectorConnection(clean_text(rw.get('injector_id')),clean_text(rw.get('tank_id')),clean_num(rw.get('weight'),1.0)) for rw in cdf25.to_dict('records') if clean_text(rw.get('injector_id')) and clean_text(rw.get('tank_id'))]
            sched=[{'date':pd.Timestamp(clean_text(rw.get('date'))).date().isoformat(),'injector_id':clean_text(rw.get('injector_id')),'rate_m3d':clean_num(rw.get('rate_m3d'),0.0)} for rw in sdf25.to_dict('records') if clean_text(rw.get('date')) and clean_text(rw.get('injector_id'))]
            st.session_state.res25=run_coupled_forecast_v25(st.session_state.nodes,st.session_state.edges,tanks,mapping,rstart,float(ryears),int(rstep),injector_schedule=sched,injector_connections=conns,aquifers=aquifers,communication_links=links)
        except Exception as exc: st.error(str(exc))
    if st.session_state.get('res25'):
        rr=st.session_state.res25; tf=pd.DataFrame(rr['tanks']); ff=pd.DataFrame(rr['field'])
        if not tf.empty:
            st.plotly_chart(charts.style(px.line(tf,x='Date',y='pressure_after_bar',color='tank_id',title='Coupled tank pressure')),use_container_width=True)
            st.dataframe(tf,hide_index=True,use_container_width=True)
        if not ff.empty: st.plotly_chart(charts.style(px.line(ff,x='Date',y='Oil [m3/d]',title='Coupled field oil')),use_container_width=True)
        if rr.get('transfers'): st.dataframe(pd.DataFrame(rr['transfers']),hide_index=True,use_container_width=True)
        st.download_button('Download coupling JSON',json.dumps(to_builtin(rr),indent=2,default=str),'fieldnet_v25_reservoir_coupling.json','application/json',use_container_width=True)


with tab_io27:
    render_interchange_v27(st, st.session_state.nodes, st.session_state.edges)


with tab_qa28:
    st.subheader('Engineering QA & model assurance')
    st.caption('Read-only assurance: definite invariant violations are errors; suspicious engineering values are warnings. No inputs are auto-corrected.')
    if st.button('Run Model Quality Report', type='primary', use_container_width=True):
        sr=solved()  # (pressures, flows, info, details); previously a dict was expected so post-solve checks never ran
        st.session_state.qa28=model_quality_report(st.session_state.nodes,st.session_state.edges,unit_profile=st.session_state.unit_profile,solve_result=sr,forecast=st.session_state.get('forecast'))
    if st.session_state.get('qa28'):
        qr=st.session_state.qa28; a,b,c,d=st.columns(4); a.metric('Quality gate',qr['quality_gate']); b.metric('Errors',qr['counts'].get('error',0)); c.metric('Warnings',qr['counts'].get('warning',0)); d.metric('Checks/info',qr['counts'].get('info',0))
        qdf=pd.DataFrame(qr['issues']); st.dataframe(qdf,hide_index=True,use_container_width=True)
        st.download_button('Download model quality report',json.dumps(to_builtin(qr),indent=2,default=str),'fieldnet_v28_model_quality.json','application/json',use_container_width=True)


with tab_scen29:
    render_scenario_v29(st, st.session_state.nodes, st.session_state.edges, st.session_state.unit_profile)
