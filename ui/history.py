import copy, json, uuid

def snapshot(nodes, edges):
    return {'nodes': copy.deepcopy(nodes), 'edges': copy.deepcopy(edges)}

def push(history, nodes, edges, limit=50):
    s=snapshot(nodes,edges)
    if history and json.dumps(history[-1],sort_keys=True)==json.dumps(s,sort_keys=True): return history
    return (history+[s])[-limit:]

def duplicate_node(nodes, edges, node_id):
    n=next(x for x in nodes if x['id']==node_id)
    c=copy.deepcopy(n); c['id']=str(uuid.uuid4())[:8]; c['name']=n['name']+' COPY'; c['x']=float(n.get('x',0))+35; c['y']=float(n.get('y',0))+35
    return nodes+[c], edges, c['id']

def normalize_project(data):
    if not isinstance(data,dict) or not isinstance(data.get('nodes'),list) or not isinstance(data.get('edges'),list): raise ValueError('Project JSON must contain nodes and edges arrays')
    nodes=copy.deepcopy(data['nodes']); edges=copy.deepcopy(data['edges']); ids={n.get('id') for n in nodes}
    if None in ids or len(ids)!=len(nodes): raise ValueError('Node IDs must be present and unique')
    for e in edges:
        if not e.get('id') or e.get('source') not in ids or e.get('target') not in ids: raise ValueError('Connection references an invalid node')
    return nodes,edges
