"""Prediction source page (GAP style): choose, per tank or per well, what drives the forecast —
the tank material balance, a decline curve, or numbers exported from a reservoir simulator."""
from __future__ import annotations
import pandas as pd
from network.prediction_assign import source_overview, assign_decline, clear_source, set_tank_mode
from ui.run_button import start_run
from ui.graph_contract import graph_hash


def render_prediction_sources(st, nodes, edges):
    st.subheader('Prediction source')
    st.caption('Decide what drives each reservoir in the forecast — like GAP\'s *prediction* setup. '
               '**Tank material balance** (default): pressure from in-place volume and produced volumes. '
               '**External simulator**: pressure / water cut / GOR follow a table exported from Eclipse, tNavigator, Intersect… (the tank balance still reports cumulatives and RF). '
               '**Decline curve** or **simulator rates** per well: limit each well to a potential profile; the well still follows IPR / VLP and the network constraints.')
    tanks = [n for n in nodes if n.get('kind') == 'reservoir']; wells = [n for n in nodes if n.get('kind') == 'well']
    if not wells: st.info('Add producers to the network first.'); return
    ss = st.session_state

    # ---- 1. overview -----------------------------------------------------------------------
    st.markdown('#### Current setup')
    ov = pd.DataFrame(source_overview(nodes))
    trows = [{'Tank': t.get('name', t['id']), 'Mode': 'External simulator table' if (t.get('params') or {}).get('prediction_mode') == 'external' else 'Material balance',
              'Table rows': len((t.get('params') or {}).get('external_table') or [])} for t in tanks]
    if trows: st.dataframe(pd.DataFrame(trows), hide_index=True, use_container_width=True)
    st.dataframe(ov.drop(columns=['ID']), hide_index=True, use_container_width=True)

    # ---- 2. tanks --------------------------------------------------------------------------
    if tanks:
        st.markdown('#### Tanks: material balance or simulator table')
        names = {t['id']: t.get('name', t['id']) for t in tanks}
        tid = st.selectbox('Tank', list(names), format_func=names.get, key='ps_tank')
        tank = next(t for t in tanks if t['id'] == tid); prm = tank.get('params') or {}
        mode = st.radio('Drive this tank by', ['material_balance', 'external'], index=1 if prm.get('prediction_mode') == 'external' else 0, horizontal=True,
                        format_func={'material_balance': 'Material balance', 'external': 'External simulator table'}.get, key='ps_mode_' + tid)
        if mode == 'external':
            up = st.file_uploader('Simulator export (CSV: date or time_days, reservoir pressure; optional water cut, GOR)', type=['csv', 'txt'], key='ps_up_' + tid)
            rows = list(prm.get('external_table') or [])
            if up is not None:
                from network.prediction_sources import parse_external_csv
                try:
                    rows, warns = parse_external_csv(up.getvalue().decode('utf-8', 'ignore'))
                    for w in warns: st.caption('ℹ ' + str(w))
                except Exception as exc: st.error(str(exc))
            ed = st.data_editor(pd.DataFrame(rows or [{'date': '2026-01-01', 'reservoir_pressure_bar': float(prm.get('reservoir_pressure_bar', 250.0))}]), num_rows='dynamic', use_container_width=True, key=f'ps_tbl_{tid}_{len(rows)}')
            clean = [{k: v for k, v in r.items() if v is not None and str(v) not in ('', 'nan', 'NaT')} for r in ed.to_dict('records')]
            clean = [r for r in clean if r]
            if clean:
                try:
                    import plotly.express as px
                    d = pd.DataFrame(clean); x = 'date' if 'date' in d else 'time_days'
                    if x in d and 'reservoir_pressure_bar' in d: st.plotly_chart(px.line(d, x=x, y='reservoir_pressure_bar', markers=True, title='Tank pressure delivered by the table', height=260), use_container_width=True, key='ps_prev_' + tid)
                except Exception: pass
        rb = start_run(st, 'Apply to tank', key='ps_apply_tank', model_hash=graph_hash(nodes, edges))
        if rb:
            try:
                ss.nodes = set_tank_mode(nodes, tid, mode, clean if mode == 'external' else None); rb.finish(); st.rerun()
            except ValueError as exc: rb.fail(str(exc)); rb.finish()

    # ---- 3. wells --------------------------------------------------------------------------
    st.markdown('#### Wells: decline curve or simulator rates')
    wn = {w['id']: w.get('name', w['id']) for w in wells}
    sel = st.multiselect('Wells', list(wn), default=list(wn), format_func=wn.get, key='ps_wells')
    kind = st.radio('Source for the selected wells', ['decline', 'rates', 'none'], horizontal=True, key='ps_kind',
                    format_func={'decline': 'Decline curve', 'rates': 'Simulator well rates (CSV)', 'none': 'Back to tank balance / IPR'}.get)
    if kind == 'decline':
        a, b, c = st.columns(3)
        basis = a.selectbox('Basis', ['oil', 'liquid', 'gas'], key='ps_basis'); qi = b.number_input('Initial rate, whole selection [m³/d or Sm³/d]', 0.0, 1e9, 1500.0, 50.0, key='ps_qi')
        split = c.selectbox('Share between wells', ['equal', 'pi'], format_func={'equal': 'Equal', 'pi': 'By productivity index'}.get, key='ps_split')
        a, b, c, d = st.columns(4)
        di = a.number_input('Decline Di [1/yr]', 0.0, 10.0, 0.25, 0.01, key='ps_di'); bb = b.number_input('b factor', 0.0, 1.0, 0.5, 0.05, key='ps_b')
        qa = c.number_input('Abandonment rate', 0.0, 1e9, 50.0, 10.0, key='ps_qa'); dt = d.number_input('Terminal Di [1/yr] (0 = none)', 0.0, 5.0, 0.06, 0.01, key='ps_dt')
        try:
            import plotly.express as px
            from network.prediction_sources import prediction_preview
            pv = pd.DataFrame(prediction_preview({'type': 'decline', 'basis': basis, 'qi': qi, 'di_per_year': di, 'b': bb, 'q_abandon': qa, 'terminal_di_per_year': dt or None}, '2026-01-01', 15, 90))
            if not pv.empty: st.plotly_chart(px.line(pv, x='Date', y=pv.columns[2], title='Selection potential', height=240), use_container_width=True, key='ps_decl_prev')
        except Exception: pass
    elif kind == 'rates':
        st.caption('CSV columns: date, well, oil, water, gas[, pressure]. Wells are matched by node id or name; the rates become each well\'s potential cap with water cut and GOR.')
        up = st.file_uploader('Simulator well rates', type=['csv'], key='ps_rates_up')
    rb = start_run(st, 'Apply to selected wells', key='ps_apply_wells', type='primary', model_hash=graph_hash(nodes, edges), disabled=not sel)
    if rb:
        try:
            if kind == 'decline': ss.nodes = assign_decline(nodes, sel, qi, di, bb, basis, split, qa or None, dt or None)
            elif kind == 'none': ss.nodes = clear_source(nodes, sel)
            else:
                from network.simulator_link import import_rate_schedule, apply_rate_schedule
                if up is None: raise ValueError('Upload a rates CSV first')
                ss.nodes = apply_rate_schedule(nodes, import_rate_schedule(up.getvalue().decode('utf-8-sig')))
            rb.finish(); st.rerun()
        except Exception as exc: rb.fail(str(exc)); rb.finish()
    st.caption('Fine-tune one well (qi, Di, b, table rows, water-cut / GOR tables) in the Network tab → select the well → *Prediction source*.')
