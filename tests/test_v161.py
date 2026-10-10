import copy, json, math
import pytest
from network.examples import demo_case
from network.field_development import DevelopmentEvent, DevelopmentScenario, exact_timeline, run_development_scenario, run_development_scenarios
from ui.field_development import export_field_csv, export_summary_json, results_frames
from solver.audit_report import calculation_audit
from solver.professional import solve_professional


def _timeline_runner(nodes, edges, start, years, step, events, depletion):
    timeline = exact_timeline(start, years, step)
    rows=[]
    for i,(d,dt) in enumerate(timeline):
        rows.append({'Date':d,'Day':i*step,'Total liquid [m3/d]':100.0,'Oil [m3/d]':80.0,'Water [m3/d]':20.0,'Gas [Sm3/d]':8000.0,'Cumulative liquid [m3]':100.0*sum(x[1] for x in timeline[:i+1]),'Violations':0,'Converged':True,'Message':''})
    return {'field':rows,'wells':[],'constraints':[],'final_state':{}}


def test_v161_exact_horizon_and_cumulative_closure():
    n,e=demo_case(); years=0.1; s=DevelopmentScenario('Audit','2026-01-01',years,30)
    r=run_development_scenario(n,e,s,forecast_runner=_timeline_runner)
    horizon=round(years*365.25)
    assert sum(x['Interval days'] for x in r['forecast']['field']) == horizon
    assert math.isclose(r['kpis']['cumulative_oil_m3'],80.0*horizon,rel_tol=0,abs_tol=1e-9)
    assert math.isclose(r['kpis']['cumulative_water_m3'],20.0*horizon,rel_tol=0,abs_tol=1e-9)
    assert math.isclose(r['kpis']['cumulative_gas_sm3'],8000.0*horizon,rel_tol=0,abs_tol=1e-9)


def test_v161_unknown_event_target_fails_fast():
    n,e=demo_case(); s=DevelopmentScenario('Bad','2026-01-01',0.1,30,events=[DevelopmentEvent('2026-01-02','TYPO_WELL','params.available',False)])
    with pytest.raises(ValueError, match='Unknown development event target_id'):
        run_development_scenario(n,e,s,forecast_runner=_timeline_runner)


def test_v161_valid_event_target_and_scenario_isolation():
    n,e=demo_case(); original=copy.deepcopy(n); target=n[0]['id']
    ss=[DevelopmentScenario('A','2026-01-01',0.1,30,events=[DevelopmentEvent('2026-01-02',target,'params.available',False)]),DevelopmentScenario('B','2026-01-01',0.1,30)]
    out=run_development_scenarios(n,e,ss,forecast_runner=_timeline_runner)
    assert [x['name'] for x in out]==['A','B'] and n==original


def test_v161_export_consistency_and_identity():
    n,e=demo_case(); r=run_development_scenario(n,e,DevelopmentScenario('Base','2026-01-01',0.1,30),forecast_runner=_timeline_runner)
    field, constraints, summary=results_frames([r])
    payload=json.loads(export_summary_json([r])); csv=export_field_csv([r])
    assert payload['application']=='FieldNet v29.1'
    assert payload['scenarios'][0]['kpis']['cumulative_oil_m3']==summary.iloc[0]['Cumulative oil [m3]']
    assert len(csv.strip().splitlines())==len(field)+1 and constraints.empty


def test_v161_audit_identity_and_physical_gate_present():
    n,e=demo_case(); p,q,info,details=solve_professional(n,e); a=calculation_audit(n,e,info,details)
    assert a['application']=='FieldNet v32.6' and a['license_note']=='MIT License'
    assert 'physical_quality_gate' in a and a['author']=='Merouane Hamdani'
