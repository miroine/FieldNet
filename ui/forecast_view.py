"""Production forecast (life-of-field) view."""
from __future__ import annotations
import pandas as pd
from network.forecast import run_forecast
from network.field_development import _coerce_value
from network.prognosis import forecast_kpis
from ui import charts
from ui.graph_contract import graph_hash
from ui.widgets import clean_text

DEFAULT_START = pd.Timestamp('2026-01-01').date()


def fmt(v, unit='', scale=1.0, digits=0):
    if v is None: return '—'
    return f"{v/scale:,.{digits}f}{(' ' + unit) if unit else ''}"


def kpi_row(st, k):
    c = st.columns(6)
    c[0].metric('Peak oil', fmt(k.get('peak_oil_m3d'), 'Sm³/d'))
    c[1].metric('Plateau', fmt(k.get('plateau_years'), 'yr', digits=1), help='Time with oil rate ≥ 90 % of peak')
    c[2].metric('Cumulative oil', fmt(k.get('cum_oil_sm3'), 'MSm³', 1e6, 2))
    c[3].metric('Recovery factor', fmt(k.get('rf_oil_pct'), '%', digits=1) if k.get('rf_oil_pct') is not None else '— (no oil tank)')
    c[4].metric('Final water cut', fmt(k.get('final_water_cut_pct'), '%'))
    c[5].metric('Cumulative gas', fmt(k.get('cum_gas_sm3'), 'GSm³', 1e9, 2))


def profile_charts(st, fc, key=''):
    fdf = pd.DataFrame(fc['field']); wdf = pd.DataFrame(fc['wells']); tdf = pd.DataFrame(fc.get('tanks', []))
    a, b = st.columns(2)
    a.plotly_chart(charts.lines(fdf, 'Date', ['Oil [m3/d]', 'Water [m3/d]', 'Total liquid [m3/d]', 'Water injection [m3/d]'], 'Liquid rates', 'Sm³/d',
                                colors={'Oil [m3/d]': charts.OIL, 'Water [m3/d]': charts.WATER, 'Total liquid [m3/d]': charts.LIQUID, 'Water injection [m3/d]': charts.INJ},
                                dash={'Total liquid [m3/d]': 'dot', 'Water injection [m3/d]': 'dash'}), use_container_width=True, key=f'fc_liq{key}')
    b.plotly_chart(charts.lines(fdf, 'Date', ['Gas [Sm3/d]'], 'Gas rate', 'Sm³/d', colors={'Gas [Sm3/d]': charts.GAS}), use_container_width=True, key=f'fc_gas{key}')
    c, d = st.columns(2)
    cdf = fdf.assign(**{'Cumulative oil [MSm3]': fdf['Cumulative oil [Sm3]'] / 1e6})
    c.plotly_chart(charts.lines(cdf, 'Date', ['Cumulative oil [MSm3]'], 'Cumulative oil', 'MSm³', colors={'Cumulative oil [MSm3]': charts.OIL}), use_container_width=True, key=f'fc_cum{key}')
    if not tdf.empty:
        d.plotly_chart(charts.by_category_lines(tdf, 'Date', 'Pressure [bar]', 'Tank', 'Reservoir pressure', 'bar'), use_container_width=True, key=f'fc_pr{key}')
    elif not wdf.empty:
        d.plotly_chart(charts.by_category_lines(wdf, 'Date', 'Reservoir pressure [bar]', 'Well', 'Reservoir pressure (per-well decline)', 'bar'), use_container_width=True, key=f'fc_pr{key}')
    e, f = st.columns(2)
    e.plotly_chart(charts.lines(fdf, 'Date', ['Water cut [%]'], 'Water cut', '%', colors={'Water cut [%]': charts.WATER}), use_container_width=True, key=f'fc_wc{key}')
    f.plotly_chart(charts.lines(fdf, 'Date', ['GOR [Sm3/Sm3]'], 'Producing GOR', 'Sm³/Sm³', colors={'GOR [Sm3/Sm3]': charts.GAS}), use_container_width=True, key=f'fc_gor{key}')
    if not wdf.empty:
        st.plotly_chart(charts.stacked_area(wdf, 'Date', 'Oil [m3/d]', 'Well', 'Oil rate by well', 'Sm³/d'), use_container_width=True, key=f'fc_wells{key}')


