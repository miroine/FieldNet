"""Bulk editor for operating constraints and facility capacities."""
from __future__ import annotations
import pandas as pd
from ui.widgets import clean_num
from ui.run_button import style_form_submit, apply_notice

BOUNDARY = ('sink', 'separator', 'separator_stage', 'oil_export', 'gas_export', 'water_disposal')
COLS = ['Liquid capacity / max rate [Sm3/d]', 'Min pressure [bar]', 'Max pressure [bar]', 'Min BHP [bar]']


def constraint_table(nodes, edges):
    rows = []
    for n in nodes:
        k = n.get('kind'); p = n.get('params') or {}
        if k == 'reservoir': continue
        cap_key = 'max_liquid_rate_m3d' if (k in BOUNDARY or k == 'well') else None
        rows.append({'ID': n['id'], 'Name': n.get('name', n['id']), 'Type': k,
                     COLS[0]: p.get(cap_key) if cap_key else None, COLS[1]: p.get('min_pressure_bar'), COLS[2]: p.get('max_pressure_bar'),
                     COLS[3]: p.get('min_bhp_bar') if k == 'well' else None})
    for e in edges:
        p = e.get('params') or {}
        rows.append({'ID': e['id'], 'Name': e.get('name', e['id']), 'Type': e.get('kind', 'pipeline'), COLS[0]: p.get('max_rate_m3d'), COLS[1]: None, COLS[2]: None, COLS[3]: None})
    df = pd.DataFrame(rows, columns=['ID', 'Name', 'Type'] + COLS)
    for c in COLS: df[c] = pd.to_numeric(df[c], errors='coerce')
    return df


def apply_constraint_table(nodes, edges, df):
    """Write the table back. Blank = no constraint. Returns number of changed values."""
    changed = 0; byn = {n['id']: n for n in nodes}; bye = {e['id']: e for e in edges}
    def put(p, key, v):
        nonlocal changed
        old = p.get(key)
        if v is None:
            if key in p: p.pop(key); changed += 1
        elif old is None or abs(float(old) - v) > 1e-9:
            p[key] = v; changed += 1
    for r in df.to_dict('records'):
        rid = r.get('ID'); cap = clean_num(r.get(COLS[0])); cap = cap if (cap is None or cap > 0) else None
        if rid in byn:
            n = byn[rid]; p = n.setdefault('params', {}); k = n.get('kind')
            if k in BOUNDARY or k == 'well': put(p, 'max_liquid_rate_m3d', cap)
            put(p, 'min_pressure_bar', clean_num(r.get(COLS[1]))); put(p, 'max_pressure_bar', clean_num(r.get(COLS[2])))
            if k == 'well': put(p, 'min_bhp_bar', clean_num(r.get(COLS[3])))
        elif rid in bye:
            put(bye[rid].setdefault('params', {}), 'max_rate_m3d', cap)
    return changed


def render_constraint_editor(st, nodes, edges):
    st.markdown('**Edit constraints (bulk)**')
    st.caption('Blank = no limit. Liquid capacity applies to separators/exports (inflow), wells (rate cap, enforced in the well equation) and connections (maximum rate). Pressures in bar. Greyed cells do not apply to that component type.')
    df = constraint_table(nodes, edges)
    sig = str(abs(hash(df.to_json())))
    # A form: typing in the grid sends nothing to the server until Apply is pressed (no rerun per cell).
    with st.form('cons_form_' + sig, clear_on_submit=False, border=False):
        ed = st.data_editor(df, hide_index=True, use_container_width=True, key='cons_edit_' + sig, disabled=['ID', 'Name', 'Type'],
                            column_config={c: st.column_config.NumberColumn(min_value=0.0, format='%.1f') for c in COLS})
        go = st.form_submit_button('Apply constraints', type='primary', use_container_width=True)
    style_form_submit(st)
    if go:
        n = apply_constraint_table(nodes, edges, ed)
        st.session_state.cons_msg = (f'{n} constraint value(s) updated. Re-solve the network to evaluate them.', True) if n else ('No changes to apply.', False)
        st.rerun()
    if st.session_state.get('cons_msg'):
        msg, ok = st.session_state.pop('cons_msg'); apply_notice(st, msg, 'applied' if ok else 'pending')
