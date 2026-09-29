import json, uuid
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from network.examples import demo_case
from solver.steady_state import solve_network
from solver.professional import solve_professional
from solver.v21 import solve_v21
from optimization.debottleneck import debottleneck_screen
from solver.audit_report import calculation_audit
from physics.ipr import ipr_rate_m3d
from physics.vlp import tubing_bhp_bar
from physics.beggs_brill import pressure_profile
from optimization.allocation import optimize_allocation, bottlenecks
from optimization.integrated_v22 import optimize_integrated
from optimization.calibration_v23 import Observation, CalParameter, calibrate, discover_parameters
from optimization.scenarios import run_sensitivity
from ui.editor_component import network_editor
from ui.history import normalize_project
from ui.topology import validate_topology, auto_layout
from network.forecast import run_forecast
from network.planning import run_scenarios, uncertainty_cases
from ui.theme import THEMES, apply_theme
from solver.diagnostics import solver_diagnostics
from physics.advanced_wells import skin_adjusted_pi, artificial_lift_assist_bar
from physics.performance_maps import pump_map, compressor_map
from ui.field_development import render_field_development
from ui.development_v26 import render_development_v26
from ui.interchange_v27 import render_interchange_v27
from ui.scenario_v29 import render_scenario_v29
from solver.model_assurance_v28 import model_quality_report
from physics.unit_system import (PROFILES, labels as unit_labels, STANDARD_CONDITIONS, pressure_to_display, pressure_from_display, temperature_to_display, temperature_from_display, length_to_display, length_from_display, diameter_to_display, diameter_from_display, liquid_rate_to_display, liquid_rate_from_display, gor_to_display, gor_from_display, pi_to_display, pi_from_display, velocity_to_display, heat_transfer_u_to_display, heat_transfer_u_from_display)
from ui.uncertainty_v17 import render_uncertainty
from physics.flow_assurance import network_flow_assurance
from physics.well_performance_v20 import nodal_operating_point, optimize_gas_lift, esp_performance, well_performance_qa
from network.reliability_v24 import ReliabilitySpec, ReliabilityStudy, run_reliability, theoretical_availability
from network.reservoir_v25 import AquiferSpec, CommunicationLink, InjectorConnection
from network.coupled_forecast_v25 import run_coupled_forecast_v25

st.set_page_config(page_title='FieldNet v29',page_icon='⛽',layout='wide')
if 'theme_name' not in st.session_state: st.session_state.theme_name='Equinor-inspired Light'
with st.sidebar:
    st.session_state.theme_name=st.selectbox('Theme',list(THEMES),index=list(THEMES).index(st.session_state.theme_name))
    if 'unit_profile' not in st.session_state: st.session_state.unit_profile='norwegian_si'
    st.session_state.unit_profile=st.selectbox('Engineering units',list(PROFILES),index=list(PROFILES).index(st.session_state.unit_profile),format_func=lambda k: PROFILES[k].label)
    ul=unit_labels(st.session_state.unit_profile)
    st.caption(f"Display/input profile: {PROFILES[st.session_state.unit_profile].label}. Internal solver remains canonical SI/bar.")
    with st.expander('Unit reference conditions'):
        st.write(f"Standard volumes: {STANDARD_CONDITIONS['standard_temperature_c']:.0f} °C and {STANDARD_CONDITIONS['standard_pressure_bara']:.5f} bara. Pressure-dependent PVT uses absolute pressure.")
apply_theme(st,st.session_state.theme_name)
st.markdown("<div class='fieldnet-brand'><h2>FieldNet v29 — Engineering QA & Model Assurance</h2><p>Made by Merouane Hamdani · For non-commercial use · Independent engineering prototype</p></div>",unsafe_allow_html=True)
st.caption('Equinor-inspired themes are unofficial and are not affiliated with, endorsed by, or sponsored by Equinor ASA. Validate engineering correlations before operational use.')
if 'nodes' not in st.session_state: st.session_state.nodes,st.session_state.edges=demo_case()
if 'results' not in st.session_state: st.session_state.results=None

def unit_input(label, canonical, to_display, from_display, key, min_value=None, max_value=None, step=None, fmt=None):
    profile=st.session_state.unit_profile
    kwargs={'key':f'{key}_{profile}'}
    if min_value is not None: kwargs['min_value']=float(to_display(min_value,profile))
    if max_value is not None: kwargs['max_value']=float(to_display(max_value,profile))
    if step is not None: kwargs['step']=float(step)
    if fmt is not None: kwargs['format']=fmt
    shown=st.number_input(label, value=float(to_display(canonical,profile)), **kwargs)
    return from_display(shown,profile)

with st.sidebar:
    st.header('Component palette')
    kind=st.selectbox('Component',['well','manifold','separator','separator_stage','water_source','gas_source','water_injector','gas_injector','oil_export','gas_export','water_disposal','sink']); name=st.text_input('Name',f'{kind.upper()}-{len(st.session_state.nodes)+1:02d}')
    if st.button('Add component',use_container_width=True):
        nid=str(uuid.uuid4())[:8]; pressure=None; prm={}
        if kind=='well': prm={'reservoir_pressure_bar':220.0,'ipr_model':'PI','pi_m3d_bar':10.0,'qmax_m3d':1500.0,'initial_pressure_bar':80.0,'initial_rate_m3d':500.0,'depth_m':2000.0,'tubing_id_m':0.0889,'tubing_roughness_m':4.5e-5,'temperature_c':70.0,'water_cut':0.2,'gor_sm3sm3':100.0,'api':35.0,'gas_sg':0.75,'correlation':'Beggs-Brill'}
        if kind in ('sink','separator','separator_stage','oil_export','gas_export','water_disposal'): pressure=35.0
        if kind in ('water_source','gas_source'): pressure=180.0
        if kind in ('water_injector','gas_injector'): prm={'injection_fluid':'water' if kind=='water_injector' else 'gas','initial_pressure_bar':120.0}
        st.session_state.nodes.append({'id':nid,'kind':kind,'name':name,'pressure_bar':pressure,'x':0,'y':0,'params':prm}); st.rerun()
    st.divider(); st.subheader('Connect')
    labels={n['id']:f"{n['name']} ({n['id']})" for n in st.session_state.nodes}
    if len(labels)>=2:
        s=st.selectbox('From',list(labels),format_func=labels.get); t=st.selectbox('To',list(labels),index=1,format_func=labels.get); link_kind=st.selectbox('Connection type',['pipeline','choke','control_valve','pump','compressor'])
        if st.button('Add connection',use_container_width=True,disabled=s==t):
            st.session_state.edges.append({'id':str(uuid.uuid4())[:8],'source':s,'target':t,'kind':link_kind,'length_m':1000.0 if link_kind=='pipeline' else 0.0,'diameter_m':0.154,'roughness_m':4.5e-5,'elevation_change_m':0.0,'params':{'temperature_c':50,'water_cut':0.2,'gor_sm3sm3':100,'api':35,'gas_sg':0.75,'initial_rate_m3d':500,'correlation':'Beggs-Brill','cv':80.0,'shutoff_head_bar':35.0,'rated_rate_m3d':1500.0,'efficiency':0.75,'pressure_ratio':1.8,'max_discharge_bar':250.0,'map_enabled':False,'rated_gas_rate_sm3d':150000.0,'speed_fraction':1.0,'opening':1.0,'max_rate_m3d':3000.0}}); st.rerun()
    if st.button('Reset demo',use_container_width=True): st.session_state.nodes,st.session_state.edges=demo_case(); st.session_state.results=None; st.rerun()

