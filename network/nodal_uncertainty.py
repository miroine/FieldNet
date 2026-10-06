"""Uncertainty on nodal-analysis inputs: what-if overrides and a Monte-Carlo fan of IPR / VLP curves for one well.

Samples are Latin-hypercube draws from ``network.uncertainty`` (same distributions as the field Monte Carlo). Every sample is a full IPR + VLP
evaluation with the same single-well model (``physics.well_model``) the network solver uses, so a sample's operating point is exactly what
the network would deliver for that input set at that wellhead pressure.

Probability convention in the results: P90 = low case (90 % chance of at least this), P10 = high case. ``pick_realisation`` maps a percentile
slider to the sample whose operating rate sits at that percentile of the distribution."""
from __future__ import annotations
import copy, math
import numpy as np, pandas as pd
from network.uncertainty import UncertainParameter, MonteCarloConfig, sample_parameters
from physics.well_model import well_settings, ipr_pwf, vlp_bhp, solve_well_rate, rate_capacity

# key -> (label, unit, kind)  kind: 'abs' absolute value, 'mult' multiplier on the base well value(s)
CATALOGUE = {
    'reservoir_pressure_bar': ('Reservoir pressure', 'bar', 'abs', 0.0, 1000.0),
    'productivity': ('Productivity (PI / qmax / gas C)', 'x', 'mult', 0.05, 5.0),
    'skin': ('Skin', '-', 'abs', -7.0, 60.0),
    'water_cut': ('Water cut', '-', 'abs', 0.0, 0.99),
    'gor_sm3sm3': ('GOR', 'Sm3/Sm3', 'abs', 0.0, 20000.0),
    'whp': ('Wellhead pressure', 'bar', 'abs', 1.0, 500.0),
    'vlp_dp_multiplier': ('Tubing pressure-drop multiplier', 'x', 'abs', 0.3, 3.0),
    'tubing_roughness_m': ('Tubing roughness', 'm', 'abs', 1e-6, 5e-3),
    'tubing_id_m': ('Tubing ID', 'm', 'abs', 0.03, 0.4),
    'api': ('Oil gravity', 'API', 'abs', 10.0, 60.0),
    'temperature_c': ('Wellhead temperature', 'C', 'abs', 0.0, 200.0),
}
PRODUCTIVITY_KEYS = ('pi_m3d_bar', 'qmax_m3d', 'gas_c_sm3d_bar2n')


def base_value(prm, key, whp):
    if key == 'whp': return float(whp)
    if key == 'productivity': return 1.0
    if key == 'vlp_dp_multiplier': return float(prm.get(key, 1.0) or 1.0)
    d = {'reservoir_pressure_bar': 200.0, 'skin': 0.0, 'water_cut': 0.2, 'gor_sm3sm3': 100.0, 'tubing_roughness_m': 4.5e-5, 'tubing_id_m': 0.0762, 'api': 35.0, 'temperature_c': 70.0}[key]
    try: return float(prm.get(key, d))
    except (TypeError, ValueError): return d


def default_spec(prm, whp, key):
    """Sensible triangular low/mode/high around the current value (users edit them)."""
    b = base_value(prm, key, whp); lab, unit, kind, lo, hi = CATALOGUE[key]
    rel = {'reservoir_pressure_bar': (0.93, 1.0, 1.04), 'productivity': (0.6, 1.0, 1.3), 'gor_sm3sm3': (0.8, 1.0, 1.3), 'whp': (0.85, 1.0, 1.2), 'vlp_dp_multiplier': (0.85, 1.0, 1.2),
           'tubing_roughness_m': (0.5, 1.0, 4.0), 'tubing_id_m': (0.97, 1.0, 1.02), 'api': (0.94, 1.0, 1.06), 'temperature_c': (0.9, 1.0, 1.1)}
    if key == 'skin': l, m, h = b - 2.0, b, b + 6.0
    elif key == 'water_cut': l, m, h = max(b - 0.1, 0.0), b, min(b + 0.15, 0.95)
    else: r = rel[key]; l, m, h = b * r[0], b, b * r[2]
    return {'key': key, 'label': lab, 'unit': unit, 'dist': 'triangular', 'low': float(min(max(l, lo), hi)), 'mode': float(min(max(m, lo), hi)), 'high': float(min(max(h, lo), hi))}


