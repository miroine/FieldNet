from ui.topology import validate_topology, auto_layout

def test_topology_clean_chain():
    n=[{'id':'w','kind':'well','name':'W'},{'id':'m','kind':'manifold','name':'M'},{'id':'s','kind':'sink','name':'S'}]
    e=[{'id':'1','source':'w','target':'m','kind':'pipeline'},{'id':'2','source':'m','target':'s','kind':'pipeline'}]
    assert validate_topology(n,e)==[]

def test_topology_missing_endpoint():
    n=[{'id':'w','kind':'well','name':'W'}]; e=[{'id':'1','source':'w','target':'x','kind':'pipeline'}]
    assert any(x['severity']=='error' for x in validate_topology(n,e))

def test_auto_layout_progresses_downstream():
    n=[{'id':'w','kind':'well','name':'W'},{'id':'s','kind':'sink','name':'S'}]; e=[{'id':'1','source':'w','target':'s','kind':'pipeline'}]
    auto_layout(n,e); assert n[1]['x']>n[0]['x']
