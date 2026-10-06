"""Material balance & voidage page under Tanks & coupling: voidage replacement, Havlena-Odeh / Campbell / Cole, p/z, drive indices.

Two sources: the forecast of the model on screen, or the user's measured history (pressure + cumulative volumes) - the case where material balance
actually tells you something new (in-place volume, aquifer strength)."""
from __future__ import annotations
import io
import pandas as pd
from network import mb_analysis as mb, annual
from network.reservoir_mb import tanks_from_nodes
from ui.hub_access import table_actions
from ui import charts

HIST_KEY = 'mb_history'


def _go():
    import plotly.graph_objects as go; return go


def voidage_figure(va, title='Voidage and replacement by year'):
    """Two stacks per year: what left the reservoir (oil, free gas, water) and what replaced it (injection, aquifer, communication) + VRR line."""
    go = _go(); from plotly.subplots import make_subplots
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08, row_heights=[0.68, 0.32]); yrs = va['Year'].tolist()
    out_cols = [('Oil', charts.OIL), ('Free gas', charts.GAS), ('Water', charts.WATER)]; in_cols = [('Water injection', charts.INJ), ('Gas injection', '#eda100'), ('Aquifer influx', '#2a78d6'), ('Communication', '#999999')]
    for c, col in out_cols:
        if c + ' [rm3]' in va: fig.add_trace(go.Bar(x=yrs, y=va[c + ' [rm3]'] / 1e6, name=f'Voidage: {c.lower()}', marker_color=col, offsetgroup='void', legendgroup='void'), row=1, col=1)
    for c, col in in_cols:
        if c + ' [rm3]' in va and va[c + ' [rm3]'].abs().sum() > 0: fig.add_trace(go.Bar(x=yrs, y=va[c + ' [rm3]'] / 1e6, name=f'Replacement: {c.lower()}', marker_color=col, opacity=0.8, offsetgroup='repl', legendgroup='repl'), row=1, col=1)
    fig.update_layout(barmode='relative')
    fig.add_trace(go.Scatter(x=yrs, y=va['VRR'], mode='lines+markers', name='VRR (replacement / voidage)', line=dict(color='#333', width=2)), row=2, col=1)
    fig.add_hline(y=1.0, line_dash='dot', line_color='#888', row=2, col=1)
    fig = charts.style(fig, title, 'Reservoir volume [Mrm³]', None, 520); fig.update_yaxes(title='VRR [-]', row=2, col=1); fig.update_xaxes(dtick=1)
    return fig


def _line(df, x, ys, title, xt, yt, fit=None, height=360, markers=True):
    go = _go(); fig = go.Figure()
    for i, y in enumerate(ys): fig.add_trace(go.Scatter(x=df[x], y=df[y], mode='markers+lines' if markers else 'lines', name=y, line=dict(color=charts.CATEGORICAL[i % 8], width=2), marker=dict(size=7)))
    if fit is not None: fig.add_trace(go.Scatter(x=fit[x], y=fit[ys[0]], mode='lines', name='fit', line=dict(color='#333', dash='dash')))
    return charts.style(fig, title, yt, xt, height)


