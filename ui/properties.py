"""Property-panel sections for the Network tab (Streamlit).

Each ``*_editor(st, ...)`` renders one section for the selected element and writes to the element's ``params`` only when
the user enables/changes something (so merely selecting an element never alters the model). Pure helpers
(``constraint_keys_for``, ``survey_xz``) carry the logic and are unit-tested without Streamlit.
"""
from __future__ import annotations
import json
import pandas as pd
from ui.widgets import synced_number, synced_select, synced_checkbox, synced_text, clean_num, clean_text

CANON = 'canonical units (Sm³/d, m³/d, bar, m/s)'


# ------------------------------------------------------------------ pure helpers
def constraint_keys_for(kind, element='node', params=None):
    """Registry keys offered for an element: (key, label, unit) tuples."""
    from solver.constraints import REGISTRY
    from network.features import applicable_capacities, SEPARATOR_KINDS
    out = []
    if element == 'edge':
        eq = kind in ('choke', 'control_valve', 'pump', 'compressor')
        allowed = ('max_rate_m3d', 'max_pressure_bar', 'max_dp_bar') + (('max_power_kw',) if kind in ('pump', 'compressor') else ()) \
            + (() if eq else ('max_oil_rate_m3d', 'max_water_rate_m3d', 'max_gas_rate_sm3d', 'max_velocity_ms', 'max_erosional_ratio'))
        for r in REGISTRY:
            if r['key'] in allowed and 'edge' in r['applies']: out.append((r['key'], r['label'], r['unit']))
        return out
    if kind == 'well':
        return [(r['key'], r['label'], r['unit']) for r in REGISTRY if 'well' in r['applies']]
    if kind in ('water_injector', 'gas_injector', 'injector'):
        return [('max_rate_m3d', 'Max injection rate', 'm3/d'), ('max_whp_bar', 'Max WHP', 'bar')]
    caps = applicable_capacities({'kind': kind, 'params': params or {}})
    reg = {r['key']: r for r in REGISTRY}
    out = [(k, reg[k]['label'], reg[k]['unit']) for k in caps if k in reg]
    out += [(k, reg[k]['label'], reg[k]['unit']) for k in ('max_pressure_bar', 'min_pressure_bar') if k in reg]
    return out


def survey_xz(rows):
    """[(horizontal departure, tvd)] from survey rows (md_m, tvd_m): departure integrated from the chord of each interval."""
    x = 0.0; out = []; prev = None
    for r in rows or []:
        md, tvd = float(r.get('md_m', 0.0)), float(r.get('tvd_m', 0.0))
        if prev is not None:
            dmd, dtvd = md - prev[0], tvd - prev[1]; x += max(dmd * dmd - dtvd * dtvd, 0.0) ** 0.5
        out.append((x, tvd)); prev = (md, tvd)
    return out


def _fig(fig, **kw):
    from ui import charts
    f = charts.style(fig, **kw); f.update_yaxes(rangemode='normal'); return f


# ------------------------------------------------------------------ editors
def role_phase_editor(st, node):
    """Producer/injector role and fluid phase. Returns True if the node was converted (caller reruns)."""
    from network.features import role_phase, set_well_role, PHASE_LABEL
    role, phase = role_phase(node); sid = node['id']
    c1, c2 = st.columns(2)
    r = synced_select(c1, 'Role', ['producer', 'injector'], role, 'wrole' + sid, format_func=str.title)
    opts = ['oil', 'gas', 'gas_condensate'] if r == 'producer' else ['water', 'gas']
    ph = synced_select(c2, 'Phase', opts, phase if phase in opts else opts[0], 'wphase' + sid, format_func=lambda k: PHASE_LABEL.get(k, k))
    if (r, ph) != (role, phase):
        set_well_role(node, r, ph); return True
    return False


def separator_type_editor(st, node):
    from network.features import SEPARATOR_TYPES, set_separator_type
    p = node.setdefault('params', {}); cur = p.get('separator_type', 'two_phase'); sid = node['id']
    cur = cur if cur in SEPARATOR_TYPES else 'two_phase'
    k = synced_select(st, 'Use as', ['separator', 'separator_stage'], node.get('kind') if node.get('kind') in ('separator', 'separator_stage') else 'separator', 'sepkind' + sid,
                      format_func={'separator': 'Separator', 'separator_stage': 'Separator stage (in a train)'}.get)
    if k != node.get('kind'): node['kind'] = k
    t = synced_select(st, 'Separator type', list(SEPARATOR_TYPES), cur, 'septype' + sid, format_func=SEPARATOR_TYPES.get)
    if t != cur: set_separator_type(node, t)


