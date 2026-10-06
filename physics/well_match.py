"""Matching the single-well model (IPR and VLP) to measured data, shown on the nodal plot.

* ``match_ipr``    well-test points (rate, flowing BHP) -> productivity (PI / Vogel qmax / gas C), optionally also reservoir pressure.
* ``match_vlp``    tests with rate, WHP and measured BHP -> ranks every tubing correlation by error, then fits a pressure-drop multiplier
                   (``vlp_dp_multiplier``) on the best ones. A multiplier far from 1 says the inputs (depth, ID, GOR, water cut, fluid) are wrong - fix those first.
* ``match_survey`` flowing-gradient survey (TVD, pressure) at one rate and WHP -> ranks correlations by profile error.

Everything is least squares on the same functions the network solver uses (``physics.well_model``), so a matched well behaves identically in
the network. The match reproduces the data; it does not prove the physics - two points cannot separate productivity from reservoir pressure."""
from __future__ import annotations
import copy, math
import numpy as np, pandas as pd
from scipy.optimize import least_squares
from physics.well_model import well_settings, ipr_pwf, vlp_bhp, _ipr_name
from physics.vlp import tubing_bhp_bar
from physics.advanced_wells import skin_adjusted_pi

TEST_COLUMNS = ['Rate [m3/d]', 'BHP [bar]', 'WHP [bar]', 'Reservoir pressure [bar]', 'Water cut', 'GOR [Sm3/Sm3]']


def clean_tests(df):
    """Keep rows with a positive rate and a finite BHP/WHP. Returns DataFrame with the standard columns that exist."""
    d = pd.DataFrame(df).copy()
    for c in TEST_COLUMNS:
        if c in d: d[c] = pd.to_numeric(d[c], errors='coerce')
    if 'Rate [m3/d]' not in d: return d.iloc[0:0]
    return d[d['Rate [m3/d]'] > 0].reset_index(drop=True)


# ----------------------------------------------------------------------------- IPR
def match_ipr(prm, tests, fit_reservoir_pressure=False, fit_exponent=False):
    t = clean_tests(tests); t = t[t['BHP [bar]'].notna()] if 'BHP [bar]' in t else t.iloc[0:0]
    if len(t) < 1: return {'error': 'need at least one test with a rate and a flowing BHP'}
    if fit_reservoir_pressure and len(t) < 2: return {'error': 'fitting reservoir pressure needs at least 2 tests'}
    ws0 = well_settings(prm); kind = ws0['ipr_model']; mult = skin_adjusted_pi(1.0, ws0['skin'], prm.get('skin_reference_factor', 7.0) or 7.0) or 1.0
    q = t['Rate [m3/d]'].values.astype(float); pw = t['BHP [bar]'].values.astype(float)
    pr_col = t['Reservoir pressure [bar]'].values if 'Reservoir pressure [bar]' in t else None

    def make(x):
        ws = dict(ws0); i = 0
        if fit_reservoir_pressure: ws['pr'] = x[i]; i += 1
        key = {'PI': 'pi', 'Vogel': 'qmax', 'Gas': 'gas_c'}[kind]; ws[key] = math.exp(x[i]); i += 1
        if kind == 'Gas' and fit_exponent: ws['gas_n'] = min(max(x[i], 0.5), 1.0)
        return ws
    def res(x):
        ws = make(x); out = []
        for k, (qq, pp) in enumerate(zip(q, pw)):
            if pr_col is not None and not fit_reservoir_pressure and pr_col[k] == pr_col[k]: ws_k = dict(ws, pr=float(pr_col[k]))
            else: ws_k = ws
            out.append(ipr_pwf(float(qq), ws_k) - pp)
        return np.array(out)
    base = {'PI': ws0['pi'], 'Vogel': ws0['qmax'], 'Gas': ws0['gas_c']}[kind]; x0 = []; lo = []; hi = []
    if fit_reservoir_pressure: x0.append(ws0['pr']); lo.append(max(float(pw.max()) * 1.001, 1.0)); hi.append(max(ws0['pr'] * 2, float(pw.max()) * 3))
    x0.append(math.log(max(base, 1e-6))); lo.append(math.log(max(base, 1e-6) / 100)); hi.append(math.log(max(base, 1e-6) * 100))
    if kind == 'Gas' and fit_exponent: x0.append(ws0['gas_n']); lo.append(0.5); hi.append(1.0)
    x0 = [min(max(v, l), h) for v, l, h in zip(x0, lo, hi)]
    before = res(np.array(x0)); sol = least_squares(res, np.array(x0, float), bounds=(lo, hi)); after = sol.fun
    ws = make(sol.x); upd = {}
    if fit_reservoir_pressure: upd['reservoir_pressure_bar'] = float(ws['pr'])
    if kind == 'PI': upd['pi_m3d_bar'] = float(ws['pi'] / mult)
    elif kind == 'Vogel': upd['qmax_m3d'] = float(ws['qmax'] / mult)
    else:
        upd['gas_c_sm3d_bar2n'] = float(ws['gas_c'] / mult)
        if fit_exponent: upd['gas_n'] = float(ws['gas_n'])
    st = float(((pw - pw.mean()) ** 2).sum()); ss = float((after ** 2).sum()); at_bound = any(abs(v - b) < 1e-6 * max(1, abs(b)) for v, b in zip(sol.x, lo)) or any(abs(v - b) < 1e-6 * max(1, abs(b)) for v, b in zip(sol.x, hi))
    return {'params': upd, 'ipr_model': kind, 'rmse_before_bar': float(math.sqrt((before ** 2).mean())), 'rmse_after_bar': float(math.sqrt((after ** 2).mean())), 'r2': (1 - ss / st) if st > 0 else float('nan'), 'n_tests': int(len(t)),
            'at_bound': bool(at_bound), 'residuals': pd.DataFrame({'Rate [m3/d]': q, 'Measured BHP [bar]': pw, 'Matched BHP [bar]': pw + after, 'Residual [bar]': after}),
            'note': 'productivity includes the effect of skin (separate skin from PI only with a build-up test)' if kind != 'Gas' else 'gas deliverability coefficient'}


