"""Per-well prediction sources: Arps decline curves and external reservoir-simulator tables (GAP/RESOLVE style).

A well node may carry ``params['prediction_source']``::

    {'type': 'none'}
    {'type': 'decline', 'basis': 'oil'|'liquid'|'gas', 'qi': 800.0, 'di_per_year': 0.25, 'b': 0.5,
     't0': '2027-01-01' (default forecast start), 'q_abandon': 50.0, 'terminal_di_per_year': 0.06,
     'apply_as': 'rate_cap',
     'water_cut_table': [{'date': '2027-01-01', 'value': 0.1}, ...] | [{'cum_oil_sm3': 1e6, 'value': 0.3}],
     'gor_table': [...same shape...]}
    {'type': 'external_table', 'x_axis': 'date'|'time_days'|'cum_oil_sm3', 'interp': 'linear'|'step',
     'extrapolate': 'hold'|'linear'|'none', 't0': optional reference date for 'time_days' rows,
     'rows': [{'date': ISO | 'time_days': float | 'cum_oil_sm3': float,
               'reservoir_pressure_bar', 'pi_m3d_bar', 'water_cut', 'gor_sm3sm3',
               'max_liquid_rate_m3d', 'max_oil_rate_m3d', 'skin'}, ...]}

``apply_prediction_sources`` is called by ``network.forecast.run_forecast`` after tank links were applied. Decline
curves become a *potential cap* (``params['_potential_cap_m3d']``, honoured by ``physics.well_model.well_settings``): the
well still follows IPR/VLP and network constraints and is simply limited to the decline potential. External tables
overwrite the matching well parameters (the tank still depletes from the produced volumes, but the well follows the
external profile).

Canonical units: bar, fraction, Sm3/Sm3, m3/d (liquid and oil), Sm3/d (gas), days. Decline rates ``qi`` are in m3/d
for oil/liquid basis and Sm3/d for gas. ``di_per_year`` is the nominal (initial) decline rate per year.
"""
from __future__ import annotations
import copy
import io
import math
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

DAYS_PER_YEAR = 365.25
TABLE_COLUMNS = ('reservoir_pressure_bar', 'pi_m3d_bar', 'water_cut', 'gor_sm3sm3', 'max_liquid_rate_m3d', 'max_oil_rate_m3d', 'skin')
X_AXES = ('date', 'time_days', 'cum_oil_sm3')
_PSI_BAR = 0.0689476; _STB_M3 = 0.158987294928; _SCF_SM3 = 0.0283168466


# ---------------------------------------------------------------------------------------------
# Arps decline
# ---------------------------------------------------------------------------------------------
def _terminal(di, b, dterm):
    """Return the terminal nominal decline if the modified-hyperbolic switch applies, else None."""
    if dterm is None or b is None or b <= 1e-6: return None
    try: dterm = float(dterm)
    except (TypeError, ValueError): return None
    return dterm if 0 < dterm < di else None


def _switch_time_years(di, b, dterm):
    return (di / dterm - 1.0) / (b * di)


def arps_rate(t_days, qi, di_per_year, b=0.0, terminal_di_per_year=None, q_abandon=None):
    """Arps rate at time ``t_days`` (>= 0) after t0. b=0 exponential, b=1 harmonic, else hyperbolic.

    With ``terminal_di_per_year`` (< di) the curve switches to exponential once the instantaneous decline
    D(t) = Di / (1 + b Di t) falls to the terminal value (modified hyperbolic). Below ``q_abandon`` the rate is 0.
    """
    t = np.maximum(np.asarray(t_days, dtype=float), 0.0) / DAYS_PER_YEAR
    qi = float(qi); di = max(float(di_per_year), 0.0); b = min(max(float(b or 0.0), 0.0), 2.0)
    if di <= 0:
        q = np.full_like(t, qi)
    elif b < 1e-6:
        q = qi * np.exp(-di * t)
    else:
        q = qi * (1.0 + b * di * t) ** (-1.0 / b)
        dt_ = _terminal(di, b, terminal_di_per_year)
        if dt_ is not None:
            ts = _switch_time_years(di, b, dt_); qs = qi * (1.0 + b * di * ts) ** (-1.0 / b)
            q = np.where(t > ts, qs * np.exp(-dt_ * (t - ts)), q)
    if q_abandon:
        q = np.where(q < float(q_abandon), 0.0, q)
    return float(q) if q.ndim == 0 else q


