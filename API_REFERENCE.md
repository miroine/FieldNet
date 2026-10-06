# FieldNet API Reference

Technical documentation for programmatic network construction, tank properties, solver interfaces, and data structures.

## Table of Contents

1. [Network Graph Contract](#network-graph-contract)
2. [Node Types & Parameters](#node-types--parameters)
3. [Edge (Connection) Types](#edge-connection-types)
4. [Tank Material Balance](#tank-material-balance)
5. [Well Performance Models](#well-performance-models)
6. [Solver API](#solver-api)
7. [Data Structures](#data-structures)
8. [Import/Export Formats](#importexport-formats)

---

## Network Graph Contract

### The Graph Model

Every network is a directed graph of `nodes` and `edges`:

```python
{
    "schema": "fieldnet.graph/1",
    "rev": "<unique-revision-id>",
    "nodes": [
        {
            "id": "<node-id>",
            "kind": "well",  # or pump, choke, separator, ...
            "name": "Producer_1",
            "x": 100.0,  # canvas position [px]
            "y": 150.0,
            "pressure_bar": None,  # results only (solver output)
            "params": { ... }  # type-specific parameters
        },
        ...
    ],
    "edges": [
        {
            "id": "<edge-id>",
            "source": "<node-id>",
            "target": "<node-id>",
            "kind": "pipeline",  # or choke, pump, etc.
            "length_m": 1000.0,
            "diameter_m": 0.154,
            "roughness_m": 4.5e-5,
            "elevation_change_m": 0.0,
            "params": { ... }  # fluid and equipment properties
        },
        ...
    ]
}
```

### Graph Normalization

The UI applies `normalize_graph(nodes, edges)` on import to:
- Drop dangling edges (missing endpoint nodes)
- Drop self-loops
- Drop duplicate pipelines (unless `allow_parallel: True`)
- Convert reservoir tank → well pipelines to tank assignments
- Fill missing fields with palette defaults

### Revision-Based State Management

To prevent Streamlit replay from overwriting the model on every rerun:
- The editor sends a unique `rev` (revision ID, e.g., UUID) with every graph payload
- Python compares `payload['rev']` with `state['canvas_rev']`
- Stale replays (same `rev` as before) are ignored
- Only new revisions update the model

This solves the critical bug where canvas replays would revert property-panel edits.

---

## Node Types & Parameters

### Well (Production)

```python
{
    "id": "well_1",
    "kind": "well",
    "name": "Producer_1",
    "params": {
        "reservoir_pressure_bar": 200.0,  # or from tank assignment
        "initial_water_cut": 0.1,
        "water_cut": 0.1,  # current, updated by tank depletion
        "initial_gor_sm3sm3": 100.0,
        "gor_sm3sm3": 100.0,  # current, updated by tank depletion
        "ipr_model": "Vogel",  # or "PI" (linear) or "Gas" (backpressure)
        "productivity_index_m3d_bar": 50.0,
        "max_liquid_rate_m3d": 1000.0,
        "vlp_model": "Beggs-Brill",  # tubing correlations
        "correlation": "Beggs-Brill",  # alias for vlp_model (legacy)
        "wellhead_pressure_bar": 20.0,  # WHP or outlet pressure
        "skin": 0.0,  # near-wellbore damage
        "completion_type": "open_hole",  # drilling/completion geometry
        "borehole_diameter_m": 0.2159,  # casing/hole size
        "tubing_od_m": 0.089,  # production tubing
        "tubing_md_m": 2000.0,  # measured depth to reservoir
        "available": True,  # availability for forecast
        "gas_lift_rate_m3d": 0.0,  # gas injection rate
        "reservoir_id": "<tank_id>",  # tank assignment (v31+)
        "_tank_linked": True  # internal flag (set by apply_tank_links)
    }
}
```

**IPR Models:**

| Model | Equation | Parameters | Notes |
|-------|----------|-----------|-------|
| PI | q = PI × (Pr − Pwf) | productivity_index_m3d_bar | Linear (early-time oil) |
| Vogel | q = qmax × [0.2(Pwf/Pr) + 0.8(Pwf/Pr)²] | none (solved from PI/Pr) | Standard pseudosteady-state oil |
| Gas | q_max = PI × (Pr² − Pwf²) / (2 × Pr) | ipr_model='Gas' | Gas backpressure (no solution gas) |

**VLP Correlations:**

| Correlation | Best For | Inputs | Notes |
|-------------|----------|--------|-------|
| Beggs-Brill | Oil wells | water_cut, gor, api, gas_sg, temp | Mechanistic, direction-sensitive, holdup correlation |
| Homogeneous | Gas wells | water_cut, temp | No-slip assumption, less sensitive to holdup jumps |

### Water Injector

```python
{
    "kind": "water_injector",
    "params": {
        "reservoir_pressure_bar": 250.0,  # injection pressure (from tank)
        "injectivity_index_m3d_bar": 20.0,  # reciprocal of PI (q = II × (Pwf − Pr)) |
        "max_injection_rate_m3d": 500.0,
        "available": True,
        "reservoir_id": "<tank_id>"  # assignment to tank
    }
}
```

Injectors take the tank's reservoir pressure. Injectivity index is the slope of a linear injection-pressure curve.

### Gas Injector

Similar to water injector, used for gas lift or gas injection. VLP defaults to "Homogeneous" (no-slip).

### Reservoir Tank (v31+)

```python
{
    "kind": "reservoir",
    "name": "Tank_Oil",
    "params": {
        "fluid_phase": "oil",  # or "gas", "gas_condensate"
        "reservoir_pressure_bar": 250.0,  # will be updated each forecast step
        "temperature_c": 90.0,
        
        # Oil phase
        "stoiip_sm3": 20e6,  # stock-tank oil in place
        "boi_rm3_sm3": 1.25,  # formation volume factor
        "rsi_sm3_sm3": 100.0,  # solution gas ratio
        "bubble_point_bar": 150.0,
        "swi": 0.2,  # initial water saturation
        "ct_1bar": 1.5e-4,  # compressibility
        
        # Gas phase (gas / gas_condensate)
        "giip_sm3": 5e9,  # gas initially in place
        "gas_sg": 0.7,  # gas specific gravity
        "cgr_sm3_per_msm3": 100.0,  # for gas_condensate only
        
        # Aquifer (optional)
        "aquifer_pi_m3d_bar": 0.0,
        "min_pressure_bar": 20.0,
        
        # Fluid evolution (oil only)
        "water_breakthrough_rf": 0.05,
        "max_water_cut": 0.9,
        "rf_at_max_water_cut": 0.40,
        "gor_rise_factor": 3.0
    }
}
```

Tanks are **not** boundary conditions; they are **reservoirs** that deplete from produced volumes. Each forecast step updates tank pressure via material balance.

### Separator / Sink / Export

```python
{
    "kind": "separator",  # or "sink", "export"
    "params": {
        "pressure_bar": 10.0,  # fixed outlet/boundary pressure
        "max_rate_m3d": 1000.0  # (optional) capacity limit
    }
}
```

- **Separator**: Liquid–gas split at a fixed pressure (typically 10 bar)
- **Sink**: Liquid or gas dump; sets a fixed boundary pressure
- **Export**: Gas export at a fixed pressure; capacity can be constrained

These are **boundary conditions** that anchor network pressure.

---

## Edge (Connection) Types

### Pipeline

```python
{
    "kind": "pipeline",
    "length_m": 1000.0,
    "diameter_m": 0.154,  # internal diameter
    "roughness_m": 4.5e-5,  # absolute roughness
    "elevation_change_m": 100.0,  # depth change (positive = uphill)
    "allow_parallel": False,  # allow multiple pipes between same nodes
    "params": {
        "temperature_c": 50.0,
        "water_cut": 0.2,
        "gor_sm3sm3": 100.0,
        "api": 35.0,  # oil density
        "gas_sg": 0.75,  # gas specific gravity
        "correlation": "Beggs-Brill"  # for multiphase friction
    }
}
```

**Pressure Drop Calculation:**

ΔP = ΔP_friction + ΔP_hydrostatic + ΔP_acceleration

- **Friction**: Beggs–Brill multiphase correlation (direction-sensitive)
- **Hydrostatic**: ρ × g × h (always positive for downhill)
- **Acceleration**: ρ × v² changes (usually small)

Pipelines are **segmented** (~1 per 1.5 km, up to 8 segments) to handle pressure/density changes accurately. Each segment uses predictor–corrector to stabilize VLP calculations in tubing.

### Choke

```python
{
    "kind": "choke",
    "length_m": 0.0,  # not used for chokes
    "params": {
        "cv": 80.0,  # flow capacity coefficient [bbl/d/√psi]
        "opening": 1.0  # valve opening fraction [0–1]
    }
}
```

**Choke Equation:**

Q = cv × opening × √(ΔP) × sign(ΔP)

The sign ensures **reverse flow is possible** (negative ΔP). Cv is typically 60–120 for production chokes.

### Control Valve

Same as choke; a general pressure-drop device.

### Pump

```python
{
    "kind": "pump",
    "length_m": 0.0,
    "params": {
        "shutoff_head_bar": 35.0,  # pump discharge pressure at zero rate
        "rated_rate_m3d": 1500.0,  # design rate
        "efficiency": 0.75,  # mechanical efficiency (not used in solver)
        "speed_fraction": 1.0,  # fraction of design speed [0–1]
        "min_head_bar": 0.0  # minimum head (optional; prevents flat curves)
    }
}
```

**Pump Curve (Affinity Law):**

H(q) = shutoff_head × (1 − (q / qmax)^n)  [simplified]

With affinity law: H(q, speed) = H(q/speed, 1) × speed²

Speed fraction allows ramping pumps from 0–100% design speed.

### Compressor

```python
{
    "kind": "compressor",
    "params": {
        "pressure_ratio": 1.8,  # discharge / inlet pressure
        "map_enabled": False,  # enable performance map
        "rated_gas_rate_sm3d": 150000.0,  # design rate
        "speed_fraction": 1.0,
        "max_discharge_bar": 250.0  # discharge limit
    }
}
```

Compressors use a **ratio-based model**: P_out = ratio × P_in. This is screening-level; detailed surge/choke maps are not supported in v31.

---

## Tank Material Balance

### Oil Tank Depletion

After solving the network at step k, the tank's produced and injected volumes are known:

```
Void = Np × Bo + Wp − Winj
```

where:
- Np = cumulative oil produced [Sm³]
- Wp = cumulative water produced [m³]
- Winj = cumulative water injected [m³]
- Bo = formation volume factor [rm³/Sm³]

Aquifer influx:

```
We = Jaq × (Pi − P) × dt
```

The solver then steps the pressure sub-iteratively (sub-steps = 10):

```
ΔP = (Void/sub − We) / (Pv × Ct_eff)
P = max(Pmin, min(Pi, P − ΔP))
```

where Ct_eff includes solution-gas-drive expansion below the bubble point.

### Gas Tank Depletion (p/z Material Balance)

For dry gas and gas condensate:

```
G × Bg_initial = (G − Gp + Ginj) × Bg + We + Winj − Wp
```

Solve for Bg iteratively, then P from the z-factor correlation.

### Aquifer

Schilthuis model: We = Jaq × (Pi − P) × dt

Integrated over each sub-step.

### Water Breakthrough & GOR Rise

**Water cut** rises as an S-curve in recovery factor:

```
RF = Np / N
x = min(max((RF − RF_bt) / (RF_wcmax − RF_bt), 0), 1)
smooth_x = x² × (3 − 2x)  # Hermite smoothing
Wc = Wc0 + (Wc_max − Wc0) × smooth_x
```

**GOR** rises when P < Pb:

```
GOR = GOR0 × (1 + rise_factor × max(Pb − P, 0) / Pb)
```

These are applied dynamically in each forecast step, not pre-computed.

---

## Well Performance Models

### IPR / VLP Intersection

At each solve step, the solver finds the pressure where IPR = VLP:

```
IPR(P):   q = f(P, Pr, PI, model)
VLP(P):   q = g(P, Pwh, D, correlation)
Solve:    Find P where IPR(P) = VLP(P)
```

The well is dead if no intersection exists at positive rate. The solver applies **GAP-style conventions**:
- Use the **highest-rate stable intersection** (not trickle rates < 5 m³/d)
- Apply minimum-rate shut-in rule post-solve

### Beggs–Brill VLP Correlation

Holdup (liquid volume fraction in tubing):

```
λ = (Np / (Np + Ng))^0.14  [with adjustments for direction]
```

Pressure gradient:

```
dP/dz = ρ_m × g + τ × ρ_m × v² / (2 × D)
```

where:
- ρ_m = holdup × ρ_oil + (1 − holdup) × ρ_gas
- τ = two-phase friction multiplier

v31 fixes:
- Uphill segregated coefficient: 3.539 (was 0.3692)
- Holdup blend at intermittent/distributed boundary (smooth transition)
- Friction factor blend at Re = 2000–4000 (smooth laminar–turbulent)

### Gas Backpressure IPR

For gas wells:

```
q_max = PI × (Pr² − Pwf²) / (2 × Pr)
```

where PI is productivity index [m³/d/bar²]. Wells on gas tanks automatically use backpressure IPR and Homogeneous VLP (no-slip).

---

## Solver API

### Main Entry Point

```python
from solver.v21 import solve

p, q, info, d = solve(
    nodes,      # list of node dicts
    edges,      # list of edge dicts
    warm_start_pressures=None,  # optional: {node_id: pressure}
    warm_start_flows=None,      # optional: {edge_id: flow}
    warm_start_rates=None,      # optional: {node_id: rate} for wells
    target_residual=1e-12,      # convergence threshold
    max_iterations=200,
    enforce_limits=True,        # apply capacity constraints
    debug=False
)
```

### Return Values

| Return | Type | Meaning |
|--------|------|---------|
| p | dict | {node_id: pressure_bar} |
| q | dict | {edge_id: flow_m3d} |
| info | dict | Solver metadata (see below) |
| d | dict | {node_id: well_details} (wells only) |

**info dict:**

```python
{
    "converged": True,  # True if residual < target
    "iterations": 42,
    "residual": 1e-13,  # final normalized residual
    "quality_gate": "PASS",  # or "REVIEW", "FAIL"
    "message": "Converged · 500 m³/d liquid",
    "debug": [  # array of diagnostic messages
        {
            "severity": "warning",
            "code": "STIFF_WELL",
            "message": "Well 'Producer_1' has high near-well PI: 200 m³/d/bar"
        }
    ]
}
```

**d (well details) dict:**

```python
{
    "<well_id>": {
        "liquid_rate_m3d": 150.0,
        "gas_rate_sm3d": 15000.0,
        "water_rate_m3d": 15.0,
        "bhp_bar": 95.0,  # bottom-hole flowing pressure
        "whp_bar": 20.0,  # wellhead pressure
        "shut_in": False,
        "reason": ""  # if shut_in, why (e.g., "No IPR/VLP intersection")
    }
}
```

### Warm-Start Optimization

Previous solve results are stored in `state['v21_warm_start']`:

```python
warm_start = state.get('v21_warm_start')
p, q, info, d = solve(
    ...,
    warm_start_pressures=warm_start.get('pressures'),
    warm_start_flows=warm_start.get('flows'),
    warm_start_rates=warm_start.get('well_rates')
)
```

Warm-start typically **reduces iterations from 34 to 5**, making forecasts ~7× faster (dogbox least-squares solver).

### Solver Internals

**Equation System:**

For each node, mass balance on liquid and gas. For each edge, pressure drop equation. For each well, IPR = VLP condition (algebraic).

**Solver:**

- SciPy least-squares with Jacobian options:
  - Sparse (>10 nodes): KLU sparse solver
  - Dense (<10 nodes): LU factorization
  
- Levenberg–Marquardt damping for robustness

- Per-component caching (PVT, correlations) to avoid recomputation

---

## Data Structures

### Forecast Step Record

```python
{
    "time_step": 1,
    "days_elapsed": 30.0,
    "pressure_bar": 225.0,  # tank pressure after depletion
    "liquid_rate_m3d": 450.0,  # total across all wells
    "gas_rate_sm3d": 45000.0,
    "water_rate_m3d": 50.0,
    "cum_oil_sm3": 13500.0,  # cumulative
    "cum_gas_sm3": 1.35e6,
    "cum_water_m3": 1500.0,
    "water_cut": 0.10,
    "gor_sm3sm3": 100.0,
    "wells": {
        "<well_id>": {
            "liquid_rate_m3d": 150.0,
            "gas_rate_sm3d": 15000.0,
            "water_rate_m3d": 5.0,
            "bhp_bar": 95.0,
            "status": "flowing"  # or "shut_in"
        }
    }
}
```

### Scenario Record

```python
{
    "name": "Base Case",
    "producer_count": 3,
    "pi_multiplier": 1.0,
    "separator_pressure_bar": 10.0,
    "injection_active": True,
    "kpis": {
        "peak_liquid_m3d": 500.0,
        "plateau_liquid_m3d": 400.0,
        "cum_oil_sm3": 50e6,
        "recovery_factor_pct": 25.0,
        "final_water_cut_pct": 60.0
    },
    "forecast": [  # array of forecast step records
        { ... }
    ]
}
```

---

## Import/Export Formats

### CSV / Network Interchange

Export a network to CSV (one row per node/edge) and re-import:

```bash
# Exported as CSV tables:
# - nodes.csv: id, kind, name, x, y, [param columns]
# - edges.csv: id, source, target, kind, length_m, diameter_m, [param columns]
```

**Normalization on import:**
- NaN/Inf values are rejected with clear error message
- Missing fields get palette defaults
- Capacity and lift keys are converted from field units (psi, stb/d, etc.)

### JSON Project Export

Full project snapshot:

```json
{
    "version": "FieldNet v31",
    "timestamp": "2024-09-30T15:30:00Z",
    "nodes": [ ... ],
    "edges": [ ... ],
    "tanks": [ ... ],
    "forecast_results": [ ... ],
    "scenarios": [ ... ],
    "manifest": {
        "sha256": "<hash>",
        "fields": 1,
        "wells": 3,
        "forecast_steps": 60
    }
}
```

Run-manifest SHA-256 is verified on import; corrupted exports are rejected.

### CSV Forecast Export

Forecast results as timestep rows:

```
time_step,days_elapsed,pressure_bar,liquid_rate_m3d,gas_rate_sm3d,...
1,30,225.0,450,45000,...
2,60,220.0,440,44000,...
```

Suitable for import into Excel, Python, R for post-processing.

---

## Example: Programmatic Network Creation

```python
from ui.graph_contract import new_edge, normalize_graph, solver_input, run_solve
from solver.v21 import solve

# Define nodes
nodes = [
    {
        "id": "tank_1",
        "kind": "reservoir",
        "name": "Oil Tank",
        "params": {
            "fluid_phase": "oil",
            "reservoir_pressure_bar": 250.0,
            "stoiip_sm3": 20e6,
            "boi_rm3_sm3": 1.25
        }
    },
    {
        "id": "prod_1",
        "kind": "well",
        "name": "Producer 1",
        "params": {
            "productivity_index_m3d_bar": 50.0,
            "ipr_model": "Vogel",
            "reservoir_id": "tank_1"
        }
    },
    {
        "id": "sep_1",
        "kind": "separator",
        "params": {"pressure_bar": 10.0}
    }
]

# Define edges
edges = [
    new_edge("prod_1", "sep_1", kind="pipeline"),
]

# Normalize and solve
nodes_norm, edges_norm, issues = normalize_graph(nodes, edges)
p, q, info, d = solve(nodes_norm, edges_norm)

# Print results
print(f"Producer rate: {d['prod_1']['liquid_rate_m3d']:.1f} m³/d")
print(f"Separator pressure: {p['sep_1']:.1f} bar")
```

---

See [USER_GUIDE.md](USER_GUIDE.md) for interactive workflows and [EXAMPLES.md](EXAMPLES.md) for real use cases.
