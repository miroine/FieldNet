"""Fluid & PVT page: correlation PVT (with CO2 / H2S / N2), calibration to lab data, temperature model, gas-quality screening."""
from __future__ import annotations
import math
import pandas as pd
from dataclasses import replace
from physics.pvt_model import (FluidSpec, FluidModel, Calibration, calibrate, rank_correlations, PB_CORRS, BO_CORRS, VISC_CORRS, Z_CORRS, CP)
from physics.pvt import simple_black_oil
from physics import thermal as th
from physics.gas_quality import screen_network
from ui.run_button import run_button

LAB_COLS = ['Pressure [bar]', 'Rs [Sm3/Sm3]', 'Bo [rm3/Sm3]', 'Oil viscosity [cP]', 'Z', 'Gas viscosity [cP]']
LABEL = {'standing': 'Standing', 'vasquez_beggs': 'Vasquez-Beggs', 'glaso': 'Glaso', 'petrosky_farshad': 'Petrosky-Farshad', 'beggs_robinson': 'Beggs-Robinson',
         'egbogah': 'Egbogah', 'beal': 'Beal', 'dak': 'Dranchuk-Abou-Kassem', 'hall_yarborough': 'Hall-Yarborough', 'papay': 'Papay (low pressure only)'}


def _cl(v, lo, hi): return min(max(float(v), lo), hi)


def _name(x): return x.get('name') or x['id']


def _first(nodes, key, default):
    for n in nodes:
        if n.get('kind') == 'well' and (n.get('params') or {}).get(key) not in (None, ''):
            try: return float(n['params'][key])
            except (TypeError, ValueError): pass
    return default


def _pick_targets(st, nodes, edges, key, kinds=('wells', 'flowlines')):
    opts = {}
    if 'wells' in kinds:
        for n in nodes:
            if n.get('kind') == 'well': opts['well · ' + _name(n)] = n
    if 'flowlines' in kinds:
        for e in edges:
            if e.get('kind', 'pipeline') == 'pipeline': opts['flowline · ' + _name(e)] = e
    chosen = st.multiselect('Apply to', list(opts), default=list(opts), key=key)
    return [opts[c] for c in chosen]


def _spec_from_state(ss, api, gsg, gor):
    cal = Calibration(**ss['pvt_cal']['spec']['cal']) if ss.get('pvt_cal') and ss['pvt_cal'].get('applied') else Calibration()
    return FluidSpec(api=api, gas_sg=gsg, rsb_sm3sm3=gor, pb_bar=(ss.get('pvt_pb') or None), water_sg=1.03, salinity_wt_pct=(ss.get('pvt_sal') or None),
                     co2=ss.get('pvt_co2', 0.0) / 100.0, h2s=ss.get('pvt_h2s', 0.0) / 100.0, n2=ss.get('pvt_n2', 0.0) / 100.0,
                     pb_corr=ss.get('pvt_pbc', 'standing'), bo_corr=ss.get('pvt_boc', 'standing'), visc_corr=ss.get('pvt_vic', 'beggs_robinson'), z_corr=ss.get('pvt_zc', 'dak'), cal=cal)


def render_pvt(st, nodes, edges, solved):
    ss = st.session_state
    st.subheader('Fluid & PVT')
    st.caption('Correlation PVT replaces the fixed screening model (which used Pb = 150 bar and Rsb = 120 Sm³/Sm³ for every fluid). Choose correlations, add CO2 / H2S / N2, '
               'calibrate to lab data, then apply to wells and flowlines. Elements you do not apply it to keep the legacy screening model.')
    t_fluid, t_lib, t_cal, t_temp, t_gq = st.tabs(['Fluid & correlations', 'Fluid library (several fluids)', 'Lab data & calibration', 'Temperature model', 'Gas quality (CO2 / H2S)'])
    with t_fluid: _fluid_tab(st, nodes, edges)
    with t_lib:
        from ui.fluid_library_view import render_fluid_library
        render_fluid_library(st, nodes, edges, solved, lambda ss, a, g, r: _spec_from_state(ss, a, g, r))
    with t_cal: _cal_tab(st, nodes, edges)
    with t_temp: _temp_tab(st, nodes, edges, solved)
    with t_gq: _gq_tab(st, nodes, edges, solved)


