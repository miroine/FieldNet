import pytest
from physics.gas_quality import *


def test_partial_pressure_and_rate_trends():
    assert partial_pressure_bar(100, .05) == 5
    assert dewaard_milliams_mm_per_year(0, 60) == 0
    assert dewaard_milliams_mm_per_year(10, 60) > dewaard_milliams_mm_per_year(1, 60)
    assert 0.5 < dewaard_milliams_mm_per_year(2, 60) < 20            # order of magnitude for 2 bar CO2 at 60 C (uninhibited carbon steel)


def test_sour_threshold_and_severity():
    assert screen(100, 60, h2s=0.00005)['sour_service'] is True and screen(100, 60, h2s=0.00001)['sour_service'] is False
    assert screen(3, 60, h2s=0.01)['sour_service'] is False        # below 0.4 MPa total pressure
    assert severity(0.05) == 'low' and severity(7) == 'severe'


def test_network_screen_rows():
    from network.examples import demo_field_case
    from solver.v21 import solve_v21
    n, e = demo_field_case()
    for x in n:
        if x['kind'] == 'well': x['params']['pvt'] = {'model': 'correlation', 'co2': 0.05, 'h2s': 0.0001}
    r = solve_v21(n, e); rows = screen_network(n, e, r); assert len(rows) == 6 and all(x['pCO2 [bar]'] > 0 for x in rows)
    assert next(x for x in rows if 'bottom' in x['Type'])['pCO2 [bar]'] > next(x for x in rows if 'wellhead' in x['Type'])['pCO2 [bar]']
