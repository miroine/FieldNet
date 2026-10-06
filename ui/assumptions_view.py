"""Forecast assumptions: recovery factor per tank, rate limits / calibration / EUR cap per well (see network/assumptions.py)."""
from __future__ import annotations
import pandas as pd
from network import assumptions as A


def _invalidate(ss):
    for k in ('hub_cache', 'forecast'): ss.pop(k, None)
    ss['solve'] = None; ss.pop('solve_request', None); ss.pop('v21_warm_start', None)


def render_assumptions(st, nodes, edges):
    ss = st.session_state
    tt = A.tank_table(nodes); wt = A.well_table(nodes)
    active = A.has_assumptions(nodes) or any(r['Calibrate to rate [%s]' % A.UNITS[r['Primary phase']]['rate']] for r in wt) or any(abs(r['Productivity ×'] - 1) > 1e-9 for r in wt)
    with st.expander('Recovery & rate assumptions (calibrate the forecast)' + (' — active' if active else ''), expanded=False):
        st.caption('Two ways to calibrate the prognosis, usable together. **Recovery factor per tank**: give the ultimate recovery (fraction of the primary phase in place: oil → STOIIP, gas / condensate → GIIP). '
                   'The tank offtake tapers exponentially (e-folding time below) towards the recoverable volume, so the target is approached smoothly and never overshot; wells share the cap pro rata, '
                   'and the physics (pressure, lift, network) can still make the field fall short — the recovery table then says so. **Rates per well**: a hard maximum rate, a rate to *calibrate* the well productivity to '
                   '(the productivity multiplier is solved on the network at start-up conditions), and an optional well EUR cap. Blank = no assumption; with nothing set the forecast is unchanged.')
        tdf = pd.DataFrame(tt)
        taper = st.number_input('RF taper time [days] (e-folding time of the approach to the target)', 30.0, 3650.0, float(A.taper_days_of(next((n.get('params') for n in nodes if n.get('kind') == 'reservoir' and (n.get('params') or {}).get('rf_taper_days')), {}))), 30.0, key='as_taper',
                                help='Shorter = production holds up and then stops more abruptly near the target; longer = a long tail. 365 d is a good default.')
        if tt:
            st.markdown('**Recovery factor per tank**')
            ed_t = st.data_editor(tdf, hide_index=True, use_container_width=True, key='as_tanks', disabled=['Tank ID', 'Tank', 'Primary phase', tdf.columns[3], 'Implied abandonment p [bar]'],
                                  column_config={'Tank ID': None, 'Target RF [%]': st.column_config.NumberColumn(min_value=0.0, max_value=100.0, format='%.1f', help='Blank = no target (pure physics).')})
            gas_pmin = st.toggle('Also set the gas tanks\' abandonment pressure from the target RF (p/z)', value=False, key='as_pmin',
                                 help='Writes min pressure = the pressure at which a volumetric gas tank has produced the target RF. Without it the taper alone enforces the target.')
        else:
            ed_t = None; gas_pmin = False; st.info('No reservoir tank: recovery targets need a tank. Per-well rate assumptions below still work.')
        st.markdown('**Rates per well**')
        wdf = pd.DataFrame(wt)
        ed_w = st.data_editor(wdf, hide_index=True, use_container_width=True, key='as_wells', disabled=['Well ID', 'Well', 'Tank', 'Primary phase', 'Productivity ×'],
                              column_config={'Well ID': None})
        st.caption('Rates are in the primary phase of the well: oil Sm³/d, gas MSm³/d. EUR: oil MSm³, gas GSm³. “Calibrate to rate” = the rate the well should deliver at start-up through this network, before its own rate limits.')
        c1, c2, c3 = st.columns(3)
        if c1.button('Apply assumptions', type='primary', use_container_width=True, key='as_apply'):
            n1 = A.apply_tank_rows(nodes, ed_t.to_dict('records') if ed_t is not None else [], taper, gas_pmin); n2 = A.apply_well_rows(nodes, ed_w.to_dict('records'))
            _invalidate(ss); st.success(f'Applied: {n1} tank(s) and {n2} well(s) changed. Re-run the forecast.'); st.rerun()
        if c2.button('Calibrate well productivity to the rates', use_container_width=True, key='as_cal',
                     help='Solves the network at the start-up conditions and scales each well productivity until it delivers its “Calibrate to rate”.'):
            A.apply_well_rows(nodes, ed_w.to_dict('records'))
            from network import calibration as C
            if not C.targets_from_nodes(nodes): st.warning('Enter a “Calibrate to rate” for at least one well first.')
            else:
                with st.spinner('Calibrating productivity on the network…'):
                    res = C.calibrate_wells(nodes, edges)
                C.apply_calibration(nodes, res); ss['as_cal_report'] = res; _invalidate(ss); st.rerun()
        if c3.button('Clear calibration', use_container_width=True, key='as_clear'):
            from network import calibration as C
            k = C.clear_calibration(nodes); _invalidate(ss); st.success(f'Productivity multipliers reset on {k} well(s).'); st.rerun()
        rep = ss.get('as_cal_report')
        if rep and rep.get('rows'):
            st.markdown('**Calibration result** — ' + ('all wells reproduce their target.' if rep.get('converged') else 'some wells could not be matched (see status).') + f" ({rep.get('iterations')} network solves)")
            st.dataframe(pd.DataFrame(rep['rows']).drop(columns=['Well ID']), hide_index=True, use_container_width=True)
