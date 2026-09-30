"""FieldNet v29 validated data interchange and project packages.

Canonical project storage is preserved. Tabular import/export may use canonical,
Norwegian SI, or Field display values; conversion occurs only at the boundary.
"""
from __future__ import annotations
import copy, csv, io, json, zipfile, hashlib, math
from datetime import datetime, timezone
from typing import Any
from physics.unit_system import PROFILES, STANDARD_CONDITIONS, convert_mapping_units
from ui.history import normalize_project

APPLICATION='FieldNet v29.1'
SCHEMA_VERSION='27.0'
NODE_COLUMNS=['id','kind','name','pressure_bar','x','y','params_json']
EDGE_COLUMNS=['id','source','target','kind','length_m','diameter_m','roughness_m','elevation_change_m','params_json']

def _profile(profile):
    if profile not in ('canonical',*PROFILES): raise ValueError(f'unknown unit profile: {profile}')
    return profile

def _display(obj, profile, direction):
    return copy.deepcopy(obj) if profile=='canonical' else convert_mapping_units(obj,profile,direction)

def _nonfinite_paths(obj, prefix=''):
    out=[]
    if isinstance(obj, dict):
        for k,v in obj.items(): out.extend(_nonfinite_paths(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, (list,tuple)):
        for i,v in enumerate(obj): out.extend(_nonfinite_paths(v, f"{prefix}[{i}]"))
    elif isinstance(obj, (int,float)) and not isinstance(obj,bool) and not math.isfinite(float(obj)):
        out.append(prefix or '*')
    return out

def validate_project(nodes:list[dict], edges:list[dict])->list[dict]:
    issues=[]
    for table, rows in (('nodes',nodes),('edges',edges)):
        for i,row in enumerate(rows,2):
            for path in _nonfinite_paths(row):
                issues.append({'table':table,'row':i,'field':path,'severity':'error','message':'numeric value must be finite'})
    ids=[]
    for i,n in enumerate(nodes,2):
        nid=str(n.get('id','')).strip(); kind=str(n.get('kind','')).strip()
        if not nid: issues.append({'table':'nodes','row':i,'field':'id','severity':'error','message':'node id is required'})
        if nid in ids: issues.append({'table':'nodes','row':i,'field':'id','severity':'error','message':f'duplicate node id {nid}'})
        ids.append(nid)
        if not kind: issues.append({'table':'nodes','row':i,'field':'kind','severity':'error','message':'node kind is required'})
        if n.get('pressure_bar') is not None:
            try:
                if float(n['pressure_bar']) < 0: issues.append({'table':'nodes','row':i,'field':'pressure_bar','severity':'warning','message':'negative canonical pressure'})
            except Exception: issues.append({'table':'nodes','row':i,'field':'pressure_bar','severity':'error','message':'pressure must be numeric'})
    valid=set(ids)
    eids=set()
    for i,e in enumerate(edges,2):
        eid=str(e.get('id','')).strip()
        if not eid: issues.append({'table':'edges','row':i,'field':'id','severity':'error','message':'edge id is required'})
        if eid in eids: issues.append({'table':'edges','row':i,'field':'id','severity':'error','message':f'duplicate edge id {eid}'})
        eids.add(eid)
        for f in ('source','target'):
            if e.get(f) not in valid: issues.append({'table':'edges','row':i,'field':f,'severity':'error','message':f'unknown node {e.get(f)}'})
        # Only pipelines need a physical length; chokes/valves/pumps/compressors are created
        # with length_m=0, which previously made every such case fail export/snapshot.
        needed=('length_m','diameter_m') if e.get('kind','pipeline')=='pipeline' else ()
        for f in needed:
            try:
                if float(e.get(f,0)) <= 0: issues.append({'table':'edges','row':i,'field':f,'severity':'error','message':f'{f} must be > 0'})
            except Exception: issues.append({'table':'edges','row':i,'field':f,'severity':'error','message':f'{f} must be numeric'})
    return issues

def _csv(rows, cols):
    s=io.StringIO(); w=csv.DictWriter(s,fieldnames=cols,extrasaction='ignore'); w.writeheader(); w.writerows(rows); return s.getvalue()

def export_tables(nodes,edges,profile='canonical'):
    _profile(profile); ns=_display(nodes,profile,'to_display'); es=_display(edges,profile,'to_display')
    nr=[]
    for n in ns:
        nr.append({k:n.get(k) for k in NODE_COLUMNS[:-1]}|{'params_json':json.dumps(n.get('params',{}),sort_keys=True,separators=(',',':'))})
    er=[]
    for e in es:
        er.append({k:e.get(k) for k in EDGE_COLUMNS[:-1]}|{'params_json':json.dumps(e.get('params',{}),sort_keys=True,separators=(',',':'))})
    return {'nodes.csv':_csv(nr,NODE_COLUMNS),'edges.csv':_csv(er,EDGE_COLUMNS)}

def _num(v, default=None):
    if v is None or str(v).strip()=='': return default
    
    x=float(v)
    if not math.isfinite(x): raise ValueError('numeric value must be finite')
    return x

def import_tables(nodes_csv:str, edges_csv:str, profile='canonical'):
    _profile(profile); issues=[]
    def read(text, table):
        try: return list(csv.DictReader(io.StringIO(text)))
        except Exception as exc:
            issues.append({'table':table,'row':1,'field':'*','severity':'error','message':str(exc)}); return []
    nr,er=read(nodes_csv,'nodes'),read(edges_csv,'edges'); nodes=[]; edges=[]
    for i,r in enumerate(nr,2):
        try:
            p=json.loads(r.get('params_json') or '{}');
            if not isinstance(p,dict): raise ValueError('params_json must contain an object')
            nodes.append({'id':str(r.get('id','')).strip(),'kind':str(r.get('kind','')).strip(),'name':str(r.get('name','')).strip(),'pressure_bar':_num(r.get('pressure_bar')),'x':_num(r.get('x'),0.0),'y':_num(r.get('y'),0.0),'params':p})
        except Exception as exc: issues.append({'table':'nodes','row':i,'field':'params_json','severity':'error','message':str(exc)})
    for i,r in enumerate(er,2):
        try:
            p=json.loads(r.get('params_json') or '{}');
            if not isinstance(p,dict): raise ValueError('params_json must contain an object')
            edges.append({'id':str(r.get('id','')).strip(),'source':str(r.get('source','')).strip(),'target':str(r.get('target','')).strip(),'kind':str(r.get('kind','pipeline')).strip() or 'pipeline','length_m':_num(r.get('length_m'),1000.0),'diameter_m':_num(r.get('diameter_m'),.15),'roughness_m':_num(r.get('roughness_m'),4.5e-5),'elevation_change_m':_num(r.get('elevation_change_m'),0.0),'params':p})
        except Exception as exc: issues.append({'table':'edges','row':i,'field':'params_json','severity':'error','message':str(exc)})
    if profile!='canonical': nodes=_display(nodes,profile,'from_display'); edges=_display(edges,profile,'from_display')
    issues.extend(validate_project(nodes,edges)); ok=not any(x['severity']=='error' for x in issues)
    if ok:
        try: normalize_project({'nodes':nodes,'edges':edges})
        except Exception as exc: issues.append({'table':'project','row':0,'field':'*','severity':'error','message':str(exc)}); ok=False
    return {'ok':ok,'nodes':nodes,'edges':edges,'issues':issues,'profile':profile}

def project_manifest(nodes,edges,profile='canonical'):
    _profile(profile)
    return {'application':APPLICATION,'schema_version':SCHEMA_VERSION,'storage_units':'canonical','interchange_profile':profile,'standard_conditions':STANDARD_CONDITIONS,'node_count':len(nodes),'edge_count':len(edges),'created_utc':datetime.now(timezone.utc).isoformat(),'files':['project.json','nodes.csv','edges.csv','manifest.json']}

def export_project_package(nodes,edges,profile='canonical')->bytes:
    issues=validate_project(nodes,edges)
    if any(x['severity']=='error' for x in issues): raise ValueError('project validation failed: '+issues[0]['message'])
    tabs=export_tables(nodes,edges,profile); project={'application':APPLICATION,'schema_version':SCHEMA_VERSION,'storage_units':'canonical','display_unit_profile':profile if profile!='canonical' else 'norwegian_si','standard_conditions':STANDARD_CONDITIONS,'nodes':copy.deepcopy(nodes),'edges':copy.deepcopy(edges)}
    manifest=project_manifest(nodes,edges,profile)
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('project.json',json.dumps(project,indent=2)); z.writestr('nodes.csv',tabs['nodes.csv']); z.writestr('edges.csv',tabs['edges.csv']); z.writestr('manifest.json',json.dumps(manifest,indent=2))
    return buf.getvalue()

def import_project_package(data:bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names=set(z.namelist()); required={'project.json','manifest.json'}
        if not required<=names: raise ValueError('package must contain project.json and manifest.json')
        manifest=json.loads(z.read('manifest.json')); project=json.loads(z.read('project.json'))
    nodes,edges=normalize_project(project); issues=validate_project(nodes,edges)
    return {'ok':not any(x['severity']=='error' for x in issues),'nodes':nodes,'edges':edges,'manifest':manifest,'issues':issues}

def package_sha256(data:bytes)->str: return hashlib.sha256(data).hexdigest()

def import_time_series_csv(text:str, profile='canonical'):
    _profile(profile); rows=list(csv.DictReader(io.StringIO(text))); out=[]; issues=[]
    for i,r in enumerate(rows,2):
        try:
            date=str(r.get('date','')).strip(); target=str(r.get('target_id','')).strip(); kind=str(r.get('kind','')).strip(); value=float(r.get('value')); sigma=float(r.get('sigma',1.0));
            if not math.isfinite(value) or not math.isfinite(sigma): raise ValueError('value and sigma must be finite')
            if not date or not target or not kind: raise ValueError('date, target_id and kind are required')
            if sigma<=0: raise ValueError('sigma must be > 0')
            out.append({'date':date,'target_id':target,'kind':kind,'value':value,'sigma':sigma})
        except Exception as exc: issues.append({'table':'timeseries','row':i,'field':'*','severity':'error','message':str(exc)})
    return {'ok':not issues,'rows':out,'issues':issues,'profile':profile}
