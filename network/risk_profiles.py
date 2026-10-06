"""Probabilistic production profiles for the TOTAL SYSTEM (pure numpy/pandas, no plotly).

Convention (industry, identical to ``network.uncertainty.percentile_summary``):
    P90 = LOW case  = 10th percentile of the realization values
    P50 = median
    P10 = HIGH case = 90th percentile of the realization values
    Mean = arithmetic mean over realizations

Cumulatives are computed PER REALIZATION (from that realization's own cumulative columns, or
rate x interval) and only then percentiled. The cumulative of the percentile rates is a different
(and wrong) quantity: P90 of cumulative is not the integral of P90 rate.

Realizations may differ in length or fail: series are aligned on Date; missing values are ignored
and the number of contributing realizations is reported per row as ``N``. A realization that ends
early therefore drops out of later dates (its cumulative is NOT carried forward).
"""
from __future__ import annotations

import fnmatch
import io
import math
import warnings
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

PROFILE_COLUMNS = ["Date", "Day", "Variable", "Category", "Unit", "P10", "P50", "P90", "Mean", "Min", "Max", "N"]
CONVENTION_NOTE = "P90 = low case (10th percentile of values); P10 = high case (90th percentile); P50 = median"

# (variable, forecast column, unit)
RATE_SPECS = [
    ("Oil rate", "Oil [m3/d]", "m3/d"),
    ("Gas rate", "Gas [Sm3/d]", "Sm3/d"),
    ("Water rate", "Water [m3/d]", "m3/d"),
    ("Liquid rate", "Total liquid [m3/d]", "m3/d"),
    ("Water injection rate", "Water injection [m3/d]", "m3/d"),
    ("Gas injection rate", "Gas injection [Sm3/d]", "Sm3/d"),
]
# (cumulative variable, rate variable, candidate cumulative columns, unit)
CUM_SPECS = [
    ("Cumulative oil", "Oil rate", ("Cumulative oil [m3]", "Cumulative oil [Sm3]"), "m3"),
    ("Cumulative gas", "Gas rate", ("Cumulative gas [Sm3]",), "Sm3"),
    ("Cumulative water", "Water rate", ("Cumulative water [m3]",), "m3"),
    ("Cumulative liquid", "Liquid rate", ("Cumulative liquid [m3]",), "m3"),
    ("Cumulative water injection", "Water injection rate", ("Cumulative water injection [m3]",), "m3"),
    ("Cumulative gas injection", "Gas injection rate", ("Cumulative gas injection [Sm3]",), "Sm3"),
]
SYSTEM_PRESSURE = "System pressure (mean tank)"
TANK_PREFIX = "Tank pressure: "
NODE_PREFIX = "Node pressure: "
NODE_GROUP_PREFIX = "Mean node pressure: "
MAX_NODE_SERIES = 300


# ----------------------------------------------------------------------------- helpers
def _date_str(v: Any) -> str:
    return str(v)[:10]


def _col(rows: Sequence[dict], key: str, n: int | None = None) -> np.ndarray:
    out = np.full(len(rows), np.nan)
    for i, r in enumerate(rows):
        v = r.get(key)
        if v is None:
            continue
        try:
            out[i] = float(v)
        except (TypeError, ValueError):
            pass
    return out


def _has(rows: Sequence[dict], key: str) -> bool:
    return any(key in r and r.get(key) is not None for r in rows)


def _wanted(name: str, patterns: Iterable[str] | None) -> bool:
    if patterns is None:
        return True
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def variable_group(name: str) -> str:
    """Display group of a variable name: Rate / Cumulative / System / Tank / Node group / Node."""
    if name.startswith(NODE_PREFIX):
        return "Node"
    if name.startswith(NODE_GROUP_PREFIX):
        return "Node group"
    if name.startswith(TANK_PREFIX):
        return "Tank"
    if name == SYSTEM_PRESSURE:
        return "System"
    if name.startswith("Cumulative"):
        return "Cumulative"
    return "Rate"


def _interval_days(rows: Sequence[dict], dates: list[str]) -> np.ndarray:
    n = len(rows)
    dt = _col(rows, "Interval days")
    if n and np.isfinite(dt).all():
        return dt
    day = _col(rows, "Day")
    if n and not np.isfinite(day).any():
        try:
            ords = np.array([pd.Timestamp(d).toordinal() for d in dates], float)
            day = ords - ords[0]
        except Exception:
            day = np.arange(n, dtype=float)
    d = np.zeros(n)
    if n > 1:
        d[:-1] = np.diff(day)
    return np.nan_to_num(np.clip(d, 0, None))


