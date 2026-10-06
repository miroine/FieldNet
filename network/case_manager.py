"""Case library: create / duplicate / rename / delete / save / load / export / import / compare.

A *case* is a named, editable working copy of the model (nodes + edges + unit profile) together with the
results that were computed for it (solve summary, forecast field profile and KPIs). Cases are plain JSON-able
dicts so they can be kept in session state, exported and shared. No solver code is imported here."""
from __future__ import annotations
import copy, hashlib, io, json, uuid, zipfile
from datetime import datetime, timezone
from network.scenario_v29 import scenario_diff, _canon

CASE_SCHEMA = 'case-1'
LIBRARY_SCHEMA = 'case-library-1'
FORECAST_KEEP = ('field', 'recovery', 'wells', 'tanks', 'constraints')   # parts of a forecast kept with a case: enough to rebuild yearly bars, groups, material balance and exports after loading
EXTRAS_KEYS = ('post_scripts', 'stea_mapping', 'mb_history', 'nodal_tests', 'nodal_survey', 'fluid_library')   # user inputs (not results) stored with a case


def _now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def _builtin(x): return json.loads(json.dumps(x, default=_default))
def _default(o):
    try:
        import numpy as np
        if isinstance(o, np.generic): return o.item()
        if isinstance(o, np.ndarray): return o.tolist()
    except Exception: pass
    if hasattr(o, 'isoformat'): return o.isoformat()
    return str(o)


def model_hash(case):
    return hashlib.sha256(_canon({'n': case['nodes'], 'e': case['edges'], 'u': case.get('unit_profile')}).encode()).hexdigest()


def solve_summary(results):
    """KPI dict from a (pressures, flows, info, details) tuple, or None."""
    if not results or not results[0]: return None
    _p, _q, info, det = results
    g = lambda k: sum(float(v.get(k, 0.0) or 0.0) for v in det.values())
    return {'liquid_m3d': g('liquid_rate_m3d'), 'oil_m3d': g('oil_rate_m3d'), 'gas_sm3d': g('gas_rate_sm3d'), 'water_m3d': g('water_rate_m3d'),
            'wells_total': len(det), 'wells_flowing': sum(1 for v in det.values() if float(v.get('liquid_rate_m3d', 0) or 0) > 1e-6),
            'violations': int(info.get('violations', 0) or 0), 'quality_gate': str(info.get('quality_gate', 'N/A'))}


def forecast_light(fc):
    if not fc: return None
    return _builtin({k: fc[k] for k in FORECAST_KEEP if k in fc})


def new_case(name, nodes, edges, unit_profile='norwegian_si', *, description='', parent_id=None, results=None, forecast=None, kpis=None, extras=None):
    from network.prognosis import forecast_kpis
    fl = forecast_light(forecast)
    return {'schema': CASE_SCHEMA, 'id': uuid.uuid4().hex[:10], 'name': (str(name).strip() or 'Case'), 'description': str(description), 'parent_id': parent_id,
            'created_utc': _now(), 'modified_utc': _now(), 'unit_profile': unit_profile,
            'nodes': _builtin(nodes), 'edges': _builtin(edges), 'solve': solve_summary(results) if not isinstance(results, dict) else results,
            'forecast': fl, 'forecast_kpis': _builtin(forecast_kpis(fl)) if fl and fl.get('field') else None,
            'extras': _builtin({k: v for k, v in (extras or {}).items() if k in EXTRAS_KEYS and v})}