def constraint_editor(st, element, params, kind, key, *, title='Constraints', expanded=False):
    """Checkbox + value per applicable constraint (registry driven). ``element`` is the node/edge dict."""
    prm = element.setdefault('params', {}) if params is None else params
    keys = constraint_keys_for(kind, 'edge' if 'source' in element else 'node', prm)
    if not keys: return
    n_set = sum(1 for k, _, _ in keys if prm.get(k) is not None)
    with st.expander(f'{title} ({n_set} active)', expanded=expanded or n_set > 0 and False):
        st.caption(f'Shared by the network solve, forecast, development and optimiser. Values in {CANON}.')
        for k, label, unit in keys:
            on = synced_checkbox(st, f'{label} [{unit}]', prm.get(k) is not None, f'c_on_{k}_{key}')
            if on:
                v = clean_num(prm.get(k), 0.0)
                prm[k] = synced_number(st, f'{label} value [{unit}]', float(v), f'c_v_{k}_{key}', 0.0, 1e12, fmt='%.4g')
            else: prm.pop(k, None)
        if kind in ('well',) or 'source' in element:
            mode = prm.get('rate_limit_mode', 'enforce')
            if any(prm.get(k) is not None for k, _, _ in keys):
                prm['rate_limit_mode'] = synced_select(st, 'Rate limits', ['enforce', 'report'], mode if mode in ('enforce', 'report') else 'enforce', f'c_mode_{key}',
                                                       format_func={'enforce': 'Enforce (cap the rate)', 'report': 'Report only'}.get) if kind == 'well' else mode


