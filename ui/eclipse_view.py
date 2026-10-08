"""Read Eclipse binary results (SMSPEC + UNSMRY, or UNRST) and offer them as a tank table or as well rates for the prediction."""
from __future__ import annotations
import re
import pandas as pd
from network import eclipse_io as E


def _classify(files):
    out = {'spec': None, 'smry': [], 'rst': []}
    for f in files:
        n = f.name.upper()
        if n.endswith('SMSPEC'): out['spec'] = f
        elif n.endswith('UNSMRY') or re.search(r'\.S\d{4}$', n) or n.endswith('.SMRY'): out['smry'].append(f)
        elif n.endswith('UNRST') or re.search(r'\.X\d{4}$', n): out['rst'].append(f)
    out['smry'].sort(key=lambda f: f.name.upper())
    return out


def render_eclipse_import(st, tid, tank_params, show_wells=True):
    """Returns nothing; writes rows to ``st.session_state['ps_ecl_tank_<tid>']`` / ``['ps_ecl_wells']`` for the Prediction-source page."""
    ss = st.session_state
    with st.expander('📂 Read Eclipse binary results (.SMSPEC + .UNSMRY, or .UNRST)', expanded=False):
        st.caption('Select the **.SMSPEC and .UNSMRY** files together (or the per-step .S0001, .S0002 … files) — or a **.UNRST** restart file for the average pressure. '
                   'Pick the vectors that drive the tank (pressure, water cut, GOR) and/or take the well rates (WOPR / WWPR / WGPR) as the simulator potential. '
                   'Field-unit files (psia, STB, MSCF) are converted to bar / Sm³. Only unformatted (binary) files are read.')
        files = st.file_uploader('Eclipse files', accept_multiple_files=True, key='ecl_files_' + tid)
        if not files: return
        c = _classify(files)
        df = None
        try:
            if c['spec'] is not None and c['smry']:
                df, spec = E.read_summary(c['spec'].getvalue(), [f.getvalue() for f in c['smry']])
                df = E.convert(df); st.success(f"Summary read: {len(df)} report steps, {len(df.columns) - 1} vectors, {df['Date'].iloc[0].date()} → {df['Date'].iloc[-1].date()} ({spec['unit_system'].lower()} units).")
                if df.attrs.get('unconverted'): st.caption('Units not recognised (left as they are): ' + ', '.join(f'{k} [{v}]' for k, v in list(df.attrs['unconverted'].items())[:8]))
            elif c['rst']:
                df = pd.concat([E.read_restart_pressure(f.getvalue()) for f in c['rst']]).drop_duplicates('Date').sort_values('Date').reset_index(drop=True)
                st.success(f"Restart file read: {len(df)} report steps (plain average of the PRESSURE array)."); df = df.rename(columns={'Average pressure [bar]': 'RESTART:PRESSURE'})
            else: st.info('Add the .SMSPEC together with its .UNSMRY (or S000n) file, or a .UNRST file.'); return
        except Exception as exc: st.error(f'Could not read the files: {exc}'); return
        ch = E.vector_choices(df); cols = [x for x in df.columns if x != 'Date']
        pres = [x for x in cols if x.split(':')[0] in ('FPR', 'RPR', 'RESTART', 'FPRP', 'WBHP', 'WBP9')] or cols
        a, b, d = st.columns(3)
        pc = a.selectbox('Tank pressure vector', pres, key='ecl_p_' + tid); wc = b.selectbox('Water cut vector (optional)', ['—'] + [x for x in cols if x.split(':')[0] in ('FWCT', 'WWCT', 'RWCT')], key='ecl_w_' + tid)
        gc = d.selectbox('GOR vector (optional)', ['—'] + [x for x in cols if x.split(':')[0] in ('FGOR', 'WGOR')], key='ecl_g_' + tid)
        every = st.number_input('Thin the table to one row every … days (0 = keep all)', 0, 3650, 0, 30, key='ecl_every_' + tid)
        try:
            import plotly.express as px
            st.plotly_chart(px.line(df, x='Date', y=pc, title=f'{pc} from the simulator', height=240), use_container_width=True, key='ecl_prev_' + tid)
        except Exception: pass
        if st.button('Use as this tank’s prediction table', type='primary', key='ecl_use_tank_' + tid):
            rows = E.tank_table(df, pc, None if wc == '—' else wc, None if gc == '—' else gc, int(every))
            ss['ps_ecl_tank_' + tid] = rows; st.success(f'{len(rows)} rows prepared — shown in the table below; press **Apply to tank** to commit them.')
        if show_wells and ch['well']:
            w_rows = E.well_rates(df)
            if w_rows:
                names = sorted({r['well'] for r in w_rows}); st.markdown(f'**Well rates found for {len(names)} well(s):** ' + ', '.join(names[:12]) + (' …' if len(names) > 12 else ''))
                if st.button('Use well rates as simulator potential', key='ecl_use_wells_' + tid):
                    ss['ps_ecl_wells'] = w_rows; st.success(f'{len(w_rows)} rate rows prepared — choose *Simulator well rates* below and press **Apply to selected wells**.')
