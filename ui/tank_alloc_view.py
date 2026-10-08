"""Which tank(s) a well drains. One tank = the usual case; several = commingled production (one wellbore, several reservoirs)."""
from __future__ import annotations
from network.reservoir_mb import tank_alloc
from ui.widgets import synced_multiselect, synced_number


def tank_alloc_editor(st, p, sid, tanks, label='Drains reservoir tank(s)', multi=True):
    """Edit ``p['reservoir_id']`` (primary tank) and ``p['reservoir_alloc']`` ([{'tank_id','share'}], only when 2+ tanks). ``tanks`` = {id: name}.
    Returns the list of selected tank ids."""
    cur = [t for t, _ in tank_alloc(p) if t in tanks]
    if not multi:
        return cur
    sel = synced_multiselect(st, label, list(tanks), cur, 'rids' + sid, format_func=lambda k: tanks.get(k, k))
    if not sel:
        p.pop('reservoir_id', None); p.pop('reservoir_alloc', None); return []
    if len(sel) == 1:
        p['reservoir_id'] = sel[0]; p.pop('reservoir_alloc', None); return sel
    old = {a.get('tank_id'): float(a.get('share') or 0) for a in p.get('reservoir_alloc') or [] if isinstance(a, dict)}
    st.caption('Commingled production: the well has one flowing bottom-hole pressure; each tank contributes in proportion to its productivity share and its own drawdown. '
               'The well sees the productivity-weighted tank pressure.')
    shares = {}
    for t in sel:
        shares[t] = synced_number(st, f'Productivity share · {tanks.get(t, t)} [%]', (old.get(t) or 100.0 / len(sel)), f'ras{sid}_{t}', 1.0, 100.0, 5.0, '%.0f')
    tot = sum(shares.values()) or 1.0
    p['reservoir_alloc'] = [{'tank_id': t, 'share': shares[t]} for t in sel]      # raw values; tank_alloc() normalises
    p['reservoir_id'] = max(sel, key=lambda t: shares[t])
    st.caption('Normalised: ' + ' · '.join(f"{tanks.get(t, t)} {100 * shares[t] / tot:.0f} %" for t in sel))
    return sel
