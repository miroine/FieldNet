import pytest, numpy as np, pandas as pd
from network.examples import demo_field_case
from network.reservoir_mb import apply_tank_links
from physics import well_match as wm
from physics.well_model import well_settings, solve_well_rate


def _w(**kw):
    n, e = demo_field_case(); n = apply_tank_links(n); p = dict(next(x for x in n if x['kind'] == 'well')['params']); p.update(kw); return p


def _ipr_pts(pi, pr=290.0, noise=0.0):
    rng = np.random.default_rng(0); q = np.array([300.0, 700.0, 1100.0, 1500.0])
    return pd.DataFrame({'Rate [m3/d]': q, 'BHP [bar]': pr - q / pi + rng.normal(0, noise, 4)})


def test_ipr_recovers_pi():
    p = _w(pi_m3d_bar=5.0, skin=0.0); f = wm.match_ipr(p, _ipr_pts(20.0))
    assert f['params']['pi_m3d_bar'] == pytest.approx(20.0, rel=0.01) and f['rmse_after_bar'] < 0.01 and f['rmse_before_bar'] > 1


def test_ipr_fits_reservoir_pressure_too():
    p = _w(pi_m3d_bar=5.0, skin=0.0, reservoir_pressure_bar=250.0); f = wm.match_ipr(p, _ipr_pts(20.0, 290.0), fit_reservoir_pressure=True)
    assert f['params']['reservoir_pressure_bar'] == pytest.approx(290.0, rel=0.01) and f['params']['pi_m3d_bar'] == pytest.approx(20.0, rel=0.02)


def test_ipr_vogel_and_skin_consistency():
    p = _w(ipr_model='Vogel', qmax_m3d=500.0, skin=3.0); pts = wm.synthetic_tests(dict(p, qmax_m3d=2500.0), whps=(15., 30., 50.))
    f = wm.match_ipr(p, pts); q2 = well_settings(wm.apply_match(p, f))
    assert f['rmse_after_bar'] < 0.5
    assert solve_well_rate(30.0, q2)[0] == pytest.approx(solve_well_rate(30.0, well_settings(dict(p, qmax_m3d=2500.0)))[0], rel=0.05)


def test_ipr_needs_data():
    assert 'error' in wm.match_ipr(_w(), pd.DataFrame({'Rate [m3/d]': [], 'BHP [bar]': []}))
    assert 'error' in wm.match_ipr(_w(), _ipr_pts(10.0).head(1), fit_reservoir_pressure=True)


def test_vlp_ranking_finds_generating_correlation_and_multiplier():
    truth = _w(vlp_model='Homogeneous', correlation='Homogeneous'); pts = wm.synthetic_tests(truth, whps=(15., 25., 40.))
    r = wm.match_vlp(_w(), pts, correlations=['Beggs-Brill', 'Homogeneous'])
    assert r['best_correlation'] == 'Homogeneous' and r['multiplier'] == pytest.approx(1.0, abs=0.02)
    t2 = _w(vlp_dp_multiplier=1.25); pts2 = wm.synthetic_tests(t2, whps=(15., 25., 40.))
    r2 = wm.match_vlp(_w(), pts2, correlations=['Beggs-Brill']); assert r2['multiplier'] == pytest.approx(1.25, rel=0.05) and r2['rmse_with_multiplier_bar'] < r2['best_rmse_bar']


def test_vlp_warns_for_extreme_multiplier():
    pts = wm.synthetic_tests(_w(vlp_dp_multiplier=1.9), whps=(5., 10., 15.)); r = wm.match_vlp(_w(), pts, correlations=['Beggs-Brill']); assert r['warning']


def test_apply_match_roundtrip_reproduces_tests():
    truth = _w(vlp_dp_multiplier=1.2, pi_m3d_bar=9.0); pts = wm.synthetic_tests(truth, whps=(15., 25., 40.))
    base = _w(); fi = wm.match_ipr(base, pts); fv = wm.match_vlp(base, pts, correlations=['Beggs-Brill']); m = wm.apply_match(base, fi, fv)
    for _, r in pts.iterrows():
        assert solve_well_rate(r['WHP [bar]'], well_settings(m))[0] == pytest.approx(r['Rate [m3/d]'], rel=0.08)


def test_survey_ranking():
    p = _w(); import physics.vlp as v
    prof = []; ws = well_settings(p); v.tubing_bhp_bar(500.0, 20.0, ws['depth'], ws['tubing_id'], ws['roughness'], ws['temperature'], ws['water_cut'], ws['gor'], ws['api'], ws['gas_sg'], 'Beggs-Brill', segments=20, profile=prof)
    s = pd.DataFrame({'TVD [m]': [r['tvd_m'] for r in prof][::4], 'Pressure [bar]': [r['pressure_bar'] for r in prof][::4]})
    r = wm.match_survey(p, s, 500.0, 20.0, correlations=['Beggs-Brill', 'Homogeneous']); assert r['best_correlation'] == 'Beggs-Brill' and r['ranking'].iloc[0]['RMSE [bar]'] < 1.0
    assert 'error' in wm.match_survey(p, s.head(1), 500.0, 20.0)