def to_parameters(specs):
    out = []
    for s in specs:
        lo, hi = CATALOGUE[s['key']][3:5]
        out.append(UncertainParameter(name=s['key'], path=s['key'], distribution=s.get('dist', 'triangular'), low=float(s['low']), mode=float(s.get('mode', s['low'])), high=float(s['high']),
                                      mean=float(s.get('mode', (s['low'] + s['high']) / 2)), std=float(s.get('std', (s['high'] - s['low']) / 4 or 1e-9)), operation='set', physical_min=lo, physical_max=hi))
    return out


def apply_overrides(prm, whp, values):
    """New (params, whp) with the override values applied (a copy; the model is never touched)."""
    p = copy.deepcopy(prm); w = float(whp)
    for k, v in (values or {}).items():
        if k == 'whp': w = float(v)
        elif k == 'productivity':
            for pk in PRODUCTIVITY_KEYS:
                if p.get(pk) not in (None, ''): p[pk] = float(p[pk]) * float(v)
        else: p[k] = float(v)
    return p, w


def q_grid(prm_list, points=41):
    cap = max(max(rate_capacity(well_settings(p)) for p in prm_list), 1.0)
    return np.linspace(0.0, cap, points)


def curves(prm, whp, q):
    ws = well_settings(prm)
    return np.array([ipr_pwf(float(x), ws) for x in q]), np.array([vlp_bhp(float(x), whp, ws)[0] for x in q])


def operating_point(prm, whp):
    ws = well_settings(prm); q, st = solve_well_rate(whp, ws); pw = ipr_pwf(q, ws) if q > 0 else float('nan')
    oil = q * (1 - ws['water_cut'])
    return {'q_liq': q, 'q_oil': oil, 'q_gas': oil * ws['gor'], 'q_water': q * ws['water_cut'], 'bhp': pw, 'status': st}


def run(prm, whp, specs, n=60, seed=1701, points=33, progress=None):
    """Monte-Carlo fan. Returns dict(samples, q, ipr[n,k], vlp[n,k], envelope, summary, sensitivity)."""
    if not specs: raise ValueError('define at least one uncertain input')
    cfg = MonteCarloConfig(samples=int(n), seed=int(seed), parameters=to_parameters(specs)); draws = sample_parameters(cfg)
    trial = [apply_overrides(prm, whp, d) for d in draws]
    q = q_grid([t[0] for t in trial], points); ipr = np.zeros((len(draws), len(q))); vlp = np.zeros_like(ipr); rows = []
    for i, (d, (p, w)) in enumerate(zip(draws, trial)):
        ipr[i], vlp[i] = curves(p, w, q); op = operating_point(p, w); rows.append({**d, **op, 'whp_used': w})
        if progress and not progress(i + 1, len(draws)): break
    s = pd.DataFrame(rows); k = len(s); ipr, vlp = ipr[:k], vlp[:k]
    env = {'q [m3/d]': q}
    for tag, a in (('IPR', ipr), ('VLP', vlp)):
        for pn, pc in (('P90', 10), ('P50', 50), ('P10', 90)): env[f'{tag} {pn}'] = np.percentile(a, pc, axis=0)   # P90 = low pressure side of the band
    q_s = s['q_liq'].values; summ = {}
    for name, col in (('Liquid rate [m3/d]', 'q_liq'), ('Oil rate [m3/d]', 'q_oil'), ('Gas rate [Sm3/d]', 'q_gas'), ('Flowing BHP [bar]', 'bhp')):
        v = s[col].dropna().values
        summ[name] = {'P90': float(np.percentile(v, 10)), 'P50': float(np.percentile(v, 50)), 'P10': float(np.percentile(v, 90)), 'Mean': float(v.mean())} if len(v) else {}
    summ['P(flowing)'] = float((s['q_liq'] > 0).mean())
    sens = []
    if k > 5 and q_s.std() > 0:
        from scipy.stats import spearmanr
        for sp in specs:
            r = spearmanr(s[sp['key']], q_s).correlation if s[sp['key']].std() > 0 else 0.0
            sens.append({'Input': sp['label'], 'Rank correlation with liquid rate': float(0.0 if r != r else r)})
        sens = sorted(sens, key=lambda x: -abs(x['Rank correlation with liquid rate']))
    return {'samples': s, 'q': q, 'ipr': ipr, 'vlp': vlp, 'envelope': pd.DataFrame(env), 'summary': summ, 'sensitivity': pd.DataFrame(sens), 'whp': float(whp), 'specs': specs}


def pick_realisation(res, percentile):
    """Index of the sample whose liquid rate is closest to the given probability-of-exceedance percentile (P10 = high case, P90 = low case)."""
    s = res['samples']; target = np.percentile(s['q_liq'].values, 100 - float(percentile))
    return int((s['q_liq'] - target).abs().idxmin())