# ----------------------------------------------------------------------------- fluid
def _fluid_tab(st, nodes, edges):
    ss = st.session_state
    c1, c2, c3, c4 = st.columns(4)
    api = c1.number_input('Oil gravity [°API]', 5.0, 70.0, _cl(_first(nodes, 'api', 36.0), 5.0, 70.0), 0.5, key='pvt_api')
    gsg = c2.number_input('Gas specific gravity (total gas, air = 1)', 0.5, 2.0, _cl(_first(nodes, 'gas_sg', 0.72), 0.5, 2.0), 0.01, key='pvt_gsg')
    gor = c3.number_input('Solution GOR at bubble point, Rsb [Sm³/Sm³]', 0.0, 3000.0, _cl(_first(nodes, 'gor_sm3sm3', 100.0), 0.0, 3000.0), 5.0, key='pvt_gor',
                          help='Used for the plots. On the elements Rsb follows each element\'s GOR unless the lab Rsb is stored by the calibration.')
    temp = c4.number_input('Temperature for the plots [°C]', 5.0, 220.0, _cl(_first(nodes, 'bottomhole_temperature_c', _first(nodes, 'temperature_c', 80.0)), 5.0, 220.0), 1.0, key='pvt_T')
    d1, d2, d3, d4, d5 = st.columns(5)
    d1.number_input('CO2 [mol %]', 0.0, 80.0, 0.0, 0.5, key='pvt_co2'); d2.number_input('H2S [mol %]', 0.0, 40.0, 0.0, 0.1, key='pvt_h2s'); d3.number_input('N2 [mol %]', 0.0, 30.0, 0.0, 0.5, key='pvt_n2')
    d4.number_input('Measured bubble point [bar] (0 = correlation)', 0.0, 800.0, 0.0, 5.0, key='pvt_pb'); d5.number_input('Water salinity [wt % NaCl] (0 = from water SG)', 0.0, 30.0, 0.0, 0.5, key='pvt_sal')
    e1, e2, e3, e4 = st.columns(4)
    e1.selectbox('Pb / Rs', PB_CORRS, format_func=LABEL.get, key='pvt_pbc'); e2.selectbox('Bo', BO_CORRS, format_func=LABEL.get, key='pvt_boc')
    e3.selectbox('Oil viscosity', VISC_CORRS, format_func=LABEL.get, key='pvt_vic'); e4.selectbox('Gas Z-factor', Z_CORRS, format_func=LABEL.get, key='pvt_zc')
    spec = _spec_from_state(ss, api, gsg, gor); errs = spec.validate()
    if errs: st.error('; '.join(errs)); return
    fm = FluidModel(spec); pb = fm.bubble_point_bar(temp)
    m1, m2, m3 = st.columns(3); m1.metric('Bubble point at T', f'{pb:,.1f} bar'); st0 = fm.state(pb, temp); m2.metric('Bo at Pb', f'{st0.oil_fvf:.3f}'); m3.metric('Oil viscosity at Pb', f'{st0.oil_viscosity_pas / CP:.2f} cP')
    if spec.co2 + spec.h2s + spec.n2 > 0: st.caption('Contaminants change the gas Z, viscosity and density and (Standing) the bubble point. They do not model CO2 dissolving in the oil (swelling / viscosity reduction): calibrate to a swelling test for CO2-rich fluids.')
    pmax = max(pb * 2.0, 100.0); ps = [pmax * i / 60 for i in range(1, 61)]; rows = fm.table(ps, temp); df = pd.DataFrame(rows)
    leg = pd.DataFrame([{'Pressure [bar]': p, 'Rs': simple_black_oil(p, temp, api, gsg).solution_gor_sm3sm3, 'Bo': simple_black_oil(p, temp, api, gsg).oil_fvf, 'Z': simple_black_oil(p, temp, api, gsg).gas_z,
                         'mu': simple_black_oil(p, temp, api, gsg).oil_viscosity_pas / CP} for p in ps])
    import plotly.graph_objects as go
    from ui.charts import style, CATEGORICAL
    spec_plots = [('Rs [Sm3/Sm3]', 'Solution GOR [Sm³/Sm³]', 'Rs'), ('Bo [rm3/Sm3]', 'Oil FVF [rm³/Sm³]', 'Bo'), ('Oil viscosity [cP]', 'Oil viscosity [cP]', 'mu'),
                  ('Oil density [kg/m3]', 'Oil density [kg/m³]', None), ('Z', 'Gas Z-factor', 'Z'), ('Gas viscosity [cP]', 'Gas viscosity [cP]', None)]
    cols = st.columns(3)
    for i, (col, title, legacy) in enumerate(spec_plots):
        fig = go.Figure(); fig.add_trace(go.Scatter(x=df['Pressure [bar]'], y=df[col], name='Correlation', mode='lines', line=dict(color=CATEGORICAL[0], width=2.5)))
        if legacy: fig.add_trace(go.Scatter(x=leg['Pressure [bar]'], y=leg[legacy], name='Legacy screening', mode='lines', line=dict(color=CATEGORICAL[1], width=1.8, dash='dash')))
        fig.add_vline(x=pb, line_dash='dot', line_color='#888'); cols[i % 3].plotly_chart(style(fig, title, x='Pressure [bar]', height=260), use_container_width=True)
    with st.expander('Property table'):
        st.dataframe(df, hide_index=True, use_container_width=True); st.download_button('Download PVT table (CSV)', df.to_csv(index=False), 'fieldnet_pvt_table.csv', 'text/csv')
    st.markdown('##### Apply to the model')
    targets = _pick_targets(st, nodes, edges, 'pvt_targets')
    a, b = st.columns(2)
    if a.button('✅ Use correlation PVT on these elements', key='pvt_apply', type='primary', use_container_width=True):
        for o in targets:
            prm = o.setdefault('params', {}); old = prm.get('pvt') or {}
            new = {'model': 'correlation', 'pb_corr': spec.pb_corr, 'bo_corr': spec.bo_corr, 'visc_corr': spec.visc_corr, 'z_corr': spec.z_corr,
                   'co2': spec.co2, 'h2s': spec.h2s, 'n2': spec.n2}
            if spec.pb_bar: new['pb_bar'] = spec.pb_bar
            if spec.salinity_wt_pct: new['salinity_wt_pct'] = spec.salinity_wt_pct
            for k in ('cal', 'rsb_sm3sm3'):
                if k in old: new[k] = old[k]
            prm['pvt'] = new
        st.success(f'Correlation PVT set on {len(targets)} element(s). Re-solve the network.'); st.rerun()
    if b.button('↩ Back to legacy screening PVT', key='pvt_legacy', use_container_width=True):
        for o in targets: (o.get('params') or {}).pop('pvt', None)
        st.success('Legacy PVT restored on the selected elements.'); st.rerun()
    n_on = sum(1 for n in nodes if (n.get('params') or {}).get('pvt')) + sum(1 for e in edges if (e.get('params') or {}).get('pvt'))
    st.caption(f'{n_on} element(s) currently use correlation PVT.')


