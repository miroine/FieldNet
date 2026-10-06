"""What to print on the network canvas and in the network results: pick the parameter (oil / gas / water rate, pressure, velocity, ...).

Pure Python. Values come from ``element_rows`` (the same table as Results -> Element results), so the canvas, the tables and the exports agree.
Gas is always shown in MSm3/d (kSm3/d below 10 000 Sm3/d)."""
from __future__ import annotations
from network.element_results import element_rows

AUTO = 'Auto (oil field: liquid & water cut, gas field: gas rate)'
MODES = [AUTO, 'Liquid rate & water cut', 'Oil rate', 'Gas rate', 'Water rate', 'Oil / gas / water rates', 'Pressure', 'Bottom-hole pressure (wells)', 'GOR (gas / oil)',
         'Pressure drop & velocity (lines)', 'Erosional ratio (lines)', 'Nothing (names only)']
_SKIP_NODE = ('reservoir',)


def fmt_gas(v):
    if v is None: return '—'
    v = float(v); return f'{v / 1e6:,.2f} MSm³/d' if abs(v) >= 1e4 else f'{v / 1e3:,.1f} kSm³/d'


def _n(v, unit, d=0): return '—' if v is None else f'{float(v):,.{d}f} {unit}'


def field_is_gas(rows):
    """True when the produced gas dominates on an oil-equivalent basis (1 Sm3 oil = 1000 Sm3 gas)."""
    oil = sum((r.get('Oil [m3/d]') or 0) for r in rows if r.get('Status') is not None); gas = sum((r.get('Gas [Sm3/d]') or 0) for r in rows if r.get('Status') is not None)
    return gas / 1000.0 > oil


def resolve_mode(mode, node_rows, phase=None):
    if mode != AUTO: return mode
    gas = (phase == 'Gas') if phase in ('Gas', 'Oil') else field_is_gas(node_rows)
    return 'Gas rate' if gas else 'Liquid rate & water cut'


def _node_text(mode, r, edge_through=False):
    liq, oil, wat, gas = r.get('Liquid [m3/d]'), r.get('Oil [m3/d]'), r.get('Water [m3/d]'), r.get('Gas [Sm3/d]')
    if mode == 'Liquid rate & water cut':
        if liq is None: return None
        return f"{liq:,.0f} m³/d · WC {((wat or 0) / liq if liq > 1e-9 else 0):.0%}"
    if mode == 'Oil rate': return None if oil is None else _n(oil, 'Sm³/d')
    if mode == 'Gas rate': return None if gas is None else fmt_gas(gas)
    if mode == 'Water rate': return None if wat is None else _n(wat, 'm³/d')
    if mode == 'Oil / gas / water rates':
        if oil is None and gas is None: return None
        return f"O {_n(oil, 'Sm³/d')} · G {fmt_gas(gas)} · W {_n(wat, 'm³/d')}"
    if mode == 'Pressure': return None if r.get('Pressure [bar]') is None else _n(r['Pressure [bar]'], 'bar', 1)
    if mode == 'Bottom-hole pressure (wells)': return None if r.get('BHP [bar]') is None else f"BHP {r['BHP [bar]']:,.1f} bar"
    if mode == 'GOR (gas / oil)':
        if oil is None or gas is None: return None
        return f"GOR {gas / oil:,.0f} Sm³/Sm³" if oil > 1e-9 else 'no oil'
    return None


def _edge_text(mode, r):
    kind = r.get('Kind') or 'pipeline'; liq = r.get('Flow [m3/d]'); oil, wat, gas = r.get('Oil [m3/d]'), r.get('Water [m3/d]'), r.get('Gas [Sm3/d]')
    if mode == 'Liquid rate & water cut': return f"{kind} • {abs(liq):,.0f} m³/d" if liq is not None else kind
    if mode == 'Oil rate': return f"{kind} • {_n(oil, 'Sm³/d')}" if oil is not None else kind
    if mode == 'Gas rate': return f"{kind} • {fmt_gas(gas)}" if gas is not None else kind
    if mode == 'Water rate': return f"{kind} • {_n(wat, 'm³/d')}" if wat is not None else kind
    if mode == 'Oil / gas / water rates': return f"{kind} • O {_n(oil, '')} · G {fmt_gas(gas)} · W {_n(wat, '')}"
    if mode == 'Pressure drop & velocity (lines)':
        dp, v = r.get('dP [bar]'), r.get('Max velocity [m/s]')
        return f"{kind} • ΔP {dp:+.1f} bar" + (f" · {v:.1f} m/s" if v is not None else '') if dp is not None else kind
    if mode == 'Erosional ratio (lines)':
        er = r.get('Max erosional ratio [-]'); return f"{kind} • erosion {er:.2f}" if er is not None else kind
    if mode in ('Pressure', 'Bottom-hole pressure (wells)', 'GOR (gas / oil)'):
        dp = r.get('dP [bar]'); return f"{kind} • ΔP {dp:+.1f} bar" if dp is not None and mode == 'Pressure' else kind
    return kind


