import copy
from network.examples import demo_case
from solver.v21 import solve_v21
from solver.model_assurance_v28 import pre_solve_assurance, post_solve_assurance, forecast_assurance, model_quality_report

def codes(x): return {i['code'] for i in x}
def test_clean_demo_precheck_has_no_errors():
 n,e=demo_case(); x=pre_solve_assurance(n,e); assert not any(i['severity']=='error' for i in x)
def test_bad_water_cut_and_geometry_are_caught():
 n,e=demo_case(); n[0]['params']['water_cut']=1.2; e[0]['roughness_m']=-1; x=pre_solve_assurance(n,e); assert {'WATER_CUT_RANGE','ROUGHNESS_NEGATIVE'}<=codes(x)
def test_dangling_edge_is_error():
 n,e=demo_case(); e[0]['source']='missing'; x=pre_solve_assurance(n,e); assert any(i['code']=='DANGLING_EDGE' and i['severity']=='error' for i in x)
def test_checker_does_not_mutate_case():
 n,e=demo_case(); a,b=copy.deepcopy(n),copy.deepcopy(e); model_quality_report(n,e); assert n==a and e==b
def test_postsolve_demo_passes_without_errors():
 n,e=demo_case(); p,q,info,d=solve_v21(n,e); x=post_solve_assurance(n,e,p,q,info); assert not any(i['severity']=='error' for i in x)
def test_bad_physical_residual_fails():
 n,e=demo_case(); info={'success':True,'physical_residual_audit':{'max_pressure_residual_bar':1,'max_mass_residual_m3d':0},'constraints':[]}; x=post_solve_assurance(n,e,{'x':10},{},info); assert 'PRESSURE_CLOSURE' in codes(x)
def test_forecast_negative_value_fails():
 x=forecast_assurance({'field':[{'Date':'2026-01-01','Oil [m3/d]':-1}]}); assert 'NEGATIVE_FORECAST' in codes(x)
def test_report_gate_review_for_warning_only():
 n,e=demo_case(); e[0]['diameter_m']=2.0; r=model_quality_report(n,e); assert r['quality_gate']=='REVIEW'
def test_report_gate_fail_for_error():
 n,e=demo_case(); n[0]['params']['depth_m']=0; r=model_quality_report(n,e); assert r['quality_gate']=='FAIL'
def test_report_identity_v28():
 n,e=demo_case(); r=model_quality_report(n,e); assert r['application']=='FieldNet v29' and r['schema_version']=='28.0'