def trajectory_editor(st, node):
    from physics.trajectory import build_survey, survey_from_rows, validate_completion, trajectory_summary, well_total_depth
    p = node.setdefault('params', {}); sid = node['id']
    with st.expander('Trajectory & completion', expanded=bool(p.get('trajectory') or p.get('completion'))):
        mode = synced_select(st, 'Well path', ['vertical', 'template', 'table'], 'table' if p.get('trajectory') else 'vertical', 'tjm' + sid,
                             format_func={'vertical': 'Vertical (from TVD above)', 'template': 'Build from template (J / S / horizontal)', 'table': 'Survey table (MD / TVD)'}.get)
        if mode == 'vertical':
            if p.get('trajectory'): p.pop('trajectory', None)
        elif mode == 'template':
            kind = synced_select(st, 'Template', ['J', 'S', 'horizontal'], 'J', 'tjk' + sid, format_func={'J': 'J-shape (build & hold)', 'S': 'S-shape', 'horizontal': 'Horizontal with lateral'}.get)
            kop = synced_number(st, 'Kick-off MD [m]', 500.0, 'tjkop' + sid, 0.0, 10000.0); br = synced_number(st, 'Build rate [°/30 m]', 3.0, 'tjbr' + sid, 0.1, 15.0)
            args = {'kickoff_md': kop, 'build_rate_deg_per_30m': br}
            if kind in ('J', 'S'):
                args['tangent_inc_deg'] = synced_number(st, 'Tangent inclination [°]', 60.0, 'tjinc' + sid, 1.0, 90.0); args['target_tvd'] = synced_number(st, 'Target TVD [m]', float(clean_num(p.get('depth_m'), 2500.0)), 'tjtvd' + sid, 100.0, 12000.0)
            if kind == 'S': args['drop_rate_deg_per_30m'] = br
            if kind == 'horizontal': args['lateral_length'] = synced_number(st, 'Lateral length [m]', 1000.0, 'tjlat' + sid, 10.0, 8000.0)
            if st.button('Apply template', key='tjapply' + sid):
                try: p['trajectory'] = build_survey(kind, **args); p['depth_m'] = well_total_depth(p)[1]; st.session_state['tjv' + sid] = st.session_state.get('tjv' + sid, 0) + 1
                except ValueError as exc: st.error(str(exc))
        else:
            rows = p.get('trajectory') or [{'md_m': 0.0, 'tvd_m': 0.0, 'inc_deg': 0.0}, {'md_m': 2000.0, 'tvd_m': 2000.0, 'inc_deg': 0.0}]
            ed = st.data_editor(pd.DataFrame(rows), num_rows='dynamic', use_container_width=True, key='tjt' + sid + str(st.session_state.get('tjv' + sid, 0)))
            recs = [r for r in ed.to_dict('records') if clean_num(r.get('md_m')) is not None]
            try:
                p['trajectory'] = survey_from_rows(recs)
            except ValueError as exc: st.error(str(exc))
        if p.get('trajectory'):
            s = trajectory_summary(p)
            c = st.columns(3); c[0].metric('MD [m]', f"{s['md_m']:,.0f}"); c[1].metric('TVD [m]', f"{s['tvd_m']:,.0f}"); c[2].metric('Max inclination [°]', f"{s['max_inc_deg']:.1f}")
            if s.get('tvd_m'): p['depth_m'] = float(s['tvd_m'])
            try:
                import plotly.graph_objects as go
                xz = survey_xz(p['trajectory']); fig = go.Figure(go.Scatter(x=[a for a, _ in xz], y=[b for _, b in xz], mode='lines', name='Well path'))
                fig.update_yaxes(autorange='reversed'); st.plotly_chart(_fig(fig, title='Well path (TVD vs departure)', x='Horizontal departure [m]', y='TVD [m]', legend=False, height=260), use_container_width=True)
            except Exception: pass
        st.markdown('**Completion / tubing diameters** (MD intervals; gaps continue the previous ID)')
        unit = synced_select(st, 'ID unit in the table', ['in', 'mm', 'm'], 'in', 'cmu' + sid, format_func={'in': 'inch (3.5 = 3½" tubing)', 'mm': 'millimetre (88.9)', 'm': 'metre (0.0889)'}.get)
        fac = {'in': 0.0254, 'mm': 1e-3, 'm': 1.0}[unit]; idcol = f'ID [{unit}]'
        crow = p.get('completion') or []
        cols = ['label', 'from_md_m', 'to_md_m', idcol, 'roughness_m']
        cdf = pd.DataFrame([{'label': r.get('label', ''), 'from_md_m': r.get('from_md_m'), 'to_md_m': r.get('to_md_m'), idcol: round(float(r['id_m']) / fac, 4), 'roughness_m': r.get('roughness_m')} for r in crow], columns=cols)
        for c in cols[1:]: cdf[c] = pd.to_numeric(cdf[c], errors='coerce').astype('float64')     # numeric columns even when the table is empty (an empty frame is otherwise all text)
        cdf['label'] = cdf['label'].astype('object')
        try: cfg = {'label': st.column_config.TextColumn('Label'), 'from_md_m': st.column_config.NumberColumn('From MD [m]', min_value=0.0), 'to_md_m': st.column_config.NumberColumn('To MD [m]', min_value=0.0),
               idcol: st.column_config.NumberColumn(idcol, min_value=0.0, help='Internal diameter of the tubing / liner in this interval. Stored in metres internally.'),
               'roughness_m': st.column_config.NumberColumn('Roughness [m]', min_value=0.0, help='Optional; blank = the well roughness')}
        except Exception: cfg = None
        ced = st.data_editor(cdf, num_rows='dynamic', use_container_width=True, key='cmp' + sid + unit, **({'column_config': cfg} if cfg else {}))
        new = []
        for r in ced.to_dict('records'):
            if clean_num(r.get(idcol)) is None or clean_num(r.get('from_md_m')) is None or clean_num(r.get('to_md_m')) is None: continue
            row = {'from_md_m': float(clean_num(r['from_md_m'])), 'to_md_m': float(clean_num(r['to_md_m'])), 'id_m': float(clean_num(r[idcol])) * fac}
            if clean_num(r.get('roughness_m')) is not None: row['roughness_m'] = float(clean_num(r.get('roughness_m')))
            if clean_text(r.get('label')): row['label'] = clean_text(r.get('label'))
            new.append(row)
        if new: p['completion'] = new
        else: p.pop('completion', None)
        for msg in validate_completion(p): st.warning(msg)


