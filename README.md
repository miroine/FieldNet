# FieldNet v29 — Development Planning

Production-only integrated network/forecast/uncertainty simulator with v19 flow-assurance screening. Economics is intentionally excluded. See `FLOW_ASSURANCE_V19.md` and `UNIT_SYSTEMS.md`.

# FieldNet v17.1 — Uncertainty, Monte Carlo & Development Optimization

## v17.1 audit additions
- Physical sample bounds with explicit clip/reject policy.
- Stronger correlation validation (finite values, coefficient range, PSD).
- Failed-realization diagnostics and survivor-bias warning.
- P10/P50/P90 convergence diagnostics by realization count.
- Spearman rank sensitivity ranking for forecast outcomes.
- Reproducibility metadata retained in Monte Carlo JSON exports.
- Current release identity propagated across audit and development exports.


FieldNet v17 extends the audited v16.1 deterministic field-development workflow with reproducible planning-level uncertainty analysis. It adds seeded Latin-hypercube/random Monte Carlo sampling, optional rank-style Gaussian correlation, explicit parameter overrides, petroleum-style P90/P50/P10 production metrics, realization exports, and bounded development-decision optimization hooks. Deterministic hydraulics and forecast physics remain those of the v16.1 baseline.

**Made by Merouane Hamdani — For non-commercial use — Independent engineering prototype.**

# FieldNet v16.1 — Field Development Audit & Patch Release

**Made by Merouane Hamdani — For non-commercial use — Independent engineering prototype.**

FieldNet v16.1 carries forward the validated professional network solver, advanced physics, and field-development forecasting layers, with an audit/patch focus on schedule integrity, cumulative accounting, scenario isolation, and export/audit consistency.

## v14 highlights
- Professional solve wrapper retaining the v13 physics residual kernel for compatibility.
- Residual quality gate and normalized residual score.
- Pressure/rate scaling metadata and continuation history.
- Hard/soft constraint classification, priority and penalty utilities.
- Active/near-active constraint reporting.
- +capacity debottleneck screening to estimate incremental production response.
- Multi-scenario runner for case comparisons.
- Calculation audit JSON: solver/model identity, convergence, residuals, constraints and warnings.
- Existing v13 advanced wells, artificial lift, performance maps, themes, forecasting and network editor retained.

## Engineering status
FieldNet remains an independent engineering prototype. Beggs–Brill/PVT/reservoir/equipment implementations include screening-level elements and must be independently validated for the intended operating envelope before operational decisions.

## Run
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Tests
```bash
pytest -q
```

## v15 advanced physics release

Adds completion-interval radial productivity, rate-dependent non-Darcy skin, detailed multiphase pressure decomposition/profiles, multi-speed pump/compressor maps, surge/choke/power and pump NPSH envelope diagnostics. Existing v14.1 solver and physics APIs remain available for backward compatibility. The new models are engineering/planning-level and should be calibrated before operational use.


## v15.1 engineering audit / patch release

- Makes 2-D pump/compressor map out-of-envelope behavior explicit instead of silently presenting clamped values as normal map interpolation.
- Reports map status, extrapolation reasons, requested speed, evaluated/clamped speed, and separate speed/flow envelope flags.
- Validates that every speed line contains at least two map points.
- Adds limiting-case validation for completion skin/non-Darcy behavior, multiphase pressure-component closure, equipment-map boundaries, and audit identity.
- No new physics claims are introduced; this is a validation and diagnostics release.


## v16 — Field Development & Integrated Forecasting

- Named development scenarios with independent immutable execution.
- Dated well/facility/network events using the existing project object IDs.
- Exact final forecast interval clipping and cumulative oil/water/gas accounting.
- Constraint/convergence KPI summaries and scenario comparison dashboard.
- Streamlit Field Development tab with timestep CSV and scenario-summary JSON exports.
- Planning/screening workflow only; not a transient reservoir simulator or reserves certification tool.


## v16.1 — Field Development audit / patch release

- Audited exact final-interval clipping and cumulative oil/water/gas accounting.
- Development schedules now fail fast when an event references an unknown node/edge ID instead of silently ignoring the typo.
- Calculation-audit and field-development export identities updated consistently to FieldNet v16.1.
- Added export/accounting/scenario-isolation and event-boundary regression checks.
- No new reservoir or hydraulic physics claims; this is an audit/validation release.

## v17.2 — Unit systems and conversion hardening
FieldNet v17.2 adds an explicit engineering-unit boundary with **Norwegian SI** and **Field** profiles while retaining canonical internal calculation units. Norwegian SI uses bar, °C, m/mm, Sm³/d, Sm³, kg/m³ and kW; Field uses psi, °F, ft/in, stb/d, Mscf/d, stb/MMscf, lb/ft³ and hp. Standard-volume reporting is defined at 15 °C and 1.01325 bara. `Sm³` standard volume is not treated as flowing `m³`; stock-tank liquid and standard-gas conversions are independent. PVT pressure is absolute; gauge/absolute conversion helpers are explicit.

## v20 well performance
v20 adds enhanced nodal analysis, selectable Beggs-Brill/homogeneous VLP, gas-lift screening/optimization, generic ESP affinity-law performance/envelopes, and well QA. See `WELL_PERFORMANCE_V20.md`. Economics remains excluded.


## v24 Reliability & Availability
See `RELIABILITY_V24.md`. Adds seeded failure/repair Monte Carlo, planned outages, redundancy, availability percentiles and deferred-production screening. No economics.

## v25
Adds communicating reservoir tanks, pressure-dependent aquifer support, injector–tank connectivity, auditable voidage accounting, exact horizon clipping, and a dedicated Reservoir coupling workspace. No economics.


## v26
Development Planning adds dependency/resource-constrained task scheduling, drilling/workover rig serialization, tieback/commissioning/facility/compression/shutdown/abandonment events, Gantt visualization, and production-forecast consequences. Economics remain excluded. See `DEVELOPMENT_PLANNING_V26.md`.


## v28 — Engineering QA & Model Assurance
Adds a read-only consolidated model-quality gate across topology, units/reference conditions, well and pipeline plausibility, solver convergence, physical residual closure, operating constraints and forecast sanity. Results are classified PASS / REVIEW / FAIL and exported as JSON. The checker never silently changes engineering inputs.


## v29 Scenario Management
Immutable content-addressed snapshots, branching/lineage, assumption registers, structural diffs, comparison tables, QA capture, reproducible run manifests, and verified scenario archives. See `SCENARIO_MANAGEMENT_V29.md`.