tab_net,tab_nodal,tab_diag,tab_fa,tab_results,tab_constraints,tab_ops,tab_cal,tab_forecast,tab_development,tab_dev26,tab_uncertainty,tab_rel,tab_res25,tab_io27,tab_qa28,tab_scen29=st.tabs(['Network','Nodal analysis','Hydraulic profiles','Flow assurance','Results','Constraints & equipment','Optimization & sensitivity','Calibration','Life-of-field','Field Development','Development Planning','Uncertainty','Reliability','Reservoir coupling','Data & interoperability','Model assurance','Scenarios'])
with tab_net:
    canvas,props=st.columns([2.1,1])
    with canvas:
        edit = network_editor(st.session_state.nodes, st.session_state.edges, st.session_state.results, key='network-v14')
        selected = edit.get('selected') if isinstance(edit,dict) else None
        if isinstance(edit,dict) and isinstance(edit.get('nodes'),list) and isinstance(edit.get('edges'),list):
            incoming={'nodes':edit['nodes'],'edges':edit['edges']}
            current={'nodes':st.session_state.nodes,'edges':st.session_state.edges}
            if json.dumps(incoming,sort_keys=True) != json.dumps(current,sort_keys=True):
                st.session_state.nodes,st.session_state.edges=incoming['nodes'],incoming['edges']
                st.session_state.results=None
                st.rerun()
        issues=validate_topology(st.session_state.nodes,st.session_state.edges)
        ca,cb=st.columns(2)
        if ca.button('Auto-layout network',use_container_width=True):
            st.session_state.nodes=auto_layout(st.session_state.nodes,st.session_state.edges); st.session_state.results=None; st.rerun()
        cb.metric('Topology issues',len(issues))
        if issues:
            with st.expander('Topology validation',expanded=any(i['severity']=='error' for i in issues)):
                for i in issues: st.write(('🔴' if i['severity']=='error' else '🟠'),i['message'])
        st.caption('v8 canvas remains bidirectional: add equipment from its palette, drag nodes, select objects, create links by clicking OUT then IN, delete/copy, and use canvas undo/redo. Every edit is synchronized into the Python case model.')
    with props:
        ids=[n['id'] for n in st.session_state.nodes]; edge_ids=[e['id'] for e in st.session_state.edges]
        sid=selected if selected in ids else (ids[0] if ids and selected not in edge_ids else None)
        if selected in edge_ids:
            e=next(x for x in st.session_state.edges if x['id']==selected)
            st.subheader('Selected connection')
            e['kind']=st.selectbox('Type',['pipeline','choke','control_valve','pump','compressor'],index=['pipeline','choke','control_valve','pump','compressor'].index(e.get('kind','pipeline')),key='ek'+e['id'])
            e['length_m']=unit_input(f"Length [{ul['length']}]",float(e.get('length_m',0)),length_to_display,length_from_display,'el'+e['id'],0.0,1e7)
            e['diameter_m']=unit_input(f"ID [{ul['diameter']}]",float(e.get('diameter_m',.154)),diameter_to_display,diameter_from_display,'ed'+e['id'],0.001,5.0,fmt='%.4f')
            ep=e.setdefault('params',{}); ep['temperature_c']=unit_input(f"Inlet temperature [{ul['temperature']}]",float(ep.get('temperature_c',50)),temperature_to_display,temperature_from_display,'et'+e['id'],-20.,250.); ep['ambient_temperature_c']=unit_input(f"Ambient temperature [{ul['temperature']}]",float(ep.get('ambient_temperature_c',4)),temperature_to_display,temperature_from_display,'ea'+e['id'],-50.,100.); ep['overall_u_w_m2k']=unit_input('Overall U [W/m²/K]' if st.session_state.unit_profile=='norwegian_si' else 'Overall U [Btu/h/ft²/°F]',float(ep.get('overall_u_w_m2k',5)),heat_transfer_u_to_display,heat_transfer_u_from_display,'eu'+e['id'],0.,500.); ep['wax_appearance_temperature_c']=unit_input(f"Wax appearance temperature [{ul['temperature']}]",float(ep.get('wax_appearance_temperature_c',25)),temperature_to_display,temperature_from_display,'ew'+e['id'],-20.,120.); ep['erosion_c_factor']=st.number_input('API-14E erosion C-factor',1.0,500.0,float(ep.get('erosion_c_factor',100.0)),key='ec'+e['id'])
        if sid:
            n=next(x for x in st.session_state.nodes if x['id']==sid); n['name']=st.text_input('Name',n['name'],key='nm'+sid)
            if n['kind'] in ('sink','separator','separator_stage','oil_export','gas_export','water_disposal','water_source','gas_source'): n['pressure_bar']=unit_input(f"Boundary pressure [{ul['pressure']}]",float(n.get('pressure_bar') or 35),pressure_to_display,pressure_from_display,'bp'+sid,0.1,1000.0)
            if n['kind']=='well':
                p=n['params']; p['reservoir_pressure_bar']=unit_input(f"Reservoir pressure [{ul['pressure']}]",float(p.get('reservoir_pressure_bar',220)),pressure_to_display,pressure_from_display,'pr'+sid,1.,1500.); p['ipr_model']=st.selectbox('IPR',['PI','Vogel'],index=0 if p.get('ipr_model')=='PI' else 1,key='im'+sid)
                if p['ipr_model']=='PI': p['pi_m3d_bar']=unit_input('PI [m³/d/bar]' if st.session_state.unit_profile=='norwegian_si' else 'PI [stb/d/psi]',float(p.get('pi_m3d_bar',10)),pi_to_display,pi_from_display,'pi'+sid,0.001,10000.)
                else: p['qmax_m3d']=unit_input(f"Vogel qmax [{ul['liquid_rate']}]",float(p.get('qmax_m3d',1500)),liquid_rate_to_display,liquid_rate_from_display,'qm'+sid,0.1,1e7)
                p['depth_m']=unit_input(f"TVD [{ul['length']}]",float(p.get('depth_m',2000)),length_to_display,length_from_display,'de'+sid,1.,10000.); p['tubing_id_m']=unit_input(f"Tubing ID [{ul['diameter']}]",float(p.get('tubing_id_m',.0889)),diameter_to_display,diameter_from_display,'ti'+sid,0.01,1.,fmt='%.4f')
                p['water_cut']=st.slider('Water cut',0.,0.99,float(p.get('water_cut',.2)),key='wc'+sid); p['gor_sm3sm3']=unit_input(f"Producing GOR [{ul['gor']}]",float(p.get('gor_sm3sm3',100)),gor_to_display,gor_from_display,'go'+sid,0.,5000.); p['temperature_c']=unit_input(f"Tubing temperature [{ul['temperature']}]",float(p.get('temperature_c',70)),temperature_to_display,temperature_from_display,'te'+sid,-10.,250.)
                p['skin']=st.number_input('Completion skin [-]',-0.95,50.0,float(p.get('skin',0.0)),key='sk'+sid); p['vlp_model']=st.selectbox('VLP model',['Beggs-Brill','Homogeneous'],index=0 if p.get('vlp_model',p.get('correlation','Beggs-Brill'))=='Beggs-Brill' else 1,key='vm'+sid); p['lift_type']=st.selectbox('Artificial lift',['none','ESP','gas_lift'],index=['none','ESP','gas_lift'].index(p.get('lift_type','none')),key='lt'+sid); p['lift_assist_bar']=unit_input(f"Legacy/manual lift assistance [{ul['pressure']}]",float(p.get('lift_assist_bar',0.0)),pressure_to_display,pressure_from_display,'la'+sid,0.,150.)
                if p['lift_type']=='gas_lift': p['gas_lift_injection_sm3d']=st.number_input('Gas-lift injection [Sm³/d]',0.0,500000.0,float(p.get('gas_lift_injection_sm3d',30000.0)),key='gli'+sid)
                if p['lift_type']=='ESP':
                    p['esp_rated_rate_m3d']=unit_input(f"ESP rated liquid rate [{ul['liquid_rate']}]",float(p.get('esp_rated_rate_m3d',1000.0)),liquid_rate_to_display,liquid_rate_from_display,'er'+sid,1.,1e7); p['esp_shutoff_head_bar']=unit_input(f"ESP shutoff head [{ul['pressure']}]",float(p.get('esp_shutoff_head_bar',80.0)),pressure_to_display,pressure_from_display,'eh'+sid,1.,500.); p['esp_speed_fraction']=st.number_input('ESP speed fraction',0.5,1.5,float(p.get('esp_speed_fraction',1.0)),key='es'+sid)
            if st.button('Delete selected node'): st.session_state.nodes=[x for x in st.session_state.nodes if x['id']!=sid]; st.session_state.edges=[e for e in st.session_state.edges if e['source']!=sid and e['target']!=sid]; st.rerun()
    st.subheader('Flowlines / pipelines')
    if st.session_state.edges:
        rows=[]
        for e in st.session_state.edges: rows.append({**{k:e[k] for k in ['id','source','target','length_m','diameter_m','roughness_m','elevation_change_m']},**{k:e.get('params',{}).get(k) for k in ['temperature_c','water_cut','gor_sm3sm3']}})
        ed=st.data_editor(pd.DataFrame(rows),hide_index=True,use_container_width=True,disabled=['id','source','target'])
        for row in ed.to_dict('records'):
            e=next(x for x in st.session_state.edges if x['id']==row['id']);
            for k in ['length_m','diameter_m','roughness_m','elevation_change_m']: e[k]=row[k]
            for k in ['temperature_c','water_cut','gor_sm3sm3']: e.setdefault('params',{})[k]=row[k]
    c1,c2=st.columns(2)
    if c1.button('▶ Solve network',type='primary',use_container_width=True):
        try: st.session_state.results=solve_v21(st.session_state.nodes,st.session_state.edges,warm_start=st.session_state.get('v21_warm_start'),attempts=3); st.session_state.v21_warm_start={'pressures':st.session_state.results[0],'flows':st.session_state.results[1]}
        except Exception as exc: st.error(f'Solver error: {exc}')
    payload=json.dumps({'version':'29','application':'FieldNet v29','storage_units':'canonical','display_unit_profile':st.session_state.unit_profile,'standard_conditions':STANDARD_CONDITIONS,'nodes':st.session_state.nodes,'edges':st.session_state.edges},indent=2); c2.download_button('Export case JSON',payload,'fieldnet_v29_case.json','application/json',use_container_width=True)
    uploaded=st.file_uploader('Load FieldNet project JSON',type=['json'],key='project_upload')
    if uploaded is not None and st.button('Load project',use_container_width=True):
        try:
            nn,ee=normalize_project(json.load(uploaded)); st.session_state.nodes,st.session_state.edges=nn,ee; st.session_state.results=None; st.success('Project loaded'); st.rerun()
        except Exception as exc: st.error(f'Invalid project: {exc}')

