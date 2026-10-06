"""Per-element results: phase flows, velocities and profiles for every node and flowline of a solved network.

* ``phase_flows``   oil / water / gas / injected-fluid rate on every edge, by propagating each producer's phases
                    through the network with flow-fraction splitting at junctions (steady state, so this is exact
                    for a tree and a consistent allocation for loops).
* ``edge_profile``  pressure / velocity / holdup / regime along a flowline (same march as the solver uses).
* ``well_profile``  pressure vs MD/TVD down the tubing.
* ``element_rows``  flat rows for the forecast tables and the element browser.
* ``constraint_rows`` the shared constraint evaluation (see solver/constraint_registry.py).
"""
from __future__ import annotations
import math
from collections import defaultdict

ZERO = 1e-9
PHASE_KEYS = ('oil', 'water', 'gas', 'inj_water', 'inj_gas')


def _own_sources(nodes, details, info):
    """Phase rates each node injects into the network: producers (oil, water, gas) and injection sources."""
    src = {}
    inj = (info or {}).get('injector_rates') or {}
    for n in nodes:
        k = n.get('kind'); d = (details or {}).get(n['id'])
        if k == 'well' and d:
            src[n['id']] = {'oil': d.get('oil_rate_m3d', 0.0), 'water': d.get('water_rate_m3d', 0.0), 'gas': d.get('gas_rate_sm3d', 0.0)}
    return src, inj


def phase_flows(nodes, edges, flows, details, info=None):
    """Return ({edge_id: {oil, water, gas, inj_water, inj_gas, liquid}}, {node_id: inflow dict})."""
    from solver.equations import links_of, INJECTOR_KINDS
    links = links_of(edges); nid = {n['id']: n for n in nodes}
    src, inj_rates = _own_sources(nodes, details, info)
    fluids = (info or {}).get('edge_fluids') or {}
    # Direction of travel per link: positive flow goes source->target.
    out_edges = defaultdict(list); in_edges = defaultdict(list)
    for e in links:
        q = flows.get(e['id'], 0.0)
        a, b = (e['source'], e['target']) if q >= 0 else (e['target'], e['source'])
        out_edges[a].append((e['id'], abs(q))); in_edges[b].append(e['id'])
    inj_nodes = {n['id'] for n in nodes if n.get('kind') in INJECTOR_KINDS}
    edge_ph = {e['id']: {k: 0.0 for k in PHASE_KEYS} for e in links}
    node_in = {n['id']: {k: 0.0 for k in PHASE_KEYS} for n in nodes}
    # Injection edges carry the injected fluid (water or gas) -> injector; production edges carry produced phases.
    inj_edge = {e['id'] for e in links if fluids.get(e['id']) == 'water' or e['target'] in inj_nodes or e['source'] in inj_nodes}
    for _ in range(max(60, len(nodes) + 5)):
        delta = 0.0
        for n in nodes:
            tot_out = sum(q for _, q in out_edges[n['id']])
            own = dict.fromkeys(PHASE_KEYS, 0.0)
            own.update(src.get(n['id'], {}))
            comp = {k: own[k] + sum(edge_ph[eid][k] for eid in in_edges[n['id']]) for k in PHASE_KEYS}
            node_in[n['id']] = comp
            for eid, q in out_edges[n['id']]:
                share = q / tot_out if tot_out > ZERO else 0.0
                for k in ('oil', 'water', 'gas'):
                    new = comp[k] * share
                    delta = max(delta, abs(new - edge_ph[eid][k])); edge_ph[eid][k] = new
        if delta < 1e-9: break
    for e in links:
        q = abs(flows.get(e['id'], 0.0)); ph = edge_ph[e['id']]
        if e['id'] in inj_edge:
            gas = any((nid.get(x) or {}).get('kind') == 'gas_injector' or ((nid.get(x) or {}).get('params') or {}).get('injection_fluid') == 'gas' for x in (e['source'], e['target'])) or fluids.get(e['id']) == 'gas'
            for k in ('oil', 'water', 'gas'): ph[k] = 0.0
            ph['inj_gas' if gas else 'inj_water'] = q
        ph['liquid'] = ph['oil'] + ph['water'] + ph['inj_water']
    return edge_ph, node_in


