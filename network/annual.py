"""Annual (calendar-year) volumes from forecast rate tables - the numbers behind yearly bar charts.

Forecast rows hold *average rates over the step that starts at the row date* (the step length is the distance to the next row; the
last step repeats the previous length). Annual volumes integrate each step over the calendar years it overlaps, so a 180-day step
across 1 January is split correctly and the sum over the years equals the integral of the profile exactly."""
from __future__ import annotations
import pandas as pd

OIL, GAS, WATER, LIQ, WINJ = 'Oil [m3/d]', 'Gas [Sm3/d]', 'Water [m3/d]', 'Total liquid [m3/d]', 'Water injection [m3/d]'
FIELD_RATES = [OIL, GAS, WATER, LIQ, WINJ]
VOLUME_NAME = {OIL: 'Oil [Sm3]', GAS: 'Gas [Sm3]', WATER: 'Water [m3]', LIQ: 'Liquid [m3]', WINJ: 'Water injection [m3]',
               'Liquid [m3/d]': 'Liquid [m3]', 'Gas lift [Sm3/d]': 'Gas lift [Sm3]'}

# unit systems for the bars: (oil/liquid/water/injection factor, label), (gas factor, label)
UNITS = {
    'MSm3 (oil, water) / GSm3 (gas)': {'liq': (1e-6, 'MSm3'), 'gas': (1e-9, 'GSm3')},
    'Sm3': {'liq': (1.0, 'Sm3'), 'gas': (1.0, 'Sm3')},
    'mmbbl (oil, water) / bcf (gas)': {'liq': (6.28981e-6, 'mmbbl'), 'gas': (35.3147e-9, 'bcf')},
}
OE_GAS_PER_OIL = 1000.0          # Norwegian convention: 1 Sm3 o.e. = 1 Sm3 oil = 1000 Sm3 gas


def step_table(dates, step_days=None):
    """DataFrame (start, end, days) for each reporting step. ``step_days`` (the forecast's own 'Step [days]' column) is used when given,
    which handles the shorter last step; otherwise the step ends at the next row and the last step repeats the previous length."""
    d = pd.to_datetime(pd.Series(dates)).reset_index(drop=True)
    if len(d) == 0: return pd.DataFrame(columns=['start', 'end', 'days'])
    if step_days is not None:
        sd = pd.Series(step_days).reset_index(drop=True).astype(float)
        return pd.DataFrame({'start': d, 'end': d + pd.to_timedelta(sd, unit='D'), 'days': sd})
    ends = list(d.iloc[1:]) + [d.iloc[-1] + (d.iloc[-1] - d.iloc[-2] if len(d) > 1 else pd.Timedelta(days=30))]
    return pd.DataFrame({'start': d, 'end': pd.to_datetime(ends)}).assign(days=lambda x: (x['end'] - x['start']).dt.days.clip(lower=1))


def annual_volumes(df, rate_cols, date_col='Date'):
    """Calendar-year volumes of the rate columns (``X [unit/d]`` -> volumes named ``X [unit]``). Adds 'Days' (covered days of the year) and
    'Partial' (True when the profile does not cover the whole year). Rates and volumes use the same unit base (m3 / Sm3)."""
    if df is None or len(df) == 0: return pd.DataFrame(columns=['Year', 'Days', 'Partial'])
    steps = step_table(df[date_col], df['Step [days]'] if 'Step [days]' in df else None); acc = {}; days = {}
    for i, s in steps.iterrows():
        a, b = s['start'], s['end']; y = a.year
        while a < b:
            ye = pd.Timestamp(year=y + 1, month=1, day=1); seg_end = min(b, ye); d = (seg_end - a).days
            if d > 0:
                days[y] = days.get(y, 0) + d
                for c in rate_cols:
                    v = df[c].iloc[i]; v = 0.0 if v is None or v != v else float(v)
                    acc.setdefault(y, {}).setdefault(c, 0.0); acc[y][c] += v * d
            a = seg_end; y += 1
    rows = []
    for y in sorted(acc):
        full = 366 if pd.Timestamp(year=y, month=12, day=31).dayofyear == 366 else 365
        r = {'Year': y, 'Days': days[y], 'Partial': days[y] < full - 1}
        for c in rate_cols: r[VOLUME_NAME.get(c, c.replace('/d]', ']'))] = acc[y][c]
        rows.append(r)
    out = pd.DataFrame(rows)
    for c in rate_cols:
        v = VOLUME_NAME.get(c, c.replace('/d]', ']'))
        if v in out: out['Cum ' + v] = out[v].cumsum()
    return out


def _step_map(forecast):
    f = forecast.get('field') or []
    return {r['Date']: r.get('Step [days]') for r in f if r.get('Step [days]') is not None}


def _with_steps(g, smap):
    g = g.copy()
    if smap and all(d in smap for d in g['Date']): g['Step [days]'] = [smap[d] for d in g['Date']]
    return g