def label_maps(nodes, edges, results, mode=AUTO, phase=None):
    """({node_id: text}, {edge_id: text}) for the canvas. Empty maps when unsolved or mode is 'Nothing'."""
    if not results or not results[0]: return {}, {}
    p, q, info, d = results; nr, er = element_rows(nodes, edges, p, q, d, info); m = resolve_mode(mode, nr, phase)
    if m.startswith('Nothing'): return {}, {r['Edge ID']: (r.get('Kind') or 'pipeline') for r in er}
    nl, el = {}, {}
    inline = (info or {}).get('inline_equipment') or {}; inj = (info or {}).get('injector_rates') or {}
    kinds = {n['id']: n.get('kind') for n in nodes}
    for r in nr:
        nid = r['Node ID']; k = kinds.get(nid)
        if k in _SKIP_NODE: continue
        status = r.get('Status')
        if status in ('shut_in', 'dead', 'shut_in_below_min_rate'): nl[nid] = f"{status.replace('_', ' ')} · {r.get('Pressure [bar]') or 0:.1f} bar"; continue
        if k in ('water_injector', 'gas_injector', 'injector') and nid in inj:
            nl[nid] = f"{inj[nid]:,.0f} m³/d injected" if m in ('Liquid rate & water cut', 'Oil rate', 'Water rate', 'Gas rate', 'Oil / gas / water rates') else _node_text('Pressure', r); continue
        txt = _node_text(m, r)
        if txt is None: txt = _node_text('Pressure', r)
        if txt is not None: nl[nid] = txt + (' · limited' if status == 'rate_limited' else '')
    for r in er: el[r['Edge ID']] = _edge_text(m, r)
    return nl, el


# ------------------------------------------------------------------ browse tables
NODE_PARAMS = {'Pressure [bar]': 1, 'BHP [bar]': 1, 'Liquid [m3/d]': 0, 'Oil [m3/d]': 0, 'Water [m3/d]': 0, 'Gas [Sm3/d]': 1e6}
EDGE_PARAMS = {'Flow [m3/d]': 0, 'Oil [m3/d]': 0, 'Water [m3/d]': 0, 'Gas [Sm3/d]': 1e6, 'dP [bar]': 1, 'Max velocity [m/s]': 1, 'Max erosional ratio [-]': 1}


def browse_frame(nodes, edges, results, scope, parameter):
    """DataFrame (Name, Kind, value) of one parameter for all nodes ('Nodes') or lines ('Lines'). Gas is returned in MSm3/d."""
    import pandas as pd
    if not results or not results[0]: return pd.DataFrame()
    p, q, info, d = results; nr, er = element_rows(nodes, edges, p, q, d, info)
    rows = nr if scope == 'Nodes' else er; col = parameter; label = parameter.replace('[Sm3/d]', '[MSm3/d]') if parameter == 'Gas [Sm3/d]' else parameter
    out = []
    for r in rows:
        v = r.get(parameter)
        if v is None: continue
        out.append({'Name': r.get('Name'), 'Kind': r.get('Kind'), label: float(v) / (1e6 if parameter == 'Gas [Sm3/d]' else 1.0)})
    return pd.DataFrame(out)


# ------------------------------------------------------------------ line thickness and labels from stored rows
def edge_widths(flows, qmax=None, wmin=1.5, wmax=11.0):
    """{edge_id: stroke width in px} growing with the square root of |flow| (so a line with a quarter of the maximum flow is half as thick as the thickest).
    ``qmax`` fixes the scale (use the maximum over all report dates so thickness is comparable while moving the date slider). Lines without flow get 1 px."""
    vals = {k: abs(float(v)) for k, v in (flows or {}).items() if v is not None}
    top = float(qmax) if qmax else (max(vals.values()) if vals else 0.0)
    out = {}
    for k, v in vals.items():
        out[k] = 1.0 if v < 1e-6 or top <= 0 else wmin + (wmax - wmin) * (min(v / top, 1.0) ** 0.5)
    return out


def max_flow(edge_rows):
    return max([abs(r.get('Flow [m3/d]') or 0.0) for r in edge_rows] or [0.0])


def labels_from_rows(node_rows, edge_rows, mode=AUTO, phase=None):
    """Same texts as :func:`label_maps` but from stored element rows (forecast report dates)."""
    m = resolve_mode(mode, node_rows, phase)
    if m.startswith('Nothing'): return {}, {r['Edge ID']: (r.get('Kind') or 'pipeline') for r in edge_rows}
    nl = {}
    for r in node_rows:
        if r.get('Kind') in _SKIP_NODE: continue
        status = r.get('Status')
        if status in ('shut_in', 'dead', 'shut_in_below_min_rate'): nl[r['Node ID']] = f"{status.replace('_', ' ')} · {r.get('Pressure [bar]') or 0:.1f} bar"; continue
        txt = _node_text(m, r) or _node_text('Pressure', r)
        if txt is not None: nl[r['Node ID']] = txt
    return nl, {r['Edge ID']: _edge_text(m, r) for r in edge_rows}
