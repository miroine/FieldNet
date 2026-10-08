"""Property-panel editor for the Darcy inflow model of a well (vertical / deviated / horizontal, optional commingled layers)."""
from __future__ import annotations
import pandas as pd
from ui.widgets import clean_num, synced_number, synced_select, synced_checkbox
from physics.darcy_ipr import ORIENTATIONS, DEFAULTS, darcy_ipr

ORIENT_LABEL = {'vertical': 'Vertical', 'deviated': 'Deviated (Cinco-Ley pseudo-skin)', 'horizontal': 'Horizontal (Joshi, with kv/kh)'}
LAYER_COLS = {'perm_md': 'Permeability kh [mD]', 'net_pay_m': 'Net pay h [m]', 'kv_kh': 'kv/kh [-]', 'skin': 'Skin [-]'}


def darcy_editor(st, p, sid, length_input, pi_to_display, pi_unit, gas):
    """Write the ``darcy_*`` parameters into ``p``. ``length_input(label_base, canonical, key, lo, hi)`` is the app's unit-aware length widget.
    Returns the Darcy result for the current pressure (``darcy_ipr``)."""
    g = lambda k: float(clean_num(p.get('darcy_' + k), DEFAULTS.get(k, 0.0)))
    orient = synced_select(st, 'Well geometry', list(ORIENTATIONS), p.get('darcy_orientation', 'vertical') if p.get('darcy_orientation') in ORIENTATIONS else 'vertical', 'dco' + sid, format_func=ORIENT_LABEL.get)
    p['darcy_orientation'] = orient
    c1, c2 = st.columns(2)
    p['darcy_perm_md'] = synced_number(c1, 'Permeability kh [mD]', g('perm_md'), 'dck' + sid, 0.001, 1e5, fmt='%.3g', container=c1)
    p['darcy_kv_kh'] = synced_number(c2, 'Anisotropy kv/kh [-]', g('kv_kh'), 'dcv' + sid, 0.0001, 1.0, fmt='%.3g', container=c2)
    p['darcy_net_pay_m'] = length_input('Net pay h', g('net_pay_m'), 'dch' + sid, 0.1, 2000.0)
    p['darcy_drainage_radius_m'] = length_input('Drainage radius re' + (' (horizontal: reh)' if orient == 'horizontal' else ''), g('drainage_radius_m'), 'dcre' + sid, 1.0, 20000.0)
    p['darcy_wellbore_radius_m'] = length_input('Wellbore radius rw', g('wellbore_radius_m'), 'dcrw' + sid, 0.01, 1.0)
    if orient == 'horizontal': p['darcy_lateral_length_m'] = length_input('Lateral length L', g('lateral_length_m'), 'dcL' + sid, 10.0, 10000.0)
    else: p.pop('darcy_lateral_length_m', None)
    if orient == 'deviated': p['darcy_inclination_deg'] = synced_number(st, 'Inclination through the pay [deg from vertical]', g('inclination_deg'), 'dci' + sid, 0.0, 89.0, fmt='%.1f')
    else: p.pop('darcy_inclination_deg', None)
    if orient != 'horizontal':
        p['darcy_regime'] = synced_select(st, 'Flow regime', ['pseudo', 'steady'], p.get('darcy_regime', 'pseudo') if p.get('darcy_regime') in ('pseudo', 'steady') else 'pseudo', 'dcr' + sid,
                                          format_func={'pseudo': 'Pseudo-steady state (ln re/rw − ¾)', 'steady': 'Steady state (ln re/rw)'}.get)
    st.caption('Mechanical skin is the well **Skin** below (it enters the Darcy denominator directly).')
    if gas:
        c3, c4 = st.columns(2)
        gv = c3.number_input('Gas viscosity [cP] (0 = correlation)', 0.0, 1.0, float(clean_num(p.get('darcy_gas_visc_cp'), 0.0) or 0.0), 0.001, format='%.4f', key='dcgv' + sid)
        gz = c4.number_input('Gas z-factor (0 = correlation)', 0.0, 2.0, float(clean_num(p.get('darcy_z'), 0.0) or 0.0), 0.01, key='dcgz' + sid)
        p['darcy_gas_visc_cp'] = gv or None; p['darcy_z'] = gz or None
        for k in ('darcy_gas_visc_cp', 'darcy_z'):
            if p.get(k) is None: p.pop(k, None)
        p.pop('darcy_visc_cp', None); p.pop('darcy_bo', None)
    else:
        c3, c4 = st.columns(2)
        p['darcy_visc_cp'] = synced_number(c3, 'Oil viscosity [cP]', g('visc_cp'), 'dcmu' + sid, 0.01, 1e4, fmt='%.3g', container=c3)
        p['darcy_bo'] = synced_number(c4, 'Oil Bo [rm³/Sm³]', g('bo'), 'dcbo' + sid, 0.5, 5.0, fmt='%.3f', container=c4)
    with st.expander('Commingled layers (optional: different properties per layer, one bottom-hole pressure)', expanded=bool(p.get('darcy_layers'))):
        rows = [dict(r) for r in (p.get('darcy_layers') or [])] or [{'perm_md': None, 'net_pay_m': None, 'kv_kh': None, 'skin': None}]
        st.caption('Leave empty to use the single layer above. Each row is one layer; blank cells take the value above. Productivities add.')
        sig = str(abs(hash(str(rows))))
        with st.form('dcl_form' + sid + sig, border=False):
            ed = st.data_editor(pd.DataFrame(rows, columns=list(LAYER_COLS)), num_rows='dynamic', hide_index=True, use_container_width=True, key='dcl' + sid + sig,
                                column_config={k: st.column_config.NumberColumn(v) for k, v in LAYER_COLS.items()})
            go = st.form_submit_button('Apply layers')
        if go:
            out = [{k: float(r[k]) for k in LAYER_COLS if r.get(k) is not None and str(r.get(k)) not in ('nan', '')} for r in ed.to_dict('records')]
            out = [r for r in out if r]
            if out: p['darcy_layers'] = out
            else: p.pop('darcy_layers', None)
    res = darcy_ipr(p, float(clean_num(p.get('reservoir_pressure_bar'), 200.0)), 'gas' if gas else 'oil')
    for w in res['warnings']: st.warning(w)
    if gas: st.metric('Back-pressure C (n = 1) [Sm³/d/bar²]', f"{res['gas_c']:,.2f}", help=f"μ = {res['mu_cp']:.4f} cP, z = {res['z']:.3f} at 0.75·Pr; D = {res['denominator'] if not isinstance(res['denominator'], list) else sum(res['denominator'])/len(res['denominator']):.2f}")
    else:
        vert = darcy_ipr({**p, 'darcy_orientation': 'vertical', 'darcy_inclination_deg': 0}, float(clean_num(p.get('reservoir_pressure_bar'), 200.0)), 'oil')['pi']
        st.metric(f'Productivity index [{pi_unit}]', f"{pi_to_display(res['pi']):,.2f}", delta=(f"{res['pi'] / vert:.1f}× a vertical well" if orient != 'vertical' and vert > 0 else None))
    return res
