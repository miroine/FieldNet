"""File-exchange link between FieldNet and reservoir simulators (NO live coupling).

SCREENING-LEVEL / INTEROP HELPERS:

* ``export_vfp_prod`` writes an Eclipse-style ``VFPPROD`` table (METRIC units, BHP body) computed from the FieldNet
  tubing VLP. FORMAT-CHECKED BY PARSER ROUND-TRIP ONLY (``parse_vfp_prod``) - it has NOT been loaded in Eclipse, tOmNavigator,
  OPM Flow or any other simulator. Check keyword items against your simulator's manual/version before use. Only
  OIL/LIQ rate types, WCT water fraction, GOR gas fraction, THP and a single dummy ALQ value are supported.
  VLP is FieldNet's correlation model (not validated against measured data).
* ``import_rate_schedule`` / ``export_forecast_rates`` convert simulator well rates (date, well, oil, water, gas,
  optional pressure) to/from CSV and to FieldNet's ``network.prediction_sources`` ``external_table`` format.
* ``compare_rates`` / ``iteration_summary`` compare FieldNet and simulator rates and report a convergence summary with
  suggested per-well rate-cap scale factors for the next manual exchange iteration.

Units: rates in Sm3/d (m3/d for oil/water), pressure bar.
"""
from __future__ import annotations
import copy
import io
import csv
import math
import re
from datetime import datetime
import numpy as np


# ----------------------------------------------------------------------------------------------
# VFPPROD export / parse
# ----------------------------------------------------------------------------------------------
def _fmt(v):
    return f"{float(v):.6g}"


def _lines(vals, per=8):
    vals = [_fmt(v) for v in vals]
    rows = [' '.join(vals[i:i + per]) for i in range(0, len(vals), per)]
    rows[-1] += ' /'
    return ['  ' + r for r in rows]


def vfp_bhp_grid(well, rates, whps, wcts, gors, rate_type='LIQ'):
    """BHP [bar] array [n_thp, n_wct, n_gor, 1(alq), n_rate] from the FieldNet well VLP (``physics.well_model.vlp_bhp``).
    ``well`` = node dict (uses ``params``) or a params dict. For ``rate_type='OIL'`` the table rate is oil and the liquid
    rate is oil/(1-WCT)."""
    from physics.well_model import well_settings, vlp_bhp
    prm = dict(well.get('params', well)) if isinstance(well, dict) else {}
    out = np.zeros((len(whps), len(wcts), len(gors), 1, len(rates)))
    for it, whp in enumerate(whps):
        for iw, wc in enumerate(wcts):
            for ig, gor in enumerate(gors):
                p = dict(prm); p['water_cut'] = float(wc); p['gor_sm3sm3'] = float(gor)
                s = well_settings(p)
                for iq, q in enumerate(rates):
                    ql = float(q) / max(1.0 - float(wc), 1e-6) if rate_type == 'OIL' else float(q)
                    out[it, iw, ig, 0, iq] = vlp_bhp(ql, float(whp), s)[0]
    return out