def arps_abandonment_time(qi, di_per_year, b=0.0, terminal_di_per_year=None, q_abandon=None):
    """Time [days] at which the rate falls to ``q_abandon`` (inf if never / not set)."""
    if not q_abandon or q_abandon <= 0 or di_per_year <= 0 or q_abandon >= qi: return 0.0 if (q_abandon and q_abandon >= qi) else math.inf
    di = float(di_per_year); b = min(max(float(b or 0.0), 0.0), 2.0)
    if b < 1e-6: return math.log(qi / q_abandon) / di * DAYS_PER_YEAR
    ta = ((qi / q_abandon) ** b - 1.0) / (b * di)
    dt_ = _terminal(di, b, terminal_di_per_year)
    if dt_ is not None:
        ts = _switch_time_years(di, b, dt_)
        if ta > ts:
            qs = qi * (1.0 + b * di * ts) ** (-1.0 / b)
            ta = ts + math.log(qs / q_abandon) / dt_
    return ta * DAYS_PER_YEAR


def arps_cumulative(t_days, qi, di_per_year, b=0.0, terminal_di_per_year=None, q_abandon=None):
    """Cumulative production [rate-unit * days] from t0 to ``t_days`` (closed forms; stops at abandonment)."""
    t_in = np.asarray(t_days, dtype=float)
    ta = arps_abandonment_time(qi, di_per_year, b, terminal_di_per_year, q_abandon)
    t = np.minimum(np.maximum(t_in, 0.0), ta) / DAYS_PER_YEAR
    qi = float(qi); di = max(float(di_per_year), 0.0); b = min(max(float(b or 0.0), 0.0), 2.0)
    if di <= 0:
        out = qi * t * DAYS_PER_YEAR
    elif b < 1e-6:
        out = qi / di * (1.0 - np.exp(-di * t)) * DAYS_PER_YEAR
    else:
        dt_ = _terminal(di, b, terminal_di_per_year)
        ts = _switch_time_years(di, b, dt_) if dt_ is not None else math.inf
        th = np.minimum(t, ts)
        if abs(b - 1.0) < 1e-9:
            hyp = qi / di * np.log1p(di * th)
        else:
            hyp = qi / ((1.0 - b) * di) * (1.0 - (1.0 + b * di * th) ** (1.0 - 1.0 / b))
        out = hyp
        if dt_ is not None:
            qs = qi * (1.0 + b * di * ts) ** (-1.0 / b)
            out = hyp + np.where(t > ts, qs / dt_ * (1.0 - np.exp(-dt_ * np.maximum(t - ts, 0.0))), 0.0)
        out = out * DAYS_PER_YEAR
    return float(out) if np.ndim(out) == 0 else out


