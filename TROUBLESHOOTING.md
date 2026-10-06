# FieldNet Troubleshooting Guide

Common issues, diagnosis steps, and solutions.

## Table of Contents

1. [App Won't Start](#app-wont-start)
2. [Solve Fails or Stalls](#solve-fails-or-stalls)
3. [Unexpected Solve Results](#unexpected-solve-results)
4. [Forecast Issues](#forecast-issues)
5. [Canvas Editor Problems](#canvas-editor-problems)
6. [Data Import/Export](#data-importexport)
7. [Streamlit Cloud Deployment](#streamlit-cloud-deployment)
8. [Performance Issues](#performance-issues)

---

## App Won't Start

### Error: `ModuleNotFoundError: No module named 'streamlit'`

**Cause:** Missing dependencies.

**Solution:**
```bash
pip install -r requirements.txt
```

Ensure you're using Python 3.9+ and a fresh virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # macOS/Linux
# or: venv\Scripts\activate  (Windows)
pip install -r requirements.txt
streamlit run app.py
```

### Error: `ImportError: cannot import name 'DEFAULT_VLP_SEGMENTS'`

**Cause:** (v30.2 deployment bug) Stale module cache. Streamlit re-deployed while new `physics/well_model.py` was incompatible with old `physics/vlp.py`.

**Solution (Local):**
```bash
streamlit cache clear
streamlit run app.py
```

**Solution (Streamlit Cloud):**
- Redeploy the entire repo in **one commit** (not piecemeal uploads)
- Ensure all changed files are committed at once:
  ```bash
  git add -A
  git commit -m "v30.2 merge: module imports fixed"
  git push
  ```

### Error: `AttributeError: 'NoneType' object has no attribute 'get'`

**Cause:** Canvas payload is malformed or network state is corrupted.

**Solution:**
1. Refresh the browser (Ctrl+R or Cmd+R)
2. Clear Streamlit cache: `streamlit cache clear`
3. If persists, delete `.streamlit/` folder and restart app

---

## Solve Fails or Stalls

### Error: `BOUNDARY_WITHOUT_PRESSURE`

**Cause:** A separator, sink, or export component exists but has no pressure specified.

**Solution:**
1. In the property panel, click on the separator/sink
2. Verify **pressure_bar** is set (e.g., 10 bar for separator)
3. Re-solve

### Error: `LOOP_ELEVATION_MISMATCH`

**Cause:** The network contains a closed loop (cycle) where elevation changes don't sum to zero.

**Example:**
```
Node A (0 m) → Node B (+100 m) → Node C → Node A (−90 m)
Total elevation change: +100 − 90 = +10 m ≠ 0
```

This creates a fictitious hydrostatic circulation that stalls the solver.

**Solution:**
1. Check the network for closed loops (use graph visualization or manual inspection)
2. Adjust elevation_change_m on one edge so loop sum = 0
3. Or break the loop by removing one pipeline

### Error: Solver doesn't converge (status: FAILED, iterations: 200)

**Causes:**

a) **Stiff well (high PI, high max rate):**
   - Check well IPR parameters: PI > 100 m³/d/bar is stiff
   - Reduce PI or add skin to moderate the sensitivity

b) **Dead well (no IPR/VLP intersection):**
   - Check tank pressure: is it below wellhead pressure?
   - Increase tank initial pressure or add aquifer support
   - Check well min BHP: set to 0 or lower value

c) **Looped network without elevation fix:**
   - See LOOP_ELEVATION_MISMATCH above

d) **Separator capacity too tight:**
   - Solver chokes wells but can't find stable equilibrium
   - Temporarily disable capacity constraint: set max_rate_m3d = 10000

**Diagnostics:**
```bash
streamlit run app.py --logger.level=debug
```

Check the debug logs for "STIFF" or "NO_INTERSECTION" warnings.

### Solver stalls at 3000 evaluations (back-pressure sweep)

**Cause:** Well very close to shut-in condition, or equipment (pump, choke) has a flat curve.

**Solution:**
1. Check pump min_head_bar: should not be 0 (curve stays flat above run-out)
2. Check choke opening: if open < 10%, friction can be extreme
3. Reduce max iterations temporarily to see if stalled or converging slowly:
   ```python
   # In solver call, add:
   max_iterations=50  # reduce to check
   ```

---

## Unexpected Solve Results

### Problem: "No oil produced" (all wells shut in)

**Diagnosis:**

In **Network Results** → **Pressure & Flow**, well status shows "shut in" with reason.

**Causes:**

a) **Tank pressure too low:** Tank pressure < wellhead pressure
   - Check tank initial reservoir_pressure_bar (set to at least 50+ bar above wellhead)
   - Add an aquifer: aquifer_pi_m3d_bar > 0

b) **Well condition not met:** Well doesn't have a valid IPR/VLP intersection at positive rate
   - Reduce wellhead pressure (10 bar separator is OK; 20+ bar separator is tight)
   - Increase well PI
   - Reduce GOR (lowers VLP curve)

c) **Minimum rate enforcement:** Rate is calculated but < 5 m³/d (minimum shut-in threshold)
   - Check tank pressure: is it marginal?
   - Check well flowing-pressure target: reduce to push more flow

**Solution Example:**

Change tank from:
```python
reservoir_pressure_bar: 100  # Too low!
```

To:
```python
reservoir_pressure_bar: 250  # Reasonable starting point
aquifer_pi_m3d_bar: 5.0      # Add support
```

### Problem: Rates seem too high (e.g., 1000+ m³/d per well)

**Cause:** Tank pressure over-estimated or separator pressure under-estimated.

**Check:**
- Tank reservoir_pressure_bar: typical oil ~200–250 bar
- Separator pressure_bar: typical ~10 bar (not 0 or negative)
- Well PI: typical 20–100 m³/d/bar

**Example Fix:**

```python
# Before
tank: 400 bar (unrealistic)
separator: 5 bar (too low)
well PI: 500 m³/d/bar (too high)

# After
tank: 250 bar (realistic)
separator: 10 bar (realistic)
well PI: 50 m³/d/bar (realistic)
```

### Problem: Water cut doesn't change (stays at initial value)

**Cause:** Tank water-breakthrough parameters not set, or RF is below breakthrough threshold.

**Check:**
```python
water_breakthrough_rf: 0.05  # When water arrives
max_water_cut: 0.9           # Final water cut
rf_at_max_water_cut: 0.40    # When max WC is reached
```

If RF stays below 5%, water won't breakthrough. Run a longer forecast (20+ years) or reduce breakthrough_rf to 0.01.

### Problem: GOR doesn't rise (stays at Rsi)

**Cause:** Tank pressure never drops below bubble point.

**Check:**
- Bubble point: should be below initial tank pressure (e.g., Pb = 150 bar, Pi = 250 bar)
- Aquifer PI: if too large, tank pressure stays high and never crosses Pb
- Forecast length: run for 10+ years to see significant depletion

**Example Fix:**

```python
bubble_point_bar: 150  # Set below Pi
gor_rise_factor: 3.0   # Ensure multiplier > 0
```

Run a forecast. GOR should start rising after tank pressure drops below 150 bar.

---

## Forecast Issues

### Error: `ValueError: Cannot step a forecast with NaN pressure`

**Cause:** Model contains NaN or Inf values (corrupted by data entry or import).

**Solution:**
1. Go to **Data & QA** → **Model Assurance**
2. Check for FAIL or REVIEW status
3. Export raw data and scan for NaN/Inf (look for blank cells or "inf")
4. Fix in the property panel or CSV, re-import

### Problem: Forecast takes 30+ seconds (slow)

**Causes:**

a) **Long forecast horizon (20+ years) with many steps:**
   - Normal for a 20-year, 1-month-step forecast (240 steps)
   - Reduce step size: use quarterly (3 months) instead of monthly

b) **Stiff well (high PI):**
   - Each solve iteration takes longer
   - Reduce PI or add skin

c) **No warm-start from previous forecast:**
   - First forecast is slower; subsequent uses warm-start
   - Re-running same forecast is faster

**Optimization:**
```python
# Use quarterly steps (3 months) instead of monthly
report_step_months = 3  # 80 steps instead of 240 for 20 years
```

Reduces forecast time from 30 s → 5 s.

### Problem: Cumulative oil is zero or very small

**Causes:**

a) **Wells shut in:** Check solver diagnostics (see above)
b) **Tank depleted instantly:** Very small STOIIP or high production rate
c) **Separator capacity too low:** Choked wells produce nothing

**Check:**
```python
stoiip_sm3 = 20e6  # 20 MSm³
expected_cum_oil = stoiip_sm3 * 0.30  # expect 6–15% RF for typical forecast
```

If cumulative is much less, wells are likely shut in or choked.

---

## Canvas Editor Problems

### Problem: Component disappears after clicking it

**Cause:** (v30 bug, fixed in v30.1) Streamlit replay overwrote canvas state.

**Solution:** Upgrade to v30.1 or later.

### Problem: Drag-to-connect doesn't work

**Cause:** Browser doesn't support modern React, or canvas JavaScript failed to load.

**Solution:**
1. Refresh the browser (Ctrl+R)
2. Open browser developer console (F12) and check for errors
3. Try a different browser (Chrome/Edge recommended)

### Problem: Changes to property panel don't take effect

**Cause:** Component is no longer selected, or panel is showing stale data.

**Solution:**
1. Click the component again to re-select
2. Verify the component ID matches (top of panel should show ID)
3. Click elsewhere to deselect, then click again

### Problem: Can't delete a component or edge

**Cause:** UI doesn't support delete yet (v31). Must edit JSON or use export/import workflow.

**Solution:**
1. Export the network as JSON
2. Edit in a text editor: find the node/edge and delete the line
3. Re-import

---

## Data Import/Export

### Error: CSV import rejected with "NaN in row 3"

**Cause:** Data has blank cells or invalid numbers.

**Solution:**
1. Open the CSV in Excel
2. Find row 3, look for blank cells
3. Fill with a valid default (e.g., 0, 1, or use right-click → fill down)
4. Re-save as CSV (not .xlsx)
5. Re-import

### Error: JSON export has numpy data types

**Cause:** (rare, v30.2+ fixed) Solver output includes numpy.int64 instead of Python int.

**Solution:** Downgrade scipy or update to latest FieldNet version.

### Problem: Exported CSV loses parameter values

**Cause:** Old v29 interchange format doesn't include all v31 tank fields.

**Solution:**
1. Export as JSON (complete)
2. Or manually re-enter tank parameters after importing CSV

---

## Streamlit Cloud Deployment

### Error: "Stale module" or "cannot import NAME after deploy"

**Cause:** (v30.2) Streamlit Cloud redeployed mid-upload.

**Solution:**

**One-time fix:**
```bash
git add -A
git commit -m "Fix: upload all changed files in one commit"
git push
```

**For future:** Always push completed changes in one commit.

### App loads but solver tab shows error

**Cause:** `requirements.txt` missing or pinned to wrong version.

**Solution:**
1. Ensure `requirements.txt` exists at repo root
2. Include all key packages:
   ```
   streamlit>=1.0
   scipy>=1.9
   pandas>=1.3
   plotly>=5.0
   numpy>=1.20
   ```
3. Commit and push

### Streamlit takes 30s to load

**Cause:** Imports are slow (large dependencies).

**Solution:**
1. Move heavy imports inside functions (lazy load)
2. Use `@st.cache_resource` for expensive computations
3. Check if Streamlit version is latest: `pip install --upgrade streamlit`

---

## Performance Issues

### Entire app becomes unresponsive

**Cause:** Forecast or optimization running in blocking loop.

**Solution:**
1. Open browser developer console (F12)
2. Look for "Solver running" message
3. Wait for it to complete (1–30 seconds typical)
4. If stuck longer, refresh browser and try a simpler model

### Charts take long to render

**Cause:** Large forecast (1000+ timesteps) or many wells.

**Solution:**
1. Reduce forecast length: 20 years → 10 years
2. Reduce step count: monthly → quarterly
3. Use fewer wells in scenario comparison

### Streamlit cache not clearing

**Cause:** Cached decorator @st.cache_data is holding old results.

**Solution:**
```bash
streamlit cache clear
# or restart the app:
streamlit run app.py
```

---

## Getting Help

If your issue isn't listed:

1. **Check [AUDIT_V30.md](AUDIT_V30.md)** for known v30 fixes
2. **Review [USER_GUIDE.md](USER_GUIDE.md)** for workflows
3. **Check [API_REFERENCE.md](API_REFERENCE.md)** for parameter ranges
4. **Export model as JSON** and share (without sensitive data) with developer
5. **Run in debug mode:**
   ```bash
   streamlit run app.py --logger.level=debug 2>&1 | tee debug.log
   ```
   Attach `debug.log` to bug report

---

**Screening-level tool disclaimer:** FieldNet is an independent engineering prototype for planning and screening. Validate all results against offset data and lab measurements before making operational decisions.
