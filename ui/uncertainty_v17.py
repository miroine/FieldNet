"""Streamlit uncertainty workspace and export helpers for FieldNet v29."""
from __future__ import annotations
import json
import pandas as pd
import plotly.express as px
from network.field_development import DevelopmentScenario
from network.uncertainty import UncertainParameter, MonteCarloConfig, run_monte_carlo

PARAM_COLS=['name','target_id','path','operation','distribution','low','mode','high','mean','std','physical_min','physical_max','bound_policy']
def parse_uncertainty_rows(rows):
    out=[]
    for r in rows:
        if not r.get('name') or not r.get('path'): continue
        out.append(UncertainParameter(name=str(r['name']),target_id=(str(r['target_id']) if r.get('target_id') else None),path=str(r['path']),operation=str(r.get('operation') or 'multiply'),distribution=str(r.get('distribution') or 'triangular'),low=float(r.get('low',0.8)),mode=float(r.get('mode',1.0)),high=float(r.get('high',1.2)),mean=float(r.get('mean',1.0)),std=float(r.get('std',0.1)),physical_min=(float(r['physical_min']) if r.get('physical_min') not in (None,'') else None),physical_max=(float(r['physical_max']) if r.get('physical_max') not in (None,'') else None),bound_policy=str(r.get('bound_policy') or 'clip')))
    return out

def export_mc_csv(result): return pd.DataFrame(result.get('runs',[])).to_csv(index=False)
def export_mc_json(result): return json.dumps(result,indent=2,default=str)

def render_uncertainty(st,nodes,edges):
    st.subheader('v19 Uncertainty, Monte Carlo & Risk')
    st.caption('Planning-level probabilistic wrapper around the deterministic production engine. P90 is conservative and P10 optimistic for production/reserves-style metrics. Samples are reproducible from the displayed seed.')
    c1,c2,c3,c4=st.columns(4)
    start=c1.date_input('MC start date',key='v17_start').isoformat(); years=c2.number_input('MC horizon [years]',0.03,50.0,1.0,0.5,key='v17_years'); step=c3.selectbox('MC timestep [days]',[10,30,60,90],index=1,key='v17_step'); samples=c4.number_input('Samples',5,1000,50,5,key='v17_samples')
    c5,c6=st.columns(2); seed=c5.number_input('Random seed',0,2_147_483_647,1701,1,key='v17_seed'); method=c6.selectbox('Sampling',['lhs','random'],key='v17_method')
    st.markdown('**Uncertain parameters**')
    st.caption('Use a network target ID plus a nested path such as `params.pi_m3d_bar`, `params.water_cut`, `pressure_bar`, or an edge `params.max_rate_m3d`. `multiply` treats sampled values as factors; `set` uses absolute values.')
    with st.expander('Network target IDs'):
        st.dataframe(pd.DataFrame([{'ID':x.get('id'),'Name':x.get('name',x.get('id')),'Type':x.get('kind')} for x in [*nodes,*edges]]),hide_index=True,use_container_width=True)
    default=pd.DataFrame([{'name':'PI multiplier','target_id':next((n.get('id') for n in nodes if n.get('kind')=='well'),''),'path':'params.pi_m3d_bar','operation':'multiply','distribution':'triangular','low':0.8,'mode':1.0,'high':1.2,'mean':1.0,'std':0.1,'physical_min':0.0,'physical_max':None,'bound_policy':'clip'}],columns=PARAM_COLS)
    df=st.data_editor(default,num_rows='dynamic',use_container_width=True,key='v17_params')
    if st.button('▶ Run v19 Monte Carlo',type='primary',use_container_width=True):
        try:
            pars=parse_uncertainty_rows(df.to_dict('records')); cfg=MonteCarloConfig(samples=int(samples),seed=int(seed),method=method,parameters=pars); sc=DevelopmentScenario('Monte Carlo',start,float(years),int(step))
            bar=st.progress(0.0,text='Running realizations...')
            def prog(i,n): bar.progress(i/n,text=f'Running realization {i}/{n}')
            st.session_state.v17_mc=run_monte_carlo(nodes,edges,sc,cfg,progress=prog); bar.empty()
        except Exception as exc: st.error(f'v19 uncertainty error: {exc}')
    r=st.session_state.get('v17_mc')
    if not r: return
    a,b,c,d=st.columns(4); a.metric('Successful',r['successful_samples']); b.metric('Failed',r['failed_samples']); c.metric('Success rate',f"{100*r.get('success_fraction',0):.1f}%"); d.metric('Seed',r['seed'])
    metrics=[]
    for name,s in r.get('metrics',{}).items(): metrics.append({'Metric':name,**s})
    if metrics: st.dataframe(pd.DataFrame(metrics),hide_index=True,use_container_width=True)
    fd=r.get('failure_diagnostics',{})
    if fd.get('survivor_bias_warning'): st.warning('Failed realizations may bias the surviving percentile sample. Review failure diagnostics before using P10/P50/P90.')
    with st.expander('Failure & survivor-bias diagnostics'):
        st.json(fd)
    sens=r.get('sensitivity',{}).get('cumulative_oil_m3',[])
    if sens:
        st.markdown('**Cumulative-oil sensitivity (Spearman rank correlation)**'); st.dataframe(pd.DataFrame(sens),hide_index=True,use_container_width=True)
    conv=r.get('percentile_convergence',{}).get('cumulative_oil_m3',[])
    if conv:
        cdf=pd.DataFrame(conv); st.plotly_chart(px.line(cdf,x='samples',y=['P90','P50','P10'],markers=True,title='Cumulative-oil percentile convergence'),use_container_width=True)
    runs=pd.DataFrame(r.get('runs',[])); good=runs[runs.get('success',False)==True] if not runs.empty and 'success' in runs else pd.DataFrame()
    if not good.empty and 'cumulative_oil_m3' in good:
        st.plotly_chart(px.histogram(good,x='cumulative_oil_m3',nbins=min(40,max(10,len(good)//3)),title='Cumulative oil uncertainty distribution'),use_container_width=True)
    with st.expander('Realizations'): st.dataframe(runs,hide_index=True,use_container_width=True)
    d1,d2=st.columns(2); d1.download_button('Download v19 Monte Carlo CSV',export_mc_csv(r),'fieldnet_v19_monte_carlo.csv','text/csv',use_container_width=True); d2.download_button('Download v19 Monte Carlo JSON',export_mc_json(r),'fieldnet_v19_monte_carlo.json','application/json',use_container_width=True)