def fit_arps(times, rates, b=None):
    """Fit an Arps decline to (time [days], rate) data with scipy ``curve_fit``.

    ``b=None`` fits b in [0, 2]; a number fixes it. Time is measured from ``times[0]`` so ``qi`` is the rate
    at the first point. Returns dict(success, message, qi, di_per_year, b, r2, t0_days, n). Never raises on bad data.
    """
    res = {'success': False, 'message': '', 'qi': None, 'di_per_year': None, 'b': b, 'r2': None, 't0_days': None, 'n': 0}
    try:
        t = np.asarray(times, dtype=float).ravel(); q = np.asarray(rates, dtype=float).ravel()
    except (TypeError, ValueError):
        res['message'] = 'Times and rates must be numeric.'; return res
    if t.shape != q.shape:
        res['message'] = 'Times and rates must have the same length.'; return res
    ok = np.isfinite(t) & np.isfinite(q) & (q > 0)
    t, q = t[ok], q[ok]; order = np.argsort(t); t, q = t[order], q[order]; res['n'] = int(len(t))
    need = 3 if b is not None else 4
    if len(t) < need:
        res['message'] = f'Need at least {need} positive-rate points to fit (got {len(t)}).'; return res
    if np.ptp(t) <= 0:
        res['message'] = 'All times are identical.'; return res
    from scipy.optimize import curve_fit
    t0 = float(t[0]); x = t - t0; res['t0_days'] = t0
    slope = np.polyfit(x / DAYS_PER_YEAR, np.log(q), 1)
    d0 = min(max(-slope[0], 1e-3), 20.0); q0 = float(q[0])
    try:
        if b is None:
            f = lambda xx, qi, di, bb: arps_rate(xx, qi, di, bb)
            p0 = [q0, d0, 0.5]; bounds = ([q0 * 1e-3, 1e-6, 0.0], [q0 * 1e3, 100.0, 2.0])
        else:
            bb0 = float(b)
            if not 0.0 <= bb0 <= 2.0:
                res['message'] = 'b must be between 0 and 2.'; return res
            f = lambda xx, qi, di: arps_rate(xx, qi, di, bb0)
            p0 = [q0, d0]; bounds = ([q0 * 1e-3, 1e-6], [q0 * 1e3, 100.0])
        popt, _ = curve_fit(f, x, q, p0=p0, bounds=bounds, x_scale=[q0, 1.0] + ([1.0] if b is None else []), maxfev=20000, xtol=1e-12, ftol=1e-12, gtol=1e-12)
    except Exception as e:  # RuntimeError (no convergence), ValueError, ...
        res['message'] = f'Arps fit failed: {e}'; return res
    pred = f(x, *popt); ss_res = float(np.sum((q - pred) ** 2)); ss_tot = float(np.sum((q - q.mean()) ** 2))
    res.update(success=True, message='ok', qi=float(popt[0]), di_per_year=float(popt[1]), b=float(popt[2]) if b is None else float(b),
               r2=1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0)
    return res


# ---------------------------------------------------------------------------------------------
# Interpolation helpers
# ---------------------------------------------------------------------------------------------
def _to_date(d):
    if isinstance(d, datetime): return d
    if hasattr(d, 'isoformat') and not isinstance(d, str): return datetime.fromisoformat(d.isoformat())
    try:
        return datetime.fromisoformat(str(d).strip()[:19])
    except ValueError:
        raise ValueError(f"Cannot read date {d!r}; use ISO format YYYY-MM-DD.")


def _days_between(d0, d1):
    return (_to_date(d1) - _to_date(d0)).total_seconds() / 86400.0


def _isnum(v):
    if v is None or isinstance(v, bool): return False
    try: return math.isfinite(float(v))
    except (TypeError, ValueError): return False


def _extrap_mode(v):
    if v is True: return 'linear'
    if v is False or v is None: return 'hold'
    v = str(v).lower()
    return v if v in ('hold', 'linear', 'none') else 'hold'


def _series(rows, col, xkey_resolver):
    """Collect finite (x, y) pairs for ``col`` sorted by x (duplicates: last wins)."""
    pts = {}
    for r in rows or []:
        if not _isnum(r.get(col)): continue
        x = xkey_resolver(r)
        if x is None: continue
        pts[x] = float(r[col])
    xs = np.array(sorted(pts)); ys = np.array([pts[k] for k in xs])
    return xs, ys


def _interp(x, xs, ys, interp='linear', extrapolate='hold'):
    if len(xs) == 0: return None
    if len(xs) == 1: return float(ys[0]) if (extrapolate != 'none' or x == xs[0]) else None
    if x < xs[0] or x > xs[-1]:
        if extrapolate == 'none': return None
        if extrapolate == 'linear' and interp == 'linear':
            i = (0, 1) if x < xs[0] else (-2, -1)
            sl = (ys[i[1]] - ys[i[0]]) / (xs[i[1]] - xs[i[0]]) if xs[i[1]] != xs[i[0]] else 0.0
            ref = 0 if x < xs[0] else -1
            return float(ys[ref] + sl * (x - xs[ref]))
        return float(ys[0] if x < xs[0] else ys[-1])
    if interp == 'step':
        k = int(np.searchsorted(xs, x, side='right')) - 1
        return float(ys[max(k, 0)])
    return float(np.interp(x, xs, ys))


def _clamp(col, v):
    if col == 'water_cut': return min(max(v, 0.0), 0.9999)
    if col == 'reservoir_pressure_bar': return max(v, 0.1)
    if col == 'skin': return v
    return max(v, 0.0)


