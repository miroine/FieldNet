"""Material-balance diagnostics for one tank: Havlena-Odeh, Campbell, Cole, p/z, drive indices, voidage replacement.

Input is a *series* (DataFrame) with one row per date, cumulative volumes and pressure:
    Date, Pressure [bar], Cum oil [Sm3], Cum gas [Sm3], Cum water [m3], Cum water inj [m3], Cum gas inj [Sm3], Aquifer influx [m3], Net communication [m3]
Two sources: ``series_from_forecast`` (what the model predicts) and ``series_from_history`` (the user's measured pressures and
cumulatives - where material balance earns its keep: fitting N (or G) and the aquifer strength).

Honest limits: PVT comes from the tank (its table or node properties - screening model has constant Bo and linear Rs below Pb). Run on
a *forecast* of the screening tank model the F-vs-Et plot is a consistency check (it should return the input STOIIP), not independent
evidence. Gas injection / water injection / communication enter as reservoir-volume replacement terms."""
from __future__ import annotations
import math
import numpy as np, pandas as pd

CUM = ['Cum oil [Sm3]', 'Cum gas [Sm3]', 'Cum water [m3]', 'Cum water inj [m3]', 'Cum gas inj [Sm3]', 'Aquifer influx [m3]', 'Net communication [m3]']


# ----------------------------------------------------------------------------- series
def series_from_forecast(forecast, tank_id, pi_bar=None):
    """Tank state per step END (forecast rows are labelled by step start). A t=0 row with zero cumulatives and initial pressure is prepended."""
    t = pd.DataFrame(forecast.get('tanks') or [])
    if t.empty or tank_id not in set(t['Tank ID']): return pd.DataFrame(columns=['Date', 'Pressure [bar]'] + CUM)
    t = t[t['Tank ID'] == tank_id].reset_index(drop=True).copy()
    steps = {r['Date']: r.get('Step [days]') for r in (forecast.get('field') or [])}
    start = pd.to_datetime(t['Date']); d = [steps.get(x) for x in t['Date']]
    if all(v is not None for v in d): end = start + pd.to_timedelta(pd.Series(d, dtype=float), unit='D')
    else: end = pd.Series(list(start.iloc[1:]) + [start.iloc[-1] + (start.iloc[-1] - start.iloc[-2] if len(start) > 1 else pd.Timedelta(days=30))])
    for c in CUM:
        if c not in t: t[c] = 0.0
    out = t[['Pressure [bar]'] + CUM].copy(); out.insert(0, 'Date', end.values)
    out = out[~out['Date'].duplicated(keep='first')].reset_index(drop=True)      # final row of a forecast has dt = 0
    first = {'Date': start.iloc[0], 'Pressure [bar]': pi_bar if pi_bar is not None else float(t['Pressure [bar]'].iloc[0]), **{c: 0.0 for c in CUM}}
    return pd.concat([pd.DataFrame([first]), out], ignore_index=True)


def series_from_history(df, mapping=None):
    """Normalise a user table. ``mapping`` maps our column -> their column. Missing cumulative columns become 0."""
    mapping = mapping or {}; out = pd.DataFrame()
    out['Date'] = pd.to_datetime(df[mapping.get('Date', 'Date')])
    out['Pressure [bar]'] = pd.to_numeric(df[mapping.get('Pressure [bar]', 'Pressure [bar]')], errors='coerce')
    for c in CUM:
        col = mapping.get(c, c); out[c] = pd.to_numeric(df[col], errors='coerce').fillna(0.0) if col in df else 0.0
    out = out.dropna(subset=['Date', 'Pressure [bar]']).sort_values('Date').reset_index(drop=True)
    for c in CUM:                                             # cumulatives must not fall
        d = out[c].diff().fillna(0.0)
        if (d < -1e-9 * max(out[c].abs().max(), 1.0)).any(): out.attrs.setdefault('warnings', []).append(f'{c} decreases between rows - check the data')
    return out


