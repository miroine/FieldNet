"""Production forecast (life-of-field) view."""
from __future__ import annotations
import pandas as pd
from network.forecast import run_forecast, iter_forecast
from network.run_control import RunController
from ui.run_button import style_button
from ui.results_browser import render_results_browser
from network.field_development import _coerce_value
from network.prognosis import forecast_kpis
from ui import charts
from ui.graph_contract import graph_hash
from ui.widgets import clean_text
from ui.schedule_builder import render_event_builder, events_to_forecast

DEFAULT_START = pd.Timestamp('2026-01-01').date()



def _step_solver(st):
    try:
        from network.solve_options import make_step_solver
        return make_step_solver(st.session_state.get('compute'))
    except Exception: return None


SPEEDS = {'Accurate (12 tubing segments)': None, 'Balanced (6 segments, ~2x faster)': 6, 'Fast (4 segments, ~2x faster, coarse)': 4}


def _workers(st):
    try: return max(1, int((st.session_state.get('compute') or {}).get('workers', 1)))
    except Exception: return 1


def fmt(v, unit='', scale=1.0, digits=0):
    if v is None: return '—'
    return f"{v/scale:,.{digits}f}{(' ' + unit) if unit else ''}"


def kpi_row(st, k):
    from network.phase_pref import current
    c = st.columns(6)
    if current(st) == 'Gas':
        c[0].metric('Peak gas', fmt(k.get('peak_gas_sm3d'), 'MSm³/d', 1e6, 2))
        c[1].metric('Plateau', fmt(k.get('plateau_gas_years'), 'yr', digits=1), help='Time with gas rate ≥ 90 % of peak')
        c[2].metric('Cumulative gas', fmt(k.get('cum_gas_sm3'), 'GSm³', 1e9, 2))
        c[3].metric('Recovery factor', fmt(k.get('rf_gas_pct'), '%', digits=1) if k.get('rf_gas_pct') is not None else '— (no gas tank)')
        c[4].metric('Cumulative condensate / oil', fmt(k.get('cum_oil_sm3'), 'MSm³', 1e6, 3))
        c[5].metric('Final water cut', fmt(k.get('final_water_cut_pct'), '%'))
        return
    c[0].metric('Peak oil', fmt(k.get('peak_oil_m3d'), 'Sm³/d'))
    c[1].metric('Plateau', fmt(k.get('plateau_years'), 'yr', digits=1), help='Time with oil rate ≥ 90 % of peak')
    c[2].metric('Cumulative oil', fmt(k.get('cum_oil_sm3'), 'MSm³', 1e6, 2))
    c[3].metric('Recovery factor', fmt(k.get('rf_oil_pct'), '%', digits=1) if k.get('rf_oil_pct') is not None else '— (no oil tank)')
    c[4].metric('Final water cut', fmt(k.get('final_water_cut_pct'), '%'))
    c[5].metric('Cumulative gas', fmt(k.get('cum_gas_sm3'), 'GSm³', 1e9, 2))


