"""Element browser: results and profiles for any node or flowline of the solved network, and their time series from the forecast."""
from __future__ import annotations
import pandas as pd


def element_choices(nodes, edges):
    names = {n['id']: n.get('name', n['id']) for n in nodes}
    out = [(n['id'], f"{n.get('name', n['id'])} ({n.get('kind')})", 'node') for n in nodes if n.get('kind') != 'reservoir']
    out += [(e['id'], f"{names.get(e['source'], e['source'])} → {names.get(e['target'], e['target'])} ({e.get('kind', 'pipeline')})", 'edge') for e in edges]
    return out


def edge_profile_frame(edge, results):
    """Pressure / velocity / holdup profile of a flowline as a DataFrame (empty for equipment or an unsolved model)."""
    from network.element_results import edge_profile, phase_flows
    if not results or not results[0]: return pd.DataFrame()
    p, q, info, d = results; flow = q.get(edge['id'])
    if flow is None or edge.get('kind', 'pipeline') != 'pipeline': return pd.DataFrame()
    return pd.DataFrame(edge_profile(edge, flow, p[edge['source']] if flow >= 0 else p[edge['target']], info))


def well_profile_frame(node, results):
    from network.element_results import well_profile
    if not results or not results[0]: return pd.DataFrame()
    p, q, info, d = results; dd = d.get(node['id'])
    if not dd or dd['liquid_rate_m3d'] <= 1e-6: return pd.DataFrame()
    return pd.DataFrame(well_profile(node, dd['liquid_rate_m3d'], p[node['id']]))


def element_series(forecast, element_id, variable):
    """Time series (Date, value) of ``variable`` for a node or edge from ``run_forecast`` element rows."""
    if not forecast: return pd.DataFrame()
    for key, idcol in (('nodes', 'Node ID'), ('edges', 'Edge ID')):
        rows = [r for r in forecast.get(key, []) if r.get(idcol) == element_id and r.get(variable) is not None]
        if rows: return pd.DataFrame({'Date': [r['Date'] for r in rows], variable: [r[variable] for r in rows]})
    return pd.DataFrame()


def series_variables(forecast, element_id):
    for key, idcol in (('nodes', 'Node ID'), ('edges', 'Edge ID')):
        rows = [r for r in (forecast or {}).get(key, []) if r.get(idcol) == element_id]
        if rows: return [k for k, v in rows[0].items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return []


def render_element_results(st, nodes, edges, results, forecast=None):
    from network.element_results import element_rows
    from ui import charts
    import plotly.express as px
    if not results or not results[0]:
        st.info('Solve the network (Network tab) to see element results.'); return
    p, q, info, d = results
    nr, er = element_rows(nodes, edges, p, q, d, info)
    t1, t2 = st.tabs(['Nodes', 'Flowlines & equipment'])
    t1.dataframe(pd.DataFrame(nr), hide_index=True, use_container_width=True); t2.dataframe(pd.DataFrame(er), hide_index=True, use_container_width=True)
    choices = element_choices(nodes, edges)
    if not choices: return
    labels = {c[0]: c[1] for c in choices}; kinds = {c[0]: c[2] for c in choices}
    sel = st.selectbox('Element', [c[0] for c in choices], format_func=lambda k: labels[k], key='elem_sel')
    if kinds[sel] == 'edge':
        e = next(x for x in edges if x['id'] == sel); row = next((r for r in er if r['Edge ID'] == sel), {})
        c = st.columns(4); c[0].metric('Flow [m³/d]', f"{row.get('Flow [m3/d]', 0):,.0f}"); c[1].metric('ΔP [bar]', f"{(row.get('dP [bar]') or 0):.2f}")
        c[2].metric('Max velocity [m/s]', f"{(row.get('Max velocity [m/s]') or 0):.2f}"); c[3].metric('Max erosional ratio', f"{(row.get('Max erosional ratio [-]') or 0):.2f}")
        prof = edge_profile_frame(e, results)
        if not prof.empty:
            a, b = st.columns(2)
            a.plotly_chart(charts.style(px.line(prof, x='x_m', y='pressure_bar', title='Pressure along the line'), x='Distance [m]', y='Pressure [bar]', legend=False, height=280), use_container_width=True)
            b.plotly_chart(charts.style(px.line(prof.dropna(subset=['velocity_ms']), x='x_m', y=['velocity_ms'], title='Mixture velocity'), x='Distance [m]', y='Velocity [m/s]', legend=False, height=280), use_container_width=True)
            a, b = st.columns(2)
            a.plotly_chart(charts.style(px.line(prof, x='x_m', y='z_m', title='Elevation'), x='Distance [m]', y='Elevation [m]', legend=False, height=240), use_container_width=True)
            b.plotly_chart(charts.style(px.line(prof.dropna(subset=['holdup']), x='x_m', y='holdup', title='Liquid holdup'), x='Distance [m]', y='Holdup [-]', legend=False, height=240), use_container_width=True)
            st.dataframe(prof, hide_index=True, use_container_width=True)
        else:
            inl = (info.get('inline_equipment') or {}).get(sel)
            if inl: st.json(inl)
    else:
        n = next(x for x in nodes if x['id'] == sel); row = next((r for r in nr if r['Node ID'] == sel), {})
        st.json({k: v for k, v in row.items() if v is not None})
        if n.get('kind') == 'well':
            prof = well_profile_frame(n, results)
            if not prof.empty:
                a, b = st.columns(2)
                a.plotly_chart(charts.style(px.line(prof, x='pressure_bar', y='tvd_m', title='Tubing pressure vs depth'), x='Pressure [bar]', y='TVD [m]', legend=False, height=320).update_yaxes(autorange='reversed'), use_container_width=True)
                b.plotly_chart(charts.style(px.line(prof.dropna(subset=['velocity_ms']), x='velocity_ms', y='tvd_m', title='Mixture velocity vs depth'), x='Velocity [m/s]', y='TVD [m]', legend=False, height=320).update_yaxes(autorange='reversed'), use_container_width=True)
                st.dataframe(prof, hide_index=True, use_container_width=True)
    vars_ = series_variables(forecast, sel)
    if vars_:
        st.markdown('**Over time (forecast)**')
        v = st.selectbox('Variable', vars_, key='elem_var'); ts = element_series(forecast, sel, v)
        if not ts.empty: st.plotly_chart(charts.style(px.line(ts, x='Date', y=v, title=f'{labels[sel]} — {v}'), y=v, legend=False), use_container_width=True)
    elif forecast: st.caption('Run a forecast to see this element over time.')
