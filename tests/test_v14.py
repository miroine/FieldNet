from network.examples import demo_case
from solver.professional import solve_professional
from solver.control_hierarchy import feasibility,constraint_penalty
from optimization.multiscenario import run_cases
from solver.audit_report import calculation_audit

def test_professional_solver_quality_gate():
    n,e=demo_case(); p,q,i,d=solve_professional(n,e,continuation_steps=2)
    assert i['success'] and i['quality_gate']=='PASS' and len(i['continuation_history'])==2

def test_hard_soft_constraint_hierarchy():
    rows=[{'Margin':-1,'Status':'VIOLATED','ConstraintClass':'soft','Priority':20},{'Margin':2,'Status':'OK','ConstraintClass':'hard','Priority':1}]
    f=feasibility(rows); assert f['feasible'] and f['soft_violations']==1 and constraint_penalty(rows)>0

def test_hard_violation_is_infeasible():
    f=feasibility([{'Margin':-0.1,'Status':'VIOLATED','ConstraintClass':'hard'}]); assert not f['feasible']

def test_multiscenario_runs():
    n,e=demo_case(); sink=next(x for x in n if x.get('pressure_bar') is not None and x['kind']!='well')
    r=run_cases(n,e,[{'name':'Base','changes':[]},{'name':'Changed','changes':[{'target_type':'node','target_id':sink['id'],'field':'pressure_bar','value':float(sink['pressure_bar'])+1}]}])
    assert len(r)==2 and all(x['Quality'] in ('PASS','FAIL') for x in r)

def test_audit_report_identity():
    n,e=demo_case(); p,q,i,d=solve_professional(n,e); a=calculation_audit(n,e,i,d)
    assert a['author']=='Merouane Hamdani' and a['application'].startswith('FieldNet v')
