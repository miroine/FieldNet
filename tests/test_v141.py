from copy import deepcopy
from network.examples import demo_case
from solver.professional import solve_professional
from solver.control_hierarchy import feasibility
from optimization.multiscenario import run_cases
from optimization.debottleneck import debottleneck_screen
from solver.audit_report import calculation_audit

def test_independent_physical_residual_audit_passes_demo():
    n,e=demo_case(); p,q,i,d=solve_professional(n,e)
    a=i['physical_residual_audit']; assert i['physical_quality_gate']=='PASS'; assert a['max_pressure_residual_bar'] <= 1e-3; assert a['max_mass_residual_m3d'] <= 0.1

def test_constraint_semantics_hard_and_soft():
    rows=[{'Margin':-1,'Status':'VIOLATED','ConstraintClass':'soft'},{'Margin':1,'Status':'OK','ConstraintClass':'hard'}]
    f=feasibility(rows); assert f['feasible'] and f['soft_violations']==1 and f['hard_violations']==0

def test_scenario_execution_does_not_mutate_base():
    n,e=demo_case(); n0=deepcopy(n); e0=deepcopy(e); target=next(x for x in n if x.get('pressure_bar') is not None and x['kind']!='well')
    run_cases(n,e,[{'name':'Changed','changes':[{'target_type':'node','target_id':target['id'],'field':'pressure_bar','value':float(target['pressure_bar'])+2}]}])
    assert n==n0 and e==e0

def test_debottleneck_screen_is_descending_when_rows_exist():
    n,e=demo_case(); rows=debottleneck_screen(n,e)
    gains=[r['ProductionGain_m3d'] for r in rows]; assert gains==sorted(gains,reverse=True)

def test_v141_audit_report_contains_independent_gate():
    n,e=demo_case(); p,q,i,d=solve_professional(n,e); a=calculation_audit(n,e,i,d)
    assert a['application'].startswith('FieldNet v') and a['physical_quality_gate']=='PASS' and 'physical_residual_audit' in a
