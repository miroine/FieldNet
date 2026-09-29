import json
from pathlib import Path
from ui.history import normalize_project, duplicate_node, push

def sample():
    n=[{'id':'a','kind':'well','name':'A','x':1,'y':2,'params':{}},{'id':'b','kind':'sink','name':'B','x':3,'y':4,'params':{}}]
    e=[{'id':'e','source':'a','target':'b','kind':'pipeline'}]
    return n,e

def test_project_roundtrip_validation():
    n,e=sample(); nn,ee=normalize_project({'version':'7.0','nodes':n,'edges':e}); assert nn==n and ee==e

def test_invalid_project_endpoint_rejected():
    n,e=sample(); e[0]['target']='missing'
    try: normalize_project({'nodes':n,'edges':e})
    except ValueError: pass
    else: raise AssertionError('invalid endpoint accepted')

def test_duplicate_node_is_independent():
    n,e=sample(); nn,ee,newid=duplicate_node(n,e,'a'); assert len(nn)==3 and newid!='a' and nn[-1]['name'].endswith('COPY') and ee==e

def test_history_deduplicates():
    n,e=sample(); h=push([],n,e); assert len(push(h,n,e))==1

def test_custom_component_is_bidirectional():
    text=(Path(__file__).parents[1]/'ui/fieldnet_canvas/build/index.html').read_text()
    assert 'streamlit:setComponentValue' in text
    assert 'streamlit:componentReady' in text
    assert 'click target IN port' in text
