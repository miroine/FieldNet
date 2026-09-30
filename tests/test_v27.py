import copy, io, json, zipfile, pytest
from network.examples import demo_case
from network.interchange_v27 import *

def test_csv_canonical_roundtrip():
 n,e=demo_case(); t=export_tables(n,e,'canonical'); r=import_tables(t['nodes.csv'],t['edges.csv'],'canonical'); assert r['ok'] and r['nodes']==n and r['edges']==e

def test_field_roundtrip_preserves_physics():
 n,e=demo_case(); t=export_tables(n,e,'field'); r=import_tables(t['nodes.csv'],t['edges.csv'],'field'); assert r['ok']; assert r['nodes'][0]['params']['reservoir_pressure_bar']==pytest.approx(n[0]['params']['reservoir_pressure_bar']); assert r['edges'][0]['length_m']==pytest.approx(e[0]['length_m'])

def test_norwegian_roundtrip_diameter():
 n,e=demo_case(); t=export_tables(n,e,'norwegian_si'); assert '154.0' in t['edges.csv']; r=import_tables(t['nodes.csv'],t['edges.csv'],'norwegian_si'); assert r['edges'][0]['diameter_m']==pytest.approx(.154)

def test_invalid_reference_rejected_without_mutation():
 n,e=demo_case(); n0=copy.deepcopy(n); t=export_tables(n,e); bad=t['edges.csv'].replace(',w1,m1,',',BAD,m1,',1); r=import_tables(t['nodes.csv'],bad); assert not r['ok'] and n==n0 and any(x['field']=='source' for x in r['issues'])

def test_duplicate_node_rejected():
 n,e=demo_case(); x=copy.deepcopy(n); x[1]['id']=x[0]['id']; assert any(i['severity']=='error' for i in validate_project(x,e))

def test_project_package_roundtrip_and_manifest():
 n,e=demo_case(); b=export_project_package(n,e,'field'); r=import_project_package(b); assert r['ok'] and r['manifest']['application']=='FieldNet v29.1' and r['manifest']['storage_units']=='canonical'; assert r['nodes']==n

def test_package_contains_canonical_json_and_display_csv():
 n,e=demo_case(); b=export_project_package(n,e,'field'); z=zipfile.ZipFile(io.BytesIO(b)); p=json.loads(z.read('project.json')); assert p['storage_units']=='canonical' and p['nodes'][0]['params']['reservoir_pressure_bar']==240.0; assert b'psi' not in z.read('project.json')

def test_time_series_validation():
 good='date,target_id,kind,value,sigma\n2026-01-01,w1,pressure,100,2\n'; assert import_time_series_csv(good)['ok']; bad='date,target_id,kind,value,sigma\n2026-01-01,w1,pressure,100,0\n'; assert not import_time_series_csv(bad)['ok']

def test_unknown_profile_rejected():
 n,e=demo_case();
 with pytest.raises(ValueError): export_tables(n,e,'magic')
