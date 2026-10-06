import pytest
from network.examples import demo_field_case
from network.reservoir_mb import apply_tank_links
from physics import blowout as bo
from physics.well_model import well_settings, solve_well_rate


def _w(**kw):
    n, e = demo_field_case(); n = apply_tank_links(n); p = dict(next(x for x in n if x['kind'] == 'well')['params']); p.update(kw); return p


def test_exit_pressure_subsea_hydrostatic():
    assert bo.exit_pressure_bar(0) == pytest.approx(1.01325) and bo.exit_pressure_bar(300) == pytest.approx(1.01325 + 1025 * 9.80665 * 300 / 1e5)


def test_tubing_matches_nodal_at_atmospheric():
    p = _w(skin=0.0); r = bo.solve(p, 'tubing', check_choking=False)
    q, st = solve_well_rate(bo.P_ATM, well_settings(dict(p, min_rate_m3d=0.0, lift_type='none')))
    assert r['q_liq'] == pytest.approx(q, rel=0.03)


def test_both_paths_exceed_single_and_subsea_reduces_rate():
    p = _w(); t = bo.solve(p, 'tubing')['q_liq']; a = bo.solve(p, 'annulus')['q_liq']; b = bo.solve(p, 'both')['q_liq']
    assert b > max(t, a) * 0.999 and b <= t + a + 1e-6
    assert bo.solve(p, 'tubing', water_depth_m=1500)['q_liq'] < t


def test_skin_removal_never_lowers_rate():
    p = _w(skin=15.0); assert bo.solve(p, 'tubing', skin_removed=True)['q_liq'] >= bo.solve(p, 'tubing', skin_removed=False)['q_liq']


def test_rate_never_exceeds_aof():
    r = bo.solve(_w(), 'both'); assert r['q_liq'] <= r['aof_m3d'] * 1.001


def test_gas_well_chokes_and_exit_pressure_rises():
    p = _w(gor_sm3sm3=20000.0, pi_m3d_bar=60.0, water_cut=0.0, tubing_id_m=0.15)
    r = bo.solve(p, 'tubing'); assert r['mach'] <= 1.05 and (not r['choked'] or r['exit_pressure_bar'] > bo.P_ATM)


def test_scenario_table_and_units():
    t = bo.scenario_table(_w()); assert len(t) == 6 and (t['Oil [bbl/d]'] > 0).all()
    u = bo.units(bo.solve(_w(), 'tubing')); assert u['Oil [bbl/d]'] == pytest.approx(u['Oil [Sm3/d]'] * 6.28981)


def test_depletion_declines_and_needs_volume():
    d = bo.depletion_profile(_w(), connected_pv_m3=2e7, days=60, steps=8)
    assert d['Reservoir pressure [bar]'].is_monotonic_decreasing and d['Oil [Sm3/d]'].iloc[-1] <= d['Oil [Sm3/d]'].iloc[0]
    with pytest.raises(ValueError): bo.depletion_profile(_w())


def test_annulus_requires_geometry():
    with pytest.raises(ValueError): bo.solve(_w(casing_id_m=0.1), 'annulus')
