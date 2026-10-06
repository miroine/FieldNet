"""Export the data-hub tables: CSV (zip), Excel, JSON, a STEA-style annual profile template, and a local read-only API bundle.

STEA: the exact import format of an economics tool is project specific and is not known here. ``stea_table`` therefore writes the
usual layout of profile files - one row per series, one column per calendar year, units and a scale per row - driven by a mapping
that the user can edit (``DEFAULT_STEA_MAPPING``) to match their template. Check the first import in the target tool.

API: ``api_bundle`` returns a zip with the tables as CSV, a manifest, a tiny standard-library read-only HTTP server (``serve.py``,
127.0.0.1 only, no dependencies) and a client example. It is a local data service for scripts / Excel / Power BI, not a hosted service."""
from __future__ import annotations
import io, json, re, zipfile, datetime
import pandas as pd

FORBIDDEN_SHEET = re.compile(r'[\[\]\*\?/\\:]')


def _safe(name, used=None, limit=200):
    s = re.sub(r'[^A-Za-z0-9_.-]+', '_', str(name)).strip('_')[:limit] or 'table'
    return s


def _sheet(name, used):
    s = FORBIDDEN_SHEET.sub('_', str(name))[:31] or 'Sheet'; base, i = s, 1
    while s.lower() in used: i += 1; s = f'{base[:28]}_{i}'
    used.add(s.lower()); return s


def select(datasets, names=None): return {k: v for k, v in datasets.items() if names is None or k in names}


def manifest(datasets, meta=None, info=None):
    meta = meta or {}
    return {'generated': datetime.datetime.now().isoformat(timespec='seconds'), 'generator': 'FieldNet', 'info': {k: v for k, v in (info or {}).items() if v is not None},
            'datasets': {k: {'rows': int(len(v)), 'columns': list(map(str, v.columns)), **{a: b for a, b in (meta.get(k) or {}).items() if a in ('description', 'source', 'units')}} for k, v in datasets.items()}}


def to_csv_zip(datasets, meta=None, info=None, sep=',', decimal='.'):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for k, v in datasets.items(): z.writestr(f'{_safe(k)}.csv', v.to_csv(index=False, sep=sep, decimal=decimal))
        z.writestr('manifest.json', json.dumps(manifest(datasets, meta, info), indent=1, default=str))
    return buf.getvalue()


def to_json(datasets, meta=None, info=None):
    return json.dumps({'manifest': manifest(datasets, meta, info), 'data': {k: json.loads(v.to_json(orient='records', date_format='iso')) for k, v in datasets.items()}}, indent=1)


def to_excel(datasets, meta=None, info=None, checks=None):
    """One sheet per table plus a README sheet (catalogue, consistency checks). Returns bytes."""
    buf = io.BytesIO(); used = {'readme'}
    try: import openpyxl as _ox; _eng = 'openpyxl'
    except ImportError: _eng = 'xlsxwriter'   # raises ImportError with a clear message if neither is installed
    with pd.ExcelWriter(buf, engine=_eng) as xw:
        cat = pd.DataFrame([{'Sheet': None, 'Dataset': k, 'Rows': len(v), 'Description': (meta or {}).get(k, {}).get('description', ''), 'Units': (meta or {}).get(k, {}).get('units', '')} for k, v in datasets.items()])
        sheets = {}
        for k, v in datasets.items(): sheets[k] = _sheet(k, used)
        cat['Sheet'] = [sheets[k] for k in cat['Dataset']]
        cat.to_excel(xw, sheet_name='README', index=False, startrow=3)
        ws = xw.sheets['README']; ws['A1'] = 'FieldNet export'; ws['A2'] = f"Generated {datetime.datetime.now():%Y-%m-%d %H:%M}  |  model hash {(info or {}).get('model_hash') or 'n/a'}"
        if checks is not None and len(checks): checks.to_excel(xw, sheet_name='README', index=False, startrow=len(cat) + 7)
        for k, v in datasets.items():
            d = v.copy()
            for c in d.columns:
                if pd.api.types.is_datetime64_any_dtype(d[c]): d[c] = d[c].dt.tz_localize(None) if getattr(d[c].dt, 'tz', None) else d[c]
            d.to_excel(xw, sheet_name=sheets[k], index=False)
            w = xw.sheets[sheets[k]]; w.freeze_panes = 'A2'
            for i, c in enumerate(d.columns, 1): w.column_dimensions[w.cell(1, i).column_letter].width = min(max(len(str(c)) + 2, 12), 40)
        xw.sheets['README'].column_dimensions['A'].width = 24; xw.sheets['README'].column_dimensions['B'].width = 24; xw.sheets['README'].column_dimensions['D'].width = 60
    return buf.getvalue()


# ----------------------------------------------------------------------------- STEA-style annual profile
# series label, source dataset, column (a list = first that exists), multiplier, unit label
DEFAULT_STEA_MAPPING = [
    {'Series': 'Oil production', 'Dataset': 'annual_field', 'Column': 'Oil [Sm3]', 'Scale': 1e-6, 'Unit': 'MSm3'},
    {'Series': 'Gas production (sales gas)', 'Dataset': 'annual_field', 'Column': 'Gas [Sm3]', 'Scale': 1e-9, 'Unit': 'GSm3'},
    {'Series': 'Produced water', 'Dataset': 'annual_field', 'Column': 'Water [m3]', 'Scale': 1e-6, 'Unit': 'Mm3'},
    {'Series': 'Water injection', 'Dataset': 'annual_field', 'Column': 'Water injection [m3]', 'Scale': 1e-6, 'Unit': 'Mm3'},
    {'Series': 'Liquid production', 'Dataset': 'annual_field', 'Column': 'Liquid [m3]', 'Scale': 1e-6, 'Unit': 'Mm3'},
]


