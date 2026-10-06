"""Assign prediction sources (decline curve / external simulator / tank balance) to several wells or tanks at once (GAP style).

Pure functions on node lists (return new lists); the Streamlit page is ``ui/prediction_view.py``.
"""
from __future__ import annotations
import copy

SOURCE_LABELS = {'none': 'Tank balance / IPR', 'decline': 'Decline curve', 'external_table': 'External table', 'external_rates': 'Simulator rates'}


def _wells(nodes, ids=None):
    return [n for n in nodes if n.get('kind') == 'well' and (ids is None or n['id'] in set(ids))]


def source_overview(nodes):
    """One row per producer: source type, tank, key parameters."""
    tanks = {n['id']: n.get('name', n['id']) for n in nodes if n.get('kind') == 'reservoir'}
    rows = []
    for w in _wells(nodes):
        p = w.get('params') or {}; src = p.get('prediction_source') or {'type': 'none'}; t = str(src.get('type', 'none'))
        if t == 'decline': detail = f"{src.get('basis', 'oil')} qi={float(src.get('qi', 0)):,.0f}, Di={float(src.get('di_per_year', 0)):.3f}/yr, b={float(src.get('b', 0)):.2f}"
        elif t == 'external_table': detail = f"{len(src.get('rows') or [])} rows, axis {src.get('x_axis', 'date')}"
        else: detail = 'follows IPR / VLP and the tank pressure'
        rows.append({'Well': w.get('name', w['id']), 'ID': w['id'], 'Tank': tanks.get(p.get('reservoir_id'), '—'), 'Source': SOURCE_LABELS.get(t, t), 'Detail': detail})
    return rows


def clear_source(nodes, well_ids):
    ns = copy.deepcopy(nodes)
    for w in _wells(ns, well_ids): (w.setdefault('params', {})).pop('prediction_source', None)
    return ns


def assign_decline(nodes, well_ids, qi_total, di_per_year, b=0.5, basis='oil', split='equal', q_abandon=None, terminal_di_per_year=None, t0=None):
    """Give each selected producer an Arps decline potential. ``qi_total`` is the group's initial rate, shared ``split='equal'`` or ``'pi'``
    (by productivity index, falling back to equal when no PI). Returns the new node list (decline = potential cap; wells still follow IPR/VLP)."""
    ns = copy.deepcopy(nodes); ws = _wells(ns, well_ids)
    if not ws: raise ValueError('Select at least one producer')
    if split == 'pi':
        pis = [float((w.get('params') or {}).get('pi_m3d_bar') or 0) for w in ws]
        tot = sum(pis); shares = [x / tot for x in pis] if tot > 0 else [1.0 / len(ws)] * len(ws)
    else: shares = [1.0 / len(ws)] * len(ws)
    for w, sh in zip(ws, shares):
        src = {'type': 'decline', 'basis': basis, 'qi': float(qi_total) * sh, 'di_per_year': float(di_per_year), 'b': float(b), 'apply_as': 'rate_cap'}
        if q_abandon: src['q_abandon'] = float(q_abandon)
        if terminal_di_per_year: src['terminal_di_per_year'] = float(terminal_di_per_year)
        if t0: src['t0'] = str(t0)
        w.setdefault('params', {})['prediction_source'] = src
    return ns


def set_tank_mode(nodes, tank_id, mode, table_rows=None):
    """'material_balance' or 'external' (pressure / water cut / GOR from a simulator table)."""
    if mode not in ('material_balance', 'external'): raise ValueError('mode must be material_balance or external')
    ns = copy.deepcopy(nodes)
    for n in ns:
        if n['id'] == tank_id and n.get('kind') == 'reservoir':
            prm = n.setdefault('params', {}); prm['prediction_mode'] = mode
            if mode == 'external':
                rows = [r for r in (table_rows if table_rows is not None else prm.get('external_table') or []) if r]
                if not rows: raise ValueError('External mode needs a table with date/time_days and reservoir_pressure_bar')
                prm['external_table'] = rows
            return ns
    raise ValueError(f'No reservoir tank {tank_id!r}')
