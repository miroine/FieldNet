"""Fluid library: several named fluids in one model.

A fluid is a dict ``{'name', 'api', 'gas_sg', 'gor_sm3sm3', 'pvt'}`` where ``pvt`` is either None (legacy screening PVT) or the correlation
PVT configuration used by ``params['pvt']`` (``model='correlation'`` + FluidSpec fields + calibration). Elements reference a fluid by name
(``params['fluid_name']``) and carry a *copy* of its numbers, so the solver never needs the library and a case file is self-contained.

* ``assign`` stamps a fluid on wells / flowlines / tanks (tanks also get Boi, Rsi and Pb from the fluid's PVT so the material balance and the wells agree).
* ``propagate`` re-stamps every element that uses a fluid after the fluid was edited. Water cut is not a fluid property and is never touched.
* ``library_from_elements`` rebuilds the library from the elements (loading an old case or a case from a colleague).
* ``commingled`` reports the blended fluid at nodes / flowlines (``network.fluid_blend``) so mixing of different fluids is visible.
* ``check`` flags inconsistencies (a well on a different fluid than its tank, an unknown fluid name, a gas tank with an oil fluid, mixed fluids at a node)."""
from __future__ import annotations
import copy
import pandas as pd

OWNED_KEYS = ('api', 'gas_sg', 'gor_sm3sm3')


def new_fluid(name, api=35.0, gas_sg=0.75, gor_sm3sm3=100.0, pvt=None):
    name = str(name).strip()
    if not name: raise ValueError('a fluid needs a name')
    return {'name': name, 'api': float(api), 'gas_sg': float(gas_sg), 'gor_sm3sm3': float(gor_sm3sm3), 'pvt': copy.deepcopy(pvt) if pvt else None}


def library_add(lib, fluid, overwrite=False):
    if fluid['name'] in lib and not overwrite: raise ValueError(f"fluid '{fluid['name']}' already exists")
    lib[fluid['name']] = copy.deepcopy(fluid); return lib


def library_rename(lib, nodes, edges, old, new):
    if old not in lib: raise KeyError(old)
    if new in lib: raise ValueError(f"fluid '{new}' already exists")
    f = lib.pop(old); f['name'] = new; lib[new] = f
    for o in list(nodes) + list(edges):
        p = o.get('params') or {}
        if p.get('fluid_name') == old: p['fluid_name'] = new
    return lib


def library_delete(lib, nodes, edges, name, force=False):
    used = usage(nodes, edges).get(name)
    if used and sum(used.values()) and not force: raise ValueError(f"fluid '{name}' is used by {sum(used.values())} elements")
    lib.pop(name, None)
    for o in list(nodes) + list(edges):
        p = o.get('params') or {}
        if p.get('fluid_name') == name: p.pop('fluid_name', None)
    return lib


def _stamp(params, fluid):
    params['fluid_name'] = fluid['name']
    for k in OWNED_KEYS: params[k] = fluid[k]
    if fluid.get('pvt'): params['pvt'] = copy.deepcopy(fluid['pvt'])
    else: params.pop('pvt', None)


def tank_properties(fluid, temperature_c, pi_bar):
    """Boi, Rsi, Pb of the tank from the fluid (correlation PVT when defined, otherwise the legacy screening model)."""
    if fluid.get('pvt') and str(fluid['pvt'].get('model', '')).lower() == 'correlation':
        from physics.pvt_model import fluid_from_params
        fm = fluid_from_params({'pvt': fluid['pvt'], 'gor_sm3sm3': fluid['gor_sm3sm3'], 'api': fluid['api'], 'gas_sg': fluid['gas_sg']})
        if fm is not None:
            st = fm.state(pi_bar, temperature_c); return {'boi_rm3_sm3': float(st.oil_fvf), 'rsi_sm3_sm3': float(min(st.solution_gor_sm3sm3, fluid['gor_sm3sm3'])), 'bubble_point_bar': float(min(fm.bubble_point_bar(temperature_c), pi_bar))}
    from physics.pvt import simple_black_oil
    st = simple_black_oil(pi_bar, temperature_c, fluid['api'], fluid['gas_sg'])
    return {'boi_rm3_sm3': float(st.oil_fvf), 'rsi_sm3_sm3': float(fluid['gor_sm3sm3']), 'bubble_point_bar': None}


def assign(nodes, edges, fluid, node_ids=(), edge_ids=(), sync_tanks=True):
    """Stamp ``fluid`` on the listed nodes (wells, tanks, ...) and edges. Returns the number of elements changed."""
    ns, es = set(node_ids), set(edge_ids); n = 0
    for nd in nodes:
        if nd['id'] not in ns: continue
        p = nd.setdefault('params', {}); _stamp(p, fluid); n += 1
        if sync_tanks and nd.get('kind') == 'reservoir' and str(p.get('fluid_phase', 'oil')) == 'oil':
            tp = tank_properties(fluid, float(p.get('temperature_c') or 80.0), float(p.get('reservoir_pressure_bar') or 250.0))
            for k, v in tp.items():
                if v is not None: p[k] = v
    for e in edges:
        if e['id'] in es: _stamp(e.setdefault('params', {}), fluid); n += 1
    return n


def assign_tank_system(nodes, edges, fluid, tank_id, include_flowlines=False):
    """Fluid for a tank and every well / injector-free producer linked to it (``reservoir_id``)."""
    ids = [tank_id] + [w['id'] for w in nodes if w.get('kind') == 'well' and (w.get('params') or {}).get('reservoir_id') == tank_id]
    return assign(nodes, edges, fluid, ids, [])


