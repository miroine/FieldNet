# FieldNet Examples & Walkthroughs

Worked examples demonstrating common workflows and the demo field case.

## Table of Contents

1. [Quick Start: 2-Well Network](#quick-start-2-well-network)
2. [Demo Field Case](#demo-field-case)
3. [Adding Artificial Lift (Gas Lift)](#adding-artificial-lift-gas-lift)
4. [Creating a Scenario with Capacity Constraints](#creating-a-scenario-with-capacity-constraints)
5. [Optimizing Well Count](#optimizing-well-count)
6. [Comparing Oil vs. Gas-Condensate Fluids](#comparing-oil-vs-gas-condensate-fluids)

---

## Quick Start: 2-Well Network

### Objective

Build a minimal working network: 2 producers → separator → export sink.

### Step 1: Add Components

1. **Add Tank (Reservoir)**
   - Drag "Reservoir Tank" from the palette
   - Name: "Oil Tank"
   - Set params:
     - Fluid phase: Oil
     - STOIIP: 20 MSm³
     - Reservoir pressure: 250 bar
     - BO: 1.25, Rs: 100, Pb: 150 bar

2. **Add Wells**
   - Drag 2× "Well" from the palette
   - Name: "Prod_1", "Prod_2"
   - Position side-by-side on canvas

3. **Add Separator**
   - Drag "Separator" from the palette
   - Set pressure: 10 bar

4. **Add Export Sink**
   - Drag "Sink" from the palette
   - Name: "Gas Export"
   - Set pressure: 5 bar

### Step 2: Connect & Assign

1. **Drag tank onto both wells** (dashed "drains" lines)
   - Wells now take tank's pressure and fluid

2. **Connect Prod_1 → Separator**
   - Drag OUT port of Prod_1 to IN port of Separator
   - Accept defaults (1 km pipeline, 154 mm diameter)

3. **Connect Prod_2 → Separator**
   - Repeat

4. **Connect Separator → Gas Export**
   - This is a dummy connection (gas export isn't modeled in v31)

### Step 3: Solve

1. Click **"Solve Network"** button
2. Status changes to SOLVING, then SOLVED
3. Check **Network Results** → **Pressure & Flow** tab:
   - Tank pressure: ~250 bar (undepleted)
   - Each producer: ~200 m³/d (typical)
   - Separator pressure: 10 bar

### Step 4: Run a 5-Year Forecast

1. Go to **Forecast & Development** → **Production Forecast**
2. Report step: 1 month
3. Forecast length: 5 years
4. Click **"Run Forecast"**

### Results

The chart shows:
- **Liquid rate** drops from ~400 m³/d → ~100 m³/d (depletion)
- **Water cut** rises from 10% → 40% (breakthrough)
- **GOR** rises from 100 → 300 Sm³/Sm³ (below bubble point)
- **Cumulative oil** reaches ~50 MSm³ (25% RF)

---

## Demo Field Case

The built-in demo field is a realistic example with:

### Network Topology

```
Oil Tank (aquifer-supported)
├── Prod_1 (no lift) → Pipeline → Separator
├── Prod_2 (gas lift) → Pipeline → Manifold → Separator
├── Prod_3 (no lift) → Choke (20% open) → Separator
└── Water Injector (PI-limited) → Pipeline ← Water Tank (aquifer-fed)

Separator (max 1000 m³/d liquid) → Export Sink (fixed 5 bar)
```

### Key Features

- **Aquifer:** 10 m³/d/bar voidage replacement
- **Gas Lift:** Injector on Prod_2, adds 50 Sm³/d to tubing flow
- **Choke:** Prod_3 is choked to test capacity constraint
- **Water Injection:** Supports tank pressure

### Expected Behavior

1. **Solve at T=0:** 
   - Total rate ~450 m³/d
   - Tank pressure 250 bar
   - Prod_2 highest rate (gas lift assist)

2. **Run 5-year forecast:**
   - First 2 years: relatively flat (aquifer supports pressure)
   - Year 3+: decline accelerates as depletion dominates
   - Water cut rises from 15% → 60%

3. **Run well-count study:**
   - Add/remove producers and compare cumulative oil
   - Recommended count: 3 producers + 1 injector

### Interpreting Charts

**Stacked Liquid Rate by Well:**
- Prod_1 (aqua): drops from 150 → 50 m³/d
- Prod_2 (orange, gas-lifted): higher initial rate due to lift
- Prod_3 (green, choked): limited to ~60 m³/d
- Injector (purple, negative): constant −500 m³/d injection

**Tank Pressure:**
- Starts 250 bar
- Plateaus ~240 bar for 2 years (aquifer influx)
- Drops to ~100 bar by year 5 (aquifer exhausted)

**Water Cut:**
- Smooth S-curve from 15% → 60%
- Matches tank's water-breakthrough parameters

---

## Adding Artificial Lift (Gas Lift)

### Objective

Increase Prod_1's rate from 200 m³/d to 250 m³/d by injecting gas from the separator.

### Step 1: Add Gas Injector

1. Drag "Gas Injector" from palette
2. Name: "Gas Lift Supply"
3. Connect it to Prod_1's tubing (downstream of well, upstream of choke)

### Step 2: Configure Gas Lift

In the property panel for the Gas Injector:
- Set **gas_lift_rate_m3d**: 50 (inject 50 Sm³/d)
- Set **ipr_model**: (not used for injectors)
- Leave other params as defaults

### Step 3: Connect to Well

Update Prod_1's params:
- **gas_lift_rate_m3d**: 50 (matches injector)
- This adds 50 Sm³/d to the tubing flow

### Step 4: Re-Solve

1. Click **"Solve Network"**
2. Prod_1's rate increases to ~250 m³/d
3. Total well rate increases by ~50 m³/d

### Physics

Gas lift reduces the hydrostatic pressure in the tubing by adding low-density (high-GOR) flow. This improves the IPR/VLP intersection and allows higher flowing pressures (lower Pwf) at the same bottomhole pressure.

---

## Creating a Scenario with Capacity Constraints

### Objective

Test the impact of a 750 m³/d separator capacity limit (vs. unconstrained base case).

### Step 1: Define Constraint

1. Go to **Network** → **Constraints** (or **Forecast & Development** → **Constraints**)
2. Edit the constraints table
3. Find "Separator" row
4. Set **Max Liquid**: 750 m³/d

### Step 2: Re-Solve

1. Click **"Solve Network"**
2. Total rate is now capped at 750 m³/d
3. The solver **pro-rata chokes** all wells upstream of the separator

### Step 3: Check Choke Status

In **Network Results** → **Pressure & Flow**:
- Note which wells are choked
- Pressure drop on each is larger (throttling)
- Well rates are reduced pro-rata

### Step 4: Run Forecast with Constraint

1. Go to **Forecast & Development** → **Production Forecast**
2. Click **"Run Forecast"**
3. Peak rate is now ~750 m³/d (vs. ~900 m³/d unconstrained)
4. Cumulative oil is lower (capacity-limited production)

### Debottleneck Analysis

1. Go to **Optimization** → **Debottleneck Screen**
2. Incrementally increase separator capacity: 750 → 825 → 900 → ...
3. For each capacity, re-solve and measure incremental cumulative oil
4. Chart shows diminishing returns: first 100 m³/d capacity worth ~5 MSm³ oil; last 100 m³/d worth only 1 MSm³

---

## Optimizing Well Count

### Objective

Recommend the optimal number of producers using a marginal-oil rule.

### Step 1: Run Well-Count Study

1. Go to **Forecast & Development** → **Well-Count Study**
2. Click **"Run Scenarios"**

The app runs 7 scenarios:
- 1 producer, 0 injectors
- 2 producers, 1 injector
- 3 producers, 1 injector
- ...
- 7 producers, 2 injectors

### Step 2: Interpret Results

The summary table shows:

| Scenario | Peak [m³/d] | Cumulative Oil [MSm³] | Marginal Oil [MSm³] | Recommend |
|----------|-------------|----------------------|---------------------|-----------|
| 1 prod | 280 | 25 | — | ✗ |
| 2 prod | 380 | 42 | 17 | ✓ |
| 3 prod | 450 | 52 | 10 | ✓ |
| 4 prod | 480 | 58 | 6 | — |
| 5 prod | 500 | 62 | 4 | ✗ |
| 6 prod | 510 | 64 | 2 | ✗ |
| 7 prod | 515 | 65 | 1 | ✗ |

**Marginal-Oil Rule:** Add a well only if it increases cumulative oil by >5% of previous.

- 1→2: +17 MSm³ (68% increase) → **RECOMMEND**
- 2→3: +10 MSm³ (24% increase) → **RECOMMEND**
- 3→4: +6 MSm³ (12% increase) → Maybe
- 4→5: +4 MSm³ (7% increase) → Maybe
- 5+: <5% marginal → **SKIP**

**Recommended count: 3 producers + 1 injector**

### Economics (Outside FieldNet)

Use the **Peak rate**, **Cumulative oil**, and **Plateau duration** to estimate NPV:

```
NPV = Σ(rate[t] × price × discount_factor) − drilling_cost × well_count
```

FieldNet provides the production data; drill costs and prices come from your business case.

---

## Comparing Oil vs. Gas-Condensate Fluids

### Objective

Show how tank fluid phase affects well behavior and forecast.

### Setup 1: Oil Tank (Baseline)

Create the 2-well network (from quick-start) with:
- **Fluid phase**: Oil
- **STOIIP**: 20 MSm³
- **Rsi**: 100 Sm³/Sm³ (solution gas)
- **Pb**: 150 bar (below initial reservoir pressure of 250 bar)

Run a solve and forecast. Results:
- Wells produce oil and gas together
- GOR rises as pressure drops below bubble point
- Water cut rises due to S-curve breakthrough model

### Setup 2: Gas-Condensate Tank

Clone the network, but change tank params to:
- **Fluid phase**: Gas condensate
- **GIIP**: 5e9 Sm³ (gas initially in place)
- **CGR**: 100 Sm³/MSm³ (condensate per million standard cubic meters)

Re-solve. Results:
- Wells automatically use **gas backpressure IPR** (not Vogel)
- Wells automatically use **Homogeneous (no-slip) VLP** (not Beggs-Brill)
- Produced liquid is liquid condensate (from CGR), not water cut
- GOR is constant (reciprocal of CGR = 10,000 Sm³/Sm³)

### Comparison Chart

Run a forecast for both tanks (export as CSV) and overlay in Excel:

```
Time [years] | Oil Tank Rate | Gas Tank Rate
0            | 400          | 350
1            | 350          | 320
2            | 300          | 300
5            | 100          | 120
```

Gas-condensate wells produce more steadily (less depletion sensitivity) because p/z material balance is more forgiving than solution-gas drive.

---

## Advanced: Custom Well Performance Map

### Objective

Use a pump performance map to optimize gas lift rate.

*Note: Detailed pump maps are not yet exposed in the v31 UI. This example describes what is possible programmatically.*

### Python Script

```python
from physics.pump_affinity import pump_curve_head

# Define pump curve (simplified 3-point curve)
pump_points = [
    (0, 100),     # (rate_m3d, head_bar) at design speed
    (1500, 35),   # design point
    (3000, 0)     # run-out
]

# Interpolate head at 50% speed
rate = 750  # m³/d
head_bar = pump_curve_head(rate, 0.5, pump_points)  # 50% speed

# Output
print(f"At 750 m³/d and 50% speed: {head_bar:.1f} bar head")
# Expected: ~17 bar (affinity: H ∝ (N)² at same rate)
```

---

## Tips for Your Own Field

### 1. Validate Against Offset Data

If you have production data from an adjacent well:
- Build a 1-well model of that well
- Adjust IPR (PI, Vogel params) until solve rate matches actual production
- Use calibrated PI for your new field wells

### 2. Sanity-Check Tank Parameters

Ensure STOIIP / GIIP makes sense:
- Compare to volumetric estimates from seismic/petrophysics
- Cross-check compressibility with regional analogs
- Verify Pb and Rs with lab PVT data

### 3. Capacity Assumptions

- Separator capacity typically 500–2000 m³/d (negotiate with vendor)
- Export line rates depend on tie-back diameter and material (erosion limits)
- Injection rates limited by injectivity index and wellhead availability

### 4. Forecast Horizon

- Plan for 5–10 year horizon for typical field life assessment
- Use sub-annual steps (monthly or quarterly) early; annual later
- Track both rate and cumulative to spot when field becomes marginal

---

See [USER_GUIDE.md](USER_GUIDE.md) for detailed workflows and [API_REFERENCE.md](API_REFERENCE.md) for tank and well equations.