# ----------------------------------------------------------------------------- PVT access
class Props:
    """Bo, Rs, Bg, Bt as functions of pressure from a Tank (table or node properties) plus MB constants."""

    def __init__(self, tank, bw=1.0, m=0.0, cf_cw_override=None):
        self.t = tank; self.phase = tank.phase; self.bw = float(bw); self.m = float(m)
        self.pi = tank.pi; self.swi = tank.swi; self.temp = tank.t
        self.ct = float(cf_cw_override) if cf_cw_override else float(getattr(tank, 'ct', 1e-4) or 1e-4)
        self.n_in = tank.n if tank.phase == 'oil' else getattr(tank, 'g', 0.0)
        self.pb = getattr(tank, 'pb', 0.0)

    def bo(self, p): return self.t._bo(p) if self.phase == 'oil' else 1.0
    def rs(self, p): return self.t._solution_rs(p) if self.phase == 'oil' else 0.0
    def bg(self, p): return self.t._bg(p)
    def rsi(self): return self.rs(self.pi) if self.phase == 'oil' else 0.0
    def bt(self, p): return self.bo(p) + (self.rsi() - self.rs(p)) * self.bg(p)


# ----------------------------------------------------------------------------- core table
def balance_table(series, props: Props):
    """Per date: F, Eo, Eg, Efw, Et, voidage terms, replacement terms. Everything in reservoir m3 unless stated."""
    s = series.reset_index(drop=True); pr = props; rows = []
    bti = pr.bt(pr.pi) if pr.phase == 'oil' else 1.0; bgi = pr.bg(pr.pi); area = pr.t.n if pr.phase == 'oil' else pr.t.g
    for _, r in s.iterrows():
        p = float(r['Pressure [bar]']); np_, gp, wp = float(r['Cum oil [Sm3]']), float(r['Cum gas [Sm3]']), float(r['Cum water [m3]'])
        wi, gi, we, xin = float(r['Cum water inj [m3]']), float(r['Cum gas inj [Sm3]']), float(r['Aquifer influx [m3]']), float(r['Net communication [m3]'])
        bg = pr.bg(p); dp = pr.pi - p
        if pr.phase == 'oil':
            bo, rs, bt = pr.bo(p), pr.rs(p), pr.bt(p)
            v_oil = np_ * bo; v_gas = (gp - np_ * rs) * bg; v_wat = wp * pr.bw
            eo = bt - bti; eg = bti * (bg / bgi - 1.0) if pr.m else 0.0
            efw = (1 + pr.m) * bti * pr.ct * dp / max(1 - pr.swi, 1e-6)
        else:
            v_oil = 0.0; v_gas = gp * bg; v_wat = wp * pr.bw
            eo = 0.0; eg = bg - bgi; efw = bgi * pr.ct * dp / max(1 - pr.swi, 1e-6)
        inj_w, inj_g = wi * pr.bw, gi * bg
        fp = v_oil + v_gas + v_wat; f = fp - inj_w - inj_g
        et = eo + pr.m * eg + efw if pr.phase == 'oil' else eg + efw
        rows.append({'Date': r['Date'], 'Pressure [bar]': p, 'F [rm3]': f, 'Withdrawal [rm3]': fp, 'Voidage oil [rm3]': v_oil, 'Voidage free gas [rm3]': v_gas, 'Voidage water [rm3]': v_wat,
                     'Water injection [rm3]': inj_w, 'Gas injection [rm3]': inj_g, 'Aquifer influx [rm3]': we, 'Net communication [rm3]': xin, 'Eo [rm3/Sm3]': eo, 'Eg [rm3/Sm3]': eg, 'Efw [rm3/Sm3]': efw, 'Et [rm3/Sm3]': et,
                     'Np [Sm3]': np_, 'Gp [Sm3]': gp, 'Rp [Sm3/Sm3]': (gp / np_) if np_ > 0 else float('nan'), 'dP [bar]': dp,
                     'z': _z(pr, p), 'p/z [bar]': p / max(_z(pr, p), 1e-6), 'RF [%]': 100.0 * (np_ if pr.phase == 'oil' else gp) / max(area, 1.0)})
    return pd.DataFrame(rows)