def profile_charts(st, fc, key=''):
    from network.phase_pref import current
    gas_first = current(st) == 'Gas'
    fdf = pd.DataFrame(fc['field']); wdf = pd.DataFrame(fc['wells']); tdf = pd.DataFrame(fc.get('tanks', []))
    fdf['Gas [MSm3/d]'] = fdf['Gas [Sm3/d]'] / 1e6; fdf['Cumulative gas [GSm3]'] = fdf['Cumulative gas [Sm3]'] / 1e9
    fdf['CGR [Sm3/MSm3]'] = (fdf['Oil [m3/d]'] / fdf['Gas [MSm3/d]'].where(fdf['Gas [MSm3/d]'] > 0)).fillna(0.0)
    liq = lambda: charts.lines(fdf, 'Date', ['Oil [m3/d]', 'Water [m3/d]', 'Total liquid [m3/d]', 'Water injection [m3/d]'], 'Liquid rates', 'Sm³/d',
                               colors={'Oil [m3/d]': charts.OIL, 'Water [m3/d]': charts.WATER, 'Total liquid [m3/d]': charts.LIQUID, 'Water injection [m3/d]': charts.INJ},
                               dash={'Total liquid [m3/d]': 'dot', 'Water injection [m3/d]': 'dash'})
    gas = lambda: charts.lines(fdf, 'Date', ['Gas [MSm3/d]'], 'Gas rate', 'MSm³/d', colors={'Gas [MSm3/d]': charts.GAS})
    cdf = fdf.assign(**{'Cumulative oil [MSm3]': fdf['Cumulative oil [Sm3]'] / 1e6})
    cum_oil = lambda: charts.lines(cdf, 'Date', ['Cumulative oil [MSm3]'], 'Cumulative oil', 'MSm³', colors={'Cumulative oil [MSm3]': charts.OIL})
    cum_gas = lambda: charts.lines(fdf, 'Date', ['Cumulative gas [GSm3]'], 'Cumulative gas', 'GSm³', colors={'Cumulative gas [GSm3]': charts.GAS})
    a, b = st.columns(2)
    (a.plotly_chart(gas(), use_container_width=True, key=f'fc_gas{key}'), b.plotly_chart(liq(), use_container_width=True, key=f'fc_liq{key}')) if gas_first else \
        (a.plotly_chart(liq(), use_container_width=True, key=f'fc_liq{key}'), b.plotly_chart(gas(), use_container_width=True, key=f'fc_gas{key}'))
    c, d = st.columns(2)
    c.plotly_chart(cum_gas() if gas_first else cum_oil(), use_container_width=True, key=f'fc_cum{key}')
    if not tdf.empty:
        d.plotly_chart(charts.by_category_lines(tdf, 'Date', 'Pressure [bar]', 'Tank', 'Reservoir pressure', 'bar'), use_container_width=True, key=f'fc_pr{key}')
    elif not wdf.empty:
        d.plotly_chart(charts.by_category_lines(wdf, 'Date', 'Reservoir pressure [bar]', 'Well', 'Reservoir pressure (per-well decline)', 'bar'), use_container_width=True, key=f'fc_pr{key}')
    e, f = st.columns(2)
    e.plotly_chart(charts.lines(fdf, 'Date', ['Water cut [%]'], 'Water cut', '%', colors={'Water cut [%]': charts.WATER}), use_container_width=True, key=f'fc_wc{key}')
    if gas_first: f.plotly_chart(charts.lines(fdf, 'Date', ['CGR [Sm3/MSm3]'], 'Condensate-gas ratio', 'Sm³/MSm³', colors={'CGR [Sm3/MSm3]': charts.OIL}), use_container_width=True, key=f'fc_gor{key}')
    else: f.plotly_chart(charts.lines(fdf, 'Date', ['GOR [Sm3/Sm3]'], 'Producing GOR', 'Sm³/Sm³', colors={'GOR [Sm3/Sm3]': charts.GAS}), use_container_width=True, key=f'fc_gor{key}')
    if not wdf.empty:
        if gas_first:
            w2 = wdf.assign(**{'Gas [MSm3/d]': wdf['Gas [Sm3/d]'] / 1e6}); st.plotly_chart(charts.stacked_area(w2, 'Date', 'Gas [MSm3/d]', 'Well', 'Gas rate by well', 'MSm³/d'), use_container_width=True, key=f'fc_wells{key}')
        else: st.plotly_chart(charts.stacked_area(wdf, 'Date', 'Oil [m3/d]', 'Well', 'Oil rate by well', 'Sm³/d'), use_container_width=True, key=f'fc_wells{key}')


def _drive_run(st, nodes, edges):
    """Progress bar + Pause / Continue / Stop controls for a running forecast (GAP-style). The partial snapshot is published
    after every timestep, so the charts below always show what has been computed so far."""
    ctl = st.session_state.get('fc_ctl')
    if ctl is None: return
    if ctl.active:
        b1, b2, b3 = st.columns([1, 1, 4])
        if ctl.status == ctl.RUNNING:
            if b1.button('⏸ Pause', key='fc_pause', use_container_width=True): ctl.pause()
        elif b1.button('▶ Continue', key='fc_continue', type='primary', use_container_width=True): ctl.resume()
        if b2.button('⏹ Stop', key='fc_stop', use_container_width=True): ctl.stop()
    bar = st.progress(ctl.fraction); msg = st.empty()
    if ctl.status == ctl.RUNNING:
        msg.caption('⏳ ' + ctl.describe() + '  — Pause to inspect results, Stop to keep what is computed.')
        while ctl.status == ctl.RUNNING:
            ev = ctl.advance(max_events=1)
            if ctl.snapshot is not None:
                st.session_state.forecast = ctl.snapshot; st.session_state.forecast_hash = st.session_state.get('fc_ctl_hash')
            bar.progress(ctl.fraction); msg.caption('⏳ ' + ctl.describe() + '  — Pause to inspect results, Stop to keep what is computed.')
        if ctl.status == ctl.FAILED: st.error(f'Forecast failed: {ctl.error}')
    if ctl.snapshot is not None:
        st.session_state.forecast = ctl.snapshot; st.session_state.forecast_hash = st.session_state.get('fc_ctl_hash')
    sink = getattr(ctl, 'sink', None)
    if sink and sink.get('res') is not None and ctl.status == ctl.DONE:
        st.session_state.sched_result = sink['res']; st.session_state.sched_base = sink.get('base'); st.session_state['_fc_mode'] = 'drill'
        st.session_state.forecast = sink['res']['forecast']
    n = len((ctl.snapshot or {}).get('field', []))
    if ctl.status == ctl.PAUSED: msg.info(f'⏸ Paused after {n} timestep(s) ({ctl.fraction:.0%}). Results below are partial — press Continue to resume from here.')
    elif ctl.status == ctl.STOPPED: msg.warning(f'⏹ Stopped by user after {n} timestep(s) ({ctl.fraction:.0%}). Results below are partial (forecast stops at the last completed step).')
    elif ctl.status == ctl.DONE: msg.success(f'Forecast complete: {n} timesteps in {ctl.t_run:.1f} s.')
    if ctl.status in (ctl.PAUSED, ctl.STOPPED) and st.session_state.get('fc_ctl_hash') != graph_hash(nodes, edges):
        st.warning('The model was edited after this run started; Continue resumes with the model as it was when the run started.')


