# v32.5 - data hub, yearly bars, groups, material balance, nodal tools, several fluids, export

## 1. One set of numbers
Every table in the app is derived from three inputs: the **model**, the current **steady-state solve** and the **forecast**. `Cases & Data → Data hub` lists them (model, solve, forecast, yearly, groups, KPIs) and runs the cross-checks:

| Check | Meaning |
|---|---|
| field rate = sum of wells | oil, water, gas |
| yearly volumes = cumulative | oil, gas, water |
| tank cumulative oil = field cumulative oil | |
| groups reconcile | top-level groups = their wells; full partition = field |
| first forecast step vs steady solve | warning only (tank pressure / fluid may differ slightly) |
| results belong to the model on screen | stale solve / forecast is flagged and left out |

Any table shown on any tab has a **"Add to the export basket"** tick plus CSV / Excel buttons; basket tables appear in Export and in the post-processing script.

## 2. Yearly profiles (Prognosis → Yearly profiles)
Calendar-year volumes integrated from the forecast steps (a step across 1 January is split by days). Field, wells, tanks or groups; units MSm³/GSm³, Sm³, mmbbl/bcf; optional oil equivalent (1 Sm³ oil = 1000 Sm³ gas); stacked bars, cumulative lines. Years the forecast does not fully cover are lighter.

## 3. Groups (Reservoir & wells → Groups)
A group is a name on the elements. "Create group" puts a tank and the wells it drains in one group; the Membership table edits any element; `North/Segment A` is part of `North`. Sums: steady-state (rates, wells flowing), forecast profile (rates, cumulatives, water injection, PV-weighted pressure, RF) and yearly volumes.

## 4. Material balance & voidage (Reservoir & wells → Tanks & coupling)
Source: forecast of the model, or **measured history** (upload / paste pressure and cumulative volumes).
* **Voidage replacement**: voidage (oil, free gas, water at reservoir conditions) against replacement (water / gas injection, aquifer influx, communication) per year, with VRR.
* **Straight-line plots**: Havlena-Odeh F vs Et, Campbell F/Et vs F, Cole F/Et vs cumulative, p/z vs Gp (gas).
* **Drive indices**: depletion, gas cap, compaction + water expansion, aquifer, injection; closure shown.
* **Fits**: in-place volume (volumetric), or in-place volume + Schilthuis aquifer J, or in-place volume with the tabulated influx removed.
* **Links between tanks**: add / remove, edit transmissibility and maximum transfer; the table shows the equalisation time constant (< 30 days: effectively one tank).

Reading guide: on a forecast of the screening tank model the balance closes by construction - the plots confirm consistency, they are not independent evidence. When pressure is supported (VRR ≈ 1) the in-place volume is **not identifiable** and the tool says so.

## 5. Nodal analysis extras
* **Uncertainty & what-if**: 11 inputs (reservoir pressure, productivity, skin, water cut, GOR, WHP, tubing pressure-drop multiplier, roughness, ID, API, temperature). Sliders move the curves and operating point live. Monte-Carlo (Latin hypercube, triangular ranges you edit): P90-P10 band of the IPR and VLP curves, rate distribution, rank-correlation sensitivity, probability the well flows, and a **percentile slider** that shows the realisation at P90 / P75 / P50 / P25 / P10. P90 = low case.
* **Match to measured data**: well-test points (rate, flowing BHP, WHP, static Pr, optional water cut / GOR) match the IPR (PI, Vogel qmax or gas C; optionally Pr); VLP ranks every tubing correlation and fits a pressure-drop multiplier (warns when far from 1: fix depth / ID / GOR / fluid first); a flowing-gradient survey ranks correlations by profile error. Measured points are plotted on the nodal chart. "Apply" writes the match into the well.
* **Blowout / worst-case discharge**: flow to atmosphere (or seabed hydrostatic) through tubing, annulus, or both, with / without skin; Wood mixture sound speed check at the exit (flow raised to the choked exit pressure when sonic); release over time with a depletion of the connected volume. Steady-state screening.

## 6. Several fluids (Reservoir & wells → Fluid & PVT → Fluid library)
Define named fluids (API, gas SG, GOR, legacy or correlation PVT with the settings of the Fluid tab). Assign to a **tank system** (tank + wells; the tank's Boi, Rsi and Pb come from the fluid PVT), to wells, or to flowlines. "Update elements" re-applies an edited fluid. "Where fluids are used" reports well/tank mismatches and the blended API / GOR where streams meet (ideal volume mixing). Water cut stays an element property.

## 7. Export (Cases & Data)
| Output | Content |
|---|---|
| Excel | README sheet (catalogue, checks, model hash) + one sheet per table |
| CSV zip | one CSV per table + manifest |
| JSON | manifest + records |
| STEA-style | rows = series, columns = calendar years, Unit, Total; editable mapping (series, table, column, scale, unit); CSV with `;` and decimal comma, or `,` and point |
| Local API | zip with CSV data, manifest, `serve.py` (standard library, 127.0.0.1, read-only), client example |

**Honest limits.** The import format of your STEA / economics tool is not known here: check the first import and adjust the mapping. The API is a snapshot server for your own computer, not a hosted or live service.

## 8. Python post-processing
Script sees `ds` (copies of all tables), `pd`, `np`, `math`, `datetime`, `kpis`; fills `out['name'] = DataFrame`. Results join the Data hub and every export. Imports, file access, dunder attributes and `eval` are rejected before running. This stops mistakes; it is not a security sandbox - run only scripts you trust.

## 9. Cases
A case now also stores the wells / tanks forecast rows (so yearly bars, groups and exports work after loading) and your inputs: post-processing script, STEA mapping, measured history and tests, fluid library. Loading a case restores its forecast while the model is unchanged.

## 10. Not done / next
Real Streamlit and Plotly were not run in the build environment (a fake-Streamlit harness exercised every page). A hosted API and a shared case store need a server. A transient blowout simulator and a reservoir-grade material balance (gas-cap tracking, PVT-table drive indices) would be the next engineering steps.
