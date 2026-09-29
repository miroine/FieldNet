from pathlib import Path
import streamlit.components.v1 as components

_BUILD = Path(__file__).parent / 'fieldnet_canvas' / 'build'
_component = components.declare_component('fieldnet_canvas_v7', path=str(_BUILD))

def network_editor(nodes, edges, results=None, height=650, key='fieldnet-editor'):
    p,q,_,_ = results if results else ({},{},{},{})
    return _component(nodes=nodes, edges=edges, pressures=p, rates=q, height=height, key=key, default=None)