def propagate(lib, nodes, edges, name):
    """Re-apply the library definition of ``name`` to every element that uses it."""
    f = lib[name]; ids = [n['id'] for n in nodes if (n.get('params') or {}).get('fluid_name') == name]; eds = [e['id'] for e in edges if (e.get('params') or {}).get('fluid_name') == name]
    return assign(nodes, edges, f, ids, eds)


def usage(nodes, edges):
    out = {}
    for kind, items in (('wells', [n for n in nodes if n.get('kind') == 'well']), ('tanks', [n for n in nodes if n.get('kind') == 'reservoir']), ('flowlines', [e for e in edges if e.get('kind', 'pipeline') == 'pipeline'])):
        for o in items:
            nm = (o.get('params') or {}).get('fluid_name')
            if nm: out.setdefault(nm, {'wells': 0, 'tanks': 0, 'flowlines': 0})[kind] += 1
    return out


def library_from_elements(nodes, edges, lib=None):
    """Library rebuilt from the elements (first element that carries each name defines it). Existing entries in ``lib`` win."""
    lib = dict(lib or {})
    for o in list(nodes) + list(edges):
        p = o.get('params') or {}; nm = p.get('fluid_name')
        if nm and nm not in lib:
            lib[nm] = new_fluid(nm, p.get('api', 35.0), p.get('gas_sg', 0.75), p.get('gor_sm3sm3', 100.0), p.get('pvt'))
    return lib


def table(lib, nodes=None, edges=None):
    u = usage(nodes or [], edges or [])
    return pd.DataFrame([{'Fluid': k, 'API': f['api'], 'Gas SG': f['gas_sg'], 'GOR [Sm3/Sm3]': f['gor_sm3sm3'], 'PVT': 'correlation' if f.get('pvt') else 'legacy screening',
                          'Wells': u.get(k, {}).get('wells', 0), 'Tanks': u.get(k, {}).get('tanks', 0), 'Flowlines': u.get(k, {}).get('flowlines', 0)} for k, f in lib.items()])


def check(lib, nodes, edges, solve_result=None):
    """Consistency problems as a DataFrame(Level, Element, Message)."""
    rows = []; byid = {n['id']: n for n in nodes}
    for n in nodes:
        p = n.get('params') or {}; nm = p.get('fluid_name')
        if nm and nm not in lib: rows.append({'Level': 'FAIL', 'Element': n.get('name') or n['id'], 'Message': f"uses fluid '{nm}' which is not in the library"})
        if n.get('kind') == 'well' and p.get('reservoir_id') in byid:
            t = byid[p['reservoir_id']]; tn = (t.get('params') or {}).get('fluid_name')
            if tn and nm and tn != nm: rows.append({'Level': 'WARN', 'Element': n.get('name') or n['id'], 'Message': f"fluid '{nm}' differs from its tank '{t.get('name') or t['id']}' fluid '{tn}' - the tank balance and the well use different PVT"})
            if tn and not nm: rows.append({'Level': 'WARN', 'Element': n.get('name') or n['id'], 'Message': f"has no fluid but its tank uses '{tn}'"})
        if n.get('kind') == 'reservoir' and nm in lib and str(p.get('fluid_phase', 'oil')) != 'oil' and lib[nm].get('gor_sm3sm3', 0) < 500:
            rows.append({'Level': 'WARN', 'Element': n.get('name') or n['id'], 'Message': f"gas tank uses oil-like fluid '{nm}' (GOR {lib[nm]['gor_sm3sm3']:.0f})"})
    for e in edges:
        nm = (e.get('params') or {}).get('fluid_name')
        if nm and nm not in lib: rows.append({'Level': 'FAIL', 'Element': e.get('name') or e['id'], 'Message': f"uses fluid '{nm}' which is not in the library"})
    if solve_result:
        try:
            c = commingled(nodes, edges, solve_result)
            for r in c.to_dict('records'):
                if (r.get('Sources') or 0) > 1 and (r.get('Distinct fluids') or 1) > 1: rows.append({'Level': 'INFO', 'Element': r['Element'], 'Message': f"commingles {r['Distinct fluids']} fluids: blended API {r['API']:.1f}, GOR {r['GOR [Sm3/Sm3]']:.0f}"})
        except Exception: pass
    return pd.DataFrame(rows, columns=['Level', 'Element', 'Message'])


def commingled(nodes, edges, solve_result):
    """Blended fluid at every node (and flowline) that carries production (``network.fluid_blend``; ideal volume mixing, API and gas SG only)."""
    from network.fluid_blend import blend_by_node, blend_by_edge
    fl = {n['id']: (n.get('params') or {}).get('fluid_name') for n in nodes if n.get('kind') == 'well'}; rows = []
    for b in blend_by_node(nodes, edges, solve_result):
        ids = [x.strip().rsplit(' ', 1)[0] for x in str(b.get('sources', '')).split(';') if x.strip()]
        distinct = {fl.get(i) for i in ids if fl.get(i)}
        rows.append({'Element': b['name'], 'Kind': 'node', 'Sources': b['n_sources'], 'Distinct fluids': max(len(distinct), 1), 'Fluids': ', '.join(sorted(distinct)), 'API': b['api'], 'Gas SG': b['gas_sg'],
                     'GOR [Sm3/Sm3]': b['gor_sm3sm3'], 'Water cut': b['wc'], 'Oil [Sm3/d]': b['oil_sm3d']})
    ename = {e['id']: e.get('name') or e['id'] for e in edges}
    for k, b in (blend_by_edge(nodes, edges, solve_result) or {}).items():
        rows.append({'Element': ename.get(k, k), 'Kind': 'line', 'Sources': None, 'Distinct fluids': None, 'Fluids': '', 'API': b['api'], 'Gas SG': b['gas_sg'], 'GOR [Sm3/Sm3]': b['gor'], 'Water cut': b['wc'], 'Oil [Sm3/d]': b['oil']})
    return pd.DataFrame(rows)
