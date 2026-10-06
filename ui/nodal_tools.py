"""Nodal-analysis extras for the selected well: input uncertainty (what-if sliders + Monte-Carlo fan with a percentile slider),
matching to measured well tests / flowing-gradient surveys, and blowout (worst-case discharge) screening.

All numbers here are canonical units (bar, m3/d, Sm3/d, m) so a table can be pasted into a report without conversion."""
from __future__ import annotations
import copy
import numpy as np, pandas as pd
from network import nodal_uncertainty as nu
from physics import well_match as wm, blowout as bo
from physics.well_model import well_settings, ipr_pwf, vlp_bhp
from ui.run_button import run_button
from ui.hub_access import table_actions
from ui import charts

PCTS = {'P90 (low case)': 90, 'P75': 75, 'P50 (median)': 50, 'P25': 25, 'P10 (high case)': 10}


def _axis(st):
    """(factor, label): liquid rate [m3/d] on the x axis for an oil field, gas rate [MSm3/d] for a gas field (gas = liquid x (1 - WC) x GOR)."""
    return st.session_state.get('_nodal_axis') or (1.0, 'Liquid rate [m³/d]')


def _set_axis(st, prm):
    from network.phase_pref import current
    if current(st) == 'Gas':
        from physics.well_model import well_settings
        ws = well_settings(prm); st.session_state['_nodal_axis'] = ((1.0 - ws['water_cut']) * ws['gor'] / 1e6, 'Gas rate [MSm³/d]')
    else: st.session_state['_nodal_axis'] = (1.0, 'Liquid rate [m³/d]')


def _go():
    import plotly.graph_objects as go; return go


def _curves_fig(curves, title, op=None, points=None, height=420, ax=(1.0, 'Liquid rate [m³/d]')):
    """curves: list of (name, q, ipr, vlp, style) ."""
    go = _go(); fig = go.Figure(); k = ax[0]
    for name, q, ipr, vlp, sty in curves:
        q = np.asarray(q) * k; keep = np.asarray(ipr) >= 0
        fig.add_trace(go.Scatter(x=np.asarray(q)[keep], y=np.asarray(ipr)[keep], mode='lines', name=f'IPR {name}', line=dict(color=charts.OIL, width=sty.get('w', 2), dash=sty.get('dash', 'solid'))))
        fig.add_trace(go.Scatter(x=q, y=vlp, mode='lines', name=f'VLP {name}', line=dict(color=charts.CATEGORICAL[0], width=sty.get('w', 2), dash=sty.get('dash', 'solid'))))
    if op:
        for name, (qq, pp) in op.items():
            if qq and qq > 0: fig.add_trace(go.Scatter(x=[qq * k], y=[pp], mode='markers', name=f'Operating point {name}', marker=dict(size=11, color=charts.GAS, line=dict(width=2, color='white'))))
    if points is not None and len(points):
        fig.add_trace(go.Scatter(x=np.asarray(points['Rate [m3/d]']) * k, y=points['BHP [bar]'], mode='markers', name='Measured', marker=dict(size=10, symbol='diamond', color='#000')))
    return charts.style(fig, title, 'Bottom-hole pressure [bar]', ax[1], height)


def render_nodal_tools(st, wid, name, prm, whp, nodes, edges, model_hash, reset=None):
    ss = st.session_state; _set_axis(st, prm)
    t_unc, t_match, t_blow = st.tabs(['Uncertainty & what-if', 'Match to measured data', 'Blowout / worst-case discharge'])
    with t_unc: _uncertainty(st, wid, name, prm, whp, model_hash)
    with t_match: _matching(st, wid, name, prm, whp, nodes, reset)
    with t_blow: _blowout(st, wid, name, prm, nodes, model_hash)


