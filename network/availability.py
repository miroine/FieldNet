"""Uptime / downtime of every element (wells, compressors, pumps, separators / hosts, pipelines, manifolds ...) in the production prognosis.

Each node or flowline can carry (all optional, in ``params``):
  uptime                           fraction of time available (0-1), e.g. 0.95 for 5 % unplanned downtime
  mtbf_days, mttr_days             failure / repair times -> availability MTBF / (MTBF + MTTR)
  planned_downtime_days_per_year   turnaround / maintenance, factor (1 - days / 365.25)
The element uptime is the product of the three.

Expected-value (screening) treatment, applied every forecast step to the solved flows:
  production of a well reaches the sink only when the well AND everything downstream is up, so
      F(node) = u(node) x sum over outgoing flow paths [ share of flow x u(line) x F(next node) ]
  (series elements multiply, parallel elements share the flow in proportion to their solved flow).
  Injection is the mirror image: an injector receives its fluid only when the source and everything upstream is up.
Downtime is deferral, not loss of reserves: the tank is depleted only by what is actually produced, so the plateau is longer and the
deferred volume is reported (it is not recovered later as catch-up). Not modelled: random outage clustering (use the Monte-Carlo in
Tools -> Reliability), capacity of the surviving parallel train to take over load, spare units, rate rescheduling after repair.
"""
from __future__ import annotations

KEYS = ('uptime', 'mtbf_days', 'mttr_days', 'planned_downtime_days_per_year')
TYPICAL = {'well': 0.95, 'compressor': 0.94, 'pump': 0.95, 'separator': 0.97, 'pipeline': 0.995, 'manifold': 0.995, 'water_injector': 0.95, 'gas_injector': 0.95,
           'choke': 0.995, 'control_valve': 0.995, 'water_source': 0.98, 'gas_source': 0.98}


def _num(v):
    try: x = float(v); return x if x == x else None
    except (TypeError, ValueError): return None


def uptime(obj):
    """Availability (0-1) of one node or edge from its params; 1.0 when nothing is set."""
    p = (obj or {}).get('params') or {}; u = 1.0
    a = _num(p.get('uptime'))
    if a is not None: u *= min(max(a / 100.0 if a > 1.0 else a, 0.0), 1.0)
    mt, mr = _num(p.get('mtbf_days')), _num(p.get('mttr_days'))
    if mt and mr is not None and mt > 0 and mr >= 0: u *= mt / (mt + mr)
    pl = _num(p.get('planned_downtime_days_per_year'))
    if pl: u *= min(max(1.0 - pl / 365.25, 0.0), 1.0)
    return u


def has_any(nodes, edges):
    return any(uptime(x) < 1.0 - 1e-12 for x in list(nodes) + list(edges))


def _graph(nodes, edges, flows, eps=1e-9):
    out, inc = {}, {}
    for e in edges:
        q = _num((flows or {}).get(e['id']))
        if q is None or abs(q) <= eps: continue
        a, b = (e['source'], e['target']) if q > 0 else (e['target'], e['source'])
        out.setdefault(a, []).append((e, b, abs(q))); inc.setdefault(b, []).append((e, a, abs(q)))
    return out, inc


def _factors(nodes, edges, flows, forward):
    u_n = {n['id']: (1.0 if n.get('kind') == 'reservoir' else uptime(n)) for n in nodes}
    out, inc = _graph(nodes, edges, flows); nxt = out if forward else inc; memo = {}

    def f(v, stack=()):
        if v in memo: return memo[v]
        if v in stack: return 1.0
        links = nxt.get(v) or []
        if not links: val = u_n.get(v, 1.0)
        else:
            tot = sum(q for _, _, q in links); s = 0.0
            for e, w, q in links: s += (q / tot) * uptime(e) * f(w, stack + (v,))
            val = u_n.get(v, 1.0) * s
        memo[v] = val; return val
    return {n['id']: f(n['id']) for n in nodes}


def delivery_factors(nodes, edges, flows):
    """{node_id: fraction of its production that is delivered}, looking downstream (use for wells)."""
    return _factors(nodes, edges, flows, True)


def supply_factors(nodes, edges, flows):
    """{node_id: fraction of time the node is supplied}, looking upstream (use for injectors)."""
    return _factors(nodes, edges, flows, False)


def register(nodes, edges):
    """Rows for the availability register (one per node except tanks, and per flowline)."""
    names = {n['id']: n.get('name', n['id']) for n in nodes}; rows = []
    for n in nodes:
        if n.get('kind') == 'reservoir': continue
        p = n.get('params') or {}
        rows.append({'Type': 'node', 'ID': n['id'], 'Name': n.get('name', n['id']), 'Kind': n.get('kind'), 'Uptime [%]': _pct(p.get('uptime')), 'MTBF [d]': _num(p.get('mtbf_days')),
                     'MTTR [d]': _num(p.get('mttr_days')), 'Planned downtime [d/yr]': _num(p.get('planned_downtime_days_per_year')), 'Effective uptime [%]': round(100 * uptime(n), 2)})
    for e in edges:
        p = e.get('params') or {}
        rows.append({'Type': 'line', 'ID': e['id'], 'Name': e.get('name') or f"{names.get(e['source'], e['source'])} → {names.get(e['target'], e['target'])}", 'Kind': e.get('kind', 'pipeline'),
                     'Uptime [%]': _pct(p.get('uptime')), 'MTBF [d]': _num(p.get('mtbf_days')), 'MTTR [d]': _num(p.get('mttr_days')),
                     'Planned downtime [d/yr]': _num(p.get('planned_downtime_days_per_year')), 'Effective uptime [%]': round(100 * uptime(e), 2)})
    return rows


def _pct(v):
    a = _num(v)
    if a is None: return None
    return a if a > 1.0 else a * 100.0


def apply_register(nodes, edges, rows):
    """Write edited register rows back into params (None / blank removes the entry). Returns the number of elements changed."""
    byid = {('node', n['id']): n for n in nodes}; byid.update({('line', e['id']): e for e in edges}); changed = 0
    for r in rows:
        obj = byid.get((r.get('Type'), r.get('ID')))
        if obj is None: continue
        p = obj.setdefault('params', {}); before = {k: p.get(k) for k in KEYS}
        up = _num(r.get('Uptime [%]'))
        for k, v in (('uptime', None if up is None else min(max(up, 0.0), 100.0) / 100.0), ('mtbf_days', _num(r.get('MTBF [d]'))), ('mttr_days', _num(r.get('MTTR [d]'))),
                     ('planned_downtime_days_per_year', _num(r.get('Planned downtime [d/yr]')))):
            if v is None: p.pop(k, None)
            else: p[k] = float(v)
        if {k: p.get(k) for k in KEYS} != before: changed += 1
    return changed


def apply_typical(nodes, edges, overwrite=False):
    """Fill typical uptimes (wells 95 %, compressors 94 %, pumps 95 %, separators 97 %, lines 99.5 % ...). Returns the number of elements set."""
    n_set = 0
    for obj in list(nodes) + list(edges):
        k = obj.get('kind', 'pipeline')
        if obj.get('kind') == 'reservoir': continue
        t = TYPICAL.get(k) if k in TYPICAL else (TYPICAL['separator'] if k in ('separator_stage', 'oil_export', 'gas_export', 'water_disposal', 'sink') else None)
        if t is None: continue
        p = obj.setdefault('params', {})
        if p.get('uptime') is not None and not overwrite: continue
        p['uptime'] = t; n_set += 1
    return n_set


def clear(nodes, edges):
    for obj in list(nodes) + list(edges):
        for k in KEYS: (obj.get('params') or {}).pop(k, None)