# ----------------------------------------------------------------------------- extraction
def _fc_of(result: dict) -> dict:
    fc = result.get("forecast")
    return fc if isinstance(fc, dict) else result


def extract_run_series(result: dict, variables: Iterable[str] | None = None, include_nodes: bool = True,
                       node_kinds: dict[str, str] | None = None, cumulative_source: str = "columns",
                       dtype=np.float32) -> dict | None:
    """Compact per-realization series: {'dates','days','vars':{name: 1-D array},'meta':{name:(cat,unit)},'notes'}.

    ``result`` is a development result ({'forecast': {...}}) or a raw forecast result ({'field': [...]}).
    Returns None when there are no field rows. ``variables`` filters by exact name or fnmatch pattern.
    """
    fc = _fc_of(result)
    rows = list(fc.get("field") or [])
    if not rows:
        return None
    variables = None if variables is None else list(variables)
    dates = [_date_str(r.get("Date")) for r in rows]
    days = _col(rows, "Day")
    if not np.isfinite(days).any():
        days = np.arange(len(rows), dtype=float)
    dt = _interval_days(rows, dates)
    out: dict[str, np.ndarray] = {}
    meta: dict[str, tuple[str, str]] = {}
    notes: list[str] = []

    def put(name, arr, cat, unit):
        if _wanted(name, variables):
            out[name] = np.asarray(arr, dtype=dtype)
            meta[name] = (cat, unit)

    rates: dict[str, np.ndarray] = {}
    for var, col, unit in RATE_SPECS:
        if _has(rows, col):
            rates[var] = _col(rows, col)
            put(var, rates[var], "Rate", unit)
    liq, oil, wat, gas = rates.get("Liquid rate"), rates.get("Oil rate"), rates.get("Water rate"), rates.get("Gas rate")
    if liq is None and oil is not None and wat is not None:
        liq = oil + wat
        rates["Liquid rate"] = liq
        put("Liquid rate", liq, "Rate", "m3/d")
    if liq is not None and wat is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            put("Water cut", np.where(liq > 0, 100.0 * wat / liq, 0.0), "Rate", "%")
    if oil is not None and gas is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            put("GOR", np.where(oil > 0, gas / oil, 0.0), "Rate", "Sm3/Sm3")
    for cvar, rvar, cols, unit in CUM_SPECS:
        arr = None
        if cumulative_source == "columns":
            for c in cols:
                if _has(rows, c):
                    arr = _col(rows, c)
                    break
        if arr is None and rvar in rates:
            arr = np.cumsum(np.nan_to_num(rates[rvar]) * dt)
        if arr is not None:
            put(cvar, arr, "Cumulative", unit)
    # pressures
    tank_cols = []
    for r in rows:
        for k in r:
            if isinstance(k, str) and k.startswith("P ") and k.endswith(" [bar]") and k not in tank_cols:
                tank_cols.append(k)
    tank_arrs = []
    for k in tank_cols:
        a = _col(rows, k)
        tank_arrs.append(a)
        put(TANK_PREFIX + k[2:-6], a, "Pressure", "bar")
    if tank_arrs:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            put(SYSTEM_PRESSURE, np.nanmean(np.vstack(tank_arrs), axis=0), "Pressure", "bar")
    if include_nodes:
        _extract_nodes(fc.get("nodes"), dates, node_kinds or {}, put, notes)
    return {"dates": dates, "days": [float(x) for x in days], "vars": out, "meta": meta, "notes": notes}


def _extract_nodes(node_rows, dates, node_kinds, put, notes):
    node_rows = list(node_rows or [])
    if not node_rows:
        return
    idx = {d: i for i, d in enumerate(dates)}
    series: dict[str, np.ndarray] = {}
    names: dict[str, str] = {}
    kinds: dict[str, str] = {}
    pkey = None
    for r in node_rows[:50]:
        pkey = next((k for k in r if isinstance(k, str) and k.lower().startswith("pressure")), None)
        if pkey:
            break
    if pkey is None:
        return
    for r in node_rows:
        nid = r.get("Node ID", r.get("Node", r.get("ID")))
        j = idx.get(_date_str(r.get("Date")))
        if nid is None or j is None:
            continue
        nid = str(nid)
        try:
            v = float(r.get(pkey))
        except (TypeError, ValueError):
            continue
        if nid not in series:
            series[nid] = np.full(len(dates), np.nan)
            names[nid] = str(r.get("Name") or nid)
            k = r.get("Kind") or node_kinds.get(nid)
            if k:
                kinds[nid] = str(k)
        series[nid][j] = v
    groups: dict[str, list[np.ndarray]] = {}
    for nid, a in series.items():
        if nid in kinds:
            groups.setdefault(kinds[nid], []).append(a)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for kind, arrs in groups.items():
            put(NODE_GROUP_PREFIX + kind, np.nanmean(np.vstack(arrs), axis=0), "Pressure", "bar")
    if len(series) > MAX_NODE_SERIES:
        notes.append(f"{len(series)} nodes: individual node pressures skipped (limit {MAX_NODE_SERIES}); group means kept")
        return
    for nid, a in series.items():
        put(f"{NODE_PREFIX}{names[nid]} ({nid})", a, "Pressure", "bar")


