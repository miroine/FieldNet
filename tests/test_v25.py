import math, copy
import pytest
from network.reservoir import ReservoirTank
from network.reservoir_v25 import AquiferSpec, CommunicationLink, InjectorConnection, allocate_connected_injection, step_coupled_tanks, validate_model
from network.coupled_forecast_v25 import run_coupled_forecast_v25
from network.examples import demo_case

def tank(i,p=200): return ReservoirTank(i,i,p,p,pore_volume_m3=1e6,total_compressibility_1bar=1e-4,min_pressure_bar=20)

def test_communication_is_conservative_and_equalizes_pressure():
    ts={'A':tank('A',220),'B':tank('B',180)}
    r=step_coupled_tanks(ts,{},dt_days=1,links=[CommunicationLink('A','B',10)])
    assert math.isclose(r['communication_balance_m3'],0,abs_tol=1e-12)
    assert ts['A'].pressure_bar < 220 and ts['B'].pressure_bar > 180
    assert math.isclose(r['ledger'][0]['communication_out_m3']+r['ledger'][1]['communication_out_m3'],400)

def test_aquifer_is_pressure_dependent_and_capped():
    t=tank('A',200); ts={'A':t}
    r=step_coupled_tanks(ts,{'A':1000},dt_days=1,aquifers=[AquiferSpec('A',10,250,300)])
    row=r['ledger'][0]; assert row['aquifer_m3']==300 and math.isclose(row['net_voidage_m3'],700)

def test_connected_injection_normalizes_weights_and_conserves_volume():
    c=[InjectorConnection('I1','A',1),InjectorConnection('I1','B',3)]
    a=allocate_connected_injection({'I1':400},c,['A','B'])
    assert a=={'A':100.0,'B':300.0} and sum(a.values())==400

def test_support_can_repressurize_but_floor_is_respected():
    ts={'A':tank('A',100)}
    step_coupled_tanks(ts,{'A':0},{'A':500},1)
    assert ts['A'].pressure_bar==105
    ts={'A':tank('A',21)}; step_coupled_tanks(ts,{'A':1000},{},1)
    assert ts['A'].pressure_bar==20

def test_invalid_model_fails_fast():
    with pytest.raises(ValueError): validate_model({'A':tank('A')},links=[CommunicationLink('A','X',1)])
    with pytest.raises(ValueError): validate_model({'A':tank('A')},connections=[InjectorConnection('I','X',1)])

def test_forecast_exact_horizon_and_base_isolation():
    nodes,edges=demo_case(); n0=copy.deepcopy(nodes); e0=copy.deepcopy(edges)
    tanks=[{'id':'T1','name':'T1','initial_pressure_bar':240,'pressure_bar':240},{'id':'T2','name':'T2','initial_pressure_bar':225,'pressure_bar':225}]
    mapping={'w1':'T1','w2':'T2'}
    r=run_coupled_forecast_v25(nodes,edges,tanks,mapping,'2026-01-01',years=0.1,step_days=30,communication_links=[CommunicationLink('T1','T2',0.1)])
    assert r['field'] and sum(x['dt_days'] for x in r['field'])==pytest.approx(0.1*365.25)
    assert nodes==n0 and edges==e0 and r['application']=='FieldNet v29.1'

def test_forecast_material_balance_ledger_has_required_terms():
    nodes,edges=demo_case(); tanks=[{'id':'T1','name':'T1','initial_pressure_bar':240,'pressure_bar':240},{'id':'T2','name':'T2','initial_pressure_bar':225,'pressure_bar':225}]
    r=run_coupled_forecast_v25(nodes,edges,tanks,{'w1':'T1','w2':'T2'},'2026-01-01',years=0.02,step_days=7,
      aquifers=[AquiferSpec('T1',1,250,100)],injector_schedule=[{'date':'2026-01-01','injector_id':'I1','rate_m3d':100}],injector_connections=[InjectorConnection('I1','T2',1)])
    row=r['tanks'][0]
    for k in ['withdrawal_m3','injection_m3','aquifer_m3','communication_in_m3','communication_out_m3','net_voidage_m3','pressure_change_bar']: assert k in row
