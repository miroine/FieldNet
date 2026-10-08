"""Streamlit uncertainty workspace and export helpers for FieldNet v29."""
from __future__ import annotations
import json
import pandas as pd
import plotly.express as px
from network.field_development import DevelopmentScenario
from network.uncertainty import UncertainParameter, MonteCarloConfig, run_monte_carlo, default_workers
from network.mc_job import start_job, get_job, SPEEDS
from network import risk_profiles as rp
from ui.widgets import clean_num, clean_text
from ui.run_button import run_button
from ui.graph_contract import graph_hash
from network.uncertainty_catalog import target_options, parameters_for, make_parameter, DISTRIBUTIONS

PARAM_COLS=['name','target_id','path','operation','distribution','low','mode','high','mean','std','physical_min','physical_max','bound_policy']
def parse_uncertainty_rows(rows):
    """Blank data-editor cells arrive as NaN; ``float(r.get('low',0.8))`` then yields NaN
    (the default is only used when the column is missing) and ``physical_max=NaN`` silently
    disabled every bound check. Clean every cell explicitly."""
    out=[]
    for r in rows:
        name=clean_text(r.get('name')); path=clean_text(r.get('path'))
        if not name or not path: continue
        out.append(UncertainParameter(name=name,target_id=(clean_text(r.get('target_id')) or None),path=path,operation=clean_text(r.get('operation'),'multiply'),distribution=clean_text(r.get('distribution'),'triangular'),low=clean_num(r.get('low'),0.8),mode=clean_num(r.get('mode'),1.0),high=clean_num(r.get('high'),1.2),mean=clean_num(r.get('mean'),1.0),std=clean_num(r.get('std'),0.1),physical_min=clean_num(r.get('physical_min')),physical_max=clean_num(r.get('physical_max')),bound_policy=clean_text(r.get('bound_policy'),'clip')))
    return out

def export_mc_csv(result): return pd.DataFrame(result.get('runs',[])).to_csv(index=False)
def export_mc_json(result): return json.dumps(result,indent=2,default=str)

# ---- production-profile helpers (pure; unit-tested without Streamlit) ----
PROFILE_TABS=[('Rates','Rate'),('Cumulative','Cumulative'),('Pressures','Pressure')]
def get_profiles_df(result,cache=None):
    """Rebuild (and optionally cache by result identity) the profiles DataFrame; empty frame when absent."""
    recs=(result or {}).get('profiles')
    if not recs: return rp.profiles_from_records([])
    if cache is not None and cache.get('src') is result and cache.get('df') is not None: return cache['df']
    df=rp.profiles_from_records(recs)
    if cache is not None: cache['src']=result; cache['df']=df
    return df
def profile_variable_options(df,category):
    """Variable names of a category, in display order (system/tank/node-group before individual nodes)."""
    ls=rp.list_series(df,category)
    if ls.empty: return []
    order={'System':0,'Tank':1,'Node group':2,'Node':3,'Rate':0,'Cumulative':0}
    ls=ls.assign(_o=[order.get(g,9) for g in ls['Group']]).sort_values('_o',kind='stable')
    return list(ls['Variable'])
def export_profiles_csv(result,wide=False): return rp.profiles_to_csv(get_profiles_df(result),wide=wide)
def export_profiles_json(result):
    return json.dumps({'application':result.get('application'),'seed':result.get('seed'),'convention':rp.CONVENTION_NOTE,'series_meta':result.get('series_meta',{}),'profiles':result.get('profiles',[])},default=str)

def _render_profiles(st,r):
    from ui import risk_charts
    df=get_profiles_df(r,st.session_state.setdefault('v17_prof_cache',{}))
    st.markdown('### Production profiles (total system)')
    meta=r.get('series_meta',{})
    st.caption(f"{rp.CONVENTION_NOTE}. Cumulatives are computed per realization before percentiling. Based on {meta.get('realizations_used','?')} successful realizations; N per date is in the tables.")
    for note in meta.get('notes',[]): st.info(note)
    if df.empty: st.warning('No profile data was retained for this run.'); return
    tabs=st.tabs([t for t,_ in PROFILE_TABS])
    for tab,(label,cat) in zip(tabs,PROFILE_TABS):
        with tab:
            opts=profile_variable_options(df,cat)
            if not opts: st.caption(f'No {label.lower()} series available.'); continue
            var=st.selectbox('Variable',opts,key=f'v17_prof_var_{cat}')
            st.plotly_chart(risk_charts.fan_chart(df,var),use_container_width=True)
            nd=st.slider('Dates in table',3,20,8,key=f'v17_prof_nd_{cat}')
            u=df[df['Variable']==var]['Unit'].iloc[0]
            st.markdown(f'**{var} [{u}] at selected dates**')
            st.dataframe(rp.profile_table(df,var,n_dates=int(nd)),hide_index=True,use_container_width=True)
    st.markdown('**End-of-horizon reserves / cumulative volumes**')
    st.dataframe(rp.reserves_table(df),hide_index=True,use_container_width=True)
    d1,d2,d3=st.columns(3)
    d1.download_button('Download profiles CSV (tidy)',export_profiles_csv(r),'fieldnet_profiles_tidy.csv','text/csv',use_container_width=True)
    d2.download_button('Download profiles CSV (wide)',export_profiles_csv(r,wide=True),'fieldnet_profiles_wide.csv','text/csv',use_container_width=True)
    d3.download_button('Download profiles JSON',export_profiles_json(r),'fieldnet_profiles.json','application/json',use_container_width=True)

