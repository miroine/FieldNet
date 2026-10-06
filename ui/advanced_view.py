"""Advanced tab: calibration, lift-gas limit, equipment curves, fluid blending, flow assurance, back-allocation,
sensitivity, reliability-weighted prognosis, simulator link, correlation benchmark. All screening-level; each panel states its limits."""
from __future__ import annotations
import io, csv, contextlib
import pandas as pd
from ui.run_button import run_button

PANELS = ['Lift-gas limit', 'Pump / compressor curves', 'Fluid blending', 'Flow assurance (profile)', 'Back-allocation & tests',
          'Sensitivity', 'Reliability-weighted P10/P50/P90', 'Simulator link', 'Correlation benchmark']


def _f(v, fmt='{:,.0f}', unit=''):
    return '—' if v is None else fmt.format(v) + unit


def _mh(nodes, edges):
    from ui.graph_contract import graph_hash
    return graph_hash(nodes, edges)


def _wells(nodes): return [n for n in nodes if n.get('kind') == 'well']


def _need_solve(st, solved):
    if not solved: st.info('Solve the network first (Solve button above the network).'); return True
    return False


def _guard(st, fn, *a):
    try: fn(st, *a)
    except Exception as exc: st.error(f'{type(exc).__name__}: {exc}')


def _apply(st, nodes, edges=None):
    st.session_state.nodes = nodes
    if edges is not None: st.session_state.edges = edges
    st.rerun()


def calibration(st, nodes, edges, solved):
    st.caption('Fits a PI multiplier and a tubing-friction multiplier per well to well tests (screening, single-well IPR/VLP at the measured WHP). Wells whose fit hits a bound are flagged — a good fit does not prove the parameters are unique.')
    from network.well_test_calibration import calibrate_well_tests, generate_synthetic_tests
    ws = _wells(nodes)
    if not ws: st.info('No wells.'); return
    default = 'well_id,whp_bar,liquid_rate_m3d,bhp_bar\n'
    text = st.text_area('Well tests (CSV: well_id, whp_bar, liquid_rate_m3d, optional bhp_bar). Well ids: ' + ', '.join(w['id'] for w in ws), value=default, height=130, key='adv_cal_csv')
    if st.button('Insert example tests from the current model (synthetic)', key='adv_cal_syn'):
        syn = generate_synthetic_tests(nodes, edges)
        buf = io.StringIO(); w = csv.writer(buf, lineterminator='\n'); w.writerow(['well_id', 'whp_bar', 'liquid_rate_m3d', 'bhp_bar'])
        for t in syn: w.writerow([t.well_id, t.whp_bar, round(t.liquid_rate_m3d or 0, 2), '' if t.bhp_bar is None else round(t.bhp_bar, 2)])
        st.session_state.adv_cal_csv = buf.getvalue(); st.rerun()
    for rb in run_button(st, 'Calibrate', key='adv_cal_run', type='primary', model_hash=_mh(nodes, edges)):
        rows = list(csv.DictReader(io.StringIO(text)))
        tests = [{'well_id': r['well_id'].strip(), 'whp_bar': float(r['whp_bar']), 'liquid_rate_m3d': float(r['liquid_rate_m3d']),
                  **({'bhp_bar': float(r['bhp_bar'])} if (r.get('bhp_bar') or '').strip() else {})} for r in rows if (r.get('well_id') or '').strip()]
        st.session_state.adv_cal = calibrate_well_tests(nodes, edges, tests)
    res = st.session_state.get('adv_cal')
    if res:
        s = res['summary']; a, b, c = st.columns(3)
        a.metric('RMSE before', _f(s['before']['rmse_m3d'], '{:.1f}', ' m³/d')); b.metric('RMSE after', _f(s['after']['rmse_m3d'], '{:.1f}', ' m³/d')); c.metric('Wells at bound', len(s['wells_at_bound']))
        st.dataframe(pd.DataFrame(res['well_table']), hide_index=True, use_container_width=True)
        with st.expander('Per-test residuals'): st.dataframe(pd.DataFrame(res['test_table']), hide_index=True, use_container_width=True)
        for f in res['flags']: st.warning(f)
        if st.button('Apply tuned parameters to the model', key='adv_cal_apply'): _apply(st, res['nodes'], res['edges'])


