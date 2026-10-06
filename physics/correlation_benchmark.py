"""Cross-correlation benchmark helpers for the six multiphase pressure-drop correlations.

``benchmark_table(cases)`` evaluates every correlation on the same reference cases and returns rows for a
spread table (UI-ready: plain dicts, DataFrame-ready). The spread is a measure of MODEL UNCERTAINTY between
correlations; it is NOT an error against measured data. No published measurement is encoded here (see
docs_correlation_validation.md for what is and is not checked).

Case dict keys (all optional except ``q_liq_m3d``): ``name``, ``q_liq_m3d`` (liquid rate at standard conditions,
sign = flow direction), ``length_m``, ``diameter_m``, ``roughness_m``, ``dz_m`` (elevation GAIN along the flow),
``pressure_bar`` (segment-inlet pressure used for PVT), ``temperature_c``, ``water_cut``, ``gor_sm3sm3``, ``api``,
``gas_sg``, ``extra_free_gas_sm3d``. Pressure drop is the loss along the flow direction (friction + gravity), as in
``physics.correlations``; the segment is evaluated as ONE step with PVT at the given pressure.
"""
from __future__ import annotations
import math

from physics.correlations import get_correlation, list_correlations
from physics.beggs_brill import beggs_brill_dp_bar
from physics.multiphase import homogeneous_dp_bar
from physics.hydraulics import G

CASE_DEFAULTS = {'length_m': 1000.0, 'diameter_m': 0.15, 'roughness_m': 4.5e-5, 'dz_m': 0.0, 'pressure_bar': 30.0,
                 'temperature_c': 60.0, 'water_cut': 0.0, 'gor_sm3sm3': 0.0, 'api': 35.0, 'gas_sg': 0.75, 'extra_free_gas_sm3d': 0.0}

# Reference cases: SYNTHETIC (chosen to cover the usual operating envelope), not taken from any publication.
REFERENCE_CASES = [
    {'name': 'Horizontal oil flowline, moderate GOR', 'q_liq_m3d': 2000.0, 'length_m': 5000.0, 'diameter_m': 0.2, 'dz_m': 0.0, 'pressure_bar': 30.0, 'water_cut': 0.2, 'gor_sm3sm3': 100.0},
    {'name': 'Horizontal wet-gas flowline', 'q_liq_m3d': 150.0, 'length_m': 8000.0, 'diameter_m': 0.25, 'dz_m': 0.0, 'pressure_bar': 80.0, 'water_cut': 0.1, 'gor_sm3sm3': 3000.0, 'gas_sg': 0.7},
    {'name': 'Vertical riser 100 m, oil + gas', 'q_liq_m3d': 1500.0, 'length_m': 100.0, 'diameter_m': 0.2, 'dz_m': 100.0, 'pressure_bar': 25.0, 'water_cut': 0.3, 'gor_sm3sm3': 120.0},
    {'name': 'Production tubing 2500 m (marched down: dz=+L)', 'q_liq_m3d': 1200.0, 'length_m': 2500.0, 'diameter_m': 0.1, 'dz_m': 2500.0, 'pressure_bar': 40.0, 'temperature_c': 90.0, 'water_cut': 0.1, 'gor_sm3sm3': 150.0},
    {'name': 'Uphill flowline 2 deg, high water cut', 'q_liq_m3d': 3000.0, 'length_m': 4000.0, 'diameter_m': 0.25, 'dz_m': 140.0, 'pressure_bar': 20.0, 'water_cut': 0.7, 'gor_sm3sm3': 60.0},
    {'name': 'Low-rate gas-lifted tubing', 'q_liq_m3d': 300.0, 'length_m': 2000.0, 'diameter_m': 0.1, 'dz_m': 2000.0, 'pressure_bar': 35.0, 'water_cut': 0.2, 'gor_sm3sm3': 80.0, 'extra_free_gas_sm3d': 60000.0},
    {'name': 'Single-phase oil (no gas)', 'q_liq_m3d': 2000.0, 'length_m': 1000.0, 'diameter_m': 0.2, 'dz_m': 0.0, 'pressure_bar': 30.0},
]


def _fn(name):
    n = str(name).lower().replace('-', '').replace(' ', '')
    if n.startswith('beggs'):
        return beggs_brill_dp_bar
    if n.startswith('homog'):
        return homogeneous_dp_bar
    return get_correlation(name)