def stea_table(datasets, mapping=None, years=None, include_total=True):
    """Rows = series, columns = calendar years (+ total). Missing sources are skipped and reported in ``.attrs['skipped']``."""
    mapping = mapping or DEFAULT_STEA_MAPPING; rows, skipped, ys = [], [], set()
    for m in mapping:
        d = datasets.get(m['Dataset'])
        if d is None or m['Column'] not in d.columns or 'Year' not in d.columns: skipped.append(f"{m['Series']} ({m['Dataset']}.{m['Column']})"); continue
        s = d.set_index('Year')[m['Column']].astype(float) * float(m.get('Scale', 1.0)); ys |= set(map(int, s.index))
        rows.append((m, s))
    cols = sorted(ys) if years is None else list(years)
    out = pd.DataFrame([{'Series': m['Series'], 'Unit': m.get('Unit', ''), **{int(y): float(s.get(y, 0.0)) for y in cols}, **({'Total': float(sum(s.get(y, 0.0) for y in cols))} if include_total else {})} for m, s in rows])
    out.attrs['skipped'] = skipped
    return out


def mapping_from_table(df):
    need = {'Series', 'Dataset', 'Column'}
    if not need <= set(df.columns): raise ValueError(f'mapping needs columns {sorted(need)}')
    out = []
    for r in df.to_dict('records'):
        if not str(r.get('Series') or '').strip(): continue
        out.append({'Series': str(r['Series']), 'Dataset': str(r['Dataset']), 'Column': str(r['Column']), 'Scale': float(r.get('Scale') if r.get('Scale') not in (None, '') and r.get('Scale') == r.get('Scale') else 1.0), 'Unit': str(r.get('Unit') or '')})
    return out


def stea_csv(datasets, mapping=None, sep=';', decimal=','):
    return stea_table(datasets, mapping).to_csv(index=False, sep=sep, decimal=decimal)


# ----------------------------------------------------------------------------- API bundle
SERVE_PY = '''"""FieldNet local read-only data service. Run:  python serve.py [port]    then open http://127.0.0.1:8765/
Endpoints:  /datasets  |  /datasets/<name>[?format=json|csv]  |  /manifest
Standard library only; binds to 127.0.0.1 so it is reachable from this computer only."""
import csv, io, json, os, re, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
HERE = os.path.dirname(os.path.abspath(__file__)); DATA = os.path.join(HERE, 'data')
MANIFEST = json.load(open(os.path.join(HERE, 'manifest.json')))


def read(name):
    p = os.path.join(DATA, re.sub(r'[^A-Za-z0-9_.-]+', '_', name).strip('_')[:200] + '.csv')
    if name not in MANIFEST['datasets'] or not os.path.exists(p): return None
    with open(p, newline='', encoding='utf-8') as f: return list(csv.DictReader(f))


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        b = body.encode('utf-8'); self.send_response(code); self.send_header('Content-Type', ctype + '; charset=utf-8'); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query); parts = [x for x in u.path.split('/') if x]
        if not parts or parts == ['datasets']: return self._send(200, json.dumps({k: {'rows': v['rows'], 'columns': v['columns'], 'url': f'/datasets/{k}'} for k, v in MANIFEST['datasets'].items()}, indent=1), 'application/json')
        if parts == ['manifest']: return self._send(200, json.dumps(MANIFEST, indent=1), 'application/json')
        if len(parts) == 2 and parts[0] == 'datasets':
            rows = read(parts[1])
            if rows is None: return self._send(404, json.dumps({'error': 'unknown dataset'}), 'application/json')
            if q.get('format', ['json'])[0] == 'csv':
                b = io.StringIO(); w = csv.DictWriter(b, fieldnames=list(rows[0].keys()) if rows else []); w.writeheader(); w.writerows(rows); return self._send(200, b.getvalue(), 'text/csv')
            return self._send(200, json.dumps(rows), 'application/json')
        self._send(404, json.dumps({'error': 'not found'}), 'application/json')

    def log_message(self, *a): pass


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f'FieldNet data service on http://127.0.0.1:{port}/  (Ctrl+C to stop)'); HTTPServer(('127.0.0.1', port), H).serve_forever()
'''
CLIENT_PY = '''"""Example client: pip install pandas, start serve.py, then run this file."""
import json, urllib.request, pandas as pd
BASE = 'http://127.0.0.1:8765'
def get(name): return pd.DataFrame(json.load(urllib.request.urlopen(f'{BASE}/datasets/{name}'))).apply(pd.to_numeric, errors='ignore')
print(json.load(urllib.request.urlopen(f'{BASE}/datasets')).keys())
# df = get('annual_field'); print(df.head())
'''
API_README = '''FieldNet local data bundle
==========================
python serve.py            # serves ./data on http://127.0.0.1:8765  (this computer only, read-only)
GET /datasets              list tables
GET /datasets/<name>       rows as JSON   (?format=csv for CSV)
GET /manifest              descriptions, units, model hash
Excel: Data > Get Data > From Web > http://127.0.0.1:8765/datasets/annual_field?format=csv
This is a snapshot of the results at export time, not a live link to the app.
'''


def api_bundle(datasets, meta=None, info=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for k, v in datasets.items(): z.writestr(f'data/{_safe(k)}.csv', v.to_csv(index=False))
        z.writestr('manifest.json', json.dumps(manifest(datasets, meta, info), indent=1, default=str))
        z.writestr('serve.py', SERVE_PY); z.writestr('client_example.py', CLIENT_PY); z.writestr('README.txt', API_README)
    return buf.getvalue()