def liftgas(st, nodes, edges, solved):
    st.caption('Shares a limited lift-gas supply among gas-lift wells by equal marginal oil gain (screening; uses full network solves).')
    from network.gaslift_constraint import allocate_with_lift_gas_limit
    tot = st.number_input('Field lift-gas supply [Sm³/d]', 0.0, 1e8, 200000.0, 10000.0, key='adv_lg_total')
    for rb in run_button(st, 'Allocate lift gas', key='adv_lg_run', type='primary', model_hash=_mh(nodes, edges)): st.session_state.adv_lg = allocate_with_lift_gas_limit(nodes, edges, float(tot))
    r = st.session_state.get('adv_lg')
    if r:
        if not r.get('success', True): st.warning(r.get('message', ''))
        a, b, c = st.columns(3)
        a.metric('Gain vs no lift', _f(r.get('gain_vs_none_m3d'), unit=' m³/d')); b.metric('Loss vs unlimited gas', _f(r.get('loss_vs_unlimited_m3d'), unit=' m³/d'))
        c.metric('Marginal value', _f(r.get('marginal_value_m3d_per_1000sm3d'), '{:,.2f}', ' m³/d per 1000 Sm³/d'))
        st.dataframe(pd.DataFrame(r.get('allocation_table', [])), hide_index=True, use_container_width=True)
        if st.button('Apply allocation to the model', key='adv_lg_apply') and r.get('nodes'): _apply(st, r['nodes'])


def curves(st, nodes, edges, solved):
    st.caption('Paste a manufacturer curve (CSV) onto an inline pump / compressor node; the solved flow is checked against minimum flow (surge), maximum flow (runout), best-efficiency range and head.')
    from network.equipment_curves import parse_curve_csv, check_network_equipment
    eq = [n for n in nodes if n.get('kind') in ('pump', 'compressor')]
    if not eq: st.info('Add an inline pump or compressor node to use curves.'); return
    n = st.selectbox('Equipment', eq, format_func=lambda x: f"{x.get('name', x['id'])} ({x['kind']})", key='adv_eq_pick')
    st.caption('Pump columns: rate_m3d, head_bar, efficiency[, power_kw][, speed]. Compressor columns: flow_sm3d, head_bar or pressure_ratio, efficiency[, speed].')
    txt = st.text_area('Curve CSV', value=(n.get('params') or {}).get('curve_csv', ''), height=140, key='adv_eq_csv_' + n['id'])
    if st.button('Save curve on node', key='adv_eq_save'):
        try: parse_curve_csv(txt, n['kind'], n.get('name', ''))
        except Exception as exc: st.error(f'Curve not parsable: {exc}')
        else:
            nn = [dict(x, params=dict(x.get('params') or {}, curve_csv=txt)) if x['id'] == n['id'] else x for x in nodes]; _apply(st, nn)
    if solved:
        rows = check_network_equipment(nodes, edges, solved)
        if rows: st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        else: st.caption('No equipment with a saved curve yet.')


def blending(st, nodes, edges, solved):
    st.caption('Blended oil/water/gas, GOR, water cut, API and gas gravity at every junction, manifold and separator, from the solved per-source flows.')
    if _need_solve(st, solved): return
    from network.fluid_blend import blend_by_node
    rows = blend_by_node(nodes, edges, solved)
    st.dataframe(pd.DataFrame(rows).drop(columns=['oil_kg_d', 'water_kg_d', 'gas_kg_d'], errors='ignore'), hide_index=True, use_container_width=True)


def assurance(st, nodes, edges, solved):
    st.caption('Hydrate margin, wax margin (if a wax appearance temperature is set on the line), erosional ratio and terrain-slugging screening along each flowline. Screening only — not a transient simulator.')
    if _need_solve(st, solved): return
    from network.flow_assurance_profile import assess_network
    res = assess_network(nodes, edges, solved)
    df = pd.DataFrame(res['rows'])
    if df.empty: st.info('No flowline carries flow.'); return
    bad = df[df['Status'].astype(str).str.upper().isin(['VIOLATED', 'WARNING', 'FAIL'])] if 'Status' in df else df.iloc[0:0]
    st.metric('Checks flagged', len(bad)); st.dataframe(df, hide_index=True, use_container_width=True)