class CaseLibrary:
    """Ordered dict of cases with an 'active' pointer. Stored in ``st.session_state['case_library']``."""
    def __init__(self, cases=None, active=None):
        self.cases = {c['id']: c for c in (cases or [])}
        self.active = active if active in self.cases else (next(iter(self.cases), None))
    def __len__(self): return len(self.cases)
    def get(self, cid): return self.cases[cid]
    def names(self): return {c['id']: c['name'] for c in self.cases.values()}
    def unique_name(self, name):
        used = {c['name'] for c in self.cases.values()}; base = name; i = 2
        while name in used: name = f'{base} ({i})'; i += 1
        return name
    def add(self, case, make_active=True):
        case = copy.deepcopy(case); case['name'] = self.unique_name(case['name'])
        while case['id'] in self.cases: case['id'] = uuid.uuid4().hex[:10]
        self.cases[case['id']] = case
        if make_active: self.active = case['id']
        return case
    def save(self, cid, nodes, edges, unit_profile, *, results=None, forecast=None, description=None, extras=None):
        """Overwrite case ``cid`` with the current working model (name/id/created kept)."""
        old = self.cases[cid]; fresh = new_case(old['name'], nodes, edges, unit_profile, description=old['description'] if description is None else description,
                                                parent_id=old.get('parent_id'), results=results, forecast=forecast, extras=extras if extras is not None else old.get('extras'))
        # a save of an unchanged model keeps results computed earlier if none are supplied now
        if results is None and forecast is None and model_hash(fresh) == model_hash(old): fresh['solve'], fresh['forecast'], fresh['forecast_kpis'] = old.get('solve'), old.get('forecast'), old.get('forecast_kpis')
        fresh['id'], fresh['created_utc'] = old['id'], old['created_utc']; self.cases[cid] = fresh; return fresh
    def duplicate(self, cid, new_name=None, *, keep_results=True):
        src = self.cases[cid]; c = copy.deepcopy(src); c['id'] = uuid.uuid4().hex[:10]; c['parent_id'] = src['id']; c['created_utc'] = c['modified_utc'] = _now()
        c['name'] = new_name or f"{src['name']} - copy"
        if not keep_results: c['solve'] = c['forecast'] = c['forecast_kpis'] = None
        return self.add(c)
    def copy_into(self, src_id, dst_id, parts=('model',)):
        """Copy parts of case ``src_id`` into ``dst_id``. parts: model (nodes+edges+units) | results."""
        s, d = self.cases[src_id], self.cases[dst_id]
        if 'model' in parts: d['nodes'], d['edges'], d['unit_profile'], d['extras'] = copy.deepcopy(s['nodes']), copy.deepcopy(s['edges']), s.get('unit_profile'), copy.deepcopy(s.get('extras') or {})
        if 'results' in parts: d['solve'], d['forecast'], d['forecast_kpis'] = copy.deepcopy(s.get('solve')), copy.deepcopy(s.get('forecast')), copy.deepcopy(s.get('forecast_kpis'))
        d['modified_utc'] = _now(); return d
    def rename(self, cid, name):
        name = str(name).strip()
        if not name: raise ValueError('name required')
        if any(c['name'] == name and c['id'] != cid for c in self.cases.values()): raise ValueError(f'a case named {name!r} already exists')
        self.cases[cid]['name'] = name; self.cases[cid]['modified_utc'] = _now()
    def delete(self, cid):
        del self.cases[cid]
        if self.active == cid: self.active = next(iter(self.cases), None)
    def table(self):
        rows = []
        for c in self.cases.values():
            rows.append({'Active': '●' if c['id'] == self.active else '', 'Name': c['name'], 'Parent': self.cases[c['parent_id']]['name'] if c.get('parent_id') in self.cases else '',
                         'Wells': sum(1 for n in c['nodes'] if n.get('kind') == 'well'), 'Solved': 'yes' if c.get('solve') else '', 'Forecast': 'yes' if c.get('forecast') else '', 'Saved (UTC)': c['modified_utc'], 'id': c['id']})
        return rows
    # ---- serialisation
    def to_dict(self, ids=None):
        ids = list(self.cases) if ids is None else list(ids)
        return {'schema': LIBRARY_SCHEMA, 'application': 'FieldNet', 'exported_utc': _now(), 'cases': [copy.deepcopy(self.cases[i]) for i in ids], 'active': self.active if self.active in ids else None}
    @classmethod
    def from_dict(cls, d):
        validate_library_dict(d); return cls(d['cases'], d.get('active'))


def validate_library_dict(d):
    if not isinstance(d, dict) or d.get('schema') not in (LIBRARY_SCHEMA, CASE_SCHEMA): raise ValueError('not a FieldNet case file')
    cases = [d] if d.get('schema') == CASE_SCHEMA else d.get('cases')
    if not isinstance(cases, list) or not cases: raise ValueError('no cases in file')
    for c in cases:
        for k in ('id', 'name', 'nodes', 'edges'):
            if k not in c: raise ValueError(f"case is missing '{k}'")
        if not isinstance(c['nodes'], list) or not isinstance(c['edges'], list): raise ValueError('nodes/edges must be lists')
    if d.get('schema') == CASE_SCHEMA: d.clear(); d.update({'schema': LIBRARY_SCHEMA, 'cases': cases})
    return True


def export_json(lib, ids=None) -> bytes: return json.dumps(lib.to_dict(ids), indent=1, default=_default).encode()
def import_json(data: bytes) -> CaseLibrary: return CaseLibrary.from_dict(json.loads(data.decode('utf-8')))


def export_zip(lib, ids=None) -> bytes:
    """One <name>.case.json per case + manifest with SHA-256 of every file (tamper check)."""
    d = lib.to_dict(ids); buf = io.BytesIO(); man = []
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for c in d['cases']:
            fn = f"{c['id']}.case.json"; b = json.dumps({**c, 'schema': CASE_SCHEMA}, indent=1, default=_default).encode()
            z.writestr(fn, b); man.append({'file': fn, 'name': c['name'], 'sha256': hashlib.sha256(b).hexdigest()})
        z.writestr('manifest.json', json.dumps({'schema': LIBRARY_SCHEMA, 'active': d['active'], 'cases': man}, indent=1))
    return buf.getvalue()


