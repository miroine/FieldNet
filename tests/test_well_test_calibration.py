"""Tests for network/well_test_calibration.py (synthetic tests from a perturbed demo case)."""
import copy
from network.examples import demo_field_case
from network.well_test_calibration import calibrate_well_tests, generate_synthetic_tests, WellTest


def _perturbed(pi_mults, fr_mults):
    n, e = demo_field_case(); truth = copy.deepcopy(n)
    for x in truth:
        if x['id'] in pi_mults:
            x['params']['pi_m3d_bar'] *= pi_mults[x['id']]
            x['params']['tubing_roughness_m'] *= fr_mults[x['id']]
    return n, e, truth


def test_recovers_pi_and_friction_perturbation():
    pim = {'P1': 1.3, 'P2': 0.7, 'P3': 1.15}; frm = {'P1': 15.0, 'P2': 8.0, 'P3': 20.0}
    n, e, truth = _perturbed(pim, frm)
    tests = generate_synthetic_tests(truth, e)
    assert len(tests) >= 9
    res = calibrate_well_tests(n, e, tests)
    wt = {w['well_id']: w for w in res['well_table']}
    for w in pim:
        assert abs(wt[w]['pi_mult'] / pim[w] - 1) < 0.03, (w, wt[w])
        assert abs(wt[w]['friction_mult'] / frm[w] - 1) < 0.15, (w, wt[w])
        assert wt[w]['flag'] == 'OK'
    s = res['summary']
    assert s['after']['rmse_m3d'] < 0.05 * s['before']['rmse_m3d'] and s['after']['rmse_m3d'] < 2.0
    # input untouched, tuned copy changed
    assert n[1]['params']['pi_m3d_bar'] == 14.0
    assert abs(next(x for x in res['nodes'] if x['id'] == 'P1')['params']['pi_m3d_bar'] / 14.0 - 1.3) < 0.05


def test_flags_well_at_bound_and_single_test_not_friction_fitted():
    n, e, truth = _perturbed({'P1': 1.0}, {'P1': 1.0})
    truth[1]['params']['pi_m3d_bar'] *= 20.0  # far outside the PI bounds
    tests = generate_synthetic_tests(truth, e, well_ids=['P1'])
    res = calibrate_well_tests(n, e, tests, fit_friction=False)
    w = res['well_table'][0]
    assert w['at_bound_pi'] and w['flag'] == 'AT_BOUND'
    assert 'P1' in res['summary']['wells_at_bound']
    one = calibrate_well_tests(n, e, [WellTest('P2', 25.0, oil_rate_m3d=500.0, water_cut=0.1)])
    assert one['well_table'][0]['friction_fitted'] is False and one['well_table'][0]['friction_mult'] == 1.0
    assert any('not fitted' in f for f in one['flags'])


def test_unknown_well_and_missing_rate():
    n, e = demo_field_case()
    res = calibrate_well_tests(n, e, [{'well_id': 'ZZ', 'whp_bar': 20, 'liquid_rate_m3d': 100},
                                       {'well_id': 'P1', 'whp_bar': 25, 'liquid_rate_m3d': 1200}])
    assert any('unknown well' in f for f in res['flags']) and len(res['test_table']) == 1
    try: calibrate_well_tests(n, e, [{'well_id': 'P1', 'whp_bar': 25}]); assert False
    except ValueError: pass