def backalloc(st, nodes, edges, solved):
    st.caption('Shares metered (fiscal) totals over wells by test potential × uptime; allocated volumes always sum to the metered total.')
    from network.back_allocation import back_allocate, schedule_well_tests
    ws = _wells(nodes); det = (solved[3] if solved else {}) or {}
    base = [{'well': w['id'], 'oil': round(det.get(w['id'], {}).get('oil_rate_m3d', 0.0), 1), 'water': round(det.get(w['id'], {}).get('water_rate_m3d', 0.0), 1),
             'gas': round(det.get(w['id'], {}).get('gas_rate_sm3d', 0.0), 0), 'uptime': 1.0} for w in ws]
    tests = st.data_editor(pd.DataFrame(base), num_rows='dynamic', key='adv_ba_tests', use_container_width=True)
    tot = {k: sum(r[k] for r in base) for k in ('oil', 'water', 'gas')}
    c = st.columns(3)
    m = {'oil': c[0].number_input('Metered oil [Sm³/d]', 0.0, 1e9, float(tot['oil']) * 0.97, key='adv_ba_oil'), 'water': c[1].number_input('Metered water [m³/d]', 0.0, 1e9, float(tot['water']) * 0.97, key='adv_ba_wat'),
         'gas': c[2].number_input('Metered gas [Sm³/d]', 0.0, 1e12, float(tot['gas']) * 0.97, key='adv_ba_gas')}
    for rb in run_button(st, 'Back-allocate', key='adv_ba_run', type='primary', model_hash=_mh(nodes, edges)):
        t = [{k: r[k] for k in ('well', 'oil', 'water', 'gas')} for r in tests.to_dict('records') if r.get('well')]
        up = {r['well']: float(r.get('uptime', 1.0)) for r in tests.to_dict('records') if r.get('well')}
        res = back_allocate(m, t, up)
        for w in res.get('warnings', []): st.warning(w)
        a = res['allocated_rates'] if 'allocated_rates' in res else {}
        st.dataframe(pd.DataFrame(a).T.rename_axis('well').reset_index() if a else pd.DataFrame(), hide_index=True, use_container_width=True)
        st.caption('Allocation factor: ' + ', '.join(f"{k} {_f(v, '{:.3f}')}" for k, v in (res.get('allocation_factor') or {}).items()))
    st.markdown('**Well-test schedule**')
    a, b, c2 = st.columns(3)
    interval = a.number_input('Test interval [days]', 7, 365, 60, key='adv_ba_int'); cap = b.number_input('Test separator capacity [m³/d] (0 = none)', 0.0, 1e7, 0.0, key='adv_ba_cap'); per_day = c2.number_input('Max tests / day', 1, 10, 1, key='adv_ba_pd')
    for rb in run_button(st, 'Build test schedule', key='adv_ba_sched', model_hash=_mh(nodes, edges)):
        res = schedule_well_tests([{'id': w['id'], 'rate': det.get(w['id'], {}).get('liquid_rate_m3d', 0.0)} for w in ws], {}, int(interval), (cap or None), int(per_day))
        st.dataframe(pd.DataFrame(res['calendar']), hide_index=True, use_container_width=True)
        if res['skipped']: st.warning('Skipped (above test-separator capacity): ' + ', '.join(str(x['well']) for x in res['skipped']))


def sensitivity(st, nodes, edges, solved):
    st.caption('One-at-a-time tornado of total oil rate (each case is a full constrained solve; interactions are ignored). Replace the generic ± ranges by your own uncertainty.')
    from network.sensitivity import default_parameters, tornado, total_oil_rate
    rel = st.slider('Default range ± [%]', 5, 50, 20, key='adv_sens_rel') / 100.0
    params = default_parameters(nodes, edges, rel=rel)
    st.dataframe(pd.DataFrame([{'parameter': p.label, 'low': p.low, 'high': p.high, 'mode': p.mode} for p in params]), hide_index=True, use_container_width=True)
    wk = int((st.session_state.get('compute') or {}).get('workers', 1) or 1)
    for rb in run_button(st, 'Run tornado', key='adv_sens_run', type='primary', model_hash=_mh(nodes, edges)):
        st.session_state.adv_sens = tornado(nodes, edges, params, metric_fn=total_oil_rate, workers=wk, progress=lambda i, n: rb.progress(i / n, f'Solved case {i}/{n}'))
    rows = st.session_state.get('adv_sens')
    if rows:
        from ui import charts
        df = pd.DataFrame(rows)
        try:
            import plotly.graph_objects as go
            d = df.sort_values('swing'); fig = go.Figure()
            fig.add_bar(y=d['parameter'], x=d['delta_low'], orientation='h', name='low case'); fig.add_bar(y=d['parameter'], x=d['delta_high'], orientation='h', name='high case')
            fig.update_layout(barmode='relative', title='Δ oil rate vs base [Sm³/d]', height=340 + 18 * len(d)); st.plotly_chart(fig, use_container_width=True, key='adv_sens_fig')
        except Exception: pass
        st.dataframe(df, hide_index=True, use_container_width=True)