# ----------------------------------------------------------------------------- uncertainty
def _uncertainty(st, wid, name, prm, whp, model_hash):
    ss = st.session_state
    st.caption('Change inputs and see the inflow / outflow curves and the operating point respond (what-if), or give each input a range and run a Monte-Carlo to get the spread of curves and rates. '
               'Values are canonical units. The model itself is not changed.')
    keys = list(nu.CATALOGUE)
    chosen = st.multiselect('Inputs to vary', keys, default=['reservoir_pressure_bar', 'productivity', 'skin', 'water_cut', 'whp', 'vlp_dp_multiplier'], format_func=lambda k: nu.CATALOGUE[k][0], key=f'nu_keys_{wid}')
    if not chosen: st.info('Pick at least one input.'); return
    specs = [nu.default_spec(prm, whp, k) for k in chosen]
    with st.expander('Ranges (low / most likely / high) - triangular distributions', expanded=False):
        df = pd.DataFrame([{'Input': s['label'], 'Unit': s['unit'], 'Low': s['low'], 'Most likely': s['mode'], 'High': s['high']} for s in specs])
        ed = st.data_editor(df, hide_index=True, disabled=['Input', 'Unit'], use_container_width=True, key=f'nu_rng_{wid}_{"_".join(chosen)}')
        for s, r in zip(specs, ed.to_dict('records')):
            lo, md, hi = float(r['Low']), float(r['Most likely']), float(r['High']); lo, hi = min(lo, hi), max(lo, hi); s.update({'low': lo, 'high': hi, 'mode': min(max(md, lo), hi)})
    st.markdown('**What-if sliders**')
    cols = st.columns(min(3, len(specs))); vals = {}
    for i, s in enumerate(specs):
        lo, hi = float(s['low']), float(s['high'])
        if hi <= lo: hi = lo + 1e-9
        step = (hi - lo) / 100.0
        vals[s['key']] = cols[i % len(cols)].slider(f"{s['label']} [{s['unit']}]", lo, hi, float(min(max(s['mode'], lo), hi)), step, key=f'nu_sl_{wid}_{s["key"]}')
    base_p, base_w = prm, whp; wi_p, wi_w = nu.apply_overrides(prm, whp, vals)
    cap = nu.q_grid([base_p, wi_p]); b_i, b_v = nu.curves(base_p, base_w, cap); w_i, w_v = nu.curves(wi_p, wi_w, cap); ob = nu.operating_point(base_p, base_w); ow = nu.operating_point(wi_p, wi_w)
    st.plotly_chart(_curves_fig([('base', cap, b_i, b_v, {'dash': 'dot', 'w': 1.5}), ('what-if', cap, w_i, w_v, {})], f'{name}: base case vs what-if', op={'base': (ob['q_liq'], ob['bhp']), 'what-if': (ow['q_liq'], ow['bhp'])}, ax=_axis(st)), use_container_width=True, key=f'nu_whatif_{wid}')
    a, b, c = st.columns(3); a.metric('Liquid rate', f"{ow['q_liq']:,.0f} m³/d", f"{ow['q_liq'] - ob['q_liq']:+,.0f} vs base"); b.metric('Oil rate', f"{ow['q_oil']:,.0f} Sm³/d", f"{ow['q_oil'] - ob['q_oil']:+,.0f}"); c.metric('Status', ow['status'])
    st.markdown('**Monte-Carlo fan**')
    m1, m2, m3 = st.columns(3); n = m1.number_input('Samples', 10, 1000, 100, 10, key=f'nu_n_{wid}'); seed = m2.number_input('Seed', 0, 99999, 1701, 1, key=f'nu_seed_{wid}')
    store = ss.setdefault('nodal_mc', {})
    for rb in run_button(st, '▶ Run Monte-Carlo on this well', key=f'nu_run_{wid}', model_hash=model_hash):
        try:
            store[wid] = nu.run(prm, whp, specs, int(n), int(seed), progress=lambda i, t: (rb.progress(i / t, f'Sample {i}/{t}') or True))
        except Exception as exc: rb.fail(str(exc))
    res = store.get(wid)
    if not res: st.caption('Run the Monte-Carlo to see the fan of curves, the rate distribution and the percentile slider.'); return
    go = _go(); env = res['envelope']; fig = go.Figure(); _k, _xl = _axis(st); q = env['q [m3/d]'] * _k; ok = env['IPR P10'] >= 0
    for tag, col in (('IPR', charts.OIL), ('VLP', charts.CATEGORICAL[0])):
        xs = q[ok] if tag == 'IPR' else q
        lo = env[f'{tag} P90'][ok] if tag == 'IPR' else env[f'{tag} P90']; hi = env[f'{tag} P10'][ok] if tag == 'IPR' else env[f'{tag} P10']; mid = env[f'{tag} P50'][ok] if tag == 'IPR' else env[f'{tag} P50']
        fig.add_trace(go.Scatter(x=xs, y=hi, mode='lines', line=dict(width=0), showlegend=False, hoverinfo='skip')); fig.add_trace(go.Scatter(x=xs, y=lo, mode='lines', line=dict(width=0), fill='tonexty', fillcolor='rgba(100,100,100,0.15)' if tag == 'VLP' else 'rgba(27,175,122,0.18)', name=f'{tag} P90-P10 band', hoverinfo='skip'))
        fig.add_trace(go.Scatter(x=xs, y=mid, mode='lines', name=f'{tag} P50', line=dict(color=col, width=2)))
    pct_name = st.select_slider('Show the realisation at probability of exceedance', list(PCTS), value='P50 (median)', key=f'nu_pct_{wid}'); idx = nu.pick_realisation(res, PCTS[pct_name]); row = res['samples'].iloc[idx]
    ipr_i, vlp_i = res['ipr'][idx], res['vlp'][idx]; keep = ipr_i >= 0
    fig.add_trace(go.Scatter(x=res['q'][keep] * _k, y=ipr_i[keep], mode='lines', name='IPR (selected)', line=dict(color=charts.OIL, width=3, dash='dash'))); fig.add_trace(go.Scatter(x=res['q'] * _k, y=vlp_i, mode='lines', name='VLP (selected)', line=dict(color=charts.CATEGORICAL[0], width=3, dash='dash')))
    if row['q_liq'] > 0: fig.add_trace(go.Scatter(x=[row['q_liq'] * _k], y=[row['bhp']], mode='markers', name='Operating point (selected)', marker=dict(size=12, color=charts.GAS, line=dict(width=2, color='white'))))
    st.plotly_chart(charts.style(fig, f"{name}: spread of inflow / outflow curves ({len(res['samples'])} samples)", 'Bottom-hole pressure [bar]', _xl, 460), use_container_width=True, key=f'nu_fan_{wid}')
    cols = st.columns(len(chosen) + 1); cols[0].metric('Liquid rate', f"{row['q_liq']:,.0f} m³/d")
    for c, s in zip(cols[1:], specs): c.metric(s['label'], f"{row[s['key']]:,.4g} {s['unit']}")
    sm = res['summary']; rows = [{'Quantity': k, 'P90 (low)': v.get('P90'), 'P50': v.get('P50'), 'P10 (high)': v.get('P10'), 'Mean': v.get('Mean')} for k, v in sm.items() if isinstance(v, dict) and v]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True); st.caption(f"Probability the well flows at all: {sm.get('P(flowing)', 0):.0%}. P90 = value exceeded with 90 % probability (low case), P10 = high case.")
    a, b = st.columns(2); h = res['samples']['q_liq']
    fh = go.Figure(go.Histogram(x=h * _k, nbinsx=20, marker_color=charts.OIL)); a.plotly_chart(charts.style(fh, 'Rate distribution', 'Samples', _xl, 300, legend=False), use_container_width=True, key=f'nu_hist_{wid}')
    if len(res['sensitivity']): sd = res['sensitivity']; fb = go.Figure(go.Bar(y=sd['Input'][::-1], x=sd['Rank correlation with liquid rate'][::-1], orientation='h', marker_color=charts.CATEGORICAL[0])); b.plotly_chart(charts.style(fb, 'Which input drives the rate', None, 'Rank correlation', 300, legend=False), use_container_width=True, key=f'nu_sens_{wid}')
    table_actions(st, res['samples'], f'nodal_mc_{name}', f'nu_ta_{wid}')


