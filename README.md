# FieldNet v32.6

FieldNet is a Streamlit application for production-network modeling, reservoir material-balance forecasting, and field-development planning. It combines network hydraulics and well models with tank depletion, scheduling, and optimization tools.

> **Engineering use:** FieldNet is a screening and planning tool, not a substitute for validated engineering software or qualified review. Validate the fluid, reservoir, well, equipment, and correlation models for the intended operating envelope before relying on results.

## Features

- **Build and solve production networks:** use the interactive canvas to connect wells, injectors, tanks, separators, pipelines, and inline equipment. The app reports explicit solve status and validates the graph.
- **Model reservoirs and commingled production:** represent oil, dry-gas, and gas-condensate tanks; account for depletion, aquifers, tank communication, and wells draining multiple tanks.
- **Forecast and plan development:** run production forecasts, schedule drilling and parameter changes, inspect production KPIs, and compare development scenarios.
- **Analyze and optimize operations:** explore network capacity constraints, debottlenecking, well allocation, uncertainty, reliability, sensitivity, and nodal performance.
- **Work with fluids and units:** manage PVT fluid definitions, assign them to model elements, and use supported display-unit profiles.

See [CHANGELOG.md](CHANGELOG.md) for the v32.6 release details and version history.

## Requirements

- Python 3.11 is the Docker image's runtime baseline.
- Install the Python dependencies listed in [requirements.txt](requirements.txt).
- The network editor is served from the bundled frontend at `ui/fieldnet_canvas/build/`.

The dependency entries in `requirements.txt` specify minimum versions rather than a fully locked environment.

## Run locally

```powershell
git clone https://github.com/miroine/fieldnet.git
cd fieldnet
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

On macOS or Linux, activate the environment with `source .venv/bin/activate`.
The app opens at `http://localhost:8501`.

## Run with Docker

The repository [Dockerfile](Dockerfile) uses Python 3.11 and exposes Streamlit on port 8501.

```sh
docker build -t fieldnet .
docker run --rm -p 8501:8501 fieldnet
```

Then open `http://localhost:8501`.

## Tests

Install the dependencies first, then run the main regression suites:

```sh
python -m pytest -q tests/test_v30_audit.py tests/test_v31_prognosis.py
```

GitHub Actions runs these regression suites on pushes and pull requests. The optional browser-based editor test requires Playwright and a Chromium browser:

```sh
python tests/browser/run_editor_browser_test.py
```

## Project layout

| Path | Responsibility |
| --- | --- |
| `app.py` | Streamlit application entry point and workflow orchestration |
| `network/` | Network state, reservoirs, forecasts, data exchange, and run control |
| `physics/` | Fluid properties, well inflow/outflow, multiphase flow, and unit conversions |
| `solver/` | Network equations, steady-state solution, constraints, and diagnostics |
| `optimization/` | Allocation, scenario analysis, and field optimization |
| `ui/` | Streamlit views, canvas integration, charts, and frontend bundle |
| `tests/` | Python regression tests and browser-oriented editor checks |

## Documentation

- [User guide](USER_GUIDE.md) — workflows and UI navigation
- [API reference](API_REFERENCE.md) — model objects and solver interfaces
- [Architecture](ARCHITECTURE.md) — modules, data flow, and algorithms
- [Examples](EXAMPLES.md) — worked examples and demo field walkthrough
- [Troubleshooting](TROUBLESHOOTING.md) — common issues
- [Changelog](CHANGELOG.md) — release history

## License

FieldNet is licensed under the [MIT License](LICENSE). The MIT License permits use, modification, distribution, and commercial use, subject to its terms.