# ----------------------------------------------------------------------------- VLP
def _tubing_names():
    from physics.correlations import list_correlations
    names = list(list_correlations('tubing'))
    if 'Beggs-Brill' not in names and not any(n.lower().startswith('beggs') for n in names): names.insert(0, 'Beggs-Brill')
    return names


def _pred_bhp(prm, correlation, q, whp, wc=None, gor=None):
    p = dict(prm); p['vlp_model'] = p['correlation'] = correlation; p['vlp_dp_multiplier'] = 1.0
    if wc is not None and wc == wc: p['water_cut'] = float(wc)
    if gor is not None and gor == gor: p['gor_sm3sm3'] = float(gor)
    return vlp_bhp(float(q), float(whp), well_settings(p))[0]


def match_vlp(prm, tests, correlations=None):
    t = clean_tests(tests)
    if not {'BHP [bar]', 'WHP [bar]'} <= set(t.columns): return {'error': 'VLP matching needs rate, WHP and measured BHP'}
    t = t[t['BHP [bar]'].notna() & t['WHP [bar]'].notna()].reset_index(drop=True)
    if len(t) < 1: return {'error': 'no complete tests (rate, WHP, BHP)'}
    rows = []; best = None
    for name in (correlations or _tubing_names()):
        try: pred = np.array([_pred_bhp(prm, name, r['Rate [m3/d]'], r['WHP [bar]'], r.get('Water cut'), r.get('GOR [Sm3/Sm3]')) for _, r in t.iterrows()])
        except Exception as e: rows.append({'Correlation': name, 'Note': f'failed: {type(e).__name__}'}); continue
        meas = t['BHP [bar]'].values.astype(float); whp = t['WHP [bar]'].values.astype(float); err = pred - meas
        dpm, dpp = meas - whp, pred - whp; m = float((dpp @ dpm) / (dpp @ dpp)) if (dpp @ dpp) > 0 else 1.0; m = min(max(m, 0.2), 5.0)
        err_m = whp + m * dpp - meas
        rows.append({'Correlation': name, 'Bias [bar]': float(err.mean()), 'RMSE [bar]': float(math.sqrt((err ** 2).mean())), 'Best multiplier': m, 'RMSE with multiplier [bar]': float(math.sqrt((err_m ** 2).mean())), 'Note': ''})
    table = pd.DataFrame(rows)
    ok = table[table['RMSE [bar]'].notna()].sort_values('RMSE [bar]').reset_index(drop=True)
    if ok.empty: return {'error': 'no correlation could be evaluated'}
    b = ok.iloc[0]; mult = float(b['Best multiplier'])
    warn = None if 0.75 <= mult <= 1.35 else f'the fitted multiplier {mult:.2f} is far from 1 - check depth, tubing ID, GOR, water cut and fluid before trusting it'
    return {'ranking': ok, 'best_correlation': b['Correlation'], 'best_rmse_bar': float(b['RMSE [bar]']), 'multiplier': mult, 'rmse_with_multiplier_bar': float(b['RMSE with multiplier [bar]']),
            'params': {'vlp_model': b['Correlation'], 'correlation': b['Correlation'], 'vlp_dp_multiplier': mult}, 'warning': warn, 'n_tests': int(len(t))}


