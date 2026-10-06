"""Gas-lift allocation under a field lift-gas supply limit (screening level, equal-slope).

``optimization.gas_lift_allocator.GasLiftAllocator`` allocates in fixed small steps with two full network solves per
well per step, which is impractical for field-scale supplies (tens of thousands of Sm3/d), and
``optimization.field_optimizer._gas_lift_plan`` is tied to that optimiser's internal state. This module instead
follows the same idea as the latter (concave-hull marginal allocation) as a stand-alone function:

1. one baseline network solve gives each gas-lift well's wellhead pressure, reservoir pressure, water cut and GOR;
2. each well's oil-rate vs lift-gas curve is evaluated with the single-well IPR/VLP model (``physics.well_model``)
   at that WHP (rate limits in the well params are respected);
3. the upper concave hull of every curve is built and hull segments are filled in order of decreasing slope until the
   supply is used up (equal-slope / equal marginal-gain condition at the optimum);
4. the allocation is verified with full network solves (none / allocated / unlimited) and the WHP is refreshed once
   (``passes=2``) because WHP moves when rates change.

Limitations: curves are evaluated at fixed WHP per pass (network back-pressure interaction is only captured through
the refresh); only wells with ``lift_type == 'gas_lift'`` and ``available`` take part; lift-gas compression/export
effects and gas-handling capacities are not modelled; the "unlimited" case caps each well at the lift giving its
maximum oil rate on a grid up to ``max_gas_lift_sm3d`` (well param) or, by default, max(3 x supply per well, 400000 Sm3/d). The result is only as good
as the well model; it is not validated against field data.
"""
from __future__ import annotations
import copy
import math

from optimization.objectives import effective_params

GL_KEY = 'gas_lift_injection_sm3d'
DEFAULT_UNLIMITED_CAP_SM3D = 400000.0   # per-well search range for the 'unlimited supply' reference case


def _gl_well_ids(nodes):
    out = []
    for n in nodes:
        p = n.get('params') or {}
        if n.get('kind') != 'well': continue
        av = p.get('available', True)
        if isinstance(av, str): av = av.strip().lower() not in ('false', '0', 'no', 'off')
        if str(p.get('lift_type', '')).lower().replace(' ', '_') == 'gas_lift' and av: out.append(n['id'])
    return out


def _total_oil(details):
    return float(sum(max(float(d.get('oil_rate_m3d', 0.0) or 0.0), 0.0) for d in (details or {}).values()))


def _with_lift(nodes, alloc):
    ns = copy.deepcopy(nodes)
    for n in ns:
        if n['id'] in alloc: n.setdefault('params', {})[GL_KEY] = float(alloc[n['id']])
    return ns


def _curve(prm, whp, gmax, npts):
    from physics.well_model import well_settings, solve_well_rate, well_state
    grid = [gmax * i / npts for i in range(npts + 1)]; oil = []
    for g in grid:
        p = dict(prm); p[GL_KEY] = g; p['lift_type'] = 'gas_lift'
        s = well_settings(p); q, _ = solve_well_rate(whp, s)
        oil.append(q * (1.0 - s['water_cut']))
    return grid, oil


def _hull_segments(grid, vals):
    hull = [0]
    for k in range(1, len(grid)):
        while len(hull) >= 2:
            a, b = hull[-2], hull[-1]
            if (vals[b] - vals[a]) * (grid[k] - grid[b]) <= (vals[k] - vals[b]) * (grid[b] - grid[a]): hull.pop()
            else: break
        hull.append(k)
    return [((vals[b] - vals[a]) / (grid[b] - grid[a]), grid[a], grid[b]) for a, b in zip(hull[:-1], hull[1:]) if grid[b] > grid[a]]


def _plan(curves, total):
    """Equal-slope fill. Returns (alloc, marginal_slope) where slope is oil [m3/d] per Sm3/d at the margin."""
    segs = [(sl, wid, a, b) for wid, (grid, vals) in curves.items() for sl, a, b in _hull_segments(grid, vals)]
    segs.sort(key=lambda s: (-s[0], s[2], str(s[1])))
    alloc = {w: 0.0 for w in curves}; left = float(total); margin = 0.0; last_funded = 0.0; exhausted = False
    for sl, wid, a, b in segs:
        if sl <= 1e-12: break
        if left <= 1e-9: margin = sl; exhausted = True; break
        take = min(b - a, left); alloc[wid] += take; left -= take; last_funded = sl
        if take < b - a - 1e-9: margin = sl; exhausted = True; break
    # supply exhausted: value of one more Sm3/d = slope at the margin; supply not binding: 0
    return alloc, (margin if exhausted else 0.0), last_funded