with tab_nodal:
    wells=[n for n in st.session_state.nodes if n['kind']=='well']
    if wells:
        w=st.selectbox('Well',wells,format_func=lambda x:x['name']); prm=w['params']; whp=unit_input(f"Wellhead pressure for nodal curve [{ul['pressure']}]",float(w.get('pressure_bar') or prm.get('initial_pressure_bar',80)),pressure_to_display,pressure_from_display,'nodal_whp',1.,500.)
        c1,c2,c3=st.columns(3); vlp_model=c1.selectbox('VLP correlation',['Beggs-Brill','Homogeneous'],index=0 if prm.get('vlp_model',prm.get('correlation','Beggs-Brill'))=='Beggs-Brill' else 1,key='nodal_vlp'); lift_type=c2.selectbox('Lift case',['none','gas_lift','ESP'],index=['none','gas_lift','ESP'].index(prm.get('lift_type','none')),key='nodal_lift'); c3.metric('Completion skin',f"{float(prm.get('skin',0)):.2f}")
        gl=float(prm.get('gas_lift_injection_sm3d',30000.0)); esp={'rated_rate_m3d':float(prm.get('esp_rated_rate_m3d',1000.0)),'shutoff_head_bar':float(prm.get('esp_shutoff_head_bar',80.0)),'speed_fraction':float(prm.get('esp_speed_fraction',1.0))}
        if lift_type=='gas_lift': gl=st.number_input('Gas-lift injection [Sm³/d]',0.0,500000.0,gl,key='nodal_gl')
        if lift_type=='ESP':
            cc1,cc2=st.columns(2); esp['rated_rate_m3d']=unit_input(f"ESP rated rate [{ul['liquid_rate']}]",esp['rated_rate_m3d'],liquid_rate_to_display,liquid_rate_from_display,'nodal_er',1.,1e7); esp['shutoff_head_bar']=unit_input(f"ESP shutoff head [{ul['pressure']}]",esp['shutoff_head_bar'],pressure_to_display,pressure_from_display,'nodal_eh',1.,500.)
        common=dict(ipr_model=prm.get('ipr_model','PI'),pi_m3d_bar=skin_adjusted_pi(prm.get('pi_m3d_bar',10),prm.get('skin',0)),qmax_m3d=prm.get('qmax_m3d',1500),vlp_model=vlp_model,lift_type=lift_type,gas_injection_sm3d=gl,esp=esp,lift_assist_bar=0.0,roughness_m=prm.get('tubing_roughness_m',4.5e-5),temperature_c=prm.get('temperature_c',70),water_cut=prm.get('water_cut',.2),gor_sm3sm3=prm.get('gor_sm3sm3',100),api=prm.get('api',35),gas_sg=prm.get('gas_sg',.75))
        op=nodal_operating_point(prm['reservoir_pressure_bar'],whp,prm.get('depth_m',2000),prm.get('tubing_id_m',.0889),**common)
        curve=pd.DataFrame(op['curve']); curve['Rate']=curve['rate_m3d'].map(lambda x: liquid_rate_to_display(x,st.session_state.unit_profile)); curve['IPR']=curve['ipr_bhp_bar'].map(lambda x: pressure_to_display(x,st.session_state.unit_profile)); curve['VLP']=curve['vlp_bhp_bar'].map(lambda x: pressure_to_display(x,st.session_state.unit_profile)); fig=go.Figure(); fig.add_scatter(x=curve['Rate'],y=curve['IPR'],name='IPR'); fig.add_scatter(x=curve['Rate'],y=curve['VLP'],name=f'VLP — {vlp_model}'); fig.update_layout(xaxis_title=f"Liquid rate [{ul['liquid_rate']}]",yaxis_title=f"Bottomhole pressure [{ul['pressure']}]",title=f"{w['name']} — v20 nodal analysis"); st.plotly_chart(fig,use_container_width=True)
        if op['converged']:
            qa=well_performance_qa(op,prm['reservoir_pressure_bar']); a,b,c=st.columns(3); a.metric('Operating liquid rate',f"{liquid_rate_to_display(op['rate_m3d'],st.session_state.unit_profile):.1f} {ul['liquid_rate']}"); b.metric('Operating BHP',f"{pressure_to_display(op['bhp_bar'],st.session_state.unit_profile):.1f} {ul['pressure']}"); c.metric('Well QA','PASS' if qa['acceptable'] else 'CHECK');
            if qa['warnings']: st.warning(', '.join(qa['warnings']))
            if lift_type=='ESP':
                ep=esp_performance(op['rate_m3d'],**esp); st.write({'ESP head':f"{pressure_to_display(ep['head_bar'],st.session_state.unit_profile):.1f} {ul['pressure']}",'Power':f"{ep['hydraulic_power_kw']:.1f} kW",'Within recommended rate envelope':ep['within_rate_envelope'],'NPSH check':ep['npsh_ok']})
        else: st.error('No stable nodal intersection found for the selected case.')
        if st.button('Screen gas-lift allocation for this well'):
            glr=optimize_gas_lift(prm['reservoir_pressure_bar'],whp,prm.get('depth_m',2000),prm.get('tubing_id_m',.0889),ipr_model=prm.get('ipr_model','PI'),pi_m3d_bar=skin_adjusted_pi(prm.get('pi_m3d_bar',10),prm.get('skin',0)),qmax_m3d=prm.get('qmax_m3d',1500),roughness_m=prm.get('tubing_roughness_m',4.5e-5),temperature_c=prm.get('temperature_c',70),water_cut=prm.get('water_cut',.2),gor_sm3sm3=prm.get('gor_sm3sm3',100),api=prm.get('api',35),gas_sg=prm.get('gas_sg',.75)); st.dataframe(pd.DataFrame(glr['candidates']),hide_index=True,use_container_width=True); st.caption(glr['limitations'][0])
        st.caption('v20 artificial-lift outputs are screening calculations. Use calibrated VLP and vendor ESP/gas-lift design models before equipment selection or operating decisions.')
    else: st.info('Add a well to run nodal analysis.')


