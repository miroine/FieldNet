# FieldNet v31 — Production Prognosis Tool

A Streamlit application for production forecasting, drainage strategy optimization, and field development planning in oil and gas reservoirs. FieldNet combines network flow analysis with reservoir material balance to recommend optimal well counts, predict field performance, and evaluate capacity constraints.

## Quick Start

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd fieldnet

# Install dependencies
pip install -r requirements.txt

# Run the app locally
streamlit run app.py
```

### First Steps

1. **Load or build a network** using the interactive canvas editor
2. **Define reservoir tanks** with fluid phase and in-place volumes
3. **Assign wells to tanks** by dragging tanks onto wells
4. **Solve the network** to establish current production state
5. **Run a forecast** to predict field performance over time
6. **Plan development** with drilling schedules and well addition timing
7. **Optimize well count** using the scenario comparison tool

## Key Features

### Network Modeling
- **Interactive canvas editor** with drag-to-connect workflows
- **Component palette**: wells, injectors, separators, pipelines, chokes, pumps, compressors, reservoir tanks
- **Real-time graph validation** with explicit solve status (UNSOLVED / SOLVING / SOLVED / FAILED)
- **Full topology support**: looped networks, multi-phase flow, parallel connections
- **Revision-based state management** prevents Streamlit replay overwrites

### Reservoir Tanks & Depletion
- **Material balance** for oil, dry gas, and gas condensate
- **Fluid evolution** with water breakthrough curves and GOR rise below bubble point
- **Aquifer support** for voidage replacement in water injectors
- **Tank pressure propagation** into well equations throughout the network
- **Tank-to-well assignment** via drag-and-drop in the editor (not via pipeline)

### Production Forecasting
- **Automatic depletion sub-stepping** prevents pressure overshoots over large report steps
- **KPI tracking**: peak rate, plateau, cumulative, recovery factor, final water cut
- **Multi-well charting** with liquid rate, gas rate, water cut, GOR per well
- **Scenario comparison** with overlaid profiles
- **Warm-start optimization** (dogbox solver) for 7× faster forecasts

### Well Optimization
- **IPR models**: Vogel (oil), gas backpressure (gas), Fetkovich (water injection)
- **VLP correlations**: Beggs–Brill (oil wells), Homogeneous (gas wells), customizable
- **Lift assistance**: gas lift injection, ESP head curves with affinity laws
- **Rate controls**: maximum liquid rate, minimum flowing pressure, choked rate limits
- **Stable intersection selection** using GAP-style well conventions

### Development Planning
- **Gantt-chart scheduling** with drilling order, rig availability, "not before" dates
- **First-oil timeline** and facility-constrained KPI comparison
- **Well-count study** using marginal-oil analysis (7 scenarios)
- **What-if scenarios**: separator capacity, injection on/off, pressure limits

### Network Constraints
- **Capacity enforcement**: separator liquid, export gas, connection rate limits
- **Pro-rata choking** upstream wells to respect bottleneck limits
- **Debottleneck analysis** showing capacity vs. gain trade-offs
- **Well caps and minimum pressures** editable in one constraints table

## Technical Architecture

FieldNet is organized into modular layers:

- **network/**: Graph topology, tank material balance, solvers
- **physics/**: PVT black-oil model, IPR correlations, VLP, friction, chokes
- **solver/**: Steady-state network solver (SciPy), warm-start optimization, convergence
- **ui/**: Streamlit app layout, canvas editor, interactive charts with Plotly
- **optimization/**: Scenario comparison, well-count study, development scheduling
- **tests/**: Unit tests (244 Python tests pass), browser editor tests (25/25 Chromium)

## Latest Changes (v31)

- **Tank-based production forecasts** with automatic depletion sub-stepping
- **Reservoir pressure** applied to all linked wells in every solver path
- **7-scenario well-count study** with recommended producer count (marginal-oil rule)
- **Production prognosis charts**: liquid rate, gas, water cut, GOR, cumulative oil per well
- **7-workflow app layout** with consistent color-blind-validated palette (oil=aqua, gas=orange, water=blue)
- **Dogbox least-squares solver** for 5–7× faster forecasts
- **Realistic demo field** with aquifer, gas lift, capacity limits, water injection

## Documentation

- **[USER_GUIDE.md](USER_GUIDE.md)** — Step-by-step workflows and UI navigation
- **[API_REFERENCE.md](API_REFERENCE.md)** — Network model, tank properties, solver API
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — Module organization, data flow, key algorithms
- **[EXAMPLES.md](EXAMPLES.md)** — Worked examples and demo field walkthrough
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — Common issues and solutions
- **[CHANGELOG.md](CHANGELOG.md)** — Version history and release notes

## Deployment

### Streamlit Cloud

1. Push code to GitHub
2. Connect repo to Streamlit Cloud
3. Ensure `requirements.txt` includes all dependencies
4. Set **one deployment** per commit (stale modules cause import errors if deployed mid-upload)

### Docker

```dockerfile
FROM python:3.10
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app.py"]
```

### Local Development

```bash
streamlit run app.py --logger.level=debug
```

The app auto-reloads on file changes. Use the browser's developer console (F12) for frontend errors in the canvas editor.

## Known Limitations

- **PVT model** is a screening black-oil with fixed 150 bar bubble point and 120 Sm³/Sm³ Rsb; replace with tuned PVT for field studies
- **Gas networks** use liquid-rate formulation (screening only); compressors are ratio-based
- **Minimum-rate shut-in** is a rule applied after solve, not a mixed-integer decision
- **Gas-lift injection** is an input, not an allocation variable in optimization

## Requirements

- Python 3.9+
- Streamlit ≥ 1.0
- SciPy (sparse/dense matrix solvers)
- Pandas, NumPy
- Plotly (charts)
- React / TypeScript (canvas editor frontend, built to `ui/fieldnet_canvas/build/`)

See `requirements.txt` for pinned versions.

## Testing

```bash
# Run unit tests
pytest tests/test_v30_audit.py tests/test_v31_prognosis.py

# Browser-based editor tests (requires Playwright & Chromium)
python tests/browser/run_editor_browser_test.py
```

## Contributing

1. Create a feature branch
2. Add tests in `tests/`
3. Run the full test suite
4. Submit a PR with description of changes
5. Ensure one commit per deployment to Streamlit Cloud (avoid stale module imports)

## License

[Your license here]

## Support

For issues, feature requests, or questions:
- Open an issue on GitHub
- Check [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common problems
- Review [EXAMPLES.md](EXAMPLES.md) for usage patterns
- See [AUDIT_V30.md](AUDIT_V30.md) for detailed v30 findings and fixes

---

**Made by Merouane Hamdani — For non-commercial use — Independent engineering prototype.**

FieldNet remains a screening-level planning tool. Beggs–Brill/PVT/reservoir/equipment implementations must be independently validated for the intended operating envelope before operational decisions.