def _row_x_factory(src, start_date):
    """Return (x_axis, resolver(row)->x) for an external table; resolver gives days since reference or cum oil."""
    axis = str(src.get('x_axis', 'date')).lower()
    if axis not in X_AXES:
        raise ValueError(f"prediction_source x_axis must be one of {', '.join(X_AXES)}, got '{src.get('x_axis')}'.")
    ref = src.get('t0') or start_date
    if axis == 'cum_oil_sm3':
        return axis, lambda r: float(r['cum_oil_sm3']) if _isnum(r.get('cum_oil_sm3')) else None
    def res(r):
        if r.get('date') not in (None, '') and axis == 'date':
            return _days_between(ref, r['date'])
        if _isnum(r.get('time_days')): return float(r['time_days'])
        if r.get('date') not in (None, ''): return _days_between(ref, r['date'])
        return None
    return axis, res


def _table_value(rows, key, date, start_date, ref, well_state, interp='linear', extrapolate='hold'):
    """Value of a small [{'date'|'time_days'|'cum_oil_sm3', 'value'}] table (wc / gor tables of a decline source)."""
    if not rows: return None
    use_cum = any(_isnum(r.get('cum_oil_sm3')) for r in rows)
    def res(r):
        if use_cum: return float(r['cum_oil_sm3']) if _isnum(r.get('cum_oil_sm3')) else None
        if r.get('date') not in (None, ''): return _days_between(ref, r['date'])
        return float(r['time_days']) if _isnum(r.get('time_days')) else None
    rr = [{**r, '_v': r.get('value', r.get(key))} for r in rows]
    xs, ys = _series(rr, '_v', res)
    x = float((well_state or {}).get('cum_oil', 0.0)) if use_cum else _days_between(ref, date)
    return _interp(x, xs, ys, interp, extrapolate)


# ---------------------------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------------------------
def decline_potential(src, date, start_date):
    """Decline potential [rate units of the basis] at ``date``."""
    t0 = src.get('t0') or start_date
    t = max(_days_between(t0, date), 0.0)
    return float(arps_rate(t, float(src['qi']), float(src.get('di_per_year', 0.0)), float(src.get('b', 0.0) or 0.0),
                           src.get('terminal_di_per_year'), src.get('q_abandon')))


def apply_prediction_sources(nodes, date, start_date, state=None):
    """Return a deep copy of ``nodes`` with decline caps / external-table values applied for ``date``.

    ``state`` maps well id -> {'cum_oil', ...}; used for cumulative-oil x axes. Unknown/none sources are ignored.
    """
    ns = copy.deepcopy(nodes); state = state or {}
    for n in ns:
        if n.get('kind') != 'well': continue
        prm = n.get('params') or {}
        src = prm.get('prediction_source')
        if not isinstance(src, dict): continue
        typ = str(src.get('type', 'none')).lower()
        if typ in ('none', ''): continue
        ws = state.get(n['id'], {})
        n['params'] = prm
        if typ == 'decline':
            _apply_decline(prm, src, date, start_date, ws)
        elif typ == 'external_table':
            _apply_table(prm, src, date, start_date, ws)
        else:
            raise ValueError(f"Well '{n.get('name', n['id'])}': unknown prediction_source type '{src.get('type')}' (use none, decline or external_table).")
        prm['_prediction_active'] = typ
    return ns


def _apply_decline(prm, src, date, start_date, ws):
    for k in ('qi',):
        if not _isnum(src.get(k)): raise ValueError("Decline prediction source needs a numeric 'qi' (rate at t0).")
    basis = str(src.get('basis', 'oil')).lower()
    if basis not in ('oil', 'liquid', 'gas'): raise ValueError("Decline basis must be 'oil', 'liquid' or 'gas'.")
    ref = src.get('t0') or start_date
    for key, col, lo, hi in (('water_cut_table', 'water_cut', 0.0, 0.9999), ('gor_table', 'gor_sm3sm3', 0.0, math.inf)):
        v = _table_value(src.get(key), col, date, start_date, ref, ws, src.get('interp', 'linear'), 'hold')
        if v is not None: prm[col] = min(max(v, lo), hi)
    if str(src.get('apply_as', 'rate_cap')).lower() != 'rate_cap': return
    q = decline_potential(src, date, start_date)
    wc = min(max(float(prm.get('water_cut', 0.0) or 0.0), 0.0), 0.9999)
    if basis == 'oil': cap = q / (1.0 - wc)
    elif basis == 'liquid': cap = q
    else:
        gor = float(prm.get('gor_sm3sm3', 0.0) or 0.0)
        cap = q / (max(gor, 1e-9) * (1.0 - wc))
    old = prm.get('_potential_cap_m3d')
    prm['_potential_cap_m3d'] = min(cap, float(old)) if _isnum(old) else cap
    prm['_decline_potential'] = q


