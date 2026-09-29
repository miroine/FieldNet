import pytest
from optimization.integrated_v22 import discover_decisions,apply_decisions,evaluate_case,optimize_integrated

def case():
    nodes=[
      {'id':'w','name':'W1','kind':'well','pressure_bar':None,'params':{'reservoir_pressure_bar':220,'pi_m3d_bar':8,'qmax_m3d':1200,'depth_m':1800,'tubing_id_m':.09,'temperature_c':70,'water_cut':.2,'gor_sm3sm3':80}},
      {'id':'m','name':'M','kind':'manifold','pressure_bar':None,'params':{}},
      {'id':'s','name':'Export','kind':'sink','pressure_bar':30,'params':{}}]
    edges=[{'id':'e1','name':'Flowline','kind':'pipeline','source':'w','target':'m','length_m':1000,'diameter_m':.15,'roughness_m':4.5e-5,'elevation_change_m':0,'params':{}}, {'id':'v','name':'CV','kind':'control_valve','source':'m','target':'s','params':{'cv':150,'opening':.8}}]
    return nodes,edges

def test_decision_discovery():
    n,e=case(); ds=discover_decisions(n,e); assert {d.kind for d in ds}=={'well_opening','valve_opening'}

def test_apply_isolation_and_bounds():
    n,e=case(); ds=discover_decisions(n,e); nn,ee=apply_decisions(n,e,ds,[.5,.6]); assert n[0]['params']['pi_m3d_bar']==8; assert nn[0]['params']['pi_m3d_bar']==4; assert ee[1]['params']['opening']==pytest.approx(.6)

def test_evaluate_reports_feasibility_separately():
    n,e=case(); ds=discover_decisions(n,e); r=evaluate_case(n,e,ds,[1,.8]); assert 'feasible' in r and 'objective' in r and r['result'] is not None

def test_optimizer_reproducible_and_not_global_claim():
    n,e=case(); a=optimize_integrated(n,e,maxiter=2,seed=4); b=optimize_integrated(n,e,maxiter=2,seed=4); assert a['controls']==pytest.approx(b['controls']); assert a['global_optimum_guaranteed'] is False

def test_pump_affinity_application():
    n,e=case(); e.append({'id':'p','name':'P','kind':'pump','source':'m','target':'s','params':{'shutoff_head_bar':40,'rated_rate_m3d':1000,'speed_fraction':1}}); ds=discover_decisions(n,e); d=[x for x in ds if x.kind=='pump_speed'][0]; nn,ee=apply_decisions(n,e,[d],[.8]); p=[x for x in ee if x['id']=='p'][0]['params']; assert p['shutoff_head_bar']==pytest.approx(25.6); assert p['rated_rate_m3d']==pytest.approx(800)

def test_uncoupled_gas_lift_is_disclosed():
    n,e=case(); n[0]['params']['lift_type']='gas_lift'; r=optimize_integrated(n,e,maxiter=1); assert any('Gas-lift' in x for x in r['unsupported'])
