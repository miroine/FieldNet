# FieldNet User Guide

Complete step-by-step workflows for building networks, forecasting production, planning development, and optimizing well counts.

## Table of Contents

1. [Getting Started](#getting-started)
2. [Building a Field Network](#building-a-field-network)
3. [Defining Reservoir Tanks](#defining-reservoir-tanks)
4. [Solving the Network](#solving-the-network)
5. [Production Forecasts](#production-forecasts)
6. [Development Planning](#development-planning)
7. [Well-Count Optimization](#well-count-optimization)
8. [Scenario Comparison](#scenario-comparison)
9. [Constraints & Bottlenecks](#constraints--bottlenecks)
10. [Understanding Charts & KPIs](#understanding-charts--kpis)

---

## Getting Started

### Launching the App

```bash
streamlit run app.py
```

The app opens at `http://localhost:8501`. The left sidebar contains the workflow navigation.

### The Seven Workflow Groups

FieldNet is organized into seven groups to guide your modeling:

1. **Network** — Build topology, solve, view results
2. **Wells & Reservoirs** — Configure wells, tanks, drilling parameters
3. **Network Results** — Nodal analysis, pressure profiles, solver diagnostics
4. **Forecast & Development** — Production forecast, development schedule, KPIs
5. **Optimization** — Scenario comparison, well-count study, debottleneck analysis
6. **Uncertainty & Risk** — Monte Carlo, reliability studies (advanced)
7. **Data & QA** — Import/export, model assurance checks, raw data editing

---

## Building a Field Network

### Step 1: Open the Canvas Editor

In the **Network** tab, you'll see the interactive canvas editor. This is where you build your field topology.

### Step 2: Add Components

Click **"Add component"** on the canvas or use the left panel. Choose from:

- **Well** — Production well
- **Water Injector** — Water injection well
- **Gas Injector** — Gas lift or gas injection
- **Separator** — Liquid–gas separation, sets output pressure
- **Export** — Gas or liquid outlet (fixed pressure boundary)
- **Sink** — Liquid or gas dump (fixed pressure boundary)
- **Choke** — Pressure drop device (adjustable opening, Cv)
- **Pump** — Subsurface or topside lift device
- **Compressor** — Gas compression (ratio-based)
- **Reservoir Tank** — Oil/gas in-place volume with material balance (v31+)

### Step 3: Position & Name

Drag components to arrange them on the canvas. Click a component and edit its name in the property panel on the right.

### Step 4: Connect with Pipelines

**Drag from OUT port (right side of component) to IN port (left side) of another component.** A dashed line appears as you drag. The target component's IN port turns green if valid, red if invalid (self-loop, duplicate pipeline, wrong types).

Drop on the target's port to complete the connection. You can also drop anywhere on the target component.

Connections default to **pipeline** type with standard parameters (diameter, roughness, length, temperature, water cut, GOR, API gravity).

### Step 5: Edit Connection Properties

Click a pipeline/choke/pump to select it. In the property panel, adjust:

- **Length [m]** — Pipeline length
- **Diameter [m]** — Internal diameter
- **Roughness [mm]** — Absolute roughness for friction calculation
- **Elevation change [m]** — Hydrostatic pressure change
- **Temperature [°C]** — Fluid temperature
- **Water cut** — Initial water fraction [0–1]
- **GOR [Sm³/Sm³]** — Gas-oil ratio
- **API gravity** — Oil density
- **Gas SG** — Gas specific gravity (air = 1.0)

**Choke parameters:**
- **Cv** — Flow capacity coefficient
- **Opening** — Valve opening fraction [0–1]

**Pump parameters:**
- **Shutoff head [bar]** — Pump discharge head at zero rate
- **Rated rate [m³/d]** — Pump design rate
- **Efficiency** — Pump mechanical efficiency [0–1]
- **Speed fraction** — Percent of design speed [0–1]

### Step 6: Assign Wells to Tanks (v31+)

**Drag a reservoir tank onto a well or injector** in the editor. The connection is drawn as a dashed line labeled "drains." Wells assigned to a tank automatically:
- Take the tank's reservoir pressure (not a fixed wellhead pressure)
- Produce at the tank's fluid composition (water cut, GOR, etc.)
- Depress the tank as they produce

Existing pipelines from tanks to wells are automatically converted to tank assignments.

---

## Defining Reservoir Tanks

Tanks are the *source* of reservoir pressure and fluid for all linked wells.

### Creating a Tank

1. Add a **Reservoir Tank** component from the palette
2. Click it to open the property panel
3. Choose a **Fluid Phase**:
   - **Oil** — STOIIP [Sm³], bubble point, solution gas drive
   - **Gas** — GIIP [Sm³], dry gas (p/z material balance)
   - **Gas Condensate** — GIIP + CGR [Sm³/MSm³]

### Tank Properties (Oil Phase)

| Parameter | Default | Units | Notes |
|-----------|---------|-------|-------|
| Reservoir Pressure | 250 | bar | Current tank pressure; updates each forecast step |
| Temperature | 90 | °C | Constant during depletion |
| STOIIP | 20e6 | Sm³ | Initial stock-tank-oil in place |
| BO (formation volume factor) | 1.25 | rm³/Sm³ | Reservoir to standard volume |
| Rs (solution gas ratio) | 100 | Sm³/Sm³ | Gas in solution at bubble point |
| Bubble Point | 150 | bar | Pressure below which gas liberates |
| Swi (initial water saturation) | 0.2 | — | Fraction of pore space [0–0.9] |
| Ct (compressibility) | 1.5e-4 | 1/bar | Fluid and rock compressibility |
| Gas SG | 0.7 | — | Air = 1.0 |

### Tank Properties (Gas Phase)

| Parameter | Default | Units | Notes |
|-----------|---------|-------|-------|
| GIIP | 5e9 | Sm³ | Initial gas in place |
| CGR (condensate GOR) | 100 | Sm³/MSm³ | For gas condensate only |
| p/z material balance | — | — | Standard dry-gas depletion |

### Optional: Aquifer Support

Add a steady-state Schilthuis aquifer to replace voidage:

| Parameter | Default | Units |
|-----------|---------|-------|
| Aquifer PI | 0.0 | m³/d/bar |
| Minimum Pressure | 20 | bar |

Aquifer influx = J_aq × (P_initial − P_current). Water injectors assigned to the same tank support the aquifer.

### Water Breakthrough & GOR Rise

For oil tanks, define fluid evolution:

| Parameter | Default | Notes |
|-----------|---------|-------|
| Water Breakthrough RF | 0.05 | Recovery factor at water arrival |
| Max Water Cut | 0.9 | Final water cut [0–0.99] |
| RF at Max Water Cut | 0.40 | Recovery factor when water cut reaches max |
| GOR Rise Factor | 3.0 | Multiplier on GOR rise below bubble point |

Water cut follows an S-curve from well initial WC to tank max WC over the RF range. GOR rises as `GOR₀ × (1 + rise_factor × (Pb − P) / Pb)` when P < Pb.

---

## Solving the Network

### Running a Steady-State Solve

1. Click **"Solve Network"** in the **Network** tab
2. The solve button shows **SOLVING** while the solver runs
3. Results appear when complete:
   - **SOLVED** — Converged with quality gate PASS (residual < 1e-12)
   - **FAILED** — Did not converge, with error message

### Solve Status Badge

Below the editor, a status badge shows:
- **UNSOLVED** — Network not yet solved, or changed since last solve
- **SOLVING** — Solver is running
- **SOLVED** — Results are valid for the current model
- **FAILED** — Last solve did not pass quality checks

### Warm-Start & Solver Details

The solver uses:
- **Sparse Jacobian** for networks with >10 nodes (SciPy KLU)
- **Dense Jacobian** for small networks (<10 nodes)
- **Warm-start** from previous pressures and flows (if available)
- **Well stabilization** on their highest-rate stable IPR/VLP intersection (GAP convention)

Average solve time: 0.1–0.5 s for typical networks.

### Viewing Pressure & Flow Results

After solving, click the **Network Results** → **Pressure & Flow** tab to see:
- Node pressures [bar]
- Well rates [m³/d]
- Pipeline flows [m³/d]
- Solver convergence info and residuals

### Common Solve Failures

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for detailed solutions:
- "No oil produced" — Wells dead or tank pressure too low
- Solver stalls — Looped network with elevation mismatch, or very stiff equipment
- "Missing endpoint" — Pipeline disconnected to unknown component

---

## Production Forecasts

### Creating a Forecast

1. Go to **Forecast & Development** → **Production Forecast**
2. Set **Report Step [months]** — Monthly (1), quarterly (3), or annual (12)
3. Set **Forecast Length [years]** — Typically 5–20 years
4. Click **"Run Forecast"**

### What Happens in a Forecast Step

Each step:
1. Solves the network at current tank pressure
2. Records well rates and total produced
3. Depletes the tank(s) from produced volumes
4. Updates tank pressure (material balance) and fluid composition
5. Moves to next time step

Depletion automatically **sub-steps** to prevent pressure overshoots.

### Forecast Results

The **Forecast Summary** shows:
- **Peak liquid rate [m³/d]** — Highest daily rate
- **Plateau rate [m³/d]** — Average during plateau phase
- **Cumulative oil [Sm³]** — Total produced
- **Recovery factor [%]** — Cum oil / STOIIP
- **Final water cut [%]** — Water cut at end
- **Final GOR [Sm³/Sm³]** — Gas-oil ratio at end

### Forecast Charts

**Liquids (Oil + Water) Rate Over Time:**
- Stacked area by well (colored by well name)
- Y-axis: m³/d
- X-axis: Years

**Gas Rate Over Time:**
- Line plot, separate axis from liquids
- Y-axis: Sm³/d
- X-axis: Years

**Cumulative Oil:**
- Monotonic increase, should match final recovery factor

**Tank Pressure:**
- Pressure decline curve(s), one per tank

**Water Cut Evolution:**
- Rises after breakthrough, affected by tank parameters

**GOR by Well:**
- One line per well, rises below bubble point for oil tanks

### Exporting Forecast Data

Click **"Export forecast as CSV"** to download:
- Time step, pressure, rates per well, cumulative totals
- Import into Excel or other tools for further analysis

---

## Development Planning

### The Development Schedule Tab

Define when wells are drilled and facilities come online.

### Key Parameters

| Parameter | Example | Notes |
|-----------|---------|-------|
| Well Name | Producer_1 | Must match a well in the network |
| Task | Drill, Complete, Tie-in | Custom event names supported |
| Start Date | 2024-01-15 | "Not before" date |
| Duration [days] | 30 | How long the task takes |
| Resource (Rig) | Rig_A | Rigs serialize; one task per rig per time |

### Interpreting the Gantt Chart

The chart shows:
- Each well on a horizontal line
- Color-coded by resource (drilling rig)
- Bars represent task durations
- Bars stop if a rig is busy elsewhere

### Development KPIs

After defining a schedule:
- **First Oil Date** — When production starts
- **Production at first oil** — Rate when wells come online
- **Cum oil vs. "all wells on stream"** — Compare to scenario where all wells exist at start

---

## Well-Count Optimization

### Running the Study

**Forecast & Development** → **Well-Count Study**

The tool runs **7 scenarios** automatically:
- 1 producer, 0 injectors
- 2 producers, 0 injectors
- 3 producers, 1 injector
- 4 producers, 1 injector
- 5 producers, 1 injector
- 6 producers, 2 injectors
- 7 producers, 2 injectors

Each scenario duplicates the highest-rate well in the network.

### Output: Recommended Well Count

Based on a **marginal-oil rule** (each new well should add at least 5% incremental cumulative oil), the tool recommends an optimal producer count. This balances drilling costs (not modeled) against production upside.

### Scenario Comparison

Compare peak rate, plateau, cumulative oil, and recovery factor across scenarios in a summary table.

---

## Scenario Comparison

### The Scenario Tab

Define custom scenarios with different assumptions:
- Well count
- Initial PI multiplier
- Separator pressure
- Injection on/off
- Capacity limits

### Running Scenarios

1. Click **"Add Scenario"**
2. Name it (e.g., "Base Case", "High GOR", "No Injection")
3. Adjust parameters
4. Click **"Run All Scenarios"**

### Comparing Results

The app overlays forecast curves:
- Liquid rate: all scenarios on one plot (by color)
- KPI table: peak, plateau, cumulative for each

---

## Constraints & Bottlenecks

### Defining Capacity Limits

Go to **Network** → **Constraints** or **Forecast & Development** → **Constraints**.

Edit a single table with all capacity and operating limits:

| Component | Parameter | Units | Effect |
|-----------|-----------|-------|--------|
| Separator | Max Liquid [m³/d] | — | Chokes wells pro-rata when exceeded |
| Export | Max Gas [Sm³/d] | — | Chokes compressor inlet when exceeded |
| Connection | Max Rate [m³/d] | — | Throttles pipeline when exceeded |
| Well | Max Liquid Rate [m³/d] | — | Hard rate cap in well equation |
| Well | Min BHP [bar] | — | Well dead if it can't maintain this |

### Pro-Rata Choking

When a separator or export capacity is hit, the solver **pro-rata chokes all upstream wells**:
- Choke all wells feeding the bottleneck equally
- Reduce rates so total ≤ limit
- Results show which wells are choked

### Debottleneck Analysis

**Optimization** → **Debottleneck Screen**

Incrementally increase separator or export capacity and re-solve to see:
- How much incremental oil per +10% capacity
- Which constraint is tightest
- Where to invest in debottlenecking

---

## Understanding Charts & KPIs

### Color Palette (Color-Blind Validated)

- **Oil / Liquid** — Aqua (#1baf7a)
- **Gas** — Orange (#eb6834)
- **Water** — Blue (#2a78d6)
- **Injection** — Purple (#4a3aa7)
- **Other** — Gray (#6b6a66)

### Common Charts

**Stacked Area (Liquid Rates by Well):**
- Y-axis: m³/d
- X-axis: Time
- One color per well, stacked to show total

**Line Plot (Gas Rate):**
- Separate from liquids to avoid unit confusion
- Y-axis: Sm³/d (standard conditions)
- X-axis: Time

**Gantt (Development Schedule):**
- Y-axis: Well names
- X-axis: Calendar dates
- Color: Resource (rig) name
- Each bar: duration of one task

### Key Performance Indicators

| KPI | Meaning | Example |
|-----|---------|---------|
| **Peak Liquid Rate** | Maximum daily production | 500 m³/d |
| **Plateau Rate** | Sustained rate during plateau phase | 400 m³/d |
| **Cumulative Oil** | Total produced over forecast period | 50 MSm³ |
| **Recovery Factor** | Cumulative / STOIIP | 25% |
| **Water Cut (Final)** | Water fraction at end of life | 60% |
| **GOR (Final)** | Gas-oil ratio at end | 300 Sm³/Sm³ |
| **First Oil Date** | When production begins | 2026-06-15 |

---

## Tips & Best Practices

### Network Modeling

- **Test simple cases first** — A 2-well, 1-separator network is a good starting point
- **Validate against known data** — Compare to offset fields or pilot data
- **Use the demo field** — Load it to see realistic topology, tank parameters, and chart styles

### Tank Setup

- **Bubble point** — For oil tanks, typically 150–300 bar; too high and wells stay undersaturated; too low and gas evolution is instant
- **Aquifer PI** — Large aquifers (> 5 m³/d/bar) can sustain field pressure artificially; validate with offset data
- **Water breakthrough** — Model water arrival conservatively (RF 3–10%) to avoid optimistic oil predictions

### Forecasting

- **Sub-steps happen automatically** — You don't need to set them; the solver refines as needed
- **Cold starts** — The first solve in a forecast is slower; subsequent steps use warm-start
- **Long horizons** — 20+ year forecasts can take 10–30 s; check progress in the UI status message

### Development Planning

- **Use realistic rig schedules** — Account for mob/demob, weather, regulatory hold-ups
- **Parallel drilling** — Multiple rigs can drill at the same time; each resource name is independent
- **Facility delays** — Tie-in, commissioning, and export bottlenecks happen after drilling; add explicit tasks

---

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common issues and [EXAMPLES.md](EXAMPLES.md) for worked walkthroughs.
