"""Fluid library tab: several named fluids in one model (assign to a tank system, wells or flowlines; commingling is reported)."""
from __future__ import annotations
import copy
import pandas as pd
from network import fluids as fl

KEY = 'fluids'


def library(st, nodes, edges):
    ss = st.session_state
    lib = ss.get(KEY)
    if lib is None: lib = ss[KEY] = {}
    for k, v in fl.library_from_elements(nodes, edges, lib).items(): lib.setdefault(k, v)      # fluids found on elements (loaded case / colleague's file)
    return lib


def pvt_config_from_spec(spec, old=None):
    new = {'model': 'correlation', 'pb_corr': spec.pb_corr, 'bo_corr': spec.bo_corr, 'visc_corr': spec.visc_corr, 'z_corr': spec.z_corr, 'co2': spec.co2, 'h2s': spec.h2s, 'n2': spec.n2}
    if spec.pb_bar: new['pb_bar'] = spec.pb_bar
    if spec.salinity_wt_pct: new['salinity_wt_pct'] = spec.salinity_wt_pct
    for k in ('cal', 'rsb_sm3sm3'):
        if old and k in old: new[k] = old[k]
    return new


def render_fluid_library(st, nodes, edges, solved, spec_from_state):
    ss = st.session_state; lib = library(st, nodes, edges)
    st.caption('Use more than one fluid in the model: an oil rim and a gas cap, two reservoirs with different oil, a sour and a sweet stream. Define each fluid once, give it to a tank (its wells follow), '
               'to individual wells or to flowlines. Streams that meet are blended in the network (ideal volume mixing of API and gas gravity). Water cut stays an element property.')
    names = lambda ids, objs: {o['id']: (o.get('name') or o['id']) for o in objs}
    t_def, t_asg, t_chk = st.tabs(['Define fluids', 'Assign to the model', 'Where fluids are used'])
    with t_def:
        if lib: st.dataframe(fl.table(lib, nodes, edges), hide_index=True, use_container_width=True)
        else: st.info('No named fluid yet. Define one below - it can start from the settings on the **Fluid & correlations** tab.')
        with st.container(border=True):
            st.markdown('**New fluid**'); c1, c2, c3, c4 = st.columns(4)
            nm = c1.text_input('Name', f'Fluid {len(lib) + 1}', key='fl_new_name'); api = c2.number_input('Oil gravity [°API]', 5.0, 70.0, float(ss.get('pvt_api', 36.0)), 0.5, key='fl_new_api')
            gsg = c3.number_input('Gas SG', 0.5, 2.0, float(ss.get('pvt_gsg', 0.72)), 0.01, key='fl_new_gsg'); gor = c4.number_input('GOR [Sm³/Sm³]', 0.0, 3000.0, float(ss.get('pvt_gor', 100.0)), 5.0, key='fl_new_gor')
            use = st.radio('PVT model', ['Legacy screening PVT', 'Correlation PVT with the settings of the Fluid & correlations tab'], horizontal=True, key='fl_new_model')
            if st.button('➕ Add fluid to the library', key='fl_add', type='primary'):
                try:
                    pvt = None
                    if use.startswith('Correlation'):
                        spec = spec_from_state(ss, api, gsg, gor); errs = spec.validate()
                        if errs: raise ValueError('; '.join(errs))
                        pvt = pvt_config_from_spec(spec, (ss.get('pvt_cal') or {}).get('applied') and {'cal': ss['pvt_cal']['spec']['cal']} or None)
                    fl.library_add(lib, fl.new_fluid(nm, api, gsg, gor, pvt)); st.success(f"Fluid '{nm}' added."); st.rerun()
                except ValueError as exc: st.error(str(exc))
        if lib:
            with st.expander('Edit / rename / delete'):
                sel = st.selectbox('Fluid', list(lib), key='fl_edit_sel'); f = lib[sel]; u = fl.usage(nodes, edges).get(sel, {'wells': 0, 'tanks': 0, 'flowlines': 0})
                e1, e2, e3 = st.columns(3); f['api'] = e1.number_input('API', 5.0, 70.0, float(f['api']), 0.5, key=f'fl_e_api_{sel}'); f['gas_sg'] = e2.number_input('Gas SG', 0.5, 2.0, float(f['gas_sg']), 0.01, key=f'fl_e_g_{sel}'); f['gor_sm3sm3'] = e3.number_input('GOR [Sm³/Sm³]', 0.0, 3000.0, float(f['gor_sm3sm3']), 5.0, key=f'fl_e_r_{sel}')
                b1, b2 = st.columns(2)
                if b1.button(f"Update the {sum(u.values())} element(s) that use it", key='fl_prop', disabled=sum(u.values()) == 0, use_container_width=True, help='Re-applies the fluid numbers (and tank Boi / Rsi / Pb) to every element that uses this fluid. Water cut is not touched.'):
                    n = fl.propagate(lib, nodes, edges, sel); ss.pop('hub_cache', None); st.success(f'{n} element(s) updated. Re-solve the network.'); st.rerun()
                new = b2.text_input('Rename to', sel, key=f'fl_ren_{sel}')
                if new != sel and b2.button('Rename', key='fl_ren_btn', use_container_width=True):
                    try: fl.library_rename(lib, nodes, edges, sel, new); st.rerun()
                    except ValueError as exc: st.error(str(exc))
                if st.checkbox(f"Delete '{sel}' (elements using it keep their numbers but lose the name)", key=f'fl_del_ok_{sel}') and st.button('🗑 Delete fluid', key='fl_del'): fl.library_delete(lib, nodes, edges, sel, force=True); st.rerun()
    with t_asg:
        if not lib: st.info('Define a fluid first.'); return
        fsel = st.selectbox('Fluid to assign', list(lib), key='fl_asg_fluid'); f = lib[fsel]
        tanks = {n['id']: n.get('name') or n['id'] for n in nodes if n.get('kind') == 'reservoir'}; wells = {n['id']: n.get('name') or n['id'] for n in nodes if n.get('kind') == 'well'}
        lines = {e['id']: e.get('name') or e['id'] for e in edges if e.get('kind', 'pipeline') == 'pipeline'}
        a, b, c = st.columns(3)
        with a:
            st.markdown('**Tank system**'); tsel = st.multiselect('Tanks (their wells follow)', list(tanks), format_func=tanks.get, key='fl_asg_tanks')
            if st.button('Assign to tanks + their wells', key='fl_asg_t', disabled=not tsel, use_container_width=True, help='Also sets the tank Boi, Rsi and bubble point from the fluid so the material balance and the wells use the same PVT.'):
                n = sum(fl.assign_tank_system(nodes, edges, f, t) for t in tsel); ss.pop('hub_cache', None); st.success(f'{n} element(s) updated. Re-solve.'); st.rerun()
        with b:
            st.markdown('**Individual wells**'); wsel = st.multiselect('Wells', list(wells), format_func=wells.get, key='fl_asg_wells')
            if st.button('Assign to wells', key='fl_asg_w', disabled=not wsel, use_container_width=True): n = fl.assign(nodes, edges, f, wsel, []); ss.pop('hub_cache', None); st.success(f'{n} well(s) updated. Re-solve.'); st.rerun()
        with c:
            st.markdown('**Flowlines**'); lsel = st.multiselect('Flowlines', list(lines), format_func=lines.get, key='fl_asg_lines')
            if st.button('Assign to flowlines', key='fl_asg_l', disabled=not lsel, use_container_width=True, help='Sets the fluid a line carries when it is hydraulically solved; commingled flow is better left to the blend (below).'): n = fl.assign(nodes, edges, f, [], lsel); ss.pop('hub_cache', None); st.success(f'{n} line(s) updated. Re-solve.'); st.rerun()
    with t_chk:
        ck = fl.check(lib, nodes, edges, solved())
        if ck.empty: st.success('No fluid inconsistencies found.')
        else: st.dataframe(ck, hide_index=True, use_container_width=True)
        r = solved()
        if r:
            try:
                cm = fl.commingled(nodes, edges, r)
                if not cm.empty: st.markdown('**Blended fluid at nodes and flowlines (current solve)**'); st.dataframe(cm, hide_index=True, use_container_width=True)
            except Exception as exc: st.caption(f'Blend not available: {exc}')
        else: st.caption('Solve the network to see the blended fluid where streams meet.')