def render_forecast(st, nodes, edges):
    st.subheader('Development schedule & production forecast')
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
        _tp = st.session_state.get('tpl_forecast') or {}
        try: _d0 = pd.Timestamp(_tp.get('start')).date() if _tp.get('start') else DEFAULT_START
        except Exception: _d0 = DEFAULT_START
        _steps = [30, 60, 90, 180, 365]
        start = a.date_input('Start date', value=_d0, key='fc_start').isoformat()
        years = b.number_input('Horizon [years]', 0.5, 50.0, float(_tp.get('years', 15.0)), 0.5, key='fc_years')
        step = c.selectbox('Report step [days]', _steps, index=_steps.index(_tp['step']) if _tp.get('step') in _steps else 2, key='fc_step', help='Tank depletion is sub-stepped automatically; the step sets the reporting interval.')
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
            st.caption('Shut in or start up wells, change rate limits, separator pressure, flowline diameter ... Pick the event, the element and the date; values use your unit profile. The same schedule is used by the Development schedule and Scenarios tabs.')
            sched = render_event_builder(st, nodes, edges, None, st.session_state.get('unit_profile', 'norwegian_si'), key_prefix='evb_fc', start_date=start)
        store_el = st.toggle('Store per-element profiles (slower, needed by the Element results tab)', value=True, key='fc_store_el')
        use_drill = st.toggle('Phase wells in with a drilling schedule', value=False, key='fc_use_drill',
                              help='Off: every well is on stream at the start date (plain production forecast). On: wells come on stream when their rig slot finishes, and the result is compared with having all wells at start.')
        rigs = drill_df = None; compare = False
        if use_drill:
            from ui import drilling_plan as dp
            rigs, drill_df = dp.table(st, nodes)
            compare = st.toggle('Compare with all wells on stream at start', value=True, key='sch_cmp')
        sp_name = st.selectbox('Run speed', list(SPEEDS), index=0, key='fc_speed',
                               help='Most of the run time is the well tubing (VLP) calculation, which grows with the number of wells. Fewer tubing segments is roughly twice as fast and changes rates by well under 1 % on test fields. Per-well "vlp_segments" settings are always kept.')
        vseg = SPEEDS.get(sp_name); wk = _workers(st)
        st.caption(f'Parallel: {wk} worker process(es) from the Compute settings — used for independent connected systems inside each step' + (' and to run the "all wells at start" comparison alongside the plan.' if use_drill and compare else '.') + ' A single connected network cannot be split across cores.')
        _ctl0 = st.session_state.get('fc_ctl')
        if _ctl0 is not None: style_button(st, 'fc_run', {'done': 'done', 'running': 'running', 'paused': 'running', 'failed': 'failed'}.get(_ctl0.status, 'none'))
        run = st.button('▶ Run forecast', type='primary', use_container_width=True, key='fc_run')
    if run and use_drill and drill_df is not None:
        from ui import drilling_plan as dp
        ids = {str(x.get('id')) for x in [*nodes, *edges]}; events = [e for e in events_to_forecast(sched) if e['target_id'] in ids]
        old = st.session_state.get('fc_ctl')
        if old is not None and old.active: old.stop()
        st.session_state['_fc_mode'] = 'plain'; st.session_state.pop('sched_result', None); st.session_state.pop('sched_base', None)
        sink = {}
        ctl = RunController(dp.iter_drill(nodes, edges, start, float(years), int(step), bool(caps), drill_df, rigs, events, compare, _step_solver(st), workers=wk, vlp_segments=vseg, sink=sink), label='Development plan')
        ctl.sink = sink; st.session_state.fc_ctl = ctl; st.session_state.fc_ctl_hash = graph_hash(nodes, edges)
    elif run:
        st.session_state['_fc_mode'] = 'plain'; st.session_state.pop('sched_result', None); st.session_state.pop('sched_base', None)
        ids = {str(x.get('id')) for x in [*nodes, *edges]}
        bad = [f'unknown target {e.target_id!r}' for e in sched if str(e.target_id) not in ids]
        events = [e for e in events_to_forecast(sched) if e['target_id'] in ids]
        if bad: st.error('Ignored events: ' + ', '.join(bad))
        old = st.session_state.get('fc_ctl')
        if old is not None and old.active: old.stop()
        st.session_state.fc_ctl = RunController(iter_forecast(nodes, edges, start, float(years), int(step), events, dep, enforce_constraints=bool(caps),
                                                              step_solver=_step_solver(st), store_elements=bool(store_el), workers=wk, vlp_segments=vseg))
        st.session_state.fc_ctl_hash = graph_hash(nodes, edges)
    _drive_run(st, nodes, edges)
    fc = st.session_state.get('forecast')
    if not (fc and fc.get('field')):
        st.info('Set the horizon and press **Run forecast**.'); return
    if st.session_state.get('forecast_hash') != graph_hash(nodes, edges): st.warning('The model changed since this forecast was run — results below are out of date.')
    k = forecast_kpis(fc)
    res_d = st.session_state.get('sched_result') if st.session_state.get('_fc_mode') == 'drill' else None
    if res_d:
        sch = pd.DataFrame(res_d['development_plan']['schedule'])
        if not sch.empty:
            sch = sch.assign(finish=[f if f > s0 else (pd.Timestamp(s0) + pd.Timedelta(days=1)).date().isoformat() for s0, f in zip(sch['start'], sch['finish'])])
            st.plotly_chart(charts.gantt(sch, 'Drilling schedule'), use_container_width=True, key='sch_gantt')
        st.markdown(f"**First production:** {k.get('first_oil') or '—'}")
    kpi_row(st, k)
    if res_d:
        phase_gas = __import__('network.phase_pref', fromlist=['current']).current(st) == 'Gas'
        col, unit, kk = ('Gas [Sm3/d]', 'MSm³/d', 1e6) if phase_gas else ('Oil [m3/d]', 'Sm³/d', 1.0)
        fdf0 = pd.DataFrame(fc['field']); comp = pd.DataFrame({'Date': fdf0['Date'], 'Scheduled': fdf0[col] / kk}); cols = ['Scheduled']; base = st.session_state.get('sched_base')
        if base:
            comp['All wells at start'] = pd.DataFrame(base['field'])[col].values[:len(comp)] / kk; cols.append('All wells at start')
            kb = forecast_kpis(base); key_c = 'cum_gas_sm3' if phase_gas else 'cum_oil_sm3'
            st.caption(f"Phasing the wells defers {fmt(kb.get(key_c, 0) - k.get(key_c, 0), 'GSm³' if phase_gas else 'MSm³', 1e9 if phase_gas else 1e6, 2)} of {'gas' if phase_gas else 'oil'} over the horizon compared with having every well on stream at start.")
        a, b = st.columns(2)
        a.plotly_chart(charts.lines(comp, 'Date', cols, f"{'Gas' if phase_gas else 'Oil'} rate: schedule vs all wells at start", unit, colors={'Scheduled': charts.GAS if phase_gas else charts.OIL, 'All wells at start': charts.LIQUID}, dash={'All wells at start': 'dot'}), use_container_width=True, key='sch_cmp_chart')
        b.plotly_chart(charts.lines(fdf0, 'Date', ['Wells flowing'], 'Producers on stream', 'wells', colors={'Wells flowing': charts.CATEGORICAL[0]}), use_container_width=True, key='sch_wells')
    nonconv = sum(1 for r in fc['field'] if not r.get('Converged'))
    if nonconv: st.warning(f'{nonconv} timestep(s) did not converge — check the Model assurance tab.')
    if k.get('peak_oil_m3d', 0) <= 0:
        st.error('No oil was produced. Typical causes: separator pressure too high for the wells to flow, wells not open, or wells connected to a tank with a pipeline (drag the tank onto the well instead).')
    profile_charts(st, fc)
    with st.expander('Browse results: network at a chosen date, any element over time', expanded=False):
        render_results_browser(st, nodes, edges, fc, key='fc_rb')
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