def flowline_profile_editor(st, edge):
    """Bathymetry profile and riser geometry for a pipeline."""
    from physics.flowline_profile import profile_points, profile_to_rows, summarize, slug_indicators, parse_profile_text, riser_profile
    p = edge.setdefault('params', {}); eid = edge['id']
    with st.expander('Bathymetry profile & riser', expanded=bool(p.get('profile') or (p.get('riser') or {}).get('enabled'))):
        riser_on = synced_checkbox(st, 'This flowline is a riser', bool((p.get('riser') or {}).get('enabled')), 'ron' + eid)
        if riser_on:
            r = dict(p.get('riser') or {}); r['enabled'] = True
            shapes = ['vertical', 'catenary', 'lazy_wave', 'steep_wave']; r['shape'] = synced_select(st, 'Riser shape', shapes, r.get('shape') if r.get('shape') in shapes else 'vertical', 'rsh' + eid)
            r['water_depth_m'] = synced_number(st, 'Water depth [m]', float(clean_num(r.get('water_depth_m'), 300.0)), 'rwd' + eid, 1.0, 4000.0)
            if r['shape'] != 'vertical': r['horizontal_offset_m'] = synced_number(st, 'Horizontal offset [m]', float(clean_num(r.get('horizontal_offset_m'), r['water_depth_m'])), 'rho' + eid, 0.0, 10000.0)
            if r['shape'] in ('lazy_wave', 'steep_wave'):
                r['buoyancy_length_m'] = synced_number(st, 'Buoyancy section length [m]', float(clean_num(r.get('buoyancy_length_m'), 100.0)), 'rbl' + eid, 0.0, 2000.0)
                r['hog_height_m'] = synced_number(st, 'Hog bend height above seabed [m]', float(clean_num(r.get('hog_height_m'), 40.0)), 'rhh' + eid, 0.0, 1000.0)
            p['riser'] = r; p.pop('profile', None)
            st.caption('Geometric screening profile for hydraulics only: not a riser structural / dynamic design. The riser replaces the straight line for this connection (length and elevation come from the shape).')
        else:
            if p.get('riser'): p['riser'] = {**p['riser'], 'enabled': False}
            use = synced_checkbox(st, 'Use a bathymetry / elevation profile', bool(p.get('profile')), 'pfon' + eid)
            if use:
                txt = st.text_area('Paste x [m], z [m] (two columns; z positive up, seabed negative)', key='pftxt' + eid, height=90)
                if txt.strip() and st.button('Load pasted profile', key='pfload' + eid):
                    try: p['profile'] = parse_profile_text(txt)
                    except ValueError as exc: st.error(str(exc))
                base = p.get('profile') or profile_to_rows(profile_points(edge))
                ed = st.data_editor(pd.DataFrame(base, columns=['x_m', 'z_m']), num_rows='dynamic', use_container_width=True, key='pfed' + eid + str(len(base)))
                rows = [{'x_m': float(r['x_m']), 'z_m': float(r['z_m'])} for r in ed.to_dict('records') if clean_num(r.get('x_m')) is not None and clean_num(r.get('z_m')) is not None]
                if len(rows) >= 2: p['profile'] = rows
            else: p.pop('profile', None)
        if p.get('profile') or (p.get('riser') or {}).get('enabled'):
            try:
                pts = profile_points(edge); s = summarize(pts); sl = slug_indicators(pts)
                c = st.columns(3); c[0].metric('True length [m]', f"{s['true_length_m']:,.0f}"); c[1].metric('Net elevation [m]', f"{s['net_elevation_change_m']:+,.0f}"); c[2].metric('Low points', len(s['low_points']))
                if sl.get('terrain_slugging_flag') or sl.get('riser_slugging_flag') not in (None, 'none'): st.warning('Geometric slugging indicators: ' + '; '.join(sl.get('notes') or ['see profile']) + ' (screening flag, not a slugging model).')
                import plotly.graph_objects as go
                fig = go.Figure(go.Scatter(x=[a for a, _ in pts], y=[b for _, b in pts], mode='lines', name='Elevation'))
                st.plotly_chart(_fig(fig, title='Flowline elevation profile', x='Distance [m]', y='Elevation [m]', legend=False, height=240), use_container_width=True)
            except Exception as exc: st.caption(f'Profile preview unavailable: {exc}')


