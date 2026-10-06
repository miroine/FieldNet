"""Groups: put tanks and wells in named groups and see the sum (steady state, forecast profile, yearly volumes)."""
from __future__ import annotations
import pandas as pd
from network import groups as grp
from ui.hub_access import table_actions
from ui import charts


def render_groups(st, nodes, edges, hub, solved, reset=None):
    st.subheader('Groups')
    st.caption('A group is just a name on the elements - a tank and the wells it drains, a drill centre, a licence. Use "/" for a hierarchy: **North/Segment A** is part of **North**. '
               'Sums are computed from the same tables as everything else, so a group never disagrees with the wells in it.')
    tanks = [n for n in nodes if n.get('kind') == 'reservoir']
    with st.container(border=True):
        st.markdown('**Quick start: group a tank with the wells it drains**')
        if tanks:
            c1, c2, c3 = st.columns([2, 2, 1]); tid = c1.selectbox('Tank', [t['id'] for t in tanks], format_func=lambda i: next(t.get('name') or t['id'] for t in tanks if t['id'] == i), key='grp_tank')
            default = next((t.get('name') or t['id'] for t in tanks if t['id'] == tid), '')
            nm = c2.text_input('Group name', default, key=f'grp_name_{tid}'); mem = grp.tank_members(nodes, tid)
            c3.write(''); 
            if c3.button('Create group', key='grp_make', type='primary', use_container_width=True, disabled=not nm.strip()):
                grp.group_from_tank(nodes, tid, nm.strip()); st.session_state.pop('hub_cache', None); st.rerun()
            st.caption(f'{len(mem)} element(s) would join: ' + ', '.join(next((n.get('name') or n['id']) for n in nodes if n['id'] == i) for i in mem))
        else: st.caption('No tanks in the model - assign wells by hand below.')
    st.markdown('**Membership** (edit the Group column; blank = no group)')
    mt = grp.member_table(nodes)
    if mt.empty: st.info('No wells or tanks yet.'); return
    shown = mt.drop(columns=['_id'])
    ed = st.data_editor(shown, hide_index=True, use_container_width=True, disabled=['Element', 'Type'], key='grp_editor_' + str(abs(hash(mt.to_json()))))
    changed = False
    for nid, old, new in zip(mt['_id'], shown['Group'], ed['Group']):
        if str(old or '') != str(new or ''): grp.assign(nodes, [nid], str(new or '')); changed = True
    if changed: st.session_state.pop('hub_cache', None); st.rerun()
    names = grp.group_names(nodes)
    if not names: st.info('No groups yet. Create one above or type a name in the Group column.'); return
    st.markdown('**Sum of each group**')
    gs = hub.datasets.get('groups_solve')
    if gs is not None: st.caption('Steady-state solve of the model on screen:'); st.dataframe(gs, hide_index=True, use_container_width=True); table_actions(st, gs, 'groups_solve', 'grp_solve')
    else: st.caption('Solve the network (Network tab) to see steady-state group sums.')
    gp = hub.datasets.get('groups_profile')
    if gp is None: st.info('Run a forecast to see group profiles and yearly volumes.'); return
    sel = st.multiselect('Groups to plot', sorted(gp['Group'].unique()), default=sorted(gp['Group'].unique())[:4], key='grp_plot_sel')
    if sel:
        d = gp[gp['Group'].isin(sel)]
        q = st.selectbox('Quantity', [c for c in (('Gas [Sm3/d]', 'Oil [m3/d]') if st.session_state.get('_phase_resolved') == 'Gas' else ('Oil [m3/d]', 'Gas [Sm3/d]')) + ( 'Water [m3/d]', 'Liquid [m3/d]', 'Water injection [m3/d]', 'Cumulative gas [Sm3]', 'Cumulative oil [Sm3]', 'Pressure [bar]', 'RF gas [%]', 'RF oil [%]', 'Water cut [%]', 'GOR [Sm3/Sm3]') if c in d], key='grp_q')
        st.plotly_chart(charts.by_category_lines(d, 'Date', q, 'Group', f'{q.split(" [")[0]} by group', q.split('[')[-1].rstrip(']') if '[' in q else ''), use_container_width=True, key='grp_fig')
    ga = hub.datasets.get('groups_annual')
    if ga is not None:
        st.markdown('**Yearly volumes per group**'); st.dataframe(ga, hide_index=True, use_container_width=True); table_actions(st, ga, 'groups_annual', 'grp_annual')
    ok = grp.reconcile(nodes, getattr(hub, 'forecast', None) or {})
    for c in ok: (st.success if c['ok'] else st.error)(f"{c['check']}: {c['detail']}")
