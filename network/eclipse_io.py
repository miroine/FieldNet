"""Read Eclipse / Intersect-style *binary* (unformatted) results for the prediction.

Supported files
* ``.SMSPEC`` + ``.UNSMRY`` (or one ``.S0001``, ``.S0002`` … per report step): the summary vectors (FPR, FOPR, FWCT, WOPR:well …).
* ``.UNRST`` / ``.X0001`` restart files: the pore-space-unweighted average PRESSURE per report step (a screening average of the active cells).

The files are Fortran-unformatted: every record is framed by a 4-byte length; a block is a 16-byte header record (8-char keyword,
int32 count, 4-char type) followed by the data in chunks of up to 1000 values (105 for CHAR). Big-endian is the norm; little-endian is detected.
Pure Python / numpy - no simulator library is needed. ``write_summary`` / ``write_records`` produce the same layout (used by the tests, handy to export).
"""
from __future__ import annotations
import struct
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

_DT = {'INTE': ('i', 4), 'REAL': ('f', 4), 'DOUB': ('d', 8), 'LOGI': ('i', 4)}
_CHUNK = {'INTE': 1000, 'REAL': 1000, 'DOUB': 1000, 'LOGI': 1000, 'CHAR': 105, 'MESS': 1000}


def _endian(b):
    if len(b) < 4: raise ValueError('File too short to be an Eclipse binary file.')
    if struct.unpack('>i', b[:4])[0] == 16: return '>'
    if struct.unpack('<i', b[:4])[0] == 16: return '<'
    raise ValueError('Not an Eclipse unformatted file (the first record is not a 16-byte block header). Formatted (text) files are not supported.')


def read_records(data):
    """Parse a whole file into ``[(keyword, type, values)]``."""
    e = _endian(data); pos = 0; n = len(data); out = []
    def rec():
        nonlocal pos
        if pos + 4 > n: raise ValueError('Truncated file.')
        ln = struct.unpack(e + 'i', data[pos:pos + 4])[0]
        if ln < 0 or pos + 8 + ln > n: raise ValueError('Truncated or corrupt record.')
        body = data[pos + 4:pos + 4 + ln]; pos += 8 + ln
        return body
    while pos < n:
        h = rec()
        if len(h) != 16: raise ValueError('Corrupt block header.')
        kw = h[:8].decode('ascii', 'ignore').strip(); cnt = struct.unpack(e + 'i', h[8:12])[0]; typ = h[12:16].decode('ascii', 'ignore')
        vals = []
        if typ in _DT:
            ch, sz = _DT[typ]
            while len(vals) < cnt:
                b = rec(); vals.extend(struct.unpack(e + f'{len(b) // sz}{ch}', b))
            if typ == 'LOGI': vals = [bool(v) for v in vals]
        elif typ.startswith('C') or typ == 'CHAR':
            w = 8 if typ == 'CHAR' else int(typ[1:] or 8)
            while len(vals) < cnt:
                b = rec(); vals.extend(b[i:i + w].decode('ascii', 'ignore').rstrip() for i in range(0, len(b), w))
        elif typ == 'MESS': pass
        else: raise ValueError(f'Unknown data type {typ!r} for keyword {kw}.')
        out.append((kw, typ, vals[:cnt] if cnt else []))
    return out


def read_smspec(data):
    """Vector list and start date of a summary specification: ``{'start': datetime, 'vectors': [{'keyword','name','num','unit'}], 'unit_system'}``."""
    recs = {}
    for kw, _, v in read_records(data): recs.setdefault(kw, v)
    kws = recs.get('KEYWORDS')
    if not kws: raise ValueError('No KEYWORDS record - this is not an SMSPEC file.')
    names = recs.get('WGNAMES') or recs.get('NAMES') or [''] * len(kws); nums = recs.get('NUMS') or [0] * len(kws); units = recs.get('UNITS') or [''] * len(kws)
    sd = recs.get('STARTDAT')
    start = datetime(int(sd[2]), int(sd[1]), int(sd[0]), int(sd[3]) if len(sd) > 3 else 0, int(sd[4]) if len(sd) > 4 else 0) if sd and len(sd) >= 3 else datetime(2000, 1, 1)
    vecs = [{'keyword': k.strip(), 'name': ('' if (names[i].strip() in ('', ':+:+:+:+')) else names[i].strip()), 'num': int(nums[i]), 'unit': units[i].strip()} for i, k in enumerate(kws)]
    return {'start': start, 'vectors': vecs, 'unit_system': 'FIELD' if any(v['unit'].upper() in ('PSIA', 'STB/DAY', 'MSCF/DAY') for v in vecs) else 'METRIC'}


def _label(v):
    return f"{v['keyword']}:{v['name']}" if v['name'] else v['keyword']


