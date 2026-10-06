"""Reliability-weighted production prognosis (Monte-Carlo uptime applied to unconstrained rate profiles).

SCREENING-LEVEL. Takes a deterministic (unconstrained-by-downtime) rate profile per well, e.g. from
``network.forecast.run_forecast`` or any rate series, and applies random daily up/down states per well plus an optional
common system (host/export) state. Two downtime models:

* ``mode='bernoulli'``: every day each unit is independently up with probability = availability. Gives the right mean but
  UNDERSTATES the spread of real outages (no clustering of downtime).
* ``mode='renewal'``: alternating exponential up (MTBF) / down (MTTR) periods using ``network.reliability_v24``; each
  unit needs ``{'mtbf_days','mttr_days'}`` (or a plain availability plus ``default_mttr_days``). Realisations start in
  the up state, so the realised mean uptime is slightly above the steady-state availability for short horizons.

Percentile convention (as ``network.risk_profiles``): P90 = LOW case (10th percentile of values), P10 = HIGH case
(90th percentile). Results are deterministic for a given ``seed``. Not modelled: rate rescheduling after repair,
deferred-production catch-up, planned shutdown campaigns (use ``ReliabilitySpec.planned_outages`` via renewal mode only
for the specs you pass in), correlated well failures other than the common system state, and the impact of downtime on
reservoir depletion (cumulative is simply deferred, not recovered later).
"""
from __future__ import annotations
from datetime import datetime
import numpy as np

_COLS = {'oil': 'Oil [m3/d]', 'water': 'Water [m3/d]', 'gas': 'Gas [Sm3/d]', 'liquid': 'Liquid [m3/d]'}
SYSTEM_KEY = '__system__'


def extract_rate_series(forecast, variable='oil'):
    """Return ``(dates|None, days, {well: rates})`` from a ``run_forecast`` result, or from a dict
    ``{'days': [...] | 'dates': [...], 'rates': {well: [...]}}``. Rates are step averages starting at each date."""
    if 'rates' in forecast:
        rates = {k: np.asarray(v, float) for k, v in forecast['rates'].items()}
        dates = forecast.get('dates')
        if forecast.get('days') is not None: days = np.asarray(forecast['days'], float)
        elif dates is not None:
            d0 = datetime.fromisoformat(str(dates[0])[:10]); days = np.array([(datetime.fromisoformat(str(d)[:10]) - d0).days for d in dates], float)
        else: days = np.arange(len(next(iter(rates.values()))), dtype=float)
        return dates, days, rates
    rows = forecast.get('wells') or []
    col = _COLS.get(variable, variable)
    dates = sorted({r['Date'] for r in rows}); idx = {d: i for i, d in enumerate(dates)}
    d0 = datetime.fromisoformat(str(dates[0])[:10]); days = np.array([(datetime.fromisoformat(str(d)[:10]) - d0).days for d in dates], float)
    rates = {}
    for r in rows:
        w = r.get('Well ID', r.get('Well')); v = r.get(col)
        if v is None: raise KeyError(f"forecast well rows lack column {col!r}")
        rates.setdefault(w, np.zeros(len(dates)))[idx[r['Date']]] = max(float(v), 0.0)
    return dates, days, rates


def _intervals(days):
    days = np.asarray(days, float)
    if len(days) == 1: return np.array([1.0])
    dt = np.diff(days); return np.append(dt, dt[-1])


def _spec(av, default_mttr):
    """-> (availability, mtbf, mttr)"""
    if isinstance(av, dict):
        mt, mr = float(av['mtbf_days']), float(av['mttr_days']); return mt / (mt + mr), mt, mr
    a = float(av)
    if not 0.0 <= a <= 1.0: raise ValueError('availability must be within [0, 1]')
    mr = float(default_mttr); mt = mr * a / (1.0 - a) if a < 1.0 else float('inf')
    return a, mt, mr


def _daily_states(av, n_samples, n_days, mode, rng, default_mttr, key):
    a, mt, mr = _spec(av, default_mttr)
    if a >= 1.0: return np.ones((n_samples, n_days), bool)
    if a <= 0.0: return np.zeros((n_samples, n_days), bool)
    if mode == 'bernoulli':   # a dict given in bernoulli mode contributes its steady-state availability
        return rng.random((n_samples, n_days)) < a
    from network.reliability_v24 import ReliabilitySpec, _simulate_component_durations
    spec = ReliabilitySpec(str(key), mt, mr); dur = [1.0] * n_days; out = np.empty((n_samples, n_days), bool)
    for i in range(n_samples):
        out[i], _ = _simulate_component_durations(spec, dur, rng)
    return out


