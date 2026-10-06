from network.examples import demo_field_case
from network.prediction_assign import source_overview, assign_decline, clear_source, set_tank_mode
from network.forecast import run_forecast


def test_assign_decline_splits_qi_and_overview_and_clear():
    n, e = demo_field_case(); ids = [x['id'] for x in n if x['kind'] == 'well']
    ns = assign_decline(n, ids, 3000.0, 0.3, 0.5, split='equal')
    qis = [x['params']['prediction_source']['qi'] for x in ns if x['kind'] == 'well']; assert abs(sum(qis) - 3000) < 1e-6
    assert all(r['Source'] == 'Decline curve' for r in source_overview(ns))
    assert all(r['Source'] == 'Tank balance / IPR' for r in source_overview(clear_source(ns, ids)))
    assert all('prediction_source' not in x['params'] for x in n if x['kind'] == 'well')            # input untouched


def test_decline_potential_caps_forecast_oil():
    n, e = demo_field_case(); ids = [x['id'] for x in n if x['kind'] == 'well']
    free = run_forecast(n, e, '2026-01-01', 1.0, 90)
    capped = run_forecast(assign_decline(n, ids, 600.0, 0.5, 0.5), e, '2026-01-01', 1.0, 90)
    assert capped['field'][1]['Total liquid [m3/d]'] < 0.6 * free['field'][1]['Total liquid [m3/d]']


def test_set_tank_mode_validation():
    n, e = demo_field_case(); tid = next(x['id'] for x in n if x['kind'] == 'reservoir')
    try: set_tank_mode(n, tid, 'external'); assert False
    except ValueError: pass
    ns = set_tank_mode(n, tid, 'external', [{'date': '2026-01-01', 'reservoir_pressure_bar': 250}]); assert next(x for x in ns if x['id'] == tid)['params']['prediction_mode'] == 'external'
