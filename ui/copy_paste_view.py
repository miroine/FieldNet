"""Copy / paste settings between wells and between tanks (property panel)."""
from __future__ import annotations
from network import copy_paste as cp


def copy_paste_panel(st, nodes, n, sid):
    kind = n.get('kind')
    if kind not in ('well', 'reservoir'): return
    word = 'well' if kind == 'well' else 'tank'
    others = [x for x in nodes if x.get('kind') == kind and x['id'] != n['id']]
    if not others: return
    names = {x['id']: x.get('name') or x['id'] for x in others}
    with st.expander(f'Copy / paste settings between {word}s'):
        mode = st.radio('Direction', [f'Paste into this {word} from another', f'Copy this {word} to other {word}s'], key='cpm' + sid, horizontal=True)
        groups = st.multiselect('What to copy', cp.groups_for(kind), default=cp.groups_for(kind)[:-1], key='cpg' + sid,
                                help='Name, position, links to tanks, communication and the mask are never copied.')
        into_this = mode.startswith('Paste')
        if into_this:
            src_id = st.selectbox(f'Copy from', list(names), format_func=lambda k: names[k], key='cps' + sid)
            src = next(x for x in nodes if x['id'] == src_id); targets = [n]
        else:
            tg = st.multiselect(f'Paste into', list(names), format_func=lambda k: names[k], key='cpt' + sid)
            src = n; targets = [x for x in nodes if x['id'] in tg]
        pv = cp.preview(src, groups)
        st.caption(f"{sum(len(v) for v in pv.values())} value(s) in {len(pv)} group(s) from {src.get('name') or src['id']}" + (': ' + ', '.join(f"{g} ({len(k)})" for g, k in pv.items()) if pv else ''))
        if st.button('Paste' if into_this else f'Copy to {len(targets)} {word}(s)', key='cpb' + sid, disabled=not (targets and groups and pv), type='primary'):
            total = sum(cp.copy_params(src, t, groups) for t in targets)
            st.session_state['_applied_note'] = f'Copied {total} value(s) from {src.get("name") or src["id"]} to {len(targets)} {word}(s). Press Apply changes to redraw.'
            st.rerun()
