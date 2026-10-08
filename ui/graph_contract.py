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
    # Tank <-> tank communication links live in the source tank's params['communication']; drop dangling/self/duplicate ones.
    tank_ids = {n['id'] for n in out_nodes if n.get('kind') == 'reservoir'}; cpairs = set()
    for n in out_nodes:
        if n.get('kind') != 'reservoir' or not isinstance((n['params']).get('communication'), list): continue
        keep = []
        for c in n['params']['communication']:
            o = c.get('to') if isinstance(c, dict) else None; key = tuple(sorted((n['id'], str(o))))
            if o not in tank_ids or o == n['id'] or key in cpairs:
                issues.append(f"Dropped invalid tank communication {n['id']}->{o}"); continue
            cpairs.add(key); keep.append(c)
        if keep: n['params']['communication'] = keep
        else: n['params'].pop('communication', None)
    out_edges = []; pairs = set(); eids = set(); kind = {n['id']: n.get('kind') for n in out_nodes}
    for e in edges:
        s, t = e.get('source'), e.get('target')
        # A reservoir tank *feeds* wells/injectors; it is not a pipe. Wiring tank -> well with a
        # pipeline made the tank a 250-bar pipe source, killed every well and gave zero forecasts.
        pair = {kind.get(s), kind.get(t)}
        if 'reservoir' in pair and pair & {'well', 'water_injector', 'gas_injector', 'injector'}:
            tank, other = (s, t) if kind.get(s) == 'reservoir' else (t, s)
            nd = next(n for n in out_nodes if n['id'] == other)
            old = nd['params'].get('reservoir_id')
            if nd.get('kind') == 'well' and old and old != tank and old in kind:                      # a second tank on a well: commingled production
                al = nd['params'].get('reservoir_alloc') or [{'tank_id': old, 'share': 1.0}]
                if tank not in [a.get('tank_id') for a in al]: al = al + [{'tank_id': tank, 'share': 1.0}]
                nd['params']['reservoir_alloc'] = al; issues.append(f"{nd.get('name', other)} now drains {len(al)} tanks (equal productivity shares; edit them in the well settings).")
                tank = old
            nd['params']['reservoir_id'] = tank
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


# Values the property panel *displays* for a missing parameter. Opening a component in the panel writes
# these back into the model; that is not an engineering change, so it must not invalidate a solve.
# (tests/test_graph_contract_hash.py checks the solver-relevant ones against the physics defaults.)
HASH_DEFAULTS = {
    'well': {'reservoir_pressure_bar': 200.0, 'ipr_model': 'PI', 'pi_m3d_bar': 10.0, 'qmax_m3d': 1500.0, 'gas_c_sm3d_bar2n': 50.0, 'gas_n': 1.0,
             'depth_m': 2000.0, 'tubing_id_m': 0.0762, 'water_cut': 0.2, 'gor_sm3sm3': 100.0, 'temperature_c': 70.0, 'skin': 0.0,
             'vlp_model': 'Beggs-Brill', 'correlation': 'Beggs-Brill', 'lift_type': 'none', 'lift_assist_bar': 0.0, 'available': True,
             'esp_rated_rate_m3d': 1000.0, 'esp_shutoff_head_bar': 80.0, 'esp_speed_fraction': 1.0},
    'injector': {'injectivity_m3d_bar': 10.0, 'reservoir_pressure_bar': 200.0, 'depth_m': 2000.0, 'available': True},
    'reservoir': {'fluid_phase': 'oil', 'reservoir_pressure_bar': 250.0, 'temperature_c': 90.0, 'boi_rm3_sm3': 1.25, 'rsi_sm3_sm3': 100.0,
                  'bubble_point_bar': 150.0, 'ct_1bar': 1.5e-4, 'swi': 0.2, 'min_pressure_bar': 20.0, 'aquifer_pi_m3d_bar': 0.0,
                  'water_breakthrough_rf': 0.05, 'rf_at_max_water_cut': 0.40, 'max_water_cut': 0.9, 'gor_rise_factor': 3.0},
    'edge': {**EDGE_PARAM_DEFAULTS, 'ambient_temperature_c': 4.0, 'overall_u_w_m2k': 5.0, 'wax_appearance_temperature_c': 25.0, 'erosion_c_factor': 100.0},
}
_HASH_KIND = {'well': 'well', 'water_injector': 'injector', 'gas_injector': 'injector', 'injector': 'injector', 'reservoir': 'reservoir'}


def _canon(x):
    """Canonical JSON-able form: floats rounded to 9 significant digits (unit round-trips add ~1e-16 noise)."""
    if isinstance(x, bool) or x is None or isinstance(x, (str, int)): return x
    if isinstance(x, float): return float(f'{x:.9g}') if x == x and abs(x) != float('inf') else str(x)
    if isinstance(x, dict): return {str(k): _canon(v) for k, v in sorted(x.items(), key=lambda kv: str(kv[0]))}
    if isinstance(x, (list, tuple)): return [_canon(v) for v in x]
    return x


