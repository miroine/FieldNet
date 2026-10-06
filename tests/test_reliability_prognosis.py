import numpy as np
from network.reliability_prognosis import uptime_weighted_profiles


def _series(n=24, step=30):
    days = [i * step for i in range(n)]
    return {'days': days, 'rates': {'A': [1000.0 * 0.97 ** i for i in range(n)], 'B': [600.0 * 0.95 ** i for i in range(n)]}}


def test_percentile_order_and_mean_matches_availability():
    s = _series(); r = uptime_weighted_profiles(s, {'A': 0.9, 'B': 0.8}, n_samples=400, seed=7)
    for k in range(len(r['days'])):
        assert r['rate']['P90'][k] <= r['rate']['P50'][k] + 1e-9 <= r['rate']['P10'][k] + 2e-9
        assert r['cumulative']['P90'][k] <= r['cumulative']['P50'][k] + 1e-9 <= r['cumulative']['P10'][k] + 2e-9
    un = r['unconstrained']['cumulative'][-1]
    expect = sum(w['availability_input'] * w['cumulative_unconstrained'] for w in r['per_well'].values())
    assert abs(r['cumulative_final']['mean'] - expect) / expect < 0.01
    assert 0.8 < r['expected_uptime_factor'] < 0.9 and abs(r['expected_uptime_factor'] - expect / un) < 0.01
    assert r['cumulative_final']['P90'] < r['cumulative_final']['P10'] < un


def test_deterministic_given_seed_and_different_seed_differs():
    s = _series(); a = uptime_weighted_profiles(s, 0.9, n_samples=50, seed=3); b = uptime_weighted_profiles(s, 0.9, n_samples=50, seed=3)
    c = uptime_weighted_profiles(s, 0.9, n_samples=50, seed=4)
    assert a['cumulative']['P50'] == b['cumulative']['P50'] and a['cumulative']['P50'] != c['cumulative']['P50']


def test_full_availability_is_unconstrained_and_zero_is_zero():
    s = _series(); r = uptime_weighted_profiles(s, 1.0, n_samples=5)
    assert np.allclose(r['cumulative']['P50'], r['unconstrained']['cumulative']) and r['expected_uptime_factor'] == 1.0
    z = uptime_weighted_profiles(s, 0.0, n_samples=5); assert z['cumulative_final']['P10'] == 0.0


def test_system_availability_multiplies():
    s = _series(); r = uptime_weighted_profiles(s, 0.95, n_samples=500, seed=1, system_availability=0.9)
    assert abs(r['expected_uptime_factor'] - 0.855) < 0.015


def test_renewal_mode_mean_close_to_mtbf_mttr_availability():
    s = _series(n=60, step=30)   # ~5 years
    r = uptime_weighted_profiles(s, {'A': {'mtbf_days': 45, 'mttr_days': 5}, 'B': 1.0}, n_samples=60, seed=11, mode='renewal')
    assert abs(r['per_well']['A']['realised_mean_uptime'] - 0.9) < 0.03 and r['per_well']['B']['realised_mean_uptime'] == 1.0


def test_from_run_forecast_result():
    from network.examples import demo_field_case
    from network.forecast import run_forecast
    n, e = demo_field_case(); fc = run_forecast(n, e, '2027-01-01', years=1, step_days=90)
    r = uptime_weighted_profiles(fc, 0.92, n_samples=100, seed=2)
    assert len(r['rate']['P50']) == len({x['Date'] for x in fc['wells']}) and r['dates'][0] == '2027-01-01'
    assert r['unconstrained']['rate'][0] > 2000 and r['rate']['P90'][0] <= r['rate']['P10'][0]
