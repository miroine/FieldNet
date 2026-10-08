"""Mask elements (GAP style): a masked element is kept on the layout but ignored by every calculation.

Masking a node removes it and its connections; masking a connection removes just that link. Anything that is left with no way to
deliver (a producer or injector whose only connections are gone, or one that drains a masked tank) is masked with it, so the
solver never sees a dangling element. ``strip_masked`` returns copies and leaves the model untouched.
"""
from __future__ import annotations
import copy

FLAG = 'masked'
SOURCES = ('well', 'water_injector', 'gas_injector', 'injector', 'water_source', 'gas_source')


def is_masked(el):
    v = (el.get('params') or {}).get(FLAG)
    return v is True or str(v).lower() == 'true'


def set_masked(el, on):
    p = el.setdefault('params', {})
    if on: p[FLAG] = True
    else: p.pop(FLAG, None)


def masked_ids(nodes, edges):
    """(node ids, edge ids) that are out of the calculation, including elements isolated by the mask."""
    mn = {n['id'] for n in nodes if is_masked(n)}; me = {e['id'] for e in edges if is_masked(e)}
    if not mn and not me: return set(), set()
    changed = True
    while changed:
        changed = False
        for e in edges:
            if e['id'] not in me and (e['source'] in mn or e['target'] in mn): me.add(e['id']); changed = True
        out = {}
        for e in edges: out.setdefault(e['source'], []).append(e['id'])
        for n in nodes:
            if n['id'] in mn or n.get('kind') not in SOURCES: continue
            from network.reservoir_mb import linked_tank_ids
            rids = linked_tank_ids(n.get('params'))
            all_out = out.get(n['id'], [])
            cut = bool(all_out) and all(i in me for i in all_out)
            if (rids and all(r in mn for r in rids)) or cut: mn.add(n['id']); changed = True
    return mn, me


def has_masked(nodes, edges=()):
    return any(is_masked(n) for n in nodes) or any(is_masked(e) for e in edges)


def strip_masked(nodes, edges):
    """Copies of nodes/edges without the masked elements (the inputs are returned unchanged when nothing is masked)."""
    if not has_masked(nodes, edges): return nodes, edges
    mn, me = masked_ids(nodes, edges)
    return ([copy.deepcopy(n) for n in nodes if n['id'] not in mn], [copy.deepcopy(e) for e in edges if e['id'] not in me])


def pad_results(nodes, edges, p, q):
    """Give masked elements placeholder results (zero flow, their own nominal pressure) so every page that walks the full model finds an entry."""
    mn, me = masked_ids(nodes, edges)
    if not mn and not me: return p, q
    p = dict(p or {}); q = dict(q or {})
    for e in edges:
        if e['id'] in me: q.setdefault(e['id'], 0.0)
    for n in nodes:
        if n['id'] in mn and n['id'] not in p:
            pr = n.get('pressure_bar'); pp = n.get('params') or {}
            p[n['id']] = float(pr if pr is not None else pp.get('reservoir_pressure_bar', 0.0) or 0.0)
    return p, q
