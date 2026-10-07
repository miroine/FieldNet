"""Export the network as a standalone SVG drawing (server side, works with st.download_button)."""
from __future__ import annotations
from xml.sax.saxutils import escape

COLORS = {'well': '#29b6f6', 'manifold': '#9fa8da', 'joint': '#cfd8dc', 'separator': '#66bb6a', 'separator_stage': '#66bb6a', 'sink': '#ef5350', 'oil_export': '#ef5350',
          'gas_export': '#ef5350', 'water_disposal': '#ef5350', 'water_source': '#4dd0e1', 'gas_source': '#ffb74d', 'water_injector': '#4dd0e1', 'gas_injector': '#ffb74d',
          'reservoir': '#8d6e63', 'choke': '#ba68c8', 'control_valve': '#ba68c8', 'pump': '#4db6ac', 'compressor': '#ffb74d'}
W, H, JR = 154, 64, 13


def _size(n):
    from ui.shapes import node_scale
    s = node_scale(n)
    if n.get('kind') == 'joint': return 2 * JR * s, 2 * JR * s
    if n.get('kind') in ('choke', 'control_valve', 'pump', 'compressor'): return 124 * s, 52 * s
    return W * s, H * s


def network_svg(nodes, edges, labels=None, rates=None, title='FieldNet network', edge_labels=None, widths=None):
    edge_labels = edge_labels or {}; labels = labels or {}; rates = rates or {}; byid = {n['id']: n for n in nodes}
    if not nodes: return '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="60"><text x="10" y="30">Empty network</text></svg>'
    def port(nid, out):
        n = byid[nid]; w, h = _size(n); return float(n.get('x') or 0) + (w if out else 0), float(n.get('y') or 0) + h / 2
    def curve(a, b):
        dx = max(45, abs(b[0] - a[0]) / 2); return f'M{a[0]:.1f},{a[1]:.1f} C{a[0]+dx:.1f},{a[1]:.1f} {b[0]-dx:.1f},{b[1]:.1f} {b[0]:.1f},{b[1]:.1f}'
    xs0 = min(float(n.get('x') or 0) for n in nodes) - 30; ys0 = min(float(n.get('y') or 0) for n in nodes) - 40
    xs1 = max(float(n.get('x') or 0) + _size(n)[0] + 110 for n in nodes) + 30; ys1 = max(float(n.get('y') or 0) + _size(n)[1] for n in nodes) + 30
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{xs0:.0f} {ys0:.0f} {xs1-xs0:.0f} {ys1-ys0:.0f}" width="{xs1-xs0:.0f}" height="{ys1-ys0:.0f}" font-family="Segoe UI,Arial,sans-serif">',
         f'<title>{escape(title)}</title><rect x="{xs0:.0f}" y="{ys0:.0f}" width="{xs1-xs0:.0f}" height="{ys1-ys0:.0f}" fill="#fff"/>',
         '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="14" markerHeight="14" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#456"/></marker></defs>']
    for n in nodes:
        rid = (n.get('params') or {}).get('reservoir_id')
        if rid in byid: o.append(f'<path d="{curve(port(rid, True), port(n["id"], False))}" fill="none" stroke="#a1887f" stroke-width="2" stroke-dasharray="3 5"/>')
        for c in (n.get('params') or {}).get('communication') or []:
            if c.get('to') in byid: o.append(f'<path d="{curve(port(n["id"], True), port(c["to"], False))}" fill="none" stroke="#e08a00" stroke-width="2" stroke-dasharray="9 4"/>')
    for e in edges:
        if e.get('source') not in byid or e.get('target') not in byid: continue
        a, b = port(e['source'], True), port(e['target'], False); q = rates.get(e['id'])
        wd = (widths or {}).get(e['id'], 2); o.append(f'<path d="{curve(a, b)}" fill="none" stroke="#456" stroke-width="{wd:.1f}" stroke-opacity="{0.45 if widths and wd <= 1.0 else 1}" marker-end="url(#a)"/>')
        txt = edge_labels.get(e['id']) or (e.get('kind', 'pipeline') + (f' • {q:,.0f} m³/d' if q is not None else ''))
        o.append(f'<text x="{(a[0]+b[0])/2:.1f}" y="{(a[1]+b[1])/2-6:.1f}" font-size="10" text-anchor="middle" fill="#234">{escape(txt)}</text>')
    for n in nodes:
        x, y = float(n.get('x') or 0), float(n.get('y') or 0); w, h = _size(n); k = n.get('kind', ''); col = COLORS.get(k, '#9aa5b1'); nm = escape(str(n.get('name', n['id'])))
        if k == 'joint':
            o.append(f'<circle cx="{x+JR:.1f}" cy="{y+JR:.1f}" r="{JR-1}" fill="#eceff1" stroke="#455a64" stroke-width="1.5"/><text x="{x+2*JR+4:.1f}" y="{y+JR+4:.1f}" font-size="10" fill="#234">{nm}</text>'); continue
        from ui.shapes import shape as _shape
        sh = _shape(k); pad = 22 if sh and k not in ('manifold', 'reservoir', 'compressor') else 12
        if sh:
            tr = f'transform="translate({x:.1f},{y:.1f}) scale({w/100:.4f},{h/100:.4f})"'; dash = ' stroke-dasharray="6 4"' if sh[2] else ''
            from ui.shapes import node_phase, water_fraction, PHASE_COLOR
            ph = node_phase(n, nodes); fill = '#f7f9fb'; gd = ''
            if ph:
                wf = water_fraction(n); fill = PHASE_COLOR[ph]
                if wf > 0.01 and ph != 'water':
                    gid = 'pf' + ''.join(ch for ch in str(n['id']) if ch.isalnum())
                    gd = (f'<defs><linearGradient id="{gid}" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="{PHASE_COLOR["water"]}"/><stop offset="{wf:.3f}" stop-color="{PHASE_COLOR["water"]}"/>'
                          f'<stop offset="{wf:.3f}" stop-color="{fill}"/><stop offset="1" stop-color="{fill}"/></linearGradient></defs>'); fill = f'url(#{gid})'
            body = (gd + f'<path d="{sh[0]}" {tr} fill="{fill}" fill-opacity="{0.9 if ph else 1}" stroke="{col}" stroke-width="2" vector-effect="non-scaling-stroke"{dash}/>'
                    + (f'<path d="{sh[1]}" {tr} fill="none" stroke="{col}" stroke-width="1.2" vector-effect="non-scaling-stroke"/>' if sh[1] else ''))
        else:
            body = f'<rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}" rx="9" fill="#f7f9fb" stroke="#455a64" stroke-width="1.3"/><rect x="{x:.1f}" y="{y:.1f}" width="5" height="{h}" rx="2" fill="{col}"/>'
        from ui.shapes import node_phase as _np
        txtc = '#fff' if (sh and _np(n, nodes)) else '#123'
        o.append(body +
                 f'<text x="{x+pad:.1f}" y="{y+16:.1f}" font-size="8" fill="#667">{escape(k.replace("_", " ").upper())}</text><text x="{x+pad:.1f}" y="{y+33:.1f}" font-size="12" font-weight="600" fill="{txtc}">{nm}</text>'
                 f'<text x="{x+pad:.1f}" y="{y+h-8:.1f}" font-size="10" fill="#046">{escape(str(labels.get(n["id"], "")))}</text>'
                 f'<circle cx="{x:.1f}" cy="{y+h/2:.1f}" r="5" fill="#fff" stroke="#456"/><circle cx="{x+w:.1f}" cy="{y+h/2:.1f}" r="5" fill="#fff" stroke="#456"/>')
    o.append('</svg>'); return ''.join(o)
