"""The single editor -> solver contract.

Every path that changes or solves the network goes through this module:

* The canvas sends ``{'schema': GRAPH_SCHEMA, 'rev', 'nodes', 'edges', 'selected'}``.
  ``accept_canvas_payload`` applies it only when the revision is new (Streamlit replays a
  component's last value on every rerun) and normalises it with ``normalize_graph``.
* ``graph_hash`` fingerprints the *solver-relevant* graph (positions and names excluded),
  so results are tied to the exact model they were computed from.
* ``solve_status`` derives the explicit state shown everywhere:
  UNSOLVED (never solved, or model changed since), SOLVING, SOLVED, FAILED.
* ``run_solve`` is the one place the UI calls the solver.
"""
from __future__ import annotations
import copy, hashlib, json, uuid

GRAPH_SCHEMA = 'fieldnet.graph/1'
UNSOLVED, SOLVING, SOLVED, FAILED = 'UNSOLVED', 'SOLVING', 'SOLVED', 'FAILED'
LINK_TYPES = ('pipeline', 'choke', 'control_valve', 'pump', 'compressor')
_LAYOUT_KEYS = {'x', 'y', 'name'}

EDGE_DEFAULTS = {'kind': 'pipeline', 'length_m': 1000.0, 'diameter_m': 0.154, 'roughness_m': 4.5e-5, 'elevation_change_m': 0.0}
EDGE_PARAM_DEFAULTS = {'temperature_c': 50.0, 'water_cut': 0.2, 'gor_sm3sm3': 100.0, 'api': 35.0, 'gas_sg': 0.75,
                       'initial_rate_m3d': 500.0, 'correlation': 'Beggs-Brill', 'cv': 80.0, 'shutoff_head_bar': 35.0,
                       'rated_rate_m3d': 1500.0, 'efficiency': 0.75, 'pressure_ratio': 1.8, 'max_discharge_bar': 250.0,
                       'map_enabled': False, 'rated_gas_rate_sm3d': 150000.0, 'speed_fraction': 1.0, 'opening': 1.0}


def to_builtin(x):
    if isinstance(x, dict): return {str(k): to_builtin(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)): return [to_builtin(v) for v in x]
    if hasattr(x, 'item') and not isinstance(x, (str, bytes)):
        try: return x.item()
        except Exception: return x
    return x


def new_edge(source, target, kind='pipeline', edge_id=None):
    e = {'id': edge_id or str(uuid.uuid4())[:8], 'source': source, 'target': target, **EDGE_DEFAULTS, 'kind': kind,
         'params': dict(EDGE_PARAM_DEFAULTS)}
    if kind != 'pipeline': e['length_m'] = 0.0
    return e


def normalize_graph(nodes, edges):
    """Clean a graph coming from the editor (or an import).

    Returns (nodes, edges, issues). Dangling edges, self-loops and duplicate connections are
    dropped with an issue rather than crashing the solver later; missing edge fields get the
    same defaults as a palette-created connection.
    """
    nodes = to_builtin(copy.deepcopy(nodes or [])); edges = to_builtin(copy.deepcopy(edges or []))
    issues = []; seen = set(); out_nodes = []
    for n in nodes:
        nid = str(n.get('id') or '').strip()
        if not nid or nid in seen:
            issues.append(f"Dropped node with missing/duplicate id {nid!r}"); continue
        seen.add(nid); n['id'] = nid
        n.setdefault('kind', 'manifold'); n.setdefault('name', nid); n.setdefault('pressure_bar', None)
        n['params'] = n.get('params') or {}
        try: n['x'] = float(n.get('x') or 0.0); n['y'] = float(n.get('y') or 0.0)
        except (TypeError, ValueError): n['x'] = n['y'] = 0.0
        out_nodes.append(n)
    out_edges = []; pairs = set(); eids = set(); kind = {n['id']: n.get('kind') for n in out_nodes}
    for e in edges:
        s, t = e.get('source'), e.get('target')
        # A reservoir tank *feeds* wells/injectors; it is not a pipe. Wiring tank -> well with a
        # pipeline made the tank a 250-bar pipe source, killed every well and gave zero forecasts.
        pair = {kind.get(s), kind.get(t)}
        if 'reservoir' in pair and pair & {'well', 'water_injector', 'gas_injector', 'injector'}:
            tank, other = (s, t) if kind.get(s) == 'reservoir' else (t, s)
            nd = next(n for n in out_nodes if n['id'] == other); nd['params']['reservoir_id'] = tank
            issues.append(f"Connection {e.get('id')} between reservoir tank and {nd.get('name', other)} converted to a drainage assignment (tanks feed wells; they are not pipes)."); continue
        if s not in seen or t not in seen: issues.append(f"Dropped connection {e.get('id')} with a missing endpoint"); continue
        if s == t: issues.append(f"Dropped self-loop {e.get('id')}"); continue
        if (s, t) in pairs and e.get('kind', 'pipeline') == 'pipeline' and not (e.get('params') or {}).get('allow_parallel'):
            issues.append(f"Dropped duplicate connection {s}->{t}"); continue
        eid = str(e.get('id') or '') or str(uuid.uuid4())[:8]
        if eid in eids: eid = str(uuid.uuid4())[:8]
        base = new_edge(s, t, e.get('kind', 'pipeline') if e.get('kind', 'pipeline') in LINK_TYPES else 'pipeline', eid)
        merged = {**base, **{k: v for k, v in e.items() if k != 'params' and v is not None}, 'id': eid}
        merged['params'] = {**base['params'], **(e.get('params') or {})}
        pairs.add((s, t)); eids.add(eid); out_edges.append(merged)
    return out_nodes, out_edges, issues