def allocate_with_lift_gas_limit(nodes, edges, total_lift_gas_sm3d, solve_fn=None, *, points=24, passes=2):
    """Distribute ``total_lift_gas_sm3d`` [Sm3/d] over the gas-lift wells by equal marginal oil gain.

    ``solve_fn(nodes, edges) -> (pressures, flows, info, details)`` (default ``solver.v21.solve_v21``).

    Returns a dict:
      ``success``, ``message``; ``allocation`` {well: Sm3/d}; ``allocation_table`` rows (well, none/allocated/unlimited lift,
      oil rates, marginal value); ``oil_none_m3d`` / ``oil_allocated_m3d`` / ``oil_unlimited_m3d`` (network solves);
      ``gain_vs_none_m3d``; ``loss_vs_unlimited_m3d`` (>= 0 means unlimited supply would give that much more);
      ``marginal_value_m3d_per_1000sm3d`` (extra oil from one more 1000 Sm3/d of supply; 0 if the supply is not binding);
      ``unlimited_allocation``; ``total_allocated_sm3d``; ``supply_binding``; ``nodes`` (copy with the allocation set);
      ``limitations``.
    """
    if solve_fn is None:
        from solver.v21 import solve_v21 as solve_fn
    total = max(float(total_lift_gas_sm3d), 0.0); ids = _gl_well_ids(nodes)
    lim = ['Screening level: curves from the single-well model at baseline WHP (refreshed once), not a full network optimisation.',
           'Lift-gas compression, gas handling and export constraints are not modelled.']
    empty = {'success': False, 'allocation': {}, 'allocation_table': [], 'oil_none_m3d': 0.0, 'oil_allocated_m3d': 0.0, 'oil_unlimited_m3d': 0.0,
             'gain_vs_none_m3d': 0.0, 'loss_vs_unlimited_m3d': 0.0, 'marginal_value_m3d_per_1000sm3d': 0.0, 'unlimited_allocation': {},
             'total_allocated_sm3d': 0.0, 'supply_binding': False, 'nodes': copy.deepcopy(nodes), 'limitations': lim}
    if not ids: return dict(empty, message='No available gas-lift wells (lift_type == "gas_lift") in the network')

    def net_oil(ns):
        r = solve_fn(ns, copy.deepcopy(edges)); return _total_oil(r[3]), r[3]
    zero = {w: 0.0 for w in ids}
    oil_none, d_none = net_oil(_with_lift(nodes, zero))
    # curves & plan, refreshing WHP/reservoir state from a network solve at the current allocation
    alloc = dict(zero); details = d_none; margin = 0.0; unlimited = {}; curves = {}
    by = {n['id']: n for n in nodes}
    for k in range(max(int(passes), 1)):
        curves = {}; ucurves = {}
        for w in ids:
            d = details.get(w) or {}; prm = effective_params(d, by[w].get('params'))
            whp = float(d.get('whp_bar') or prm.get('potential_whp_bar') or 20.0)
            ucap = float(prm.get('max_gas_lift_sm3d', max(3.0 * total / len(ids), 2.0 * float(by[w]['params'].get(GL_KEY, 0.0)), DEFAULT_UNLIMITED_CAP_SM3D)))
            gmax = max(min(total, ucap), 1e-9)
            curves[w] = _curve(prm, whp, gmax, points)
            ucurves[w] = _curve(prm, whp, ucap, points)
        alloc, margin, _ = _plan(curves, total)
        if k < passes - 1:
            _, details = net_oil(_with_lift(nodes, alloc))
    unlimited = {}
    for w, (grid, vals) in ucurves.items():
        i = max(range(len(vals)), key=lambda j: (round(vals[j], 9), -grid[j])); unlimited[w] = grid[i]
    oil_alloc, d_alloc = net_oil(_with_lift(nodes, alloc))
    oil_unl, d_unl = net_oil(_with_lift(nodes, unlimited))
    # never report an allocation worse than no lift (model/network mismatch): fall back and say so
    msg = f'Allocated {sum(alloc.values()):.0f} of {total:.0f} Sm3/d over {len(ids)} gas-lift well(s)'
    if oil_alloc < oil_none - 1e-6:
        alloc = dict(zero); oil_alloc, d_alloc = oil_none, d_none; msg += '; allocation reduced network oil, kept zero lift instead'
    rows = []
    for w in ids:
        gh = _hull_segments(*curves[w]); slope_here = next((sl for sl, a, b in gh if b > alloc[w] + 1e-9), 0.0)
        rows.append({'Well': w, 'Lift gas allocated (Sm3/d)': alloc[w], 'Lift gas unlimited (Sm3/d)': unlimited[w],
                     'Oil none (m3/d)': (d_none.get(w) or {}).get('oil_rate_m3d', 0.0), 'Oil allocated (m3/d)': (d_alloc.get(w) or {}).get('oil_rate_m3d', 0.0),
                     'Oil unlimited (m3/d)': (d_unl.get(w) or {}).get('oil_rate_m3d', 0.0),
                     'Marginal gain (m3/d per 1000 Sm3/d)': 1000.0 * slope_here})
    return {'success': True, 'message': msg, 'allocation': alloc, 'allocation_table': rows, 'oil_none_m3d': oil_none, 'oil_allocated_m3d': oil_alloc,
            'oil_unlimited_m3d': oil_unl, 'gain_vs_none_m3d': oil_alloc - oil_none, 'loss_vs_unlimited_m3d': max(oil_unl - oil_alloc, 0.0),
            'marginal_value_m3d_per_1000sm3d': 1000.0 * margin, 'unlimited_allocation': unlimited, 'total_allocated_sm3d': sum(alloc.values()),
            'supply_binding': sum(unlimited.values()) > total + 1e-6, 'nodes': _with_lift(nodes, alloc), 'limitations': lim}