def _apply_table(prm, src, date, start_date, ws):
    rows = src.get('rows') or []
    if not rows: return
    axis, res = _row_x_factory(src, start_date)
    interp = str(src.get('interp', 'linear')).lower(); interp = interp if interp in ('linear', 'step') else 'linear'
    extrap = _extrap_mode(src.get('extrapolate', 'hold'))
    ref = src.get('t0') or start_date
    x = float(ws.get('cum_oil', 0.0)) if axis == 'cum_oil_sm3' else _days_between(ref, date)
    vals = {}
    for col in TABLE_COLUMNS:
        xs, ys = _series(rows, col, res)
        v = _interp(x, xs, ys, interp, extrap)
        if v is not None: vals[col] = _clamp(col, v)
    mo = vals.pop('max_oil_rate_m3d', None)
    for col, v in vals.items(): prm[col] = v
    if mo is not None:
        wc = min(max(float(prm.get('water_cut', 0.0) or 0.0), 0.0), 0.9999); cap = mo / (1.0 - wc)
        old = prm.get('_potential_cap_m3d'); prm['_potential_cap_m3d'] = min(cap, float(old)) if _isnum(old) else cap


# ---------------------------------------------------------------------------------------------
# Preview for the UI
# ---------------------------------------------------------------------------------------------
def prediction_preview(source, start_date, years=5, step_days=30) -> pd.DataFrame:
    """Time series of what a source delivers: Date, Day, plus 'Rate' / 'Cumulative' (decline) or the table columns."""
    src = source or {}; typ = str(src.get('type', 'none')).lower()
    t0 = _to_date(start_date); step = max(float(step_days), 1.0)
    days = np.arange(0.0, years * DAYS_PER_YEAR + 1e-9, step)
    dates = [(t0 + timedelta(days=float(d))).date().isoformat() for d in days]
    df = pd.DataFrame({'Date': dates, 'Day': days})
    if typ == 'decline':
        ref_off = _days_between(start_date, src.get('t0') or start_date); t = days - ref_off
        args = (float(src['qi']), float(src.get('di_per_year', 0.0)), float(src.get('b', 0.0) or 0.0), src.get('terminal_di_per_year'), src.get('q_abandon'))
        df[f"Rate ({src.get('basis', 'oil')})"] = arps_rate(t, *args)
        df['Cumulative'] = arps_cumulative(t, *args)
        for key, col in (('water_cut_table', 'water_cut'), ('gor_table', 'gor_sm3sm3')):
            if src.get(key):
                df[col] = [_table_value(src[key], col, d, start_date, src.get('t0') or start_date, {}, src.get('interp', 'linear'), 'hold') for d in dates]
    elif typ == 'external_table':
        axis, res = _row_x_factory(src, start_date)
        interp = str(src.get('interp', 'linear')).lower(); extrap = _extrap_mode(src.get('extrapolate', 'hold'))
        ref = src.get('t0') or start_date
        if axis == 'cum_oil_sm3':
            allx = sorted({res(r) for r in src.get('rows') or [] if res(r) is not None})
            if not allx: return pd.DataFrame()
            df = pd.DataFrame({'cum_oil_sm3': np.linspace(allx[0], allx[-1], 60)})
            xv = df['cum_oil_sm3'].to_numpy()
        else:
            xv = days - _days_between(start_date, ref)
        for col in TABLE_COLUMNS:
            xs, ys = _series(src.get('rows') or [], col, res)
            if len(xs):
                df[col] = [_interp(float(x), xs, ys, interp, extrap) for x in xv]
    return df


