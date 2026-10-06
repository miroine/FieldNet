import math, pytest
import physics.pvt_model as pm
from physics.pvt_model import *


def test_standing_textbook_and_inverse():
    assert abs(pm._RS['standing'](2000, 200, 35, .75) - 445) < 2          # McCain worked example order of magnitude
    for c in PB_CORRS:
        rs = pm._RS[c](2000, 200, 35, .75); assert abs(pm._PB[c](rs, 200, 35, .75) - 2000) < 1.0


@pytest.mark.parametrize('tr,pr,z', [(1.5, 2.0, 0.83), (2.0, 5.0, 0.96), (1.3, 4.0, 0.66), (1.6, 10.0, 1.13)])
def test_z_matches_standing_katz(tr, pr, z):
    assert abs(pm.z_dak(tr, pr) - z) < 0.02 and abs(pm.z_hall_yarborough(tr, pr) - z) < 0.02


def test_black_oil_shape():
    fm = FluidModel(FluidSpec(rsb_sm3sm3=100)); pb = fm.bubble_point_bar(80); assert 80 < pb < 300
    ps = [20, 60, 100, pb * 0.99, pb * 1.01, pb * 1.5, pb * 2]; st = [fm.state(p, 80) for p in ps]
    rs = [s.solution_gor_sm3sm3 for s in st]; assert rs == sorted(rs) and abs(rs[-1] - 100) < 1e-9 and abs(rs[-2] - 100) < 1e-9
    bo = [s.oil_fvf for s in st]; assert bo[:4] == sorted(bo[:4]) and bo[4] > bo[5] > bo[6]            # peak at Pb, falls above
    mu = [s.oil_viscosity_pas for s in st]; assert min(mu) in (mu[3], mu[4]) and mu[-1] > mu[4]          # minimum at Pb
    assert all(s.oil_density_kgm3 > 500 for s in st)


def test_measured_pb_overrides_and_rsb_is_the_gor():
    fm = FluidModel(FluidSpec(rsb_sm3sm3=140, pb_bar=210.0)); assert fm.bubble_point_bar(90) == pytest.approx(210.0, rel=1e-6)
    assert abs(fm.state(250, 90).solution_gor_sm3sm3 - 140) < 1e-9 and 0 < fm.state(150, 90).solution_gor_sm3sm3 < 140


def test_contaminants_change_gas_and_pb():
    base = FluidModel(FluidSpec(rsb_sm3sm3=100)); co2 = FluidModel(FluidSpec(rsb_sm3sm3=100, co2=0.25)); h2s = FluidModel(FluidSpec(rsb_sm3sm3=100, h2s=0.1)); n2 = FluidModel(FluidSpec(rsb_sm3sm3=100, n2=0.1))
    assert co2.bubble_point_bar(80) < base.bubble_point_bar(80) and h2s.bubble_point_bar(80) < base.bubble_point_bar(80) and n2.bubble_point_bar(80) > base.bubble_point_bar(80)
    s0, s1 = base.state(120, 80), co2.state(120, 80); assert s1.gas_z != s0.gas_z
    t0, p0 = gas_pseudocritical(0.8); t1, p1 = gas_pseudocritical(0.8, h2s=0.1); assert t1 != t0
    # Wichert-Aziz lowers the pseudo-critical temperature of a sour gas relative to plain Kay mixing
    tk, _ = gas_pseudocritical(0.8, h2s=0.1, sour_correction=False); assert t1 < tk


def test_gas_properties_in_range():
    z = gas_z(100, 60, 0.7); assert 0.8 < z < 0.95
    mu = gas_viscosity_cp(100, 60, z, 0.7); assert 0.010 < mu < 0.025
    assert gas_viscosity_cp(100, 60, z, 0.7, co2=0.2) != mu


def test_water_props_sane():
    bw, rho, mu = water_props(200, 80, 4.0); assert 1.0 < bw < 1.06 and 960 < rho < 1060 and 0.2e-3 < mu < 0.6e-3
    assert water_props(200, 80, 10.0)[1] > rho


def test_spec_validation_and_roundtrip():
    with pytest.raises(ValueError): FluidModel(FluidSpec(co2=0.6, h2s=0.4))
    with pytest.raises(ValueError): FluidModel(FluidSpec(pb_corr='nope'))
    s = FluidSpec(api=30, co2=0.1, cal=Calibration(bo_mult=1.2)); assert FluidSpec.from_dict(s.to_dict()) == s


def _lab_from(truth, t_c, ps):
    fm = FluidModel(truth); rows = []
    for p in ps:
        st = fm.state(p, t_c); rows.append({'p_bar': p, 'rs': st.solution_gor_sm3sm3, 'bo': st.oil_fvf, 'mu_o': st.oil_viscosity_pas / CP, 'z': st.gas_z, 'mu_g': st.gas_viscosity_pas / CP})
    return {'pb_bar': fm.bubble_point_bar(t_c), 'rsb': truth.rsb_sm3sm3, 'table': rows}


