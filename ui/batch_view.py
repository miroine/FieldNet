"""Network tab: every input table in one place (batch editor, constraints, uptime, import / export)."""
from __future__ import annotations
import json
import pandas as pd
from network import batch_io as B
from ui.run_button import style_form_submit, apply_notice


def _invalidate(ss, reset=None):
    for k in ('hub_cache', 'forecast'): ss.pop(k, None)
    if reset: reset()
    else: ss['solve'] = None


def render_data_tables(st, nodes, edges, results=None, forecast=None, reset=None, on_project=None):
    ss = st.session_state
    st.subheader('Data tables — edit all inputs in one place')
    st.caption('Everything the layout holds, as tables: batch-edit any parameter for many elements, then press **Apply**. Constraints and uptime live here too. Values are in the model\'s canonical units '
               '(m, m³/d, Sm³/d, bar, °C); the column header is the parameter name.')
    t_batch, t_cons, t_up, t_io = st.tabs(['Batch editor', 'Constraints', 'Uptime', 'Import / export'])
    with t_batch: _batch(st, nodes, edges, reset)
    with t_cons:
        from ui.constraints_view import render_constraint_editor
        render_constraint_editor(st, nodes, edges)
    with t_up:
        from ui.availability_view import render_availability
        render_availability(st, nodes, edges, results, forecast, reset=reset)
    with t_io: _import_export(st, nodes, edges, reset, on_project)


def _batch(st, nodes, edges, reset):
    ss = st.session_state
    groups = [g for g in list(B.GROUPS) + ['Flowlines'] if B.members(nodes, edges, g)]
    if not groups: st.info('The model is empty.'); return
    c1, c2 = st.columns([1, 2])
    g = c1.selectbox('Element group', groups, key='bt_group')
    extra = list(ss.get('bt_extra', {}).get(g, []))
    with c2:
        a, b = st.columns([2, 1])
        opts = [k for k in B.CATALOG.get(g, [])]
        pick = a.selectbox('Add a column (parameter)', ['—'] + opts, key='bt_add_' + g)
        custom = a.text_input('…or type a parameter key', key='bt_custom_' + g, placeholder='e.g. min_oil_rate_m3d')
        if b.button('Add column', key='bt_addbtn_' + g, use_container_width=True):
            new = custom.strip() or (pick if pick != '—' else '')
            if new and new not in extra:
                extra.append(new); ss.setdefault('bt_extra', {})[g] = extra; st.rerun()
    df, cols = B.element_table(nodes, edges, g, extra)
    st.caption(f'{len(df)} element(s), {len(cols)} parameter column(s). Blank = not set (clearing a cell removes the parameter). Pasting a column from Excel works.')
    sig = str(abs(hash(df.to_json(default_handler=str))))
    fixed = [c for c in ('ID', 'Kind', 'From', 'To') if c in df.columns]
    with st.form(f'bt_form_{g}_{sig}', border=False):   # nothing is sent to the server while typing in the grid
        ed = st.data_editor(df, hide_index=True, use_container_width=True, key=f'bt_ed_{g}_{sig}', disabled=fixed)
        go = st.form_submit_button('Apply changes to the model', type='primary', use_container_width=True)
    style_form_submit(st)
    if go:
        n = B.apply_table(nodes, edges, g, ed, df)
        ss['bt_msg'] = (f'{n} value(s) updated. Re-solve the network and re-run the forecast.', True) if n else ('No changes to apply.', False)
        if n: _invalidate(ss, reset)
        st.rerun()
    if ss.get('bt_msg'):
        msg, ok = ss.pop('bt_msg'); apply_notice(st, msg, 'applied' if ok else 'pending')


def _import_export(st, nodes, edges, reset, on_project):
    ss = st.session_state
    st.markdown('**Import input data from a file**')
    st.caption('JSON, YAML, Excel (.xlsx; one sheet per group, or everything on one sheet) or CSV. A full project file replaces the model; any other file is merged into the existing elements by ID '
               '(or by name) — only the cells that are filled in are written. Tip: download the Excel below, edit it, upload it again.')
    up = st.file_uploader('Input file', type=['json', 'yaml', 'yml', 'xlsx', 'csv'], key='bt_upload')
    if up is not None:
        try: doc = B.read_file(up.name, up.getvalue())
        except ValueError as exc: st.error(str(exc)); doc = None
        if doc and doc['project'] is not None:
            pj = doc['project']; st.info(f"Project file: {len(pj['nodes'])} nodes, {len(pj['edges'])} connections.")
            if on_project and st.button('Replace the model with this project', type='primary', key='bt_proj', use_container_width=True):
                try: on_project(pj)
                except Exception as exc: st.error(f'Invalid project: {exc}')
        elif doc:
            for sh, d in doc['sheets'].items():
                with st.expander(f'Sheet “{sh}” — {len(d)} row(s), {len(d.columns)} column(s)'): st.dataframe(d.head(50), hide_index=True, use_container_width=True)
            if st.button('Merge into the model', type='primary', key='bt_merge', use_container_width=True):
                rep = B.merge_sheets(nodes, edges, doc['sheets'])
                _invalidate(ss, reset)
                st.success(f"{rep['matched']} of {rep['rows']} row(s) matched; {rep['values']} value(s) written.")
                if rep['unknown']: st.warning('Not found in the model: ' + ', '.join(rep['unknown'][:15]) + (' …' if len(rep['unknown']) > 15 else ''))
                if rep['ignored_columns']: st.caption('Ignored columns: ' + ', '.join(rep['ignored_columns']))
    st.markdown('**Export all inputs**')
    from ui.graph_contract import graph_hash
    h = graph_hash(nodes, edges); cache = ss.get('bt_export')
    if not cache or cache.get('hash') != h:          # building workbook / YAML on every page run is slow on big models: build on demand only
        if st.button('Prepare export files (Excel, CSV, YAML, JSON)', key='bt_prep', use_container_width=True):
            cache = {'hash': h}
            try: cache['xlsx'] = B.to_workbook(nodes, edges)
            except Exception as exc: cache['xlsx_err'] = str(exc)
            try: cache['yaml'] = B.to_yaml(nodes, edges)
            except Exception as exc: cache['yaml_err'] = str(exc)
            cache['json'] = json.dumps({'nodes': nodes, 'edges': edges}, indent=2, default=str)
            ss['bt_export'] = cache
        else:
            st.caption('Files are built when you press the button, so editing stays fast.'); return
    c1, c2, c3, c4 = st.columns(4)
    if 'xlsx' in cache: c1.download_button('Excel (all groups)', cache['xlsx'], 'fieldnet_inputs.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', use_container_width=True)
    else: c1.caption(f"Excel export unavailable: {cache.get('xlsx_err')}")
    grp = [g for g in list(B.GROUPS) + ['Flowlines'] if B.members(nodes, edges, g)]
    if grp:
        pg = c2.selectbox('CSV group', grp, key='bt_csvg', label_visibility='collapsed')
        c2.download_button('CSV (this group)', B.to_csv(nodes, edges, pg), f"fieldnet_{pg.lower().replace(' & ', '_').replace(' ', '_')}.csv", 'text/csv', use_container_width=True)
    if 'yaml' in cache: c3.download_button('YAML (project)', cache['yaml'], 'fieldnet_project.yaml', 'text/yaml', use_container_width=True)
    else: c3.caption(f"YAML export unavailable: {cache.get('yaml_err')}")
    c4.download_button('JSON (project)', cache['json'], 'fieldnet_project.json', 'application/json', use_container_width=True)