# ---------------------------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------------------------
_ALIASES = {
    'date': ('date', 'datetime', 'dates', 'timestamp'),
    'time_days': ('time', 'timedays', 'days', 'day', 't', 'timeday', 'elapsed', 'timeyears', 'years', 'year', 'timeyr', 'yr'),
    'reservoir_pressure_bar': ('pres', 'pressure', 'pr', 'pavg', 'pave', 'reservoirpressure', 'respressure', 'fpr', 'avgpressure', 'pressurebar', 'prbar'),
    'pi_m3d_bar': ('pi', 'productivityindex', 'j', 'prodindex'),
    'water_cut': ('wct', 'watercut', 'wcut', 'wc', 'fw', 'wcutfrac'),
    'gor_sm3sm3': ('gor', 'gasoilratio', 'rsgor', 'gasoil', 'gorsm3sm3'),
    'max_liquid_rate_m3d': ('rate', 'liqrate', 'liquidrate', 'qliq', 'qtot', 'maxliquidrate', 'totalrate', 'liquid', 'liqpotential', 'potential'),
    'max_oil_rate_m3d': ('oilrate', 'qo', 'maxoilrate', 'orat', 'opr', 'oilpotential', 'oil', 'oilrate'),
    'skin': ('skin', 's'),
    'cum_oil_sm3': ('cumoil', 'np', 'cumulativeoil', 'fopt', 'cumoilprod', 'cumulativeoilproduction'),
}
_ALIAS_LOOKUP = {a: k for k, v in _ALIASES.items() for a in v}


def _split_header(h):
    """'Pres (psi)' -> ('pres', 'psi'); 'WCT [%]' -> ('wct', '%')."""
    import re
    h = str(h).strip(); unit = ''
    m = re.search(r'[\(\[\{](.*?)[\)\]\}]\s*$', h)
    if m: unit = m.group(1).strip().lower(); h = h[:m.start()].strip()
    name = re.sub(r'[^a-z0-9]', '', h.lower())
    if not unit:  # trailing unit words: 'pressure_psi', 'rate stb/d'
        m = re.match(r'^(.*?)[\s_]+(psia|psig|psi|bar|barg|kpa|mpa|pct|percent|stb/d|bopd|bbl/d|stb/day|sm3/d|m3/d|scf/stb|mscf/stb|sm3/sm3|years?|yrs?|days?)$', h.lower())
        if m: name = re.sub(r'[^a-z0-9]', '', m.group(1)); unit = m.group(2)
    return name, unit.replace(' ', '')


