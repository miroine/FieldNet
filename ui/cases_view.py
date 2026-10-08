"""Cases page: a library of working copies of the model that can be saved, duplicated, loaded, exported, shared with a
password and compared side by side."""
from __future__ import annotations
import time
import pandas as pd
from network.case_manager import (CaseLibrary, new_case, model_hash, solve_summary, case_diff, compare_table, profile_series,
                                  export_json, export_zip, import_json, import_zip, merge, forecast_light)
from network.case_share import (ShareError, share_package, open_package, to_link, from_link, link_ok, password_problem, MAX_LINK_CHARS)
from ui.run_button import start_run

KEY = 'case_library'


def library(st):
    ss = st.session_state
    if KEY not in ss: ss[KEY] = CaseLibrary()
    return ss[KEY]


def _working_hash(ss):
    return model_hash({'nodes': ss.nodes, 'edges': ss.edges, 'unit_profile': ss.get('unit_profile')})


def _fresh_forecast(ss):
    """The session forecast, only if it was computed for the model currently on screen."""
    from ui.graph_contract import graph_hash
    fc = ss.get('forecast')
    if fc and ss.get('forecast_hash') == graph_hash(ss.nodes, ss.edges): return fc
    cf = ss.get('case_forecast')
    return cf['fc'] if cf and cf.get('hash') == graph_hash(ss.nodes, ss.edges) else None


SESSION_EXTRAS = {'post_scripts': 'pp_src', 'stea_mapping': 'stea_map', 'mb_history': 'mb_history', 'nodal_tests': 'nodal_tests', 'nodal_survey': 'nodal_survey', 'fluid_library': 'fluids'}


def current_extras(ss):
    """User inputs worth keeping with a case (scripts, mappings, measured data, fluid library)."""
    return {k: ss.get(v) for k, v in SESSION_EXTRAS.items() if ss.get(v)}


def load_case_into_editor(st, case, reset):
    import copy
    ss = st.session_state
    ss.nodes, ss.edges = copy.deepcopy(case['nodes']), copy.deepcopy(case['edges']); ss['canvas_epoch'] = ss.get('canvas_epoch', 0) + 1
    if case.get('unit_profile'): ss.unit_profile = case['unit_profile']
    ex = case.get('extras') or {}
    for k, v in SESSION_EXTRAS.items():
        if k in ex: ss[v] = copy.deepcopy(ex[k])
        else: ss.pop(v, None)
    for k in ('hub_cache', 'pp_tables', 'nodal_mc', 'nodal_match', 'blowout', 'case_forecast', 'shared_tables', 'export_basket'): ss.pop(k, None)
    reset()
    if case.get('forecast') and case['forecast'].get('field'):
        from ui.graph_contract import graph_hash
        ss['case_forecast'] = {'fc': copy.deepcopy(case['forecast']), 'hash': graph_hash(ss.nodes, ss.edges)}
    for k in ('forecast', 'sched_result', 'scn_results', 'wc_result', 'qa28'): ss.pop(k, None)
    ss.pop('selected', None)


def _solve_case(case):
    from solver.v21 import solve_v21
    from ui.graph_contract import solver_input
    n, e = solver_input(case['nodes'], case['edges'])
    res = solve_v21(n, e)
    case['solve'] = solve_summary(res)
    return res


def _forecast_case(case, start, years, step, caps):
    from network.forecast import run_forecast
    from ui.graph_contract import solver_input
    from network.prognosis import forecast_kpis
    n, e = solver_input(case['nodes'], case['edges'])
    fc = run_forecast(n, e, start, years, step, enforce_constraints=caps, store_elements=False)
    case['forecast'] = forecast_light(fc); case['forecast_kpis'] = forecast_kpis(case['forecast']) if case['forecast'] else None