def _param_builder(st,nodes,edges):
    """Drop-down uncertainty builder: What -> Which parameter -> How uncertain -> Add. Returns the list of parameter rows (PARAM_COLS schema)."""
    ss=st.session_state
    if 'mc_params' not in ss:
        w=next((n for n in nodes if n.get('kind')=='well'),None); ps=parameters_for(nodes,edges,'kind:well') if sum(1 for n in nodes if n.get('kind')=='well')>1 else (parameters_for(nodes,edges,w['id']) if w else [])
        spec=next((p for p in ps if p['label'].startswith('Productivity')),None)
        ss.mc_params=[make_parameter('kind:well' if sum(1 for n in nodes if n.get('kind')=='well')>1 else w['id'],'All wells' if sum(1 for n in nodes if n.get('kind')=='well')>1 else w.get('name',w['id']),spec,spec['low'],spec['mode'],spec['high'])] if (spec and w) else []
    rows=ss.mc_params
    valid={r['target_id'] for r in rows}; known={t for t,_ in target_options(nodes,edges)}
    st.markdown('#### What is uncertain?')
    opts=target_options(nodes,edges)
    if not opts: st.info('The model has no elements with uncertain parameters yet.'); return rows
    labels=dict(opts)
    c1,c2=st.columns(2)
    tid=c1.selectbox('1 · Element or group',[t for t,_ in opts],format_func=labels.get,key='mcb_target')
    specs=parameters_for(nodes,edges,tid)
    sp=c2.selectbox('2 · Parameter',range(len(specs)),format_func=lambda i: specs[i]['label'],key='mcb_param_'+str(tid)) if specs else None
    if sp is not None:
        spec=specs[sp]; k='mcb_'+str(tid)+'_'+spec['path']
        c3,c4=st.columns([1,2])
        dist=c3.selectbox('3 · Shape',list(DISTRIBUTIONS),format_func=DISTRIBUTIONS.get,key=k+'_dist')
        lo,hi=c4.slider('4 · Range as a factor of the current value  (1.00 = as in the model)',0.1,3.0,(float(spec['low']),float(spec['high'])),0.01,key=k+'_rng')
        mode=1.0
        if dist=='triangular': mode=st.slider('Most likely factor',float(lo),float(hi),min(max(1.0,float(lo)),float(hi)),0.01,key=k+'_mode')
        elif dist in ('normal','lognormal'): mode=min(max(1.0,float(lo)),float(hi))
        if st.button('➕ Add this uncertainty',key=k+'_add',type='primary'):
            try:
                rows.append(make_parameter(tid,labels[tid],spec,lo,mode,hi,dist)); ss.mc_params=rows; st.rerun()
            except ValueError as exc: st.error(str(exc))
    st.markdown('#### Current uncertainties')
    if not rows: st.caption('None yet — add at least one above.')
    for i,r in enumerate(rows):
        a,b,c=st.columns([5,3,1])
        a.markdown(f"**{r['name']}**"); b.caption(f"{r['distribution']} · {r['low']:.2f} – {r['high']:.2f}"+(f" (likely {r['mode']:.2f})" if r['distribution']=='triangular' else ''))
        if c.button('✖',key=f'mcb_del_{i}',help='Remove'): rows.pop(i); ss.mc_params=rows; st.rerun()
    with st.expander('Advanced: edit the table directly'):
        st.caption('Target IDs also accept groups: `kind:well`, `kind:reservoir`, `edges:pipeline`. Paths such as `params.pi_m3d_bar`; `multiply` = factor, `set` = absolute value.')
        ed=st.data_editor(pd.DataFrame(rows,columns=PARAM_COLS),num_rows='dynamic',use_container_width=True,key='v17_params_adv_'+str(len(rows)))
        if st.button('Apply table edits',key='mcb_apply_tbl'):
            ss.mc_params=[{k:(None if (isinstance(v,float) and v!=v) else v) for k,v in r.items()} for r in ed.to_dict('records') if clean_text(r.get('name'))]; st.rerun()
    return rows