def _fluid_of(e, info):
    return ((info or {}).get('edge_fluids') or {}).get(e['id'], 'production')


def edge_profile(e, q, p_up, info=None, ph=None):
    """March a flowline from its upstream end. Returns rows {x_m, z_m, pressure_bar, velocity_ms, holdup, regime, rho, erosional_ratio}."""
    from solver.equations import flowline_segments, _flowline_fn, fnum
    from solver.steady_state import apply_fluid_follow
    e = apply_fluid_follow([e], info)[0]     # the line fluid the solver used (follows the wells)
    prm = e.get('params') or {}
    segs = flowline_segments(e)
    if e.get('kind', 'pipeline') != 'pipeline' or not segs or sum(s[0] for s in segs) <= 0: return []
    D = fnum(e, 'diameter_m', .154); eps = fnum(e, 'roughness_m', 4.5e-5); fn = _flowline_fn(prm)
    fluid = _fluid_of(e, info)
    wc = 1.0 if fluid == 'water' else fnum(prm, 'water_cut', .2); gor = 0.0 if fluid == 'water' else fnum(prm, 'gor_sm3sm3', 100.0)
    T = fnum(prm, 'temperature_c', 50.0); api = fnum(prm, 'api', 35.0); sg = fnum(prm, 'gas_sg', .75); C = fnum(prm, 'erosion_c_factor', 100.0)
    if ph and fluid != 'water' and ph.get('liquid', 0) > 1e-6:   # line fluid from the propagated phase rates
        wc = ph['water'] / max(ph['oil'] + ph['water'], 1e-9); gor = ph['gas'] / max(ph['oil'], 1e-9) if ph['oil'] > 1e-9 else gor
    order = segs if q >= 0 else list(reversed(segs))
    p = float(p_up); x = 0.0; z = 0.0; rows = [{'x_m': 0.0, 'z_m': 0.0, 'pressure_bar': p, 'velocity_ms': None, 'holdup': None, 'regime': None, 'rho_kgm3': None, 'erosional_ratio': None}]
    for dl, dz in order:
        dzs = dz if q >= 0 else -dz
        d1, _ = fn(q, dl, D, eps, dzs, max(p, 1.0), T, wc, gor, api, sg)
        pm = max(p + 0.5 * (-d1 if q >= 0 else d1), 1.0)
        d, pr = fn(q, dl, D, eps, dzs, pm, T, wc, gor, api, sg)
        p = p - d if q >= 0 else p + d; x += dl; z += dz
        v = abs(pr.get('mixture_velocity_ms') or 0.0); rho = pr.get('rho') or 0.0
        ve = C / math.sqrt(rho) if rho > 0 else None
        rows.append({'x_m': x, 'z_m': z, 'pressure_bar': p, 'velocity_ms': v, 'holdup': pr.get('liquid_holdup'), 'regime': pr.get('flow_regime'),
                     'rho_kgm3': rho, 'erosional_ratio': (v / ve) if ve else None})
    return rows


def well_profile(node, q, whp):
    """Pressure along the tubing for a producer at rate q and wellhead pressure whp."""
    from physics.well_model import well_settings
    from physics.vlp import tubing_bhp_bar
    s = well_settings(node.get('params') or {}); prof = []
    bhp, _ = tubing_bhp_bar(max(q, 0.0), whp, s['depth'], s['tubing_id'], s['roughness'], s['temperature'], s['water_cut'], s['gor'], s['api'], s['gas_sg'],
                            s['correlation'], segments=s['segments'], extra_gas_sm3d=s['gas_lift_sm3d'], gas_injection_depth_m=s['gas_lift_depth'],
                            bottomhole_temperature_c=s['bh_temperature'], geometry=s.get('geometry'), profile=prof)
    return [{'md_m': 0.0, 'tvd_m': 0.0, 'pressure_bar': whp, 'velocity_ms': None, 'liquid_holdup': None, 'rho_kgm3': None, 'regime': None}] + prof


