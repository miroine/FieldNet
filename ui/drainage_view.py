"""Drainage strategy recommendation: wells, plateau, recovery and what limits them, with a printable report."""
from __future__ import annotations
import pandas as pd
from network import drainage as D
from ui.forecast_view import _step_solver, _workers
from ui.run_button import start_run
from ui.graph_contract import graph_hash
from ui import charts


def render_drainage(st, nodes, edges):
    st.divider()
    st.subheader('Drainage strategy recommendation')
    st.caption('Sweeps the number of producers with the facility capacities honoured and answers: how many wells, what plateau (rate and length), what recovery, and what limits it. '
               'The recommendation is the smallest well count that meets your plateau target and still earns its last well (extra recovery, or NPV if you give economics).')
    prods = [n for n in nodes if n.get('kind') == 'well']
    if len(prods) < 2: st.info('Add at least two producers to sweep the well count.'); return
    names = {n['id']: n.get('name', n['id']) for n in prods}; phase0 = D.primary_phase(nodes)
    with st.container(border=True):
        a, b, c, d = st.columns(4)
        start = a.text_input('Start date (YYYY-MM-DD)', value=str(st.session_state.get('fc_start') or '2030-01-01'), key='ds_start')
        years = b.number_input('Horizon [years]', 1.0, 50.0, 20.0, 1.0, key='ds_years')
        step = c.selectbox('Report step [days]', [90, 180, 365], index=1, key='ds_step')
        phase = d.selectbox('Primary phase', ['oil', 'gas'], index=0 if phase0 == 'oil' else 1, key='ds_phase')
        order = st.multiselect('Producers in drilling priority', list(names), default=list(names), format_func=names.get, key='ds_order')
        e1, e2, e3 = st.columns(3)
        thr = e1.slider('Minimum extra recovery from one more well [%]', 1, 30, 5, key='ds_thr')
        unit = 'MSm³/d' if phase == 'gas' else 'Sm³/d'; k = 1e6 if phase == 'gas' else 1.0
        pt = e2.number_input(f'Plateau target [{unit}] (0 = none)', 0.0, 1e9, 0.0, key='ds_pt'); pty = e3.number_input('…held for at least [years] (0 = any)', 0.0, 50.0, 0.0, 0.5, key='ds_pty')
        with st.expander('Economics (optional — the NPV-optimal well count is then recommended)'):
            use_e = st.toggle('Use economics', value=False, key='ds_econ')
            f1, f2, f3, f4 = st.columns(4)
            price = f1.number_input(f'Price per {"MSm³" if phase == "gas" else "Sm³"} produced', 0.0, 1e9, 3000.0 if phase == 'oil' else 2.0e6, key='ds_price')
            capex = f2.number_input('Capex per well [same currency]', 0.0, 1e12, 8e7, key='ds_capex'); opex = f3.number_input('Opex per well per year', 0.0, 1e12, 2e6, key='ds_opex')
            disc = f4.number_input('Discount rate [-]', 0.0, 0.5, 0.08, 0.01, key='ds_disc')
        rb = start_run(st, '▶ Run drainage strategy study', key='ds_run', type='primary', model_hash=graph_hash(nodes, edges), disabled=len(order) < 2)
    if rb:
        econ = {'price_per_sm3': price / (1e6 if phase == 'gas' else 1.0), 'capex_per_well': capex, 'opex_per_well_year': opex, 'discount_rate': disc} if use_e else None
        rb.progress(0.02, 'Starting well-count cases…')
        try:
            st.session_state.ds_result = D.drainage_strategy(nodes, edges, order, start, float(years), int(step), True, thr / 100.0, (pt * k) or None, pty or None, econ, phase,
                                                             progress=lambda i, n: rb.progress(i / n, f'{i}/{n} well counts'), step_solver=_step_solver(st), workers=_workers(st))
            st.session_state.ds_names = names
        except Exception as exc: rb.fail(f'Study failed: {exc}')
        rb.finish()
    res = st.session_state.get('ds_result')
    if not res: return
    nm = st.session_state.get('ds_names', {})
    st.success('**Recommendation:** ' + res['summary']); st.caption(res['reason'])
    k = res['unit_scale']; unit = res['unit']; ek = 1e9 if res['phase'] == 'gas' else 1e6; eu = 'GSm³' if res['phase'] == 'gas' else 'MSm³'
    df = pd.DataFrame([{k_: v for k_, v in r.items() if k_ != '_forecast'} for r in res['rows']])
    show = pd.DataFrame({'Wells': df['Wells'], 'Added well': df['Added well'].map(lambda x: nm.get(x, x)), f'Plateau rate [{unit}]': df['Plateau rate'] / k, 'Plateau [years]': df['Plateau [years]'],
                         f'EUR [{eu}]': df['EUR'] / ek, 'RF [%]': df['RF [%]'], 'Incremental [%]': df['Incremental [%]'], 'Binding constraint': df['Binding constraint'].fillna('—')})
    if 'NPV' in df: show['NPV'] = df['NPV']
    a, b = st.columns(2)
    col = charts.GAS if res['phase'] == 'gas' else charts.OIL
    a.plotly_chart(charts.bars(show.assign(Label=show['Wells'].astype(str)), 'Label', f'EUR [{eu}]', 'Ultimate recovery vs number of producers', eu, color=col), use_container_width=True, key='ds_eur')
    b.plotly_chart(charts.bars(show.assign(Label=show['Wells'].astype(str)), 'Label', f'Plateau rate [{unit}]', 'Plateau rate vs number of producers', unit, color=col), use_container_width=True, key='ds_plat')
    st.dataframe(show, hide_index=True, use_container_width=True)
    st.download_button('Download the recommendation page (HTML, print to PDF)', D.to_html(res), 'fieldnet_drainage_strategy.html', 'text/html', use_container_width=True, key='ds_dl')
