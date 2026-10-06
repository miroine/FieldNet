import math, pytest
from physics import thermal as th
from solver.equations import pipeline_march, pipeline_dp_bar
from network.thermal_network import thermal_pass, with_thermal, has_thermal
from network.examples import demo_field_case


def _stream(q=800, wc=.3, gor=100): return th.Stream(q, wc, gor, 36, .72)


def test_no_sources_is_exponential_decay():
    s = _stream(); a = th.relaxation_length_m(s, 5.0, 0.2, 50, 80)
    t = th.advance_segment(80, 1000, 0, 0, 0.2, 5.0, 4.0, s, 50, include_jt=False, include_elevation=False)
    assert t == pytest.approx(4 + 76 * math.exp(-1000 / a), rel=1e-3)


def test_adiabatic_limits():
    s = _stream(gor=300)
    assert th.advance_segment(60, 500, 0, 0, 0.2, 0.0, 4, s, 50, include_jt=False) == pytest.approx(60)
    up = th.advance_segment(60, 500, 1000, 0, 0.2, 0.0, 4, s, 50, include_jt=False)          # 1000 m rise, no heat exchange
    assert up == pytest.approx(60 - th.G * 1000 / s.cp(50, 60), rel=1e-3) and 3.5 < 60 - up < 5.5


def test_jt_gas_cooling_and_liquid_heating():
    cp = th.gas_cp(100, 50, .7); mu = th.jt_coefficient_gas_k_per_bar(100, 50, .7, cp); assert 0.15 < mu < 0.55
    assert th.jt_coefficient_liquid_k_per_bar(60, 800, 2000) < 0           # liquids warm up on a pressure drop
    gas = th.Stream(10, 0, 50000, 36, .7)                                      # gas dominated
    t = th.advance_segment(60, 100, 0, -40, 0.2, 0.0, 4, gas, 100, free_gas_fraction=0.99)
    assert 60 - t > 5                                                           # 40 bar drop cools a wet gas by several K
    assert th.advance_segment(60, 100, 0, -40, 0.2, 0.0, 4, gas, 100, free_gas_fraction=0.99, include_jt=False) == pytest.approx(60)


def test_ramey_wellhead_temperature_rises_with_rate_and_is_bounded():
    p = {'thermal_model': 'ramey', 'bottomhole_temperature_c': 110, 'surface_temperature_c': 4, 'overall_u_w_m2k': 10}
    thi = th.well_thermal_inputs(p, 2200, 0.0889); assert thi['grad'] == pytest.approx(106 / 2200)
    wht = []
    for q in (100, 400, 1000, 4000):
        f, t0 = th.ramey_profile(thi, th.Stream(q, .2, 100, 36, .72), 30); wht.append(t0)
        assert f(2200) == pytest.approx(110) and 4 <= t0 <= 110 and f(0) == pytest.approx(t0)
    assert wht == sorted(wht) and wht[0] < wht[-1] - 10
    assert th.well_thermal_inputs({}, 2200, 0.09) is None


def test_ramey_profile_monotonic_with_depth():
    thi = th.well_thermal_inputs({'thermal_model': 'ramey', 'bottomhole_temperature_c': 100, 'surface_temperature_c': 10}, 2000, .09)
    f, _ = th.ramey_profile(thi, th.Stream(600, .2, 100, 36, .72), 30); ts = [f(z) for z in range(0, 2001, 200)]; assert ts == sorted(ts)


def _pipe(**kw):
    prm = {'water_cut': .3, 'gor_sm3sm3': 100, 'api': 36, 'gas_sg': .72, 'temperature_c': 80}; prm.update(kw)
    return {'id': 'E', 'source': 'A', 'target': 'B', 'kind': 'pipeline', 'diameter_m': 0.25, 'length_m': 6000, 'roughness_m': 4.5e-5, 'elevation_change_m': 0, 'params': prm}