def relperm_editor(st, node):
    p = node.setdefault('params', {}); sid = node['id']
    if p.get('fluid_phase', 'oil') != 'oil': return
    with st.expander('Relative permeability & water-cut model', expanded=bool(p.get('relperm'))):
        use = synced_checkbox(st, 'Use relative permeability for the water cut', bool(p.get('relperm')), 'rpon' + sid)
        if not use:
            p.pop('relperm', None); p.pop('water_cut_mode', None)
            st.caption('Off: water cut follows the screening S-curve (breakthrough RF → max water cut RF).'); return
        from physics.relperm import relperm_defaults, RelPerm
        rp = dict(p.get('relperm') or relperm_defaults('corey'))
        rp['model'] = synced_select(st, 'Model', ['corey', 'let', 'table'], rp.get('model', 'corey'), 'rpm' + sid, format_func={'corey': 'Corey', 'let': 'LET', 'table': 'Table (Sw, krw, kro)'}.get)
        if rp['model'] != (p.get('relperm') or {}).get('model'): rp = {**relperm_defaults(rp['model']), 'mu_o_cp': rp.get('mu_o_cp', 2.0), 'mu_w_cp': rp.get('mu_w_cp', 0.5)}
        if rp['model'] == 'table':
            ed = st.data_editor(pd.DataFrame(rp.get('table') or [{'sw': 0.2, 'krw': 0.0, 'kro': 0.9}, {'sw': 0.75, 'krw': 0.35, 'kro': 0.0}]), num_rows='dynamic', use_container_width=True, key='rpt' + sid)
            rp['table'] = [{'sw': float(r['sw']), 'krw': float(r['krw']), 'kro': float(r['kro'])} for r in ed.to_dict('records') if all(clean_num(r.get(k)) is not None for k in ('sw', 'krw', 'kro'))]
        else:
            c = st.columns(2)
            rp['swc'] = synced_number(c[0], 'Connate water Swc [-]', float(clean_num(rp.get('swc'), 0.2)), 'rpswc' + sid, 0.0, 0.6, 0.01); rp['sorw'] = synced_number(c[1], 'Residual oil Sorw [-]', float(clean_num(rp.get('sorw'), 0.25)), 'rpsor' + sid, 0.0, 0.6, 0.01)
            rp['krw_max'] = synced_number(c[0], 'krw at Sorw [-]', float(clean_num(rp.get('krw_max'), 0.35)), 'rpkw' + sid, 0.01, 1.0, 0.01); rp['kro_max'] = synced_number(c[1], 'kro at Swc [-]', float(clean_num(rp.get('kro_max'), 0.9)), 'rpko' + sid, 0.01, 1.0, 0.01)
            if rp['model'] == 'corey':
                rp['nw'] = synced_number(c[0], 'Corey nw [-]', float(clean_num(rp.get('nw'), 2.5)), 'rpnw' + sid, 0.5, 8.0, 0.1); rp['no'] = synced_number(c[1], 'Corey no [-]', float(clean_num(rp.get('no'), 2.0)), 'rpno' + sid, 0.5, 8.0, 0.1)
            else:
                for k, lab, dv in (('Lw', 'LET Lw', 2.0), ('Ew', 'LET Ew', 2.0), ('Tw', 'LET Tw', 2.0), ('Lo', 'LET Lo', 2.0), ('Eo', 'LET Eo', 2.0), ('To', 'LET To', 2.0)):
                    rp[k] = synced_number(c[0 if k.endswith('w') else 1], lab, float(clean_num(rp.get(k), dv)), 'rp' + k + sid, 0.1, 20.0, 0.1)
        c = st.columns(2)
        rp['mu_o_cp'] = synced_number(c[0], 'Oil viscosity [cP]', float(clean_num(rp.get('mu_o_cp'), 2.0)), 'rpmo' + sid, 0.05, 5000.0); rp['mu_w_cp'] = synced_number(c[1], 'Water viscosity [cP]', float(clean_num(rp.get('mu_w_cp'), 0.5)), 'rpmw' + sid, 0.05, 50.0)
        rp['sweep_efficiency'] = synced_number(st, 'Sweep efficiency [-]', float(clean_num(rp.get('sweep_efficiency'), p.get('sweep_efficiency', 0.7))), 'rpsw' + sid, 0.05, 1.0, 0.05)
        p['relperm'] = rp; p['water_cut_mode'] = 'relperm'
        st.caption('Screening model: the tank-average water saturation (sweep-weighted) sets the surface water cut via fractional flow. It is not a 3-D simulation.')
        try:
            import plotly.graph_objects as go
            cv = RelPerm.from_params(rp).curves(60); df = pd.DataFrame(cv); fig = go.Figure()
            for col, nm in (('krw', 'krw'), ('kro', 'kro'), ('fw', 'fw')):
                if col in df: fig.add_trace(go.Scatter(x=df['Sw'] if 'Sw' in df else df['sw'], y=df[col], mode='lines', name=nm))
            st.plotly_chart(_fig(fig, title='Relative permeability & fractional flow', x='Sw [-]', height=260), use_container_width=True)
            st.caption(f"End-point mobility ratio M = {RelPerm.from_params(rp).mobility_ratio():.2f}")
        except Exception as exc: st.warning(f'Curve preview unavailable: {exc}')


