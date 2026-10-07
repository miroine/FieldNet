"""Forward history match of a tank: fit in-place volume (and aquifer strength, optionally compressibility) to measured pressures.

Unlike the straight-line plots in mb_analysis (Havlena-Odeh), this replays the *measured production* through the same tank model the
forecast uses (``network.reservoir_mb.Tank``), so what is matched is what is later predicted: a pressure history, not a diagnostic plot.
Parameters are fitted in log space (in-place multiplier, aquifer J, compressibility multiplier) by bounded least squares on the pressure
misfit; the parameter covariance from the Jacobian gives an approximate 95 % range, and a record that barely depletes the tank is
reported as not identifiable instead of returning a confident number.
"""
from __future__ import annotations
import copy, math
import numpy as np

PRESSURE_FRACTION_MIN = 0.03    # pressure fall below this share of initial pressure: in-place volume is not identifiable


def _tank(node, N=None, J=None, ct=None):
    from network.reservoir_mb import Tank
    n = copy.deepcopy(node); p = n.setdefault('params', {})
    if N is not None: p['stoiip_sm3' if str(p.get('fluid_phase', 'oil')).lower() == 'oil' else 'giip_sm3'] = float(N)
    if J is not None: p['aquifer_pi_m3d_bar'] = float(J)
    if ct is not None: p['ct_1bar'] = float(ct)
    p['min_pressure_bar'] = min(float(p.get('min_pressure_bar', 20.0)), 1.0)    # history can go below the forecast abandonment pressure
    return Tank(n)


def replay(node, hist, N=None, J=None, ct=None):
    """Predicted pressure at every history row [bar] (row 0 = initial state) for the given parameters."""
    tk = _tank(node, N, J, ct); out = [float(tk.p)]
    dates = hist['Date'].astype('datetime64[ns]').values
    for i in range(1, len(hist)):
        dt = max(float((dates[i] - dates[i - 1]) / np.timedelta64(1, 'D')), 0.0)
        d = {c: float(hist[c].iloc[i] - hist[c].iloc[i - 1]) for c in ('Cum oil [Sm3]', 'Cum gas [Sm3]', 'Cum water [m3]', 'Cum water inj [m3]', 'Cum gas inj [Sm3]')}
        tk.step(max(d['Cum oil [Sm3]'], 0.0), max(d['Cum water [m3]'], 0.0), max(d['Cum gas [Sm3]'], 0.0), max(d['Cum water inj [m3]'], 0.0), max(d['Cum gas inj [Sm3]'], 0.0), dt)
        out.append(float(tk.p))
    return np.array(out)