# ----------------------------------------------------------------------------- calibration
def _cal_tab(st, nodes, edges):
    ss = st.session_state
    st.caption('Enter what the PVT report gives you (leave 0 / blank for what you do not have). The correlation is shifted onto the measurements: bubble point, Rs shape, Bo, '
               'undersaturated compressibility, viscosity, Z and gas viscosity. Measured values are never altered.')
    c1, c2, c3, c4, c5 = st.columns(5)
    t_c = c1.number_input('Lab temperature [°C]', 5.0, 220.0, _cl(_first(nodes, 'bottomhole_temperature_c', 80.0), 5.0, 220.0), 1.0, key='cal_T')
    pb = c2.number_input('Bubble point [bar]', 0.0, 800.0, 0.0, 1.0, key='cal_pb'); rsb = c3.number_input('Rsb [Sm³/Sm³]', 0.0, 3000.0, 0.0, 1.0, key='cal_rsb')
    bo = c4.number_input('Bo at Pb [rm³/Sm³]', 0.0, 5.0, 0.0, 0.01, key='cal_bo'); mu = c5.number_input('Oil viscosity at Pb [cP]', 0.0, 500.0, 0.0, 0.05, key='cal_mu')
    up = st.file_uploader('Lab table (CSV with the columns below)', type=['csv'], key='cal_csv')
    if up is not None:
        try: ss['cal_df'] = pd.read_csv(up)
        except Exception as exc: st.error(f'Could not read CSV: {exc}')
    base = ss.get('cal_df') if ss.get('cal_df') is not None else pd.DataFrame({c: [None] * 4 for c in LAB_COLS})
    for c in LAB_COLS:
        if c not in base.columns: base[c] = None
    df = st.data_editor(base[LAB_COLS], num_rows='dynamic', use_container_width=True, key='cal_editor')
    lab = {'pb_bar': pb or None, 'rsb': rsb or None, 'bo_pb': bo or None, 'mu_o_pb_cp': mu or None, 'table': df.dropna(how='all').to_dict('records')}
    api = ss.get('pvt_api', 36.0); gsg = ss.get('pvt_gsg', 0.72); gor = rsb or ss.get('pvt_gor', 100.0)
    spec = replace(_spec_from_state(ss, api, gsg, gor), pb_bar=None, cal=Calibration())
    if spec.validate(): st.error('Fix the fluid inputs first: ' + '; '.join(spec.validate())); return
    for rb in run_button(st, '🔎 Rank the correlations against the lab data', key='cal_rank_run'):
        ss['pvt_rank'] = rank_correlations(spec, lab, t_c)
    if ss.get('pvt_rank'):
        rk = pd.DataFrame(ss['pvt_rank']); st.dataframe(rk[rk['Rank'] <= 2].assign(Correlation=lambda d: d['Correlation'].map(lambda x: LABEL.get(x, x))), hide_index=True, use_container_width=True)
        best = rk[rk['Rank'] == 1].set_index('Property')['Correlation'].to_dict()
        st.caption('Best per property: ' + ', '.join(f'{k}: {LABEL.get(v, v)}' for k, v in best.items()))
    for rb in run_button(st, '🎯 Calibrate to the lab data', key='cal_run', type='primary'):
        s2, rows, notes = calibrate(spec, lab, t_c); ss['pvt_cal'] = {'spec': s2.to_dict(), 'rows': rows, 'notes': notes, 't_c': t_c, 'lab': lab, 'applied': False}
    cr = ss.get('pvt_cal')
    if cr:
        for n in cr['notes']: st.caption('• ' + n)
        if cr['rows']: st.dataframe(pd.DataFrame(cr['rows']), hide_index=True, use_container_width=True)
        _cal_plot(st, cr, FluidSpec.from_dict(cr['spec']), spec)
        targets = _pick_targets(st, nodes, edges, 'cal_targets')
        if st.button('✅ Apply calibration to these elements', key='cal_apply', type='primary', use_container_width=True):
            cs = FluidSpec.from_dict(cr['spec'])
            for o in targets:
                prm = o.setdefault('params', {}); pv = dict(prm.get('pvt') or {'model': 'correlation'}); pv['model'] = 'correlation'; pv['cal'] = cs.cal.as_dict()
                for k in ('pb_corr', 'bo_corr', 'visc_corr', 'z_corr'): pv[k] = getattr(cs, k)
                if lab.get('rsb'): pv['rsb_sm3sm3'] = float(lab['rsb'])
                prm['pvt'] = pv
            cr['applied'] = True; st.success(f'Calibrated PVT applied to {len(targets)} element(s). Re-solve the network.'); st.rerun()
        st.download_button('Download calibration (JSON)', __import__('json').dumps({'spec': cr['spec'], 'report': cr['rows'], 'notes': cr['notes'], 'lab_temperature_c': cr['t_c']}, indent=2, default=str), 'fieldnet_pvt_calibration.json', 'application/json')