def _is_run_series(x: Any) -> bool:
    return isinstance(x, dict) and "vars" in x and "dates" in x


def collect_series(run_results: Iterable[Any], variables: Iterable[str] | None = None, include_nodes: bool = True,
                   node_kinds: dict[str, str] | None = None, cumulative_source: str = "columns",
                   dtype=np.float64) -> dict:
    """Align per-realization series on Date into 2-D arrays (realizations x dates).

    ``run_results`` items may be development results, forecast results, compact outputs of
    ``extract_run_series``, or ``None`` / failed markers (skipped). Returns
    {'dates','days','variables':{name:{'category','unit','values'}},'n_runs','n_skipped','notes'}.
    """
    compact = []
    skipped = 0
    notes: list[str] = []
    for r in run_results:
        if r is None or isinstance(r, BaseException) or (isinstance(r, dict) and r.get("success") is False):
            skipped += 1
            continue
        c = r if _is_run_series(r) else extract_run_series(r, variables, include_nodes, node_kinds, cumulative_source, dtype)
        if c is None:
            skipped += 1
            continue
        compact.append(c)
        for n in c.get("notes", []):
            if n not in notes:
                notes.append(n)
    dates = sorted({d for c in compact for d in c["dates"]})
    col_of = {d: i for i, d in enumerate(dates)}
    day_of: dict[str, float] = {}
    for c in compact:
        for d, dy in zip(c["dates"], c["days"]):
            day_of.setdefault(d, dy)
    names: dict[str, tuple[str, str]] = {}
    for c in compact:
        for k, m in c["meta"].items():
            if _wanted(k, variables):
                names.setdefault(k, tuple(m))
    nr, nt = len(compact), len(dates)
    vars_out = {k: {"category": m[0], "unit": m[1], "values": np.full((nr, nt), np.nan, dtype=dtype)} for k, m in names.items()}
    for i, c in enumerate(compact):
        cols = np.fromiter((col_of[d] for d in c["dates"]), dtype=int, count=len(c["dates"]))
        for k, arr in c["vars"].items():
            if k in vars_out:
                vars_out[k]["values"][i, cols] = np.asarray(arr, dtype=dtype)
    return {"dates": dates, "days": [day_of[d] for d in dates], "variables": vars_out, "n_runs": nr, "n_skipped": skipped, "notes": notes}


# ----------------------------------------------------------------------------- percentiles
def _pcol(p: float) -> str:
    return f"P{int(p)}" if float(p) == int(p) else f"P{p:g}"


def percentile_profiles(series: dict, percentiles: Sequence[float] = (10, 50, 90)) -> pd.DataFrame:
    """Tidy percentile profiles. P-label k maps to the (100-k)th percentile of values (P90 = low case)."""
    cols_p = [_pcol(p) for p in percentiles]
    frames = []
    dates, days = series["dates"], np.asarray(series["days"], float)
    for name, v in series["variables"].items():
        x = np.asarray(v["values"], dtype=float)
        if x.ndim != 2 or x.shape[1] == 0:
            continue
        n = np.sum(np.isfinite(x), axis=0)
        data = {"Date": dates, "Day": days, "Variable": name, "Category": v["category"], "Unit": v["unit"]}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            for p, c in zip(percentiles, cols_p):
                data[c] = np.nanpercentile(x, 100.0 - float(p), axis=0) if x.shape[0] else np.full(x.shape[1], np.nan)
            data["Mean"] = np.nanmean(x, axis=0) if x.shape[0] else np.full(x.shape[1], np.nan)
            data["Min"] = np.nanmin(x, axis=0) if x.shape[0] else np.full(x.shape[1], np.nan)
            data["Max"] = np.nanmax(x, axis=0) if x.shape[0] else np.full(x.shape[1], np.nan)
        data["N"] = n.astype(int)
        frames.append(pd.DataFrame(data))
    order = ["Date", "Day", "Variable", "Category", "Unit", *cols_p, "Mean", "Min", "Max", "N"]
    if not frames:
        return pd.DataFrame(columns=order)
    return pd.concat(frames, ignore_index=True)[order]


