"""Fluid blending through the production network (screening level, steady state).

Every producing well is a *source* with its own standard-condition rates (oil, water, gas from the solved
``details``) and fluid properties (API, gas specific gravity, optionally a ``physics.pvt_table.PVTTable``).
Phase rates are routed through the network with the same flow-fraction splitting as
``network.element_results.phase_flows`` (that function is called once per source, so the contribution of each
well to each node / edge is known; the problem is linear so the sum over sources equals the ordinary
``phase_flows`` result - this is tested).

Blending rules (all at standard conditions, ideal mixing - NO mixing-rule thermodynamics, no flash):
* oil, water and gas standard rates add (each phase is conserved by construction);
* blended GOR = total gas / total oil, WC = water / (oil + water), WOR = water / oil;
* blended oil specific gravity is the oil-VOLUME-weighted mean of the source oil SGs (ideal volume blending,
  equivalent to mass conservation of the stock-tank oil), API = 141.5 / SG - 131.5;
* blended gas SG is the gas-VOLUME(= mole) weighted mean (ideal gas mixing of molecular weights);
* optional PVT: with a PVTTable for a source and a node pressure, the in-situ volumes (Bo, Bg, Bw, free gas from
  Rs(p)) are evaluated per source and summed. This only reports in-situ gas fraction; it does not alter the
  standard-condition blend. Table interpolation is as implemented in PVTTable (no temperature dependence).

Limitations: gas-lift gas is only counted if it is already inside ``gas_rate_sm3d`` of the well details
(same as ``phase_flows``); injection edges carry no produced phases; loops use the consistent allocation of
``phase_flows``; no compositional tracking, no phase-change / mass transfer along the lines; the hydraulics are
NOT re-solved after ``propagate_blend_to_edges`` (re-run the solver to see the effect on pressures).
"""
from __future__ import annotations
import copy
import math
from collections import defaultdict

from network.element_results import phase_flows

SKIP_KINDS = ('well', 'reservoir', 'water_source', 'water_injector', 'gas_injector', 'tank')
RHO_WATER_SC = 999.0     # kg/Sm3 (screening constant)
RHO_AIR_SC = 1.225       # kg/Sm3 at 15 C, 1 atm
DEFAULT_API = 35.0
DEFAULT_GAS_SG = 0.75


def _num(v, default):
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def api_to_sg(api):
    return 141.5 / (float(api) + 131.5)


def sg_to_api(sg):
    return 141.5 / float(sg) - 131.5


def _unpack(solve_result):
    """Accept the (pressures, flows, info, details) tuple of solve_v21 or a dict with those keys."""
    if isinstance(solve_result, dict):
        return (solve_result.get('pressures') or {}, solve_result.get('flows') or {},
                solve_result.get('info') or {}, solve_result.get('details') or {})
    p, q, info, d = solve_result[:4]
    return p, q, info, d


def blend_streams(streams):
    """Blend standard-condition streams.

    ``streams``: iterable of dicts with ``oil`` [Sm3/d], ``water`` [Sm3/d], ``gas`` [Sm3/d] and optional
    ``api`` / ``gas_sg`` (defaults 35 / 0.75). Returns a dict with summed rates and blended properties
    (``oil, water, gas, liquid, wc, gor, wor, api, oil_sg, gas_sg, oil_kg_d, water_kg_d, gas_kg_d``).
    Properties with no basis (no oil / no gas) are None."""
    oil = water = gas = 0.0; w_sg_oil = 0.0; w_sg_gas = 0.0
    for s in streams:
        o = max(_num(s.get('oil'), 0.0), 0.0); w = max(_num(s.get('water'), 0.0), 0.0); g = max(_num(s.get('gas'), 0.0), 0.0)
        oil += o; water += w; gas += g
        w_sg_oil += o * api_to_sg(_num(s.get('api'), DEFAULT_API))
        w_sg_gas += g * _num(s.get('gas_sg'), DEFAULT_GAS_SG)
    oil_sg = w_sg_oil / oil if oil > 0 else None
    gas_sg = w_sg_gas / gas if gas > 0 else None
    liquid = oil + water
    return {'oil': oil, 'water': water, 'gas': gas, 'liquid': liquid,
            'wc': water / liquid if liquid > 0 else None,
            'gor': gas / oil if oil > 0 else None,
            'wor': water / oil if oil > 0 else None,
            'oil_sg': oil_sg, 'api': sg_to_api(oil_sg) if oil_sg else None, 'gas_sg': gas_sg,
            'oil_kg_d': oil * (oil_sg or 0.0) * RHO_WATER_SC, 'water_kg_d': water * RHO_WATER_SC,
            'gas_kg_d': gas * (gas_sg or 0.0) * RHO_AIR_SC}