def render_mb(st, nodes, edges, hub):
    ss = st.session_state
    tanks = [n for n in nodes if n.get('kind') == 'reservoir']
    if not tanks: st.info('Add a reservoir tank to analyse its material balance.'); return
    st.markdown('### Material balance & voidage')
    st.caption('How the voidage (what you produce, at reservoir conditions) is replaced - by injection, aquifer influx and communication - and the classical straight-line plots. '
               'Run on the **forecast** it shows how the model balances; run on **measured history** it estimates in-place volume and aquifer strength.')
    c1, c2 = st.columns([2, 2]); names = {t['id']: t.get('name') or t['id'] for t in tanks}
    tid = c1.selectbox('Tank', list(names), format_func=names.get, key='mb_tank'); src = c2.radio('Data', ['Forecast of the model', 'Measured history'], horizontal=True, key='mb_src')
    tank = tanks_from_nodes(nodes)[tid]; fc = getattr(hub, 'forecast', None)
    if src == 'Forecast of the model':
        if not (fc and fc.get('tanks')): st.info('Run a forecast first (Development schedule tab, or the button above) - there is nothing to analyse yet.'); return
        series = mb.series_from_forecast(fc, tid)
        if len(series) < 3: st.info('The forecast has too few steps for this tank.'); return
    else:
        series = _history_input(st, tid, tank, fc)
        if series is None: return
    o1, o2, o3, o4 = st.columns(4)
    m = o1.number_input('Gas-cap ratio m', 0.0, 5.0, 0.0, 0.05, key='mb_m', help='Initial gas-cap size / oil-zone size (reservoir volumes). 0 = no gas cap.') if tank.phase == 'oil' else 0.0
    bw = o2.number_input('Bw [rm³/Sm³]', 0.9, 1.2, 1.0, 0.01, key='mb_bw')
    modes = ['Use the tabulated aquifer influx', 'Fit in-place volume and aquifer (Schilthuis)', 'Volumetric: fit in-place volume only']
    mode = o3.selectbox('Fit', modes, index=0 if src == 'Forecast of the model' else 1, key='mb_fit')
    ct = o4.number_input('Lumped compressibility [1/bar] (0 = tank value)', 0.0, 1e-2, 0.0, 1e-5, format='%.6f', key='mb_ct')
    r = mb.analyse(series, tank, bw=bw, m=m, with_aquifer=mode.startswith('Fit'), known_influx=mode.startswith('Use'), ct_override=ct or None)
    for w in series.attrs.get('warnings', []): st.warning(w)
    fit = r['fit']; pr = r['props']; inp = pr.n_in; unit = 'Sm³ oil' if pr.phase == 'oil' else 'Sm³ gas'
    k1, k2, k3, k4 = st.columns(4)
    if 'N' in fit:
        k1.metric(f'In place from balance', f"{fit['N'] / (1e6 if pr.phase == 'oil' else 1e9):,.2f} {'M' if pr.phase == 'oil' else 'G'}{unit}", delta=f"{(fit['N'] / max(inp, 1) - 1) * 100:+.0f}% vs input", delta_color='off')
        k2.metric('Input in place', f"{inp / (1e6 if pr.phase == 'oil' else 1e9):,.2f} {'M' if pr.phase == 'oil' else 'G'}{unit}")
        k3.metric('Aquifer J fitted [m³/d/bar]', f"{fit['J']:,.0f}" if fit['J'] else '—', help=f"Tank input: {tank.jaq:,.0f}")
        k4.metric('Fit R²', f"{fit['r2']:.3f}" if fit['r2'] == fit['r2'] else '—')
        if fit.get('note'): (st.warning if 'WARNING' in fit['note'] or 'unphysical' in fit['note'] else st.caption)(fit['note'])
    else: st.warning(fit.get('error', 'no fit'))
    if src == 'Forecast of the model': st.caption('On a forecast of this screening model the balance closes by construction, so the straight lines mainly confirm the model is consistent - they are not independent evidence. Use measured history for that.')
    t_v, t_s, t_d, t_p, t_t = st.tabs(['Voidage replacement', 'Straight-line plots', 'Drive indices', 'Pressure & recovery', 'Tables'])
    with t_v:
        va = r['voidage_annual']; iv = r['voidage']
        if va.empty: st.info('Not enough rows.')
        else:
            st.plotly_chart(voidage_figure(va), use_container_width=True, key='mb_void')
            go = _go(); fig = go.Figure()
            fig.add_trace(go.Scatter(x=iv['End'], y=iv['Voidage [rm3]'].cumsum() / 1e6, name='Cumulative voidage', line=dict(color=charts.OIL, width=2)))
            fig.add_trace(go.Scatter(x=iv['End'], y=iv['Replacement [rm3]'].cumsum() / 1e6, name='Cumulative replacement', line=dict(color=charts.INJ, width=2)))
            st.plotly_chart(charts.style(fig, 'Cumulative voidage vs replacement', 'Mrm³', None, 320), use_container_width=True, key='mb_cumvoid')
            last = iv['Cum VRR'].iloc[-1]; st.caption(f"Cumulative VRR {last:.2f}: " + ('injection + influx fully replace the voidage - pressure is supported.' if last >= 0.98 else 'the voidage is not fully replaced, so pressure declines.'))
    with t_s:
        L = r['lines']; ho = L['havlena_odeh']
        if ho.empty: st.info('No points with a pressure drop yet - straight-line plots need depletion.')
        else:
            a, b = st.columns(2)
            a.plotly_chart(_line(ho.assign(**{'F [Mrm3]': ho['F [rm3]'] / 1e6}), 'Et [rm3/Sm3]', ['F [Mrm3]'], 'Havlena-Odeh: F vs Et', 'Et [rm³/Sm³]', 'F [Mrm³]', fit=(L.get('havlena_odeh_fit').assign(**{'F [Mrm3]': L['havlena_odeh_fit']['F [rm3]'] / 1e6}) if 'havlena_odeh_fit' in L else None)), use_container_width=True, key='mb_ho')
            b.plotly_chart(_line(L['campbell'].assign(**{'F/Et [MSm3]': L['campbell']['F/Et [Sm3]'] / 1e6, 'F [Mrm3]': L['campbell']['F [rm3]'] / 1e6}), 'F [Mrm3]', ['F/Et [MSm3]'], 'Campbell: F/Et vs F', 'F [Mrm³]', 'F/Et [MSm³]'), use_container_width=True, key='mb_camp')
            c, d = st.columns(2)
            c.plotly_chart(_line(L['cole'].assign(**{'x': L['cole']['Cumulative production [Sm3]'] / 1e6, 'F/Et [MSm3]': L['cole']['F/Et [Sm3]'] / 1e6}), 'x', ['F/Et [MSm3]'], 'Cole: F/Et vs cumulative production', 'Cumulative production [MSm³]', 'F/Et [MSm³]'), use_container_width=True, key='mb_cole')
            if r['pz'] and 'G_apparent' in r['pz']:
                bal = r['balance']; z = r['pz']; go = _go(); fig = go.Figure()
                fig.add_trace(go.Scatter(x=bal['Gp [Sm3]'] / 1e9, y=bal['p/z [bar]'], mode='markers', name='p/z'))
                xs = [0, z['G_apparent'] / 1e9]; fig.add_trace(go.Scatter(x=xs, y=[z['intercept'], 0], mode='lines', name='straight line', line=dict(dash='dash', color='#333')))
                d.plotly_chart(charts.style(fig, f"p/z vs Gp - apparent GIIP {z['G_apparent'] / 1e9:,.2f} GSm³", 'p/z [bar]', 'Gp [GSm³]', 360), use_container_width=True, key='mb_pz')
            elif pr.phase != 'oil': d.info('p/z line not available (pressure does not fall with production).')
            st.caption('F = reservoir-volume withdrawal net of injection. Et = total expansion (oil + dissolved gas, gas cap, rock/connate water). A straight line through the origin with slope N means a volumetric reservoir; an upward-curving F/Et means aquifer influx.')
    with t_d:
        dr = r['drive']
        if dr.empty: st.info('Drive indices are shown for oil tanks with a pressure drop.')
        else:
            go = _go(); fig = go.Figure()
            for i, c in enumerate([c for c in dr.columns if c not in ('Date', 'Closure')]):
                if dr[c].abs().sum() > 1e-9: fig.add_trace(go.Bar(x=dr['Date'], y=dr[c], name=c, marker_color=charts.CATEGORICAL[i % 8]))
            fig.update_layout(barmode='relative'); st.plotly_chart(charts.style(fig, 'Drive indices (share of the withdrawal each mechanism supplies)', 'Fraction', None, 380), use_container_width=True, key='mb_drive')
            st.caption(f"Basis: {r['drive_basis']} in-place volume {r['drive_basis_N'] / 1e6:,.2f} MSm³ ({'the balance fit is reliable here' if r['drive_basis'] == 'fitted' else 'the fit is not reliable for this record, so the input value is used'}). Closure (sum of indices) is {dr['Closure'].iloc[-1]:.2f}; far from 1 means the PVT / compressibility used here disagrees with the data.")
    with t_p:
        bal = r['balance']; a, b = st.columns(2)
        a.plotly_chart(_line(bal.assign(**{'Cum [MSm3]': (bal['Np [Sm3]'] if pr.phase == 'oil' else bal['Gp [Sm3]']) / (1e6 if pr.phase == 'oil' else 1e9)}), 'Cum [MSm3]', ['Pressure [bar]'], 'Pressure vs cumulative production', 'Cumulative production [M/GSm³]', 'bar'), use_container_width=True, key='mb_pcum')
        b.plotly_chart(_line(bal, 'RF [%]', ['Pressure [bar]'], 'Pressure vs recovery factor', 'RF [%]', 'bar'), use_container_width=True, key='mb_prf')
        if not r['voidage'].empty: st.plotly_chart(_line(r['voidage'], 'Cum net [rm3]', ['Pressure [bar]'], 'Pressure vs cumulative net voidage (replacement - voidage)', 'Cumulative net [rm³]', 'bar'), use_container_width=True, key='mb_pnet')
    with t_t:
        st.markdown('**Balance table**'); st.dataframe(r['balance'], hide_index=True, use_container_width=True); table_actions(st, r['balance'], f"mb_balance_{names[tid]}", f'mb_bal_{tid}')
        if not r['voidage_annual'].empty:
            st.markdown('**Voidage by year**'); st.dataframe(r['voidage_annual'], hide_index=True, use_container_width=True); table_actions(st, r['voidage_annual'], f"mb_voidage_annual_{names[tid]}", f'mb_va_{tid}')


