"""Solve / optimiser / parallel settings panel (one place for the Network, Forecast and Development tabs)."""
from __future__ import annotations
from ui.widgets import synced_number, synced_select, synced_checkbox, synced_text


def render_compute_settings(st, nodes=None, edges=None, key='cmp', honour=None):
    """Render the settings, store them in ``st.session_state['compute']`` and return the normalised dict."""
    from network.solve_options import normalize_compute
    from optimization.objectives import PRESETS, GUIDE_FORMULA_PRESETS, GUIDE_MODES, WELL_NAMES, TOTAL_NAMES, DEFAULT_PRICES
    from network.parallel_solve import cpu_count, compute_plan
    cur = dict(st.session_state.get('compute') or {}); cur.setdefault('optimizer', {}); o = dict(cur['optimizer'])
    o['enabled'] = synced_checkbox(st, '⚡ Optimise while solving (maximise the objective, honouring all constraints)', bool(o.get('enabled', False)), key + '_opt')
    with st.expander('Objective · guide rates · parallel · constraints', expanded=bool(o['enabled'])):
        cur['honour'] = synced_checkbox(st, 'Honour constraints', bool(cur.get('honour', True)), key + '_honour') if honour is None else bool(honour)
        if o['enabled']:
            obj = dict(o.get('objective') or {'preset': 'max_oil'}); presets = list(PRESETS) + ['custom']
            obj['preset'] = synced_select(st, 'Objective', presets, obj.get('preset', 'max_oil') if obj.get('preset', 'max_oil') in presets else 'max_oil', key + '_obj',
                                          format_func=lambda k: {'max_oil': 'Maximise oil', 'max_liquid': 'Maximise liquid', 'max_gas': 'Maximise gas', 'max_revenue': 'Maximise revenue (prices)',
                                                                'min_water': 'Oil with water penalty', 'custom': 'Custom expression'}.get(k, k))
            if obj['preset'] == 'custom':
                obj['expression'] = synced_text(st, 'Objective expression (maximised)', obj.get('expression') or 'oil*price_oil - water*price_water', key + '_expr')
                st.caption('Per-well names: ' + ', '.join(WELL_NAMES) + '  ·  System totals: ' + ', '.join(TOTAL_NAMES) + '. Do not mix per-well and total names in one expression.')
            if obj['preset'] in ('max_revenue', 'min_water', 'custom'):
                pr = dict(obj.get('prices') or {}); c = st.columns(4)
                for i, (k, dv) in enumerate(DEFAULT_PRICES.items()): pr[k] = synced_number(c[i], f'price {k}', float(pr.get(k, dv)), f'{key}_price_{k}', 0.0, 1e9, fmt='%.3g')
                obj['prices'] = pr
            o['objective'] = obj
            g = dict(o.get('guide') or {}); modes = ['objective', 'pro_rata', 'potential', 'priority', 'formula']
            g['mode'] = synced_select(st, 'Sharing a limited capacity between wells (guide)', modes, g.get('mode', 'objective') if g.get('mode', 'objective') in modes else 'objective', key + '_guide',
                                      format_func=lambda k: {'objective': 'Best objective value (default)', 'pro_rata': 'Pro rata to rate', 'potential': 'By potential', 'priority': 'By priority rank',
                                                             'formula': 'Guide-rate formula (Eclipse style)'}.get(k, k))
            if g['mode'] == 'objective': o['guide'] = None
            else:
                g['phase'] = synced_select(st, 'Guide phase', ['oil', 'liquid', 'gas'], g.get('phase', 'oil'), key + '_gph')
                if g['mode'] == 'formula':
                    names = list(GUIDE_FORMULA_PRESETS); pre = synced_select(st, 'Formula preset', ['custom'] + names, g.get('preset', 'custom') if g.get('preset', 'custom') in names + ['custom'] else 'custom', key + '_gpre')
                    if pre != 'custom': g['preset'] = pre; g['formula'] = GUIDE_FORMULA_PRESETS[pre]['formula']; g['params'] = dict(GUIDE_FORMULA_PRESETS[pre]['params'])
                    else: g.pop('preset', None)
                    g['formula'] = synced_text(st, 'Guide formula G (high = served first)', g.get('formula') or 'potential_oil', key + '_gform')
                    g['convention'] = synced_select(st, 'Convention', ['inverse', 'allocation'], g.get('convention', 'inverse'), key + '_gconv', format_func={'inverse': 'Reduction ∝ 1/G (curtail low G first)', 'allocation': 'Rate ∝ G (GUIDERAT)'}.get)
                    st.caption('Constants A, B, C ... come from the formula parameters below.')
                    prm = dict(g.get('params') or {})
                    for k in list(prm): prm[k] = synced_number(st, f'Parameter {k}', float(prm[k]), f'{key}_gp_{k}', -1e9, 1e9, fmt='%.4g')
                    g['params'] = prm
                if g['mode'] == 'priority':
                    txt = synced_text(st, 'Priority ranks (well_id=rank, comma separated; 1 = most important)', ','.join(f'{k}={v}' for k, v in (g.get('priority') or {}).items()), key + '_gprio')
                    try: g['priority'] = {a.strip(): int(b) for a, b in (x.split('=') for x in txt.split(',') if '=' in x)}
                    except ValueError: st.error('Use well_id=rank pairs, e.g. P1=1, P2=2')
                o['guide'] = g
            o['gas_lift'] = synced_checkbox(st, 'Also allocate gas-lift gas', bool(o.get('gas_lift', False)), key + '_gl')
        cur['optimizer'] = o
        cc = cpu_count(); cur['workers'] = int(synced_number(st, f'Worker processes ({cc} cores available)', float(cur.get('workers', 1)), key + '_workers', 1, max(cc, 1), 1, fmt='%d'))
        if nodes is not None and edges is not None:
            for ln in compute_plan(nodes, edges, cur['workers']): st.caption(ln)
        try: norm = normalize_compute(cur)
        except ValueError as exc: st.error(f'Invalid optimiser settings: {exc}'); o['enabled'] = False; cur['optimizer'] = o; norm = normalize_compute({**cur, 'optimizer': {**o, 'enabled': False}})
    st.session_state['compute'] = cur
    return norm
