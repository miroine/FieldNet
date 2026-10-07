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

Report: screenshot + the browser/terminal traceback for anything that fails.