def _cal_plot(st, cr, cal_spec, raw_spec):
    import plotly.graph_objects as go
    from ui.charts import style, CATEGORICAL
    lab = cr['lab']; t = cr['t_c']; pts = [r for r in lab['table'] if r.get('Pressure [bar]') == r.get('Pressure [bar]') and r.get('Pressure [bar]') is not None]
    if not pts: return
    ps = sorted(float(r['Pressure [bar]']) for r in pts); lo, hi = max(1.0, ps[0] * 0.5), ps[-1] * 1.1; grid = [lo + (hi - lo) * i / 50 for i in range(51)]
    fa, fb = FluidModel(raw_spec), FluidModel(cal_spec); cols = st.columns(3)
    for i, (col, key, fn) in enumerate((('Rs [Sm3/Sm3]', 'Rs', lambda s: s.solution_gor_sm3sm3), ('Bo [rm3/Sm3]', 'Bo', lambda s: s.oil_fvf), ('Oil viscosity [cP]', 'Oil viscosity [cP]', lambda s: s.oil_viscosity_pas / CP))):
        xs = [float(r['Pressure [bar]']) for r in pts if r.get(col) not in (None, '') and r.get(col) == r.get(col)]; ys = [float(r[col]) for r in pts if r.get(col) not in (None, '') and r.get(col) == r.get(col)]
        if not xs: continue
        fig = go.Figure(); fig.add_trace(go.Scatter(x=grid, y=[fn(fa.state(p, t)) for p in grid], name='Uncalibrated', mode='lines', line=dict(color='#999', dash='dash')))
        fig.add_trace(go.Scatter(x=grid, y=[fn(fb.state(p, t)) for p in grid], name='Calibrated', mode='lines', line=dict(color=CATEGORICAL[0], width=2.5)))
        fig.add_trace(go.Scatter(x=xs, y=ys, name='Lab', mode='markers', marker=dict(color=CATEGORICAL[1], size=9)))
        cols[i].plotly_chart(style(fig, key, x='Pressure [bar]', height=260), use_container_width=True)