def prediction_source_editor(st, node, start_date):
    """Decline curve or external-simulator table (GAP / RESOLVE style) for a producer."""
    from network.prediction_sources import prediction_preview, parse_external_csv, fit_arps
    p = node.setdefault('params', {}); sid = node['id']; src = dict(p.get('prediction_source') or {'type': 'none'})
    with st.expander('Prediction source (decline curve / external simulator)', expanded=src.get('type', 'none') != 'none'):
        t = synced_select(st, 'Source', ['none', 'decline', 'external_table'], src.get('type', 'none') if src.get('type') in ('none', 'decline', 'external_table') else 'none', 'psty' + sid,
                          format_func={'none': 'None — IPR / tank model', 'decline': 'Arps decline curve', 'external_table': 'External table (reservoir simulator profile)'}.get)
        if t == 'none': p.pop('prediction_source', None); return
        if t != src.get('type'): src = {'type': t}
        if t == 'decline':
            src['basis'] = synced_select(st, 'Basis', ['oil', 'liquid', 'gas'], src.get('basis', 'oil'), 'psb' + sid)
            c = st.columns(3)
            src['qi'] = synced_number(c[0], 'qi [m³/d or Sm³/d]', float(clean_num(src.get('qi'), 800.0)), 'psq' + sid, 0.0, 1e9); src['di_per_year'] = synced_number(c[1], 'Di [1/yr]', float(clean_num(src.get('di_per_year'), 0.25)), 'psd' + sid, 0.0, 10.0, 0.01)
            src['b'] = synced_number(c[2], 'b [-]', float(clean_num(src.get('b'), 0.5)), 'psbx' + sid, 0.0, 1.0, 0.05)
            c = st.columns(2)
            src['q_abandon'] = synced_number(c[0], 'Abandonment rate', float(clean_num(src.get('q_abandon'), 50.0)), 'psab' + sid, 0.0, 1e9); src['terminal_di_per_year'] = synced_number(c[1], 'Terminal Di [1/yr]', float(clean_num(src.get('terminal_di_per_year'), 0.06)), 'pstd' + sid, 0.0, 5.0, 0.01)
            src['apply_as'] = 'rate_cap'
            st.caption('The decline is a potential cap: the well still follows IPR/VLP and network constraints and is limited to this potential.')
            with st.popover('Fit to history') if hasattr(st, 'popover') else st.expander('Fit to history'):
                h = st.text_area('Time [days], rate (two columns)', key='psfit' + sid, height=80)
                if h.strip() and st.button('Fit Arps', key='psfitb' + sid):
                    try:
                        arr = [tuple(float(x) for x in ln.replace(',', ' ').split()[:2]) for ln in h.strip().splitlines() if ln.strip()]
                        fit = fit_arps([a for a, _ in arr], [b for _, b in arr]); st.success(f'Fitted: qi={fit.get("qi", 0):.1f}, Di={fit.get("di_per_year", 0):.3f}/yr, b={fit.get("b", 0):.2f} — enter these above.')
                    except Exception as exc: st.error(str(exc))
        else:
            up = st.file_uploader('Simulator export (CSV: date or time_days, plus pressure / PI / water cut / GOR / rate columns)', type=['csv', 'txt'], key='psup' + sid)
            if up is not None and st.button('Load table', key='psload' + sid):
                try:
                    src['rows'], _w = parse_external_csv(up.getvalue().decode('utf-8', 'ignore')); src.setdefault('x_axis', 'date')
                    for _m in _w: st.caption('ℹ ' + str(_m))
                except Exception as exc: st.error(str(exc))
            src['x_axis'] = synced_select(st, 'Table axis', ['date', 'time_days', 'cum_oil_sm3'], src.get('x_axis', 'date'), 'psax' + sid)
            src['interp'] = synced_select(st, 'Interpolation', ['linear', 'step'], src.get('interp', 'linear'), 'psin' + sid); src['extrapolate'] = synced_select(st, 'Beyond the table', ['hold', 'linear', 'none'], src.get('extrapolate', 'hold'), 'psex' + sid)
            ed = st.data_editor(pd.DataFrame(src.get('rows') or []), num_rows='dynamic', use_container_width=True, key='pst' + sid + str(len(src.get('rows') or [])))
            src['rows'] = [{k: v for k, v in r.items() if clean_num(v) is not None or isinstance(v, str)} for r in ed.to_dict('records') if any(clean_num(v) is not None for v in r.values())]
        p['prediction_source'] = src
        try:
            import plotly.express as px
            pv = pd.DataFrame(prediction_preview(src, start_date, years=10, step_days=60))
            if not pv.empty:
                ycols = [c for c in pv.columns if c not in ('Date', 'Day')][:3]
                st.plotly_chart(_fig(px.line(pv, x='Date', y=ycols, title='What this source delivers'), height=240), use_container_width=True)
        except Exception: pass