def _z(pr, p):
    from network.reservoir_mb import z_factor
    return z_factor(p, pr.temp, pr.t.gas_sg or 0.7)


# ----------------------------------------------------------------------------- fits
def fit_in_place(bal, props: Props, with_aquifer=True, known_influx=False, min_dp_bar=1.0):
    """Least squares for in-place volume (and Schilthuis aquifer productivity J [m3/d/bar]).

    known_influx: subtract the tabulated aquifer influx (+ communication) from F and fit N only (forecast / simulator output).
    with_aquifer: fit  F = N Et + J I  with I = integral (Pi - P) dt [bar.d]; otherwise F = N Et (volumetric).
    Returns dict (N, J, r2, rmse, n_points, note) or an 'error' key."""
    b = bal[(bal['dP [bar]'] >= min_dp_bar) & (bal['Et [rm3/Sm3]'] > 0)].copy()
    if len(b) < 3: return {'error': 'need at least 3 points with a pressure drop and positive Et'}
    f = b['F [rm3]'].values.astype(float); et = b['Et [rm3/Sm3]'].values.astype(float)
    if known_influx: f = f - b['Aquifer influx [rm3]'].values - b['Net communication [rm3]'].values
    full = bal.copy(); days = (pd.to_datetime(full['Date']) - pd.to_datetime(full['Date']).iloc[0]).dt.days.values.astype(float)
    integ = np.concatenate([[0.0], np.cumsum(0.5 * (full['dP [bar]'].values[1:] + full['dP [bar]'].values[:-1]) * np.diff(days))]); full['I'] = integ
    i_ = full.loc[b.index, 'I'].values
    if with_aquifer and not known_influx and len(b) >= 4:
        A = np.column_stack([et, i_]); sol, *_ = np.linalg.lstsq(A, f, rcond=None); n_, j_ = float(sol[0]), float(sol[1]); pred = A @ sol
        if j_ < 0 or n_ <= 0:                                  # unphysical -> volumetric fallback
            n_ = float((et @ f) / (et @ et)); j_ = 0.0; pred = n_ * et; note = 'aquifer fit was unphysical (J<0); volumetric fit used'
        else: note = 'N and aquifer J fitted together (correlated: N and J trade off when the pressure record is short)'
    else:
        n_ = float((et @ f) / (et @ et)); j_ = 0.0; pred = n_ * et; note = 'volumetric (no aquifer) fit' if not known_influx else 'N fitted with the tabulated aquifer influx subtracted'
    ss = float(((f - pred) ** 2).sum()); st = float(((f - f.mean()) ** 2).sum())
    dp_frac = float(b['dP [bar]'].max() / max(props.pi, 1.0))
    if dp_frac < 0.03: note += f'; WARNING: pressure fell only {dp_frac:.1%} of initial - in-place volume is not identifiable from this record (pressure supported)'
    return {'dp_fraction': dp_frac, 'N': n_, 'J': j_, 'r2': 1 - ss / st if st > 0 else float('nan'), 'rmse': math.sqrt(ss / len(f)), 'n_points': int(len(b)), 'note': note, 'phase': props.phase}


