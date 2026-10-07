"""Consistent Plotly styling for FieldNet.

Phase colours follow a colour-blind-validated categorical palette (checked with the dataviz
validator): oil = aqua, gas = orange, water = blue. Different units never share an axis:
liquids (m3/d) and gas (Sm3/d) are separate charts.
"""
from __future__ import annotations
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from ui.shapes import PHASE_COLOR
OIL = PHASE_COLOR['oil']; GAS = PHASE_COLOR['gas']; WATER = PHASE_COLOR['water']; LIQUID = '#6b6a66'; INJ = '#4a3aa7'
CATEGORICAL = ['#2a78d6', '#eb9a34', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#6b6a66']   # no red: red is reserved for gas
PHASE_COLORS = {'Oil': OIL, 'Gas': GAS, 'Water': WATER, 'Liquid': LIQUID, 'Water injection': INJ}


def style(fig, title=None, y=None, x=None, height=340, legend=True):
    if title: fig.update_layout(title=dict(text=title, x=0, xanchor='left', font=dict(size=15)))
    else: fig.update_layout(title=dict(x=0, xanchor='left', font=dict(size=15)))
    fig.update_layout(
        height=height, margin=dict(l=10, r=10, t=48, b=10),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', hovermode='x unified',
        legend=dict(orientation='h', yanchor='bottom', y=1.0, xanchor='right', x=1, title=None) if legend else None,
        showlegend=legend, font=dict(size=12))
    if x is not None: fig.update_xaxes(title=x)
    if y is not None: fig.update_yaxes(title=y)
    fig.update_xaxes(showgrid=False, linecolor='rgba(128,128,128,0.4)', ticks='outside')
    fig.update_yaxes(gridcolor='rgba(128,128,128,0.18)', zeroline=False, rangemode='tozero')
    return fig


def series_color(name, i=0):
    """Phase colour for a series by its name (gas red, oil green, water blue, liquid grey, injection violet); categorical otherwise."""
    v = str(name).lower().split(' [')[0]
    if 'inj' in v: return INJ
    if 'water' in v: return WATER
    if 'gas' in v or v == 'gor': return GAS
    if 'oil' in v or 'condensate' in v or 'cgr' in v: return OIL
    if 'liquid' in v: return LIQUID
    return CATEGORICAL[i % len(CATEGORICAL)]


def lines(df, x, cols, title, y, colors=None, dash=None, height=340):
    fig = go.Figure()
    for i, c in enumerate(cols):
        if c not in df: continue
        name = c.split(' [')[0]
        fig.add_trace(go.Scatter(x=df[x], y=df[c], name=name, mode='lines',
                                 line=dict(width=2, color=(colors or {}).get(c, series_color(c, i)), dash=(dash or {}).get(c))))
    return style(fig, title, y, None, height, legend=len(cols) > 1)


def stacked_area(df, x, y, color, title, ytitle, height=340):
    cats = list(dict.fromkeys(df[color].tolist()))
    fig = px.area(df, x=x, y=y, color=color, color_discrete_sequence=CATEGORICAL, category_orders={color: cats})
    fig.update_traces(line=dict(width=1))
    return style(fig, title, ytitle, None, height)


def by_category_lines(df, x, y, color, title, ytitle, height=340):
    cats = list(dict.fromkeys(df[color].tolist()))
    fig = px.line(df, x=x, y=y, color=color, color_discrete_sequence=CATEGORICAL, category_orders={color: cats})
    fig.update_traces(line=dict(width=2))
    return style(fig, title, ytitle, None, height, legend=len(cats) > 1)


def bars(df, x, y, title, ytitle, color=OIL, height=320, text=None):
    fig = go.Figure(go.Bar(x=df[x], y=df[y], marker=dict(color=color), text=text, textposition='outside'))
    return style(fig, title, ytitle, None, height, legend=False)


def gantt(df, title='Development schedule', height=None):
    cats = list(dict.fromkeys(df['resource'].tolist()))
    fig = px.timeline(df, x_start='start', x_end='finish', y='name', color='resource', color_discrete_sequence=CATEGORICAL, category_orders={'resource': cats})
    fig.update_yaxes(autorange='reversed')
    return style(fig, title, None, None, height or max(220, 40 + 34 * len(df)))


def kpi(col, label, value, help=None):
    col.metric(label, value, help=help)