def export_vfp_prod(well, rates, whps, wcts, gors, table_number=1, datum_depth_m=None, rate_type='LIQ', path=None):
    """Write a VFPPROD table text (METRIC, BHP body) from the FieldNet VLP.

    ``rates`` [Sm3/d, LIQ or OIL], ``whps`` [barsa], ``wcts`` [fraction, WCT], ``gors`` [Sm3/Sm3, GOR]; ``datum_depth_m``
    defaults to the well depth. Each value list must be strictly increasing (Eclipse requirement). Returns the text
    (also written to ``path`` if given). FORMAT-CHECKED BY PARSER ROUND-TRIP ONLY - NOT LOADED IN ECLIPSE.
    """
    rate_type = rate_type.upper()
    if rate_type not in ('LIQ', 'OIL'): raise ValueError("rate_type must be 'LIQ' or 'OIL'")
    for nm, v in (('rates', rates), ('whps', whps), ('wcts', wcts), ('gors', gors)):
        if len(v) < 1 or any(b <= a for a, b in zip(v, v[1:])): raise ValueError(f'{nm} must be non-empty and strictly increasing')
    if int(table_number) < 1: raise ValueError('table_number must be >= 1')
    prm = well.get('params', well) if isinstance(well, dict) else {}
    datum = float(datum_depth_m if datum_depth_m is not None else prm.get('depth_m', 0.0))
    bhp = vfp_bhp_grid(well, rates, whps, wcts, gors, rate_type)
    L = ['-- FieldNet VFPPROD export (VLP from FieldNet tubing correlation; screening-level)',
         '-- FORMAT-CHECKED BY PARSER ROUND-TRIP ONLY - NOT LOADED IN ECLIPSE',
         'VFPPROD',
         '-- Table  Datum depth  Rate type  WFR type  GFR type  THP type  ALQ type  Units   Body',
         f"   {int(table_number)}   {_fmt(datum)}   {rate_type}   WCT   GOR   THP   ' '   METRIC   BHP /",
         '-- Flow rates (Sm3/d)']
    L += _lines(rates); L.append('-- THP values (barsa)'); L += _lines(whps)
    L.append('-- WCT values (Sm3/Sm3)'); L += _lines(wcts); L.append('-- GOR values (Sm3/Sm3)'); L += _lines(gors)
    L.append('-- ALQ values (dummy)'); L += _lines([0.0])
    L.append('-- THP idx, WFR idx, GFR idx, ALQ idx, BHP values (one per flow rate)')
    for it in range(len(whps)):
        for iw in range(len(wcts)):
            for ig in range(len(gors)):
                vals = [_fmt(b) for b in bhp[it, iw, ig, 0]]
                L.append(f"  {it + 1} {iw + 1} {ig + 1} 1 " + ' '.join(vals) + ' /')
    text = '\n'.join(L) + '\n'
    if path: open(path, 'w').write(text)
    return text


def _tokens(record_text):
    out = []
    for t in re.findall(r"'[^']*'|\S+", record_text):
        m = re.fullmatch(r'(\d+)\*(.+)', t)
        if m: out += [m.group(2)] * int(m.group(1))
        else: out.append(t)
    return out


def _records(text):
    """Split the body after VFPPROD into '/'-terminated records, dropping '--' comments."""
    clean = []
    for ln in text.splitlines():
        q = False; cut = len(ln)
        for i, ch in enumerate(ln):          # drop '--' comments that are not inside a quoted string
            if ch == "'": q = not q
            elif not q and ln.startswith('--', i): cut = i; break
        clean.append(ln[:cut])
    body = '\n'.join(clean)
    return [r for r in (x.strip() for x in body.split('/')) if r]


def parse_vfp_prod(text):
    """Parse a ``VFPPROD`` table (as written by ``export_vfp_prod``; common Eclipse layout, single table).

    Returns ``{'table','datum','rate_type','wfr_type','gfr_type','thp_type','alq_type','units','body_type','flow','thp',
    'wfr','gfr','alq','bhp'}`` with ``bhp`` shaped [thp, wfr, gfr, alq, flow]. Raises ValueError on malformed input
    (missing records, wrong value counts, index out of range)."""
    m = re.search(r'^\s*VFPPROD\b', text, flags=re.M)
    if not m: raise ValueError('no VFPPROD keyword found')
    recs = _records(text[m.end():])
    if len(recs) < 6: raise ValueError('VFPPROD needs header + 5 axis records + body')
    h = _tokens(recs[0])
    if len(h) < 8: raise ValueError('VFPPROD header record needs at least 8 items')
    h = [x.strip("'") for x in h] + [''] * (9 - len(h))
    axes = [np.array([float(x) for x in _tokens(r)]) for r in recs[1:5]]
    flow, thp, wfr, gfr = axes; alq = np.array([float(x) for x in _tokens(recs[5])])
    body = recs[6:]; nf = len(flow)
    bhp = np.full((len(thp), len(wfr), len(gfr), len(alq), nf), np.nan)
    for r in body:
        t = _tokens(r)
        if len(t) != 4 + nf: raise ValueError(f'body record has {len(t)} items, expected {4 + nf}')
        i, j, k, l = (int(x) - 1 for x in t[:4])
        if not (0 <= i < len(thp) and 0 <= j < len(wfr) and 0 <= k < len(gfr) and 0 <= l < len(alq)): raise ValueError('body index out of range')
        bhp[i, j, k, l] = [float(x) for x in t[4:]]
    if np.isnan(bhp).any(): raise ValueError('VFPPROD body is incomplete (missing index combinations)')
    return {'table': int(h[0]), 'datum': float(h[1]), 'rate_type': h[2], 'wfr_type': h[3], 'gfr_type': h[4], 'thp_type': h[5],
            'alq_type': h[6], 'units': h[7], 'body_type': h[8] or 'BHP', 'flow': flow, 'thp': thp, 'wfr': wfr, 'gfr': gfr, 'alq': alq, 'bhp': bhp}