def _source_fluids(nodes, source_fluids):
    out = {}
    for n in nodes:
        if n.get('kind') != 'well':
            continue
        prm = n.get('params') or {}
        f = {'api': _num(prm.get('api'), DEFAULT_API), 'gas_sg': _num(prm.get('gas_sg'), DEFAULT_GAS_SG), 'pvt_table': None}
        f.update((source_fluids or {}).get(n['id'], {}))
        out[n['id']] = f
    return out


def _per_source_flows(nodes, edges, flows, details, info):
    """{well_id: (edge_ph, node_in)} from one phase_flows call per producing well."""
    res = {}
    for n in nodes:
        d = (details or {}).get(n['id'])
        if n.get('kind') == 'well' and d:
            res[n['id']] = phase_flows(nodes, edges, flows, {n['id']: d}, info)
    return res


def _insitu(contribs, fluids, p_bar):
    """Sum per-source in-situ volumes with each source's PVTTable (sources without a table are skipped)."""
    if p_bar is None:
        return None
    oil_r = wat_r = gas_r = 0.0; used = False; free_gas_sc = 0.0
    for sid, c in contribs.items():
        tab = fluids.get(sid, {}).get('pvt_table')
        if tab is None:
            continue
        used = True
        rs = float(tab.get_gor(p_bar)); free = max(c['gas'] - c['oil'] * rs, 0.0)
        oil_r += c['oil'] * float(tab.get_bo(p_bar)); wat_r += c['water'] * float(tab.get_bw(p_bar))
        gas_r += free * float(tab.get_bg(p_bar)); free_gas_sc += free
    if not used:
        return None
    tot = oil_r + wat_r + gas_r
    return {'free_gas_sm3d': free_gas_sc, 'insitu_oil_rm3d': oil_r, 'insitu_water_rm3d': wat_r, 'insitu_gas_rm3d': gas_r,
            'insitu_gas_fraction': gas_r / tot if tot > 0 else None}


