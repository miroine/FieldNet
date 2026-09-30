from __future__ import annotations
import json
import pandas as pd
import plotly.express as px
from network.development_v26 import DevelopmentTask, DevelopmentPlan, compile_plan, run_development_plan
from network.field_development import _coerce_value
from ui.widgets import clean_num, clean_text

TASK_TYPES=['drill_well','workover','tieback','commission','first_production','facility_expansion','compression_start','shutdown','abandon']

def _tasks(rows):
    out=[]
    for r in rows:
        tid=clean_text(r.get('id')); target=clean_text(r.get('target_id'))
        if not tid or not target: continue
        preds=tuple(x.strip() for x in clean_text(r.get('predecessors')).split(',') if x.strip())
        val=r.get('value')
        if not isinstance(val,(list,dict)) and pd.isna(val): val=None
        elif isinstance(val,str): val=_coerce_value(val)
        start=clean_text(r.get('earliest_start'))
        if start:
            try: start=pd.Timestamp(start).date().isoformat()
            except Exception: raise ValueError(f"Task {tid}: invalid earliest_start {start!r}")
        out.append(DevelopmentTask(tid,clean_text(r.get('name'),tid),clean_text(r.get('task_type'),'commission'),target,start,int(clean_num(r.get('duration_days'),0)),preds,clean_text(r.get('resource')) or None,clean_text(r.get('field')) or None,val,clean_text(r.get('description'))))
    return out

def render_development_v26(st,nodes,edges):
    st.subheader('v26 Development Planning')
    st.caption('Dependency- and resource-constrained development scheduling compiled into the existing quasi-steady production forecast. No economics are used.')
    c1,c2,c3=st.columns(3); start=c1.date_input('Plan start',key='v26_start').isoformat(); years=c2.number_input('Horizon [years]',0.1,50.0,5.0,0.5,key='v26_years'); step=c3.selectbox('Forecast timestep [days]',[7,14,30,60,90],2,key='v26_step')
    ids=pd.DataFrame([{'id':x.get('id'),'name':x.get('name',x.get('id')),'type':x.get('kind')} for x in [*nodes,*edges]])
    with st.expander('Valid target IDs'): st.dataframe(ids,hide_index=True,use_container_width=True)
    default=pd.DataFrame(columns=['id','name','task_type','target_id','earliest_start','duration_days','predecessors','resource','field','value','description'])
    df=st.data_editor(default,num_rows='dynamic',use_container_width=True,key='v26_tasks',column_config={'task_type':st.column_config.SelectboxColumn(options=TASK_TYPES)})
    b1,b2=st.columns(2)
    try: plan=DevelopmentPlan('Development Plan',start,float(years),int(step),[t if t.earliest_start else DevelopmentTask(t.id,t.name,t.task_type,t.target_id,start,t.duration_days,t.predecessors,t.resource,t.field,t.value,t.description) for t in _tasks(df.to_dict('records'))])
    except Exception as exc: st.error(str(exc)); plan=None
    if b1.button('Compile v26 schedule',use_container_width=True) and plan is not None:
        try: st.session_state.v26_compiled=compile_plan(plan,nodes,edges)
        except Exception as exc: st.error(str(exc))
    if b2.button('Run v26 development forecast',type='primary',use_container_width=True) and plan is not None:
        try: st.session_state.v26_result=run_development_plan(nodes,edges,plan); st.session_state.v26_compiled=st.session_state.v26_result['development_plan']
        except Exception as exc: st.error(str(exc))
    comp=st.session_state.get('v26_compiled')
    if comp:
        sdf=pd.DataFrame(comp['schedule']); st.markdown('### Executable schedule'); st.dataframe(sdf,hide_index=True,use_container_width=True)
        if not sdf.empty:
            sdf=sdf.assign(finish=[f if f>s0 else (pd.Timestamp(s0)+pd.Timedelta(days=1)).date().isoformat() for s0,f in zip(sdf['start'],sdf['finish'])])  # zero-duration bars are invisible
            st.plotly_chart(px.timeline(sdf,x_start='start',x_end='finish',y='name',color='resource',title='Development schedule'),use_container_width=True)
        st.download_button('Download v26 plan JSON',json.dumps({**comp,'events':[e.as_forecast_event() for e in comp['events']]},indent=2,default=str),'fieldnet_v26_development_plan.json','application/json',use_container_width=True)
    r=st.session_state.get('v26_result')
    if r:
        f=pd.DataFrame(r['forecast']['field']); st.markdown('### Production consequence')
        if not f.empty: st.plotly_chart(px.line(f,x='Date',y='Oil [m3/d]',title='Oil production under development plan'),use_container_width=True); st.dataframe(f,hide_index=True,use_container_width=True)