# ----------------------------------------------------------------------------------------------
# Rate schedules <-> CSV <-> prediction_sources
# ----------------------------------------------------------------------------------------------
_ALIASES = {'date': ('date', 'time', 'datetime'), 'well': ('well', 'wellname', 'name', 'wellid', 'wname'),
            'oil': ('oil', 'oilrate', 'orat', 'qo', 'wopr', 'oilsm3d', 'oilm3d'), 'water': ('water', 'waterrate', 'wrat', 'qw', 'wwpr', 'watersm3d', 'waterm3d'),
            'gas': ('gas', 'gasrate', 'grat', 'qg', 'wgpr', 'gassm3d'), 'pressure': ('pressure', 'bhp', 'pbh', 'wbhp', 'pressurebar', 'p', 'whp', 'wthp')}


def _key(h): return re.sub(r'[^a-z0-9]', '', re.sub(r'\[.*?\]|\(.*?\)', '', str(h).lower()))


def import_rate_schedule(text):
    """Parse simulator well rates CSV text (header row; columns date, well, oil, water, gas, optional pressure - common
    synonyms accepted, units in brackets ignored) into ``[{'date','well','oil','water','gas'[,'pressure']}]`` sorted by
    (well, date). Rates assumed Sm3/d / m3/d, pressure bar. Missing water/gas default to 0. Raises ValueError for missing
    required columns or unparsable numbers."""
    txt = str(text).strip()
    if not txt: raise ValueError('empty rate schedule')
    dialect = csv.Sniffer().sniff(txt.splitlines()[0], delimiters=',;\t') if any(c in txt.splitlines()[0] for c in ',;\t') else csv.excel
    rd = csv.reader(io.StringIO(txt), dialect); hdr = next(rd); col = {}
    for i, h in enumerate(hdr):
        for std, al in _ALIASES.items():
            if _key(h) in al and std not in col: col[std] = i
    for need in ('date', 'well', 'oil'):
        if need not in col: raise ValueError(f"rate schedule lacks required column '{need}' (found: {hdr})")
    rows = []
    for ln, r in enumerate(rd, start=2):
        if not r or not any(x.strip() for x in r): continue
        def num(std, default=None):
            if std not in col or col[std] >= len(r) or not r[col[std]].strip(): return default
            try: return float(r[col[std]])
            except ValueError: raise ValueError(f"line {ln}: cannot parse {std} {r[col[std]]!r}")
        try: d = datetime.fromisoformat(r[col['date']].strip()[:10]).date().isoformat()
        except ValueError: raise ValueError(f"line {ln}: date must be ISO (YYYY-MM-DD), got {r[col['date']]!r}")
        row = {'date': d, 'well': r[col['well']].strip(), 'oil': num('oil', 0.0), 'water': num('water', 0.0), 'gas': num('gas', 0.0)}
        p = num('pressure')
        if p is not None: row['pressure'] = p
        rows.append(row)
    rows.sort(key=lambda x: (x['well'], x['date']))
    return rows