# ----------------------------------------------------------------------------- temperature
def _temp_tab(st, nodes, edges, solved):
    ss = st.session_state
    st.caption('Default: every element keeps its fixed temperature (isothermal flowlines, linear tubing profile). Switch on the thermal model for wells (Ramey: wellhead temperature '
               'follows the rate) and flowlines / risers (energy balance with mixture heat capacity, Joule-Thomson and elevation). Temperatures then propagate from the wells '
               'through the network and mix at nodes, and the hydraulics are re-solved with them.')
    w1, w2 = st.columns(2)
    with w1:
        st.markdown('**Wells (tubing)**')
        bht = st.number_input('Bottom-hole temperature [°C]', 20.0, 250.0, _cl(_first(nodes, 'bottomhole_temperature_c', 100.0), 20.0, 250.0), 1.0, key='th_bht')
        ts = st.number_input('Surface / mudline temperature [°C]', -5.0, 40.0, 4.0, 1.0, key='th_ts')
        gg = st.number_input('Geothermal gradient [°C/km] (0 = from BHT)', 0.0, 80.0, 0.0, 1.0, key='th_grad')
        uw = st.number_input('Overall U, tubing to formation [W/m²K]', 0.5, 100.0, 10.0, 0.5, key='th_uw')
        wt = _pick_targets(st, nodes, edges, 'th_wtargets', ('wells',))
        if st.button('🌡 Use Ramey wellbore temperature on these wells', key='th_apply_w', use_container_width=True):
            for n in wt:
                p = n.setdefault('params', {}); p.update({'thermal_model': 'ramey', 'bottomhole_temperature_c': bht, 'surface_temperature_c': ts, 'overall_u_w_m2k': uw})
                if gg: p['geothermal_gradient_c_per_km'] = gg
                else: p.pop('geothermal_gradient_c_per_km', None)
            st.success(f'{len(wt)} well(s) updated. Re-solve.'); st.rerun()
    with w2:
        st.markdown('**Flowlines / risers**')
        amb = st.number_input('Ambient (seabed / air) temperature [°C]', -30.0, 40.0, 4.0, 1.0, key='th_amb')
        ul = st.number_input('Overall U, outer surface [W/m²K]', 0.1, 50.0, 5.0, 0.1, key='th_ul', help='Bare buried steel ~ 10-25, coated 4-6, wet insulation 2-3, pipe-in-pipe 0.5-1.')
        jt = st.checkbox('Joule-Thomson effect', True, key='th_jt'); el = st.checkbox('Elevation (potential energy) term', True, key='th_el')
        ft = _pick_targets(st, nodes, edges, 'th_ftargets', ('flowlines',))
        if st.button('🌡 Use energy-balance temperature on these flowlines', key='th_apply_f', use_container_width=True):
            for e in ft: e.setdefault('params', {}).update({'thermal_model': 'heat_loss', 'ambient_temperature_c': amb, 'overall_u_w_m2k': ul, 'include_jt': bool(jt), 'include_elevation': bool(el)})
            st.success(f'{len(ft)} flowline(s) updated. Re-solve.'); st.rerun()
    if st.button('↩ Back to fixed temperatures (all elements)', key='th_reset'):
        for o in list(nodes) + list(edges):
            p = o.get('params') or {}
            if th.mode(p) != 'fixed': p['thermal_model'] = 'fixed'
        st.success('All elements use their fixed temperature again.'); st.rerun()
    r = solved(); info = (r[2] if r else {}) or {}; tp = info.get('thermal')
    if not r: st.info('Solve the network to see temperatures.'); return
    if not tp: st.info('No element uses a thermal model yet.'); return
    s1, s2, s3 = st.columns(3); s1.metric('Thermal iterations', tp.get('iterations', 0)); s2.metric('Converged', 'yes' if tp.get('converged') else 'NO'); s3.metric('Last change [°C]', f"{tp.get('max_change_c', 0):.2f}")
    for w in tp.get('warnings', []): st.warning(w)
    names = {n['id']: _name(n) for n in nodes}
    st.dataframe(pd.DataFrame([{'Node': names.get(k, k), 'Temperature [°C]': v} for k, v in tp['node_temperature_c'].items()]), hide_index=True, use_container_width=True)
    enames = {e['id']: _name(e) for e in edges}
    st.dataframe(pd.DataFrame([{'Element': enames.get(k, k), 'Model': v['mode'], 'Inlet [°C]': v['t_in'], 'Outlet [°C]': v['t_out'], 'Change [°C]': v['t_out'] - v['t_in']} for k, v in tp['edge'].items()]), hide_index=True, use_container_width=True)
    prof = {k: v for k, v in tp['edge'].items() if v.get('profile')}
    if prof:
        import plotly.graph_objects as go
        from ui.charts import style, CATEGORICAL
        sel = st.selectbox('Temperature profile of', list(prof), format_func=lambda k: enames.get(k, k), key='th_prof_sel')
        pr = prof[sel]['profile']; fig = go.Figure(); fig.add_trace(go.Scatter(x=[0] + [x['x_m'] for x in pr], y=[prof[sel]['t_in']] + [x['temperature_c'] for x in pr], name='Fluid temperature', mode='lines', line=dict(color=CATEGORICAL[1], width=2.5)))
        e = next(x for x in edges if x['id'] == sel); amb_t = float((e.get('params') or {}).get('ambient_temperature_c', 4.0)); fig.add_hline(y=amb_t, line_dash='dot', line_color='#888', annotation_text='ambient')
        st.plotly_chart(style(fig, 'Flowline temperature', y='°C', x='Distance along the flow [m]'), use_container_width=True)


