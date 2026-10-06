import numpy as np
from network.examples import demo_field_case
from network.simulator_link import (export_vfp_prod, parse_vfp_prod, import_rate_schedule, rates_to_csv, export_forecast_rates,
                                    rates_to_prediction_sources, apply_rate_schedule, compare_rates, iteration_summary)

RATES = [200.0, 800.0, 1500.0]; WHP = [20.0, 40.0]; WCT = [0.0, 0.5]; GOR = [50.0, 150.0]


def _well(): return demo_field_case()[0][1]


def test_vfp_round_trip_and_keywords():
    w = _well(); txt = export_vfp_prod(w, RATES, WHP, WCT, GOR, table_number=7)
    assert [l for l in txt.splitlines() if l.strip() == 'VFPPROD'] == ['VFPPROD'] and 'METRIC' in txt and 'NOT LOADED IN ECLIPSE' in txt and "' '" in txt
    t = parse_vfp_prod(txt)
    assert t['table'] == 7 and t['datum'] == 2400.0 and t['rate_type'] == 'LIQ' and t['units'] == 'METRIC' and t['body_type'] == 'BHP'
    assert t['bhp'].shape == (2, 2, 2, 1, 3) and list(t['flow']) == RATES and list(t['thp']) == WHP
    from physics.well_model import well_settings, vlp_bhp
    p = dict(w['params']); p['water_cut'] = 0.5; p['gor_sm3sm3'] = 150.0
    ref = vlp_bhp(800.0, 40.0, well_settings(p))[0]
    assert abs(t['bhp'][1, 1, 1, 0, 1] - ref) < 1e-3 * ref + 1e-4
    assert np.all(t['bhp'][1, 0, 0, 0] > t['bhp'][0, 0, 0, 0])      # higher WHP -> higher BHP


def test_vfp_oil_rate_type_and_validation():
    t = parse_vfp_prod(export_vfp_prod(_well(), RATES, WHP, [0.0, 0.5], [100.0], rate_type='OIL'))
    assert t['rate_type'] == 'OIL'
    try: export_vfp_prod(_well(), [800.0, 200.0], WHP, WCT, GOR); assert False
    except ValueError: pass
    try: parse_vfp_prod('VFPPROD\n 1 2400 LIQ WCT GOR THP / \n'); assert False
    except ValueError: pass


def test_parser_handles_repeat_counts_comments_and_wraps():
    txt = "-- c\nVFPPROD\n 1 100 LIQ WCT GOR THP ' ' METRIC BHP /\n 100 200 /\n 10 /\n 0 /\n 0 /\n 0 /\n 1 1 1 1\n 2*55.5 /\n"
    t = parse_vfp_prod(txt); assert t['bhp'].shape == (1, 1, 1, 1, 2) and list(t['bhp'][0, 0, 0, 0]) == [55.5, 55.5]


CSV = "date,well,oil,water,gas,pressure\n2027-01-01,P1,1000,100,110000,250\n2027-07-01,P1,800,200,100000,240\n2027-01-01,P2,500,0,50000,\n"


def test_import_rate_schedule_and_csv_round_trip():
    rows = import_rate_schedule(CSV); assert len(rows) == 3 and rows[0]['well'] == 'P1' and rows[2]['well'] == 'P2' and 'pressure' not in rows[2]
    assert import_rate_schedule(rates_to_csv(rows)) == rows
    r2 = import_rate_schedule("Date;Well;Oil [Sm3/d];WCT\n2027-01-01;A;5;1\n".replace(';WCT', ';Water [Sm3/d]'))
    assert r2[0]['oil'] == 5.0 and r2[0]['water'] == 1.0 and r2[0]['gas'] == 0.0
    try: import_rate_schedule('date,well\n2027-01-01,A\n'); assert False
    except ValueError: pass


def test_prediction_source_format_is_usable():
    from network.prediction_sources import prediction_preview
    src = rates_to_prediction_sources(import_rate_schedule(CSV))
    s = src['P1']; assert s['type'] == 'external_table' and s['rows'][0]['max_oil_rate_m3d'] == 1000.0
    assert abs(s['rows'][0]['water_cut'] - 100 / 1100) < 1e-12 and abs(s['rows'][0]['gor_sm3sm3'] - 110.0) < 1e-12
    n, e = demo_field_case(); ns = apply_rate_schedule(n, import_rate_schedule(CSV))
    assert ns[1]['params']['prediction_source']['type'] == 'external_table' and 'prediction_source' not in n[1]['params'] and 'prediction_source' not in ns[3]['params']
    df = prediction_preview(ns[1]['params']['prediction_source'], '2027-01-01', years=1, step_days=90); assert len(df) > 0


def test_forecast_export_round_trip_and_iteration():
    from network.forecast import run_forecast
    n, e = demo_field_case(); fc = run_forecast(n, e, '2027-01-01', years=1, step_days=90)
    fnet = import_rate_schedule(export_forecast_rates(fc)); assert {r['well'] for r in fnet} == {'PROD-A', 'PROD-B', 'PROD-C'}
    assert abs(sum(r['oil'] for r in fnet if r['date'] == '2027-01-01') - fc['field'][0]['Oil [m3/d]']) < 1e-6 * fc['field'][0]['Oil [m3/d]']
    same = iteration_summary(fnet, fnet); assert same['converged'] and same['max_abs_rel_error'] == 0.0
    sim = [dict(r, oil=r['oil'] * (0.8 if r['well'] == 'PROD-A' else 1.0)) for r in fnet]
    it = iteration_summary(fnet, sim, rel_tol=0.05); assert not it['converged'] and abs(it['per_well']['PROD-A']['scale_factor'] - 0.8) < 1e-9
    assert abs(it['per_well']['PROD-B']['scale_factor'] - 1.0) < 1e-9 and it['worst'][0]['well'] == 'PROD-A' and it['cumulative_rel_error']['oil'] > 0
    adj = [dict(r, oil=r['oil'] * it['per_well'][r['well']]['scale_factor']) for r in fnet]
    it2 = iteration_summary(adj, sim, previous_max_rel_error=it['max_abs_rel_error']); assert it2['converged'] and it2['improving']


def test_unmatched_rows_reported():
    a = [{'well': 'A', 'date': '2027-01-01', 'oil': 1.0, 'water': 0, 'gas': 0}]; b = [{'well': 'B', 'date': '2027-01-01', 'oil': 1.0, 'water': 0, 'gas': 0}]
    s = iteration_summary(a, b); assert s['n_compared'] == 0 and not s['converged'] and s['unmatched_fieldnet'] == [('A', '2027-01-01')]
