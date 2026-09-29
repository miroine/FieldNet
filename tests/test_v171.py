import copy, json
import numpy as np
import pytest
from network.examples import demo_case
from network.field_development import DevelopmentScenario, exact_timeline
from network.uncertainty import (UncertainParameter, MonteCarloConfig, sample_parameters, apply_sample,
    percentile_summary, percentile_convergence, sensitivity_summary, failure_diagnostics, run_monte_carlo)
from ui.uncertainty_v17 import export_mc_json

def fake_runner(nodes,edges,start,years,step,events,depletion):
    pi=sum(float(n.get('params',{}).get('pi_m3d_bar',0)) for n in nodes if n.get('kind')=='well')
    rows=[]; cum=0
    for i,(d,dt) in enumerate(exact_timeline(start,years,step)):
        oil=10*pi; liq=12*pi; cum += liq*dt
        rows.append({'Date':d,'Day':i*step,'Total liquid [m3/d]':liq,'Oil [m3/d]':oil,'Water [m3/d]':liq-oil,'Gas [Sm3/d]':100*oil,'Cumulative liquid [m3]':cum,'Violations':0,'Converged':True,'Message':''})
    return {'field':rows,'wells':[],'constraints':[],'final_state':{}}

def test_lhs_uniform_moments_and_strata():
    p=UncertainParameter('x','x','uniform',0,0.5,1,target_id='w1')
    vals=np.array([x['x'] for x in sample_parameters(MonteCarloConfig(1000,7,'lhs',[p]))])
    assert abs(vals.mean()-0.5)<0.01 and abs(vals.std()-1/np.sqrt(12))<0.01
    bins=np.histogram(vals,bins=10,range=(0,1))[0]; assert np.all(bins==100)

def test_physical_bounds_clip_and_reject():
    p=UncertainParameter('x','params.pi_m3d_bar','normal',mean=-2,std=0,physical_min=0,target_id='w1')
    assert sample_parameters(MonteCarloConfig(3,1,'lhs',[p]))[0]['x']==0
    n,e=demo_case(); w=next(x for x in n if x['kind']=='well')
    q=UncertainParameter('x','params.pi_m3d_bar','normal',mean=-2,std=0,physical_min=0,bound_policy='reject',target_id=w['id'],operation='set')
    with pytest.raises(ValueError): apply_sample(n,e,DevelopmentScenario(),[q],{'x':-2})

def test_invalid_correlation_coefficients_and_duplicate_names():
    ps=[UncertainParameter('a','x',target_id='w1'),UncertainParameter('b','x',target_id='w1')]
    with pytest.raises(ValueError): MonteCarloConfig(10,1,'lhs',ps,[[1,1.2],[1.2,1]]).validate()
    with pytest.raises(ValueError): MonteCarloConfig(10,1,'lhs',[ps[0],ps[0]]).validate()

def test_percentile_convergence_final_matches_summary():
    runs=[{'success':True,'m':float(i)} for i in range(1,101)]
    c=percentile_convergence(runs,'m',[10,50,100]); s=percentile_summary(range(1,101))
    assert c[-1]['samples']==100 and c[-1]['P10']==s['P10'] and c[-1]['P90']==s['P90']

def test_sensitivity_ranking_detects_driver():
    runs=[{'success':True,'x':i,'z':(-1)**i,'m':3*i} for i in range(1,50)]
    pars=[UncertainParameter('x','x'),UncertainParameter('z','z')]
    s=sensitivity_summary(runs,pars,'m'); assert s[0]['parameter']=='x' and s[0]['abs_rho']>0.99

def test_failure_diagnostics_warns_on_selection_bias():
    runs=[{'success':i<80,'x':float(i),**({} if i<80 else {'error':'infeasible'})} for i in range(100)]
    d=failure_diagnostics(runs,[UncertainParameter('x','x')]); assert d['failure_fraction']==pytest.approx(.2) and d['survivor_bias_warning'] and d['failure_causes']['infeasible']==20

def test_mc_metadata_and_diagnostics_v171():
    n,e=demo_case(); w=next(x for x in n if x['kind']=='well')
    p=UncertainParameter('PI','params.pi_m3d_bar','uniform',.8,1,1.2,target_id=w['id'],physical_min=0)
    r=run_monte_carlo(n,e,DevelopmentScenario('MC','2026-01-01',.03,10),MonteCarloConfig(20,17,'lhs',[p]),forecast_runner=fake_runner)
    assert r['application']=='FieldNet v29' and r['successful_samples']==20
    assert r['percentile_convergence']['cumulative_oil_m3'] and r['sensitivity']['cumulative_oil_m3']
    assert json.loads(export_mc_json(r))['seed']==17
