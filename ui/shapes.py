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