with tab_diag:
    pipes=[e for e in st.session_state.edges if e.get('kind','pipeline')=='pipeline']
    if not st.session_state.results: st.info('Solve the network first to generate pressure profiles.')
    elif pipes:
        e=st.selectbox('Pipeline',pipes,format_func=lambda x:x['id']); p,q,info,d=st.session_state.results; prm=e.get('params',{}); prof=pressure_profile(q[e['id']],float(e['length_m']),float(e['diameter_m']),float(e['roughness_m']),float(e.get('elevation_change_m',0)),p[e['source']],float(prm.get('temperature_c',50)),float(prm.get('water_cut',.2)),float(prm.get('gor_sm3sm3',100)),float(prm.get('api',35)),float(prm.get('gas_sg',.75)))
        xcol=f"Distance [{ul['length']}]"; ppcol=f"Pressure [{ul['pressure']}]"; dfp=pd.DataFrame({xcol:[length_to_display(x,st.session_state.unit_profile) for x in prof['distance_m']],ppcol:[pressure_to_display(x,st.session_state.unit_profile) for x in prof['pressure_bar']]}); st.plotly_chart(px.line(dfp,x=xcol,y=ppcol,title=f"{e['id']} pressure profile"),use_container_width=True)
        st.write('Calculated outlet pressure:',f"{pressure_to_display(prof['pressure_bar'][-1],st.session_state.unit_profile):.2f} {ul['pressure']}",' | Network node:',f"{pressure_to_display(p[e['target']],st.session_state.unit_profile):.2f} {ul['pressure']}")
        if prof['regime']: st.dataframe(pd.DataFrame({'Segment':range(1,len(prof['regime'])+1),'Liquid holdup':prof['holdup'],'Flow regime':prof['regime']}),hide_index=True,use_container_width=True)