def parse_external_csv(text_or_df):
    """Parse a simulator export (CSV text or DataFrame) into canonical table rows.

    Returns ``(rows, warnings)``. ``rows`` is a list of dicts with canonical keys (``date`` ISO string or
    ``time_days``, plus any of TABLE_COLUMNS / ``cum_oil_sm3``); blank/NaN cells are omitted. ``warnings`` lists every
    assumption (unit defaults, % vs fraction, ignored columns).
    """
    warnings = []
    if isinstance(text_or_df, pd.DataFrame): df = text_or_df.copy()
    else:
        txt = str(text_or_df)
        if not txt.strip(): raise ValueError("The external table is empty.")
        df = pd.read_csv(io.StringIO(txt), sep=None, engine='python', skipinitialspace=True)
    if df.empty: raise ValueError("The external table has no data rows.")
    cols = {}   # canonical -> (original column, unit)
    for c in df.columns:
        name, unit = _split_header(c)
        key = _ALIAS_LOOKUP.get(name)
        if key is None:
            warnings.append(f"Column '{c}' not recognised - ignored."); continue
        if key == 'time_days' and 'date' in cols: warnings.append(f"Column '{c}' ignored because a date column is present."); continue
        if key in cols: warnings.append(f"Column '{c}' duplicates '{cols[key][0]}' - ignored."); continue
        cols[key] = (c, unit)
    if 'date' not in cols and 'time_days' not in cols and 'cum_oil_sm3' not in cols:
        raise ValueError("No time axis found: add a Date, Time (days) or Cum oil column. Recognised headers: Date, Time, Days, Cum oil, Np.")
    out = pd.DataFrame(index=df.index)
    for key, (c, unit) in cols.items():
        if key == 'date':
            parsed = pd.to_datetime(df[c], errors='coerce')
            if parsed.isna().all(): raise ValueError(f"Column '{c}' could not be read as dates (use ISO YYYY-MM-DD).")
            if parsed.isna().any(): warnings.append(f"{int(parsed.isna().sum())} rows with unreadable dates skipped.")
            out[key] = parsed.dt.strftime('%Y-%m-%d'); out.loc[parsed.isna(), key] = None; continue
        v = pd.to_numeric(df[c], errors='coerce')
        if key == 'time_days':
            if unit.startswith('y'): v = v * DAYS_PER_YEAR; warnings.append(f"'{c}' interpreted in years -> days.")
            elif not unit or unit.startswith('d'):
                if unit == '' and c.strip().lower() in ('years', 'year', 'time_years', 'timeyears'): v = v * DAYS_PER_YEAR; warnings.append(f"'{c}' interpreted in years -> days.")
                elif unit == '': warnings.append(f"'{c}': no unit given, assuming days (relative to forecast start).")
        elif key == 'reservoir_pressure_bar':
            if unit in ('psi', 'psia'): v = v * _PSI_BAR; warnings.append(f"'{c}': psi -> bar.")
            elif unit == 'psig': v = (v + 14.696) * _PSI_BAR; warnings.append(f"'{c}': psig -> bar (added 14.696 psi).")
            elif unit == 'kpa': v = v / 100.0; warnings.append(f"'{c}': kPa -> bar.")
            elif unit == 'mpa': v = v * 10.0; warnings.append(f"'{c}': MPa -> bar.")
            elif unit in ('bar', 'barg', 'barsa'):
                pass
            else: warnings.append(f"'{c}': no pressure unit given, assuming bar.")
        elif key == 'water_cut':
            pct = unit in ('%', 'pct', 'percent')
            if not pct and unit not in ('fraction', 'frac', 'sm3/sm3', 'v/v') and v.max(skipna=True) > 1.0:
                pct = True; warnings.append(f"'{c}': values exceed 1, interpreted as percent.")
            elif not pct and not unit: warnings.append(f"'{c}': no unit given, assuming fraction.")
            if pct:
                v = v / 100.0
                if unit in ('%', 'pct', 'percent'): warnings.append(f"'{c}': % -> fraction.")
        elif key == 'gor_sm3sm3':
            if unit in ('scf/stb',): v = v * _SCF_SM3 / _STB_M3; warnings.append(f"'{c}': scf/stb -> Sm3/Sm3.")
            elif unit in ('mscf/stb',): v = v * 1000 * _SCF_SM3 / _STB_M3; warnings.append(f"'{c}': Mscf/stb -> Sm3/Sm3.")
            elif not unit: warnings.append(f"'{c}': no unit given, assuming Sm3/Sm3.")
        elif key in ('max_liquid_rate_m3d', 'max_oil_rate_m3d'):
            if unit in ('stb/d', 'bopd', 'bbl/d', 'stb/day', 'stbd', 'bpd'): v = v * _STB_M3; warnings.append(f"'{c}': stb/d -> m3/d.")
            elif not unit: warnings.append(f"'{c}': no unit given, assuming m3/d.")
        elif key == 'pi_m3d_bar':
            if unit in ('stb/d/psi', 'bbl/d/psi', 'stb/psi/d'): v = v * _STB_M3 / _PSI_BAR; warnings.append(f"'{c}': stb/d/psi -> m3/d/bar.")
            elif not unit: warnings.append(f"'{c}': no unit given, assuming m3/d/bar.")
        elif key == 'cum_oil_sm3':
            mult = {'mstb': 1e3 * _STB_M3, 'mmstb': 1e6 * _STB_M3, 'stb': _STB_M3, 'msm3': 1e6, 'mm3': 1e6}.get(unit)
            if mult: v = v * mult; warnings.append(f"'{c}': {unit} -> Sm3.")
            elif not unit: warnings.append(f"'{c}': no unit given, assuming Sm3.")
        out[key] = v
    rows = []
    for _, r in out.iterrows():
        d = {k: (v if k == 'date' else float(v)) for k, v in r.items() if v is not None and not (isinstance(v, float) and math.isnan(v)) and not (v is pd.NA)}
        if any(k in d for k in ('date', 'time_days', 'cum_oil_sm3')) and len(d) > 1: rows.append(d)
    if not rows: raise ValueError("No usable rows found in the external table.")
    return rows, warnings