def profiles_to_wide(df: pd.DataFrame, stats: Sequence[str] = ("P90", "P50", "P10", "Mean"), variables: Sequence[str] | None = None) -> pd.DataFrame:
    """One row per Date; columns '<Variable> [<Unit>] <stat>'."""
    d = df if variables is None else df[df["Variable"].isin(list(variables))]
    if d.empty:
        return pd.DataFrame(columns=["Date", "Day"])
    base = d[["Date", "Day"]].drop_duplicates("Date").set_index("Date")
    parts = [base]
    for (var, unit), g in d.groupby(["Variable", "Unit"], sort=False):
        gg = g.set_index("Date")
        parts.append(pd.DataFrame({f"{var} [{unit}] {s}": gg[s] for s in stats if s in gg}))
    return pd.concat(parts, axis=1).reset_index()


def profiles_to_csv(df: pd.DataFrame, wide: bool = False, **kw) -> str:
    out = profiles_to_wide(df, **kw) if wide else df
    buf = io.StringIO()
    out.to_csv(buf, index=False)
    return buf.getvalue()


def _clean(v: Any) -> Any:
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return float(f"{f:.7g}") if math.isfinite(f) else None
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


def profiles_to_records(df: pd.DataFrame) -> list[dict]:
    """JSON-friendly records: NaN -> None, floats rounded to 7 significant digits."""
    return [{k: _clean(v) for k, v in rec.items()} for rec in df.to_dict("records")]


def profiles_from_records(records: Iterable[dict]) -> pd.DataFrame:
    """Rebuild the profile DataFrame from ``profiles_to_records`` output (None -> NaN)."""
    recs = list(records or [])
    if not recs:
        return pd.DataFrame(columns=PROFILE_COLUMNS)
    df = pd.DataFrame(recs)
    for c in df.columns:
        if c not in ("Date", "Variable", "Category", "Unit"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "N" in df:
        df["N"] = df["N"].fillna(0).astype(int)
    return df


# ----------------------------------------------------------------------------- tables
def list_series(df: pd.DataFrame, category: str | None = None) -> pd.DataFrame:
    """Available variables (Variable, Category, Unit, Group), in first-seen order."""
    if df.empty:
        return pd.DataFrame(columns=["Variable", "Category", "Unit", "Group"])
    d = df if category is None else df[df["Category"] == category]
    u = d[["Variable", "Category", "Unit"]].drop_duplicates("Variable").reset_index(drop=True)
    u["Group"] = [variable_group(v) for v in u["Variable"]]
    return u


def available_node_series(df: pd.DataFrame) -> list[str]:
    """Node-level pressure variables: group means by kind first, then individual nodes."""
    names = list(dict.fromkeys(df["Variable"])) if not df.empty else []
    return [n for n in names if n.startswith(NODE_GROUP_PREFIX)] + [n for n in names if n.startswith(NODE_PREFIX)]


def profile_table(df: pd.DataFrame, variable: str, n_dates: int = 8, dates: Sequence[str] | None = None) -> pd.DataFrame:
    """P90/P50/P10/Mean for one variable at selected dates (evenly spaced incl. first & last by default)."""
    d = df[df["Variable"] == variable]
    if d.empty:
        return d[["Date", "Day", "P90", "P50", "P10", "Mean", "N"]] if {"Date", "Day", "P90", "P50", "P10", "Mean", "N"} <= set(d.columns) else d
    if dates is not None:
        d = d[d["Date"].isin(list(dates))]
    elif n_dates and len(d) > n_dates:
        sel = np.unique(np.linspace(0, len(d) - 1, int(n_dates)).round().astype(int))
        d = d.iloc[sel]
    return d[["Date", "Day", "P90", "P50", "P10", "Mean", "N"]].reset_index(drop=True)


def reserves_table(df: pd.DataFrame) -> pd.DataFrame:
    """End-of-horizon cumulatives: last date with at least one contributing realization."""
    cols = ["Variable", "Unit", "Date", "P90", "P50", "P10", "Mean", "N"]
    d = df[(df["Category"] == "Cumulative") & (df["N"] > 0)] if not df.empty else df
    if d is None or d.empty:
        return pd.DataFrame(columns=cols)
    last = d.groupby("Variable", sort=False).tail(1)
    return last[cols].reset_index(drop=True)


def probability_of_exceedance(values: Iterable[float], threshold: float) -> float:
    """Fraction of finite realization values strictly above ``threshold`` (NaN if none)."""
    a = np.asarray(list(values), dtype=float)
    a = a[np.isfinite(a)]
    return float(np.mean(a > threshold)) if len(a) else float("nan")
