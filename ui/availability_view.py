"""Availability & downtime page: uptime of wells, compressors, pumps, separators / hosts, lines ... and its effect on the prognosis."""
from __future__ import annotations
import pandas as pd
from network import availability as av


def render_availability(st, nodes, edges, results, forecast, reset=None):
    ss = st.session_state
    st.subheader('Availability & downtime')
    st.caption('Give any well, compressor, pump, separator / host, manifold or flowline an uptime (or MTBF / MTTR, or planned downtime per year). The forecast then delivers only what is up all the way '
               'to the sink: series elements multiply, parallel trains share the flow in proportion. Downtime defers production (the tank is depleted only by what is produced); '
               'the deferred volume is reported, not recovered later. Expected-value screening: for random outages use Tools → Reliability.')
    c1, c2, c3 = st.columns(3)
    if c1.button('Fill typical uptimes (empty only)', key='av_typical', use_container_width=True, help='Wells 95 %, compressors 94 %, pumps 95 %, separators 97 %, lines 99.5 %'):
        n = av.apply_typical(nodes, edges); ss.pop('hub_cache', None); ss.pop('forecast', None)
        if reset: reset()
        st.success(f'Set a typical uptime on {n} elements.'); st.rerun()
    if c2.button('Clear all downtime', key='av_clear', use_container_width=True):
        av.clear(nodes, edges); ss.pop('hub_cache', None); ss.pop('forecast', None)
        if reset: reset()
        st.rerun()
    reg = pd.DataFrame(av.register(nodes, edges))
    if reg.empty: st.info('No elements.'); return
    kinds = sorted(reg['Kind'].dropna().unique()); pick = st.multiselect('Show kinds', kinds, default=kinds, key='av_kinds')
    view = reg[reg['Kind'].isin(pick)]
    ed = st.data_editor(view, hide_index=True, use_container_width=True, key='av_editor', disabled=['Type', 'ID', 'Name', 'Kind', 'Effective uptime [%]'],
                        column_config={'Uptime [%]': st.column_config.NumberColumn(min_value=0.0, max_value=100.0, format='%.1f'), 'MTBF [d]': st.column_config.NumberColumn(min_value=0.0),
                                       'MTTR [d]': st.column_config.NumberColumn(min_value=0.0), 'Planned downtime [d/yr]': st.column_config.NumberColumn(min_value=0.0, max_value=365.0)})
    if c3.button('Apply to the model', key='av_apply', type='primary', use_container_width=True):
        n = av.apply_register(nodes, edges, ed.to_dict('records')); ss.pop('hub_cache', None); ss.pop('forecast', None)
        if reset: reset()
        st.success(f'{n} elements updated. Re-run the forecast.'); st.rerun()
    if not av.has_any(nodes, edges): st.info('No downtime set: the prognosis assumes 100 % uptime.'); 
    if results and results[0]:
        p, q, info, d = results; f = av.delivery_factors(nodes, edges, q); names = {n['id']: n.get('name', n['id']) for n in nodes}
        rows = [{'Well': names[w], 'Own uptime [%]': round(100 * av.uptime(next(n for n in nodes if n['id'] == w)), 2), 'Delivered fraction [%]': round(100 * f.get(w, 1.0), 2),
                 'Rate if all up [Sm³/d oil]': v.get('oil_rate_m3d'), 'Expected rate [Sm³/d oil]': (v.get('oil_rate_m3d') or 0) * f.get(w, 1.0),
                 'Gas if all up [MSm³/d]': (v.get('gas_rate_sm3d') or 0) / 1e6, 'Expected gas [MSm³/d]': (v.get('gas_rate_sm3d') or 0) * f.get(w, 1.0) / 1e6} for w, v in d.items()]
        if rows:
            df = pd.DataFrame(rows); st.markdown('**Expected delivery from the current solve** (the steady solve itself always shows the all-up rate)')
            st.dataframe(df, hide_index=True, use_container_width=True)
            tot0 = sum(r['Rate if all up [Sm³/d oil]'] or 0 for r in rows); tot1 = sum(r['Expected rate [Sm³/d oil]'] or 0 for r in rows); g0 = sum(r['Gas if all up [MSm³/d]'] for r in rows); g1 = sum(r['Expected gas [MSm³/d]'] for r in rows)
            ref = (tot1 / tot0) if tot0 > 0 else ((g1 / g0) if g0 > 0 else 1.0); st.metric('Field production efficiency (from this solve)', f'{100 * ref:.1f} %')
    rows = (forecast or {}).get('field') or []
    if rows and 'Uptime [%]' in rows[0]:
        df = pd.DataFrame(rows); st.markdown('**In the forecast**')
        dep = (df['Step [days]'] * df['Oil deferred [m3/d]']).sum(); depg = (df['Step [days]'] * df['Gas deferred [Sm3/d]']).sum()
        k = st.columns(3); k[0].metric('Average uptime', f"{df['Uptime [%]'].mean():.1f} %"); k[1].metric('Deferred oil / condensate', f'{dep / 1e6:,.3f} MSm³'); k[2].metric('Deferred gas', f'{depg / 1e9:,.3f} GSm³')
        import plotly.graph_objects as go
        from ui import charts
        fig = go.Figure(); fig.add_scatter(x=df['Date'], y=df['Uptime [%]'], mode='lines', name='Uptime', line=dict(color=charts.CATEGORICAL[0], width=2))
        st.plotly_chart(charts.style(fig, 'Production efficiency over time', 'Date', 'Uptime [%]', 300, legend=False), use_container_width=True, key='av_up_chart')
    elif forecast: st.caption('Re-run the forecast to see the effect of downtime.')
