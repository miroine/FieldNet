"""FieldNet v29 scenario management and reproducible run snapshots."""
from __future__ import annotations
import copy, hashlib, io, json, zipfile
from datetime import datetime, timezone
from typing import Any
from network.interchange_v27 import validate_project

APPLICATION='FieldNet v29'
SCHEMA_VERSION='29.0'

def _canon(x:Any)->str:
    return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,default=str)

def content_hash(nodes,edges,assumptions=None,parent_id=None)->str:
    payload={'nodes':nodes,'edges':edges,'assumptions':assumptions or [],'parent_id':parent_id}
    return hashlib.sha256(_canon(payload).encode()).hexdigest()

def normalize_assumptions(items):
    out=[]
    for i,a in enumerate(items or []):
        if isinstance(a,str): a={'statement':a}
        if not isinstance(a,dict): raise ValueError(f'assumption {i} must be object or string')
        statement=str(a.get('statement','')).strip()
        if not statement: raise ValueError(f'assumption {i} statement is required')
        out.append({'statement':statement,'category':str(a.get('category','general')),'source':str(a.get('source','user')),'status':str(a.get('status','active'))})
    return out

def create_snapshot(name,nodes,edges,*,assumptions=None,parent_id=None,description='',qa_report=None,tags=None,created_utc=None):
    issues=validate_project(nodes,edges)
    if any(x['severity']=='error' for x in issues): raise ValueError('invalid project: '+next(x['message'] for x in issues if x['severity']=='error'))
    ass=normalize_assumptions(assumptions); h=content_hash(nodes,edges,ass,parent_id)
    return {'application':APPLICATION,'schema_version':SCHEMA_VERSION,'snapshot_id':h[:16],'content_sha256':h,'name':str(name).strip() or 'Scenario','description':str(description),'parent_id':parent_id,'created_utc':created_utc or datetime.now(timezone.utc).isoformat(),'tags':list(tags or []),'assumptions':ass,'qa':copy.deepcopy(qa_report),'nodes':copy.deepcopy(nodes),'edges':copy.deepcopy(edges)}

def verify_snapshot(s):
    required=('snapshot_id','content_sha256','nodes','edges','assumptions')
    missing=[k for k in required if k not in s]
    if missing:return {'ok':False,'reason':'missing '+','.join(missing)}
    h=content_hash(s['nodes'],s['edges'],s.get('assumptions'),s.get('parent_id'))
    return {'ok':h==s['content_sha256'] and s['snapshot_id']==h[:16],'expected_sha256':h,'stored_sha256':s.get('content_sha256')}

def branch_snapshot(parent,name,*,nodes=None,edges=None,assumptions=None,description=''):
    v=verify_snapshot(parent)
    if not v['ok']: raise ValueError('parent snapshot hash verification failed')
    return create_snapshot(name,nodes if nodes is not None else parent['nodes'],edges if edges is not None else parent['edges'],assumptions=assumptions if assumptions is not None else parent.get('assumptions',[]),parent_id=parent['snapshot_id'],description=description)

def _index(items): return {str(x.get('id')):x for x in items}
def scenario_diff(a,b):
    changes=[]
    for table in ('nodes','edges'):
        ai,bi=_index(a.get(table,[])),_index(b.get(table,[]))
        for ident in sorted(set(ai)|set(bi)):
            if ident not in ai: changes.append({'table':table,'id':ident,'change':'added','before':None,'after':copy.deepcopy(bi[ident])})
            elif ident not in bi: changes.append({'table':table,'id':ident,'change':'removed','before':copy.deepcopy(ai[ident]),'after':None})
            elif _canon(ai[ident])!=_canon(bi[ident]): changes.append({'table':table,'id':ident,'change':'modified','before':copy.deepcopy(ai[ident]),'after':copy.deepcopy(bi[ident])})
    if _canon(a.get('assumptions',[]))!=_canon(b.get('assumptions',[])):
        changes.append({'table':'assumptions','id':'*','change':'modified','before':copy.deepcopy(a.get('assumptions',[])),'after':copy.deepcopy(b.get('assumptions',[]))})
    return {'application':APPLICATION,'from':a.get('snapshot_id'),'to':b.get('snapshot_id'),'change_count':len(changes),'changes':changes}

def comparison_table(snapshots):
    rows=[]
    for s in snapshots:
        v=verify_snapshot(s); kinds={}
        for n in s.get('nodes',[]): kinds[n.get('kind','unknown')]=kinds.get(n.get('kind','unknown'),0)+1
        rows.append({'snapshot_id':s.get('snapshot_id'),'name':s.get('name'),'parent_id':s.get('parent_id'),'hash_ok':v['ok'],'quality_gate':(s.get('qa') or {}).get('quality_gate'),'nodes':len(s.get('nodes',[])),'edges':len(s.get('edges',[])),'wells':kinds.get('well',0),'assumptions':len(s.get('assumptions',[]))})
    return rows

def run_manifest(snapshot,*,run_type='scenario',settings=None,result_summary=None):
    v=verify_snapshot(snapshot)
    if not v['ok']: raise ValueError('snapshot hash verification failed')
    body={'application':APPLICATION,'schema_version':SCHEMA_VERSION,'snapshot_id':snapshot['snapshot_id'],'snapshot_sha256':snapshot['content_sha256'],'run_type':run_type,'settings':copy.deepcopy(settings or {}),'result_summary':copy.deepcopy(result_summary or {}),'qa_gate':(snapshot.get('qa') or {}).get('quality_gate')}
    body['manifest_sha256']=hashlib.sha256(_canon(body).encode()).hexdigest(); return body

def export_scenario_archive(snapshots,manifests=None)->bytes:
    if not snapshots: raise ValueError('at least one snapshot required')
    for s in snapshots:
        if not verify_snapshot(s)['ok']: raise ValueError('snapshot hash verification failed')
    index={'application':APPLICATION,'schema_version':SCHEMA_VERSION,'snapshot_ids':[s['snapshot_id'] for s in snapshots],'snapshot_count':len(snapshots)}
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('index.json',json.dumps(index,indent=2))
        for s in snapshots:z.writestr(f"snapshots/{s['snapshot_id']}.json",json.dumps(s,indent=2,default=str))
        for i,m in enumerate(manifests or []):z.writestr(f'runs/run_{i+1:04d}.json',json.dumps(m,indent=2,default=str))
    return buf.getvalue()

def import_scenario_archive(data:bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names=z.namelist()
        if 'index.json' not in names: raise ValueError('scenario archive missing index.json')
        idx=json.loads(z.read('index.json')); snaps=[]
        for sid in idx.get('snapshot_ids',[]):
            fn=f'snapshots/{sid}.json'
            if fn not in names: raise ValueError(f'missing snapshot {sid}')
            s=json.loads(z.read(fn))
            if not verify_snapshot(s)['ok']: raise ValueError(f'snapshot hash mismatch: {sid}')
            snaps.append(s)
        runs=[json.loads(z.read(n)) for n in names if n.startswith('runs/') and n.endswith('.json')]
    return {'index':idx,'snapshots':snaps,'manifests':runs}
