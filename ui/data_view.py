"""Cases & Data tab pieces: data hub browser + consistency checks, export (Excel / CSV / JSON / STEA-style / local API), Python post-processing."""
from __future__ import annotations
import pandas as pd
from network import data_hub as dh, exporters as ex, postprocess as pp
from ui.hub_access import shared_tables, table_actions, EXTRA, basket

MIME_XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def all_datasets(st, hub):
    """Hub tables + tables shared from other tabs (export basket) + post-processed tables. Later sources never overwrite hub names."""
    d = dict(hub.datasets); meta = dict(hub.meta)
    for k, v in shared_tables(st).items():
        name = k if k not in d else f'{k}_shared'; d[name] = v; meta[name] = {'description': 'Table sent from a tab', 'source': 'tab', 'units': ''}
    return d, meta


def render_data(st, nodes, edges, hub, solved):
    t_hub, t_exp, t_pp, t_api = st.tabs(['Data hub', 'Export', 'Post-process (Python)', 'Local API'])
    with t_hub: _hub_tab(st, nodes, hub)
    with t_exp: _export_tab(st, nodes, hub)
    with t_pp: _pp_tab(st, hub)
    with t_api: _api_tab(st, hub)


def _hub_tab(st, nodes, hub):
    ss = st.session_state
    st.caption('Every plot, group sum, export and script reads the same tables, built from the model, the current solve and the forecast. The checks below compare the tables with each other.')
    if hub.info.get('stale_forecast'): st.warning('The forecast on screen was computed for an earlier version of the model and is left out of the hub. Re-run the forecast.')
    if not any(k.startswith('forecast_') for k in hub.datasets): st.info('No forecast yet: only model and steady-state tables are available. Run a forecast to add profiles, yearly volumes and group sums.')
    from ui.graph_contract import graph_hash
    chk = dh.check_consistency(hub, nodes, getattr(hub, 'forecast', None), graph_hash(nodes, ss.edges))
    if len(chk):
        n_ok = int((chk['Status'] == 'OK').sum()); n_warn = int((chk['Status'] == 'WARN').sum()); n_fail = int((chk['Status'] == 'FAIL').sum())
        (st.error if n_fail else st.warning if n_warn else st.success)(f'Consistency: {n_ok} OK, {n_warn} warning(s), {n_fail} failure(s)')
        with st.expander('Show the checks', expanded=bool(n_fail or n_warn)): st.dataframe(chk, hide_index=True, use_container_width=True)
    d, meta = all_datasets(st, hub); cat = pd.DataFrame([{'Dataset': k, 'Rows': len(v), 'Columns': len(v.columns), 'Source': meta.get(k, {}).get('source', ''), 'Description': meta.get(k, {}).get('description', '')} for k, v in d.items()])
    st.dataframe(cat, hide_index=True, use_container_width=True)
    if d:
        sel = st.selectbox('Look at a table', list(d), key='hub_sel'); st.dataframe(d[sel], hide_index=True, use_container_width=True); table_actions(st, d[sel], sel, 'hub_view')


def _export_tab(st, nodes, hub):
    ss = st.session_state; d, meta = all_datasets(st, hub)
    if not d: st.info('Nothing to export yet.'); return
    default = [k for k in ('annual_field', 'forecast_field', 'annual_wells', 'annual_tanks', 'groups_annual', 'kpis') if k in d] + [k for k in d if k in shared_tables(st) or k.endswith('_shared')]
    names = st.multiselect('Tables to export', list(d), default=[k for k in dict.fromkeys(default)], key='exp_names'); sel = {k: d[k] for k in names}
    if not sel: st.info('Pick at least one table.'); return
    c1, c2, c3, c4 = st.columns(4)
    info = dict(hub.info); chk = None
    try: chk = dh.check_consistency(hub)
    except Exception: pass
    try: _xl = ex.to_excel(sel, meta, info, chk)
    except ImportError: _xl = None
    if _xl is None: c1.button('📗 Excel (openpyxl missing)', disabled=True, use_container_width=True, key='dl_xlsx', help='Add openpyxl to requirements.txt')
    else: c1.download_button('📗 Excel (.xlsx)', _xl, 'fieldnet_export.xlsx', MIME_XLSX, use_container_width=True, key='dl_xlsx')
    c2.download_button('🗜 CSV files (.zip)', ex.to_csv_zip(sel, meta, info), 'fieldnet_export_csv.zip', 'application/zip', use_container_width=True, key='dl_csv')
    c3.download_button('{ } JSON', ex.to_json(sel, meta, info), 'fieldnet_export.json', 'application/json', use_container_width=True, key='dl_json')
    c4.download_button('🔌 Local API bundle', ex.api_bundle(sel, meta, info), 'fieldnet_api_bundle.zip', 'application/zip', use_container_width=True, key='dl_api')
    st.markdown('---'); st.markdown('#### STEA-style yearly profile')
    st.caption('One row per series and one column per calendar year, with a unit and a total - the usual layout of profile files for economics tools. **The exact import format of your STEA / economics model is not known here**: '
               'edit the mapping below (series name, source table and column, scale, unit) to match your template and check the first import. Volumes are calendar-year sums.')
    base = pd.DataFrame(ss.get('stea_map') or ex.DEFAULT_STEA_MAPPING)
    cols = [c for c in d if 'Year' in d[c].columns]; st.caption('Source tables with a Year column: ' + (', '.join(cols) or 'none yet - run a forecast'))
    ed = st.data_editor(base, num_rows='dynamic', hide_index=True, use_container_width=True, key='stea_map_ed'); ss['stea_map'] = ed.to_dict('records')
    try:
        mp = ex.mapping_from_table(ed); tbl = ex.stea_table(d, mp)
        if len(tbl):
            st.dataframe(tbl, hide_index=True, use_container_width=True)
            if tbl.attrs.get('skipped'): st.warning('Skipped (source not available): ' + '; '.join(tbl.attrs['skipped']))
            x1, x2, x3 = st.columns(3)
            x1.download_button('STEA table CSV (; and decimal comma)', ex.stea_csv(d, mp), 'fieldnet_stea_profiles.csv', 'text/csv', use_container_width=True, key='dl_stea_csv')
            x2.download_button('STEA table CSV (, and decimal point)', ex.stea_csv(d, mp, sep=',', decimal='.'), 'fieldnet_stea_profiles_en.csv', 'text/csv', use_container_width=True, key='dl_stea_csv2')
            try: _sx = ex.to_excel({'profiles': tbl})
            except ImportError: _sx = None
            if _sx is None: x3.button('STEA Excel (openpyxl missing)', disabled=True, use_container_width=True, key='dl_stea_xlsx')
            else: x3.download_button('STEA table Excel', _sx, 'fieldnet_stea_profiles.xlsx', MIME_XLSX, use_container_width=True, key='dl_stea_xlsx')
        else: st.info('No series could be built - run a forecast (the yearly volumes come from it).')
    except ValueError as exc: st.error(str(exc))