with tab_fa:
    st.subheader('v19 Flow assurance')
    st.caption('Post-solve screening layer. Thermal, hydrate, wax, erosion, liquid-loading and slugging indicators do not alter hydraulic convergence and are not substitutes for compositional or transient flow-assurance simulation.')
    if not st.session_state.results:
        st.info('Solve the network first to evaluate flow assurance.')
    else:
        fa=network_flow_assurance(st.session_state.nodes,st.session_state.edges,st.session_state.results,segments=20)
        rc=fa['risk_counts']; cols=st.columns(5)
        for c,(k,label) in zip(cols,[('hydrate_risk','Hydrate'),('wax_risk','Wax'),('erosion_risk','Erosion'),('liquid_loading_risk','Liquid loading'),('slugging_indicator','Slugging indicator')]): c.metric(label,rc[k])
        pipes=fa['pipelines']
        if not pipes: st.info('No solved pipeline edges available.')
        else:
            choice=st.selectbox('Pipeline flow-assurance report',pipes,format_func=lambda r:r['edge_id'],key='fa_pipe')
            a,b,c,d=st.columns(4); a.metric('Outlet temperature',f"{temperature_to_display(choice['outlet_temperature_c'],st.session_state.unit_profile):.1f} {ul['temperature']}"); b.metric('Min hydrate margin',f"{choice['minimum_hydrate_margin_c']:.1f} °C"); c.metric('Max erosion ratio',f"{choice['maximum_erosion_ratio']:.2f}"); d.metric('Min loading ratio',f"{choice['minimum_liquid_loading_ratio']:.2f}")
            rows=[]
            for r in choice['segments']:
                rows.append({'Segment':r['segment'],f"Distance [{ul['length']}]":length_to_display(r['x1_m'],st.session_state.unit_profile),f"Pressure [{ul['pressure']}]":pressure_to_display(r['p_out_bar'],st.session_state.unit_profile),f"Temperature [{ul['temperature']}]":temperature_to_display(r['temperature_out_c'],st.session_state.unit_profile),'Hydrate margin [°C]':r['hydrate_margin_c'],'Wax margin [°C]':r['wax_margin_c'],('Velocity [m/s]' if st.session_state.unit_profile=='norwegian_si' else 'Velocity [ft/s]'):velocity_to_display(r['mixture_velocity_ms'],st.session_state.unit_profile),'Erosion ratio':r['erosion_ratio'],'Loading ratio':r['liquid_loading_ratio'],'Regime':r['flow_regime'],'Slug indicator':r['slugging_indicator']})
            fdf=pd.DataFrame(rows); st.dataframe(fdf,hide_index=True,use_container_width=True)
            st.plotly_chart(px.line(fdf,x=f"Distance [{ul['length']}]",y=f"Temperature [{ul['temperature']}]",title='Thermal profile'),use_container_width=True)
            with st.expander('Model limitations / interpretation'):
                for item in choice['limitations']: st.write('•',item)
            st.download_button('Export flow-assurance JSON',json.dumps(fa,indent=2),'fieldnet_v19_flow_assurance.json','application/json',use_container_width=True)

with tab_results:
    if not st.session_state.results: st.info('Solve the network to populate results.')
    else:
        p,q,info,d=st.session_state.results; a,b,c,dcol=st.columns(4); a.metric('Converged','Yes' if info['success'] else 'No'); b.metric('Max residual',f"{info['max_abs_residual']:.2e}"); c.metric('Nodes',len(p)); dcol.metric('Flowlines',len(q))
        if info['max_abs_residual']>1e-4: st.warning('Residual tolerance not met. Review topology, boundary conditions, or initial guesses.')
        with st.expander('Solver diagnostics'):
            for item in solver_diagnostics(info,p,q): st.write(item['severity'].upper(), item['code'], '—', item['message'])
        with st.expander('v21 Solver 2.0 debugger',expanded=info.get('quality_gate')=='FAIL'):
            st.write('Mode:',info.get('solver_mode','legacy')); st.write('Quality gate:',info.get('quality_gate','N/A')); st.write('Function evaluations:',info.get('nfev','N/A')); st.write('Jacobian condition:',info.get('jacobian_condition','N/A')); st.write('Warm start used:',info.get('warm_start_used',False))
            if info.get('attempt_history'): st.dataframe(pd.DataFrame(info['attempt_history']),hide_index=True,use_container_width=True)
            if info.get('debug'): st.dataframe(pd.DataFrame(info['debug']),hide_index=True,use_container_width=True)
            audit=info.get('physical_residual_audit',{})
            if audit.get('edge_residuals'): st.caption('Pressure-equation residuals [bar]'); st.dataframe(pd.DataFrame(audit['edge_residuals']),hide_index=True,use_container_width=True)
            if audit.get('node_residuals'): st.caption('Node mass-balance residuals [m³/d]'); st.dataframe(pd.DataFrame(audit['node_residuals']),hide_index=True,use_container_width=True)
        pcol=f"Pressure [{ul['pressure']}]"; qcol=f"Liquid rate [{ul['liquid_rate']}]"; rdf=pd.DataFrame([{'Component':next(n['name'] for n in st.session_state.nodes if n['id']==nid),pcol:pressure_to_display(v,st.session_state.unit_profile)} for nid,v in p.items()]); qdf=pd.DataFrame([{'Flowline':eid,qcol:liquid_rate_to_display(v,st.session_state.unit_profile)} for eid,v in q.items()]); wdf=pd.DataFrame([{'Well':next(n['name'] for n in st.session_state.nodes if n['id']==nid),**v} for nid,v in d.items()])
        st.dataframe(wdf,use_container_width=True,hide_index=True); l,r=st.columns(2); l.plotly_chart(px.bar(rdf,x='Component',y=pcol,title='Node pressures'),use_container_width=True); r.plotly_chart(px.bar(qdf,x='Flowline',y=qcol,title='Flowline rates'),use_container_width=True)

with tab_constraints:
    if not st.session_state.results:
        st.info('Solve the network first to evaluate equipment and operating constraints.')
    else:
        p,q,info,d=st.session_state.results
        c1,c2,c3=st.columns(3); c1.metric('Constraint checks',len(info.get('constraints',[]))); c2.metric('Violations',info.get('violations',0)); c3.metric('Equipment items',len(info.get('equipment',[])))
        if info.get('violations',0): st.error(f"{info['violations']} operating constraint(s) violated. Hydraulic convergence does not imply an operable case.")
        elif info.get('constraints'): st.success('All configured operating constraints are satisfied.')
        if info.get('constraints'): st.dataframe(pd.DataFrame(info['constraints']),hide_index=True,use_container_width=True)
        else: st.caption('No explicit operating constraints are configured in this case. Add limits in JSON or component parameters for evaluation.')
        if info.get('equipment'):
            st.subheader('Rotating equipment')
            st.dataframe(pd.DataFrame(info['equipment']),hide_index=True,use_container_width=True)