def render_cases(st, *, solved, reset, profile_label=''):
    ss = st.session_state; lib = library(st)
    st.subheader('Cases')
    st.caption('A case is a named working copy of the model together with its results. Duplicate a case, change the copy, then compare. '
               'Cases live in this browser session — export or share them to keep them.')
    if len(lib) == 0:
        st.info('No case yet. Save the model you have on screen as your first case.')
        c1, c2 = st.columns([3, 1]); nm = c1.text_input('Case name', 'Base case', key='cs_first_name')
        if c2.button('Create first case', type='primary', key='cs_first', use_container_width=True):
            lib.add(new_case(nm, ss.nodes, ss.edges, ss.get('unit_profile'), results=solved(), forecast=_fresh_forecast(ss), extras=current_extras(ss))); st.rerun()
        _share_import_only(st, lib)
        return
    act = lib.cases[lib.active]
    dirty = model_hash({'nodes': ss.nodes, 'edges': ss.edges, 'unit_profile': ss.get('unit_profile')}) != model_hash(act)
    st.markdown(f"**Active case:** {act['name']} " + ('· 🟠 *the model on screen has unsaved changes*' if dirty else '· 🟢 *saved*'))
    t_lib, t_cmp, t_share = st.tabs(['Library', 'Compare', 'Share & export'])
    with t_lib: _library_tab(st, lib, solved, reset, dirty)
    with t_cmp: _compare_tab(st, lib)
    with t_share: _share_tab(st, lib)


def _library_tab(st, lib, solved, reset, dirty):
    ss = st.session_state
    rows = lib.table(); df = pd.DataFrame(rows).drop(columns=['id'])
    st.dataframe(df, hide_index=True, use_container_width=True)
    names = lib.names(); ids = list(names)
    sel = st.selectbox('Case', ids, index=ids.index(lib.active) if lib.active in ids else 0, format_func=lambda i: names[i], key='cs_sel')
    c = lib.get(sel)
    a, b, d = st.columns(3)
    if a.button('💾 Save model on screen into this case', key='cs_save', type='primary' if (dirty and sel == lib.active) else 'secondary', use_container_width=True,
                help='Overwrites the case with the model currently in the editor (and the current solve / forecast, if any).'):
        r = solved(); fc = _fresh_forecast(ss)
        lib.save(sel, ss.nodes, ss.edges, ss.get('unit_profile'), results=r, forecast=fc, extras=current_extras(ss)); lib.active = sel; st.success(f"Saved to '{c['name']}'."); st.rerun()
    if b.button('📂 Load this case into the editor', key='cs_load', use_container_width=True, help='Replaces the model on screen. Save first if you have unsaved changes.'):
        load_case_into_editor(st, c, reset); lib.active = sel; st.success(f"Loaded '{c['name']}'. Solve to see its results."); st.rerun()
    if d.button('➕ Save model on screen as a new case', key='cs_saveas', use_container_width=True):
        lib.add(new_case(f"{c['name']} (edited)" if dirty else 'New case', ss.nodes, ss.edges, ss.get('unit_profile'), parent_id=sel, results=solved(), forecast=_fresh_forecast(ss), extras=current_extras(ss))); st.rerun()
    if dirty and sel == lib.active: st.caption('The editor differs from the saved case — use Save, or Save as a new case to keep both.')
    with st.expander('Duplicate / copy between cases', expanded=True):
        x1, x2 = st.columns([3, 1]); nm = x1.text_input('Name of the copy', f"{c['name']} - copy", key=f'cs_dup_name_{sel}')
        keep = x1.checkbox('Keep the results of the original', value=True, key='cs_dup_keep')
        if x2.button('Duplicate', key='cs_dup', use_container_width=True): lib.duplicate(sel, nm, keep_results=keep); st.rerun()
        others = [i for i in ids if i != sel]
        if others:
            y1, y2, y3 = st.columns([2, 2, 1]); dst = y1.selectbox('Copy this case into', others, format_func=lambda i: names[i], key='cs_cp_dst')
            parts = y2.multiselect('What to copy', ['model', 'results'], default=['model'], key='cs_cp_parts')
            y3.write(''); 
            if y3.button('Copy', key='cs_cp', use_container_width=True, disabled=not parts): lib.copy_into(sel, dst, tuple(parts)); st.success(f"Copied {', '.join(parts)} into '{names[dst]}' (its previous {', '.join(parts)} was overwritten)."); st.rerun()
    with st.expander('Rename / description / delete'):
        r1, r2 = st.columns([3, 1]); new = r1.text_input('Name', c['name'], key=f'cs_ren_{sel}')
        if r2.button('Rename', key='cs_ren', use_container_width=True):
            try: lib.rename(sel, new); st.rerun()
            except ValueError as exc: st.error(str(exc))
        desc = st.text_area('Description / assumptions', c.get('description', ''), key=f'cs_desc_{sel}')
        if desc != c.get('description', ''): c['description'] = desc
        if len(lib) > 1:
            sure = st.checkbox('I want to delete this case', key=f'cs_del_ok_{sel}')
            if st.button('🗑 Delete case', key='cs_del', disabled=not sure): lib.delete(sel); st.rerun()