def _pp_tab(st, hub):
    ss = st.session_state; d, meta = all_datasets(st, hub)
    st.caption('Write a short Python script to reshape results before exporting them. You get the tables in `ds` (copies), `pd`, `np`, `math`, `datetime`, `kpis`, and fill `out` with new tables. '
               'Results appear in the Data hub and in every export. Imports, file access and dunder tricks are blocked - this protects against mistakes, it is **not a security sandbox**, so only run scripts you trust. Your model is never changed.')
    ex_name = st.selectbox('Start from an example', ['(keep my script)'] + list(pp.EXAMPLES), key='pp_example')
    if ex_name != '(keep my script)' and ss.get('pp_loaded') != ex_name: ss['pp_src'] = pp.EXAMPLES[ex_name]; ss['pp_loaded'] = ex_name; st.rerun()
    src = st.text_area('Script', ss.get('pp_src', pp.EXAMPLES['Convert annual oil to MSm3']), height=260, key='pp_src')
    with st.expander('Tables available to the script (ds[...])'): st.write({k: list(v.columns)[:12] for k, v in d.items()})
    c1, c2 = st.columns(2)
    if c1.button('▶ Run script', type='primary', key='pp_run', use_container_width=True):
        try:
            kp = {r['KPI']: r['Value'] for r in hub.datasets['kpis'].to_dict('records')} if 'kpis' in hub.datasets else {}
            tables, log = pp.run(src, {k: v for k, v in d.items() if k not in (ss.get(EXTRA) or {})}, kp)
            ss[EXTRA] = {**(ss.get(EXTRA) or {}), **tables}; ss['pp_log'] = log; ss['pp_err'] = None; ss.pop('hub_cache', None); st.rerun()
        except pp.ScriptError as exc: ss['pp_err'] = str(exc)
    if c2.button('Remove post-processed tables', key='pp_clear', use_container_width=True): ss[EXTRA] = {}; ss.pop('hub_cache', None); st.rerun()
    if ss.get('pp_err'): st.error(ss['pp_err'])
    if ss.get('pp_log'): st.code(ss['pp_log'])
    for k, v in (ss.get(EXTRA) or {}).items(): st.markdown(f'**{k}** ({len(v)} rows)'); st.dataframe(v, hide_index=True, use_container_width=True)
    if ss.get(EXTRA): st.caption('These tables are now in the Export tab and the Data hub. Re-run the script after the model or forecast changes.')


def _api_tab(st, hub):
    d, meta = all_datasets(st, hub)
    st.markdown('The **local API bundle** is a zip with the tables as CSV, a manifest, and a tiny read-only web server (`serve.py`, Python standard library only, bound to 127.0.0.1) so scripts, Excel (Data > From Web) or Power BI on *your* computer can read the numbers. '
                'It is a snapshot of the results at export time - not a hosted service and not a live link to this app.')
    if d: st.download_button('🔌 Download the bundle (all tables)', ex.api_bundle(d, meta, hub.info), 'fieldnet_api_bundle.zip', 'application/zip', key='dl_api2')
    st.code("python serve.py            # then in another terminal:\ncurl http://127.0.0.1:8765/datasets\ncurl 'http://127.0.0.1:8765/datasets/annual_field?format=csv'", language='bash')