def graph_hash(nodes, edges):
    """Fingerprint of everything the solver sees (layout/name edits do not invalidate results)."""
    body = {'nodes': [{k: v for k, v in n.items() if k not in _LAYOUT_KEYS} for n in sorted(nodes, key=lambda z: str(z.get('id')))],
            'edges': sorted(edges, key=lambda z: str(z.get('id')))}
    return hashlib.sha256(json.dumps(to_builtin(body), sort_keys=True, default=str).encode()).hexdigest()[:16]


def structure_changed(a_nodes, a_edges, b_nodes, b_edges):
    sig = lambda ns, es: (sorted((n['id'], n.get('kind')) for n in ns), sorted((e['id'], e['source'], e['target']) for e in es))
    return sig(a_nodes, a_edges) != sig(b_nodes, b_edges)


def accept_canvas_payload(state, payload):
    """Apply a canvas payload to ``state`` (a mutable mapping such as st.session_state).

    Returns 'ignored' (stale replay / not a graph), 'selection' (only the selection changed)
    or 'graph' (nodes/edges changed; caller should rerun).
    """
    if not isinstance(payload, dict) or not payload.get('rev'): return 'ignored'
    if payload.get('schema', GRAPH_SCHEMA) != GRAPH_SCHEMA: return 'ignored'
    if payload['rev'] == state.get('canvas_rev'): return 'ignored'
    state['canvas_rev'] = payload['rev']
    if 'selected' in payload: state['selected'] = payload.get('selected')
    if not isinstance(payload.get('nodes'), list) or not isinstance(payload.get('edges'), list): return 'selection'
    nodes, edges, issues = normalize_graph(payload['nodes'], payload['edges'])
    state['graph_issues'] = issues
    cur_n, cur_e = state.get('nodes', []), state.get('edges', [])
    if json.dumps({'n': nodes, 'e': edges}, sort_keys=True, default=str) == json.dumps({'n': to_builtin(cur_n), 'e': to_builtin(cur_e)}, sort_keys=True, default=str):
        return 'selection'
    state['nodes'], state['edges'] = nodes, edges
    return 'graph'


def solver_input(nodes, edges):
    """Isolated copy of the current graph for the solver - the only form the solver receives.
    Wells/injectors assigned to a reservoir tank take the tank's pressure (and gas-tank fluid)."""
    from network.reservoir_mb import apply_tank_links
    return apply_tank_links(copy.deepcopy(to_builtin(nodes))), copy.deepcopy(to_builtin(edges))


def solve_status(state):
    """(status, message) for the current graph."""
    if state.get('solve_request'): return SOLVING, 'Solving network...'
    rec = state.get('solve')
    if not rec: return UNSOLVED, 'Network not solved yet.'
    if rec.get('hash') != graph_hash(state.get('nodes', []), state.get('edges', [])):
        return UNSOLVED, 'Model changed since the last solve.'
    return rec.get('status', FAILED), rec.get('message', '')


def current_results(state):
    """(pressures, flows, info, details) for the current graph, or None."""
    rec = state.get('solve')
    if not rec or rec.get('hash') != graph_hash(state.get('nodes', []), state.get('edges', [])): return None
    r = rec.get('results')
    return r if (r and r[0]) else None


def run_solve(state, solver, **kwargs):
    """Solve the current graph and store the result tagged with its graph hash."""
    h = graph_hash(state.get('nodes', []), state.get('edges', []))  # fingerprint the model, not the tank-linked solver copy
    nodes, edges = solver_input(state.get('nodes', []), state.get('edges', []))
    try:
        p, q, info, d = solver(nodes, edges, **kwargs)
    except Exception as exc:  # never leave the UI stuck in SOLVING
        state['solve'] = {'hash': h, 'status': FAILED, 'message': f'Solver error: {exc}', 'results': None}
        return state['solve']
    ok = bool(p) and info.get('quality_gate') == 'PASS'
    if ok: msg = f"Converged · {sum(v.get('liquid_rate_m3d', 0.0) for v in d.values()):,.0f} m³/d liquid"
    else:
        errs = [x.get('message', '') for x in info.get('debug', []) if x.get('severity') == 'error'] or [info.get('message', 'Solve failed')]
        msg = ' | '.join(str(m) for m in errs[:3])
    state['solve'] = {'hash': h, 'status': SOLVED if ok else FAILED, 'message': msg, 'results': (p, q, info, d)}
    if p: state['v21_warm_start'] = {'pressures': p, 'flows': q, 'well_rates': {k: v['liquid_rate_m3d'] for k, v in d.items()}}
    return state['solve']
