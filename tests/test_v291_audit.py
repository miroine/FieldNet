import copy, io, json, math, zipfile
import pytest
from network.interchange_v27 import import_tables, validate_project
from solver.model_assurance_v28 import model_quality_report, forecast_assurance
from optimization.calibration_v23 import CalParameter, apply_parameters
from network.reliability_v24 import ReliabilityStudy, run_reliability
from network.reservoir import ReservoirTank
from network.reservoir_v25 import CommunicationLink, step_coupled_tanks
from network.scenario_v29 import create_snapshot, run_manifest, export_scenario_archive, import_scenario_archive
from network.examples import demo_case

def test_nan_csv_rejected():
    n='id,kind,name,pressure_bar,x,y,params_json\na,sink,A,nan,0,0,{}\n'
    e='id,source,target,kind,length_m,diameter_m,roughness_m,elevation_change_m,params_json\n'
    r=import_tables(n,e); assert not r['ok']; assert any('finite' in x['message'] for x in r['issues'])

def test_nan_geometry_rejected():
    n,e=demo_case(); e=copy.deepcopy(e); e[0]['length_m']=float('nan')
    assert any(x['severity']=='error' and 'finite' in x['message'] for x in validate_project(n,e))

def test_assurance_rejects_nonfinite_pre_and_forecast():
    n,e=demo_case(); n=copy.deepcopy(n); n[0]['pressure_bar']=float('nan')
    r=model_quality_report(n,e); assert r['quality_gate']=='FAIL'
    f={'field':[{'Date':'2026-01-01','Oil [m3/d]':float('nan')}]}
    x=forecast_assurance(f); assert any(i['code']=='NONFINITE_FORECAST' for i in x)

def test_roughness_calibration_hits_solver_field():
    n,e=demo_case(); pipe=next(x for x in e if x.get('kind')=='pipeline'); base=pipe['roughness_m']
    par=CalParameter('r',pipe['id'],'edge_roughness_mult',.25,4,1)
    _,lo=apply_parameters(n,e,[par],[.25]); _,hi=apply_parameters(n,e,[par],[4.0])
    plo=next(x for x in lo if x['id']==pipe['id']); phi=next(x for x in hi if x['id']==pipe['id'])
    assert plo['roughness_m']==pytest.approx(base*.25); assert phi['roughness_m']==pytest.approx(base*4)

def test_reliability_clips_final_interval():
    r=run_reliability(ReliabilityStudy(years=.1,step_days=30,realizations=1,specs=[]),100.0)
    assert r['study']['horizon_days']==pytest.approx(36.525)
    assert r['summary']['mean_production_m3']==pytest.approx(3652.5)
    assert r['summary']['mean_deferred_m3']==pytest.approx(0.0)

def test_stiff_reservoir_link_cannot_overshoot_equalization():
    tanks={'a':ReservoirTank(id='a',name='A',initial_pressure_bar=300,pressure_bar=300,pore_volume_m3=100,total_compressibility_1bar=1.0,min_pressure_bar=50),'b':ReservoirTank(id='b',name='B',initial_pressure_bar=100,pressure_bar=100,pore_volume_m3=100,total_compressibility_1bar=1.0,min_pressure_bar=50)}
    r=step_coupled_tanks(tanks,{},dt_days=30,links=[CommunicationLink('a','b',1000)])
    assert tanks['a'].pressure_bar==pytest.approx(200); assert tanks['b'].pressure_bar==pytest.approx(200)
    assert abs(r['communication_balance_m3'])<1e-9; assert r['transfers'][0]['stability_limited']

def test_tampered_run_manifest_rejected():
    n,e=demo_case(); s=create_snapshot('base',n,e); m=run_manifest(s,result_summary={'oil':1})
    data=export_scenario_archive([s],[m])
    src=zipfile.ZipFile(io.BytesIO(data)); files={x:src.read(x) for x in src.namelist()}; src.close()
    obj=json.loads(files['runs/run_0001.json']); obj['result_summary']['oil']=999; files['runs/run_0001.json']=json.dumps(obj).encode()
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for k,v in files.items(): z.writestr(k,v)
    with pytest.raises(ValueError,match='manifest hash mismatch'): import_scenario_archive(out.getvalue())

def test_current_release_metadata():
    n,e=demo_case(); s=create_snapshot('base',n,e); assert s['application']=='FieldNet v29.1'
