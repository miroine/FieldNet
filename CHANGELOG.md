# FieldNet Changelog

Version history and major releases. See [AUDIT_V30.md](AUDIT_V30.md) for detailed v30 findings.

## v32.6 — Templates & examples library

**Release Date:** 2026-10-04 - see TEMPLATES_V32_6.md

### Round 7 — one palette item per family, Darcy inflow, schedule everything, one pressure input, consistency audit
- **Palette:** a single **Tank**, **Well** and **Separator** item. Role / phase / type (oil producer, gas producer, water or gas injector; two-phase, three-phase, test, scrubber, water treatment; separator stage) is chosen in the element's settings, like the tank phase.
- **Darcy inflow for wells** (`physics/darcy_ipr.py`, switch "Compute the inflow from reservoir properties"): vertical (pseudo-steady or steady state), deviated (Cinco-Ley skin, up to 75°) and horizontal (Joshi with kv/kh anisotropy); gas wells use the p² form with µ and z from the correlations; several commingled layers add. Direct PI / C / qmax inputs are ignored while Darcy is on. *Fixed during testing:* kv/kh had no effect on horizontal/deviated wells (an absolute floor was applied to permeabilities in m²).
- **Schedule:** every parameter listed in `network/param_registry.py` (about 70, 120 event types) can be changed by a dated event, in the user's display units (temperature and GOR added); an *Element type* filter keeps the list usable. *Fixed:* tank parameter events (aquifer, abandonment pressure, recovery-factor targets, sweep, water cut, GOR rise, gas cap) were not reaching the tanks during a forecast (`Tank.update_params`).
- **One pressure input:** reservoir pressure is entered on the tank only. Wells and injectors that drain a tank show the tank pressure; the field only appears when no tank is assigned. Injectors got a tank selector. Tables show the Darcy-derived PI.
- **Consistency audit:** `network/input_check.py` (Tools → Model checks → *Input & unit consistency*) checks ranges from the registry, names likely unit slips (mm/inch, psi, %, °F), pressure conflicts, broken tank links, Darcy warnings and scheduled events. Unit conversions: round-trip tests for every `*_to_display` / `*_from_display` pair in both profiles and constant checks.

### Development schedule: pause / parallel / speed
- **Pause / Continue / Stop are back for the drilling-schedule run.** The drilling run was synchronous and bypassed the run controller; it is now a generator (`ui.drilling_plan.iter_drill`) driven by the same `RunController` as the plain forecast. The progress bar spans both phases (scheduled plan, then the all-wells-at-start comparison); stopping keeps the partial scheduled-plan snapshot.
- **The Compute → worker setting is now honoured by the Development schedule / forecast.** (a) Independent connected systems inside each timestep are solved in separate processes (`solve_step(..., workers)`); (b) the "all wells at start" comparison runs in a second process alongside the plan when workers ≥ 2 (not with the field optimiser, whose solver cannot be sent to another process). A single connected network cannot be split across cores, so a one-platform field gets no gain from workers.
- **Run speed** selector (Accurate 12 / Balanced 6 / Fast 4 tubing segments). Well VLP is ~80 % of the run time and grows with the number of wells; on a 30-well test field Balanced is ~1.8x faster with oil rate within ~0.3 %. Explicit per-well `vlp_segments` are kept.
- Tests: `tests/test_run_control_drill.py`.

### Solver robustness: capacity-limited solves (gas condensate / compressor reports)
Stress-swept all 18 examples (well productivity x1/x20/x100, facility limits 100 % / 10 %, forecast solver and Network-tab solver).
- **A stalled solve is no longer reported as a solution.** `success` now also requires a residual <= 1e-4 (the raw solver flag is kept as `scipy_success`). Before, a x20 gas-condensate solve exited with residual 54 and was shown as converged.
- **Recovery path (`solve_network_robust`)**: short first attempt, then cold start (a warm start from a very different operating point was the cause of the compressor-example stall), then **rate continuation** (wells held to a fraction of natural rate, limit relaxed in x2.5 steps), then the full-budget solve; 20 s wall-clock budget. Used by the forecast, the Network-tab solver, the optimiser and scenarios. Gas-condensate x5/x20/x100: 12-16 s -> 0.4-2.5 s.
- **Capacity enforcement** no longer repeats the same choke 8 times and stops with an explanatory message if the choked network does not converge.
- **HPHT gas crash fixed**: oil Bo underflowed to zero at extreme trial pressures (ZeroDivisionError for high productivity). Exponent and density guarded.
- **Template compressors** now use the 5e5 gas convention of their wells (compressor power was understated 5x).
- Known limits: compressor / booster-pump examples with a facility limit ~10 % of natural rate, or x20+ productivity, can still fail to converge; they now say so (`recovery` / message) instead of reporting success.
- Tests: `tests/test_solver_recovery.py`.

### Round 6: blank page, apply-based editing, mask, tank phases, Eclipse binary reader, simpler tabs
- **Start from a blank page** (sidebar and Network tab, with a confirmation step): empties the layout; an empty-layout hint explains how to start. Loading a template, case or project also resets the canvas.
- **No more hanging while editing.**
  - *Canvas*: creating, moving, connecting, deleting and resizing are staged in the browser (no server round trip). An **Apply** button in the canvas toolbar turns orange with the number of pending changes and green once applied. Selection is still immediate. If Python re-renders while edits are staged, the staged layout is kept and the panel parameters are taken over.
  - *Property panel*: now a Streamlit fragment, so changing a value reruns only the panel; an **Apply changes** bar (orange when the model differs from the drawn canvas) redraws everything.
  - *Tables* (Constraints, Batch editor, Uptime, Flowlines) are forms: typing in a cell sends nothing until **Apply**. Apply buttons are orange; confirmations are coloured notices (green applied / orange pending).
