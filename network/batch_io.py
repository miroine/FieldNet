"""Batch editing and file import / export of all input data (values in canonical units: m, m3/d, Sm3/d, bar, degC).

One table per element group, one row per element, one column per scalar parameter (the parameter key is the column header, e.g.
``max_oil_rate_m3d``, ``min_gas_rate_sm3d``, ``availability_factor``, ``scale``). Blank cell = parameter not set. Nested inputs
(trajectory, relperm, communication, tables) are not shown and are never touched by a table edit or import.

Import accepts JSON, YAML, Excel (.xlsx, one sheet per group or one sheet for everything) and CSV. A full project document
(``{nodes, edges}``) can replace the model; any other file is merged into the existing elements by ID (or by name).
"""
from __future__ import annotations
import io, json, math, copy

GROUPS = {
    'Wells': ('well',),
    'Injectors': ('water_injector', 'gas_injector', 'injector'),
    'Tanks': ('reservoir',),
    'Facilities': ('separator', 'separator_stage', 'sink', 'oil_export', 'gas_export', 'water_disposal', 'water_source', 'gas_source'),
    'Equipment': ('choke', 'control_valve', 'pump', 'compressor'),
    'Manifolds & joints': ('manifold', 'joint'),
}
EDGE_CORE = ('length_m', 'diameter_m', 'roughness_m', 'elevation_change_m')
NODE_CORE = ('pressure_bar',)
BOOL_KEYS = ('available', 'follow_wells', 'enabled', 'closed', 'open')
ID_HEADERS = ('id', 'well id', 'tank id', 'node id', 'edge id', 'element id', 'element', 'tag')
SKIP_HEADERS = ('kind', 'type', 'source', 'target', 'x', 'y')
PRIORITY = ('reservoir_id', 'available', 'availability_factor', 'scale', 'max_liquid_rate_m3d', 'max_oil_rate_m3d', 'max_water_rate_m3d', 'max_gas_rate_sm3d',
            'min_liquid_rate_m3d', 'min_oil_rate_m3d', 'min_water_rate_m3d', 'min_gas_rate_sm3d', 'min_pressure_bar', 'max_pressure_bar', 'min_bhp_bar',
            'target_rf', 'rf_taper_days', 'eur_cap', 'calibrate_rate', 'productivity_multiplier')
# parameters offered by "Add column" (the batch editor creates the column even when no element has a value yet)
CATALOG = {
    'Wells': ['max_liquid_rate_m3d', 'max_oil_rate_m3d', 'max_water_rate_m3d', 'max_gas_rate_sm3d', 'min_liquid_rate_m3d', 'min_oil_rate_m3d', 'min_water_rate_m3d',
              'min_gas_rate_sm3d', 'min_bhp_bar', 'availability_factor', 'scale', 'eur_cap', 'calibrate_rate', 'productivity_multiplier', 'skin', 'water_cut', 'gor_sm3sm3'],
    'Injectors': ['max_rate_m3d', 'max_whp_bar', 'availability_factor', 'scale', 'injectivity_m3d_bar'],
    'Tanks': ['target_rf', 'rf_taper_days', 'min_pressure_bar', 'aquifer_pi_m3d_bar', 'swi', 'scale'],
    'Facilities': ['max_liquid_rate_m3d', 'max_oil_rate_m3d', 'max_gas_rate_sm3d', 'max_water_rate_m3d', 'min_pressure_bar', 'max_pressure_bar', 'availability_factor', 'scale'],
    'Equipment': ['max_power_kw', 'availability_factor', 'scale'],
    'Manifolds & joints': ['max_pressure_bar', 'min_pressure_bar', 'availability_factor', 'scale'],
    'Flowlines': ['max_rate_m3d', 'max_velocity_ms', 'max_erosional_ratio', 'availability_factor', 'water_cut', 'gor_sm3sm3', 'temperature_c'],
}


def _scalar(v):
    return isinstance(v, (bool, int, float, str)) and not (isinstance(v, float) and not math.isfinite(v))


def group_of(node):
    k = node.get('kind')
    for g, kinds in GROUPS.items():
        if k in kinds: return g
    return None


def members(nodes, edges, group):
    if group == 'Flowlines': return list(edges)
    return [n for n in nodes if group_of(n) == group]


def columns_for(items, group, extra=()):
    keys = []
    for it in items:
        for k, v in (it.get('params') or {}).items():
            if not k.startswith('_') and _scalar(v) and k not in keys: keys.append(k)
    for k in extra:
        if k not in keys: keys.append(k)
    pr = [k for k in PRIORITY if k in keys]
    return pr + sorted(k for k in keys if k not in pr)


