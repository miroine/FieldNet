import copy
import pytest
from network.examples import demo_case
from network.development_v26 import DevelopmentTask, DevelopmentPlan, compile_plan, run_development_plan

def plan(tasks): return DevelopmentPlan('P','2026-01-01',0.1,10,tasks)

def test_dependency_and_rig_serialization():
 n,e=demo_case(); p=plan([DevelopmentTask('A','Drill 1','drill_well','w1','2026-01-01',10,resource='R1'),DevelopmentTask('B','Drill 2','drill_well','w2','2026-01-01',5,resource='R1'),DevelopmentTask('C','Tieback','tieback','w2','2026-01-01',3,('B',))])
 s=compile_plan(p,n,e)['schedule']; x={r['task_id']:r for r in s}; assert x['B']['start']>=x['A']['finish'] and x['C']['start']>=x['B']['finish']

def test_parallel_resources_can_overlap():
 n,e=demo_case(); p=plan([DevelopmentTask('A','A','drill_well','w1','2026-01-01',10,resource='R1'),DevelopmentTask('B','B','drill_well','w2','2026-01-01',10,resource='R2')]); x={r['task_id']:r for r in compile_plan(p,n,e)['schedule']}; assert x['A']['start']==x['B']['start']

def test_cycle_and_unknown_target_rejected():
 n,e=demo_case()
 with pytest.raises(ValueError): compile_plan(plan([DevelopmentTask('A','A','commission','bad','2026-01-01')]),n,e)
 with pytest.raises(ValueError): compile_plan(plan([DevelopmentTask('A','A','commission','w1','2026-01-01',predecessors=('B',)),DevelopmentTask('B','B','commission','w2','2026-01-01',predecessors=('A',))]),n,e)

def test_drilling_compiles_availability_events():
 n,e=demo_case(); c=compile_plan(plan([DevelopmentTask('A','Drill','drill_well','w1','2026-01-01',10)]),n,e); ev=[x.as_forecast_event() for x in c['events']]; assert ev[0]['value'] is False and ev[-1]['value'] is True and ev[-1]['date']=='2026-01-11'

def test_facility_expansion_compiles_explicit_value():
 n,e=demo_case(); c=compile_plan(plan([DevelopmentTask('A','Expand','facility_expansion','s1','2026-01-01',20,value=5000)]),n,e); ev=c['events'][0].as_forecast_event(); assert ev['field']=='params.max_rate_m3d' and ev['value']==5000

def test_run_isolation_and_exact_horizon():
 n,e=demo_case(); n0=copy.deepcopy(n); r=run_development_plan(n,e,plan([DevelopmentTask('A','Drill','drill_well','w1','2026-01-01',5)])); assert n==n0 and r['application']=='FieldNet v29.1' and r['forecast']['field']