def render_forecast(st, nodes, edges):
    st.subheader('Production forecast')
    st.caption('Quasi-steady life-of-field prognosis: every step re-solves the full network with tank pressures from material balance (in-place volume, fluid phase, aquifer and injection support). Screening model, not a reservoir simulator.')
    wells = [n for n in nodes if n.get('kind') == 'well']; tanks = [n for n in nodes if n.get('kind') == 'reservoir']
    linked = [w for w in wells if (w.get('params') or {}).get('reservoir_id') in {t['id'] for t in tanks}]
    if not wells:
        st.info('Add producers to the network to run a forecast.'); return
    if not tanks:
        st.warning('No reservoir tank in the model — wells deplete with a per-well decline coefficient. For a proper prognosis add a **Reservoir tank** (in-place volume + fluid phase) and drag it onto the wells it drains.')
    elif len(linked) < len(wells):
        st.info(f"{len(wells)-len(linked)} producer(s) are not assigned to a tank and use the per-well decline coefficient: " + ', '.join(w['name'] for w in wells if w not in linked))
    with st.container(border=True):
        a, b, c, d = st.columns(4)
        start = a.date_input('Start date', value=DEFAULT_START, key='fc_start').isoformat()
        years = b.number_input('Horizon [years]', 0.5, 50.0, 15.0, 0.5, key='fc_years')
        step = c.selectbox('Report step [days]', [30, 60, 90, 180, 365], index=2, key='fc_step', help='Tank depletion is sub-stepped automatically; the step sets the reporting interval.')
        caps = d.toggle('Honour facility capacities', value=True, key='fc_caps')
        dep = {}
        unlinked = [w for w in wells if w not in linked]
        if unlinked:
            with st.expander('Per-well decline (wells without a tank)'):
                for w in unlinked:
                    c1, c2 = st.columns(2)
                    dep[w['id']] = {'pressure_decline_bar_per_1000m3': c1.number_input(f"{w['name']} decline [bar/1000 m³]", 0.0, 10.0, float((w.get('params') or {}).get('pressure_decline_bar_per_1000m3', 0.03)), 0.01, key='dec' + w['id']),
                                    'pressure_support_bar_per_day': c2.number_input(f"{w['name']} support [bar/day]", 0.0, 5.0, float((w.get('params') or {}).get('pressure_support_bar_per_day', 0.0)), 0.001, key='sup' + w['id'])}
        with st.expander('Schedule events (optional)'):
            st.caption('date (YYYY-MM-DD) · target (ID) · field, e.g. `params.available`, `pressure_bar`, `params.max_liquid_rate_m3d` · value')
            evdf = st.data_editor(pd.DataFrame({'date': pd.Series(dtype=str), 'target_id': pd.Series(dtype=str), 'field': pd.Series(dtype=str), 'value': pd.Series(dtype=str)}),
                                  num_rows='dynamic', use_container_width=True, key='forecast_events')
        run = st.button('▶ Run forecast', type='primary', use_container_width=True, key='fc_run')
    if run:
        events = []; bad = []; ids = {str(x.get('id')) for x in [*nodes, *edges]}
        for row in evdf.to_dict('records'):
            dte, tid, fld = clean_text(row.get('date')), clean_text(row.get('target_id')), clean_text(row.get('field'))
            if not (dte and tid and fld): continue
            try: dte = pd.Timestamp(dte).date().isoformat()
            except Exception: bad.append(f'bad date {dte!r}'); continue
            if tid not in ids: bad.append(f'unknown target {tid!r}'); continue
            events.append({'date': dte, 'target_id': tid, 'field': fld, 'value': _coerce_value(row.get('value'))})
        if bad: st.error('Ignored events: ' + ', '.join(bad))
        with st.spinner('Forecasting: re-solving the network at every step...'):
            try:
                st.session_state.forecast = run_forecast(nodes, edges, start, float(years), int(step), events, dep, enforce_constraints=bool(caps))
                st.session_state.forecast_hash = graph_hash(nodes, edges)
            except Exception as exc: st.error(f'Forecast failed: {exc}')
    fc = st.session_state.get('forecast')
    if not (fc and fc.get('field')):
        st.info('Set the horizon and press **Run forecast**.'); return
    if st.session_state.get('forecast_hash') != graph_hash(nodes, edges): st.warning('The model changed since this forecast was run — results below are out of date.')
    k = forecast_kpis(fc); kpi_row(st, k)
    nonconv = sum(1 for r in fc['field'] if not r.get('Converged'))
    if nonconv: st.warning(f'{nonconv} timestep(s) did not converge — check the Model assurance tab.')
    if k.get('peak_oil_m3d', 0) <= 0:
        st.error('No oil was produced. Typical causes: separator pressure too high for the wells to flow, wells not open, or wells connected to a tank with a pipeline (drag the tank onto the well instead).')
    profile_charts(st, fc)
    if fc.get('recovery'):
        st.markdown('**Recovery by reservoir tank**')
        rdf = pd.DataFrame(fc['recovery'])
        st.dataframe(rdf.drop(columns=['Tank ID'], errors='ignore').style.format({c: '{:,.0f}' for c in rdf.columns if c.startswith('Cum') or 'influx' in c} | {'Pressure [bar]': '{:.1f}', 'RF oil [%]': '{:.1f}', 'RF gas [%]': '{:.1f}'}), hide_index=True, use_container_width=True)
    with st.expander('Timestep table & downloads'):
        fdf = pd.DataFrame(fc['field']); st.dataframe(fdf, hide_index=True, use_container_width=True)
        d1, d2 = st.columns(2)
        d1.download_button('Field profile CSV', fdf.to_csv(index=False), 'fieldnet_forecast_field.csv', 'text/csv', use_container_width=True)
        d2.download_button('Well profiles CSV', pd.DataFrame(fc['wells']).to_csv(index=False), 'fieldnet_forecast_wells.csv', 'text/csv', use_container_width=True)
    if fc.get('constraints'):
        with st.expander('Constraint history'):
            st.dataframe(pd.DataFrame(fc['constraints']), hide_index=True, use_container_width=True)