def uptime_weighted_profiles(forecast, availability, n_samples=500, seed=1234, mode='bernoulli', system_availability=None,
                             variable='oil', default_mttr_days=5.0, keep_samples=False):
    """Monte-Carlo uptime applied to rate profiles -> system-level P90/P50/P10/mean rate and cumulative.

    ``forecast``: ``run_forecast`` result (uses its ``wells`` rows and ``variable`` in oil|water|gas|liquid) or
    ``{'days'|'dates', 'rates': {well: series}}``. ``availability``: float for all wells, or ``{well: float |
    {'mtbf_days','mttr_days'}}`` (wells missing from the dict get availability 1.0). ``system_availability``: optional
    common up-state (float or MTBF/MTTR dict) multiplying every well.

    Returns dict with ``dates``, ``days``, ``interval_days``, ``rate`` and ``cumulative`` each ``{'P90','P50','P10','mean'}``
    (lists, P90 = low case), ``unconstrained`` ``{'rate','cumulative'}``, ``expected_uptime_factor`` (mean final cumulative
    / unconstrained final cumulative), ``per_well`` summaries, ``cumulative_final`` percentiles, ``n_samples``, ``seed``,
    ``mode``, ``variable``. With ``keep_samples`` also ``samples`` {'rate','cumulative'} arrays (n_samples x n_steps).
    """
    if mode not in ('bernoulli', 'renewal'): raise ValueError("mode must be 'bernoulli' or 'renewal'")
    if n_samples < 1: raise ValueError('n_samples must be >= 1')
    dates, days, rates = extract_rate_series(forecast, variable)
    nt = len(days); dt = _intervals(days); cnt = np.maximum(np.rint(dt).astype(int), 1); n_days = int(cnt.sum())
    rng = np.random.default_rng(seed)
    def frac(states):   # (samples, days) -> (samples, steps) uptime fraction per step
        edges_ = np.concatenate([[0], np.cumsum(cnt)[:-1]])
        return np.add.reduceat(states.astype(float), edges_, axis=1) / cnt
    sys_states = None
    if system_availability is not None: sys_states = _daily_states(system_availability, n_samples, n_days, mode, rng, default_mttr_days, SYSTEM_KEY)
    rate_s = np.zeros((n_samples, nt)); per = {}
    for w in sorted(rates, key=str):
        q = rates[w]
        if isinstance(availability, dict): av = availability.get(w, 1.0)
        else: av = availability
        st = _daily_states(av, n_samples, n_days, mode, rng, default_mttr_days, w)
        if sys_states is not None: st = st & sys_states
        f = frac(st); r = q[None, :] * f; rate_s += r
        per[w] = {'availability_input': _spec(av, default_mttr_days)[0], 'realised_mean_uptime': float(f.mean()),
                  'cumulative_unconstrained': float(np.sum(q * dt)), 'cumulative_mean': float((r * dt).sum(axis=1).mean())}
    cum_s = np.cumsum(rate_s * dt[None, :], axis=1)
    un_rate = sum(rates.values()); un_cum = np.cumsum(un_rate * dt)
    def stats(x):
        return {'P90': np.percentile(x, 10, axis=0).tolist(), 'P50': np.percentile(x, 50, axis=0).tolist(),
                'P10': np.percentile(x, 90, axis=0).tolist(), 'mean': x.mean(axis=0).tolist()}
    cs = stats(cum_s); fin = {k: v[-1] for k, v in cs.items()}
    out = {'dates': list(dates) if dates is not None else None, 'days': days.tolist(), 'interval_days': dt.tolist(),
           'rate': stats(rate_s), 'cumulative': cs, 'cumulative_final': fin,
           'unconstrained': {'rate': np.asarray(un_rate).tolist(), 'cumulative': un_cum.tolist()},
           'expected_uptime_factor': float(fin['mean'] / un_cum[-1]) if un_cum[-1] > 0 else float('nan'),
           'per_well': per, 'n_samples': int(n_samples), 'seed': seed, 'mode': mode, 'variable': variable,
           'convention': 'P90 = low case (10th percentile), P10 = high case (90th percentile)'}
    if keep_samples: out['samples'] = {'rate': rate_s, 'cumulative': cum_s}
    return out