def match_survey(prm, survey, rate_m3d, whp_bar, correlations=None):
    """survey: DataFrame with 'TVD [m]' and 'Pressure [bar]' (flowing gradient survey, wellhead to bottom)."""
    s = pd.DataFrame(survey)
    if not {'TVD [m]', 'Pressure [bar]'} <= set(s.columns) or len(s) < 2: return {'error': 'survey needs TVD [m] and Pressure [bar] (at least 2 stations)'}
    s = s.dropna(subset=['TVD [m]', 'Pressure [bar]']).sort_values('TVD [m]'); depth = float(max(prm.get('depth_m') or 0, s['TVD [m]'].max()))
    rows = []; profiles = {}
    for name in (correlations or _tubing_names()):
        p = dict(prm); p['vlp_model'] = p['correlation'] = name; p['depth_m'] = depth; ws = well_settings(p); prof = []
        try:
            from physics.pvt_model import fluid_scope
            with fluid_scope(ws.get('pvt_prm'), ws['gor'], ws['api'], ws['gas_sg']):
                tubing_bhp_bar(float(rate_m3d), float(whp_bar), ws['depth'], ws['tubing_id'], ws['roughness'], ws['temperature'], ws['water_cut'], ws['gor'], ws['api'], ws['gas_sg'], name, segments=max(ws['segments'], 20),
                               bottomhole_temperature_c=ws['bh_temperature'], geometry=ws.get('geometry'), thermal=ws.get('thermal'), profile=prof)
        except Exception as e: rows.append({'Correlation': name, 'Note': f'failed: {type(e).__name__}'}); continue
        z = np.array([0.0] + [r['tvd_m'] for r in prof]); pp = np.array([float(whp_bar)] + [r['pressure_bar'] for r in prof]); pred = np.interp(s['TVD [m]'].values, z, pp); err = pred - s['Pressure [bar]'].values
        g_meas = np.polyfit(s['TVD [m]'], s['Pressure [bar]'], 1)[0] * 1000; g_pred = np.polyfit(s['TVD [m]'], pred, 1)[0] * 1000
        rows.append({'Correlation': name, 'Bias [bar]': float(err.mean()), 'RMSE [bar]': float(math.sqrt((err ** 2).mean())), 'Max abs [bar]': float(np.abs(err).max()), 'Gradient measured [bar/km]': float(g_meas), 'Gradient predicted [bar/km]': float(g_pred), 'Note': ''})
        profiles[name] = pd.DataFrame({'TVD [m]': z, 'Pressure [bar]': pp})
    t = pd.DataFrame(rows); ok = t[t['RMSE [bar]'].notna()].sort_values('RMSE [bar]').reset_index(drop=True) if 'RMSE [bar]' in t else t
    if ok.empty: return {'error': 'no correlation could be evaluated'}
    return {'ranking': ok, 'best_correlation': ok.iloc[0]['Correlation'], 'profiles': profiles, 'params': {'vlp_model': ok.iloc[0]['Correlation'], 'correlation': ok.iloc[0]['Correlation']}}


def apply_match(prm, *fits):
    """Params copy with the fitted parameter updates of every fit that has them."""
    p = copy.deepcopy(prm)
    for f in fits:
        if f and 'params' in f and 'error' not in f: p.update(f['params'])
    return p


def synthetic_tests(prm, whps=(15.0, 25.0, 40.0), noise_bar=0.0, seed=1):
    """Tests generated from the current model (to try the tool, or to check a match recovers the truth)."""
    from physics.well_model import solve_well_rate, ipr_pwf as _ip
    rng = np.random.default_rng(seed); ws = well_settings(prm); rows = []
    for w in whps:
        q, st = solve_well_rate(w, ws)
        if q > 0: rows.append({'Rate [m3/d]': q, 'BHP [bar]': _ip(q, ws) + rng.normal(0, noise_bar), 'WHP [bar]': w, 'Reservoir pressure [bar]': ws['pr']})
    return pd.DataFrame(rows)
