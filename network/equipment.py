"""Inline equipment nodes (choke, control valve, pump, compressor) and joints.

In the editor an equipment item is a *node* that sits on a flowline (flowline -> equipment -> flowline) like a
joint in GAP/Petex. The solver kernel only knows two-ended links, so before solving every inline node ``E`` is
expanded into

    E::in  (junction)  --[E::link, kind=E.kind, params=E.params]-->  E::out (junction)

with the upstream connections re-targeted to ``E::in`` and the downstream ones started from ``E::out``.
After the solve the results are folded back onto ``E``: pressure of ``E`` = inlet pressure, flow of ``E`` = flow
through the internal link, outlet pressure/dP/power are in ``info['inline_equipment']``.

``expand_inline_equipment`` is idempotent (an expanded graph has no inline nodes left) and never mutates its inputs.
"""
from __future__ import annotations
import copy

INLINE_KINDS = ('choke', 'control_valve', 'pump', 'compressor')
JUNCTION_KINDS = ('manifold', 'joint')          # pure connection points
SEP = '::'
_LINK_PARAM_KEYS = ('cv', 'opening', 'shutoff_head_bar', 'rated_rate_m3d', 'min_head_bar', 'efficiency', 'pressure_ratio', 'max_discharge_bar',
                    'map_enabled', 'rated_gas_rate_sm3d', 'speed_fraction', 'gor_sm3sm3', 'rho_kgm3', 'max_rate_m3d', 'max_power_kw', 'fluid')


def has_inline(nodes):
    return any(n.get('kind') in INLINE_KINDS for n in nodes or [])


def is_inline(node):
    return node.get('kind') in INLINE_KINDS


def expand_inline_equipment(nodes, edges):
    """Return (nodes2, edges2, mapping). ``mapping`` is ``{equipment_id: {...}}`` for :func:`collapse_results`."""
    if not has_inline(nodes): return nodes, edges, {}
    out_nodes, mapping = [], {}
    for n in nodes:
        if not is_inline(n): out_nodes.append(n); continue
        nid = n['id']; name = n.get('name', nid); p = n.get('params') or {}
        i, o, l = nid + SEP + 'in', nid + SEP + 'out', nid + SEP + 'link'
        out_nodes.append({'id': i, 'kind': 'manifold', 'name': f'{name} (inlet)', 'pressure_bar': None, 'params': {}, 'x': n.get('x', 0), 'y': n.get('y', 0), '_inline_of': nid})
        out_nodes.append({'id': o, 'kind': 'manifold', 'name': f'{name} (outlet)', 'pressure_bar': None, 'params': {}, 'x': n.get('x', 0), 'y': n.get('y', 0), '_inline_of': nid})
        link_params = {k: copy.deepcopy(p[k]) for k in _LINK_PARAM_KEYS if k in p and p[k] is not None}
        link = {'id': l, 'source': i, 'target': o, 'kind': n['kind'], 'name': name, 'length_m': 0.0, 'diameter_m': float(p.get('diameter_m', 0.154)),
                'roughness_m': 4.5e-5, 'elevation_change_m': 0.0, 'params': link_params, '_inline_of': nid}
        mapping[nid] = {'in': i, 'out': o, 'link': l, 'kind': n['kind'], 'name': name}
    out_edges = []
    for e in edges:
        e2 = e
        s, t = e.get('source'), e.get('target')
        if s in mapping or t in mapping:
            e2 = dict(e)
            if s in mapping: e2['source'] = mapping[s]['out']
            if t in mapping: e2['target'] = mapping[t]['in']
        out_edges.append(e2)
    for nid, m in mapping.items():
        n = next(x for x in nodes if x['id'] == nid); p = n.get('params') or {}
        out_edges.append({'id': m['link'], 'source': m['in'], 'target': m['out'], 'kind': n['kind'], 'name': m['name'], 'length_m': 0.0,
                          'diameter_m': float(p.get('diameter_m', 0.154)), 'roughness_m': 4.5e-5, 'elevation_change_m': 0.0,
                          'params': {k: copy.deepcopy(p[k]) for k in _LINK_PARAM_KEYS if k in p and p[k] is not None}, '_inline_of': nid})
    return out_nodes, out_edges, mapping


