"""Component outlines (GAP-style symbols) drawn on the network canvas and in the SVG export.

Every outline is a path in a 0..100 x 0..100 box that is stretched to the card; the in/out ports sit on the left/right tips at
mid-height, which every outline touches. ``DETAIL`` adds inner lines (tank rim, separator end caps...). The same table is mirrored
in ``ui/fieldnet_canvas/build/index.html`` (a test keeps the two in sync)."""
from __future__ import annotations

_ELLIPSE = 'M50,0 A50,50 0 1 1 50,100 A50,50 0 1 1 50,0Z'
_PILL = 'M18,0 H82 Q100,0 100,50 Q100,100 82,100 H18 Q0,100 0,50 Q0,0 18,0Z'
_VESSEL = 'M14,0 H86 Q100,0 100,50 Q100,100 86,100 H14 Q0,100 0,50 Q0,0 14,0Z'
_HEX = 'M10,0 H90 L100,50 L90,100 H10 L0,50Z'
_OCT = 'M20,0 H80 L100,26 V74 L80,100 H20 L0,74 V26Z'

SHAPES = {
    'reservoir': 'M0,16 Q0,0 50,0 Q100,0 100,16 V84 Q100,100 50,100 Q0,100 0,84Z',          # cylinder (tank)
    'well': _ELLIPSE, 'water_injector': _ELLIPSE, 'gas_injector': _ELLIPSE,                   # wellhead / bore
    'manifold': _HEX,                                                                          # header
    'separator': _VESSEL, 'separator_stage': _VESSEL,                                          # horizontal vessel
    'sink': 'M0,0 H80 L100,50 L80,100 H0Z', 'oil_export': 'M0,0 H80 L100,50 L80,100 H0Z',      # outlets point right
    'gas_export': 'M0,0 H80 L100,50 L80,100 H0Z', 'water_disposal': 'M0,0 H80 L100,50 L80,100 H0Z',
    'water_source': 'M20,0 H100 V100 H20 L0,50Z', 'gas_source': 'M20,0 H100 V100 H20 L0,50Z',   # inlets point in
    'choke': _OCT, 'control_valve': _OCT,
    'pump': _ELLIPSE,
    'compressor': 'M0,0 L100,24 V76 L0,100Z',                                                  # converging trapezoid
}
DETAIL = {
    'reservoir': 'M0,16 Q0,32 50,32 Q100,32 100,16',                                          # rim of the cylinder
    'separator': 'M14,0 V100 M86,0 V100', 'separator_stage': 'M14,0 V100 M86,0 V100',
    'well': 'M50,0 V16', 'water_injector': 'M50,0 V16', 'gas_injector': 'M50,0 V16',
}
DASHED = {'water_injector', 'gas_injector', 'water_source', 'gas_source'}   # supply side drawn dashed


def shape(kind):
    """-> (outline path, detail path or '', dashed) or None for the default rounded card."""
    return (SHAPES[kind], DETAIL.get(kind, ''), kind in DASHED) if kind in SHAPES else None


# ---- phase colours (one set for the layout fills and every chart: gas red, oil green, water blue) -------------------------------
PHASE_COLOR = {'oil': '#1baf7a', 'gas': '#d93a3a', 'water': '#2a78d6'}


def node_phase(node, nodes=()):
    """Phase that fills a node symbol: 'oil' | 'gas' | 'water', or None for equipment that has no fluid of its own."""
    k = node.get('kind'); p = node.get('params') or {}
    if k == 'reservoir': return 'oil' if str(p.get('fluid_phase', 'oil')).lower() == 'oil' else 'gas'
    if k in ('water_injector', 'water_source', 'water_disposal'): return 'water'
    if k in ('gas_injector', 'gas_source', 'gas_export'): return 'gas'
    if k == 'oil_export': return 'oil'
    if k in ('well', 'injector'):
        ph = str(p.get('phase') or '').lower()
        if ph == 'gas_condensate': ph = 'gas'
        if ph in PHASE_COLOR: return ph
        tank = next((t for t in nodes if t.get('id') == p.get('reservoir_id') and t.get('kind') == 'reservoir'), None)
        if tank is not None: return node_phase(tank)
        return 'gas' if str(p.get('ipr_model', '')).lower().startswith('gas') else 'oil'
    return None


def water_fraction(node):
    """Fraction of the symbol drawn blue (water) at the bottom: well water cut, tank initial water saturation."""
    p = node.get('params') or {}; k = node.get('kind')
    try:
        if k == 'well': v = float(p.get('water_cut', 0.0))
        elif k == 'reservoir': v = float(p.get('swi', 0.2))
        else: return 0.0
    except (TypeError, ValueError): return 0.0
    return min(max(v, 0.0), 0.95) if v == v else 0.0


def node_scale(node):
    """Symbol size multiplier set by the user (params.scale), clamped to 0.4 .. 3."""
    try: v = float((node.get('params') or {}).get('scale', 1.0))
    except (TypeError, ValueError): return 1.0
    return min(max(v, 0.4), 3.0) if v == v else 1.0
