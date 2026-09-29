from network.examples import demo_case
from network.forecast import run_forecast, apply_events

def test_forecast_generates_profile_and_cumulative():
    n,e=demo_case(); r=run_forecast(n,e,'2026-01-01',years=.25,step_days=30)
    assert len(r['field'])>=4
    assert r['field'][-1]['Cumulative liquid [m3]']>0
    assert len(r['wells'])>0

def test_depletion_reduces_reservoir_pressure():
    n,e=demo_case(); initial=n[0]['params']['reservoir_pressure_bar']
    r=run_forecast(n,e,'2026-01-01',years=.25,step_days=30,depletion={'w1':{'pressure_decline_bar_per_1000m3':.1}})
    assert r['final_state']['w1']['pr'] < initial

def test_schedule_event_changes_case():
    n,e=demo_case(); ev=[{'date':'2026-02-01','target_id':'w1','field':'params.available','value':False}]
    nn,_=apply_events(n,e,ev,'2026-03-01')
    assert next(x for x in nn if x['id']=='w1')['params']['available'] is False
