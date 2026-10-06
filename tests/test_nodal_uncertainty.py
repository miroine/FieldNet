import numpy as np, pytest
from network.examples import demo_field_case
from network.reservoir_mb import apply_tank_links
from network import nodal_uncertainty as nu
from physics.well_model import well_settings, solve_well_rate, vlp_bhp


def _well():
    n, e = demo_field_case(); n = apply_tank_links(n); return dict(next(x for x in n if x['kind'] == 'well')['params'])


KEYS = ('reservoir_pressure_bar', 'productivity', 'skin', 'water_cut', 'whp', 'vlp_dp_multiplier')


def test_overrides_do_not_mutate_and_scale():
    p = _well(); pi0 = p['pi_m3d_bar']; q, w = nu.apply_overrides(p, 20.0, {'productivity': 2.0, 'whp': 25.0, 'water_cut': 0.5})
    assert q['pi_m3d_bar'] == pytest.approx(2 * pi0) and p['pi_m3d_bar'] == pi0 and w == 25.0 and q['water_cut'] == 0.5


def test_higher_pressure_gives_more_rate():
    p = _well(); a = nu.operating_point(p, 20.0)
    b = nu.operating_point(nu.apply_overrides(p, 20.0, {'reservoir_pressure_bar': p['reservoir_pressure_bar'] + 20})[0], 20.0)
    assert b['q_liq'] >= a['q_liq']


def test_mc_reproducible_and_ordered():
    p = _well(); specs = [nu.default_spec(p, 20.0, k) for k in KEYS]
    r1 = nu.run(p, 20.0, specs, n=30, seed=5); r2 = nu.run(p, 20.0, specs, n=30, seed=5)
    assert r1['samples']['q_liq'].equals(r2['samples']['q_liq'])
    s = r1['summary']['Liquid rate [m3/d]']; assert s['P90'] <= s['P50'] <= s['P10']
    e = r1['envelope']; assert (e['IPR P90'] <= e['IPR P10'] + 1e-9).all()
    assert r1['ipr'].shape == (30, len(r1['q']))


def test_sample_equals_single_well_model():
    p = _well(); specs = [nu.default_spec(p, 20.0, k) for k in KEYS]; r = nu.run(p, 20.0, specs, n=10, seed=3)
    row = r['samples'].iloc[4]; pp, w = nu.apply_overrides(p, 20.0, {k: row[k] for k in KEYS})
    q, _ = solve_well_rate(w, well_settings(pp)); assert row['q_liq'] == pytest.approx(q)


def test_pick_realisation_monotone():
    p = _well(); specs = [nu.default_spec(p, 20.0, k) for k in KEYS]; r = nu.run(p, 20.0, specs, n=40, seed=2)
    hi, lo = nu.pick_realisation(r, 10), nu.pick_realisation(r, 90)
    assert r['samples']['q_liq'].iloc[hi] >= r['samples']['q_liq'].iloc[lo]


def test_vlp_multiplier_raises_bhp_and_lowers_rate():
    p = _well(); q = 400.0; a = vlp_bhp(q, 20.0, well_settings(p))[0]; b = vlp_bhp(q, 20.0, well_settings(dict(p, vlp_dp_multiplier=1.3)))[0]
    assert b > a and (b - 20.0) == pytest.approx(1.3 * (a - 20.0), rel=1e-6)
    assert solve_well_rate(20.0, well_settings(dict(p, vlp_dp_multiplier=1.5)))[0] <= solve_well_rate(20.0, well_settings(p))[0] + 1e-9


def test_requires_specs():
    with pytest.raises(ValueError): nu.run(_well(), 20.0, [])
