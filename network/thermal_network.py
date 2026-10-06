"""Network temperature pass: well wellhead temperatures -> flowlines (heat loss, JT, elevation) -> chokes (JT) -> mixing at nodes.

``thermal_pass`` post-processes a solved network. ``with_thermal(solver)`` wraps a solver so that pipelines with
``thermal_model == 'heat_loss'`` take their inlet temperature from the upstream node and the hydraulics are re-solved until the inlet
temperatures change by less than ``tol`` degC (default max 4 passes). With no thermal element in the model the wrapper is a plain call."""
from __future__ import annotations
import copy, math
from collections import defaultdict, deque
from physics import thermal as th
from solver.equations import pipeline_march, links_of, edge_fluids, fnum


def has_thermal(nodes, edges):
    return any(th.mode(e.get('params')) == 'heat_loss' for e in edges) or any(th.mode(n.get('params')) == 'ramey' for n in nodes if n.get('kind') == 'well')


def _stream(e, q, fluid):
    prm = e.get('params') or {}; pv = prm.get('pvt') or {}
    wc = 1.0 if fluid == 'water' else fnum(prm, 'water_cut', 0.2); gor = 0.0 if fluid == 'water' else fnum(prm, 'gor_sm3sm3', 100.0)
    return th.Stream(q, wc, gor, fnum(prm, 'api', 35.0), fnum(prm, 'gas_sg', 0.75), gas_cp_override=prm.get('gas_cp_jkgk'),
                     co2=fnum(pv, 'co2', 0.0), h2s=fnum(pv, 'h2s', 0.0), n2=fnum(pv, 'n2', 0.0))


def thermal_pass(nodes, edges, results):
    """Returns {'node_temperature_c': {id: T}, 'edge': {id: {'t_in','t_out','profile','mode'}}, 'warnings': [...]}."""
    p, q, _info, det = results
    byid = {n['id']: n for n in nodes}; fl = edge_fluids(nodes, edges); warnings = []
    tnode = {}; mixes = defaultdict(list)               # node -> [(mass-cp weight, T)]
    for n in nodes:
        if n.get('kind') == 'well':
            d = det.get(n['id']) or {}
            t = d.get('wellhead_temperature_c')
            tnode[n['id']] = float(t) if t is not None else fnum(n.get('params') or {}, 'temperature_c', 50.0)
    links = links_of(edges); indeg = defaultdict(int); out_of = defaultdict(list); edge_dir = {}
    for e in links:
        f = float(q.get(e['id'], 0.0))
        if abs(f) < 1e-9: continue
        u, v = (e['source'], e['target']) if f >= 0 else (e['target'], e['source']); edge_dir[e['id']] = (u, v, f)
        indeg[v] += 1; out_of[u].append(e)
    ready = deque([n['id'] for n in nodes if indeg[n['id']] == 0]); done = set(); res = {}
    def settle(nid):
        if nid in tnode or not mixes[nid]: return
        w = sum(a for a, _ in mixes[nid]); tnode[nid] = sum(a * t for a, t in mixes[nid]) / w if w > 0 else None
    count = {nid: indeg[nid] for nid in indeg}
    while ready:
        nid = ready.popleft(); done.add(nid); settle(nid)
        for e in out_of.get(nid, []):
            u, v, f = edge_dir[e['id']]; prm = e.get('params') or {}; kind = e.get('kind', 'pipeline'); fluid = fl.get(e['id'], 'production')
            t_in = tnode.get(u)
            if t_in is None: t_in = fnum(prm, 'temperature_c', 50.0)
            ps, pt = float(p.get(e['source'], 0.0)), float(p.get(e['target'], 0.0)); mode = th.mode(prm)
            if kind == 'pipeline':
                if mode == 'heat_loss':
                    prof = []; _dp, t_out = pipeline_march(e, f, ps, pt, fluid, t_in=t_in, profile=prof)
                else: t_out = fnum(prm, 'temperature_c', t_in) if (prm.get('temperature_c') is not None and th.mode((byid.get(u) or {}).get('params')) != 'ramey' and (byid.get(u) or {}).get('kind') != 'well') else t_in; prof = []
                if mode != 'heat_loss' and (byid.get(u) or {}).get('kind') == 'well': t_out = t_in    # no thermal model: fluid keeps the well's temperature
            elif kind in ('choke', 'control_valve'):
                s = _stream(e, f, fluid); dp = pt - ps if f >= 0 else ps - pt
                t_out = t_in + s.jt_k_per_bar(max(ps if f >= 0 else pt, 1.0), t_in) * dp; prof = []
            else: t_out = t_in; prof = []
            res[e['id']] = {'t_in': t_in, 't_out': t_out, 'profile': prof, 'mode': mode if kind == 'pipeline' else kind, 'from': u, 'to': v}
            s = _stream(e, f, fluid); cp = s.cp(max(pt, 1.0), t_out); mixes[v].append((s.mdot * cp, t_out))
            count[v] -= 1
            if count[v] == 0: ready.append(v)
    left = [nid for nid, c in count.items() if c > 0 and nid not in done]
    if left: warnings.append('flow loop(s) at ' + ', '.join(str(x) for x in left[:5]) + ': temperatures there are not propagated')
    for nid in list(mixes): settle(nid)
    return {'node_temperature_c': {k: v for k, v in tnode.items() if v is not None}, 'edge': res, 'warnings': warnings}


def with_thermal(solver, tol=0.5, max_iter=4):
    """Wrap ``solver(nodes, edges, **kw) -> (p, q, info, d)`` with the thermal fixed-point iteration (no-op without thermal elements)."""
    def wrapped(nodes, edges, *a, **kw):
        if not has_thermal(nodes, edges): return solver(nodes, edges, *a, **kw)
        nodes = copy.deepcopy(nodes); edges = copy.deepcopy(edges); hist = []
        for it in range(max_iter):
            res = solver(nodes, edges, *a, **kw)
            if not res or not res[0]: return res
            from solver.steady_state import apply_fluid_follow
            tp = thermal_pass(nodes, apply_fluid_follow(edges, res[2]), res); change = 0.0
            for e in edges:
                if th.mode(e.get('params')) != 'heat_loss' or e['id'] not in tp['edge']: continue
                t_new = tp['edge'][e['id']]['t_in']; t_old = fnum(e.get('params') or {}, 'temperature_c', 50.0)
                change = max(change, abs(t_new - t_old)); e.setdefault('params', {})['temperature_c'] = t_new
            hist.append(change)
            if change < tol: break
        p, q, info, d = res; info = dict(info); tp['iterations'] = len(hist); tp['converged'] = bool(hist and hist[-1] < tol); tp['max_change_c'] = hist[-1] if hist else 0.0
        info['thermal'] = tp
        return p, q, info, d
    return wrapped