def _same(a, b):
    if isinstance(a, (bool, str)) or isinstance(b, (bool, str)) or a is None or b is None: return a == b
    try: return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(b)))
    except (TypeError, ValueError): return a == b


def _drop_defaults(params, defaults):
    return {k: v for k, v in (params or {}).items() if not (k in defaults and _same(v, defaults[k]))}


def graph_hash(nodes, edges):
    """Fingerprint of everything the solver sees. Layout/name edits, float noise from unit conversion and
    panel-injected default values do not invalidate results (moving or merely selecting an object must not)."""
    nn = []
    for n in sorted(nodes, key=lambda z: str(z.get('id'))):
        d = {k: v for k, v in n.items() if k not in _LAYOUT_KEYS}
        d['params'] = _drop_defaults(n.get('params'), HASH_DEFAULTS.get(_HASH_KIND.get(n.get('kind')), {}))
        nn.append(d)
    ee = []
    for e in sorted(edges, key=lambda z: str(z.get('id'))):
        d = dict(e); d['params'] = _drop_defaults(e.get('params'), HASH_DEFAULTS['edge']); ee.append(d)
    body = _canon(to_builtin({'nodes': nn, 'edges': ee}))
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]


def set_edge_kind(e, kind):
    """Change a connection's type consistently: non-pipelines carry no length, a pipeline regains one."""
    if kind not in LINK_TYPES or e.get('kind') == kind: return e
    e['kind'] = kind
    if kind == 'pipeline':
        if float(e.get('length_m') or 0.0) <= 0.0: e['length_m'] = EDGE_DEFAULTS['length_m']
    else: e['length_m'] = 0.0
    for k, v in EDGE_PARAM_DEFAULTS.items(): e.setdefault('params', {}).setdefault(k, v)
    return e


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
    from network.masking import strip_masked
    nodes, edges = strip_masked(nodes, edges)      # masked elements stay on the layout but never reach the solver
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
        # A warm start from an earlier operating point can trap the solver; if it did not converge, retry from scratch and keep the better result.
        if kwargs.get('warm_start') and not (bool(p) and info.get('quality_gate') == 'PASS'):
            kw2 = dict(kwargs); kw2['warm_start'] = None
            try:
                r2 = solver(nodes, edges, **kw2)
                if bool(r2[0]) and (r2[2].get('quality_gate') == 'PASS' or float(r2[2].get('max_abs_residual', 1e9)) < float(info.get('max_abs_residual', 1e9))): p, q, info, d = r2
            except Exception: pass
    except Exception as exc:  # never leave the UI stuck in SOLVING
        state.pop('v21_warm_start', None)
        state['solve'] = {'hash': h, 'status': FAILED, 'message': f'Solver error: {exc}', 'results': None}
        return state['solve']
    from network.masking import pad_results
    p, q = pad_results(state.get('nodes', []), state.get('edges', []), p, q)
    ok = bool(p) and info.get('quality_gate') == 'PASS'
    if ok: msg = f"Converged · {sum(v.get('liquid_rate_m3d', 0.0) for v in d.values()):,.0f} m³/d liquid"
    else:
        errs = [x.get('message', '') for x in info.get('debug', []) if x.get('severity') == 'error'] or [info.get('message', 'Solve failed')]
        msg = ' | '.join(str(m) for m in errs[:3])
    state['solve'] = {'hash': h, 'status': SOLVED if ok else FAILED, 'message': msg, 'results': (p, q, info, d)}
    # Only a converged state is a valid starting point. Keeping a failed one made every later solve (after the user fixed the data) start from the bad point.
    if ok and p: state['v21_warm_start'] = {'pressures': p, 'flows': q, 'well_rates': {k: v['liquid_rate_m3d'] for k, v in d.items()}}
    else: state.pop('v21_warm_start', None)
    return state['solve']
    ok = bool(p) and info.get('quality_gate') == 'PASS'
    if ok: msg = f"Converged · {sum(v.get('liquid_rate_m3d', 0.0) for v in d.values()):,.0f} m³/d liquid"
    else:
        errs = [x.get('message', '') for x in info.get('debug', []) if x.get('severity') == 'error'] or [info.get('message', 'Solve failed')]
        msg = ' | '.join(str(m) for m in errs[:3])
    state['solve'] = {'hash': h, 'status': SOLVED if ok else FAILED, 'message': msg, 'results': (p, q, info, d)}
    if p: state['v21_warm_start'] = {'pressures': p, 'flows': q, 'well_rates': {k: v['liquid_rate_m3d'] for k, v in d.items()}}
    return state['solve']
