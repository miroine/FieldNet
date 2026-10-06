"""Browse forecast / development results by simulated date and by element, GAP-style: the network diagram is redrawn with the
pressures and rates of the chosen date, and any node or flowline can be inspected over time."""
from __future__ import annotations
import pandas as pd


def forecast_dates(fc):
    """Report dates that have element results (empty when the run was made without per-element profiles)."""
    return sorted({str(r['Date']) for r in (fc or {}).get('nodes', []) if r.get('Date') is not None})


def state_at(fc, date):
    """(node_rows, edge_rows) of one report date."""
    d = str(date)
    return [r for r in fc.get('nodes', []) if str(r.get('Date')) == d], [r for r in fc.get('edges', []) if str(r.get('Date')) == d]


def diagram_labels(node_rows, edge_rows):
    """Text per node and flow per edge for ``ui.svg_export.network_svg``."""
    labels = {}
    for r in node_rows:
        parts = []
        if r.get('Pressure [bar]') is not None: parts.append(f"{r['Pressure [bar]']:.1f} bar")
        if r.get('Kind') == 'well' and r.get('Oil [m3/d]') is not None:
            parts.append('off' if (r.get('Liquid [m3/d]') or 0) <= 1e-6 else f"{r['Oil [m3/d]']:,.0f} Sm³/d oil")
        elif r.get('Kind') not in ('well', 'reservoir', 'joint') and (r.get('Oil [m3/d]') or 0) > 0:
            parts.append(f"{r['Oil [m3/d]']:,.0f} oil")
        labels[r['Node ID']] = ' · '.join(parts)
    rates = {r['Edge ID']: r.get('Flow [m3/d]') for r in edge_rows if r.get('Flow [m3/d]') is not None}
    return labels, rates


def network_svg_at(nodes, edges, fc, date, mode=None, phase=None, thickness=True):
    """Network diagram at one report date. Line thickness grows with the flow on that date, on a scale fixed by the largest flow of any date."""
    from ui.svg_export import network_svg
    from network.net_display import labels_from_rows, edge_widths, max_flow, AUTO
    nr, er = state_at(fc, date); labels, rates = diagram_labels(nr, er)
    elabels = {}
    if mode is not None or phase is not None: labels, elabels = labels_from_rows(nr, er, mode or AUTO, phase)
    widths = edge_widths(rates, qmax=max_flow(fc.get('edges', []))) if thickness else None
    return network_svg(nodes, edges, labels=labels, rates=rates, title=f'Network at {date}', edge_labels=elabels, widths=widths)


def render_results_browser(st, nodes, edges, fc, key='rb'):
    dates = forecast_dates(fc)
    if not dates:
        st.info('No per-element results stored. Re-run with **Store per-element profiles** switched on to browse the network by date.'); return
    st.markdown('#### Network at a chosen date')
    date = st.select_slider('Simulated date', options=dates, value=dates[0], key=f'{key}_date')
    frow = next((r for r in fc['field'] if str(r.get('Date')) == str(date)), None)
    if frow:
        c = st.columns(5)
        gas = st.session_state.get('_phase_resolved') == 'Gas'
        oil_m = lambda col: col.metric('Condensate / oil' if gas else 'Oil', f"{frow.get('Oil [m3/d]', 0):,.0f} Sm³/d"); gas_m = lambda col: col.metric('Gas', f"{frow.get('Gas [Sm3/d]', 0) / 1e6:,.2f} MSm³/d")
        (gas_m(c[0]), oil_m(c[2])) if gas else (oil_m(c[0]), gas_m(c[2])); c[1].metric('Water', f"{frow.get('Water [m3/d]', 0):,.0f} m³/d")
        c[3].metric('Wells flowing', f"{frow.get('Wells flowing', 0)}")
        c[4].metric('Cumulative gas', f"{frow.get('Cumulative gas [Sm3]', 0)/1e9:,.2f} GSm³") if gas else c[4].metric('Cumulative oil', f"{frow.get('Cumulative oil [Sm3]', 0)/1e6:,.2f} MSm³")
    from network.net_display import MODES, AUTO
    o1, o2 = st.columns([2, 1])
    show = o1.selectbox('Show on network', MODES, index=0, key=f'{key}_show'); thick = o2.checkbox('Line thickness follows flow', value=True, key=f'{key}_thick', help='Scaled to the largest flow of any report date, so moving the slider shows lines growing and thinning.')
    svg = network_svg_at(nodes, edges, fc, date, mode=show, phase=st.session_state.get('_phase_resolved'), thickness=thick)
    try:
        import streamlit.components.v1 as components
        components.html(f'<div style="overflow:auto">{svg}</div>', height=620, scrolling=True)
    except Exception: st.markdown(svg, unsafe_allow_html=True)
    st.download_button('Download this diagram (SVG)', svg, f'fieldnet_network_{date}.svg', 'image/svg+xml', key=f'{key}_svgdl')
    nr, er = state_at(fc, date)
    t1, t2 = st.tabs([f'Nodes at {date}', f'Flowlines & equipment at {date}'])
    t1.dataframe(pd.DataFrame(nr).drop(columns=['Date'], errors='ignore'), hide_index=True, use_container_width=True)
    t2.dataframe(pd.DataFrame(er).drop(columns=['Date'], errors='ignore'), hide_index=True, use_container_width=True)
    st.markdown('#### Any element over time')
    from ui.element_view import element_choices, element_series, series_variables
    ch = element_choices(nodes, edges)
    if not ch: return
    labels = {c[0]: c[1] for c in ch}
    sel = st.selectbox('Element', [c[0] for c in ch], format_func=lambda k: labels[k], key=f'{key}_elem')
    vars_ = series_variables(fc, sel)
    if not vars_: st.caption('No time series for this element.'); return
    v = st.selectbox('Variable', vars_, key=f'{key}_var'); ts = element_series(fc, sel, v)
    if ts.empty: return
    import plotly.graph_objects as go
    fig = go.Figure(go.Scatter(x=ts['Date'], y=ts[v], mode='lines+markers', name=v))
    fig.add_shape(type='line', x0=date, x1=date, y0=0, y1=1, yref='paper', line=dict(color='#c0392b', dash='dash'))
    fig.update_layout(title=f'{labels[sel]} — {v}', height=340, showlegend=False, yaxis_title=v)
    st.plotly_chart(fig, use_container_width=True, key=f'{key}_fig')
