import copy, json
from network.examples import demo_case
from network.field_development import DevelopmentEvent, DevelopmentScenario, exact_timeline, run_development_scenario, run_development_scenarios
from ui.field_development import parse_event_rows, export_field_csv, export_summary_json, results_frames


def _fake_runner(nodes, edges, start, years, step, events, depletion):
    # deterministic two-row runner for fast orchestration tests
    return {"field":[
        {"Date":start,"Day":0,"Total liquid [m3/d]":100.0,"Oil [m3/d]":80.0,"Water [m3/d]":20.0,"Gas [Sm3/d]":8000.0,"Cumulative liquid [m3]":0.0,"Violations":0,"Converged":True,"Message":""},
        {"Date":"2026-01-11","Day":10,"Total liquid [m3/d]":90.0,"Oil [m3/d]":70.0,"Water [m3/d]":20.0,"Gas [Sm3/d]":7000.0,"Cumulative liquid [m3]":950.0,"Violations":1,"Converged":True,"Message":""},
    ],"wells":[],"constraints":[{"Date":"2026-01-11","name":"liquid_capacity"}],"final_state":{}}


def test_exact_timeline_clips_final_interval():
    t=exact_timeline("2026-01-01",0.1,30)
    assert sum(x[1] for x in t)==round(0.1*365.25)
    assert t[-1][1] <= 30


def test_scenario_enrichment_and_kpis():
    n,e=demo_case(); s=DevelopmentScenario("Base","2026-01-01",0.1,10)
    r=run_development_scenario(n,e,s,forecast_runner=_fake_runner)
    assert r["forecast"]["field"][1]["Cumulative oil [m3]"]>0
    assert r["kpis"]["constraint_events"]==1
    assert r["kpis"]["convergence_fraction"]==1.0


def test_scenarios_do_not_mutate_base_case():
    n,e=demo_case(); original=copy.deepcopy(n)
    ss=[DevelopmentScenario("A","2026-01-01",0.1,10,events=[DevelopmentEvent("2026-01-02","w1","params.available",False)]),DevelopmentScenario("B","2026-01-01",0.1,10)]
    out=run_development_scenarios(n,e,ss,forecast_runner=_fake_runner)
    assert len(out)==2 and n==original


def test_ui_event_parser_and_exports():
    events=parse_event_rows([{"date":"2026-02-01","target_id":"w1","field":"params.available","value":"false","description":"workover"},{"date":"","target_id":"","field":"","value":""}])
    assert len(events)==1 and events[0].as_forecast_event()["value"] is False
    n,e=demo_case(); result=run_development_scenario(n,e,DevelopmentScenario("Base","2026-01-01",0.1,10),forecast_runner=_fake_runner)
    csv=export_field_csv([result]); js=export_summary_json([result]); payload=json.loads(js)
    assert "Scenario" in csv and payload["application"]=="FieldNet v29"
    assert len(results_frames([result])[2])==1


def test_real_forecast_smoke_short_horizon():
    n,e=demo_case(); s=DevelopmentScenario("Base","2026-01-01",0.03,10)
    r=run_development_scenario(n,e,s)
    assert r["forecast"]["field"] and r["kpis"]["timesteps"]>=2