def straight_lines(bal, fit, props: Props):
    """Plot tables. Havlena-Odeh F vs Et (+ fitted line), Campbell F/Et vs F, Cole F/Et vs cumulative production, and p/z vs Gp for gas."""
    b = bal[bal['Et [rm3/Sm3]'] > 0].copy()
    out = {}
    out['havlena_odeh'] = pd.DataFrame({'Et [rm3/Sm3]': b['Et [rm3/Sm3]'], 'F [rm3]': b['F [rm3]'], 'Date': b['Date']})
    if fit and 'N' in fit:
        x = np.linspace(0, float(b['Et [rm3/Sm3]'].max()) * 1.05, 20) if len(b) else np.array([0.0])
        out['havlena_odeh_fit'] = pd.DataFrame({'Et [rm3/Sm3]': x, 'F [rm3]': fit['N'] * x})
    out['campbell'] = pd.DataFrame({'F [rm3]': b['F [rm3]'], 'F/Et [Sm3]': b['F [rm3]'] / b['Et [rm3/Sm3]'], 'Date': b['Date']})
    cumprod = b['Np [Sm3]'] if props.phase == 'oil' else b['Gp [Sm3]']
    out['cole'] = pd.DataFrame({'Cumulative production [Sm3]': cumprod, 'F/Et [Sm3]': b['F [rm3]'] / b['Et [rm3/Sm3]'], 'Date': b['Date']})
    return out


def pz_fit(bal, min_points=3, last_n=None):
    """Linear p/z vs Gp (gas). Straight line through the later ``last_n`` points; GIIP at p/z = 0 (apparent, ignores aquifer)."""
    b = bal[bal['Gp [Sm3]'] >= 0]
    if last_n: b = b.tail(last_n)
    if len(b) < min_points or b['Gp [Sm3]'].nunique() < 2: return {'error': 'need more points'}
    a, c = np.polyfit(b['Gp [Sm3]'].values, b['p/z [bar]'].values, 1)
    if a >= 0: return {'error': 'p/z does not fall with production'}
    return {'slope': float(a), 'intercept': float(c), 'G_apparent': float(-c / a), 'n_points': int(len(b))}


# ----------------------------------------------------------------------------- drive indices & voidage
def drive_indices(bal, n_in_place):
    """Fractions of the withdrawal supplied by each mechanism (sum to 1 when the balance closes).
    Depletion (oil + solution gas expansion), gas cap, compaction + connate-water expansion, natural water influx, injection, communication."""
    d = bal[bal['Withdrawal [rm3]'] > 0].copy()
    if d.empty: return pd.DataFrame()
    fp = d['Withdrawal [rm3]']
    out = pd.DataFrame({'Date': d['Date'], 'Depletion (DDI)': n_in_place * d['Eo [rm3/Sm3]'] / fp, 'Gas cap (SDI)': 0.0,
                        'Compaction / water exp. (CDI)': n_in_place * d['Efw [rm3/Sm3]'] / fp, 'Aquifer (WDI)': d['Aquifer influx [rm3]'] / fp,
                        'Injection (IDI)': (d['Water injection [rm3]'] + d['Gas injection [rm3]']) / fp, 'Communication': d['Net communication [rm3]'] / fp})
    out['Closure'] = out.drop(columns='Date').sum(axis=1)
    return out.reset_index(drop=True)


def drive_indices_with_gascap(bal, n_in_place, m):
    out = drive_indices(bal, n_in_place)
    if out.empty: return out
    d = bal[bal['Withdrawal [rm3]'] > 0]; out['Gas cap (SDI)'] = (n_in_place * m * d['Eg [rm3/Sm3]'] / d['Withdrawal [rm3]']).values
    out['Closure'] = out.drop(columns=['Date', 'Closure']).sum(axis=1); return out


