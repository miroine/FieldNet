from __future__ import annotations
import json
import pandas as pd
import plotly.express as px
from network.development_v26 import DevelopmentTask, DevelopmentPlan, compile_plan, run_development_plan

TASK_TYPES=['drill_well','workover','tieback','commission','first_production','facility_expansion','compression_start','shutdown','abandon']

def _tasks(rows):
    out=[]
    for r in rows:
        if not r.get('id') or not r.get('target_id'): continue
        preds=tuple(x.strip() for x in str(r.get('predecessors') or '').split(',') if x.strip())
        val=r.get('value');
        if pd.isna(val) if not isinstance(val,(list,dict)) else False: val=None
        out.append(DevelopmentTask(str(r['id']),str(r.get('name') or r['id']),str(r.get('task_type') or 'commission'),str(r['target_id']),str(r.get('earliest_start')),int(r.get('duration_days') or 0),preds,str(r.get('resource') or '') or None,str(r.get('field') or '') or None,val,str(r.get('description') or '')))
    return out

def render_development_v26(st,nodes,edges):
    st.subheader('v26 Development Planning')
    st.caption('Dependency- and resource-constrained development scheduling compiled into the existing quasi-steady production forecast. No economics are used.')
    c1,c2,c3=st.columns(3); start=c1.date_input('Plan start',key='v26_start').isoformat(); years=c2.number_input('Horizon [years]',0.1,50.0,5.0,0.5,key='v26_years'); step=c3.selectbox('Forecast timestep [days]',[7,14,30,60,90],2,key='v26_step')
    ids=pd.DataFrame([{'id':x.get('id'),'name':x.get('name',x.get('id')),'type':x.get('kind')} for x in [*nodes,*edges]])
    with st.expander('Valid target IDs'): st.dataframe(ids,hide_index=True,use_container_width=True)
    default=pd.DataFrame(columns=['id','name','task_type','target_id','earliest_start','duration_days','predecessors','resource','field','value','description'])
    df=st.data_editor(default,num_rows='dynamic',use_container_width=True,key='v26_tasks',column_config={'task_type':st.column_config.SelectboxColumn(options=TASK_TYPES)})
    plan=DevelopmentPlan('Development Plan',start,float(years),int(step),_tasks(df.to_dict('records')))
    b1,b2=st.columns(2)
    if b1.button('Compile v26 schedule',use_container_width=True):
        try: st.session_state.v26_compiled=compile_plan(plan,nodes,edges)
        except Exception as exc: st.error(str(exc))
    if b2.button('Run v26 development forecast',type='primary',use_container_width=True):
        try: st.session_state.v26_result=run_development_plan(nodes,edges,plan); st.session_state.v26_compiled=st.session_state.v26_result['development_plan']
        except Exception as exc: st.error(str(exc))
    comp=st.session_state.get('v26_compiled')
    if comp:
        sdf=pd.DataFrame(comp['schedule']); st.markdown('### Executable schedule'); st.dataframe(sdf,hide_index=True,use_container_width=True)
        if not sdf.empty: st.plotly_chart(px.timeline(sdf,x_start='start',x_end='finish',y='name',color='resource',title='Development schedule'),use_container_width=True)
        st.download_button('Download v26 plan JSON',json.dumps({**comp,'events':[e.as_forecast_event() for e in comp['events']]},indent=2),'fieldnet_v26_development_plan.json','application/json',use_container_width=True)
    r=st.session_state.get('v26_result')
    if r:
        f=pd.DataFrame(r['forecast']['field']); st.markdown('### Production consequence')
        if not f.empty: st.plotly_chart(px.line(f,x='Date',y='Oil [m3/d]',title='Oil production under development plan'),use_container_width=True); st.dataframe(f,hide_index=True,use_container_width=True)
