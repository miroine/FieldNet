"""Tank coupling summaries derived from the canvas model (no separate tables to keep in sync)."""
from __future__ import annotations
import pandas as pd

WELLS = ('well',); INJ = ('water_injector', 'gas_injector', 'injector')


from network.reservoir_mb import linked_tank_ids


def tank_coupling_table(nodes, edges=None):
    rows = []
    byid = {n['id']: n for n in nodes}
    for t in nodes:
        if t.get('kind') != 'reservoir': continue
        p = t.get('params') or {}; ph = p.get('fluid_phase', 'oil')
        drains = [w.get('name', w['id']) for w in nodes if w.get('kind') in WELLS and t['id'] in linked_tank_ids(w.get('params'))]
        supports = [w.get('name', w['id']) for w in nodes if w.get('kind') in INJ and (w.get('params') or {}).get('reservoir_id') == t['id']]
        links = [byid[c['to']].get('name', c['to']) for c in p.get('communication') or [] if c.get('to') in byid]
        links += [n.get('name', n['id']) for n in nodes if n.get('kind') == 'reservoir' for c in (n.get('params') or {}).get('communication') or [] if c.get('to') == t['id']]
        inplace = (float(p.get('stoiip_sm3') or 0) / 1e6, 'MSm³ oil') if ph == 'oil' else (float(p.get('giip_sm3') or 0) / 1e9, 'GSm³ gas')
        rows.append({'Tank': t.get('name', t['id']), 'Phase': ph, 'In place': f'{inplace[0]:,.2f} {inplace[1]}', 'Initial pressure [bar]': p.get('reservoir_pressure_bar'),
                     'Producers': ', '.join(drains) or '—', 'Injectors': ', '.join(supports) or '—', 'Aquifer PI [m3/d/bar]': p.get('aquifer_pi_m3d_bar', 0) or 0,
                     'Communicates with': ', '.join(links) or '—', 'Relperm': 'yes' if p.get('relperm') else 'screening S-curve'})
    return pd.DataFrame(rows)


def communication_table(nodes):
    byid = {n['id']: n for n in nodes}; rows = []
    for t in nodes:
        for c in (t.get('params') or {}).get('communication') or []:
            if c.get('to') in byid:
                rows.append({'From': t.get('name', t['id']), 'To': byid[c['to']].get('name', c['to']), 'Transmissibility [m3/d/bar]': c.get('transmissibility_m3d_bar', 100.0),
                             'Max transfer [m3/d] (blank = none)': c.get('max_transfer_m3d'), '_from_id': t['id'], '_to_id': c['to']})
    return pd.DataFrame(rows)


def apply_communication_table(nodes, df):
    """Write edited values back; returns True when anything changed."""
    byid = {n['id']: n for n in nodes}; changed = False
    for r in df.to_dict('records'):
        t = byid.get(r.get('_from_id'))
        if not t: continue
        for c in (t.get('params') or {}).get('communication') or []:
            if c.get('to') != r.get('_to_id'): continue
            T = r.get('Transmissibility [m3/d/bar]'); M = r.get('Max transfer [m3/d] (blank = none)')
            try: T = float(T)
            except (TypeError, ValueError): T = c.get('transmissibility_m3d_bar', 100.0)
            try: M = float(M) if M == M and M not in (None, '') and float(M) > 0 else None
            except (TypeError, ValueError): M = None
            if abs(float(c.get('transmissibility_m3d_bar') or 0) - T) > 1e-12 or c.get('max_transfer_m3d') != M:
                c['transmissibility_m3d_bar'] = max(T, 0.0); c['max_transfer_m3d'] = M; changed = True
    return changed


# ----------------------------------------------------------------------------- create / delete links, equalisation time
def link_exists(nodes, a, b):
    byid = {n['id']: n for n in nodes}
    for x, y in ((a, b), (b, a)):
        if any(c.get('to') == y for c in ((byid.get(x) or {}).get('params') or {}).get('communication') or []): return True
    return False


def add_link(nodes, a, b, transmissibility_m3d_bar=100.0, max_transfer_m3d=None):
    """Tank a <-> tank b communication (stored once, on tank a; flow goes from the higher to the lower pressure). Raises ValueError on invalid input."""
    byid = {n['id']: n for n in nodes}
    if a == b: raise ValueError('a tank cannot communicate with itself')
    if a not in byid or b not in byid or byid[a].get('kind') != 'reservoir' or byid[b].get('kind') != 'reservoir': raise ValueError('both ends must be reservoir tanks')
    if link_exists(nodes, a, b): raise ValueError('these tanks are already linked - edit the existing link')
    T = float(transmissibility_m3d_bar)
    if not T >= 0: raise ValueError('transmissibility must be >= 0')
    byid[a].setdefault('params', {}).setdefault('communication', []).append({'to': b, 'transmissibility_m3d_bar': T, 'max_transfer_m3d': (float(max_transfer_m3d) if max_transfer_m3d else None)}); return True


def remove_link(nodes, a, b):
    """Remove the link between a and b (either direction). Returns True when something was removed."""
    byid = {n['id']: n for n in nodes}; removed = False
    for x, y in ((a, b), (b, a)):
        p = (byid.get(x) or {}).get('params') or {}; com = p.get('communication') or []; keep = [c for c in com if c.get('to') != y]
        if len(keep) != len(com): removed = True; p['communication'] = keep
    return removed


def link_dynamics(nodes):
    """Per link: pressure difference, current transfer rate and the time constant of pressure equalisation tau = (C1 C2 / (C1 + C2)) / T [days]
    (C = compliance [m3/bar] from the tank's pore volume and compressibility), from the tanks' *initial* state."""
    from network.reservoir_mb import tanks_from_nodes
    tk = tanks_from_nodes(nodes); byid = {n['id']: n for n in nodes}; rows = []
    for t in nodes:
        for c in (t.get('params') or {}).get('communication') or []:
            o = tk.get(c.get('to')); s = tk.get(t['id'])
            if not (o and s): continue
            T = float(c.get('transmissibility_m3d_bar') or 0.0); c1, c2 = s.compliance(), o.compliance(); dp = s.p - o.p
            tau = (c1 * c2 / (c1 + c2)) / T if T > 0 else float('inf')
            rows.append({'From': s.name, 'To': o.name, 'Initial dP [bar]': dp, 'Transfer now [m3/d]': T * dp, 'Equalisation time constant [days]': tau,
                         'Reading': 'effectively one tank (tau < 30 d)' if tau < 30 else ('slow coupling' if tau < 3650 else 'practically isolated')})
    return pd.DataFrame(rows)