def expand_guess(guess, mapping):
    """Translate a warm start keyed by original ids to the expanded graph."""
    if not guess or not mapping: return guess
    g = dict(guess); pr = dict(g.get('pressures') or {}); fl = dict(g.get('flows') or {})
    for nid, m in mapping.items():
        if nid in pr: pr[m['in']] = pr[m['out']] = pr.pop(nid)
        if nid in fl: fl[m['link']] = fl.pop(nid)
    g['pressures'], g['flows'] = pr, fl
    return g


def _remap_rows(rows, rev_name):
    out = []
    for r in rows or []:
        if not isinstance(r, dict): out.append(r); continue
        r = dict(r)
        for k in ('ComponentId', 'component', 'well_id'):
            v = r.get(k)
            if isinstance(v, str) and SEP in v: r[k] = v.split(SEP)[0]
        c = r.get('Component')
        if isinstance(c, str) and c in rev_name: r['Component'] = rev_name[c]
        out.append(r)
    return out


def collapse_results(result, mapping):
    """Fold the solution of the expanded graph back onto the equipment nodes (see module docstring)."""
    if not mapping: return result
    p, q, info, d = result
    p = dict(p); q = dict(q); info = dict(info) if info else {}
    inline = {}
    for nid, m in mapping.items():
        pin, pout = p.pop(m['in'], None), p.pop(m['out'], None); flow = q.pop(m['link'], None)
        if pin is not None: p[nid] = pin
        if flow is not None: q[nid] = flow
        row = {'id': nid, 'name': m['name'], 'kind': m['kind'], 'p_in_bar': pin, 'p_out_bar': pout, 'rate_m3d': flow,
               'dp_bar': (pin - pout) if (pin is not None and pout is not None) else None}
        inline[nid] = row
    info['inline_equipment'] = inline
    for k in ('constraints', 'active_constraints', 'debug', 'well_warnings'):
        if k in info: info[k] = _remap_rows(info[k], {})
    # Equipment table rows were built on the internal link: keep them, labelled with the node name.
    info['n_inline_equipment'] = len(mapping)
    return p, q, info, d


def convert_edge_equipment_to_nodes(nodes, edges, edge_ids=None):
    """Legacy helper: replace link-type equipment *edges* (choke/valve/pump/compressor) by inline equipment
    nodes joined with 1 m connector flowlines (zero length is rejected by validation), so they can be selected/placed like a joint. Returns new lists."""
    import uuid
    nodes = copy.deepcopy(nodes); keep = []
    byid = {n['id']: n for n in nodes}
    for e in copy.deepcopy(edges):
        if e.get('kind') in INLINE_KINDS and (edge_ids is None or e['id'] in edge_ids) and e['source'] in byid and e['target'] in byid:
            a, b = byid[e['source']], byid[e['target']]
            nid = e['id'] if e['id'] not in byid else str(uuid.uuid4())[:8]
            params = dict(e.get('params') or {})
            nodes.append({'id': nid, 'kind': e['kind'], 'name': e.get('name') or f"{e['kind'].upper()}-{nid}", 'pressure_bar': None, 'params': params,
                          'x': (float(a.get('x') or 0) + float(b.get('x') or 0)) / 2, 'y': (float(a.get('y') or 0) + float(b.get('y') or 0)) / 2 + 40})
            byid[nid] = nodes[-1]
            for s, t, suf in ((e['source'], nid, 'a'), (nid, e['target'], 'b')):
                keep.append({'id': f"{e['id']}-{suf}", 'source': s, 'target': t, 'kind': 'pipeline', 'length_m': 1.0, 'diameter_m': float(e.get('diameter_m') or 0.154),
                             'roughness_m': 4.5e-5, 'elevation_change_m': 0.0, 'params': {k: v for k, v in params.items() if k in ('water_cut', 'gor_sm3sm3', 'temperature_c', 'api', 'gas_sg')}})
        else: keep.append(e)
    return nodes, keep
