"""Constraint priority and hard/soft feasibility helpers for v14."""
def classify_constraints(rows, default_priority=100):
    out=[]
    for r in rows:
        z=dict(r)
        z['ConstraintClass']=str(r.get('ConstraintClass',r.get('class','hard'))).lower()
        z['Priority']=int(r.get('Priority',r.get('priority',default_priority)))
        z['PenaltyWeight']=float(r.get('PenaltyWeight',r.get('penalty_weight',1.0)))
        out.append(z)
    return sorted(out,key=lambda x:(x['ConstraintClass']!='hard',x['Priority'],x.get('Margin',0)))

def constraint_penalty(rows):
    hard=0.0; soft=0.0
    for r in classify_constraints(rows):
        violation=max(-float(r.get('Margin',0)),0.0)
        if r['ConstraintClass']=='hard': hard += violation*violation*1e6
        else: soft += violation*violation*r['PenaltyWeight']
    return hard+soft

def feasibility(rows):
    c=classify_constraints(rows)
    hard=[r for r in c if r['ConstraintClass']=='hard' and r.get('Status')=='VIOLATED']
    soft=[r for r in c if r['ConstraintClass']=='soft' and r.get('Status')=='VIOLATED']
    return {'feasible':not hard,'hard_violations':len(hard),'soft_violations':len(soft),'ordered':c}
