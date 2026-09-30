# FieldNet v30 — Audit & Bug-fix Release

Audit of FieldNet v29 (Streamlit GAP-style production network solver). All findings below were reproduced by running the code (unit tests, a headless run of every app tab, and a solver stress network) and are covered by `tests/test_v30_audit.py`.

## Verification summary

| Check | v29 (as received) | v30 |
|---|---|---|
| Test suite | 179 pass (4 files could not import) | **228 pass**, 0 fail (incl. 25 new regression tests) |
| Headless run of all 17 tabs | runs, but several silent failures (below) | all tabs run, no errors |
| Demo solve (kernel) | converged to a non-solution on its own (residual 0.51); v21 retries rescued it | converges in ~0.15 s, residual < 1e-12 |
| Back-pressure sweep 20→80 bar | stalls up to 3000 evaluations / 44 s near well death | every case PASS in < 0.5 s |
| 5-year monthly forecast | 36 s | 5 s |
| 3-scenario development run | 110 s | 16 s |

## Critical bugs (app "not working")

1. **Canvas overwrote the model on every rerun.** Streamlit replays a component's *last* value on every rerun. The app compared that stale value with the case and overwrote it, so every property-panel edit was reverted on the next click, and components added from the sidebar disappeared as soon as the canvas had been touched once. *Fix:* the canvas now sends a unique revision; Python applies a canvas value only when the revision is new. Clicking a node/edge now also sends the selection (before, selection only arrived with the next drag).
2. **Stale widgets overwrote imports and external changes.** Keyed Streamlit widgets ignore `value=` after the first render, and their values were written back into the model every run. After *Load project*, calibration, or a bulk-table edit, the selected component was silently reset to the old widget values. *Fix:* `ui/widgets.py` synced widgets re-seed themselves whenever the model changed elsewhere.
3. **Single-segment tubing (VLP) model.** The whole 2,000–2,500 m string was evaluated at one average pressure, so gas liberation near the wellhead was ignored. *Fix:* the model now steps down the tubing in segments, with a predictor–corrector on each segment.
4. **Well equation solved by a nested optimiser inside every residual.** It was slow, restarted from the same guess on every call, stopped early (leaving ~0.1 m³/d of noise in the finite-difference Jacobian), and had no notion of shut-in, rate caps or artificial lift. *Fix:* well rates are now unknowns. Each well obeys one condition: it flows at the IPR/VLP intersection, is dead at zero, or is choked at its rate limit. This is the condition GAP-style solvers impose on wells.
5. **Network ignored skin, gas lift, ESP and the selected VLP model.** The UI wrote `vlp_model` but the solver read `correlation`, and skin and lift only affected the nodal tab. *Fix:* the network, the nodal tab and the audit now share one well model (`physics/well_model.py`).
6. **Sink or separator without a pressure crashed the solver** with a bare `KeyError`, and the topology precheck wrongly counted such a sink as a pressure anchor. *Fix:* it is now a clear `BOUNDARY_WITHOUT_PRESSURE` error.
7. **Every control valve was reported as a topology error**, because `control_valve` was missing from the valid edge types. Most palette node types were also flagged as "unknown".
8. **Model QA never ran its post-solve checks.** The tab expected a dict, but results are stored as a tuple.
9. **Water injectors could not take water.** An injector node had no sink term, so its flow was forced to zero. *Fix:* added an injectivity-index injector model, and edges fed only by a water source now carry water automatically.

## Physics & numerics