with tab_ops:
    st.subheader('v21 Network Solver 2.0 & debottlenecking')
    st.caption('Solver 2.0 adds topology prechecks, Jacobian variable scaling, warm starts, retry orchestration, physical residual reconstruction and equation-level failure diagnostics while retaining the validated production-physics kernel.')
    if st.button('Run v21 Solver 2.0',use_container_width=True):
        st.session_state.results=solve_v21(st.session_state.nodes,st.session_state.edges,warm_start=st.session_state.get('v21_warm_start'),attempts=3); st.session_state.v21_warm_start={'pressures':st.session_state.results[0],'flows':st.session_state.results[1]}
    if st.session_state.results:
        _p,_q,_i,_d=st.session_state.results
        a,b,c=st.columns(3); a.metric('Quality gate',_i.get('quality_gate','N/A')); b.metric('Normalized residual',f"{_i.get('normalized_residual_score',0):.3g}"); c.metric('Active constraints',len(_i.get('active_constraints',[])))
        if st.button('Screen +10% capacity debottlenecks',use_container_width=True): st.session_state.v14_debottleneck=debottleneck_screen(st.session_state.nodes,st.session_state.edges,0.10)
        if st.session_state.get('v14_debottleneck'): st.dataframe(pd.DataFrame(st.session_state.v14_debottleneck),hide_index=True,use_container_width=True)
        audit=calculation_audit(st.session_state.nodes,st.session_state.edges,_i,_d)
        st.download_button('Download calculation audit JSON',json.dumps(audit,indent=2),'fieldnet_v14_audit.json','application/json',use_container_width=True)
    st.divider()
    st.subheader('v22 Integrated production optimization')
    st.caption('Optimizes solver-active controls jointly against network and facility constraints. Feasibility is reported separately from production objective; global optimality is not guaranteed.')
    oc1,oc2=st.columns(2); opt_iter=oc1.slider('Optimization iterations',1,40,8); opt_seed=oc2.number_input('Optimization seed',0,999999,22)
    if st.button('Optimize integrated production', type='primary'):
        with st.spinner('Optimizing wells and solver-active equipment controls...'):
            st.session_state.opt_v22=optimize_integrated(st.session_state.nodes,st.session_state.edges,maxiter=int(opt_iter),seed=int(opt_seed))
    if st.session_state.get('opt_v22'):
        o=st.session_state.opt_v22; m1,m2,m3=st.columns(3); m1.metric('Optimized liquid',f"{o.get('best_rate_m3d',0):.1f} m³/d"); m2.metric('Gain',f"{o.get('production_gain_m3d',0):+.1f} m³/d"); m3.metric('Feasible','YES' if o.get('feasible') else 'NO')
        if o.get('decisions'):
            rows=[]
            for d in o['decisions']: rows.append({'Component':d['component_name'],'Control':d['kind'],'Lower':d['lower'],'Optimized':o['controls'].get(d['key']),'Upper':d['upper']})
            st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)
        if o.get('unsupported'):
            for msg in o['unsupported']: st.warning(msg)
        st.caption(f"Method: {o.get('method','')} · Global optimum guaranteed: {o.get('global_optimum_guaranteed',False)} · Seed: {o.get('seed')}")
        if o.get('best',{}).get('violations'): st.dataframe(pd.DataFrame(o['best']['violations']),hide_index=True,use_container_width=True)
        st.download_button('Download v22 optimization JSON',json.dumps({k:v for k,v in o.items() if k!='best'},indent=2),'fieldnet_v22_optimization.json','application/json',use_container_width=True)
    st.divider(); st.subheader('One-variable sensitivity')
    candidates=[n for n in st.session_state.nodes if n['kind'] in ('sink','separator','separator_stage','oil_export','gas_export','water_disposal')]
    if candidates:
        sn=st.selectbox('Boundary component',candidates,format_func=lambda x:x['name'],key='sensnode')
        lo=st.number_input('Start pressure [bar]',0.1,1000.0,20.0); hi=st.number_input('End pressure [bar]',0.1,1000.0,60.0); steps=st.slider('Cases',3,15,7)
        if st.button('Run pressure sensitivity'):
            st.session_state.sens=run_sensitivity(st.session_state.nodes,st.session_state.edges,'node',sn['id'],'pressure_bar',np.linspace(lo,hi,steps))
        if st.session_state.get('sens'):
            sdf=pd.DataFrame(st.session_state.sens); st.dataframe(sdf,hide_index=True,use_container_width=True); st.plotly_chart(px.line(sdf,x='Value',y='Total liquid [m3/d]',markers=True,title='Production sensitivity to boundary pressure'),use_container_width=True)


with tab_cal:
    st.subheader('v23 Calibration & history matching')
    st.caption('Bounded weighted least-squares against measured node pressures and edge liquid rates. Fit quality does not imply parameter uniqueness or physical correctness.')
    rows=[]
    for n in st.session_state.nodes:
        if n.get('pressure_bar') is not None: rows.append({'enabled':False,'kind':'node_pressure_bar','target_id':n['id'],'name':n.get('name',n['id']),'value':float(n.get('pressure_bar') or 0.0),'sigma':1.0})
    for e in st.session_state.edges: rows.append({'enabled':False,'kind':'edge_rate_m3d','target_id':e['id'],'name':e.get('name',e['id']),'value':float(e.get('params',{}).get('initial_rate_m3d',500.0)),'sigma':10.0})
    obsdf=st.data_editor(pd.DataFrame(rows),use_container_width=True,key='v23_obs')
    maxeval=st.slider('Maximum calibration evaluations',5,150,40,key='v23_maxeval')
    if st.button('Run v23 calibration',use_container_width=True):
        obs=[Observation(r.kind,r.target_id,float(r.value),float(r.sigma),str(r.name)) for r in obsdf.itertuples() if bool(r.enabled)]
        try: st.session_state.cal_v23=calibrate(st.session_state.nodes,st.session_state.edges,obs,max_nfev=maxeval)
        except Exception as exc: st.error(str(exc))
    if st.session_state.get('cal_v23'):
        c=st.session_state.cal_v23; a,b,d=st.columns(3); a.metric('Weighted RMSE',f"{c['weighted_rmse']:.3f}"); b.metric('Jacobian rank',str(c['jacobian_rank'])); d.metric('Locally identifiable','YES' if c['identifiable_linearized'] else 'NO')
        st.dataframe(pd.DataFrame([{'parameter':k,'value':v,'std':(c.get('parameter_std') or {}).get(k),'at_bound':c['at_bounds'].get(k)} for k,v in c['values'].items()]),use_container_width=True)
        st.dataframe(pd.DataFrame([{'measurement':o.get('name') or o['target_id'],'kind':o['kind'],'observed':o['value'],'predicted':p,'normalized_residual':r} for o,p,r in zip(c['observations'],c['predicted'],c['normalized_residuals'])]),use_container_width=True)
        export={k:v for k,v in c.items() if k not in ('calibrated_nodes','calibrated_edges','solver_info')}
        st.download_button('Download v23 calibration JSON',json.dumps(export,indent=2),'fieldnet_v23_calibration.json','application/json',use_container_width=True)

