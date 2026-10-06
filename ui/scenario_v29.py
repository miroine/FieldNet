"""Streamlit scenario-management workspace for FieldNet v29."""
import json
import pandas as pd
from network.scenario_v29 import create_snapshot, branch_snapshot, scenario_diff, comparison_table, export_scenario_archive, import_scenario_archive
from solver.model_assurance_v28 import model_quality_report

def render_scenario_v29(st,nodes,edges,unit_profile='norwegian_si'):
    st.subheader('Scenario Management & Reproducibility')
    st.caption('Immutable content-addressed snapshots. Branches copy engineering cases; scenario bookkeeping never mutates solver inputs.')
    ss=st.session_state.setdefault('v29_snapshots',[])
    name=st.text_input('Snapshot / scenario name',value=f'Scenario {len(ss)+1}',key='v29_name')
    desc=st.text_input('Description',value='',key='v29_desc')
    assumptions=st.text_area('Assumptions (one per line)',value='',key='v29_assumptions')
    parent=st.selectbox('Parent snapshot',['None']+[f"{s['name']} | {s['snapshot_id']}" for s in ss],key='v29_parent')
    if st.button('Create immutable snapshot',type='primary',use_container_width=True):
        qa=model_quality_report(nodes,edges,unit_profile=unit_profile)
        ass=[{'statement':x.strip()} for x in assumptions.splitlines() if x.strip()]
        if parent=='None': snap=create_snapshot(name,nodes,edges,assumptions=ass,description=desc,qa_report=qa)
        else:
            p=ss[['None']+[f"{s['name']} | {s['snapshot_id']}" for s in ss].index(parent)-1]
            snap=branch_snapshot(p,name,nodes=nodes,edges=edges,assumptions=ass or p.get('assumptions'),description=desc); snap['qa']=qa
        ss.append(snap); st.success(f"Snapshot {snap['snapshot_id']} created; QA {qa['quality_gate']}")
    if ss:
        st.dataframe(pd.DataFrame(comparison_table(ss)),hide_index=True,use_container_width=True)
        if len(ss)>=2:
            labels=[f"{s['name']} | {s['snapshot_id']}" for s in ss]; a=st.selectbox('Compare from',labels,index=max(0,len(labels)-2),key='v29_a'); b=st.selectbox('Compare to',labels,index=len(labels)-1,key='v29_b')
            da=ss[labels.index(a)]; db=ss[labels.index(b)]; d=scenario_diff(da,db)
            st.metric('Structured changes',d['change_count']); st.dataframe(pd.DataFrame(d['changes']),hide_index=True,use_container_width=True)
        archive=export_scenario_archive(ss)
        st.download_button('Download scenario archive',archive,'fieldnet_scenarios.zip','application/zip',use_container_width=True)
    up=st.file_uploader('Import scenario archive',type=['zip'],key='v29_upload')
    if up and st.button('Validate/import scenario archive',use_container_width=True):
        try:
            got=import_scenario_archive(up.getvalue()); st.session_state.v29_snapshots=got['snapshots']; st.success(f"Verified and imported {len(got['snapshots'])} snapshots")
        except Exception as exc: st.error(str(exc))