def field_annual(forecast):
    f = pd.DataFrame(forecast.get('field') or [])
    if f.empty: return pd.DataFrame()
    cols = [c for c in FIELD_RATES if c in f]
    return annual_volumes(f, cols)


def wells_annual(forecast, rate_cols=(OIL, 'Gas [Sm3/d]', 'Water [m3/d]', 'Liquid [m3/d]')):
    w = pd.DataFrame(forecast.get('wells') or [])
    if w.empty: return pd.DataFrame()
    cols = [c for c in rate_cols if c in w]; out = []
    smap = _step_map(forecast)
    for (wid, name), g in w.groupby(['Well ID', 'Well'], sort=False):
        a = annual_volumes(_with_steps(g.reset_index(drop=True), smap), cols)
        if len(a): a.insert(0, 'Well', name); a.insert(1, 'Well ID', wid); out.append(a)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def tanks_annual(forecast):
    """Per tank and year: produced / injected / influx volumes (differences of the cumulative columns; year-end value by step interpolation)."""
    t = pd.DataFrame(forecast.get('tanks') or [])
    if t.empty: return pd.DataFrame()
    cums = {'Cum oil [Sm3]': 'Oil [Sm3]', 'Cum gas [Sm3]': 'Gas [Sm3]', 'Cum water [m3]': 'Water [m3]', 'Cum water inj [m3]': 'Water injection [m3]',
            'Aquifer influx [m3]': 'Aquifer influx [m3]', 'Net communication [m3]': 'Net communication [m3]'}
    out = []; smap = _step_map(forecast)
    for (tid, name), g in t.groupby(['Tank ID', 'Tank'], sort=False):
        g = _with_steps(g.reset_index(drop=True), smap); steps = step_table(g['Date'], g['Step [days]'] if 'Step [days]' in g else None)
        rate = pd.DataFrame({'Date': g['Date']})
        if 'Step [days]' in g: rate['Step [days]'] = g['Step [days]'].values
        for c in cums:
            if c not in g: continue
            d = g[c].diff().fillna(g[c].iloc[0]); rate[cums[c] + '/d'] = (d / steps['days'].where(steps['days'] > 0, float('nan'))).fillna(0.0).values
        cols = [c for c in rate.columns if c != 'Date']
        a = annual_volumes(rate, cols)
        if len(a):
            a.columns = [c.replace('/d]', ']').replace('/d', '') for c in a.columns]
            last = g.groupby(pd.to_datetime(g['Date']).dt.year).last()
            a['Pressure end of year [bar]'] = [float(last['Pressure [bar]'].get(y, float('nan'))) for y in a['Year']]
            a.insert(0, 'Tank', name); a.insert(1, 'Tank ID', tid); out.append(a)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def convert(df, unit='MSm3 (oil, water) / GSm3 (gas)', oe=False):
    """Scale an annual table for plotting. Returns (DataFrame, {column: label})."""
    u = UNITS[unit]; out = df.copy(); labels = {}
    for c in list(out.columns):
        if c in ('Year', 'Days', 'Partial', 'Well', 'Well ID', 'Tank', 'Tank ID', 'Group') or 'pressure' in c.lower(): continue
        base = c.replace('Cum ', '')
        fac, lab = u['gas'] if base.startswith('Gas') else u['liq']
        out[c] = out[c] * fac; labels[c] = f"{c.split(' [')[0]} [{lab}]"
    if oe and 'Oil [Sm3]' in df and 'Gas [Sm3]' in df:
        fac, lab = u['liq']; out['Oil equivalent [Sm3 o.e.]'] = (df['Oil [Sm3]'] + df['Gas [Sm3]'] / OE_GAS_PER_OIL) * fac; labels['Oil equivalent [Sm3 o.e.]'] = f'Oil equivalent [{lab} o.e.]'
    return out.rename(columns=labels), labels


def bar_figure(df, columns, title, unit_label='', stacked=False, cumulative=None, height=380):
    """Plotly bar chart by year (partial years are hatched via lighter opacity). ``cumulative`` adds a separate line subplot (no dual axes)."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    from ui.charts import style, CATEGORICAL
    rows = 2 if cumulative else 1
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.08, row_heights=[0.65, 0.35] if cumulative else [1.0])
    years = df['Year'].tolist(); part = df['Partial'].tolist() if 'Partial' in df else [False] * len(years)
    for i, c in enumerate(columns):
        fig.add_trace(go.Bar(x=years, y=df[c], name=c, marker=dict(color=CATEGORICAL[i % len(CATEGORICAL)], opacity=[0.45 if p else 0.95 for p in part], line=dict(width=0))), row=1, col=1)
    fig.update_layout(barmode='stack' if stacked else 'group')
    if cumulative:
        for i, c in enumerate(cumulative): fig.add_trace(go.Scatter(x=years, y=df[c], name=c, mode='lines+markers', line=dict(color=CATEGORICAL[i % len(CATEGORICAL)], width=2)), row=2, col=1)
    fig = style(fig, title, height=height + (160 if cumulative else 0)); fig.update_xaxes(dtick=1)
    return fig