def _call(fn, case):
    c = {**CASE_DEFAULTS, **case}
    return fn(c['q_liq_m3d'], c['length_m'], c['diameter_m'], c['roughness_m'], c['dz_m'], c['pressure_bar'], c['temperature_c'],
              c['water_cut'], c['gor_sm3sm3'], c['api'], c['gas_sg'], extra_free_gas_sm3d=c['extra_free_gas_sm3d'])


def colebrook_friction_factor(re, rel_rough):
    """Darcy friction factor: 64/Re (laminar, Re < 2300) else the Colebrook-White equation solved by fixed-point
    iteration (independent of ``physics.hydraulics`` which uses Swamee-Jain)."""
    if re <= 0:
        return 0.0
    if re < 2300.0:
        return 64.0 / re
    f = 0.02
    for _ in range(100):
        new = (-2.0 * math.log10(rel_rough / 3.7 + 2.51 / (re * math.sqrt(f)))) ** -2
        if abs(new - f) < 1e-14:
            break
        f = new
    return f


def darcy_weisbach_dp_bar(q_m3s, length_m, diameter_m, roughness_m, rho, mu_pa_s, dz_m=0.0):
    """Single-phase pressure loss [bar] (friction with Colebrook / Hagen-Poiseuille + gravity)."""
    area = math.pi * diameter_m ** 2 / 4.0; v = abs(q_m3s) / area
    re = rho * v * diameter_m / mu_pa_s
    f = colebrook_friction_factor(re, roughness_m / diameter_m)
    fr = f * length_m / diameter_m * rho * v * v / 2.0
    return ((fr if q_m3s >= 0 else -fr) + rho * G * dz_m) / 1e5


def benchmark_table(cases=None, correlations=None, reference='Beggs-Brill'):
    """Compare pressure drop of the correlations on each case.

    Returns a list (one dict per case) with ``Case``, one ``'<name> dp [bar]'`` column per correlation (None if the
    correlation raised; the message goes to ``'<name> error'``), and ``Min / Max / Mean dp [bar]``,
    ``Spread [%]`` = (max - min) / mean x 100 over the correlations that returned a finite value,
    ``Max dev from mean [%]``, ``Dev vs <reference> [%]`` per correlation, ``n_correlations`` and the main inputs
    (``q_liq_m3d``, ``length_m``, ``diameter_m``, ``dz_m``). Spread is model-to-model uncertainty only."""
    cases = REFERENCE_CASES if cases is None else cases
    names = list(correlations) if correlations else list_correlations()
    rows = []
    for i, case in enumerate(cases):
        c = {**CASE_DEFAULTS, **case}
        row = {'Case': case.get('name', f'case {i + 1}'), 'q_liq_m3d': c['q_liq_m3d'], 'length_m': c['length_m'], 'diameter_m': c['diameter_m'], 'dz_m': c['dz_m']}
        vals = {}
        for n in names:
            try:
                dp, pr = _call(_fn(n), case)
                if not math.isfinite(dp):
                    raise ValueError('non-finite dp')
                vals[n] = float(dp); row[f'{n} dp [bar]'] = float(dp)
                row[f'{n} regime'] = pr.get('flow_regime'); row[f'{n} holdup'] = pr.get('liquid_holdup')
            except Exception as ex:                       # report, do not hide, a failing correlation
                row[f'{n} dp [bar]'] = None; row[f'{n} error'] = f'{type(ex).__name__}: {ex}'
        v = list(vals.values())
        if v:
            mean = sum(v) / len(v)
            row.update({'Min dp [bar]': min(v), 'Max dp [bar]': max(v), 'Mean dp [bar]': mean, 'n_correlations': len(v),
                        'Spread [%]': (max(v) - min(v)) / abs(mean) * 100.0 if mean else None,
                        'Max dev from mean [%]': max(abs(x - mean) for x in v) / abs(mean) * 100.0 if mean else None})
            ref = vals.get(reference)
            for n, x in vals.items():
                row[f'Dev vs {reference} [%]' if n == reference else f'{n} vs {reference} [%]'] = ((x - ref) / abs(ref) * 100.0) if ref else None
        else:
            row.update({'n_correlations': 0, 'Spread [%]': None})
        rows.append(row)
    return rows