def blend_by_node(nodes, edges, solve_result, source_fluids=None, node_kinds=None, include_single_source=True, min_rate=1e-9):
    """Blended stream at every non-source node: list of flat dicts (``pandas.DataFrame(rows)`` ready).

    ``solve_result``: ``solve_v21`` tuple or dict. ``source_fluids``: optional ``{well_id: {'api', 'gas_sg',
    'pvt_table'}}`` overriding the well ``params``. ``node_kinds``: restrict to these kinds (default: every node
    that is not a well / reservoir / injection node). Columns: node_id, name, kind, pressure_bar, n_sources,
    sources ('P1 62%; P2 38%' by liquid+gas-free oil share), oil_sm3d, water_sm3d, gas_sm3d, liquid_sm3d,
    wc, gor_sm3sm3, wor, api, gas_sg, oil/water/gas_kg_d, optional in-situ columns."""
    p, q, info, details = _unpack(solve_result)
    fluids = _source_fluids(nodes, source_fluids)
    per = _per_source_flows(nodes, edges, q, details, info)
    rows = []
    for n in nodes:
        k = n.get('kind')
        if (node_kinds is not None and k not in node_kinds) or (node_kinds is None and k in SKIP_KINDS):
            continue
        contribs = {}
        for sid, (_, node_in) in per.items():
            c = node_in.get(n['id'])
            if c and (c['oil'] + c['water'] + c['gas']) > min_rate:
                contribs[sid] = {'oil': c['oil'], 'water': c['water'], 'gas': c['gas']}
        if not contribs or (len(contribs) < 2 and not include_single_source):
            continue
        b = blend_streams([{**c, 'api': fluids[s]['api'], 'gas_sg': fluids[s]['gas_sg']} for s, c in contribs.items()])
        tot = sum(c['oil'] + c['water'] for c in contribs.values()) or 1.0
        share = '; '.join(f"{s} {100 * (c['oil'] + c['water']) / tot:.0f}%" for s, c in sorted(contribs.items()))
        row = {'node_id': n['id'], 'name': n.get('name', n['id']), 'kind': k, 'pressure_bar': p.get(n['id']),
               'n_sources': len(contribs), 'sources': share,
               'oil_sm3d': b['oil'], 'water_sm3d': b['water'], 'gas_sm3d': b['gas'], 'liquid_sm3d': b['liquid'],
               'wc': b['wc'], 'gor_sm3sm3': b['gor'], 'wor': b['wor'], 'api': b['api'], 'gas_sg': b['gas_sg'],
               'oil_kg_d': b['oil_kg_d'], 'water_kg_d': b['water_kg_d'], 'gas_kg_d': b['gas_kg_d']}
        iv = _insitu(contribs, fluids, p.get(n['id']))
        if iv:
            row.update(iv)
        rows.append(row)
    return rows


def blend_by_edge(nodes, edges, solve_result, source_fluids=None, min_rate=1e-9):
    """``{edge_id: blended dict}`` (see ``blend_streams``) for every flowline carrying produced fluid."""
    p, q, info, details = _unpack(solve_result)
    fluids = _source_fluids(nodes, source_fluids)
    per = _per_source_flows(nodes, edges, q, details, info)
    out = {}
    for e in edges:
        contribs = []
        for sid, (eph, _) in per.items():
            c = eph.get(e['id'])
            if c and (c['oil'] + c['water'] + c['gas']) > min_rate:
                contribs.append({'oil': c['oil'], 'water': c['water'], 'gas': c['gas'], 'api': fluids[sid]['api'], 'gas_sg': fluids[sid]['gas_sg']})
        if contribs:
            out[e['id']] = blend_streams(contribs)
    return out


def propagate_blend_to_edges(nodes, edges, solve_result, source_fluids=None, kinds=('pipeline',), skip_single_source=False):
    """Return a DEEP COPY of ``edges`` whose pipeline ``params`` carry the blended fluid of the stream they carry
    (``water_cut``, ``gor_sm3sm3``, ``api``, ``gas_sg``; marked ``params['fluid_blend']=True``). Inputs are not
    modified; the hydraulics are not re-solved. Edges without produced fluid (or with undefined property, e.g. no
    gas) keep their existing value for that property. Single-source edges are left identical to the source fluid
    unless ``skip_single_source`` is set (then they are not touched)."""
    p, q, info, details = _unpack(solve_result)
    blended = blend_by_edge(nodes, edges, solve_result, source_fluids)
    new = copy.deepcopy(edges)
    nsrc = _edge_source_counts(nodes, edges, q, details, info)
    for e in new:
        b = blended.get(e['id'])
        if b is None or e.get('kind', 'pipeline') not in kinds:
            continue
        if skip_single_source and nsrc.get(e['id'], 0) < 2:
            continue
        prm = e.setdefault('params', {})
        for key, val in (('water_cut', b['wc']), ('gor_sm3sm3', b['gor']), ('api', b['api']), ('gas_sg', b['gas_sg'])):
            if val is not None:
                prm[key] = float(val)
        prm['fluid_blend'] = True
    return new


def _edge_source_counts(nodes, edges, flows, details, info):
    cnt = defaultdict(int)
    for sid, (eph, _) in _per_source_flows(nodes, edges, flows, details, info).items():
        for eid, c in eph.items():
            if c['oil'] + c['water'] + c['gas'] > 1e-9:
                cnt[eid] += 1
    return cnt