def read_unsmry(data_list, spec):
    """DataFrame of every summary vector against ``Date``. ``data_list`` = bytes or a list of bytes (unified file, or the per-step S000n files in order)."""
    if isinstance(data_list, (bytes, bytearray)): data_list = [data_list]
    vecs = spec['vectors']; rows = []
    for data in data_list:
        for kw, _, v in read_records(data):
            if kw == 'PARAMS':
                if len(v) != len(vecs): raise ValueError(f'PARAMS record has {len(v)} values but SMSPEC lists {len(vecs)} vectors - wrong SMSPEC for this UNSMRY?')
                rows.append(list(v[:len(vecs)]))
    if not rows: raise ValueError('No PARAMS records found - not a summary data file.')
    arr = np.asarray(rows, dtype=float); labels = []; seen = {}
    for v in vecs:
        lb = _label(v); k = seen.get(lb, 0); seen[lb] = k + 1; labels.append(lb if k == 0 else f'{lb}#{k}')
    df = pd.DataFrame(arr, columns=labels)
    ti = next((i for i, v in enumerate(vecs) if v['keyword'] == 'TIME'), None)
    if ti is None: raise ValueError('No TIME vector in the summary specification.')
    unit = vecs[ti]['unit'].upper(); fac = 1.0 if unit.startswith('DAY') else 1 / 24.0 if unit.startswith('HOUR') else 365.25 if unit.startswith('YEAR') else 1.0
    df.insert(0, 'Date', [spec['start'] + timedelta(days=float(t) * fac) for t in arr[:, ti]])
    df = df.drop(columns=[labels[ti]]).drop_duplicates('Date', keep='last').reset_index(drop=True)
    df.attrs['units'] = {_label(v): v['unit'] for v in vecs}
    return df


def read_summary(smspec, unsmry):
    spec = read_smspec(smspec); return read_unsmry(unsmry, spec), spec


# ---- unit conversion to FieldNet's canonical units (bar, Sm3, Sm3/d) ------------------------------------
_SCF = 0.028316846592; _STB = 0.158987294928
UNIT_FACTORS = {'BARSA': ('bar', 1.0), 'BARS': ('bar', 1.0), 'BAR': ('bar', 1.0), 'PSIA': ('bar', 0.0689475729), 'PSI': ('bar', 0.0689475729), 'ATM': ('bar', 1.01325), 'ATMA': ('bar', 1.01325),
                'KG/CM2': ('bar', 0.980665), 'KGF/CM2': ('bar', 0.980665),
                'SM3/DAY': ('Sm3/d', 1.0), 'SM3/D': ('Sm3/d', 1.0), 'STB/DAY': ('Sm3/d', _STB), 'MSCF/DAY': ('Sm3/d', 1000 * _SCF), 'MSCF/D': ('Sm3/d', 1000 * _SCF),
                'SM3': ('Sm3', 1.0), 'STB': ('Sm3', _STB), 'MSCF': ('Sm3', 1000 * _SCF),
                'SM3/SM3': ('Sm3/Sm3', 1.0), 'MSCF/STB': ('Sm3/Sm3', 1000 * _SCF / _STB), 'SCF/STB': ('Sm3/Sm3', _SCF / _STB), 'STB/MSCF': ('Sm3/Sm3', _STB / (1000 * _SCF))}


def convert(df, columns=None):
    """Copy of ``df`` with the pressure / rate / ratio columns converted to bar, Sm3, Sm3/d and Sm3/Sm3 (``attrs['converted']`` lists what was changed,
    ``attrs['unconverted']`` the unit strings that were not recognised and left as they are)."""
    out = df.copy(); units = df.attrs.get('units', {}); done = {}; skipped = {}
    for c in (columns or [c for c in df.columns if c != 'Date']):
        u = str(units.get(c, '')).upper().strip()
        if not u or u in ('DAYS', 'DAY', 'HOURS', 'YEARS', 'YEAR', ' '): continue
        if u in UNIT_FACTORS:
            tgt, f = UNIT_FACTORS[u]
            if f != 1.0: out[c] = out[c] * f
            done[c] = tgt
        elif u != 'DIMENSIONLESS': skipped[c] = u
    out.attrs['converted'] = done; out.attrs['unconverted'] = skipped; out.attrs['units'] = {c: done.get(c, units.get(c, '')) for c in out.columns if c != 'Date'}
    return out


# ---- restart files: average pressure per report step -------------------------------------------------------
def read_restart_pressure(data):
    """``DataFrame[Date, Average pressure [bar]]`` from a unified restart (or one X000n): date from INTEHEAD (DAY, MONTH, YEAR = items 65-67), the plain
    mean of the PRESSURE array over its cells (a simple screening average - not pore-volume weighted). Pressure unit from INTEHEAD item 3."""
    rows = []; cur = None
    for kw, _, v in read_records(data):
        if kw == 'INTEHEAD' and len(v) >= 67:
            try: cur = {'date': datetime(int(v[66]), int(v[65]), int(v[64])), 'unit': int(v[2])}
            except ValueError: cur = None
        elif kw == 'PRESSURE' and cur is not None and v:
            f = {1: 1.0, 2: 0.0689475729, 3: 1.01325}.get(cur['unit'], 1.0); rows.append((cur['date'], float(np.mean(v)) * f)); cur = None
    if not rows: raise ValueError('No PRESSURE arrays with INTEHEAD dates found - not a restart file (or PRESSURE was not written).')
    return pd.DataFrame(rows, columns=['Date', 'Average pressure [bar]']).drop_duplicates('Date', keep='last').reset_index(drop=True)


