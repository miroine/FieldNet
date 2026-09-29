import copy, io, json, zipfile
from network.examples import demo_case
from network.scenario_v29 import *
from solver.model_assurance_v28 import model_quality_report

def test_snapshot_deterministic_hash_for_same_content():
 n,e=demo_case(); a=create_snapshot('A',n,e,created_utc='x'); b=create_snapshot('B',n,e,created_utc='y'); assert a['content_sha256']==b['content_sha256']
def test_snapshot_deep_copy_immutable():
 n,e=demo_case(); s=create_snapshot('A',n,e); n[0]['name']='CHANGED'; assert s['nodes'][0]['name']!='CHANGED' and verify_snapshot(s)['ok']
def test_branch_lineage_and_diff():
 n,e=demo_case(); a=create_snapshot('Base',n,e); n2=copy.deepcopy(n); n2[0]['params']['water_cut']=.4; b=branch_snapshot(a,'Branch',nodes=n2); d=scenario_diff(a,b); assert b['parent_id']==a['snapshot_id'] and d['change_count']==1 and d['changes'][0]['change']=='modified'
def test_assumption_change_changes_hash():
 n,e=demo_case(); a=create_snapshot('A',n,e,assumptions=['one']); b=create_snapshot('B',n,e,assumptions=['two']); assert a['content_sha256']!=b['content_sha256']
def test_tamper_detection():
 n,e=demo_case(); s=create_snapshot('A',n,e); s['nodes'][0]['name']='tampered'; assert not verify_snapshot(s)['ok']
def test_archive_roundtrip_verifies_hashes():
 n,e=demo_case(); a=create_snapshot('A',n,e); b=branch_snapshot(a,'B'); data=export_scenario_archive([a,b],[run_manifest(a)]); r=import_scenario_archive(data); assert [x['snapshot_id'] for x in r['snapshots']]==[a['snapshot_id'],b['snapshot_id']]
def test_archive_rejects_tampered_snapshot():
 n,e=demo_case(); s=create_snapshot('A',n,e); data=export_scenario_archive([s]); zin=zipfile.ZipFile(io.BytesIO(data)); files={x:zin.read(x) for x in zin.namelist()}; zin.close(); obj=json.loads(files[f"snapshots/{s['snapshot_id']}.json"]); obj['nodes'][0]['name']='bad'; files[f"snapshots/{s['snapshot_id']}.json"]=json.dumps(obj).encode(); out=io.BytesIO(); z=zipfile.ZipFile(out,'w'); [z.writestr(k,v) for k,v in files.items()]; z.close();
 try: import_scenario_archive(out.getvalue()); assert False
 except ValueError: pass
def test_manifest_reproducible_and_qa_capture():
 n,e=demo_case(); qa=model_quality_report(n,e); s=create_snapshot('A',n,e,qa_report=qa,created_utc='x'); a=run_manifest(s,settings={'x':1}); b=run_manifest(s,settings={'x':1}); assert a['manifest_sha256']==b['manifest_sha256'] and a['qa_gate']==qa['quality_gate']
def test_comparison_table():
 n,e=demo_case(); s=create_snapshot('A',n,e,assumptions=['x']); row=comparison_table([s])[0]; assert row['hash_ok'] and row['assumptions']==1 and row['nodes']==len(n)