def test_pipeline_isothermal_by_default_and_cools_with_model():
    dp0, t0 = pipeline_march(_pipe(), 1500, 40, 30); assert t0 == 80 and pipeline_dp_bar(_pipe(), 1500, 40, 30) == dp0
    dp1, t1 = pipeline_march(_pipe(thermal_model='heat_loss', overall_u_w_m2k=5, ambient_temperature_c=4), 1500, 40, 30)
    assert t1 < 80 - 3 and dp1 > 0
    prof = []; pipeline_march(_pipe(thermal_model='heat_loss', overall_u_w_m2k=5), 1500, 40, 30, profile=prof)
    ts = [r['temperature_c'] for r in prof]; assert ts == sorted(ts, reverse=True) and len(ts) > 3
    _, t_ins = pipeline_march(_pipe(thermal_model='heat_loss', overall_u_w_m2k=0.5), 1500, 40, 30); assert t_ins > t1                    # better insulation keeps heat
    _, t_hi = pipeline_march(_pipe(thermal_model='heat_loss', overall_u_w_m2k=5), 4000, 40, 30); assert t_hi > t1                       # higher rate cools less
    _, t_in = pipeline_march(_pipe(thermal_model='heat_loss'), 1500, 40, 30, t_in=60); assert t_in < t1


def test_cold_pipeline_raises_viscous_losses():
    a = pipeline_dp_bar(_pipe(), 1500, 40, 30); b = pipeline_dp_bar(_pipe(thermal_model='heat_loss', overall_u_w_m2k=15, ambient_temperature_c=4), 1500, 40, 30); assert b != a


def test_riser_elevation_cools_fluid():
    base = dict(thermal_model='heat_loss', overall_u_w_m2k=0.0, include_jt=False)
    e = _pipe(**base); e['elevation_change_m'] = 800; _, t = pipeline_march(e, 1500, 40, 30); assert 80 - t > 2.5


def _hot_case():
    n, e = demo_field_case()
    for x in e:
        if x['kind'] == 'pipeline': x['params'].update({'thermal_model': 'heat_loss', 'ambient_temperature_c': 4, 'overall_u_w_m2k': 5})
    for x in n:
        if x['kind'] == 'well': x['params'].update({'thermal_model': 'ramey', 'bottomhole_temperature_c': 110, 'surface_temperature_c': 4, 'overall_u_w_m2k': 10})
    return n, e


def test_network_pass_and_feedback():
    from solver.v21 import solve_v21
    n, e = _hot_case(); assert has_thermal(n, e)
    r = with_thermal(solve_v21)(n, e); t = r[2]['thermal']
    assert t['converged'] and t['iterations'] <= 4 and not t['warnings']
    tn = t['node_temperature_c']; assert tn['SEP'] < tn['M1'] < min(tn['P1'], tn['P2'], tn['P3'])
    # mixing at the manifold: between the coldest and hottest incoming flowline outlets
    outs = [t['edge'][k]['t_out'] for k in ('FL-A', 'FL-B', 'FL-C')]; assert min(outs) <= tn['M1'] <= max(outs)
    assert all(v['wellhead_temperature_c'] > 30 for v in r[3].values() if 'wellhead_temperature_c' in v)


def test_wrapper_is_identity_without_thermal_elements():
    from solver.v21 import solve_v21
    n, e = demo_field_case(); a = solve_v21(n, e); b = with_thermal(solve_v21)(n, e)
    assert not has_thermal(n, e) and 'thermal' not in b[2] and a[1] == b[1]


def test_flow_assurance_uses_network_inlet_temperature_and_energy_balance():
    from network.flow_assurance_profile import assess_line, ProfileFAConfig
    from solver.v21 import solve_v21
    n, e = _hot_case(); r = with_thermal(solve_v21)(n, e); p, q, info, d = r
    line = next(x for x in e if x['id'] == 'TRUNK'); out = assess_line(line, q['TRUNK'], p[line['source']], info=info, cfg=ProfileFAConfig())
    ts = [row['temperature_c'] for row in out['profile']]
    assert ts[0] == pytest.approx(info['thermal']['edge']['TRUNK']['t_in'], abs=1e-6) and ts[-1] < ts[0]