with tab_forecast:
    st.subheader('Life-of-field forecast')
    st.caption('Quasi-steady-state forecast: reservoir/well/facility state is updated at each timestep and the full network is re-solved. This is not a transient reservoir simulator.')
    a,b,c=st.columns(3)
    start=a.date_input('Forecast start').isoformat(); years=b.number_input('Years',0.1,50.0,5.0,0.5); step=c.selectbox('Timestep [days]',[7,14,30,60,90],index=2)
    wells=[n for n in st.session_state.nodes if n['kind']=='well']
    dep={}
    with st.expander('Reservoir depletion assumptions'):
        for w in wells:
            c1,c2=st.columns(2); decline=c1.number_input(f"{w['name']} pressure decline [bar/1000 m³]",0.0,10.0,float(w.get('params',{}).get('pressure_decline_bar_per_1000m3',0.03)),0.01,key='dec'+w['id']); support=c2.number_input(f"{w['name']} pressure support [bar/day]",0.0,5.0,float(w.get('params',{}).get('pressure_support_bar_per_day',0.0)),0.001,key='sup'+w['id']); dep[w['id']]={'pressure_decline_bar_per_1000m3':decline,'pressure_support_bar_per_day':support}
    st.markdown('**Schedule / intervention events**')
    default_events=pd.DataFrame(columns=['date','target_id','field','value'])
    evdf=st.data_editor(default_events,num_rows='dynamic',use_container_width=True,key='forecast_events')
    if st.button('▶ Run life-of-field simulation',type='primary',use_container_width=True):
        events=[]
        for row in evdf.to_dict('records'):
            if row.get('date') and row.get('target_id') and row.get('field'):
                v=row.get('value');
                if isinstance(v,str) and v.lower() in ('true','false'): v=v.lower()=='true'
                events.append({'date':str(row['date']),'target_id':str(row['target_id']),'field':str(row['field']),'value':v})
        with st.spinner('Resolving network through forecast timesteps...'):
            st.session_state.forecast=run_forecast(st.session_state.nodes,st.session_state.edges,start,float(years),int(step),events,dep)
    if st.session_state.get('forecast'):
        fc=st.session_state.forecast; fdf=pd.DataFrame(fc['field']); wdf=pd.DataFrame(fc['wells']); cdf=pd.DataFrame(fc['constraints'])
        m1,m2,m3,m4=st.columns(4); m1.metric('Final liquid',f"{fdf.iloc[-1]['Total liquid [m3/d]']:.0f} m³/d"); m2.metric('Cumulative liquid',f"{fdf.iloc[-1]['Cumulative liquid [m3]']/1e6:.2f} MMm³"); m3.metric('Final oil',f"{fdf.iloc[-1]['Oil [m3/d]']:.0f} m³/d"); m4.metric('Constraint events',int((fdf['Violations']>0).sum()))
        st.plotly_chart(px.line(fdf,x='Date',y=['Oil [m3/d]','Water [m3/d]','Total liquid [m3/d]'],title='Field production profile'),use_container_width=True)
        st.plotly_chart(px.line(fdf,x='Date',y='Gas [Sm3/d]',title='Field gas profile'),use_container_width=True)
        if not wdf.empty:
            st.plotly_chart(px.line(wdf,x='Date',y='Liquid [m3/d]',color='Well',title='Well liquid profiles'),use_container_width=True)
            st.plotly_chart(px.line(wdf,x='Date',y='Reservoir pressure [bar]',color='Well',title='Reservoir pressure depletion'),use_container_width=True)
        st.dataframe(fdf,hide_index=True,use_container_width=True)
        if not cdf.empty:
            with st.expander('Constraint history'): st.dataframe(cdf,hide_index=True,use_container_width=True)
        st.download_button('Download field forecast CSV',fdf.to_csv(index=False),'fieldnet_v12_forecast.csv','text/csv',use_container_width=True)
        st.divider(); st.subheader('Scenario & uncertainty planning')
        st.caption('Compare depletion uncertainty using the same network and schedule. Low/Base/High refer to reservoir-pressure-decline assumptions, not probabilistic P10/P50/P90 reserves.')
        if st.button('Run 3-case depletion uncertainty',use_container_width=True):
            cases=uncertainty_cases(dep)
            with st.spinner('Running forecast scenarios...'):
                st.session_state.v10_scenarios=run_scenarios(st.session_state.nodes,st.session_state.edges,start,float(years),int(step),cases)
        if st.session_state.get('v10_scenarios'):
            frames=[pd.DataFrame(x['forecast']['field']) for x in st.session_state.v10_scenarios]
            comp=pd.concat(frames,ignore_index=True)
            st.plotly_chart(px.line(comp,x='Date',y='Oil [m3/d]',color='Scenario',title='Scenario oil comparison'),use_container_width=True)
            st.plotly_chart(px.line(comp,x='Date',y='Total liquid [m3/d]',color='Scenario',title='Scenario liquid comparison'),use_container_width=True)



with tab_development:
    render_field_development(st, st.session_state.nodes, st.session_state.edges)


with tab_dev26:
    render_development_v26(st, st.session_state.nodes, st.session_state.edges)

with tab_uncertainty:
    render_uncertainty(st, st.session_state.nodes, st.session_state.edges)


with tab_rel:
    st.subheader('v24 Reliability & availability')
    st.caption('Screening reliability Monte Carlo. Failure/repair availability is separate from hydraulic convergence; exponential MTBF/MTTR assumptions should be replaced with asset data when available.')
    candidates=[x for x in [*st.session_state.nodes,*st.session_state.edges] if x.get('kind') in ('well','pump','compressor','separator','separator_stage','pipeline')]
    rows=[{'enabled':False,'target_id':x['id'],'name':x.get('name',x['id']),'kind':x.get('kind'),'mtbf_days':365.0,'mttr_days':3.0,'redundancy_group':'','required_online':1} for x in candidates]
    rdf=st.data_editor(pd.DataFrame(rows),use_container_width=True,key='v24_rel_specs')
    a,b,c,d=st.columns(4); ryears=a.number_input('Reliability years',0.1,30.0,1.0,0.5); rstep=b.selectbox('Reliability step [days]',[1,7,14,30]); rn=c.number_input('Realizations',10,5000,200,10); rseed=d.number_input('Seed',0,999999,2401,1)
    base_rate=st.number_input('Reference production [m³/d]',0.0,1e7,1000.0,100.0)
    if st.button('Run v24 reliability study',type='primary',use_container_width=True):
        specs=[ReliabilitySpec(str(r.target_id),float(r.mtbf_days),float(r.mttr_days),(),str(r.redundancy_group),int(r.required_online)) for r in rdf.itertuples() if bool(r.enabled)]
        try: st.session_state.rel_v24=run_reliability(ReliabilityStudy(float(ryears),int(rstep),int(rn),int(rseed),specs),float(base_rate))
        except Exception as exc: st.error(str(exc))
    if st.session_state.get('rel_v24'):
        rr=st.session_state.rel_v24; sm=rr['summary']; a,b,c,d=st.columns(4); a.metric('Mean availability',f"{sm['mean_availability']:.1%}"); b.metric('P90 availability',f"{sm['p90_availability']:.1%}"); c.metric('Mean deferred',f"{sm['mean_deferred_m3']:,.0f} m³"); d.metric('P(A<90%)',f"{sm['probability_below_90pct_availability']:.1%}")
        rrf=pd.DataFrame(rr['realizations']); st.plotly_chart(px.histogram(rrf,x='availability',title='Availability distribution'),use_container_width=True); st.dataframe(rrf,hide_index=True,use_container_width=True)
        st.download_button('Download v24 reliability JSON',json.dumps(rr,indent=2),'fieldnet_v24_reliability.json','application/json',use_container_width=True)

