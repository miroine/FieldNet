def evaluate_constraints(nodes, edges, pressures, flows, details):
    rows=[]
    def add(component, constraint, value, limit, relation, unit, cid=None):
        margin=(limit-value) if relation=='<=' else (value-limit)
        rows.append({'Component':component,'ComponentId':cid,'Constraint':constraint,'Value':float(value),'Limit':float(limit),'Relation':relation,'Margin':float(margin),'Unit':unit,'Status':'OK' if margin>=-1e-9 else 'VIOLATED'})
    byid={n['id']:n for n in nodes}
    for n in nodes:
        prm=n.get('params',{}) or {}; p=pressures.get(n['id'])
        if p is not None:
            if prm.get('max_pressure_bar') is not None: add(n.get('name',n['id']),'Maximum pressure',p,float(prm['max_pressure_bar']),'<=','bar',n['id'])
            if prm.get('min_pressure_bar') is not None: add(n.get('name',n['id']),'Minimum pressure',p,float(prm['min_pressure_bar']),'>=','bar',n['id'])
        if n['kind']=='well' and n['id'] in details:
            d=details[n['id']]; q=d['liquid_rate_m3d']
            if prm.get('max_liquid_rate_m3d') is not None: add(n.get('name',n['id']),'Maximum liquid rate',q,float(prm['max_liquid_rate_m3d']),'<=','m3/d',n['id'])
            if prm.get('min_bhp_bar') is not None: add(n.get('name',n['id']),'Minimum BHP',d['bhp_bar'],float(prm['min_bhp_bar']),'>=','bar',n['id'])
        if n['kind'] in ('separator','sink','separator_stage','water_disposal','gas_export','oil_export'):
            incoming=sum(max(flows.get(e['id'],0),0) for e in edges if e['target']==n['id'])
            if prm.get('max_liquid_rate_m3d') is not None: add(n.get('name',n['id']),'Liquid capacity',incoming,float(prm['max_liquid_rate_m3d']),'<=','m3/d',n['id'])
    for e in edges:
        prm=e.get('params',{}) or {}; q=abs(flows.get(e['id'],0)); name=e.get('name',e['id'])
        if prm.get('max_rate_m3d') is not None: add(name,'Maximum rate',q,float(prm['max_rate_m3d']),'<=','m3/d',e.get('id'))
        if e.get('kind')=='compressor' and prm.get('max_power_kw') is not None:
            # power is evaluated in steady_state equipment table; this row reserves the declared operating envelope
            pass
    return rows

def active_constraints(rows, tolerance_fraction=0.05):
    """Return violated or near-active constraints, most limiting first."""
    out=[]
    for r in rows:
        limit=abs(float(r.get('Limit',0.0)))
        margin=float(r.get('Margin',0.0))
        frac=margin/max(limit,1e-12)
        if r.get('Status')=='VIOLATED' or frac <= float(tolerance_fraction):
            z=dict(r); z['MarginFraction']=frac; out.append(z)
    return sorted(out,key=lambda x:x['MarginFraction'])