def import_zip(data: bytes) -> CaseLibrary:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if 'manifest.json' not in z.namelist(): raise ValueError('manifest.json missing')
        man = json.loads(z.read('manifest.json')); cases = []
        for m in man['cases']:
            b = z.read(m['file'])
            if hashlib.sha256(b).hexdigest() != m['sha256']: raise ValueError(f"checksum mismatch for {m['file']} - file was modified")
            cases.append(json.loads(b))
    return CaseLibrary.from_dict({'schema': LIBRARY_SCHEMA, 'cases': cases, 'active': man.get('active')})


def merge(lib, other, make_active=False):
    """Add every case of ``other`` to ``lib`` (names de-duplicated, ids kept when free). Returns the added cases."""
    return [lib.add(c, make_active=False) for c in other.cases.values()]


# ---------------------------------------------------------------- comparison
SOLVE_ROWS = [('liquid_m3d', 'Liquid [m³/d]'), ('oil_m3d', 'Oil [m³/d]'), ('gas_sm3d', 'Gas [Sm³/d]'), ('water_m3d', 'Water [m³/d]'),
              ('wells_flowing', 'Wells flowing'), ('violations', 'Constraint violations'), ('quality_gate', 'Quality gate')]
KPI_ROWS = [('first_oil', 'First oil'), ('peak_oil_m3d', 'Peak oil [m³/d]'), ('plateau_years', 'Plateau [years]'), ('cum_oil_sm3', 'Cum oil [Sm³]'),
            ('cum_gas_sm3', 'Cum gas [Sm³]'), ('cum_water_m3', 'Cum water [m³]'), ('rf_oil_pct', 'Oil RF [%]'), ('final_oil_m3d', 'Final oil [m³/d]'),
            ('final_water_cut_pct', 'Final water cut [%]'), ('max_wells_flowing', 'Max wells flowing')]


def compare_table(cases, baseline_idx=0):
    """Rows: metric × case values (+ Δ vs baseline for numeric metrics). Missing results are None."""
    rows = []
    base = cases[baseline_idx] if cases else None
    for section, key, spec in (('Solve', 'solve', SOLVE_ROWS), ('Forecast', 'forecast_kpis', KPI_ROWS)):
        for k, label in spec:
            vals = [((c.get(key) or {}).get(k)) for c in cases]
            row = {'Section': section, 'Metric': label}
            for c, v in zip(cases, vals): row[c['name']] = v
            bv = vals[baseline_idx] if vals else None
            if len(cases) > 1 and isinstance(bv, (int, float)) and not isinstance(bv, bool):
                for i, (c, v) in enumerate(zip(cases, vals)):
                    if i != baseline_idx: row[f"Δ {c['name']}"] = (v - bv) if isinstance(v, (int, float)) else None
            rows.append(row)
    return rows


def model_counts(case):
    out = {}
    for n in case['nodes']: out[n.get('kind', '?')] = out.get(n.get('kind', '?'), 0) + 1
    return out


def case_diff(a, b):
    """Parameter-level differences between two cases (uses the scenario diff on nodes/edges, adds a flat field list)."""
    d = scenario_diff({'nodes': a['nodes'], 'edges': a['edges']}, {'nodes': b['nodes'], 'edges': b['edges']})
    flat = []
    for ch in d['changes']:
        if ch['change'] == 'modified':
            for path, (x, y) in _flat_changes(ch['before'], ch['after']).items():
                flat.append({'Table': ch['table'], 'Element': ch['id'], 'Change': 'modified', 'Field': path, 'A': x, 'B': y})
        else: flat.append({'Table': ch['table'], 'Element': ch['id'], 'Change': ch['change'], 'Field': '', 'A': '', 'B': ''})
    if a.get('unit_profile') != b.get('unit_profile'): flat.append({'Table': 'settings', 'Element': '*', 'Change': 'modified', 'Field': 'unit_profile', 'A': a.get('unit_profile'), 'B': b.get('unit_profile')})
    return {'change_count': len(flat), 'rows': flat, 'raw': d}


def _flat_changes(a, b, prefix=''):
    out = {}
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            p = f'{prefix}.{k}' if prefix else str(k)
            out.update(_flat_changes(a.get(k), b.get(k), p))
    elif _canon(a) != _canon(b): out[prefix] = (a, b)
    return out


def profile_series(cases, column='Oil [m3/d]'):
    """{case name: (dates, values)} from each case's stored forecast field profile."""
    out = {}
    for c in cases:
        rows = (c.get('forecast') or {}).get('field') or []
        if rows: out[c['name']] = ([r.get('Date') for r in rows], [r.get(column) for r in rows])
    return out