def _history_input(st, tid, tank, fc):
    ss = st.session_state; store = ss.setdefault(HIST_KEY, {})
    st.markdown('**Measured history** - one row per date with the measured reservoir pressure and the cumulative volumes to that date. Leave a column at 0 if it does not apply.')
    c1, c2 = st.columns(2)
    up = c1.file_uploader('Upload CSV / Excel', type=['csv', 'xlsx'], key=f'mb_up_{tid}')
    if up is not None and ss.get(f'mb_up_done_{tid}') != getattr(up, 'file_id', up.name):
        try:
            d = pd.read_csv(up) if up.name.lower().endswith('.csv') else pd.read_excel(up); store[tid] = d.to_dict('records'); ss[f'mb_up_done_{tid}'] = getattr(up, 'file_id', up.name); st.rerun()
        except Exception as exc: st.error(f'Could not read the file: {exc}')
    if c2.button('Start from the forecast (to edit / try the tool)', key=f'mb_seed_{tid}', disabled=not (fc and fc.get('tanks')), use_container_width=True):
        s = mb.series_from_forecast(fc, tid); store[tid] = s.assign(Date=s['Date'].dt.strftime('%Y-%m-%d')).to_dict('records'); st.rerun()
    base = pd.DataFrame(store.get(tid) or [], columns=['Date', 'Pressure [bar]'] + mb.CUM)
    if base.empty: base = pd.DataFrame([{'Date': '2027-01-01', 'Pressure [bar]': tank.pi, **{c: 0.0 for c in mb.CUM}}])
    ed = st.data_editor(base, num_rows='dynamic', hide_index=True, use_container_width=True, key=f'mb_hist_{tid}')
    store[tid] = ed.to_dict('records')
    try: s = mb.series_from_history(ed)
    except Exception as exc: st.error(f'Check the table: {exc}'); return None
    if len(s) < 3: st.info('Enter at least 3 rows (date, pressure and cumulative volumes).'); return None
    return s