def _mc_status_body(st):
    job=get_job('v17_mc')
    if job is None: return
    if job.status=='running':
        eta=job.eta_s; txt=f'Realization {job.done}/{job.n}  ·  {job.elapsed:.0f} s'+(f'  ·  about {eta/60:.1f} min left' if eta is not None else '  ·  estimating time…')
        st.progress(min(job.done/max(job.n,1),1.0),text=txt)
        if st.button('■ Cancel run',key='mc_cancel'): job.cancel()
        st.caption('The run goes on in the background: you can change tabs or settings without restarting it.')
    elif job.status=='done':
        if st.session_state.get('v17_mc_t0')!=job.t0:
            st.session_state['v17_mc']=job.result; st.session_state['v17_mc_t0']=job.t0
            try: st.rerun()
            except Exception: pass
        st.success(f'Finished {job.n} realizations in {job.elapsed:.0f} s.')
    elif job.status=='cancelled': st.warning(f'Cancelled after {job.done}/{job.n} realizations.')
    else: st.error(f'Monte Carlo failed: {job.error}')


def _mc_status(st):
    job=get_job('v17_mc')
    if job is not None and job.status=='running':
        try: st.fragment(run_every=2)(_mc_status_body)(st); return
        except TypeError: pass
    _mc_status_body(st)


def render_uncertainty(st,nodes,edges):
    st.subheader('Uncertainty, Monte Carlo & Risk')
    st.caption('Planning-level probabilistic wrapper around the deterministic production engine. P90 is conservative and P10 optimistic for production/reserves-style metrics. Samples are reproducible from the displayed seed.')
    c1,c2,c3,c4=st.columns(4)
    start=c1.date_input('MC start date',key='v17_start').isoformat(); years=c2.number_input('MC horizon [years]',0.03,50.0,1.0,0.5,key='v17_years'); step=c3.selectbox('MC timestep [days]',[10,30,60,90],index=3,key='v17_step'); samples=c4.number_input('Samples',5,1000,50,5,key='v17_samples')
    c5,c6=st.columns(2); seed=c5.number_input('Random seed',0,2_147_483_647,1701,1,key='v17_seed'); method=c6.selectbox('Sampling',['lhs','random'],key='v17_method')
    c7,c8=st.columns(2); maxw=default_workers(); workers=c7.number_input('Compute workers',1,maxw,1,1,key='v17_workers',help='Parallel processes for realizations (results identical to serial). Upper bound = min(CPUs-1, 8).'); store=c8.checkbox('Store full profiles',True,key='v17_store_profiles',help='Keep each realization\'s system time series (float32, memory-capped) to build P90/P50/P10/Mean profiles.')
    rows=_param_builder(st,nodes,edges)
    df=pd.DataFrame(rows,columns=PARAM_COLS)
    speed=st.selectbox('Run speed',list(SPEEDS),index=1,key='v17_speed',help='Each realization is a full forecast. Fewer tubing segments is faster and changes rates by well under 1 %. Per-element results are never stored in Monte Carlo runs.')
    ss=st.session_state; job=get_job('v17_mc'); running=bool(job and job.status=='running')
    if float(years)*365.25/max(int(step),1)>150: st.info(f'{int(years*365.25/step)} timesteps per realization: expect a long run. A 90-day step is roughly 3x faster than 30 days and is usually adequate for cumulative-oil statistics.')
    if st.button('▶ Run Monte Carlo',key='rb_mc',type='primary',disabled=running,use_container_width=True):
        try:
            pars=parse_uncertainty_rows(df.to_dict('records')); cfg=MonteCarloConfig(samples=int(samples),seed=int(seed),method=method,parameters=pars); sc=DevelopmentScenario('Monte Carlo',start,float(years),int(step))
            job=start_job('v17_mc',nodes,edges,sc,cfg,workers=int(workers),keep_series=bool(store),vlp_segments=SPEEDS[speed]); running=True; ss.pop('v17_mc',None)
        except Exception as exc: st.error(f'Monte Carlo failed: {exc}')
    _mc_status(st)
    if job and job.status=='done' and ss.get('v17_mc_t0')!=job.t0: ss['v17_mc']=job.result; ss['v17_mc_t0']=job.t0
    r=st.session_state.get('v17_mc')
    if not r: return
    a,b,c,d=st.columns(4); a.metric('Successful',r['successful_samples']); b.metric('Failed',r['failed_samples']); c.metric('Success rate',f"{100*r.get('success_fraction',0):.1f}%"); d.metric('Seed',r['seed'])
    metrics=[]
    for name,s in r.get('metrics',{}).items(): metrics.append({'Metric':name,**s})
    if metrics: st.dataframe(pd.DataFrame(metrics),hide_index=True,use_container_width=True)
    cp=r.get('compute')
    if cp:
        st.caption(f"Compute: {cp.get('workers_used',1)} worker(s) [{cp.get('start_method')}], {cp.get('elapsed_s',0):.1f} s")
        for note in cp.get('notes',[]): st.warning(note)
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
    if r.get('profiles'): _render_profiles(st,r)
    with st.expander('Realizations'): st.dataframe(runs,hide_index=True,use_container_width=True)
    d1,d2=st.columns(2); d1.download_button('Download Monte Carlo CSV',export_mc_csv(r),'fieldnet_monte_carlo.csv','text/csv',use_container_width=True); d2.download_button('Download Monte Carlo JSON',export_mc_json(r),'fieldnet_monte_carlo.json','application/json',use_container_width=True)