- **Mask** (like GAP): toolbar button and checkbox in the panel for any component or connection. A masked element stays on the layout (greyed) but is ignored by the solver, forecast, drainage study and optimiser; producers cut off by a mask (or draining a masked tank) are masked with it. Batch column `masked`.
- **Tank**: the palette item is simply *Tank*; the fluid is chosen on it: Oil, **Oil with gas cap** (new, size `m`), Dry gas, Gas condensate. The gas cap expands with pressure (cg ≈ 1/p) and props up the tank pressure (`gas_cap_m` in `network/reservoir_mb.py`; free-gas production from the cap is not tracked separately).
- **Eclipse binary results** (`network/eclipse_io.py`, Reservoir & wells → Prediction source): reads `.SMSPEC` + `.UNSMRY` (or per-step `.S000n`) and `.UNRST` (average pressure), both endians, METRIC or FIELD units converted to bar / Sm³. Pick pressure, water-cut and GOR vectors for a tank's external table, or take WOPR / WWPR / WGPR / WBHP as the simulator well rates.
- **Tabs simplified**: 8 → 6 (Calibration moved under Reservoir & wells; Monte Carlo and Reliability under Prognosis); the empty *Availability & downtime* tab is gone (uptime is edited in Network → Uptime); *Scenarios & well count* → *Drainage strategy & well count*.
- Tests: `tests/test_round6.py` (19), real-browser tests `tests/browser/run_editor_round6_test.py` (staged edits, Apply colours, rebase, mask, epoch reset, tank label) and the two existing browser tests updated for staged edits. The fake-Streamlit harness gained `fragment` and `form_submit_button`.
- Known limits: the live Streamlit UI was still not run (not installable here); fragment/form behaviour is covered by the fake harness and the real-browser canvas tests only.

### Round 5: drainage strategy, history match, stable-branch choking
- **Drainage strategy study** (`network/drainage.py`, Development tab): sweeps the number of wells under the facility limits; reports plateau rate and length, recovery factor, binding constraint, optional NPV, a recommended well count with reason (marginal-gain rule or NPV max, raised to meet a plateau target, or flagged unreachable) and a printable HTML page.
- **Tank history match** (`network/history_match.py`, material balance view): bounded least-squares fit of in-place volume, aquifer productivity and compressibility to measured pressures, profile-likelihood 95 % ranges, identifiability warnings, quality grade and an *Apply to tank* button.
- **Solver**: capacity enforcement (`solver/v21.py`) now detects when a choked solve falls onto the dead (flowline-loaded) branch, bisects back to the lowest stable choke and reports the violation instead of a collapsed solution.
- **Manual UI checklist** `MANUAL_UI_CHECKLIST_V32_6.md` (Streamlit is not installable in the build sandbox; live UI untested).
- Tests: `tests/test_round5.py` (12).

### Round 4: file import, batch editing, tables in the Network tab, minimum rates, symbol size, phase colours
- **Data tables in the Network tab** (below the layout): *Batch editor*, *Constraints*, *Uptime* and *Import / export* — every input is edited in one place. The Constraints (Results) and Availability & downtime tabs now point there.
- **Batch editor** (`network/batch_io.py`, `ui/batch_view.py`): one table per group (wells, injectors, tanks, facilities, equipment, manifolds & joints, flowlines), one column per scalar parameter, **Apply changes** writes only the cells you edited; clearing a cell removes the parameter. "Add column" creates any parameter (catalogue or typed key) for the whole group. Nested inputs (trajectory, relperm …) are never touched.
- **Import input data from a file**: JSON, YAML, Excel (.xlsx, one sheet per group or one sheet for everything) or CSV. A project file replaces the model; any other file is merged by ID (or unique name), writing only filled cells, with a report of unmatched rows and ignored columns. **Export** all inputs as Excel (re-importable), CSV per group, YAML or JSON. `pyyaml` added to requirements.
- **Minimum stable rate per phase** (`min_oil_rate_m3d`, `min_water_rate_m3d`, `min_gas_rate_sm3d`, `min_liquid_rate_m3d`): converted to an equivalent liquid rate at the current water cut / GOR; the well shuts in ("below min rate") when it cannot hold the highest. Editable in the well panel, the batch editor and by file.
- **Symbol size**: per element `scale` (0.4–3×) — slider in the property panel, **Size − / Size +** buttons on the canvas for the selected symbol, batch column; ports, links, fit and SVG export follow the size.
- **Phase colours**: tanks and wells are filled by phase — gas red, oil green, water blue (water injectors / sources blue); a blue band at the bottom shows water cut (wells) or Swi (tanks). The same colours are used in the forecast, yearly and element charts (`charts.series_color`); red is reserved for gas, so the categorical palette no longer contains it.
- Tests: `tests/test_v326_round4.py`. Not exercised in a live Streamlit session (test harness and a headless-browser canvas screenshot only).

### Forecast assumptions: recovery factor per tank, rates per well (calibration)
- **New "Recovery & rate assumptions" panel on the forecast tab** (`ui/assumptions_view.py`, logic in `network/assumptions.py`, `network/calibration.py`). Stored as node params, so they travel with the case; with nothing set the forecast is unchanged.
- **Target recovery factor per tank** (fraction of the primary phase in place: oil → STOIIP, gas / condensate → GIIP). Recoverable volume R = RF × in place; the tank offtake is capped at (R − cumulative) / τ (τ = taper days, default 365), so production tapers exponentially to the target instead of stopping abruptly, and never overshoots. Wells share the cap pro rata to their network-solved rates and the network is re-solved with the resulting well caps. If the choked rate cannot be held (a flowline that loads up at low rate leaves the wells dead), the cap is delivered as a producing-time fraction instead (reported as deferral). The recovery table gains Target RF / Primary RF / status ("target reached" or "below target (x % short)" when the physics, not the assumption, limits recovery).
- **Gas p/z helper**: the target RF gives the implied abandonment pressure ((p/z)ab = (p/z)i (1 − RF)); an option writes it to the tank's minimum pressure.
- **Per-well rate assumptions**: hard maximum rate (primary phase: oil Sm³/d, gas MSm³/d), **calibrate to rate** and **EUR cap** (same taper). Calibration solves a productivity multiplier (applied to PI / Vogel qmax / gas C, `productivity_multiplier`) jointly on the network at start-up conditions, with the wells' own rate limits lifted; it reports wells it cannot match (network-limited, or the target falls in an unstable line-loading region) instead of forcing them.
- Tests: `tests/test_assumptions.py`. Not exercised in a live Streamlit session (test harness only).