def element_table(nodes, edges, group, extra_cols=()):
    """-> (DataFrame, param column list). Index columns: ID, Name (nodes) / ID, From, To (edges), Kind."""
    import pandas as pd
    items = members(nodes, edges, group); cols = columns_for(items, group, extra_cols); rows = []
    for it in items:
        p = it.get('params') or {}
        if group == 'Flowlines':
            r = {'ID': it['id'], 'From': it.get('source'), 'To': it.get('target'), 'Kind': it.get('kind', 'pipeline')}
            for k in EDGE_CORE: r[k] = it.get(k)
        else:
            r = {'ID': it['id'], 'Name': it.get('name', it['id']), 'Kind': it.get('kind')}
            if it.get('kind') in ('separator', 'separator_stage', 'sink', 'oil_export', 'gas_export', 'water_disposal', 'water_source', 'gas_source', 'manifold', 'joint'):
                r['pressure_bar'] = it.get('pressure_bar')
        for k in cols: r[k] = p.get(k)
        rows.append(r)
    lead = ['ID', 'From', 'To', 'Kind'] if group == 'Flowlines' else ['ID', 'Name', 'Kind']
    core = list(EDGE_CORE) if group == 'Flowlines' else (['pressure_bar'] if any('pressure_bar' in r for r in rows) else [])
    df = pd.DataFrame(rows, columns=lead + core + cols)
    for c in core + cols:   # numeric columns stay numeric (editors need a uniform dtype); text / bool columns keep objects
        vals = [v for v in df[c] if v is not None and v == v]
        if vals and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals): df[c] = pd.to_numeric(df[c], errors='coerce')
        elif vals and all(isinstance(v, bool) for v in vals): df[c] = df[c].astype(object)
    return df, cols


def _blank(v):
    return v is None or (isinstance(v, float) and v != v) or (isinstance(v, str) and not v.strip())


def coerce(v, old=None):
    """Cell value -> stored value: bools and numbers are recognised, text is kept as text."""
    if isinstance(v, str):
        t = v.strip(); tl = t.lower()
        if tl in ('true', 'yes', 'on'): return True
        if tl in ('false', 'no', 'off'): return False
        try: return float(t)
        except ValueError: return t
    if hasattr(v, 'item'):
        try: v = v.item()
        except Exception: pass
    if isinstance(old, bool) and isinstance(v, (int, float)): return bool(v)
    return v


def apply_table(nodes, edges, group, df_new, df_old):
    """Write only the cells that changed between ``df_old`` and ``df_new`` back to the model (in place). Returns the number of changed values."""
    byid = {x['id']: x for x in (edges if group == 'Flowlines' else nodes)}
    old = {r['ID']: r for r in df_old.to_dict('records')}; changed = 0
    core = set(EDGE_CORE) if group == 'Flowlines' else set(NODE_CORE)
    for r in df_new.to_dict('records'):
        el = byid.get(r.get('ID')); o = old.get(r.get('ID'), {})
        if el is None: continue
        for c, v in r.items():
            if c in ('ID', 'Kind', 'From', 'To'): continue
            ov = o.get(c)
            if _blank(v) and _blank(ov): continue
            if not _blank(v) and not _blank(ov) and coerce(v, ov) == coerce(ov, ov): continue
            if c == 'Name':
                if not _blank(v) and str(v) != str(ov): el['name'] = str(v); changed += 1
                continue
            if c in core:
                if _blank(v): el.pop(c, None) if c == 'pressure_bar' else None
                else: el[c] = float(v)
                changed += 1; continue
            p = el.setdefault('params', {})
            if _blank(v): p.pop(c, None)
            else: p[c] = coerce(v, ov)
            changed += 1
    return changed


# ---- reading files ----------------------------------------------------------------------------------------------------------
def _load_structured(name, data):
    ext = name.lower().rsplit('.', 1)[-1]
    text = data.decode('utf-8-sig') if isinstance(data, (bytes, bytearray)) else str(data)
    if ext in ('yaml', 'yml'):
        import yaml
        return yaml.safe_load(text)
    return json.loads(text)


def _records_from_obj(obj):
    """Structured document -> list of record dicts (each with an id/name) or None."""
    if isinstance(obj, list) and all(isinstance(x, dict) for x in obj): return obj
    if isinstance(obj, dict):
        if all(isinstance(v, dict) for v in obj.values()) and obj:   # {id: {param: value}}
            return [{'id': k, **v} for k, v in obj.items()]
        for key in ('elements', 'wells', 'records', 'data'):
            if isinstance(obj.get(key), list): return obj[key]
    return None


