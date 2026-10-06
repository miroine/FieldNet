"""Parallel solving of a network that consists of independent connected components.

A field network is often several disconnected systems (e.g. one per platform/host or an injection network next to the
production network). Their steady-state equations do not couple, so each connected component is solved in its own
process and the results are merged. HONEST LIMITS: a single connected network cannot be split this way and gets no
speed-up from ``workers`` (the Jacobian is already sparse); parallelism for those cases lives in the Monte-Carlo,
scenario and well-count runs (``network.uncertainty.parallel_map``).
"""
from __future__ import annotations
import copy, os
from collections import defaultdict


def connected_components(nodes, edges):
    """Lists of node ids per connected component (links + tank drainage assignment keep wells with their tank only for reporting)."""
    ids = [n['id'] for n in nodes]; parent = {i: i for i in ids}
    def find(x):
        while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for e in edges:
        a, b = e.get('source'), e.get('target')
        if a in parent and b in parent: parent[find(a)] = find(b)
    groups = defaultdict(list)
    for i in ids: groups[find(i)].append(i)
    return list(groups.values())


def split_components(nodes, edges):
    """[(nodes_i, edges_i)] per component; tanks stay with the wells that drain them, isolated nodes are kept in their own component."""
    comps = connected_components(nodes, edges); byid = {n['id']: n for n in nodes}; out = []
    owner = {nid: ci for ci, c in enumerate(comps) for nid in c}
    for ci, c in enumerate(comps):
        ns = [byid[i] for i in c]; es = [e for e in edges if owner.get(e.get('source')) == ci]
        out.append((ns, es))
    # tanks referenced by wells in a component are needed there (pressure comes from them): copy them in.
    for ci, (ns, es) in enumerate(out):
        have = {n['id'] for n in ns}
        for n in list(ns):
            rid = (n.get('params') or {}).get('reservoir_id')
            if rid and rid not in have and rid in byid: ns.append(byid[rid]); have.add(rid)
    return out


def _solve_component(args):
    solver, nodes, edges, kw = args
    return solver(nodes, edges, **kw)


def merge_results(results):
    """Merge per-component (p, q, info, d) tuples into one."""
    P, Q, D = {}, {}, {}; info = {}
    for p, q, i, d in results: P.update(p); Q.update(q); D.update(d)
    infos = [i or {} for _, _, i, _ in results]
    list_keys = ('constraints', 'active_constraints', 'constraint_actions', 'debug', 'well_warnings', 'injectors', 'equipment', 'attempt_history')
    dict_keys = ('well_rates', 'injector_rates', 'edge_fluids', 'enforced_well_caps_m3d', 'inline_equipment')
    for k in list_keys: info[k] = [x for i in infos for x in (i.get(k) or [])]
    for k in dict_keys:
        m = {}
        for i in infos: m.update(i.get(k) or {})
        info[k] = m
    info['success'] = all(bool(i.get('success')) for i in infos)
    info['max_abs_residual'] = max((float(i.get('max_abs_residual', 0.0)) for i in infos), default=0.0)
    info['violations'] = sum(int(i.get('violations', 0)) for i in infos)
    info['quality_gate'] = 'PASS' if all(i.get('quality_gate', 'PASS') == 'PASS' for i in infos) else 'FAIL'
    info['normalized_residual_score'] = max((float(i.get('normalized_residual_score', 0.0)) for i in infos), default=0.0)
    info['n_unknowns'] = sum(int(i.get('n_unknowns', 0)) for i in infos)
    info['nfev'] = sum(int(i.get('nfev', 0) or 0) for i in infos)
    info['message'] = '; '.join(str(i.get('message', '')) for i in infos if not i.get('success'))[:500] or 'Converged'
    info['constraints_enforced'] = any(i.get('constraints_enforced') for i in infos)
    info['solver_mode'] = 'parallel components'; info['n_components'] = len(results)
    return P, Q, info, D


def solve_parallel(nodes, edges, solver, workers=None, **kw):
    """Solve each connected component with ``solver(nodes, edges, **kw)`` (a top-level, picklable function), in parallel
    when ``workers>1`` and more than one component exists. Falls back to serial (never fails because a pool cannot start)."""
    comps = [c for c in split_components(nodes, edges) if c[1] or len(c[0]) > 1]   # drop isolated nodes (the solvers ignore them too)
    workers = int(workers or 1)
    if len(comps) <= 1 or workers <= 1:
        return solver(nodes, edges, **kw) if len(comps) <= 1 else merge_results([solver(n, e, **kw) for n, e in comps])
    from network.uncertainty import parallel_map
    res = parallel_map(_solve_component, [(solver, n, e, copy.deepcopy(kw)) for n, e in comps], min(workers, len(comps)))
    return merge_results(res)


def cpu_count():
    try: return max(1, len(os.sched_getaffinity(0)))
    except Exception: return max(1, os.cpu_count() or 1)


def compute_plan(nodes, edges, workers):
    """Human-readable description of what will run in parallel for the current model (shown in the UI)."""
    comps = [c for c in split_components(nodes, edges) if c[1] or len(c[0]) > 1]
    lines = []
    if len(comps) > 1: lines.append(f"Network solve: {len(comps)} independent systems -> up to {min(workers, len(comps))} processes.")
    else: lines.append('Network solve: one connected system -> runs on a single core (sparse Newton/TRF; parallel workers are used by Monte-Carlo, scenarios and well-count studies).')
    lines.append(f'Monte-Carlo / scenarios / well-count: {workers} worker process(es) of {cpu_count()} available cores.')
    return lines
