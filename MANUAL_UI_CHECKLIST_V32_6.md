# FieldNet v32.6 — manual live-UI checklist

Streamlit could not be installed in the build sandbox, so the live UI has **never been run** by the author. The 150+ automated tests cover the model, solver, tables, import/export, HTML/SVG output and the JS syntax of the canvas through a fake-Streamlit harness. Please run this list once (about 20 min) with `streamlit run app.py` and report any red flags.

## 1. Start-up
- [ ] App opens with no traceback; all tabs render.
- [ ] Load a template (Network tab). Canvas shows nodes, wells filled green (oil) / red (gas) / blue (water).

## 2. Network tab
- [ ] `Size −` / `Size +` buttons and the symbol-size slider shrink / grow symbols; links stay attached to ports.
- [ ] Batch editor: change a value, press Apply — layout updates; Constraints and Uptime tables edit and apply.
- [ ] Import: upload a JSON, YAML, XLSX and CSV produced by the Export section; values round-trip, booleans stay booleans.
- [ ] Well panel: minimum-rate expander (oil / water / gas / liquid) — a rate above the well capability gives 'below minimum rate'.

## 3. Forecast
- [ ] Run a forecast; pause / resume works; yearly and monthly charts use the same oil-green / gas-red / water-blue colours.
- [ ] Assumptions: set target RF and per-well EUR cap; plateau, tapering and duty-cycle message appear; calibration gives a recommended RF.

## 4. Development tab — Drainage strategy (new)
- [ ] Set well range, plateau target and (optional) economics; press **▶ Run drainage strategy study**.
- [ ] Table and chart show plateau rate, plateau years, RF and binding constraint per well count; a recommendation with the reason is displayed.
- [ ] HTML download opens offline and prints cleanly on A4.
- [ ] Setting an unreachable plateau target shows the 'unreachable' message, not a crash.

## 5. Material balance — History match (new)
- [ ] Reservoir tank, source *Measured history*: the *History match* expander appears.
- [ ] Fit in-place volume / aquifer / compressibility; check RMSE before vs after, quality, 95 % ranges and warnings.
- [ ] *Apply* writes the fitted values into the tank; re-running the forecast uses them.

## 6. Capacity choking
- [ ] Set a facility limit at ~50 % of natural rate: forecast holds it (choke or duty cycle).
- [ ] Set a limit at ~10 %: a message 'Capacity limit cannot be held by steady choking…' is shown and the lowest stable rate is reported.

## 7. Round 6 — editing, mask, tank, Eclipse
- [ ] Sidebar / Network tab **Start from a blank page** asks for confirmation, then the canvas is empty and the hint shows.
- [ ] Add 3 components from the canvas palette and drag them: nothing reloads; the canvas **Apply** button turns orange ("Apply N changes"), press it → green "Applied" and a green notice appears below the canvas.
- [ ] Select a well and change several values in the panel: only the panel refreshes (no flicker of other tabs); the orange **Apply changes** bar appears; press it → canvas and status refresh.
- [ ] Constraints, Batch editor, Uptime and Flowlines tables: type in several cells (no rerun), press the orange Apply button once; a coloured confirmation appears.
- [ ] **Mask** a well (toolbar button or panel checkbox), Apply, Solve: the well is greyed and absent from the results/forecast; unmask restores it.
- [ ] Tank: palette item is "Tank"; in the panel choose *Oil with gas cap*, set m = 0.5; the pressure falls more slowly in the forecast than with plain *Oil*.
- [ ] Prediction source → tank → *Read Eclipse binary results*: select `.SMSPEC` + `.UNSMRY` together, pick FPR (and FWCT/FGOR), press *Use as this tank's prediction table* then *Apply to tank*.
- [ ] Tabs: only six top-level tabs; no empty "Availability & downtime" tab.

## 8. Round 7 — palette, Darcy, schedule, pressure, checks
- [ ] Palette shows one **Tank**, one **Well**, one **Separator**; in the panel set the well to gas producer / water injector, and the separator to three-phase or stage.
- [ ] Well panel: tick **Compute the inflow from reservoir properties**; PI changes with permeability; *Horizontal* + lateral length 1000 m gives several times the vertical PI; lowering kv/kh lowers it.
- [ ] Well / injector with a tank assigned: no pressure input, caption names the tank; change the tank pressure and Apply: the well follows.
- [ ] Schedule: pick *Element type* = Well, event *Darcy: permeability*, set a date, add; run the forecast: PI drops/rises from that date. Repeat with a Tank event (target RF, aquifer) and a temperature event in °F on the Field profile.
- [ ] Tools → Model checks → *Input & unit consistency*: set a tubing ID of 3.5 → error naming "inch"; fix it → clean.

Report: screenshot + the browser/terminal traceback for anything that fails.
