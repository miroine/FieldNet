# Templates & examples (v32.6)

Sidebar → **Load an example** (drop-down + Load example), or Cases & Data → **Templates & examples**. Pick a category and a template, read what it shows and what to watch, then **Load into the editor** (replaces the model on screen) or **Load and save as a new case**. Loading also sets the suggested start date, horizon and report step in Prognosis → Production forecast. Solve, then run the forecast.

All templates are illustrative round-number models for learning and for starting your own work. They are not real fields and are not calibrated to any data.

## Library

| # | Template | Category | Wells | Tanks | Inj. | Forecast (start / yrs / step) |
|---|---|---|---|---|---|---|
| 1 | HPHT gas - 4-slot subsea template | Subsea | 4 | 1 | 0 | 2028-01-01 / 10 / 90 d |
| 2 | Daisy chain - three templates in series | Subsea | 6 | 1 | 1 | 2028-01-01 / 12 / 90 d |
| 3 | Several tanks, communicating, commingled wells | Reservoir | 8 | 3 | 0 | 2028-01-01 / 12 / 90 d |
| 4 | Oil with gas injection | Oil | 4 | 1 | 2 | 2028-01-01 / 12 / 90 d |
| 5 | Pure depletion oil | Oil | 5 | 1 | 0 | 2028-01-01 / 15 / 90 d |
| 6 | Wellhead platform tied back to a host platform | Platforms | 10 | 2 | 0 | 2028-01-01 / 12 / 90 d |
| 7 | Subsea booster pump | Subsea | 4 | 1 | 0 | 2028-01-01 / 12 / 90 d |
| 8 | Subsea gas compression | Subsea | 4 | 1 | 0 | 2028-01-01 / 15 / 90 d |
| 9 | Topside export compressor | Platforms | 5 | 1 | 0 | 2028-01-01 / 15 / 90 d |
| 10 | Horizontal well development | Oil | 4 | 1 | 0 | 2028-01-01 / 12 / 90 d |
| 11 | HPHT tight gas with hydraulic fractures | Gas | 8 | 4 | 0 | 2028-01-01 / 12 / 90 d |
| 12 | Gas lift | Oil | 5 | 1 | 0 | 2028-01-01 / 12 / 90 d |
| 13 | ESP lifted heavy oil | Oil | 4 | 1 | 0 | 2028-01-01 / 12 / 90 d |
| 14 | Waterflood | Oil | 5 | 1 | 3 | 2028-01-01 / 15 / 90 d |
| 15 | Gas-condensate tieback | Gas | 3 | 1 | 0 | 2028-01-01 / 12 / 90 d |
| 16 | Onshore gas gathering with compression | Gas | 8 | 2 | 0 | 2028-01-01 / 15 / 90 d |
| 17 | Two wells (learning example) | Learning | 2 | 0 | 0 | 2026-01-01 / 5 / 90 d |
| 18 | Standard demo field | Learning | 3 | 1 | 1 | 2026-01-01 / 10 / 90 d |

## Details

### 1. HPHT gas - 4-slot subsea template

Four HPHT gas wells (750 bar, 170 °C) on one subsea template, 25 km export flowline to a host platform. Correlation PVT with CO2 and N2. Wellbore / flowline thermal models are off for speed and robustness (switch on Ramey / heat-loss per element to see the arrival temperature).

**Watch:** Host gas capacity 26 MSm³/d, tubing / flowline erosional velocity, pressure fall of the tank.

### 2. Daisy chain - three templates in series

Templates A, B, C joined in series with growing line size; one trunk to the host with water injection support.

**Watch:** Back-pressure of the upstream template wells and the trunk capacity: wells far down the chain are the first to choke back.

### 3. Several tanks, communicating, commingled wells

Three stacked sands with tank-to-tank transmissibility; single-zone wells plus two dual-zone wells (two zone wells joined at a joint) that produce two tanks through one wellhead.

**Watch:** Pressure equalisation through the links (Tanks & coupling), crossflow is not modelled inside a commingled well; a well draws from one tank, so a dual-zone well is two zone wells.

### 4. Oil with gas injection

Producers plus two gas injectors fed through an injection compressor from a gas header; the tank sees gas injection in its balance.

