"""Tabulated equipment performance maps with transparent interpolation."""
import numpy as np

def _clean(points, xkey):
    pts=sorted([{k:float(v) for k,v in p.items()} for p in points], key=lambda p:p[xkey])
    if len(pts)<2: raise ValueError('At least two map points are required')
    xs=[p[xkey] for p in pts]
    if len(set(xs)) != len(xs): raise ValueError('Map abscissa values must be unique')
    return pts

def pump_map(rate_m3d, points):
    pts=_clean(points,'rate_m3d'); q=float(rate_m3d); xs=np.array([p['rate_m3d'] for p in pts]);
    head=np.interp(q,xs,[p['head_bar'] for p in pts]); eff=np.interp(q,xs,[p.get('efficiency',0.75) for p in pts])
    return {'head_bar':float(head),'efficiency':float(eff),'in_envelope':bool(xs[0] <= q <= xs[-1]),'min_rate_m3d':float(xs[0]),'max_rate_m3d':float(xs[-1])}

def compressor_map(rate_sm3d, points):
    pts=_clean(points,'rate_sm3d'); q=float(rate_sm3d); xs=np.array([p['rate_sm3d'] for p in pts]);
    ratio=np.interp(q,xs,[p['pressure_ratio'] for p in pts]); eff=np.interp(q,xs,[p.get('efficiency',0.75) for p in pts])
    return {'pressure_ratio':float(ratio),'efficiency':float(eff),'in_envelope':bool(xs[0] <= q <= xs[-1]),'min_rate_sm3d':float(xs[0]),'max_rate_sm3d':float(xs[-1])}