with tab_res25:
    st.subheader('v25 Reservoir–Network Coupling 2.0')
    st.caption('Reduced-order quasi-steady material balance coupled to the production network. Communicating tanks, aquifer influx and injector connectivity are planning models—not a 3-D reservoir simulator.')
    wells25=[n for n in st.session_state.nodes if n.get('kind')=='well']
    default_tanks=[]
    for i,w in enumerate(wells25):
        rp=float(w.get('params',{}).get('reservoir_pressure_bar',220.0))
        default_tanks.append({'id':f'T{i+1}','name':f'Tank {i+1}','pressure_bar':rp,'pore_volume_m3':2e6,'total_compressibility_1bar':8e-5,'min_pressure_bar':20.0})
    tdf=st.data_editor(pd.DataFrame(default_tanks),num_rows='dynamic',use_container_width=True,key='v25_tanks')
    maprows=[]
    tids=[str(x) for x in tdf.get('id',pd.Series(dtype=str)).tolist() if str(x)]
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
    if st.button('Run v25 coupled forecast',type='primary',use_container_width=True):
        try:
            tanks=[]
            for r in tdf.to_dict('records'):
                if not r.get('id'): continue
                tanks.append({'id':str(r['id']),'name':str(r.get('name') or r['id']),'initial_pressure_bar':float(r['pressure_bar']),'pressure_bar':float(r['pressure_bar']),'pore_volume_m3':float(r['pore_volume_m3']),'total_compressibility_1bar':float(r['total_compressibility_1bar']),'min_pressure_bar':float(r['min_pressure_bar'])})
            mapping={str(r['well_id']):str(r['tank_id']) for r in mdf.to_dict('records') if r.get('well_id') and r.get('tank_id')}
            links=[CommunicationLink(str(r['tank_a']),str(r['tank_b']),float(r['transmissibility_m3d_bar']),float(r.get('max_transfer_m3d') or 1e30)) for r in ldf.to_dict('records') if r.get('tank_a') and r.get('tank_b')]
            aquifers=[AquiferSpec(str(r['tank_id']),float(r['productivity_m3d_bar']),float(r['reference_pressure_bar']),float(r.get('max_influx_m3d') or 1e30)) for r in adf.to_dict('records') if r.get('tank_id')]
            conns=[InjectorConnection(str(r['injector_id']),str(r['tank_id']),float(r.get('weight') or 0)) for r in cdf25.to_dict('records') if r.get('injector_id') and r.get('tank_id')]
            sched=[{'date':str(r['date']),'injector_id':str(r['injector_id']),'rate_m3d':float(r['rate_m3d'])} for r in sdf25.to_dict('records') if r.get('date') and r.get('injector_id')]
            st.session_state.res25=run_coupled_forecast_v25(st.session_state.nodes,st.session_state.edges,tanks,mapping,rstart,float(ryears),int(rstep),injector_schedule=sched,injector_connections=conns,aquifers=aquifers,communication_links=links)
        except Exception as exc: st.error(str(exc))
    if st.session_state.get('res25'):
        rr=st.session_state.res25; tf=pd.DataFrame(rr['tanks']); ff=pd.DataFrame(rr['field'])
        if not tf.empty:
            st.plotly_chart(px.line(tf,x='Date',y='pressure_after_bar',color='tank_id',title='Coupled tank pressure'),use_container_width=True)
            st.dataframe(tf,hide_index=True,use_container_width=True)
        if not ff.empty: st.plotly_chart(px.line(ff,x='Date',y='Oil [m3/d]',title='Coupled field oil'),use_container_width=True)
        if rr.get('transfers'): st.dataframe(pd.DataFrame(rr['transfers']),hide_index=True,use_container_width=True)
        st.download_button('Download v25 coupling JSON',json.dumps(rr,indent=2),'fieldnet_v25_reservoir_coupling.json','application/json',use_container_width=True)


with tab_io27:
    render_interchange_v27(st, st.session_state.nodes, st.session_state.edges)


with tab_qa28:
    st.subheader('v29 Engineering QA & Scenario Reproducibility')
    st.caption('Read-only assurance: definite invariant violations are errors; suspicious engineering values are warnings. No inputs are auto-corrected.')
    if st.button('Run v28 Model Quality Report', type='primary', use_container_width=True):
        sr=None
        if st.session_state.get('results'):
            rr=st.session_state.results
            if isinstance(rr,dict) and all(k in rr for k in ('pressures','flows','info')): sr=(rr['pressures'],rr['flows'],rr['info'])
        st.session_state.qa28=model_quality_report(st.session_state.nodes,st.session_state.edges,unit_profile=st.session_state.unit_profile,solve_result=sr,forecast=st.session_state.get('forecast'))
    if st.session_state.get('qa28'):
        qr=st.session_state.qa28; a,b,c,d=st.columns(4); a.metric('Quality gate',qr['quality_gate']); b.metric('Errors',qr['counts'].get('error',0)); c.metric('Warnings',qr['counts'].get('warning',0)); d.metric('Checks/info',qr['counts'].get('info',0))
        qdf=pd.DataFrame(qr['issues']); st.dataframe(qdf,hide_index=True,use_container_width=True)
        st.download_button('Download v28 Model Quality Report',json.dumps(qr,indent=2),'fieldnet_v28_model_quality.json','application/json',use_container_width=True)


with tab_scen29:
    render_scenario_v29(st, st.session_state.nodes, st.session_state.edges, st.session_state.unit_profile)
