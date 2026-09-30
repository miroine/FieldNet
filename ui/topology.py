from collections import defaultdict, deque

# Must match every component the palette can create; control_valve was missing, so every
# valve was reported as a topology *error*.
VALID_KINDS={'well','manifold','separator','separator_stage','sink','reservoir','water_source','gas_source','water_injector','gas_injector','injector','oil_export','gas_export','water_disposal'}
VALID_EDGE_KINDS={'pipeline','choke','control_valve','pump','compressor'}
BOUNDARY_KINDS={'sink','separator','separator_stage','oil_export','gas_export','water_disposal'}

def validate_topology(nodes, edges):
    issues=[]; ids={n['id'] for n in nodes}; incoming=defaultdict(int); outgoing=defaultdict(int)
    if len(ids)!=len(nodes): issues.append({'severity':'error','message':'Duplicate node IDs detected'})
    for e in edges:
        if e.get('source') not in ids or e.get('target') not in ids: issues.append({'severity':'error','message':f"Edge {e.get('id')} has missing endpoint"})
        if e.get('source')==e.get('target'): issues.append({'severity':'error','message':f"Edge {e.get('id')} is a self-loop"})
        if e.get('kind','pipeline') not in VALID_EDGE_KINDS: issues.append({'severity':'error','message':f"Edge {e.get('id')} has unsupported type"})
        incoming[e.get('target')]+=1; outgoing[e.get('source')]+=1
    for n in nodes:
        if n.get('kind') not in VALID_KINDS: issues.append({'severity':'warning','message':f"{n.get('name')} has unknown component type"})
        if n.get('kind')=='well' and incoming[n['id']]: issues.append({'severity':'warning','message':f"Well {n['name']} has an incoming connection"})
        if n.get('kind') in BOUNDARY_KINDS and outgoing[n['id']] and n.get('pressure_bar') is not None: issues.append({'severity':'warning','message':f"Fixed-pressure boundary {n['name']} has an outgoing connection"})
        if incoming[n['id']]+outgoing[n['id']]==0: issues.append({'severity':'warning','message':f"{n['name']} is disconnected"})
    return issues

def auto_layout(nodes, edges, xgap=260, ygap=120):
    indeg={n['id']:0 for n in nodes}; adj=defaultdict(list)
    for e in edges:
        if e['source'] in indeg and e['target'] in indeg: indeg[e['target']]+=1; adj[e['source']].append(e['target'])
    q=deque([k for k,v in indeg.items() if v==0]); level={k:0 for k in q}
    while q:
        u=q.popleft()
        for v in adj[u]:
            indeg[v]-=1; level[v]=max(level.get(v,0),level[u]+1)
            if indeg[v]==0:q.append(v)
    groups=defaultdict(list)
    for n in nodes: groups[level.get(n['id'],0)].append(n)
    for lv,grp in groups.items():
        for i,n in enumerate(grp): n['x']=40+lv*xgap; n['y']=50+i*ygap
    return nodes