def _compare_tab(st, lib):
    names = lib.names(); ids = list(names)
    if len(ids) < 2: st.info('Duplicate a case (Library tab) to compare two versions.'); return
    pick = st.multiselect('Cases to compare', ids, default=ids[:4], format_func=lambda i: names[i], key='cs_cmp')
    if len(pick) < 2: st.info('Pick at least two cases.'); return
    cases = [lib.get(i) for i in pick]
    base_id = st.selectbox('Baseline (differences are shown against it)', pick, format_func=lambda i: names[i], key='cs_base')
    cases = [lib.get(base_id)] + [c for c in cases if c['id'] != base_id]
    with st.expander('Compute results for the selected cases', expanded=not all(c.get('solve') for c in cases)):
        st.caption('Cases keep the results that existed when they were saved. Use these to (re)compute them without touching the editor.')
        p1, p2, p3, p4 = st.columns(4)
        start = p1.date_input('Forecast start', key='cs_fc_start').isoformat(); years = p2.number_input('Horizon [years]', 0.5, 50.0, 10.0, 0.5, key='cs_fc_years')
        step = p3.selectbox('Report step [days]', [30, 60, 90, 180, 365], index=3, key='cs_fc_step'); caps = p4.toggle('Honour capacities', True, key='cs_fc_caps')
        h = '|'.join(model_hash(c) for c in cases)
        rb = start_run(st, '▶ Solve selected cases', 'cs_run_solve', model_hash=h, type='primary')
        if rb is not None:
            for i, c in enumerate(cases):
                rb.progress(i / len(cases), f"Solving '{c['name']}' ({i + 1}/{len(cases)})")
                try: _solve_case(c)
                except Exception as exc: rb.fail(f"{c['name']}: {exc}"); break
            rb.finish()
        rf = start_run(st, '▶ Run forecast for selected cases', 'cs_run_fc', model_hash=h)
        if rf is not None:
            for i, c in enumerate(cases):
                rf.progress(i / len(cases), f"Forecast '{c['name']}' ({i + 1}/{len(cases)})")
                try: _forecast_case(c, start, float(years), int(step), bool(caps))
                except Exception as exc: rf.fail(f"{c['name']}: {exc}"); break
            rf.finish()
    rows = compare_table(cases)
    df = pd.DataFrame(rows).dropna(axis=1, how='all')
    st.markdown('##### Results')
    if not any(c.get('solve') or c.get('forecast_kpis') for c in cases): st.warning('None of these cases has results yet — use the compute buttons above.')
    st.dataframe(df, hide_index=True, use_container_width=True)
    st.download_button('Download comparison (CSV)', df.to_csv(index=False), 'fieldnet_case_comparison.csv', 'text/csv')
    series = profile_series(cases)
    if series:
        import plotly.graph_objects as go
        from ui.charts import style, CATEGORICAL
        for col, title in (('Oil [m3/d]', 'Oil rate'), ('Cumulative oil [Sm3]', 'Cumulative oil')):
            fig = go.Figure()
            for i, (nm, (x, y)) in enumerate({k: v for k, v in profile_series(cases, col).items()}.items()):
                fig.add_trace(go.Scatter(x=x, y=y, name=nm, mode='lines', line=dict(color=CATEGORICAL[i % len(CATEGORICAL)], width=2.5 if nm == cases[0]['name'] else 2, dash='solid' if nm == cases[0]['name'] else 'dash')))
            st.plotly_chart(style(fig, title, y=col.split(' ')[-1] if '[' in col else None), use_container_width=True)
    else: st.caption('No forecast stored in these cases — run a forecast above to overlay profiles.')
    st.markdown('##### What is different in the model?')
    for c in cases[1:]:
        d = case_diff(cases[0], c)
        with st.expander(f"{cases[0]['name']} → {c['name']}: {d['change_count']} difference(s)", expanded=len(cases) == 2):
            if d['change_count']: st.dataframe(pd.DataFrame(d['rows']), hide_index=True, use_container_width=True)
            else: st.caption('Identical model.')