**Watch:** Voidage replacement in the Tanks & coupling page, GOR evolution, compressor discharge limit.

### 5. Pure depletion oil

No aquifer and no injection: pressure falls through the bubble point, GOR rises, water cut stays low. Plateau then decline.

**Watch:** Recovery factor (typically low), when the wells can no longer overcome the separator pressure.

### 6. Wellhead platform tied back to a host platform

WHP-A with six wells, a 14 km tieback to host B (own wells, only separator).

**Watch:** Tieback arrival pressure and capacity; wells at A are set by host B inlet pressure plus the line.

### 7. Subsea booster pump

Low-pressure heavy oil on a 28 km tieback with a multiphase booster pump; remove the pump to see the wells choke back.

**Watch:** Pump head and power in the equipment table, suction pressure vs bubble point (gas handling).

### 8. Subsea gas compression

Gas field on a 60 km tieback with a subsea compressor lowering the manifold pressure.

**Watch:** Compression ratio, discharge limit, power; compare the rate with the compressor ratio set to 1.

### 9. Topside export compressor

Low-pressure inlet separation and a topside compressor to the export pipeline pressure.

**Watch:** Inlet pressure vs compression ratio and power; late-life drop in plateau.

### 10. Horizontal well development

Four horizontal wells with trajectory and a two-size completion (Vogel inflow); hydrostatic head uses true vertical depth.

**Watch:** The Element results tab shows the tubing profile along the deviated well.

### 11. HPHT tight gas with hydraulic fractures

Four drainage compartments with two fractured horizontal wells each (negative skin, high deliverability), weak communication, HPHT PVT with CO2 and Ramey temperature.

**Watch:** Steep decline from the small compartments; fracture transient (linear flow) is NOT modelled - calibrate C, n and skin to rate-transient analysis.

### 12. Gas lift

Five gas-lifted producers with high water cut; per-well injection rate.

**Watch:** Gas-lift curve per well in Nodal analysis.

### 13. ESP lifted heavy oil

Heavy oil with ESPs on every well.

**Watch:** ESP head, power and rate envelope in Nodal analysis.

### 14. Waterflood

Five producers, three injectors fed by a seawater pump; pressure held by injection.

**Watch:** VRR and drive indices in Tanks & coupling.

### 15. Gas-condensate tieback

Three subsea wells on a 35 km tieback, condensate-gas ratio from the tank.

**Watch:** Liquid loading, arrival temperature and hydrate margin (Profiles & flow assurance).

### 16. Onshore gas gathering with compression

Two clusters, gathering network, field compressor station and sales gas.

**Watch:** Inlet pressure vs compressor power; cluster back-pressure interaction.

### 17. Two wells (learning example)

Smallest network.

**Watch:** Nodal analysis tab.

### 18. Standard demo field

Oil tank with aquifer, three producers (one gas lift), water injection.

**Watch:** Every tab.

## Verified behaviour

- Every template builds, has unique ids, valid end points, tanks linked to wells, and solves (residual < 1e-3).
- Pump and compressor templates carry more flow than the same network with the equipment bypassed (tested at depleted reservoir pressure for the compressors).
- Short forecasts converge for the simple, gas-lift, waterflood and depletion templates; all templates were also run through a 3-year / 180-day forecast during development.

## Limits

- Gas convention of the engine: a gas well (Gas IPR) is carried as liquid-equivalent rate with GOR 5e5, whatever `gor_sm3sm3` says on the well. Flowlines of a gas model must carry the same GOR (5e5) or their pressure drop is under-stated; the gas templates are set this way. If you build a gas model by hand, copy this.

- Numbers are illustrative; the shape (plateau, decline, back-pressure, benefit of equipment) is the point, not the volumes.
- Fracture-stimulated wells are a strong negative skin plus a high deliverability coefficient: there is no fracture transient model.
- A well draws from one tank. A dual-zone well is two zone wells joined at a joint (see the multi-tank template).
- The HPHT 4-slot template runs with thermal models off for speed; switch Ramey / heat-loss on per element (the 3-year forecast is slower with them).
- A compressor or pump edge must sit between free nodes (not between two fixed-pressure nodes), as in the templates.
- The Streamlit page was exercised through the fake-Streamlit harness, not a live browser.