# ----------------------------------------------------------------------------- gas quality
def _gq_tab(st, nodes, edges, solved):
    st.caption('Screening of CO2 corrosion (de Waard-Milliams, uncorrected = conservative) and sour service (ISO 15156 / NACE MR0175: pH2S ≥ 0.003 bar). Enter CO2 / H2S on the '
               'Fluid tab and apply it to the wells and flowlines, then solve. Not a materials selection.')
    r = solved()
    if not r: st.info('Solve the network first.'); return
    tp = (r[2] or {}).get('thermal'); rows = screen_network(nodes, edges, r, tp)
    if not rows: st.info('No element has CO2 or H2S content set (Fluid & correlations tab).'); return
    df = pd.DataFrame(rows); st.dataframe(df, hide_index=True, use_container_width=True)
    if (df['Sour service'] == 'YES').any(): st.warning('Sour-service threshold exceeded on: ' + ', '.join(sorted(set(df.loc[df['Sour service'] == 'YES', 'Element']))) + ' - NACE MR0175 / ISO 15156 materials required.')
    if df['Severity'].isin(['high', 'severe']).any(): st.warning('High CO2 corrosion rate on: ' + ', '.join(sorted(set(df.loc[df['Severity'].isin(['high', 'severe']), 'Element']))) + ' - consider CRA, inhibition or glycol.')
    st.download_button('Download gas-quality screening (CSV)', df.to_csv(index=False), 'fieldnet_gas_quality.csv', 'text/csv')