def reliability(st, nodes, edges, solved):
    st.caption('Applies random well/system downtime to the last forecast and reports P90 (low) / P50 / P10 (high) / mean rate and cumulative. Deferred production is not recovered and downtime does not feed back into depletion.')
    fc = st.session_state.get('forecast')
    if not (fc and fc.get('wells')): st.info('Run a forecast first.'); return
    from network.reliability_prognosis import uptime_weighted_profiles
    a, b, c = st.columns(3)
    av = a.slider('Well availability [%]', 50, 100, 92, key='adv_rel_av') / 100.0; sysav = b.slider('System (facility) availability [%]', 50, 100, 97, key='adv_rel_sys') / 100.0
    mode = c.selectbox('Downtime model', ['bernoulli', 'renewal'], key='adv_rel_mode', help='Renewal uses MTTR (5 d default) and gives realistic outage clustering.')
    n = st.number_input('Samples', 100, 5000, 500, 100, key='adv_rel_n')
    for rb in run_button(st, 'Compute', key='adv_rel_run', type='primary', model_hash=_mh(nodes, edges)):
        st.session_state.adv_rel = uptime_weighted_profiles(fc, av, int(n), 1234, mode, system_availability=sysav)
    r = st.session_state.get('adv_rel')
    if r:
        st.metric('Expected uptime factor', f"{r['expected_uptime_factor']:.3f}")
        try:
            import plotly.graph_objects as go
            for key, title in (('rate', 'Oil rate [Sm³/d]'), ('cumulative', 'Cumulative oil [Sm³]')):
                fig = go.Figure(); x = r['dates']
                for p, dash in (('P10', 'dot'), ('P50', 'solid'), ('P90', 'dot'), ('mean', 'dash')): fig.add_scatter(x=x, y=r[key][p], name=p, line=dict(dash=dash))
                fig.add_scatter(x=x, y=r['unconstrained'][key], name='no downtime', line=dict(color='grey', width=1))
                fig.update_layout(title=title, height=320); st.plotly_chart(fig, use_container_width=True, key='adv_rel_' + key)
        except Exception: pass


def simlink(st, nodes, edges, solved):
    st.caption('File-based exchange only (no live coupling). The VFP table layout is verified by parser round-trip, **not loaded in Eclipse** — check it in your simulator before use.')
    from network.simulator_link import export_vfp_prod, import_rate_schedule, export_forecast_rates, apply_rate_schedule
    ws = _wells(nodes)
    if ws:
        w = st.selectbox('Well', ws, format_func=lambda x: x.get('name', x['id']), key='adv_sim_well')
        a, b = st.columns(2)
        rates = [float(x) for x in a.text_input('Liquid rates [m³/d]', '100,500,1000,2000,3000', key='adv_sim_q').split(',') if x.strip()]
        whps = [float(x) for x in b.text_input('WHP [bar]', '10,20,30,50', key='adv_sim_whp').split(',') if x.strip()]
        a, b = st.columns(2)
        wcts = [float(x) for x in a.text_input('Water cut [-]', '0,0.3,0.6', key='adv_sim_wct').split(',') if x.strip()]
        gors = [float(x) for x in b.text_input('GOR [Sm³/Sm³]', '100,200', key='adv_sim_gor').split(',') if x.strip()]
        for rb in run_button(st, 'Build VFPPROD table', key='adv_sim_vfp', model_hash=_mh(nodes, edges)): st.session_state.adv_vfp = export_vfp_prod(w, rates, whps, wcts, gors)
        if st.session_state.get('adv_vfp'):
            st.download_button('Download VFPPROD (.inc)', st.session_state.adv_vfp, 'fieldnet_vfp.inc', 'text/plain', key='adv_sim_dl'); st.code(st.session_state.adv_vfp[:1500])
    fc = st.session_state.get('forecast')
    if fc and fc.get('wells'): st.download_button('Download FieldNet forecast rates (CSV)', export_forecast_rates(fc), 'fieldnet_forecast_rates.csv', 'text/csv', key='adv_sim_rates')
    up = st.file_uploader('Import simulator well rates (CSV: date, well, oil, water, gas[, pressure])', type=['csv'], key='adv_sim_up')
    if up is not None:
        rows = import_rate_schedule(up.getvalue().decode('utf-8-sig')); st.success(f'{len(rows)} rows for {len({r["well"] for r in rows})} wells.')
        if st.button('Use as prediction source on matching wells', key='adv_sim_apply'): _apply(st, apply_rate_schedule(nodes, rows))


def benchmark(st, nodes, edges, solved):
    st.caption('Mutual spread of the six multiphase correlations on reference cases. This is **not** validation against published data — see docs_correlation_validation.md.')
    from physics.correlation_benchmark import benchmark_table
    t = pd.DataFrame(benchmark_table())
    keep = [c for c in t.columns if c in ('Case', 'q_liq_m3d', 'length_m', 'diameter_m', 'Spread [%]') or c.endswith(' dp [bar]')]
    st.dataframe(t[keep], hide_index=True, use_container_width=True)


_FN = [liftgas, curves, blending, assurance, backalloc, sensitivity, reliability, simlink, benchmark]


def render_advanced(st, nodes, edges, solved):
    st.subheader('Advanced engineering tools')
    st.caption('Screening-level tools built on the solved network. Each states its assumptions; none replaces a validated simulator.')
    tabs = st.tabs(PANELS)
    for t, fn in zip(tabs, _FN):
        with t: _guard(st, fn, nodes, edges, solved)
