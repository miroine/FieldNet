"""Templates & examples page: ready-made, solvable field models that can be loaded into the editor or saved as a new case."""
from __future__ import annotations
import pandas as pd
from network.templates import TEMPLATES, categories, build, catalogue

CLEAR_KEYS = ('forecast', 'forecast_hash', 'sched_result', 'scn_results', 'wc_result', 'qa28', 'hub_cache', 'pp_tables', 'nodal_mc', 'nodal_match', 'blowout',
              'case_forecast', 'shared_tables', 'export_basket', 'selected', 'fc_start', 'fc_years', 'fc_step')


def load_template(st, key, reset):
    """Replace the model on screen by a template and remember its suggested forecast settings (read by the forecast tab)."""
    ss = st.session_state; t = TEMPLATES[key]; ss.nodes, ss.edges = build(key); ss['canvas_epoch'] = ss.get('canvas_epoch', 0) + 1
    for k in CLEAR_KEYS: ss.pop(k, None)
    ss['tpl_forecast'] = {'start': t['start'], 'years': t['years'], 'step': t['step'], 'template': key}
    ss['loaded_template'] = key
    reset()


def render_templates(st, *, reset, library=None, solved=None):
    ss = st.session_state
    st.subheader('Templates & examples')
    st.caption('Ready-made illustrative models with round numbers — they are teaching starting points, not real fields. Loading replaces the model on screen '
               '(save it as a case first if you want to keep it). After loading: Solve, then run the forecast with the suggested settings.')
    cats = categories(); c1, c2 = st.columns([1, 2])
    cat = c1.selectbox('Category', ['All'] + cats, key='tpl_cat')
    keys = [k for k, t in TEMPLATES.items() if cat == 'All' or t['category'] == cat]
    key = c2.selectbox('Template', keys, format_func=lambda k: f"{TEMPLATES[k]['name']}  ({TEMPLATES[k]['category']})", key='tpl_sel')
    t = TEMPLATES[key]
    with st.container(border=True):
        st.markdown(f"**{t['name']}**"); st.write(t['shows'])
        st.markdown('**What to look at:** ' + t['watch'])
        st.caption(f"Suggested forecast: start {t['start']}, {t['years']} years, report step {t['step']} days.")
    a, b = st.columns(2)
    if a.button('📥 Load into the editor', key='tpl_load', type='primary', use_container_width=True):
        load_template(st, key, reset); st.success(f"Loaded '{t['name']}'. Solve to see its results."); st.rerun()
    if library is not None and b.button('➕ Load and save as a new case', key='tpl_load_case', use_container_width=True):
        from network.case_manager import new_case
        load_template(st, key, reset)
        library.add(new_case(t['name'], ss.nodes, ss.edges, ss.get('unit_profile'), description=t['shows'])); st.success('Saved as a new case.'); st.rerun()
    with st.expander('All templates'):
        st.dataframe(catalogue().drop(columns=['Key']), hide_index=True, use_container_width=True)