# ----------------------------------------------------------------------------- matching
def _matching(st, wid, name, prm, whp, nodes, reset):
    ss = st.session_state; tests_store = ss.setdefault('nodal_tests', {}); surv_store = ss.setdefault('nodal_survey', {}); res_store = ss.setdefault('nodal_match', {})
    st.caption('Enter well-test points - rate, flowing bottom-hole pressure, wellhead pressure and (optionally) the static reservoir pressure - to match the inflow (IPR) and the tubing outflow (VLP). '
               'The match tunes productivity and a tubing pressure-drop multiplier with the same functions the network solver uses, so a matched well behaves the same in the network.')
    base = pd.DataFrame(tests_store.get(wid) or [], columns=wm.TEST_COLUMNS)
    c1, c2 = st.columns([1, 1])
    if c1.button('Generate example tests from the current model', key=f'wm_syn_{wid}', use_container_width=True, help='Only to try the tool: the points are computed, not measured.'):
        tests_store[wid] = wm.synthetic_tests(prm, whps=(max(whp * 0.5, 2.0), whp, whp * 1.6)).to_dict('records'); st.rerun()
    up = c2.file_uploader('Upload tests (CSV / Excel)', type=['csv', 'xlsx'], key=f'wm_up_{wid}')
    if up is not None and ss.get(f'wm_up_done_{wid}') != getattr(up, 'file_id', up.name):
        try: d = pd.read_csv(up) if up.name.lower().endswith('.csv') else pd.read_excel(up); tests_store[wid] = d.to_dict('records'); ss[f'wm_up_done_{wid}'] = getattr(up, 'file_id', up.name); st.rerun()
        except Exception as exc: st.error(f'Could not read the file: {exc}')
    if base.empty: base = pd.DataFrame([{c: None for c in wm.TEST_COLUMNS}])
    ed = st.data_editor(base, num_rows='dynamic', hide_index=True, use_container_width=True, key=f'wm_ed_{wid}'); tests_store[wid] = ed.to_dict('records'); tests = wm.clean_tests(ed)
    with st.expander('Flowing-gradient survey (optional) - pressure vs depth at one rate'):
        sb = pd.DataFrame(surv_store.get(wid) or [], columns=['TVD [m]', 'Pressure [bar]'])
        if sb.empty: sb = pd.DataFrame([{'TVD [m]': None, 'Pressure [bar]': None}])
        se = st.data_editor(sb, num_rows='dynamic', hide_index=True, use_container_width=True, key=f'wm_sv_{wid}'); surv_store[wid] = se.to_dict('records')
        s1, s2 = st.columns(2); sq = s1.number_input('Survey rate [m³/d]', 0.0, 1e6, 500.0, 10.0, key=f'wm_sq_{wid}'); sw = s2.number_input('Survey WHP [bar]', 0.0, 1000.0, float(whp), 1.0, key=f'wm_sw_{wid}')
    o1, o2 = st.columns(2); fit_pr = o1.checkbox('Also fit reservoir pressure (needs ≥ 2 tests at different rates)', value=False, key=f'wm_fpr_{wid}'); only_best = o2.checkbox('Fit the multiplier on the best correlation only', value=True, key=f'wm_best_{wid}')
    for rb in run_button(st, '▶ Match IPR and VLP', key=f'wm_run_{wid}', model_hash=None):
        out = {}
        out['ipr'] = wm.match_ipr(prm, tests, fit_reservoir_pressure=fit_pr) if len(tests) else {'error': 'no tests entered'}
        out['vlp'] = wm.match_vlp(prm, tests) if len(tests) else {'error': 'no tests entered'}
        s = se.dropna() if len(se.dropna()) >= 2 else None
        out['survey'] = wm.match_survey(prm, s, sq, sw) if s is not None else None
        res_store[wid] = out
    out = res_store.get(wid)
    if not out: st.caption('Enter at least one test and press Match.'); return
    ipr, vlp, sv = out.get('ipr'), out.get('vlp'), out.get('survey')
    a, b = st.columns(2)
    with a:
        st.markdown('**IPR match**')
        if ipr and 'error' not in ipr:
            st.metric('RMS error in BHP', f"{ipr['rmse_after_bar']:.2f} bar", f"{ipr['rmse_after_bar'] - ipr['rmse_before_bar']:+.2f} vs before", delta_color='inverse')
            st.write({k: round(v, 4) for k, v in ipr['params'].items()}); st.caption(ipr['note'] + ('. A fitted value sits at its bound - check the data.' if ipr['at_bound'] else ''))
        else: st.warning((ipr or {}).get('error', 'no result'))
    with b:
        st.markdown('**VLP match**')
        if vlp and 'error' not in vlp:
            st.metric('Best correlation', vlp['best_correlation'], f"RMS {vlp['best_rmse_bar']:.1f} bar"); st.write(f"Pressure-drop multiplier **{vlp['multiplier']:.2f}** → RMS {vlp['rmse_with_multiplier_bar']:.2f} bar")
            if vlp.get('warning'): st.warning(vlp['warning'])
        else: st.warning((vlp or {}).get('error', 'no result'))
    if vlp and 'ranking' in vlp: st.dataframe(vlp['ranking'].drop(columns=['Note'], errors='ignore'), hide_index=True, use_container_width=True)
    if sv and 'ranking' in sv:
        st.markdown('**Flowing-gradient survey: correlation ranking**'); st.dataframe(sv['ranking'].drop(columns=['Note'], errors='ignore'), hide_index=True, use_container_width=True)
        go = _go(); fig = go.Figure(); s = se.dropna(); fig.add_trace(go.Scatter(x=s['Pressure [bar]'], y=s['TVD [m]'], mode='markers', name='Survey', marker=dict(size=9, color='#000', symbol='diamond')))
        for i, nm in enumerate(list(sv['profiles'])[:5]): p = sv['profiles'][nm]; fig.add_trace(go.Scatter(x=p['Pressure [bar]'], y=p['TVD [m]'], mode='lines', name=nm, line=dict(color=charts.CATEGORICAL[i % 8], width=2)))
        fig.update_yaxes(autorange='reversed', rangemode='normal'); st.plotly_chart(charts.style(fig, 'Flowing-gradient survey vs correlations', 'TVD [m]', 'Pressure [bar]', 380), use_container_width=True, key=f'wm_sv_fig_{wid}')
    matched = wm.apply_match(prm, ipr, vlp if (vlp and 'error' not in vlp) else None); cap = nu.q_grid([prm, matched]); b_i, b_v = nu.curves(prm, whp, cap); m_i, m_v = nu.curves(matched, whp, cap)
    ob = nu.operating_point(prm, whp); om = nu.operating_point(matched, whp)
    st.plotly_chart(_curves_fig([('before', cap, b_i, b_v, {'dash': 'dot', 'w': 1.5}), ('matched', cap, m_i, m_v, {})], f'{name}: before vs matched, with measured points', op={'before': (ob['q_liq'], ob['bhp']), 'matched': (om['q_liq'], om['bhp'])}, points=tests if 'BHP [bar]' in tests else None, ax=_axis(st)), use_container_width=True, key=f'wm_fig_{wid}')
    if len(tests) and 'BHP [bar]' in tests and 'WHP [bar]' in tests:
        rows = []
        for _, r in tests.dropna(subset=['BHP [bar]', 'WHP [bar]']).iterrows():
            ws0, ws1 = well_settings(prm), well_settings(matched); rows.append({'Rate [m3/d]': r['Rate [m3/d]'], 'WHP [bar]': r['WHP [bar]'], 'Measured BHP [bar]': r['BHP [bar]'], 'Predicted before [bar]': vlp_bhp(r['Rate [m3/d]'], r['WHP [bar]'], ws0)[0], 'Predicted after [bar]': vlp_bhp(r['Rate [m3/d]'], r['WHP [bar]'], ws1)[0]})
        pt = pd.DataFrame(rows); st.dataframe(pt, hide_index=True, use_container_width=True); table_actions(st, pt, f'vlp_match_{name}', f'wm_ta_{wid}')
    if st.button('✅ Apply the match to this well', key=f'wm_apply_{wid}', type='primary', use_container_width=True, help='Writes the fitted productivity / reservoir pressure / correlation / multiplier into the well. Re-solve afterwards.'):
        upd = {}
        for f in (ipr, vlp if (vlp and 'error' not in vlp) else None):
            if f and 'error' not in f: upd.update(f['params'])
        real = next((n for n in nodes if n['id'] == wid), None)
        if real is not None and upd:
            real.setdefault('params', {}).update({k: v for k, v in upd.items() if not (k == 'reservoir_pressure_bar' and real['params'].get('reservoir_id'))})
            if 'reservoir_pressure_bar' in upd and real['params'].get('reservoir_id'): st.warning('Reservoir pressure was not written: this well takes its pressure from the tank. Change the tank pressure instead.')
            ss.pop('hub_cache', None)
            if reset: reset()
            st.success('Applied. Solve the network again.'); st.rerun()


