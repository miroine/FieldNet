from network.examples import demo_field_case
from network.forecast import run_forecast
from ui.results_browser import forecast_dates, state_at, diagram_labels, network_svg_at


def _fc(**kw):
    n, e = demo_field_case(); return n, e, run_forecast(n, e, '2026-01-01', 1.0, 90, **kw)


def test_dates_state_and_diagram_change_with_date():
    n, e, fc = _fc(); d = forecast_dates(fc); assert len(d) >= 3
    a, b = network_svg_at(n, e, fc, d[0]), network_svg_at(n, e, fc, d[-1])
    assert a.startswith('<svg') and 'bar' in a and a != b                # pressures / rates differ as the field depletes
    nr, er = state_at(fc, d[1]); assert nr and er and all(str(r['Date']) == d[1] for r in nr)
    labels, rates = diagram_labels(nr, er); assert any('Sm³/d' in v for v in labels.values()) and rates


def test_no_elements_when_not_stored():
    n, e, fc = _fc(store_elements=False); assert forecast_dates(fc) == []
