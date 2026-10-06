from __future__ import annotations
import json
import pandas as pd
from ui.graph_contract import normalize_graph
from network.interchange_v27 import export_tables, import_tables, export_project_package, import_project_package, package_sha256

def render_interchange_v27(st,nodes,edges):
    st.subheader('Data & interoperability')
    st.caption('Validated boundary for CSV/project-package exchange. Solver storage remains canonical; Norwegian SI and Field values are converted only on import/export.')
    profile=st.selectbox('Interchange unit profile',['canonical','norwegian_si','field'],format_func=lambda x:{'canonical':'Canonical solver units','norwegian_si':'Norwegian SI','field':'Field'}[x],key='v27_profile')
    tabs=export_tables(nodes,edges,profile)
    a,b,c=st.columns(3)
    a.download_button('Download nodes CSV',tabs['nodes.csv'],'fieldnet_nodes.csv','text/csv',use_container_width=True)
    b.download_button('Download edges CSV',tabs['edges.csv'],'fieldnet_edges.csv','text/csv',use_container_width=True)
    package=export_project_package(nodes,edges,profile)
    c.download_button('Download project package',package,'fieldnet_project.zip','application/zip',use_container_width=True)
    st.caption(f'Package SHA-256: `{package_sha256(package)}`')
    st.markdown('**Import node + edge tables**')
    nfile=st.file_uploader('Nodes CSV',type=['csv'],key='v27_nodes_upload'); efile=st.file_uploader('Edges CSV',type=['csv'],key='v27_edges_upload')
    if nfile and efile and st.button('Validate tabular import',use_container_width=True):
        result=import_tables(nfile.getvalue().decode('utf-8-sig'),efile.getvalue().decode('utf-8-sig'),profile); st.session_state.v27_import=result
    if st.session_state.get('v27_import'):
        r=st.session_state.v27_import
        if r['ok']:
            st.success(f"Validated {len(r['nodes'])} nodes and {len(r['edges'])} edges.")
            if st.button('Apply validated import',type='primary',use_container_width=True): st.session_state.nodes,st.session_state.edges,_gi=normalize_graph(r['nodes'],r['edges']); st.session_state.solve=None; st.rerun()
        else: st.error('Import rejected. No project data were changed.')
        if r['issues']: st.dataframe(pd.DataFrame(r['issues']),hide_index=True,use_container_width=True)
    st.markdown('**Import FieldNet package**')
    pfile=st.file_uploader('Project package ZIP',type=['zip'],key='v27_package_upload')
    if pfile and st.button('Validate project package',use_container_width=True):
        try: st.session_state.v27_pkg=import_project_package(pfile.getvalue())
        except Exception as exc: st.session_state.v27_pkg={'ok':False,'issues':[{'severity':'error','message':str(exc)}]}
    if st.session_state.get('v27_pkg'):
        r=st.session_state.v27_pkg
        if r.get('ok'):
            st.json(r.get('manifest',{}))
            if st.button('Apply validated package',type='primary',use_container_width=True): st.session_state.nodes,st.session_state.edges,_gi=normalize_graph(r['nodes'],r['edges']); st.session_state.solve=None; st.rerun()
        else: st.error('Package rejected. No project data were changed.')
        if r.get('issues'): st.dataframe(pd.DataFrame(r['issues']),hide_index=True,use_container_width=True)