def edge_summary(e, q, p, info, ph):
    """Max velocity / erosional ratio and dP for one link."""
    from solver.steady_state import apply_fluid_follow
    e = apply_fluid_follow([e], info)[0]
    out = {'dp_bar': None, 'max_velocity_ms': None, 'max_erosional_ratio': None, 'arrival_velocity_ms': None}
    a, b = p.get(e['source']), p.get(e['target'])
    if a is not None and b is not None: out['dp_bar'] = a - b
    if e.get('kind', 'pipeline') == 'pipeline' and a is not None and abs(q) > 1e-6:
        try:
            rows = edge_profile(e, q, a if q >= 0 else b, info, ph)
            vs = [r['velocity_ms'] for r in rows if r['velocity_ms'] is not None]; er = [r['erosional_ratio'] for r in rows if r['erosional_ratio'] is not None]
            if vs: out['max_velocity_ms'] = max(vs); out['arrival_velocity_ms'] = vs[-1]
            if er: out['max_erosional_ratio'] = max(er)
        except Exception: pass
    return out


def element_rows(nodes, edges, pressures, flows, details, info=None, date=None):
    """(node_rows, edge_rows) with phase rates and velocities; ``date`` is added as the first column when given."""
    info = info or {}; ph, node_in = phase_flows(nodes, edges, flows, details, info)
    names = {n['id']: n.get('name', n['id']) for n in nodes}
    pre = {'Date': date} if date is not None else {}
    nrows = []
    for n in nodes:
        r = {**pre, 'Node ID': n['id'], 'Name': n.get('name', n['id']), 'Kind': n.get('kind'), 'Pressure [bar]': pressures.get(n['id'])}
        d = (details or {}).get(n['id'])
        if d: r.update({'Liquid [m3/d]': d['liquid_rate_m3d'], 'Oil [m3/d]': d['oil_rate_m3d'], 'Water [m3/d]': d['water_rate_m3d'], 'Gas [Sm3/d]': d['gas_rate_sm3d'], 'BHP [bar]': d.get('bhp_bar'), 'Status': d.get('status')})
        elif n.get('kind') not in ('reservoir',):
            c = node_in.get(n['id'], {}); r.update({'Oil [m3/d]': c.get('oil'), 'Water [m3/d]': c.get('water'), 'Gas [Sm3/d]': c.get('gas'), 'Liquid [m3/d]': (c.get('oil') or 0) + (c.get('water') or 0)})
        nrows.append(r)
    erows = []
    for e in edges:
        q = flows.get(e['id'])
        if q is None: continue
        s = edge_summary(e, q, pressures, info, ph.get(e['id']))
        w = ph.get(e['id'], {})
        erows.append({**pre, 'Edge ID': e['id'], 'Name': e.get('name') or f"{names.get(e['source'])} → {names.get(e['target'])}", 'Kind': e.get('kind', 'pipeline'),
                      'From': names.get(e['source']), 'To': names.get(e['target']), 'Flow [m3/d]': q, 'Oil [m3/d]': w.get('oil'), 'Water [m3/d]': w.get('water'), 'Gas [Sm3/d]': w.get('gas'),
                      'Inj. water [m3/d]': w.get('inj_water'), 'Inj. gas [m3/d]': w.get('inj_gas'), 'dP [bar]': s['dp_bar'], 'Max velocity [m/s]': s['max_velocity_ms'],
                      'Max erosional ratio [-]': s['max_erosional_ratio']})
    return nrows, erows