def rates_to_csv(rows):
    """Rows (``import_rate_schedule`` format) -> CSV text (date,well,oil,water,gas[,pressure])."""
    has_p = any('pressure' in r for r in rows)
    out = io.StringIO(); w = csv.writer(out, lineterminator='\n'); w.writerow(['date', 'well', 'oil', 'water', 'gas'] + (['pressure'] if has_p else []))
    for r in rows:
        w.writerow([r['date'], r['well'], repr(float(r['oil'])), repr(float(r['water'])), repr(float(r['gas']))] + ([('' if r.get('pressure') is None else repr(float(r['pressure'])))] if has_p else []))
    return out.getvalue()


def export_forecast_rates(forecast, path=None):
    """FieldNet ``run_forecast`` result -> the same CSV format (per-well oil / water / gas, BHP as pressure). Returns text."""
    rows = []
    for r in forecast.get('wells', []):
        row = {'date': str(r['Date'])[:10], 'well': r.get('Well', r.get('Well ID')), 'oil': float(r.get('Oil [m3/d]', 0.0) or 0.0),
               'water': float(r.get('Water [m3/d]', 0.0) or 0.0), 'gas': float(r.get('Gas [Sm3/d]', 0.0) or 0.0)}
        if r.get('BHP [bar]') is not None: row['pressure'] = float(r['BHP [bar]'])
        rows.append(row)
    rows.sort(key=lambda x: (x['well'], x['date'])); text = rates_to_csv(rows)
    if path: open(path, 'w').write(text)
    return text


def rates_to_prediction_sources(rows, well_ids=None, pressure_as=None, use='oil_cap'):
    """Simulator rates -> ``{well: prediction_source}`` (``network.prediction_sources`` ``external_table``, x_axis 'date',
    step interpolation, hold extrapolation). Per row: ``max_oil_rate_m3d`` = oil rate (a potential cap - the FieldNet well
    still follows its IPR/VLP and network limits), ``water_cut`` = w/(o+w), ``gor_sm3sm3`` = gas/oil (oil > 0 only).
    ``well_ids``: optional ``{simulator well name: FieldNet node id}``. ``pressure_as``: None (ignore pressure column) or
    'reservoir_pressure_bar' (use ONLY if the column is a reservoir/average pressure, not BHP/WHP).
    ``use='oil_cap'`` is the only mode."""
    if pressure_as not in (None, 'reservoir_pressure_bar'): raise ValueError("pressure_as must be None or 'reservoir_pressure_bar'")
    out = {}
    for r in rows:
        wid = (well_ids or {}).get(r['well'], r['well']); o = float(r['oil']); w = float(r['water']); g = float(r['gas'])
        t = {'date': r['date'], 'max_oil_rate_m3d': o}
        if o + w > 0: t['water_cut'] = min(w / (o + w), 0.9999)
        if o > 0: t['gor_sm3sm3'] = g / o
        if pressure_as and r.get('pressure') is not None: t[pressure_as] = float(r['pressure'])
        out.setdefault(wid, {'type': 'external_table', 'x_axis': 'date', 'interp': 'step', 'extrapolate': 'hold', 'rows': []})['rows'].append(t)
    for s in out.values(): s['rows'].sort(key=lambda x: x['date'])
    return out


def apply_rate_schedule(nodes, rows, well_ids=None, pressure_as=None):
    """Return a deep copy of ``nodes`` with ``params['prediction_source']`` set from simulator rates for matching well nodes
    (matched by node id or name after ``well_ids`` mapping). Wells without simulator data are untouched."""
    src = rates_to_prediction_sources(rows, well_ids, pressure_as); ns = copy.deepcopy(nodes)
    for n in ns:
        if n.get('kind') == 'well':
            s = src.get(n['id']) or src.get(n.get('name'))
            if s: n.setdefault('params', {})['prediction_source'] = s
    return ns


