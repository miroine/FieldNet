import copy, json, math
import numpy as np
import pytest
from network.examples import demo_case
from network.field_development import DevelopmentScenario, exact_timeline
from network.uncertainty import UncertainParameter, MonteCarloConfig, sample_parameters, apply_sample, percentile_summary, run_monte_carlo
from optimization.development_v17 import DecisionVariable, optimize_development
from ui.uncertainty_v17 import parse_uncertainty_rows, export_mc_csv, export_mc_json

def fake_runner(nodes,edges,start,years,step,events,depletion):
    pi=sum(float(n.get('params',{}).get('pi_m3d_bar',0)) for n in nodes if n.get('kind')=='well')
    timeline=exact_timeline(start,years,step); rows=[]; cum=0
    for i,(d,dt) in enumerate(timeline):
        oil=10*pi; liq=12*pi; cum += liq*dt
        rows.append({'Date':d,'Day':i*step,'Total liquid [m3/d]':liq,'Oil [m3/d]':oil,'Water [m3/d]':liq-oil,'Gas [Sm3/d]':100*oil,'Cumulative liquid [m3]':cum,'Violations':0,'Converged':True,'Message':''})
    return {'field':rows,'wells':[],'constraints':[],'final_state':{}}

def test_sampling_reproducible_and_lhs_bounds():
    p=UncertainParameter('x','params.pi_m3d_bar','triangular',0.8,1.0,1.2,target_id='w1')
    c=MonteCarloConfig(50,123,'lhs',[p]); a=sample_parameters(c); b=sample_parameters(c)
    assert a==b and all(.8 <= x['x'] <= 1.2 for x in a)

def test_correlation_validation_and_sample_shape():
    ps=[UncertainParameter('a','x','normal',mean=1,std=.1,target_id='w1'),UncertainParameter('b','x','normal',mean=1,std=.1,target_id='w1')]
    with pytest.raises(ValueError): MonteCarloConfig(10,1,'lhs',ps,[[1,2],[0,1]]).validate()
    s=sample_parameters(MonteCarloConfig(30,2,'random',ps,[[1,.7],[.7,1]])); assert len(s)==30 and set(s[0])=={'a','b'}

def test_apply_sample_isolated_and_multiplier():
    n,e=demo_case(); original=copy.deepcopy(n); w=next(x for x in n if x['kind']=='well'); base=w['params']['pi_m3d_bar']
    p=UncertainParameter('pi','params.pi_m3d_bar','uniform',.5,1,1.5,target_id=w['id'])
    nn,ee,ss=apply_sample(n,e,DevelopmentScenario(),[p],{'pi':1.1})
    nw=next(x for x in nn if x['id']==w['id']); assert math.isclose(nw['params']['pi_m3d_bar'],base*1.1) and n==original

def test_percentile_petroleum_ordering():
    s=percentile_summary(range(1,101)); assert s['P90'] < s['P50'] < s['P10']

def test_monte_carlo_reproducible_exports_and_success_counts():
    n,e=demo_case(); w=next(x for x in n if x['kind']=='well'); p=UncertainParameter('PI factor','params.pi_m3d_bar','uniform',.8,1,1.2,target_id=w['id'])
    cfg=MonteCarloConfig(12,77,'lhs',[p]); sc=DevelopmentScenario('MC','2026-01-01',.03,10)
    a=run_monte_carlo(n,e,sc,cfg,forecast_runner=fake_runner); b=run_monte_carlo(n,e,sc,cfg,forecast_runner=fake_runner)
    assert a['runs']==b['runs'] and a['successful_samples']==12 and a['metrics']['cumulative_oil_m3']['P90'] <= a['metrics']['cumulative_oil_m3']['P10']
    assert 'cumulative_oil_m3' in export_mc_csv(a) and json.loads(export_mc_json(a))['application']=='FieldNet v29'

def test_ui_parser():
    r=parse_uncertainty_rows([{'name':'x','target_id':'w1','path':'params.pi_m3d_bar','operation':'multiply','distribution':'triangular','low':.8,'mode':1,'high':1.2,'mean':1,'std':.1}]); assert len(r)==1 and r[0].name=='x'

def test_development_optimizer_finds_upper_pi_for_oil():
    n,e=demo_case(); w=next(x for x in n if x['kind']=='well'); base=w['params']['pi_m3d_bar']; d=DecisionVariable('PI',w['id'],'params.pi_m3d_bar',base*.5,base*1.5)
    r=optimize_development(n,e,DevelopmentScenario('Opt','2026-01-01',.03,10),[d],maxiter=3,forecast_runner=fake_runner)
    assert r['objective_value']>0 and r['decisions']['PI'] > base*1.4