# ----------------------------------------------------------------------------- blowout
def _blowout(st, wid, name, prm, nodes, model_hash):
    ss = st.session_state
    st.caption('Worst-case uncontrolled flow: the reservoir flows to atmosphere (or the seabed) with no choke and no lift, through the tubing, the annulus or both. '
               'Screening only - steady inflow, no transient, no bridging or hole collapse; not a regulatory worst-case-discharge study.')
    c1, c2, c3, c4 = st.columns(4)
    depth = c1.number_input('Water depth at the wellhead [m] (0 = platform / land)', 0.0, 3500.0, 0.0, 10.0, key=f'bo_wd_{wid}')
    skin_off = c2.checkbox('Remove skin (no completion damage)', value=True, key=f'bo_skin_{wid}'); chk = c3.checkbox('Check exit choking', value=True, key=f'bo_chk_{wid}')
    tid = float(prm.get('tubing_id_m') or 0.0762); d1, d2 = st.columns(2)
    ci = d1.number_input('Casing ID [m] (annulus flow)', 0.05, 0.8, float(prm.get('casing_id_m') or 0.2205), 0.005, format='%.4f', key=f'bo_ci_{wid}'); to = d2.number_input('Tubing OD [m]', 0.03, 0.5, float(prm.get('tubing_od_m') or round(tid * 1.15, 4)), 0.005, format='%.4f', key=f'bo_to_{wid}')
    p = dict(prm, casing_id_m=ci, tubing_od_m=to); store = ss.setdefault('blowout', {})
    for rb in run_button(st, '▶ Calculate blowout rates', key=f'bo_run_{wid}', model_hash=model_hash):
        try:
            tab = bo.scenario_table(p, depth); main = bo.solve(p, 'both' if ci > to * 1.02 else 'tubing', depth, skin_off, chk); store[wid] = {'table': tab, 'main': main, 'prm': p, 'depth': depth, 'skin': skin_off}
        except Exception as exc: rb.fail(str(exc))
    res = store.get(wid)
    if not res: st.caption('Press the button to compute the rate for tubing / annulus / both, with and without skin.'); return
    m = res['main']; u = bo.units(m); k = st.columns(4)
    k[0].metric('Oil', f"{u['Oil [Sm3/d]']:,.0f} Sm³/d", f"{u['Oil [bbl/d]']:,.0f} bbl/d", delta_color='off'); k[1].metric('Gas', f"{u['Gas [Sm3/d]'] / 1e6:,.2f} MSm³/d", f"{u['Gas [MMscf/d]']:,.1f} MMscf/d", delta_color='off')
    k[2].metric('Water', f"{u['Water [m3/d]']:,.0f} m³/d"); k[3].metric('Flowing BHP', f"{m['pwf']:,.0f} bar", f"exit {m['exit_pressure_bar']:.1f} bar", delta_color='off')
    st.caption(f"Flow path: {m['path']} · exit Mach {m['mach']:.2f}" + (' · **exit flow is sonic - exit pressure raised until just choked**' if m['choked'] else ''))
    st.dataframe(res['table'], hide_index=True, use_container_width=True); table_actions(st, res['table'], f'blowout_scenarios_{name}', f'bo_ta_{wid}')
    cv = m['curves']; vl = cv['vlp']; ipr = cv['ipr']; q = cv['q']
    curves = [('', q, ipr, next(iter(vl.values())), {})] + ([('annulus', q, ipr, vl['annulus'], {'dash': 'dot'})] if 'annulus' in vl and 'tubing' in vl else [])
    st.plotly_chart(_curves_fig(curves, f'{name}: inflow vs open-flow outflow at exit pressure {m["exit_pressure_bar"]:.1f} bar', op={'blowout': (m['q_liq'] if len(m['path_rates']) == 1 else None, m['pwf'])}, ax=_axis(st)), use_container_width=True, key=f'bo_fig_{wid}')
    with st.expander('Release over time (depletion of the connected volume)'):
        tk = next((n for n in nodes if n['id'] == (prm.get('reservoir_id') or '')), None); pv0 = 0.0
        if tk and (tk.get('params') or {}).get('stoiip_sm3'): tp = tk['params']; pv0 = float(tp['stoiip_sm3']) * float(tp.get('boi_rm3_sm3') or 1.2) / max(1 - float(tp.get('swi') or 0.2), 0.05)
        e1, e2, e3 = st.columns(3); pv = e1.number_input('Connected pore volume [Mm³]', 0.0, 1e5, round(pv0 / 1e6 * 0.05, 2) if pv0 else 0.0, 1.0, key=f'bo_pv_{wid}', help='Default 5 % of the tank pore volume: the part that actually drains in weeks. No aquifer or injection support.')
        days = e2.number_input('Duration [days]', 1.0, 365.0, 90.0, 1.0, key=f'bo_days_{wid}'); ct = e3.number_input('Compressibility [1/bar]', 1e-6, 1e-3, float(prm.get('ct_1bar') or 1.5e-4), 1e-5, format='%.6f', key=f'bo_ct_{wid}')
        if st.button('Compute release profile', key=f'bo_dep_{wid}', disabled=pv <= 0):
            try: store[wid]['dep'] = bo.depletion_profile(res['prm'], m['path'], res['depth'], res['skin'], pv * 1e6, ct, days, 18)
            except Exception as exc: st.error(str(exc))
        dp = store[wid].get('dep')
        if dp is not None and len(dp):
            st.plotly_chart(charts.lines(dp, 'Day', ['Oil [Sm3/d]'], 'Blowout oil rate over time', 'Sm³/d', colors={'Oil [Sm3/d]': charts.OIL}), use_container_width=True, key=f'bo_dep_fig_{wid}')
            st.caption(f"Released in {dp['Day'].iloc[-1]:.0f} days: {dp['Cum oil [Sm3]'].iloc[-1]:,.0f} Sm³ oil ({dp['Cum oil [Sm3]'].iloc[-1] * 6.28981:,.0f} bbl), {dp['Cum gas [Sm3]'].iloc[-1] / 1e6:,.1f} MSm³ gas.")
            table_actions(st, dp, f'blowout_release_{name}', f'bo_dep_ta_{wid}')