- **Beggs–Brill inclination coefficients were wrong.** The uphill segregated coefficient was 0.3692 (a downhill value) instead of 3.539. The liquid velocity number was replaced by viscosity × 1000, downhill holdup was clamped to at least the no-slip value, and the two-phase friction multiplier exp(S) was missing. All corrected.
- **Holdup jumped at the intermittent/distributed boundary.** That left some wells with no exact IPR/VLP root, so the solve stalled. *Fix:* holdup is blended across a narrow band.
- **Friction factor jumped** from 0.028 to 0.049 at Re = 2300. *Fix:* laminar and turbulent values are now blended between Re 2000 and 4000.
- **Pipeline Δp was discontinuous at zero flow.** Flowlines behind dead wells park there, which caused thousands of solver iterations. *Fix:* Δp is bridged linearly for |q| < 0.5 m³/d.
- **Chokes and control valves gave a positive Δp for reverse flow.** They are now signed.
- **Pump head was clipped at zero above run-out rate.** The equation went flat and the solver stalled. The curve now keeps falling unless `min_head_bar` is set.
- **Pipelines are marched in segments** (about one per 1.5 km, up to 8).
- **Wrong-branch solutions.** The solver could settle on a low-rate or unstable intersection, or on a trickle rate below 5 m³/d. It now applies the GAP convention: each well uses its highest-rate stable intersection, and wells below `min_rate_m3d` (default 5) are reported as shut in.
- **Better starting guesses.** Pressures now start from the nearest boundary instead of a fixed 80–90 bar. That old value started wells above the separator pressure, where they are dead. A sparse Jacobian and per-component caching make each solve 5–10× faster.
- **Unavailable Vogel wells kept flowing.** The shut-in hack only zeroed the PI, not q_max.
- **v11 coupled forecast had a units error.** It compared facility capacity [m³/d] with the sum of well PIs [m³/d/bar].

## Constraints (GAP-like behaviour)

- Well `max_liquid_rate_m3d` is enforced directly in the well equation.
- Separator/export liquid capacities and connection `max_rate_m3d` can be enforced by **pro-rata choking of the upstream wells**. The toggle is on the Network tab and in the forecast (on by default in the UI; the API defaults to report-only).
- The debottleneck screen used to re-solve without enforcing limits, so every +10 % capacity showed exactly zero gain. It now re-solves with limits enforced.

## UI / data robustness

- **Blank data-editor cells arrive as NaN, and `NaN or default` is still NaN.** Event schedules, Monte Carlo parameters, development tasks, tanks, aquifers and reliability specs could crash or silently disable bounds, and a `resource`/`field` of `'nan'` could be written into the model. All tables are now cleaned explicitly.
- **Calibration offered fixed boundary pressures as observations.** A fixed pressure is an input, so those observations had zero sensitivity. The tab now lists the free pressures (wellheads, manifolds), pre-fills them with the current solution, and adds an *Apply calibrated parameters* button.
- **The Nodal tab defaulted to WHP = 90 bar**, which showed "no intersection" on the demo. It now defaults to the solved WHP. The skin model is consistent (J = J₀·C/(C+S), with C = 7 by default and configurable via `skin_reference_factor`), and gas lift adds injection gas to the tubing flow instead of subtracting an arbitrary pressure "assist".
- **Tabs used stale results after topology edits.** They now use `solved()`, and results are cleared when the topology changes. Keyed selectboxes survive their option disappearing.
- The property panel now edits choke/valve Cv and opening, pump curve, compressor ratio, separator capacity, well cap, availability, injector parameters and pipeline elevation.
- **Unconnected (just-added) components** are excluded from the solve with a warning, instead of making the whole network fail.
- **CSV/package interchange rejected any choke, pump or valve** because it required `length_m > 0` for every edge. Capacity, lift and temperature keys were also left unconverted in field units.
- **JSON exports could fail** on numpy integers coming from data editors.

## Known limitations (not changed)

- PVT is a screening black-oil model with a fixed bubble point of 150 bar and Rsb = 120 Sm³/Sm³, regardless of the producing GOR. Replace it with tuned PVT for real studies.
- Gas and gas-injection networks use the liquid-rate formulation (screening only). Compressors are ratio-based.
- The optimizer treats gas-lift injection as an input, not an allocation variable.
- Minimum-rate shut-in is a rule applied after the solve, not a mixed-integer decision.

## Files changed (main)

`physics/well_model.py` (new), `solver/equations.py` (new), `ui/widgets.py` (new), `solver/steady_state.py` (rewritten), `solver/physical_audit.py`, `solver/v21.py`, `physics/vlp.py`, `physics/beggs_brill.py`, `physics/multiphase.py`, `physics/hydraulics.py`, `physics/choke.py`, `physics/controls.py`, `physics/well_performance_v20.py`, `network/forecast.py`, `network/coupled_forecast*.py`, `network/interchange_v27.py`, `physics/unit_system.py`, `optimization/debottleneck.py`, `solver/constraints.py`, `ui/*`, `app.py`, `ui/fieldnet_canvas/build/index.html`, `tests/test_v30_audit.py` (new).
