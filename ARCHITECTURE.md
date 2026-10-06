# FieldNet Architecture

Technical overview of modular design, data flow, key algorithms, and extension points.

## Table of Contents

1. [Modular Design](#modular-design)
2. [Data Flow](#data-flow)
3. [Key Algorithms](#key-algorithms)
4. [Canvas Editor Contract](#canvas-editor-contract)
5. [Solver Kernel](#solver-kernel)
6. [Tank Material Balance](#tank-material-balance)
7. [Caching & Performance](#caching--performance)
8. [Extension Points](#extension-points)

---

## Modular Design

```
fieldnet/
├── network/          # Graph topology, tank material balance
│   ├── __init__.py
│   ├── reservoir_mb.py       # Tank class, material balance stepping
│   ├── forecast.py           # Forecast loop and KPI aggregation
│   ├── coupled_forecast*.py  # Aquifer / multi-tank scenarios
│   └── interchange_v27.py    # CSV/JSON serialization
│
├── physics/          # PVT, IPR, VLP, hydraulics
│   ├── pvt.py               # Black-oil screening model
│   ├── well_model.py        # Unified well IPR/VLP (NEW v30)
│   ├── well_performance_v20.py  # Legacy well definitions
│   ├── vlp.py               # Beggs-Brill, Homogeneous tubing correlations
│   ├── beggs_brill.py       # Detailed Beggs-Brill implementation
│   ├── multiphase.py        # Holdup, friction, density calculations
│   ├── hydraulics.py        # Pipeline pressure drop
│   ├── choke.py             # Choke/control valve models
│   ├── controls.py          # Rate limiters, shut-in enforcement
│   └── unit_system.py       # Unit conversion helpers
│
├── solver/           # Network equations and solvers
│   ├── steady_state.py      # Main SciPy interface (REWRITTEN v30)
│   ├── equations.py         # Residual calculation (NEW v30)
│   ├── v21.py               # Public solve API, warm-start
│   ├── physical_audit.py    # Model QA checks
│   └── constraints.py       # Capacity enforcement
│
├── ui/               # Streamlit app and frontend
│   ├── app.py               # Main app entry point
│   ├── graph_contract.py    # Canvas ↔ solver contract (v30.1)
│   ├── widgets.py           # Streamlit component reseeding (v30)
│   ├── charts.py            # Plotly styling and chart functions
│   ├── canvas.py            # Canvas component wrapper
│   ├── editor_component.py  # React editor interface
│   ├── theme.py             # Colors, styling constants
│   ├── development_v26.py   # Development schedule UI
│   ├── scenario_v29.py      # Scenario comparison UI
│   ├── uncertainty_v17.py   # Monte Carlo UI
│   └── fieldnet_canvas/     # React/TypeScript frontend (built to build/)
│       ├── src/
│       │   ├── App.tsx      # Canvas React component
│       │   └── ...
│       └── build/           # Compiled JavaScript (pre-built)
│           └── index.html   # Served by Streamlit
│
├── optimization/     # Scenarios, well-count, debottleneck
│   ├── multiscenario.py
│   ├── scenarios.py
│   ├── debottleneck.py
│   ├── allocation.py
│   └── development_v17.py
│
├── tests/            # Unit and integration tests
│   ├── test_v30_audit.py       # Solver audit tests (228 pass)
│   ├── test_v31_prognosis.py   # Tank & forecast tests (14 new)
│   └── browser/                # Browser-based editor tests (25/25)
│       └── run_editor_browser_test.py
│
├── requirements.txt  # Python dependencies
├── README.md         # Quick start
└── AUDIT_V30.md      # Detailed v30 findings and fixes
```

---

## Data Flow

### Solve Path (Steady-State)

```
Canvas Editor
    │
    ↓ (graph_contract.accept_canvas_payload)
state['nodes', 'edges']  ← normalized, stored
    │
    ↓ (User clicks "Solve")
ui/graph_contract.run_solve()
    │
    ├─→ reservoir_mb.apply_tank_links()
    │   (Add tank pressure/fluid to linked wells)
    │
    ├─→ solver.v21.solve()
    │   (Solve network)
    │   ├─→ solver/steady_state.py (setup equations)
    │   ├─→ solver/equations.py (residual callback)
    │   └─→ scipy.optimize.least_squares (SciPy solver)
    │
    ├─→ State updated: state['solve'] = {
    │       'hash': graph_hash(...),
    │       'status': 'SOLVED' or 'FAILED',
    │       'results': (pressures, flows, info, well_details),
    │       'v21_warm_start': {pressures, flows, well_rates}
    │   }
    │
    └─→ UI renders results
        Network Results → Pressure & Flow tab
```

### Forecast Path (Time-Stepping)

```
network/forecast.run_forecast()
    │
    Loop: for each timestep in forecast:
    │
    ├─→ reservoir_mb.Tank.step(produced_volumes, ...)
    │   (Material balance: update tank pressure)
    │
    ├─→ apply_tank_links()
    │   (Propagate new tank pressure to wells)
    │
    ├─→ solver.v21.solve()
    │   with warm_start from previous step
    │   (Solve network at new tank pressure)
    │
    ├─→ Record: rates, pressures, KPIs
    │
    └─→ Next timestep
    │
    └─→ Aggregate KPIs & charts
        UI renders forecast results
```

### Canvas → Solver Contract (v30.1+)

The **single source of truth** is `ui/graph_contract.py`:

```python
# Editor sends:
{
    'schema': 'fieldnet.graph/1',
    'rev': '<unique-id>',  # prevents Streamlit replay
    'nodes': [...],
    'edges': [...]
}

# Python normalizes & stores:
accept_canvas_payload(state, payload)
    ├─→ Check rev (ignore stale replays)
    ├─→ normalize_graph() (drop dangling, fill defaults)
    └─→ state['nodes'], state['edges'] updated

# Solver sees:
solver_input(nodes, edges)
    └─→ apply_tank_links()
        └─→ Returns modified nodes with tank pressures
```

---

## Key Algorithms

### Well IPR/VLP Intersection (Nodal Analysis)

**Problem:** Find the pressure where IPR = VLP.

**Algorithm:**

```python
def nodal_solve(well, tank_pressure, wellhead_pressure):
    # IPR: q = f(Pwf, Pr, PI, model)
    # VLP: q = g(Pwf, Pwh, D, correlation)
    
    # Search for intersection
    for pwf in linspace(wellhead_pressure, tank_pressure):
        q_ipr = ipr_rate(pwf, ...)
        q_vlp = vlp_rate(pwf, ...)
        if abs(q_ipr - q_vlp) < tolerance:
            return (pwf, q_ipr)  # Found intersection
    
    # No intersection found
    return (None, 0.0)  # Well is dead
```

**Stability:** The solver applies **GAP convention**: select the highest-rate *stable* intersection. This avoids trickle rates below 5 m³/d and low-rate unstable branches.

### Beggs–Brill Holdup & Friction

**Holdup (liquid fraction):**

```
λ_HL = C * (Np / (Np + Ng))^n

C, n = f(flow regime, angle, properties)
```

v30 fixes:
- **Uphill segregated coefficient:** 3.539 (was 0.3692)
- **Holdup blending:** smooth S-curve at intermittent/distributed boundary (prevents discontinuity)
- **No-slip clamping:** holdup ≥ lambda_no_slip

**Friction multiplier:**

```
dP/dz = gradient_tp × f_tp = gradient_tp × exp(S)
```

where S is a correlation-dependent factor. v30 adds:
- **Laminar–turbulent blend** at Re = 2000–4000 (smooth transition, was discontinuous at Re = 2300)

### Pipeline Segmentation

Large pressure changes within a single pipe segment can cause converge issues. v31 uses **adaptive segmentation**:

```python
num_segments = min(8, max(1, int(length_m / 1500)))
```

Each segment:
1. Predicts pressure at midpoint using upstream density
2. Corrects pressure at outlet
3. Iterates until convergence

This stabilizes VLP for long, steep flowlines.

### Tank Material Balance (Oil)

**Void depletion:**

```
void = Np × Bo + Wp - Winj
```

**Sub-stepping (n_sub = 10):**

```python
for i in range(n_sub):
    we = jaq * max(pi - p, 0) * dt / n_sub
    dp = (void/n_sub - we) / (pv * ct_eff)
    p = max(pmin, min(pi, p - dp))
```

**Effective compressibility** (accounts for solution gas below bubble point):

```
ct_eff = ct + (1 - swi) * bg * (rsi / pb) / boi   (if p < pb)
```

This captures the **solution-gas-drive expansion** when gas liberates.

### Gas Tank Material Balance (p/z)

**Conservation equation:**

```
G * bgi_initial = (G - Gp + Ginj) * bg(p) + We + Winj - Wp
```

**Iterative solution:**

```python
net_g = G - Gp + Ginj
if net_g <= 0:
    p = pmin  # Tank depleted
    return p

target_bg = (G * bgi - (We + Winj - Wp)) / net_g
lo, hi = 0.5, pi * 1.5

for _ in range(60):  # Bisection
    m = 0.5 * (lo + hi)
    if bg(m, T, gas_sg) > target_bg:
        lo = m
    else:
        hi = m

p = 0.5 * (lo + hi)
```

No sub-stepping needed; gas tank pressure is solved directly.

---

## Canvas Editor Contract

### Revision-Based State Management

**Problem:** Streamlit replays every component's last value on every rerun, overwriting edits.

**Solution:** Graph payloads include a unique revision ID (`rev`). Python compares:

```python
if payload['rev'] == state.get('canvas_rev'):
    return 'ignored'  # Stale replay, do nothing
state['canvas_rev'] = payload['rev']  # New revision, process it
```

This allows:
- **Property-panel edits** to persist (not overwritten on next canvas click)
- **Drag operations** to send deltas (not full graph)
- **Selection changes** to not trigger solves (only graph/edge/node changes do)

### Graph Normalization

When accepting a canvas payload, run `normalize_graph()`:

```python
def normalize_graph(nodes, edges):
    # Drop dangling edges
    # Drop self-loops
    # Drop duplicate pipelines (unless allow_parallel=True)
    # Convert tank→well pipelines to tank assignments
    # Fill missing fields with defaults
    return nodes, edges, issues
```

This ensures the solver never sees invalid topologies.

### Graph Hashing

The **solve result is tagged** with a fingerprint of the solver-relevant graph:

```python
def graph_hash(nodes, edges):
    body = {
        'nodes': [...exclude x, y, name...],
        'edges': [...include all...]
    }
    return sha256(body)[:16]
```

**Purpose:** Layout edits and component renames don't invalidate cached results. Only physics changes do.

---

## Solver Kernel

### Main Interface

```python
from solver.v21 import solve

p, q, info, d = solve(
    nodes, edges,
    warm_start_pressures=None,
    warm_start_flows=None,
    warm_start_rates=None,
    target_residual=1e-12,
    max_iterations=200,
    enforce_limits=True,
    debug=False
)
```

### Equation System

For a network with N nodes and M edges:

- **N node equations:** liquid + gas mass balance at each node
- **M edge equations:** pressure drop along each edge
- **P well equations:** IPR = VLP (algebraic, not differential)

**Total unknowns:** pressures (N) + flows (M) + well rates (P)
**Total equations:** N + M + P (square system)

### Solver Algorithm

```python
def residual_callback(x):  # x = [pressures, flows, well_rates]
    # Compute mass balance residuals at each node
    # Compute pressure drop residuals at each edge
    # Compute well IPR/VLP difference
    return residuals  # vector of size N + M + P

# SciPy least-squares
result = least_squares(
    residual_callback,
    x0=initial_guess,
    jac='3-point',  # or 'cs' for complex-step
    method='lm',  # Levenberg-Marquardt
    ftol=1e-12,
    xtol=1e-9,
    max_nfev=200
)
```

**Jacobian options:**
- **Sparse (>10 nodes):** KLU direct solver via scipy.sparse.linalg
- **Dense (<10 nodes):** LU factorization via numpy.linalg

### Warm-Start

From a previous solve, retain:
- Pressures `p_prev`
- Flows `q_prev`
- Well rates `rates_prev`

Pass as initial guess. Typically reduces iterations from ~34 → ~5.

---

## Tank Material Balance

### Tank Class

```python
class Tank:
    def __init__(self, node):
        self.phase = node['params']['fluid_phase']
        # Load phase-specific params
        if self.phase == 'oil':
            self.n = stoiip; self.boi = ...; self.rsi = ...; self.pb = ...
        else:
            self.g = giip; self.cgr = ...
    
    def well_overrides(self, well_params):
        # Return tank pressure and fluid composition for linked wells
        o = {'reservoir_pressure_bar': self.p}
        if self.phase == 'oil':
            o['water_cut'] = ...  # S-curve
            o['gor_sm3sm3'] = ...  # Rise below Pb
        elif self.phase != 'oil':
            o['ipr_model'] = 'Gas'
            o['vlp_model'] = 'Homogeneous'
        return o
    
    def step(self, oil_sm3, water_m3, gas_sm3, water_inj_m3=0, gas_inj_sm3=0, dt_days=0):
        # Material balance step
        # Update self.p, self.np, self.wp, self.gp, ...
```

### Integration with Forecast

```python
def run_forecast(nodes, edges, forecast_steps=60):
    tanks = tanks_from_nodes(nodes)
    
    for step in range(forecast_steps):
        # Solve network at current tank pressures
        p, q, info, d = solve(nodes, edges)
        
        # Extract produced volumes
        oil_prod = sum(d[w]['liquid'] * ... for w in wells)  # [Sm³]
        
        # Step tanks
        for tank_id, tank in tanks.items():
            tank.step(
                oil_sm3=oil_prod,
                ...,
                dt_days=timestep_days
            )
        
        # Update nodes for next solve
        nodes = apply_tank_links(nodes, tanks)
        
        # Record KPIs
        results.append({
            'pressure': tanks[tank_id].p,
            'rates': {w: d[w]['liquid'] for w in wells},
            ...
        })
```

---

## Caching & Performance

### Streamlit Caching

The v30 **stale widget bug** was fixed with manual widget reseeding in `ui/widgets.py`:

```python
@st.cache_resource
def get_solver():
    return solve  # Avoid reimporting

def sync_widgets_to_model(state):
    # Recompute widget values from model
    # Prevents stale widget values from overwriting model on rerun
```

### Per-Component Caching

Solver uses internal caching for:
- PVT properties (density, viscosity) at pressure/composition
- Correlation (Beggs-Brill) holdup/friction at conditions
- Choke flow coefficients

Cache is cleared between solves (new graph) but kept within a solve (same nodes/edges).

### Warm-Start Caching

Previous solve results are stored:

```python
state['v21_warm_start'] = {
    'pressures': p,
    'flows': q,
    'well_rates': {k: v['liquid_rate_m3d'] for k, v in d.items()}
}
```

Next solve (e.g., forecast step) uses these as initial guess. ~7× speedup.

---

## Extension Points

### Adding a New Correlation (VLP)

1. **Implement in `physics/`:**

   ```python
   # physics/my_correlation.py
   def tubing_gradient(q, pwf, t, d, gor, api, gas_sg, ...):
       # Return dP/dz [bar/m]
       ...
   ```

2. **Register in `physics/vlp.py`:**

   ```python
   CORRELATIONS = {
       'Beggs-Brill': beggs_brill_gradient,
       'My_Correlation': my_correlation.tubing_gradient,
   }
   ```

3. **Use in UI:** Set well `correlation: 'My_Correlation'` in property panel

### Adding a New IPR Model

1. **Implement in `physics/well_model.py`:**

   ```python
   def my_ipr_rate(q_max, pwf, pr, **params):
       # Return rate [m³/d]
       ...
   ```

2. **Register:**

   ```python
   IPR_MODELS = {
       'Vogel': vogel_ipr,
       'My_IPR': my_ipr_rate,
   }
   ```

3. **Use:** Set well `ipr_model: 'My_IPR'`

### Adding KPIs to Forecast

1. **Compute in `network/forecast.py`:**

   ```python
   def compute_kpis(results):
       return {
           'my_kpi': sum(...),
           ...
       }
   ```

2. **Display in UI:** Add chart or metric in `ui/scenario_v29.py`

### Custom Scenario Logic

1. **Extend `optimization/scenarios.py`:**

   ```python
   def run_custom_scenario(base_nodes, scenario_params):
       nodes = copy.deepcopy(base_nodes)
       # Modify nodes based on scenario_params
       # Run forecast or multi-scenario
       return results
   ```

2. **Hook into UI:** Add button in Streamlit sidebar calling `run_custom_scenario()`

---

See [API_REFERENCE.md](API_REFERENCE.md) for solver and tank equations, and [EXAMPLES.md](EXAMPLES.md) for use cases.

## v32 additions
- `network/equipment.py` inline equipment expansion/collapse around the steady-state kernel.
- `solver/constraints.py` registry shared by solve / forecast / development; `solver/v21.py` enforces `ENFORCEABLE` ones.
- `network/solve_options.py` + `network/parallel_solve.py` compute layer (honour constraints, optimiser, parallel components).
- `network/element_results.py` per-element profiles; `ui/properties.py`, `ui/element_view.py`, `ui/compute_panel.py`, `ui/tank_coupling.py` UI helpers; `ui/svg_export.py`.


## v32.3 case management
- `network/case_manager.py` - pure functions/classes: `CaseLibrary` (add/save/duplicate/copy_into/rename/delete, `to_dict`), `new_case`, `solve_summary`, `compare_table`, `case_diff`, `profile_series`, JSON/ZIP (checksummed) export and import.
- `network/case_share.py` - `.fncase` = `FNCASE1 | salt | nonce | AES-256-GCM(zlib(JSON))`; scrypt n=2^15; link = URL-safe base64 with prefix `fieldnet-share:`.
- `ui/cases_view.py` - `render_cases(st, solved=, reset=)`; library kept in `st.session_state['case_library']`.
- Recommended next step for real collaboration: a shared store (S3/Azure blob or a small FastAPI + SQLite) holding the same `.fncase` blobs, with the password-derived key kept client-side. The case format already supports this unchanged.


## v32.4 PVT and thermal
- `physics/pvt_model.py` - `FluidSpec` / `Calibration` / `FluidModel.state(p,T)` (returns the same `BlackOilState` as the legacy model), `calibrate`, `rank_correlations`. The active fluid is a context variable (`fluid_scope(params)`) read by `physics.multiphase.mixture_properties`, so no correlation signature changed. `solver.equations.pipeline_march` and `physics.well_model.vlp_bhp` enter the scope from the element's `params['pvt']`.
- `physics/thermal.py` - `Stream`, `advance_segment` (energy balance), Ramey profile. `physics/vlp.tubing_bhp_bar(thermal=...)`, `solver.equations.pipeline_march(t_in, profile)`.
- `network/thermal_network.py` - `thermal_pass`, `with_thermal(solver)` (wraps the Network-tab solver; no-op without thermal elements).
- `physics/gas_quality.py`, `ui/pvt_view.py`.


## v32.5 data layer, material balance, nodal tools
- `network/annual.py` calendar-year integration of step rates (`annual_volumes`, `field_/wells_/tanks_annual`, `convert`, `bar_figure`). Forecast rows are step starts with `Step [days]`; cumulatives are step ends.
- `network/groups.py` groups as `params['group']`; all group results derived. `network/data_hub.py` `build_hub` / `check_consistency`; `ui/hub_access.py` `current_hub` (cached by model / forecast / solve signature) and the export basket (`table_actions`).
- `network/mb_analysis.py` (series from forecast or history -> balance table, fits, straight-line tables, drive indices, voidage). `ui/mb_view.py`.
- `network/nodal_uncertainty.py` (reuses `network/uncertainty.py` LHS samplers), `physics/blowout.py`, `physics/well_match.py`, `ui/nodal_tools.py`. `physics/well_model.vlp_bhp` honours `vlp_dp_multiplier`.
- `network/fluids.py` + `ui/fluid_library_view.py`: the library is a view over element params (`fluid_name`), rebuilt from elements when absent.
- `network/exporters.py`, `network/postprocess.py`, `ui/data_view.py`. Case package: `FORECAST_KEEP` now includes wells / tanks / constraints; `case['extras']` holds user inputs (`EXTRAS_KEYS`).


## v32.6 templates

`network/templates.py` holds builders (small helpers: node / pipe / well / tank / pump / compressor, `_finish` auto-layouts) and the `TEMPLATES` registry (name, category, builder, suggested start / years / step, shows, watch). `build(key)` returns a deep copy. `ui/templates_view.py` loads one into `session_state` (clears forecast / hub / case-forecast caches, stores `tpl_forecast` for the forecast tab) or saves it as a new case. Rule for new templates: pump / compressor edges sit between free nodes; verify solve + a short forecast converge before registering.