def _share_tab(st, lib):
    names = lib.names(); ids = list(names)
    st.markdown('##### Export')
    ex = st.multiselect('Cases to export', ids, default=ids, format_func=lambda i: names[i], key='cs_ex')
    if ex:
        a, b = st.columns(2)
        a.download_button('⬇ Export as JSON', export_json(lib, ex), 'fieldnet_cases.json', 'application/json', use_container_width=True)
        b.download_button('⬇ Export as ZIP (one file per case + checksums)', export_zip(lib, ex), 'fieldnet_cases.zip', 'application/zip', use_container_width=True)
    st.markdown('##### Import')
    up = st.file_uploader('Case file (.json or .zip)', type=['json', 'zip'], key='cs_up')
    if up is not None and st.button('Add to library', key='cs_import', use_container_width=True):
        try:
            data = up.getvalue() if hasattr(up, 'getvalue') else up.read()
            other = import_zip(data) if up.name.lower().endswith('.zip') else import_json(data)
            n = len(merge(lib, other)); st.success(f'Added {n} case(s).'); st.rerun()
        except Exception as exc: st.error(f'Could not import: {exc}')
    st.divider()
    st.markdown('##### Share with a password')
    st.caption('Cases are encrypted (AES-256-GCM, key derived from the password with scrypt). Send the file or the link by any channel and give the password '
               'separately. **There is no server:** the link *contains* the data, so it is long and only practical for small/medium cases; the file is the robust option. '
               'Anyone holding the file/link **and** the password can open it, and a shared copy cannot be revoked.')
    sh = st.multiselect('Cases to share', ids, default=ids[:1], format_func=lambda i: names[i], key='cs_sh')
    pw = st.text_input('Password (min 8 characters, not only letters)', type='password', key='cs_pw')
    if pw and password_problem(pw): st.caption('⚠ ' + password_problem(pw))
    if sh and pw and not password_problem(pw):
        blob = share_package(lib, sh, pw); link = to_link(blob)
        st.download_button('⬇ Download encrypted package (.fncase)', blob, 'fieldnet_share.fncase', 'application/octet-stream', use_container_width=True)
        if link_ok(link): st.text_area('Share link (copy all of it)', link, height=100, key='cs_link_out')
        else: st.info(f'This package is too large for a pasted link ({len(link) / 1e6:.1f} M characters, limit {MAX_LINK_CHARS / 1e6:.1f} M). Send the file instead.')
    _share_import_only(st, lib, header=False)


def _share_import_only(st, lib, header=True):
    if header: st.divider()
    st.markdown('##### Open a shared package')
    f = st.file_uploader('Encrypted package (.fncase)', type=['fncase'], key='cs_pkg')
    link = st.text_area('…or paste a share link', '', height=80, key='cs_link_in')
    pw = st.text_input('Password', type='password', key='cs_pw_open')
    if st.button('🔓 Decrypt and add to library', key='cs_open', use_container_width=True, disabled=not ((f is not None or link.strip()) and pw)):
        try:
            blob = (f.getvalue() if hasattr(f, 'getvalue') else f.read()) if f is not None else from_link(link)
            other = open_package(blob, pw); n = len(merge(lib, other)); st.success(f'Added {n} case(s): ' + ', '.join(c['name'] for c in other.cases.values())); st.rerun()
        except ShareError as exc: st.error(str(exc))
