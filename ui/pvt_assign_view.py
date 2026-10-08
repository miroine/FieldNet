"""PVT drop-down for every element that needs a PVT (tank, well, gas injector, flowline): pick one of the fluids created on the Fluid & PVT tab."""
from __future__ import annotations
from network import fluids as fl
from network.reservoir_mb import linked_tank_ids
from ui.fluid_library_view import library
from ui.widgets import synced_select, synced_checkbox

NONE = ''


def pvt_dropdown(st, nodes, edges, obj, sid, is_edge=False):
    """Selectbox 'PVT model' on the element panel. Choosing a fluid stamps it on the element (API, gas SG, GOR and its PVT correlation block; a tank also
    gets Boi / Rsi / Pb from it) and, for a tank, optionally on the wells that drain it. Applied only when the choice changes, so edits made afterwards stay."""
    lib = library(st, nodes, edges)
    p = obj.setdefault('params', {}); cur = p.get('fluid_name') or NONE
    if not lib:
        st.caption('PVT: no fluid created yet. Define one on the Fluid & PVT tab (Fluid library) and it appears here.'); return
    opts = [NONE] + list(lib)
    fmt = lambda k: '— none (own values, screening PVT) —' if k == NONE else (f"{k}  ·  {'correlation' if lib[k].get('pvt') else 'screening'}, API {lib[k]['api']:g}, GOR {lib[k]['gor_sm3sm3']:g}")
    sel = synced_select(st, 'PVT model (fluid)', opts, cur if cur in opts else NONE, 'pvtsel' + sid, format_func=fmt)
    also = False
    if obj.get('kind') == 'reservoir':
        also = synced_checkbox(st, 'Also give it to the wells that drain this tank', True, 'pvtal' + sid)
    if sel == (cur if cur in opts else NONE): return
    if sel == NONE:
        p.pop('fluid_name', None); p.pop('pvt', None)
    else:
        ids = [obj['id']] if not is_edge else []
        if obj.get('kind') == 'reservoir' and also:
            ids += [w['id'] for w in nodes if w.get('kind') == 'well' and obj['id'] in linked_tank_ids(w.get('params'))]
        fl.assign(nodes, edges, lib[sel], ids, [obj['id']] if is_edge else [])
        st.session_state.pop('hub_cache', None)
    st.session_state['_applied_note'] = f"PVT '{sel or 'none'}' set on {obj.get('name') or obj['id']}" + (f" and {len(ids) - 1} well(s)" if sel and not is_edge and len(ids) > 1 else '') + '. Press Apply changes to redraw.'
    st.rerun()
