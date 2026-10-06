from pathlib import Path
import streamlit.components.v1 as components
from ui.graph_contract import GRAPH_SCHEMA

_BUILD = Path(__file__).parent / 'fieldnet_canvas' / 'build'
_component = components.declare_component('fieldnet_editor', path=str(_BUILD))

def network_editor(nodes, edges, results=None, height=860, key='fieldnet-editor', status='UNSOLVED', status_message='', selected=None, palette=None, labels=None, edge_labels=None, edge_widths=None):
    """Render the graph editor.

    Pan, zoom, Fit and Reset are handled entirely in the browser and never send a value
    (no Streamlit rerun). Only graph edits and selection changes are sent back, as a
    ``fieldnet.graph/1`` payload with a unique ``rev``.
    """
    p,q,_,_ = results if results else ({},{},{},{})
    return _component(schema=GRAPH_SCHEMA, nodes=nodes, edges=edges, pressures=p, rates=q, status=status,
                      status_message=status_message, selected=selected, palette=palette or [], labels=labels or {}, edge_labels=edge_labels or {}, edge_widths=edge_widths or {}, height=height, key=key, default=None)