def communication_editor(st, node, nodes):
    p = node.setdefault('params', {}); sid = node['id']; names = {n['id']: n.get('name', n['id']) for n in nodes if n.get('kind') == 'reservoir'}
    links = p.get('communication') or []
    incoming = [(n['id'], c) for n in nodes if n.get('kind') == 'reservoir' and n['id'] != sid for c in (n.get('params') or {}).get('communication') or [] if c.get('to') == sid]
    linked = {c['to'] for c in links} | {o for o, _ in incoming}
    free = [i for i in names if i != sid and i not in linked]
    if free:
        c1, c2 = st.columns([3, 1.3])
        tgt = c1.selectbox('Link this tank to another tank', free, format_func=lambda k: names.get(k, k), key='cadd' + sid, help='Same as dragging from this tank\'s OUT port onto the other tank on the canvas.')
        if c2.button('➕ Add link', key='cbtn' + sid, use_container_width=True):
            p.setdefault('communication', []).append({'to': tgt, 'transmissibility_m3d_bar': 100.0, 'max_transfer_m3d': None})
            st.session_state['_applied_note'] = f"Tank link {names.get(sid, sid)} to {names.get(tgt, tgt)} added (edit the transmissibility below). Press Apply changes to redraw."
            st.rerun()
        links = p.get('communication') or []
    if not links and not incoming:
        st.caption('Communication: drag from this tank\'s OUT port onto another tank in the editor, or use the selector above.'); return
    with st.expander(f'Tank communication ({len(links) + len(incoming)} links)', expanded=True):
        for i, c in enumerate(links):
            st.markdown(f"**→ {names.get(c['to'], c['to'])}**")
            c['transmissibility_m3d_bar'] = synced_number(st, 'Transmissibility [m³/d/bar]', float(clean_num(c.get('transmissibility_m3d_bar'), 100.0)), f'ct{sid}{i}', 0.0, 1e8, fmt='%.3g')
            mx = synced_number(st, 'Max transfer [m³/d] (0 = unlimited)', float(clean_num(c.get('max_transfer_m3d'), 0.0)), f'cm{sid}{i}', 0.0, 1e9)
            c['max_transfer_m3d'] = mx if mx > 0 else None
        for oid, c in incoming: st.caption(f"← {names.get(oid, oid)} (T = {c.get('transmissibility_m3d_bar')} m³/d/bar; edit on that tank)")
        st.caption('A link moves reservoir volume toward the lower-pressure tank each step (voidage for the donor, influx for the receiver). It is a planning-level transfer, not a flow simulation.')


def correlation_select(st, params, key, kind='flowline', default='Beggs-Brill', param_key='correlation', label=None):
    from physics.correlations import list_correlations
    opts = list_correlations(kind); cur = params.get(param_key, default)
    try:
        from physics.correlations import canonical_name
        cur = canonical_name(cur)
    except Exception: cur = default
    out = synced_select(st, label or 'Multiphase correlation', opts, cur if cur in opts else opts[0], key)
    params[param_key] = out
    return out