def match_tank(node, hist, fit_in_place=True, fit_aquifer=False, fit_ct=False, bounds_n=(0.1, 10.0)):
    """``hist``: DataFrame from ``mb_analysis.series_from_history`` (Date, Pressure [bar], Cum ... columns; first row = initial state).
    Returns a dict: fitted values, rmse, r2, ranges, predicted/measured arrays, identifiability warnings. Never raises for poor data."""
    from scipy.optimize import least_squares
    p = node.get('params') or {}; oil = str(p.get('fluid_phase', 'oil')).lower() == 'oil'
    n0 = float(p.get('stoiip_sm3' if oil else 'giip_sm3', 20e6 if oil else 5e9)); j0 = float(p.get('aquifer_pi_m3d_bar', 0.0)); ct0 = float(p.get('ct_1bar', 1.5e-4))
    meas = hist['Pressure [bar]'].astype(float).values
    res = {'phase': 'oil' if oil else 'gas', 'input': {'N': n0, 'J': j0, 'ct': ct0}, 'warnings': [], 'measured': meas, 'dates': hist['Date'].values}
    if len(hist) < 3: res['error'] = 'need at least 3 history rows'; return res
    # the tank starts from the first measured pressure
    node = copy.deepcopy(node); node.setdefault('params', {})['reservoir_pressure_bar'] = float(meas[0])
    dpf = float((meas[0] - meas.min()) / max(meas[0], 1.0))
    if dpf < PRESSURE_FRACTION_MIN: res['warnings'].append(f'pressure fell only {dpf:.1%} of initial: in-place volume cannot be identified from this record (pressure supported or too short)')
    names = []; x0 = []; lo = []; hi = []
    if fit_in_place: names.append('N'); x0.append(0.0); lo.append(math.log(bounds_n[0])); hi.append(math.log(bounds_n[1]))
    if fit_aquifer: names.append('J'); x0.append(math.log1p(max(j0, 0.0)) if j0 > 0 else math.log1p(10.0)); lo.append(0.0); hi.append(math.log1p(1e5))
    if fit_ct and oil: names.append('ct'); x0.append(0.0); lo.append(math.log(0.2)); hi.append(math.log(5.0))
    if not names: res['error'] = 'select at least one parameter to fit'; return res

    def unpack(x):
        d = dict(zip(names, x)); return (n0 * math.exp(d['N']) if 'N' in d else n0, math.expm1(d['J']) if 'J' in d else j0, ct0 * math.exp(d['ct']) if 'ct' in d else ct0)

    def resid(x):
        N, J, ct = unpack(x)
        try: pred = replay(node, hist, N, J, ct)
        except Exception: return np.full(len(meas) - 1, 1e3)
        return (pred[1:] - meas[1:])
    best = None
    starts = [x0] + ([[a if nm != 'J' else math.log1p(0.0) for a, nm in zip(x0, names)]] if 'J' in names else []) + ([[a + (0.7 if nm == 'N' else 0.0) for a, nm in zip(x0, names)], [a - (0.7 if nm == 'N' else 0.0) for a, nm in zip(x0, names)]] if 'N' in names else [])
    for s in starts:
        try:
            r = least_squares(resid, np.clip(s, lo, hi), bounds=(lo, hi), x_scale=1.0, diff_step=1e-3, max_nfev=40)
            if best is None or r.cost < best.cost: best = r
        except Exception as exc: res['warnings'].append(f'fit start failed: {exc}')
    if best is None: res['error'] = 'the fit did not run'; return res
    N, J, ct = unpack(best.x); pred = replay(node, hist, N, J, ct); err = pred[1:] - meas[1:]
    ss = float((err ** 2).sum()); st = float(((meas[1:] - meas[1:].mean()) ** 2).sum())
    rmse = math.sqrt(ss / len(err)); rmse0 = math.sqrt(float(((replay(node, hist) [1:] - meas[1:]) ** 2).mean()))
    ranges = {}
    dof = max(len(err) - len(names), 1)
    if 'N' in names:   # profile likelihood on the in-place volume: re-fit the other parameters for each fixed N (honest when N and aquifer J trade off)
        others = [i for i, nm in enumerate(names) if nm != 'N']; thr = ss * (1.0 + 4.0 / dof); grid = [math.exp(v) for v in np.linspace(lo[names.index('N')], hi[names.index('N')], 17)]
        ok = []
        for g in grid:
            xg = np.array(best.x, dtype=float); xg[names.index('N')] = math.log(g)
            if others:
                def r2(z, xg=xg):
                    xx = xg.copy(); xx[others] = z; return resid(xx)
                try:
                    rr = least_squares(r2, np.clip(best.x[others], np.array(lo)[others], np.array(hi)[others]), bounds=(np.array(lo)[others], np.array(hi)[others]), diff_step=1e-3, max_nfev=25)
                    sse = float((rr.fun ** 2).sum())
                except Exception: continue
            else: sse = float((resid(xg) ** 2).sum())
            if sse <= thr: ok.append(g * n0)
        if ok:
            lo_n, hi_n = min(ok + [N]), max(ok + [N]); ranges['N'] = (lo_n, hi_n)
            edge_lo = lo_n <= n0 * bounds_n[0] * 1.001; edge_hi = hi_n >= n0 * bounds_n[1] * 0.999
            if edge_lo or edge_hi or hi_n / max(lo_n, 1e-9) > 3.0:
                res['warnings'].append('in-place volume is poorly constrained by this record (the profile range spans more than a factor 3 or runs to the search limit): '
                                       'do not rely on the point estimate; more depletion or a longer record is needed')
            res['n_range_open'] = bool(edge_lo or edge_hi)
    try:
        Jm = best.jac; s2 = ss / dof; cov = s2 * np.linalg.inv(Jm.T @ Jm); se = np.sqrt(np.clip(np.diag(cov), 0, None))
        for nm, sd, xv in zip(names, se, best.x):
            if nm == 'J': ranges[nm] = (max(math.expm1(xv - 1.96 * sd), 0.0), math.expm1(xv + 1.96 * sd))
            elif nm == 'ct': ranges[nm] = (ct0 * math.exp(xv - 1.96 * sd), ct0 * math.exp(xv + 1.96 * sd))
    except Exception:
        res['warnings'].append('parameter uncertainty could not be estimated (parameters are not separately identifiable: in-place volume and aquifer trade off)')
    for nm, xv, l, h in zip(names, best.x, lo, hi):
        if abs(xv - l) < 1e-6 or abs(xv - h) < 1e-6: res['warnings'].append(f'{nm} hit its bound - the fit is not trustworthy for this parameter')
    res.update({'fitted': {'N': N, 'J': J, 'ct': ct}, 'rmse_bar': rmse, 'rmse_before_bar': rmse0, 'r2': 1 - ss / st if st > 0 else float('nan'), 'predicted': pred, 'ranges': ranges,
                'fit_params': names, 'n_points': int(len(err))})
    res['quality'] = 'good' if rmse <= 0.01 * meas[0] and not any('bound' in w for w in res['warnings']) else ('fair' if rmse <= 0.03 * meas[0] else 'poor')
    return res


def apply_match(node, res):
    """Write the fitted values into the tank node (in place). Returns the list of changed parameter keys."""
    if 'fitted' not in res: return []
    p = node.setdefault('params', {}); oil = res['phase'] == 'oil'; f = res['fitted']; changed = []
    if 'N' in res['fit_params']: p['stoiip_sm3' if oil else 'giip_sm3'] = float(f['N']); changed.append('stoiip_sm3' if oil else 'giip_sm3')
    if 'J' in res['fit_params']: p['aquifer_pi_m3d_bar'] = float(f['J']); changed.append('aquifer_pi_m3d_bar')
    if 'ct' in res['fit_params']: p['ct_1bar'] = float(f['ct']); changed.append('ct_1bar')
    return changed
