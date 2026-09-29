def injection_summary(nodes, edges, flows):
    """Summarize explicit gas/water injection branches from solved edge flows."""
    byid={n['id']:n for n in nodes}; rows=[]
    for e in edges:
        target=byid.get(e.get('target'),{})
        if target.get('kind') in ('water_injector','gas_injector','injector'):
            rows.append({'Injector':target.get('name',target.get('id')),'Fluid':target.get('params',{}).get('injection_fluid','water'),'Rate':abs(float(flows.get(e['id'],0.0))),'Unit':'m3/d equivalent'})
    return rows