# ---- hand the data to FieldNet ----------------------------------------------------------------------------------
def vector_choices(df):
    """Column names grouped for the pickers: field level, regions and wells."""
    cols = [c for c in df.columns if c != 'Date']
    return {'field': [c for c in cols if c.startswith('F') and ':' not in c], 'region': [c for c in cols if c.startswith('R') and ':' in c], 'well': [c for c in cols if c.startswith('W') and ':' in c]}


def tank_table(df, pressure_col, water_cut_col=None, gor_col=None, every_days=0):
    """Rows for a tank's external prediction table (``params['external_table']``); the frame must already be in bar / Sm3/Sm3 (see :func:`convert`)."""
    cols = {'reservoir_pressure_bar': pressure_col, 'water_cut': water_cut_col, 'gor_sm3sm3': gor_col}
    d = df[['Date'] + [c for c in cols.values() if c]].copy(); d = d.dropna(subset=[pressure_col])
    if every_days and len(d) > 2:
        keep = [0]; last = d['Date'].iloc[0]
        for i in range(1, len(d) - 1):
            if (d['Date'].iloc[i] - last).days >= every_days: keep.append(i); last = d['Date'].iloc[i]
        keep.append(len(d) - 1); d = d.iloc[keep]
    rows = []
    for _, r in d.iterrows():
        row = {'date': r['Date'].date().isoformat()}
        for k, c in cols.items():
            if c and pd.notna(r[c]):
                v = round(float(r[c]), 6); row[k] = min(max(v, 0.0), 0.99) if k == 'water_cut' else v
        rows.append(row)
    return rows


def well_rates(df, wells=None):
    """Rows ``{'date','well','oil','water','gas'[,'pressure']}`` (as ``simulator_link.import_rate_schedule``) from WOPR / WWPR / WGPR / WBHP columns."""
    names = sorted({c.split(':', 1)[1] for c in df.columns if c.startswith('WOPR:') or c.startswith('WGPR:') or c.startswith('WWPR:')})
    if wells is not None: names = [w for w in names if w in set(wells)]
    rows = []
    for w in names:
        g = lambda k: df[f'{k}:{w}'] if f'{k}:{w}' in df.columns else None
        o, wt, gs, bh = g('WOPR'), g('WWPR'), g('WGPR'), g('WBHP')
        for i, d in enumerate(df['Date']):
            row = {'date': d.date().isoformat(), 'well': w, 'oil': float(o.iloc[i]) if o is not None else 0.0, 'water': float(wt.iloc[i]) if wt is not None else 0.0, 'gas': float(gs.iloc[i]) if gs is not None else 0.0}
            if bh is not None and pd.notna(bh.iloc[i]) and bh.iloc[i] > 0: row['pressure'] = float(bh.iloc[i])
            rows.append(row)
    return rows


# ---- writer (round-trip tests, export) ------------------------------------------------------------------------------
def write_records(records, big_endian=True):
    e = '>' if big_endian else '<'; out = bytearray()
    def frame(b): out.extend(struct.pack(e + 'i', len(b))); out.extend(b); out.extend(struct.pack(e + 'i', len(b)))
    for kw, typ, vals in records:
        frame(kw.ljust(8).encode('ascii') + struct.pack(e + 'i', len(vals)) + typ.encode('ascii'))
        ch = _CHUNK.get(typ, 1000)
        for i in range(0, len(vals), ch):
            part = vals[i:i + ch]
            if typ in _DT: frame(struct.pack(e + f'{len(part)}{_DT[typ][0]}', *[(int(v) if typ in ('INTE', 'LOGI') else float(v)) for v in part]))
            else: frame(b''.join(str(v).ljust(8)[:8].encode('ascii') for v in part))
    return bytes(out)


def write_summary(df, units, start, big_endian=True):
    """(smspec_bytes, unsmry_bytes) for a frame ``Date`` + vectors named ``KEYWORD`` or ``KEYWORD:NAME`` (``units`` maps each to its unit string)."""
    cols = [c for c in df.columns if c != 'Date']; kws = ['TIME'] + [c.split(':')[0] for c in cols]; nm = [':+:+:+:+'] + [c.split(':', 1)[1] if ':' in c else ':+:+:+:+' for c in cols]
    un = ['DAYS'] + [units.get(c, '') for c in cols]
    spec = write_records([('DIMENS', 'INTE', [len(kws), 1, 1, 1, 0, -1]), ('KEYWORDS', 'CHAR', kws), ('WGNAMES', 'CHAR', nm), ('NUMS', 'INTE', [0] * len(kws)), ('UNITS', 'CHAR', un),
                          ('STARTDAT', 'INTE', [start.day, start.month, start.year, 0, 0, 0])], big_endian)
    recs = [('SEQHDR', 'INTE', [1])]
    for i, d in enumerate(df['Date']):
        recs += [('MINISTEP', 'INTE', [i]), ('PARAMS', 'REAL', [(d - start).total_seconds() / 86400.0] + [float(df[c].iloc[i]) for c in cols])]
    return spec, write_records(recs, big_endian)