### Audit fixes (v32.6)
- **Tank gas z-factor** now uses the same Dranchuk–Abou-Kassem correlation as the wells (`physics.pvt_model.gas_z`). The earlier Papay form over-predicted z at high pressure (1.28–1.42 vs ~1.12 at 450 bar), which distorted HPHT gas-tank pressure, Bg and the p/z abandonment helper.
- RF / EUR taper caps now account for well availability (delivered rate = cap × uptime).
- Checked and found correct: volumetric gas p/z line, oil compressibility depletion, Bg at standard conditions; no undefined names or syntax errors in the source tree.

### Round 3: shapes, completion units, solver reset, CGR
- **GAP-style component symbols** on the canvas and in the SVG export (`ui/shapes.py`, mirrored in the canvas; a test keeps them in sync): tank = cylinder, well / injectors = oval (injectors dashed), separator = horizontal vessel with end caps, manifold = hexagon, outlets point right, sources point in, compressor = trapezoid, pump = oval, choke / valve = octagon. Well cards show gas / oil from the IPR model.
- **Completion table units**: an *ID unit* selector (inch / mm / m, default inch) above the table; values are stored in metres. Columns are numeric even when the table is empty (an empty frame used to be all-text), the editor key no longer changes on every keystroke, and an ID above 1 m is flagged ("0.0889 m = 3.5 in").
- **Failed solve no longer poisons the next one.** The warm start is stored only from a converged solve (a failed state used to be kept and reused after the user corrected the data); a warm-started solve that fails is retried from scratch; a **Reset solver & retry** button appears when the solve has failed.
- **Flowline fluid follows the wells.** Line `gor_sm3sm3`, `water_cut`, `api`, `gas_sg` (pipelines and compressors) are now taken from the blended fluid of the wells feeding them, estimated before the first solve and refined after it (max 3 passes, `network/fluid_blend`). Before, raising a tank's CGR changed the wells but not the lines, which then carried e.g. 16x too much gas: the gas rate collapsed and steps stopped converging. Gas condensate converges for CGR 60-2000; a step fell from ~14 s to 0.4 s. Opt a line out with `params.follow_wells = false`; `info.fluid_follow` lists the values used, and the quality-gate audit and thermal pass use them.
- Tests: `tests/test_v326_round3.py`.

### New
- **18 loadable templates** (`network/templates.py`, `ui/templates_view.py`; Cases & Data → Templates & examples): HPHT 4-slot subsea gas template, daisy chain, several communicating tanks with dual-zone wells, oil with gas injection, pure depletion, wellhead platform tied back to a host, subsea booster pump, subsea and topside compressors, horizontal wells, HPHT tight gas with fractured wells, gas lift, ESP, waterflood, gas-condensate tieback, onshore gas gathering, plus two learning examples. Each has a description, what to watch and suggested forecast settings; load into the editor or as a new case.
- Forecast tab picks up the template's suggested start / horizon / step.

### Changed (one Development schedule tab, line thickness)
- **Production forecast and Development schedule are one tab**, "Development schedule" (Prognosis). Same start / horizon / step / capacities / events; a toggle **Phase wells in with a drilling schedule** adds the rig table (order, days, rigs, not-before dates). Off = every well on stream at start (the old production forecast); on = wells come on stream when drilled, with the Gantt, first production, and a comparison with all wells at start (gas when gas is the primary phase). The result feeds all other tabs (yearly profiles, groups, data hub, exports) either way.
- **Line thickness follows flow**: on the network canvas (checkbox next to Show on network) and on the network-at-a-date diagram under the date slider (scaled to the largest flow over all dates, so lines visibly grow and thin as you move the slider). Width ~ sqrt(flow). The date diagram also gets the Show-on-network selector and primary-phase labels. Arrowheads no longer scale with line width.

### New (availability & downtime)
- **Availability & downtime** page (Prognosis tab; `network/availability.py`, `ui/availability_view.py`): uptime %, MTBF / MTTR and planned downtime per year for any well, compressor, pump, separator / host, manifold, injector, choke or flowline, in one editable register; one click fills typical values (wells 95 %, compressors 94 %, pumps 95 %, separators 97 %, lines 99.5 %).
- The forecast delivers only what is up all the way to the sink (series multiply, parallel trains share flow by solved flow); injectors are limited by the uptime of their source and upstream equipment. Downtime defers production: tanks deplete only by what is produced. Forecast rows gain `Uptime [%]`, `Oil deferred [m3/d]`, `Gas deferred [Sm3/d]` (field) and `Uptime [%]` (wells); they flow into the data hub and exports. Expected-value screening, no random clustering (use Tools -> Reliability), no catch-up, no take-over by a surviving parallel train beyond flow sharing.

### Changed (primary phase)
- **Primary phase** setting (sidebar → Display: Auto / Oil / Gas; `network/phase_pref.py`). Auto follows the model (solve, then forecast, then tank fluid phases). For a gas field: nodal plots (main, what-if, Monte-Carlo, matching, blowout) use gas rate in MSm³/d on the x axis, nodal cards lead with gas rate, forecast KPIs show peak gas / plateau / cumulative gas / gas RF, forecast charts lead with gas rate, cumulative gas, condensate-gas ratio and gas by well, summary cards, results-by-date cards, yearly profiles and groups default to gas, and network labels follow.
- Forecast KPIs gained `peak_gas_sm3d`, `plateau_gas_years`, `final_gas_sm3d`, `rf_gas_pct`.

