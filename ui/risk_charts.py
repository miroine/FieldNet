"""Plotly figures for probabilistic production profiles (plotly imported lazily).

Convention shown on every chart: P90 = low case (10th percentile of values), P10 = high case
(90th percentile), P50 = median, Mean = arithmetic mean over realizations.
"""
from __future__ import annotations

LEGEND_NOTE = "P90 = low case, P10 = high case"


def _plotly():
    import plotly.graph_objects as go
    return go


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip('#')
    if len(h) != 6:
        return f'rgba(128,128,128,{alpha})'
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def variable_color(variable: str) -> str:
    from ui import charts
    v = variable.lower()
    if 'inj' in v: return charts.INJ
    if 'oil' in v or v == 'gor': return charts.OIL
    if 'gas' in v: return charts.GAS
    if 'water' in v: return charts.WATER
    if 'liquid' in v: return charts.LIQUID
    return charts.CATEGORICAL[0]


def _series(df, variable):
    d = df[df['Variable'] == variable]
    return d.sort_values('Day') if 'Day' in d else d


def _add_fan(fig, d, color, name_suffix='', row=None, col=None, show_legend=True):
    go = _plotly()
    kw = {} if row is None else {'row': row, 'col': col}
    x = d['Date']
    # P90 (low) first, then P10 (high) filled down to it -> shaded P90-P10 band
    fig.add_trace(go.Scatter(x=x, y=d['P90'], mode='lines', line=dict(width=0.6, color=_rgba(color, 0.55)),
                             name='P90 (low)' + name_suffix, legendgroup='band', showlegend=False,
                             hovertemplate='P90 (low): %{y:,.4g}<extra></extra>'), **kw)
    fig.add_trace(go.Scatter(x=x, y=d['P10'], mode='lines', line=dict(width=0.6, color=_rgba(color, 0.55)),
                             fill='tonexty', fillcolor=_rgba(color, 0.22),
                             name='P90–P10 band (' + LEGEND_NOTE + ')', legendgroup='band', showlegend=show_legend,
                             hovertemplate='P10 (high): %{y:,.4g}<extra></extra>'), **kw)
    fig.add_trace(go.Scatter(x=x, y=d['P50'], mode='lines', line=dict(width=2.2, color=color),
                             name='P50 (median)', legendgroup='p50', showlegend=show_legend,
                             hovertemplate='P50: %{y:,.4g}<extra></extra>'), **kw)
    fig.add_trace(go.Scatter(x=x, y=d['Mean'], mode='lines', line=dict(width=1.8, color=color, dash='dash'),
                             name='Mean', legendgroup='mean', showlegend=show_legend,
                             hovertemplate='Mean: %{y:,.4g}<extra></extra>'), **kw)


def fan_chart(df, variable, title=None, unit=None, height=380):
    """P90-P10 shaded band + P50 line + dashed Mean for one variable of the profiles DataFrame."""
    from ui import charts
    go = _plotly()
    d = _series(df, variable)
    unit = unit if unit is not None else (d['Unit'].iloc[0] if len(d) else '')
    fig = go.Figure()
    if len(d):
        _add_fan(fig, d, variable_color(variable))
    cat = d['Category'].iloc[0] if len(d) else ''
    n_min = int(d['N'].min()) if len(d) else 0
    n_max = int(d['N'].max()) if len(d) else 0
    nlabel = f'N={n_max}' if n_min == n_max else f'N={n_min}–{n_max}'
    fig = charts.style(fig, title or variable, f'{variable} [{unit}]' if unit else variable, f'Date ({LEGEND_NOTE}; {nlabel})', height, True)
    if cat == 'Pressure':
        fig.update_yaxes(rangemode='normal')
    return fig


def profiles_grid(df, category, variables=None, cols=2, height_per_row=300):
    """Small-multiple fan charts for every variable of a category ('Rate'|'Cumulative'|'Pressure')."""
    from plotly.subplots import make_subplots
    d = df[df['Category'] == category]
    names = list(variables) if variables is not None else list(dict.fromkeys(d['Variable']))
    names = [n for n in names if n in set(d['Variable'])]
    if not names:
        return _plotly().Figure()
    cols = max(1, min(int(cols), len(names)))
    rows = -(-len(names) // cols)
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=[f"{n} [{d[d['Variable'] == n]['Unit'].iloc[0]}]" for n in names],
                        shared_xaxes=False, horizontal_spacing=0.08, vertical_spacing=min(0.12, 0.5 / rows))
    for i, n in enumerate(names):
        _add_fan(fig, _series(d, n), variable_color(n), row=i // cols + 1, col=i % cols + 1, show_legend=(i == 0))
    fig.update_layout(height=max(320, height_per_row * rows), margin=dict(l=10, r=10, t=60, b=10),
                      paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', hovermode='x unified',
                      legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1), font=dict(size=12),
                      title=dict(text=f'{category} profiles – {LEGEND_NOTE}', x=0, xanchor='left', font=dict(size=15)))
    fig.update_yaxes(gridcolor='rgba(128,128,128,0.18)', zeroline=False, rangemode='tozero' if category != 'Pressure' else 'normal')
    fig.update_xaxes(showgrid=False)
    return fig