def voidage_intervals(bal):
    """Per-interval voidage (what left the reservoir) and replacement (what came in), reservoir m3 - differences of the cumulative balance
    table. 'Net' = replacement - voidage; VRR = replacement / voidage."""
    b = bal.reset_index(drop=True)
    if len(b) < 2: return pd.DataFrame()
    cols = {'Voidage oil [rm3]': 'Oil', 'Voidage free gas [rm3]': 'Free gas', 'Voidage water [rm3]': 'Water', 'Water injection [rm3]': 'Water injection', 'Gas injection [rm3]': 'Gas injection', 'Aquifer influx [rm3]': 'Aquifer influx', 'Net communication [rm3]': 'Communication'}
    d = b[list(cols)].diff().iloc[1:].rename(columns=cols).reset_index(drop=True)
    d.insert(0, 'Date', pd.to_datetime(b['Date']).iloc[:-1].values); d.insert(1, 'End', pd.to_datetime(b['Date']).iloc[1:].values)
    d['Days'] = (d['End'] - d['Date']).dt.days.clip(lower=1)
    d['Voidage [rm3]'] = d['Oil'] + d['Free gas'] + d['Water']
    d['Replacement [rm3]'] = d['Water injection'] + d['Gas injection'] + d['Aquifer influx'] + d['Communication']
    d['Net [rm3]'] = d['Replacement [rm3]'] - d['Voidage [rm3]']
    d['VRR'] = d['Replacement [rm3]'] / d['Voidage [rm3]'].replace(0, np.nan)
    d['Cum VRR'] = d['Replacement [rm3]'].cumsum() / d['Voidage [rm3]'].cumsum().replace(0, np.nan)
    d['Cum net [rm3]'] = d['Net [rm3]'].cumsum(); d['Pressure [bar]'] = b['Pressure [bar]'].iloc[1:].values
    return d


def voidage_annual(intervals):
    """Calendar-year voidage / replacement (volumes pro-rated by days)."""
    from network.annual import annual_volumes
    if intervals is None or len(intervals) == 0: return pd.DataFrame()
    cols = ['Oil', 'Free gas', 'Water', 'Water injection', 'Gas injection', 'Aquifer influx', 'Communication', 'Voidage [rm3]', 'Replacement [rm3]']
    r = pd.DataFrame({'Date': intervals['Date'], 'Step [days]': intervals['Days'].astype(float)})
    for c in cols: r[f'{c} [rm3/d]'] = intervals[c] / intervals['Days']
    a = annual_volumes(r, [f'{c} [rm3/d]' for c in cols])
    a = a[[c for c in a.columns if not c.startswith('Cum ')]]
    a = a.rename(columns={f'{c} [rm3]': f'{c} [rm3]' for c in cols}); a = a.rename(columns={'Voidage [rm3] [rm3]': 'Voidage [rm3]', 'Replacement [rm3] [rm3]': 'Replacement [rm3]'})
    a['Net [rm3]'] = a['Replacement [rm3]'] - a['Voidage [rm3]']; a['VRR'] = a['Replacement [rm3]'] / a['Voidage [rm3]'].replace(0, np.nan)
    return a


def analyse(series, tank, bw=1.0, m=0.0, with_aquifer=True, known_influx=False, ct_override=None):
    """One call for the UI: balance table, fit, straight-line tables, drive indices, voidage."""
    pr = Props(tank, bw, m, ct_override); bal = balance_table(series, pr)
    fit = fit_in_place(bal, pr, with_aquifer=with_aquifer, known_influx=known_influx)
    reliable = bool(fit and 'N' in fit and fit.get('dp_fraction', 0) >= 0.03 and fit.get('r2', 0) == fit.get('r2', 0) and fit.get('r2', 0) >= 0.9)
    n_use = fit['N'] if reliable else pr.n_in
    iv = voidage_intervals(bal)
    return {'props': pr, 'balance': bal, 'fit': fit, 'lines': straight_lines(bal, fit, pr), 'pz': pz_fit(bal) if pr.phase != 'oil' else None,
            'drive': drive_indices_with_gascap(bal, n_use, pr.m) if pr.phase == 'oil' else pd.DataFrame(), 'drive_basis_N': n_use, 'drive_basis': 'fitted' if reliable else 'input',
            'voidage': iv, 'voidage_annual': voidage_annual(iv)}