### Changed (network display)
- **Show on network** selector above the canvas (`network/net_display.py`): Auto (oil field: liquid rate & water cut; gas field: gas rate in MSm³/d), oil / gas / water rate, all three, pressure, BHP, GOR, line ΔP & velocity, erosional ratio, or names only. Applies to components and to flowline labels (new `edge_labels` argument of the canvas) and to the SVG export.
- Results → Summary: **Browse a parameter** (nodes or lines; pressure, rates, BHP, ΔP, velocity, erosional ratio; gas in MSm³/d) with a sorted bar chart and table.
- Gas templates: flowline GOR set to 5e5 (the engine's gas-well convention: gas = 5e5 x liquid-equivalent), pipe sizes raised so erosional ratios are about 1; HPHT export line 24 in. Previously the line pressure drop was under-stated for gas lines whose GOR did not match the wells.

### Fixed
- Streamlit Cloud crash `ModuleNotFoundError: openpyxl` on the Excel buttons: `openpyxl` and `xlsxwriter` added to requirements.txt; the Excel buttons fall back to xlsxwriter or are disabled (CSV still works) instead of crashing.
- Moving a box forced a new solve: the canvas re-normalises the model (default edge parameters) on every move, which changed the model fingerprint. Templates are now built in normalised form and default-valued edge parameters no longer count as a model change.
- Gas cards show MSm³/d (network results, nodal card, results browser).
- Sidebar: 'Load demo field' replaced by an example drop-down (all 18 templates) with a Load button.
- Gas viscosity (Lee) overflowed at extreme solver trial pressures with correlation PVT; the exponent is now clamped (`physics/pvt_model.py`).

### Tests
- `tests/test_templates.py`: structure, solve, short forecasts, equipment benefit, content checks, loader through the harness.

## v32.5 — Data hub, yearly bars, groups, material balance, nodal uncertainty / matching / blowout, several fluids, export

**Release Date:** 2026-10-04 - see DATA_NODAL_V32_5.md

### New
- **One consistent data layer** (`network/data_hub.py`, `ui/hub_access.py`): every table (model, steady solve, forecast, yearly volumes, groups, KPIs) is derived from the same model / solve / forecast; 12+ cross-checks (field = sum of wells, yearly = cumulative, tank = field, groups reconcile, results belong to the model on screen). Any table on any tab can be sent to the export basket.
- **Yearly bars** (`network/annual.py`, `ui/annual_view.py`; Prognosis → Yearly profiles): calendar-year volumes for field / wells / tanks / groups, unit systems (MSm3/GSm3, Sm3, mmbbl/bcf), oil-equivalent, stacked, cumulative lines; partial years drawn lighter. The yearly sums equal the cumulatives exactly (forecast rows now carry `Step [days]`).
- **Groups** (`network/groups.py`, `ui/groups_view.py`; Reservoir & wells → Groups): tank + its wells in one click, `North/Segment A` hierarchy, sums at the steady solve, group profiles (rates, cumulatives, water injection, volume-weighted pressure, RF) and yearly volumes.
- **Material balance & voidage** (`network/mb_analysis.py`, `ui/mb_view.py`; Tanks & coupling): voidage vs replacement by year + VRR, cumulative voidage, Havlena-Odeh, Campbell, Cole, p/z, drive indices, pressure vs cumulative / RF / net voidage; fits in-place volume and Schilthuis aquifer on **measured history** or checks a forecast.
- **Transmissibility links**: add / remove tank-tank links (editing existed) and the equalisation time constant per link.
- **Nodal analysis** (`ui/nodal_tools.py`): what-if sliders on 11 inputs; Monte-Carlo fan of IPR / VLP curves (P90-P10 band, percentile slider, rate distribution, sensitivity); **blowout / worst-case discharge** (`physics/blowout.py`: tubing, annulus, both, skin removed, subsea hydrostatic exit, sonic exit check, release over time); **matching to measured data** (`physics/well_match.py`: IPR fit, VLP correlation ranking + `vlp_dp_multiplier`, flowing-gradient survey, apply to well). Measured points are overlaid on the main nodal chart.
- **Several fluids** (`network/fluids.py`, `ui/fluid_library_view.py`; Fluid & PVT → Fluid library): named fluids assigned to a tank system / wells / flowlines, tank Boi-Rsi-Pb synced from the fluid PVT, propagate edits, consistency checks, blended fluid where streams meet.
- **Export** (`network/exporters.py`, `ui/data_view.py`; Cases & Data): Excel (README + one sheet per table), CSV zip, JSON, STEA-style yearly profile table with an editable mapping, and a local read-only API bundle (`serve.py`).
- **Python post-processing** (`network/postprocess.py`): script reshapes tables before export; AST-checked subset.
- Cases now also keep the wells / tanks forecast rows, and the user inputs (scripts, STEA mapping, measured data, fluid library); loading a case restores its forecast while the model is unchanged.

### Fixed
- Forecast field rows lacked the step length; the last (shorter) step was mis-annualised by 1.4 %.

### Limits (stated, not hidden)
- The STEA import format is not known: the export is template-driven - check the first import.
- The "API" is a local snapshot server, not a hosted or live service.
- Material balance on a forecast of the screening tank model closes by construction (consistency check, not evidence); in-place volume is not identifiable when pressure is supported (the tool warns).
- Blowout is steady-state screening: no transient inflow, bridging, gas-cap coning or relief-well kill; the annulus is an equal-area pipe.
- The post-processing filter blocks honest mistakes; it is not a security sandbox.
- Run only in the fake-Streamlit harness here; real Streamlit / Plotly rendering was not exercised.

### Tests
83 new tests (`tests/test_data_layer.py`, `test_export_postprocess.py`, `test_mb_analysis.py`, `test_nodal_uncertainty.py`, `test_blowout.py`, `test_well_match.py`, `test_fluids.py`, `test_app_v325.py`); full suite 1415 passed.

## v32.4 — Fluid & PVT, calibration, CO2 / H2S, temperature model

**Release Date:** 2026-10-04

### New (Reservoir & wells → Fluid & PVT) - see PVT_THERMAL.md
- **Correlation PVT** (`physics/pvt_model.py`): Standing / Vasquez-Beggs / Glaso / Petrosky-Farshad (Pb, Rs, Bo), Beggs-Robinson / Glaso / Egbogah / Beal viscosity, DAK / Hall-Yarborough / Papay Z, Lee-Gonzalez-Eakin gas viscosity, McCain water with salinity. Opt-in per element (`params['pvt']`); default behaviour unchanged.
- **CO2 / H2S / N2** in the gas: pseudo-criticals (Kay + Wichert-Aziz), Z, viscosity, density and bubble point (Standing factors).
- **Calibration to lab data**: fit Pb, Rs shape, Bo, undersaturated compressibility, viscosity, Z, gas viscosity; before / after error table, overlay plots, **correlation ranking**, apply to selected elements, download.
- **Temperature model**: Ramey wellbore profile (wellhead T follows the rate), flowline / riser energy balance (heat loss, Joule-Thomson, elevation, mixture cp), choke JT, network temperature propagation and mixing with hydraulic feedback; flow-assurance profile uses it.
- **Gas-quality screening**: pCO2, pH2S, de Waard-Milliams corrosion rate, sour-service flag.

### Fixed
- The legacy PVT ignored the element's GOR (fixed Pb 150 bar / Rsb 120): free gas appeared at the wrong pressure. The correlation model ties Rsb to the GOR (or the lab Rsb). Legacy kept as default for backward compatibility.
- Wells now report `wellhead_temperature_c`.

### Tests
31 new tests (`tests/test_pvt_model.py`, `tests/test_thermal.py`, `tests/test_gas_quality.py`, app smoke).

## v32.3 — Cases: save, duplicate, compare, export, password sharing

**Release Date:** 2026-10-04

### New (tab **📁 Cases**)
- **Case library** (`network/case_manager.py`, `ui/cases_view.py`): a case = model on screen (nodes, edges, units) + its solve summary + forecast profile/KPIs. *Save* overwrites a case, *Save as new case*, *Load into editor*, *Duplicate* (keeps parent link, optionally the results), *Copy model / results from one case into another*, rename (unique names), description, delete. Shows whether the model on screen has unsaved changes.
- **Compare** two or more cases against a baseline: solve KPIs and forecast KPIs with Δ columns, oil-rate and cumulative-oil overlays, and a parameter-level model diff (element, field, A, B). Buttons *Solve selected cases* / *Run forecast for selected cases* (progress bar, turn green) compute results without touching the editor. Comparison downloadable as CSV.
- **Export / import** one, several or all cases as JSON or as ZIP (one file per case + SHA-256 manifest; a modified file is rejected).
- **Password sharing** (`network/case_share.py`): AES-256-GCM, key from scrypt, header authenticated. Output is a `.fncase` file and a copy-paste *share link* (`fieldnet-share:...`). Wrong password or any modification is rejected. Requires `cryptography` (added to requirements.txt).

### Limits
- There is no server: a *link* contains the data itself (long; practical for small/medium cases), it is not a pointer. Shared copies cannot be revoked and edits are not synchronised between colleagues - each person imports a copy. Live co-editing needs a shared backend (see ARCHITECTURE.md).
- The case library lives in the browser session; export to keep it.
- A loaded case does not restore its forecast into the Prognosis tab (stored for comparison only); re-run to browse element results.

### Tests
13 new tests (`tests/test_cases.py`, 2 in `tests/test_app_smoke.py`). Suite: 1299 passed.

## v32.2 — Simpler app, progress everywhere, prediction sources, uncertainty builder

**Release Date:** 2026-10-04

### Fixed
- **Crash in the relative-permeability / prediction-source editors** (`TypeError: argument of type 'function' is not a container or iterable`): a column was passed to a helper that reads the session state. `ui/widgets.py` now always reads the real session state; the test harness now behaves like Streamlit here (child containers have no `session_state`) and also detects duplicate widget ids.
- Simulator CSV upload on a well stored a `(rows, warnings)` tuple as the table; now stores the rows and shows the warnings.

### New
- **Progress bar + green button** on every run button (`ui/run_button.py`): amber while running, green when finished (stays green until the model changes), red on failure. Solve button is green while the network is solved.
- **Development schedule**: progress through both forecasts, and a *Browse the results* section: network diagram redrawn for any simulated date (pressures, well rates, flows; downloadable SVG), tables for that date, and any node / flowline over time with the date marked. Same browser under the Production forecast.
- **Prediction source tab** (`ui/prediction_view.py`, `network/prediction_assign.py`): per tank *material balance* or *external simulator table* (pressure / water cut / GOR follow the table), per well *decline curve* (qi shared equally or by PI) or *simulator rates CSV*, or back to the tank balance. Overview table of the current setup.
- **Monte Carlo uncertainty builder**: pick element or group, pick the parameter, shape and range from drop-downs / sliders, add; remove with ✖; groups (`All wells`) use one shared factor. Advanced table still available.
- **Calibration tab** replaces Optimization: match measured pressures/rates, and match well tests (IPR / VLP).
- **Optimise while solving** is now a checkbox next to Solve (one operation; the separate integrated optimiser was removed). Debottleneck and sensitivity moved to Tools.
- **Bigger editor**: height slider (600-1600, default 1050), *Wide editor* toggle (full width, properties below), wider default column.
- Forecast: **~3x faster** and Pause / Continue / Stop (see v32.1).

### Simplified
- 8 top-level tabs and 25 sub-tabs reduced to 7 tabs: Network · Reservoir & wells · Results · Prognosis · Calibration · Uncertainty · Tools. Version numbers removed from button labels and titles.

### Honest limits
- Green button colouring uses Streamlit's `st-key-<key>` class (Streamlit >= 1.39); older versions keep the normal colour but still show progress.
- Progress is stage-level for single long calls (calibration, solve); per-step for forecast, scenarios, well count, Monte Carlo, tornado.
- Tank external mode forces pressure from the table; the material balance still tracks cumulatives and RF but no longer drives pressure. A column with a single value is held flat.
- Still verified only against a fake Streamlit + Chromium canvas tests, not a live Streamlit session.

## v32.1 — Forecast speed + run control, Advanced tab

**Release Date:** 2026-10-04

### Forecast
- **~3x faster forecast** (demo, 3 yr: 3.5 s -> 1.0 s): `physics.well_model.solve_well_rate` now scans from the highest rate and stops at the first stable root and refines it with Brent's method instead of evaluating the whole 25-point grid plus bisection (same operating point to solver tolerance). Optional *Store per-element profiles* toggle saves a further ~25 %.
- **Progress, pause/continue, stop** (`network/run_control.py`, `network.forecast.iter_forecast`): the forecast is a generator with stage events (solving network at date, depletion substep, step completed), a progress bar with elapsed time and ETA, a Pause/Continue button and a Stop button. Partial results (charts, KPIs, tables) are published after every step, so you can inspect them while paused; Stop keeps everything computed so far. A paused-then-resumed run gives bit-identical results to an uninterrupted run (tested).
- `run_forecast(..., progress=cb)` accepts a callback; return `False` to stop.

### Advanced tab (new, screening-level)
Well-test calibration (PI + friction multipliers), lift-gas supply limit, pump/compressor curve import and operating-point checks, fluid blending at junctions, flow assurance along the profile (hydrate, wax, erosion, terrain slugging), back-allocation and well-test scheduling, tornado sensitivity, reliability-weighted P10/P50/P90, simulator file link (VFPPROD export, rate import/export), correlation benchmark.

### Honest limits
- Correlations: **still not validated against published data**; see `docs_correlation_validation.md` (consistency checks only).
- VFPPROD output not loaded into Eclipse; no live simulator coupling.
- Pause/Stop act between timesteps/substeps (a single network solve cannot be interrupted).
- Pause relies on Streamlit script reruns; verified with a fake Streamlit only.

## v32 — Inline equipment, constraint registry, tank communication, compute layer

**Release Date:** 2026-10-04

### Added
- **Inline equipment** (`network/equipment.py`): choke, control valve, pump and compressor are now *nodes* placed on a line (expanded to `E::in`/`E::out` junctions + `E::link` during solve, collapsed back afterwards). A *joint* node joins flowlines. "Convert to inline equipment node" migrates legacy equipment edges (connector flowlines are 1 m; zero length is rejected by validation).
- **Feature grouping** (`network/features.py`): palette groups Reservoir / Wells / Connections / Equipment / Processing / Boundaries; well role (producer/injector) + phase; separator types.
- **Well trajectory and completion diameters** feed tubing VLP (`geometry=`), depth and ID by segment.
- **Flowline bathymetry profile and riser option** (`solver/equations.flowline_segments`).
- **Constraint registry** (`solver/constraints.py`): per-feature constraints (per-phase well rates, min BHP, drawdown, WHP, separator per-phase capacity, flowline rate/velocity/erosional ratio/MAOP/dP, equipment power). One implementation shared by solve, forecast and development; `ENFORCEABLE` constraints are honoured by pro-rata choking of contributing wells.
- **Tank-to-tank communication** (transmissibility x dp, max transfer, 90 % equalisation cap) drawn as links between tanks; simplified coupling tab.
- **Compute layer** (`network/solve_options.py`, `network/parallel_solve.py`): honour-constraints option, optimiser objective, Eclipse-style guide rates, component-parallel solve, parallel scenario / well-count runs.
- **Element results tab**: per-element profile (pressure, velocity, holdup...) and time series.
- **SVG export** of the network.
- More tubing/flowline correlations: Hagedorn-Brown, Gray, Drift-flux, Hasan-Kabir (see `docs_correlations.md`; not validated against published data).
- Whole-app smoke harness (`tests/support/app_harness.py`) and Chromium canvas tests (`tests/browser/run_editor_v32_test.py`).

### Fixed
- Changing a flowline type was ignored (graph hash/canonicalisation); moving nodes no longer invalidates a solve.
- 14 pre-existing failing tests (earlier "all passing" statements were wrong).
- App crash: `solver_input` not imported in the Element-results tab (found by the smoke harness).

### Known limitations
- Streamlit / plotly could not be installed in the build environment; the UI was exercised against a fake Streamlit and in Chromium for the canvas only, never in a live Streamlit session.
- Relative permeability is a screening average-saturation model.
- Parallelism only helps for independent systems and MC / scenario / well-count runs; one connected network solves serially.
- Gas-network solver and legacy v25 coupling are unchanged.
- Mukherjee-Brill, Duns-Ros, Orkiszewski not implemented.

## v31 — Reservoir Tanks, Production Prognosis & App Reorganization

**Release Date:** 2026-09-30

### Major Features

#### Tank-Based Reservoir Model (`network/reservoir_mb.py`)

- **Reservoir tank** nodes define in-place volume and fluid phase
- Supported phases:
  - **Oil:** STOIIP [Sm³], BO, Rs, bubble point, solution-gas drive
  - **Dry gas:** GIIP [Sm³], p/z material balance
  - **Gas condensate:** GIIP + CGR, with condensate production
- **Tank assignment:** Drag tank onto well in editor → well takes tank pressure and fluid
- **Automatic depletion:** Tank pressure updated each forecast step via material balance
- **Aquifer support:** Optional Schilthuis aquifer for voidage replacement
- **Fluid evolution:**
  - Water cut follows S-curve from initial to max over recovery factor range
  - GOR rises as tank depletes below bubble point

#### Production Forecast & Development Planning

- **Forecast tab:** Run multi-step production prognosis with automatic depletion sub-stepping
- **KPIs:** Peak rate, plateau, cumulative oil, recovery factor, final water cut
- **Charts:** Liquid rate, gas rate, cumulative oil, tank pressure, water cut, GOR per well
- **Development schedule:** Gantt chart with rig serialization, task dependencies, first-oil date
- **Constraints editor:** Single table for all capacity limits, well rate caps, minimum BHP

#### Well-Count Optimization

- **7-scenario study:** Automatically runs 1–7 producer configurations
- **Marginal-oil rule:** Recommends optimal well count based on incremental production per well
- **KPI comparison:** Peak, plateau, cumulative for each scenario

#### Scenario Comparison

- **Custom scenarios:** Define variations (PI multiplier, capacity, injection on/off)
- **Multi-forecast:** Run all scenarios and overlay production profiles
- **Summary table:** KPI comparison across scenarios

#### App Reorganization

- **7 workflow groups:**
  1. Network — Build topology, solve
  2. Wells & Reservoirs — Configure wells and tanks
  3. Network Results — Nodal analysis, diagnostics
  4. Forecast & Development — Production forecast, schedules
  5. Optimization — Well-count, scenarios, debottleneck
  6. Uncertainty & Risk — Monte Carlo, reliability (advanced)
  7. Data & QA — Import/export, model assurance
- **Consistent styling:** Colour-blind-validated palette (oil=aqua, gas=orange, water=blue)
- **Realistic demo field:** Oil tank with aquifer, gas lift, water injection, separator capacity limit

### Physics & Numerics

- **Dogbox least-squares solver:** ~5 iterations per warm-start solve (vs. ~34 cold-start) → 5–7× faster forecasts
- **Tank pressure propagation:** Applied in every solver path (network, nodal, calibration, optimization, forecast)
- **Gas backpressure IPR:** Automatic for gas-tank wells (no solution gas)
- **Homogeneous VLP:** Automatic for gas-tank wells (no-slip, avoids Beggs–Brill over-prediction)

### Bug Fixes

- **Solve status fell back to UNSOLVED** for models with tanks → fixed by fingerprinting pre-tank-link graph
- **Canvas stale-update on Esc** → deferred updates now properly cancelled
- **Pipeline Δp discontinuous at zero flow** → bridged linearly for |q| < 0.5 m³/d

### Tests

- 258 Python tests pass (14 new in `tests/test_v31_prognosis.py`)
- Browser editor tests: 25/25 Chromium checks pass

---

## v30.2 — Merge with v29.1 + Deployment Fix

**Release Date:** 2026-09-28

### Critical Fix

- **Deployment error `cannot import name 'DEFAULT_VLP_SEGMENTS'`:** Resolved by ensuring all changed files uploaded in one commit (not piecemeal)

### Merged v29.1 Corrective Fixes

- NaN/Inf rejection at CSV/project validation
- Roughness calibration writes solver's `roughness_m`
- Reliability uses exact intervals and clips final timestep
- Stiff reservoir links avoid pressure-equalization overshoot
- Run-manifest SHA-256 verification

### Ported v29.1 Editor Features

- Full palette: reservoir tank, separator stage, exports, gas source, gas injector
- 860 px canvas
- Reservoir-tank property panel

### Solver & Tests

- Dense Jacobian for networks with 1–2 unknowns (SciPy sparse path crashed)
- 244/244 Python tests pass (232 v30.1 + 12 v29.1)
- Browser editor tests: 20/20 pass

---

## v30.1 — Graph Editor & Editor→Solver Contract

**Release Date:** 2026-09-26

### Editor Enhancements

- **Drag-to-connect:** Drag OUT port (right) to IN port (left) of target component
  - Live dashed line follows cursor
  - Valid targets highlight (green); invalid (red)
  - Drop anywhere on target; Esc cancels
- **Sidebar "Connect" removed:** Editor is only place to create connections
- **Property panel:** Edit all connection and component parameters in one place

### View & Performance

- **Browser-local view:** Zoom, pan, Fit, Reset run entirely in React; no Streamlit rerun
- **View persistence:** Zoom/pan maintained across reruns
- **Mid-drag rerun handling:** Streamlit rerun deferred until gesture completes

### Single Editor→Solver Contract (`ui/graph_contract.py`)

- **Editor sends:** `{schema, rev, nodes, edges, selected}`
- **Revision-based:** Only processes graph when `rev` is new (prevents Streamlit replay overwrites)
- **Normalization:** `normalize_graph()` drops dangling, self-loops, duplicates; fills defaults
- **Explicit states:** UNSOLVED → SOLVING → SOLVED/FAILED (shown in editor badge)

### New Model Check

- **LOOP_ELEVATION_MISMATCH:** Elevation changes around closed loop must sum to zero

### Solver

- First-solve pass shorter via well re-seeding on stable branch
- Looped network solves from 8 s → <0.3 s

### Tests

- 232 Python tests pass
- Browser editor tests: 20/20 Chromium checks

---

## v30 — Audit & Bug-Fix Release

**Release Date:** 2026-09-20

### Overview

Complete audit of v29 (Streamlit GAP-style production network solver). All findings reproduced via unit tests, headless runs, and stress networks.

### Critical Bugs Fixed

1. **Canvas overwrote model on rerun** → revision-based state management
2. **Stale widgets overwrote imports** → widget re-seeding on model change
3. **Single-segment tubing (VLP)** → segmented with predictor–corrector
4. **Well equation nested optimizer** → well rates now unknowns with IPR = VLP condition
5. **Network ignored skin, gas lift, VLP model** → unified well model across all paths
6. **Sink without pressure crashed solver** → clear BOUNDARY_WITHOUT_PRESSURE error
7. **Control valve marked as topology error** → fixed edge type validation
8. **Model QA never ran post-solve checks** → fixed dict/tuple mismatch
9. **Water injectors took no flow** → added injectivity-index model

### Physics & Numerics Corrected

- Beggs–Brill uphill segregated coefficient: 3.539 (was 0.3692)
- Holdup blend at intermittent/distributed boundary (smooth S-curve)
- Friction factor blend at Re = 2000–4000 (smooth laminar–turbulent transition)
- Pipeline Δp bridged linearly for |q| < 0.5 m³/d
- Choke/valve Δp now signed (support reverse flow)
- Pump head no longer clipped at zero (keeps falling)
- Pipelines segmented (~1 per 1.5 km, up to 8)
- Well solver applies highest-rate stable intersection (GAP convention)
- Pressure starting guesses from nearest boundary (not fixed 80–90 bar)
- Sparse Jacobian caching for 5–10× speedup per solve

### Constraints (GAP-like)

- Well `max_liquid_rate_m3d` enforced directly
- Separator/export/connection capacity limits via **pro-rata choking**
- Debottleneck screen re-solves with limits (not report-only)

### UI/Data Robustness

- NaN/Inf rejection at interchange and validation boundaries
- Calibration lists free pressures only (fixed pressures are inputs)
- Nodal tab defaults to solved WHP (not fixed 90 bar)
- Unconnected components excluded from solve (with warning)
- CSV interchange fixed for chokes, pumps, valves
- JSON export handles numpy integer types

### Tests

- 228 Python tests pass (25 new regression tests in `tests/test_v30_audit.py`)
- Headless run of all 17 UI tabs
- Demo solve: converges in ~0.15 s (residual < 1e-12)
- Back-pressure sweep: 20→80 bar, every case PASS < 0.5 s
- 5-year forecast: 5 s (was 36 s)
- 3-scenario run: 16 s (was 110 s)

---

## v29 — Scenario Management

**Release Date:** 2026-08-15

### Features

- Immutable content-addressed snapshots
- Branching and scenario lineage
- Assumption registers
- Structural diffs
- Comparison tables
- QA capture
- Reproducible run manifests with SHA-256 verification

### v29.1 Audit Additions

- NaN/Inf rejection at interchange boundaries
- Roughness calibration correctness
- Reliability exact interval clipping
- Stiff reservoir link safeguards
- Run-manifest hash verification

---

## v28 — Engineering QA & Model Assurance

**Release Date:** 2026-07-01

### Features

- Consolidated model-quality gate
- Classification: PASS / REVIEW / FAIL
- Topology checks, unit validation, solver convergence, residual closure
- Operating constraint sanity checks
- Forecast sanity checks
- JSON export of all diagnostics

---

## v26 — Development Planning

**Release Date:** 2026-06-01

### Features

- Dependency and resource-constrained task scheduling
- Drilling/workover rig serialization
- Event types: tieback, commissioning, facility, compression, shutdown, abandonment
- Gantt visualization
- Production-forecast consequences

---

## v25 — Reservoir Coupling

**Release Date:** 2026-05-01

### Features

- Communicating reservoir tanks
- Pressure-dependent aquifer support
- Injector–tank connectivity
- Auditable voidage accounting
- Exact horizon clipping
- Dedicated Reservoir Coupling workspace

---

## v24 — Reliability & Availability

**Release Date:** 2026-04-01

### Features

- Seeded failure/repair Monte Carlo
- Planned outages
- Redundancy modeling
- Availability percentiles
- Deferred-production screening

See `RELIABILITY_V24.md` for details.

---

## v20 — Well Performance

**Release Date:** 2026-02-15

### Features

- Enhanced nodal analysis
- Selectable Beggs-Brill / Homogeneous VLP
- Gas-lift screening and optimization
- Generic ESP affinity-law performance and envelopes
- Well QA

See `WELL_PERFORMANCE_V20.md` for details.

---

## v17.2 — Unit Systems Hardening

**Release Date:** 2026-01-20

### Features

- Explicit engineering-unit boundary
- Norwegian SI profile (bar, °C, m, Sm³/d, kW)
- Field profile (psi, °F, ft, stb/d, hp)
- Standard-volume reference: 15 °C, 1.01325 bara
- PVT absolute pressure; gauge/absolute helpers

---

## v17.1 — Uncertainty Audit

**Release Date:** 2025-12-15

### Features

- Physical sample bounds with explicit clip/reject policy
- Correlation validation (finite, PSD)
- Realization diagnostics and survivor-bias warning
- P10/P50/P90 convergence by realization count
- Spearman rank sensitivity
- Reproducibility metadata in Monte Carlo exports

---

## v17 — Uncertainty & Monte Carlo

**Release Date:** 2025-11-01

### Features

- Seeded Latin-hypercube and random sampling
- Rank-style Gaussian correlation
- Parameter overrides
- P90/P50/P10 production metrics
- Realization exports
- Bounded development-decision optimization

---

## v16.1 — Field Development Audit

**Release Date:** 2025-09-15

### Features

- Schedule integrity audit
- Cumulative accounting
- Scenario isolation
- Export/audit consistency
- Deterministic development forecasting

---

## v14 — Professional Solver Baseline

**Release Date:** 2025-08-01

### Features

- Residual quality gate and normalized residual score
- Pressure/rate scaling metadata
- Hard/soft constraint classification
- Active/near-active constraint reporting
- Debottleneck screening
- Multi-scenario runner
- Calculation audit JSON

---

## Known Limitations

- **PVT:** Screening black-oil with fixed Pb = 150 bar, Rsb = 120 Sm³/Sm³
- **Gas networks:** Liquid-rate formulation (screening only)
- **Shut-in:** Rule applied post-solve, not mixed-integer
- **Gas-lift:** Input (not allocation variable in optimization)

---

## Contributing & Support

See README.md for links to USER_GUIDE.md, API_REFERENCE.md, and TROUBLESHOOTING.md.

For issues or questions, open an issue on GitHub or contact the author.

---

**Made by Merouane Hamdani — For non-commercial use — Independent engineering prototype.**

FieldNet is a screening and planning tool. All results must be validated against offset data and lab measurements before operational decisions.
