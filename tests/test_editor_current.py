from pathlib import Path
from network.examples import demo_case
from solver.v21 import solve_v21

ROOT=Path(__file__).resolve().parents[1]

def test_current_editor_not_legacy_v7_and_has_full_palette_zoom():
    html=(ROOT/'ui/fieldnet_canvas/build/index.html').read_text()
    bridge=(ROOT/'ui/editor_component.py').read_text()
    assert 'FieldNet v7 Editor' not in html
    assert "fieldnet_canvas_v7" not in bridge
    for token in ['Reservoir tank','Water injector','Gas injector','Separator stage','Zoom']:
        assert token.lower() in html.lower()
    for control in ['id="zin"','id="zout"','id="fit"','id="reset"']:
        assert control in html

def test_demo_solve_produces_explicit_solved_state_contract():
    nodes,edges=demo_case(); p,q,info,_=solve_v21(nodes,edges,attempts=3)
    assert info['success'] and info['quality_gate']=='PASS'
    assert p and q

def test_app_wires_explicit_solver_state_and_large_canvas():
    # v30.1+: solve state lives in the single editor->solver contract (ui/graph_contract.py)
    app=(ROOT/'app.py').read_text(); contract=(ROOT/'ui/graph_contract.py').read_text()
    assert 'solve_status(st.session_state)' in app and 'run_solve(st.session_state' in app
    assert "SOLVED if ok else FAILED" in contract
    assert 'height=_edh' in app and "'ed_h'" in app   # editor height is now user-adjustable (default 1050)
    assert 'The editor above has already been sent with the SOLVING badge' in app
    assert "canvas_tanks=[n for n in st.session_state.nodes if n.get('kind')=='reservoir']" in app

def test_reservoir_node_is_valid_solver_pressure_boundary():
    nodes=[
      {'id':'r','kind':'reservoir','name':'TANK-1','pressure_bar':None,'x':0,'y':0,'params':{'reservoir_pressure_bar':100}},
      {'id':'s','kind':'sink','name':'SINK','pressure_bar':90,'x':0,'y':0,'params':{}},
    ]
    edges=[{'id':'e','source':'r','target':'s','kind':'pipeline','length_m':100,'diameter_m':.2,'roughness_m':4.5e-5,'elevation_change_m':0,'params':{'temperature_c':50,'water_cut':1.0,'gor_sm3sm3':0,'initial_rate_m3d':100,'correlation':'Homogeneous'}}]
    p,q,info,_=solve_v21(nodes,edges)
    assert info['success']
    assert abs(p['r']-100)<1e-12 and p['s']==90
    assert 'e' in q