# ----------------------------------------------------------------------------------------------
# File-exchange iteration helper
# ----------------------------------------------------------------------------------------------
def compare_rates(fieldnet_rows, simulator_rows, phases=('oil',), rel_tol=0.05, abs_floor=1.0):
    """Join FieldNet and simulator rows on (well, date) and compare. Rows as ``import_rate_schedule``. Relative error =
    (fieldnet - sim) / max(|sim|, abs_floor). Returns ``{'records': [...], 'unmatched_fieldnet', 'unmatched_simulator'}``."""
    f = {(r['well'], r['date']): r for r in fieldnet_rows}; s = {(r['well'], r['date']): r for r in simulator_rows}
    recs = []
    for k in sorted(set(f) & set(s)):
        for ph in phases:
            a, b = float(f[k].get(ph, 0.0)), float(s[k].get(ph, 0.0)); rel = (a - b) / max(abs(b), abs_floor)
            recs.append({'well': k[0], 'date': k[1], 'phase': ph, 'fieldnet': a, 'simulator': b, 'abs_error': a - b, 'rel_error': rel, 'within_tol': abs(rel) <= rel_tol})
    return {'records': recs, 'unmatched_fieldnet': sorted(set(f) - set(s)), 'unmatched_simulator': sorted(set(s) - set(f))}


def iteration_summary(fieldnet_rows, simulator_rows, phases=('oil',), rel_tol=0.05, abs_floor=1.0, previous_max_rel_error=None):
    """Convergence summary of one FieldNet <-> simulator exchange iteration (no live coupling).

    Returns ``{'converged', 'n_compared', 'max_abs_rel_error', 'mean_abs_rel_error', 'cumulative_rel_error' (per phase,
    over matched rows weighted by rate), 'worst' (top 5 records), 'per_well' {well: {'mean_abs_rel_error','scale_factor'}},
    'unmatched_*', 'improving' (vs previous_max_rel_error), 'rel_tol'}``. ``scale_factor`` = sim/FieldNet mean rate for the
    first listed phase: a suggested multiplier for that well's FieldNet rate cap / PI in the next manual iteration (a
    heuristic, not a calibrated update). Converged = every compared record within ``rel_tol`` and at least one match."""
    c = compare_rates(fieldnet_rows, simulator_rows, phases, rel_tol, abs_floor); recs = c['records']
    if not recs:
        return {'converged': False, 'n_compared': 0, 'message': 'no matching (well, date) rows', 'unmatched_fieldnet': c['unmatched_fieldnet'], 'unmatched_simulator': c['unmatched_simulator'], 'rel_tol': rel_tol}
    ar = np.array([abs(r['rel_error']) for r in recs]); per = {}
    for w in sorted({r['well'] for r in recs}):
        rr = [r for r in recs if r['well'] == w and r['phase'] == phases[0]]
        fa = sum(r['fieldnet'] for r in rr); sa = sum(r['simulator'] for r in rr)
        per[w] = {'mean_abs_rel_error': float(np.mean([abs(r['rel_error']) for r in recs if r['well'] == w])), 'scale_factor': (sa / fa) if fa > 0 else None}
    cum = {}
    for ph in phases:
        fa = sum(r['fieldnet'] for r in recs if r['phase'] == ph); sa = sum(r['simulator'] for r in recs if r['phase'] == ph)
        cum[ph] = (fa - sa) / max(abs(sa), abs_floor)
    mx = float(ar.max())
    return {'converged': bool(all(r['within_tol'] for r in recs)), 'n_compared': len(recs), 'max_abs_rel_error': mx, 'mean_abs_rel_error': float(ar.mean()),
            'cumulative_rel_error': cum, 'worst': sorted(recs, key=lambda r: -abs(r['rel_error']))[:5], 'per_well': per,
            'unmatched_fieldnet': c['unmatched_fieldnet'], 'unmatched_simulator': c['unmatched_simulator'],
            'improving': None if previous_max_rel_error is None else mx < float(previous_max_rel_error), 'rel_tol': rel_tol}