def test_calibration_recovers_synthetic_truth():
    truth = FluidSpec(rsb_sm3sm3=110, cal=Calibration(pb_a=1.25, bo_mult=1.15, mu_mult=1.6, z_mult=1.03, rs_shape=1.2, mug_mult=1.1))
    lab = _lab_from(truth, 85.0, [30, 60, 90, 120, 160, 200, 250, 300, 350])
    start = FluidSpec(rsb_sm3sm3=110)
    spec, rows, notes = calibrate(start, lab, 85.0)
    err = {r['Property']: r for r in rows}
    assert err['Bubble point']['Error before [%]'] > 10 and err['Bubble point']['Error after [%]'] < 0.5
    for prop in ('Rs', 'Bo', 'Oil viscosity', 'Z factor', 'Gas viscosity'):
        assert err[prop]['Error after [%]'] < 2.0 <= err[prop]['Error before [%]'] + 1.5 or err[prop]['Error after [%]'] < err[prop]['Error before [%]'], (prop, err[prop])
    assert spec.cal.pb_a == pytest.approx(1.25, rel=0.02) and spec.cal.bo_mult == pytest.approx(1.15, rel=0.1)


def test_calibration_with_only_pb_and_nothing():
    s, rows, notes = calibrate(FluidSpec(rsb_sm3sm3=100), {'pb_bar': 180.0}, 80.0); assert FluidModel(s).bubble_point_bar(80) == pytest.approx(180.0, rel=1e-3)
    s, rows, notes = calibrate(FluidSpec(), {}, 80.0); assert rows == [] and any('nothing' in n for n in notes)


def test_rank_correlations_prefers_the_true_one():
    truth = FluidSpec(rsb_sm3sm3=100, api=28, bo_corr='vasquez_beggs', visc_corr='glaso')
    lab = _lab_from(truth, 90.0, [40, 80, 120, 160, 200, 260]); r = rank_correlations(FluidSpec(rsb_sm3sm3=100, api=28), lab, 90.0)
    best = {p: next(x['Correlation'] for x in r if x['Property'] == p and x['Rank'] == 1) for p in {x['Property'] for x in r}}
    assert best['Bo'] == 'vasquez_beggs' and best['Oil viscosity'] == 'glaso'


def test_element_integration_changes_hydraulics_only_when_selected():
    from physics.beggs_brill import beggs_brill_dp_bar
    a = beggs_brill_dp_bar(800, 1000, 0.15, 4.5e-5, 0, 60, 50, .3, 100, 36, .72)[0]
    prm = {'gor_sm3sm3': 100, 'api': 36, 'gas_sg': .72, 'pvt': {'model': 'correlation', 'co2': 0.15}}
    with fluid_scope(prm): b = beggs_brill_dp_bar(800, 1000, 0.15, 4.5e-5, 0, 60, 50, .3, 100, 36, .72)[0]
    with fluid_scope({'gor_sm3sm3': 100}): c = beggs_brill_dp_bar(800, 1000, 0.15, 4.5e-5, 0, 60, 50, .3, 100, 36, .72)[0]
    assert a == c and a != b and current_fluid() is None


def test_legacy_rs_ignored_gor_new_model_uses_it():
    from physics.pvt import simple_black_oil
    assert simple_black_oil(300, 60).solution_gor_sm3sm3 == 120      # legacy: fixed Rsb whatever the fluid (documented defect)
    fm = fluid_from_params({'gor_sm3sm3': 60, 'api': 35, 'gas_sg': .75, 'pvt': {'model': 'correlation'}}); assert abs(fm.state(300, 60).solution_gor_sm3sm3 - 60) < 1e-9


def test_fluid_from_params_legacy_is_none_and_bad_spec_is_none():
    assert fluid_from_params({'pvt': {'model': 'legacy'}}) is None and fluid_from_params({}) is None
    assert fluid_from_params({'gor_sm3sm3': 100, 'pvt': {'model': 'correlation', 'co2': 2}}) is None


def test_full_network_solve_and_forecast_with_correlation_pvt_and_thermal():
    import datetime
    from network.examples import demo_field_case
    from network.solve_options import make_network_solver
    from network.forecast import run_forecast
    n, e = demo_field_case()
    for o in n + e:
        if o in e or o.get('kind') == 'well': o.setdefault('params', {})['pvt'] = {'model': 'correlation', 'co2': 0.08, 'h2s': 0.001}
    for x in e:
        if x['kind'] == 'pipeline': x['params']['thermal_model'] = 'heat_loss'
    for x in n:
        if x['kind'] == 'well': x['params'].update({'thermal_model': 'ramey', 'bottomhole_temperature_c': 105})
    p, q, info, d = make_network_solver({})(n, e)
    assert info['quality_gate'] == 'PASS' and info['thermal']['converged'] and sum(v['liquid_rate_m3d'] for v in d.values()) > 100
    fc = run_forecast(n, e, datetime.date(2027, 1, 1), 1, 180, store_elements=False); assert fc['field'] and all(r['Converged'] for r in fc['field'])