def read_file(name, data):
    """-> {'project': {nodes, edges} | None, 'sheets': {sheet name: DataFrame}}. Raises ValueError for unreadable files."""
    import pandas as pd
    ext = name.lower().rsplit('.', 1)[-1]; out = {'project': None, 'sheets': {}}
    try:
        if ext in ('json', 'yaml', 'yml'):
            obj = _load_structured(name, data)
            if isinstance(obj, dict) and isinstance(obj.get('nodes'), list):
                out['project'] = {'nodes': obj['nodes'], 'edges': obj.get('edges') or []}; return out
            recs = _records_from_obj(obj)
            if recs is None: raise ValueError('Expected a project ({nodes, edges}), a list of records or {id: {parameters}}')
            out['sheets']['data'] = pd.json_normalize(recs, sep='.') if any(isinstance(v, dict) for r in recs for v in r.values()) else pd.DataFrame(recs)
        elif ext in ('xlsx', 'xlsm', 'xls'):
            for sh, df in pd.read_excel(io.BytesIO(data), sheet_name=None).items():
                if not df.empty: out['sheets'][str(sh)] = df
        elif ext in ('csv', 'txt', 'tsv'):
            out['sheets']['data'] = pd.read_csv(io.BytesIO(data), sep=None, engine='python')
        else:
            raise ValueError(f'Unsupported file type .{ext} (use json, yaml, xlsx or csv)')
    except ValueError: raise
    except Exception as exc: raise ValueError(f'Could not read {name}: {exc}')
    if not out['sheets'] and not out['project']: raise ValueError('The file has no data rows')
    return out


def merge_sheets(nodes, edges, sheets):
    """Merge sheet rows into the elements by ID (or by unique name). Returns a report dict; the model is changed in place."""
    byid = {n['id']: n for n in nodes}; bye = {e['id']: e for e in edges}
    byname = {}
    for n in nodes: byname.setdefault(str(n.get('name', '')).strip().lower(), []).append(n)
    rep = {'rows': 0, 'matched': 0, 'values': 0, 'unknown': [], 'ignored_columns': set()}
    for sh, df in sheets.items():
        cols = {str(c): c for c in df.columns}; low = {c.strip().lower(): c for c in cols}
        idc = next((cols[low[h]] for h in ID_HEADERS if h in low), None); namec = low.get('name') and cols.get(low['name'])
        if idc is None and namec is None:
            rep['unknown'].append(f'sheet "{sh}": no ID / Name column'); continue
        for r in df.to_dict('records'):
            rep['rows'] += 1; key = r.get(idc) if idc is not None else None
            el = None
            if not _blank(key):
                k = str(key).strip(); el = byid.get(k) or bye.get(k)
                if el is None and idc is not None and namec is None: el = (byname.get(k.lower()) or [None])[0] if len(byname.get(k.lower(), [])) == 1 else None
            if el is None and namec is not None and not _blank(r.get(namec)):
                cand = byname.get(str(r[namec]).strip().lower(), []); el = cand[0] if len(cand) == 1 else None
            if el is None:
                rep['unknown'].append(str(key if not _blank(key) else r.get(namec))); continue
            rep['matched'] += 1; is_edge = 'source' in el and 'target' in el
            for c, v in r.items():
                cs = str(c).strip(); cl = cs.lower()
                if c in (idc,) or cl in SKIP_HEADERS or cl in ID_HEADERS or _blank(v): continue
                if cl == 'name':
                    if not is_edge: el['name'] = str(v)
                    continue
                v = coerce(v)
                if cs in (EDGE_CORE if is_edge else NODE_CORE):
                    if isinstance(v, (int, float)) and not isinstance(v, bool): el[cs] = float(v); rep['values'] += 1
                    else: rep['ignored_columns'].add(cs)
                    continue
                if cs.startswith('_') or cl in ('from', 'to'): rep['ignored_columns'].add(cs); continue
                cur = (el.get('params') or {}).get(cs)
                if (isinstance(cur, bool) or cs in BOOL_KEYS) and isinstance(v, (int, float)) and not isinstance(v, bool) and v in (0, 1): v = bool(v)   # Excel / CSV readers turn True/False into 1/0
                el.setdefault('params', {})[cs] = v; rep['values'] += 1
    rep['ignored_columns'] = sorted(rep['ignored_columns']); rep['unknown'] = list(dict.fromkeys(rep['unknown']))
    return rep


# ---- exporting ----------------------------------------------------------------------------------------------------------------
def to_workbook(nodes, edges):
    """All inputs as an .xlsx (one sheet per element group). Re-importable with :func:`read_file` + :func:`merge_sheets`."""
    buf = io.BytesIO()
    import pandas as pd
    with pd.ExcelWriter(buf, engine='openpyxl') as xw:
        wrote = False
        for g in list(GROUPS) + ['Flowlines']:
            if not members(nodes, edges, g): continue
            df, _ = element_table(nodes, edges, g); df.to_excel(xw, sheet_name=g[:31].replace('&', 'and'), index=False); wrote = True
        if not wrote: pd.DataFrame({'ID': []}).to_excel(xw, sheet_name='Empty', index=False)
    return buf.getvalue()


def to_csv(nodes, edges, group):
    df, _ = element_table(nodes, edges, group); return df.to_csv(index=False)


def to_yaml(nodes, edges):
    import yaml
    from ui.graph_contract import to_builtin
    return yaml.safe_dump(to_builtin({'nodes': copy.deepcopy(nodes), 'edges': copy.deepcopy(edges)}), sort_keys=False, allow_unicode=True)
