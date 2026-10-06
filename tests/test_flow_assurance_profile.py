import pytest
from network.flow_assurance_profile import (ProfileFAConfig, assess_line, assess_network, hammerschmidt_depression_c, hammerschmidt_required_wt_pct,
                                            inhibitor_dose, towler_mokhatab_hydrate_temperature_c, hydrate_temperature_c, temperature_profile)
from physics.flow_assurance import api14e_erosional_velocity_ms

KEYS = {'Component', 'Check', 'Value', 'Limit', 'Margin', 'Status'}


def _line(D=0.3, L=6000.0, profile=None, **pp):
    prm = {'temperature_c': 60.0, 'water_cut': 0.3, 'gor_sm3sm3': 150.0, 'api': 35.0, 'gas_sg': 0.75}
    prm.update(pp)
    if profile:
        prm['profile'] = profile
    return {'id': 'L', 'source': 'A', 'target': 'B', 'kind': 'pipeline', 'length_m': L, 'diameter_m': D, 'roughness_m': 4.5e-5,
            'elevation_change_m': 0.0, 'params': prm}


def _by(rows, frag):
    return next(r for r in rows if frag in r['Check'])


def test_hammerschmidt_values_and_inverse():
    assert hammerschmidt_depression_c(10, 'MeOH') == pytest.approx(2335 * 10 / (1.8 * 32.04 * 90), rel=1e-12)
    assert hammerschmidt_depression_c(10, 'MeOH') == pytest.approx(4.50, abs=0.01)
    assert hammerschmidt_depression_c(30, 'MEG') == pytest.approx(10.36, abs=0.01)
    for inh in ('MEG', 'MeOH'):
        for d in (1.0, 5.0, 12.0):
            assert hammerschmidt_depression_c(hammerschmidt_required_wt_pct(d, inh), inh) == pytest.approx(d, rel=1e-9)
    assert hammerschmidt_required_wt_pct(0.0) == 0.0


def test_inhibitor_dose_mass_balance_and_limits():
    d = inhibitor_dose(10.0, 100.0, 'MEG')            # 100 m3/d water = 100 000 kg/d
    w = d['wt_pct']
    assert d['kg_d'] / (d['kg_d'] + 1e5) == pytest.approx(w / 100.0)
    assert d['l_d'] == pytest.approx(d['kg_d'] / 1113.0 * 1000.0)
    assert d['within_limit'] and not inhibitor_dose(60.0, 100.0, 'MeOH')['within_limit']
    with pytest.raises(ValueError):
        inhibitor_dose(5, 10, 'brine')


def test_hydrate_temperature_monotonic_and_plausible():
    t = [towler_mokhatab_hydrate_temperature_c(p, 0.7) for p in (10, 20, 40, 60)]
    assert all(a < b for a, b in zip(t, t[1:]))
    assert towler_mokhatab_hydrate_temperature_c(40, 0.9) > towler_mokhatab_hydrate_temperature_c(40, 0.6)
    assert 12 < towler_mokhatab_hydrate_temperature_c(68.9, 0.6) < 20            # sanity band only, not a validation
    assert hydrate_temperature_c(30, 0.7, 'conservative') >= max(hydrate_temperature_c(30, 0.7, 'tm'), hydrate_temperature_c(30, 0.7, 'v19'))
    with pytest.raises(ValueError):
        hydrate_temperature_c(30, 0.7, 'nope')


def test_temperature_profile_cools_toward_ambient_and_monotone():
    rows = [{'x_m': 0.0, 'rho_kgm3': None, 'velocity_ms': None}] + [{'x_m': 1000.0 * i, 'rho_kgm3': 300.0, 'velocity_ms': 1.0} for i in range(1, 6)]
    t = temperature_profile(rows, 60.0, 4.0, 5.0, 0.3, 2200.0)
    assert t[0] == 60.0 and all(a > b for a, b in zip(t, t[1:])) and t[-1] > 4.0


def test_hot_line_passes_and_cold_low_rate_line_fails_hydrate():
    hot = assess_line(_line(), 8000.0, 30.0)
    h = _by(hot['rows'], 'Hydrate margin')
    assert KEYS <= set(h) and h['Status'] == 'OK' and h['Margin'] == pytest.approx(h['Value'] - 3.0)
    assert _by(hot['rows'], 'dose')['Value'] == 0.0
    cold = assess_line(_line(temperature_c=20.0), 200.0, 40.0)         # low rate -> cools to near ambient at 40 bar
    c = _by(cold['rows'], 'Hydrate margin')
    assert c['Status'] == 'Fail' and c['Value'] < 0 and cold['worst']['hydrate']['margin_c'] == pytest.approx(c['Value'])
    d = _by(cold['rows'], 'dose')
    assert d['Value'] > 0 and d['Status'] in ('Warning', 'Fail') and cold['worst']['inhibitor']['kg_d'] > 0
    # worst point is at the cold end of the line
    assert cold['worst']['hydrate']['x_m'] == pytest.approx(max(r['x_m'] for r in cold['profile']))


def test_dose_required_depression_closes_margin():
    cold = assess_line(_line(temperature_c=20.0), 200.0, 40.0)
    need = cold['worst']['inhibitor']['required_depression_c']
    w = min(cold['profile'], key=lambda r: r['hydrate_margin_c'])
    assert need == pytest.approx(3.0 - w['hydrate_margin_c'])


def test_no_water_gives_na():
    r = assess_line(_line(water_cut=0.0), 3000.0, 30.0)
    assert _by(r['rows'], 'Hydrate margin')['Status'] == 'n/a'
    assert not any('dose' in x['Check'] for x in r['rows'])


def test_wax_margin_only_with_wat():
    assert not any('Wax' in x['Check'] for x in assess_line(_line(), 3000.0, 30.0)['rows'])
    r = assess_line(_line(wat_c=45.0, temperature_c=50.0), 400.0, 30.0)
    w = _by(r['rows'], 'Wax')
    assert w['Status'] == 'Fail' and w['Value'] < 0
    ok = _by(assess_line(_line(wat_c=10.0), 3000.0, 30.0)['rows'], 'Wax')
    assert ok['Status'] == 'OK'
    cfg = ProfileFAConfig(wat_c=45.0)
    assert any('Wax' in x['Check'] for x in assess_line(_line(temperature_c=50.0), 400.0, 30.0, cfg=cfg)['rows'])


def test_erosional_ratio_matches_api14e_and_flags_small_pipe():
    r = assess_line(_line(D=0.1, L=2000.0), 6000.0, 30.0)
    e = _by(r['rows'], 'Erosional')
    pt = max((x for x in r['profile'] if x['erosional_ratio'] is not None), key=lambda x: x['erosional_ratio'])
    assert e['Value'] == pytest.approx(pt['velocity_ms'] / api14e_erosional_velocity_ms(pt['rho_kgm3'], 100.0))
    assert e['Status'] in ('Warning', 'Fail') and e['Value'] > 0.8
    big = _by(assess_line(_line(D=0.5), 300.0, 30.0)['rows'], 'Erosional')
    assert big['Status'] == 'OK' and big['Value'] < 0.1
    c150 = _by(assess_line(_line(D=0.1, L=2000.0, erosion_c_factor=200.0), 6000.0, 30.0)['rows'], 'Erosional')
    assert c150['Value'] == pytest.approx(e['Value'] / 2.0, rel=1e-6)


def test_terrain_slugging_trap_detected_and_low_flow_warns():
    prof = [{'x_m': 0, 'z_m': 0}, {'x_m': 2000, 'z_m': -30}, {'x_m': 4000, 'z_m': 10}, {'x_m': 6000, 'z_m': -40}, {'x_m': 8000, 'z_m': 0}]
    r = assess_line(_line(L=8000.0, profile=prof), 300.0, 30.0)
    t = _by(r['rows'], 'Terrain')
    assert t['Status'] == 'Warning' and t['Unit'].startswith('Fr') and r['worst']['terrain_slugging']['n_traps'] == 1
    assert 1000 < t['Location [m]'] < 3500                       # near the first low point (x = 2000)
    assert _by(r['rows'], 'Riser') is not None


def test_flat_line_has_no_slugging_flags():
    r = assess_line(_line(), 3000.0, 30.0)
    assert _by(r['rows'], 'Terrain')['Status'] == 'OK'
    assert not any(x['Check'].startswith('Riser') and x['Status'] == 'Warning' for x in r['rows'])


def test_reverse_flow_reverses_profile_geometry():
    prof = [{'x_m': 0, 'z_m': 0}, {'x_m': 3000, 'z_m': -100}, {'x_m': 6000, 'z_m': -100}, {'x_m': 6100, 'z_m': 0}]
    fwd = assess_line(_line(L=6100.0, profile=prof), 300.0, 30.0)
    rev = assess_line(_line(L=6100.0, profile=prof), -300.0, 30.0)
    xf = _by(fwd['rows'], 'Riser')['Location [m]']; xr = _by(rev['rows'], 'Riser')['Location [m]']
    assert xf > 2500          # forward: riser base near the far end of the 6 km line (after the 3 km descent)
    assert xr < 500           # reversed: steep 100 m descent first, so the low point / riser base is at the start


def test_zero_flow_is_na():
    r = assess_line(_line(), 0.0, 30.0)
    assert r['rows'][0]['Status'] == 'n/a' and r['profile'] == []


def test_assess_network_demo_case():
    from network.examples import demo_field_case
    from solver.v21 import solve_v21
    from ui.graph_contract import normalize_graph, solver_input
    n, e = demo_field_case(); n, e, _ = normalize_graph(n, e); ns, es = solver_input(n, e)
    res = solve_v21(ns, es, enforce_constraints=True)
    out = assess_network(ns, es, res)
    assert set(out['lines']) == {'FL-A', 'FL-B', 'FL-C', 'TRUNK'} and set(out['profiles']) == set(out['lines'])
    assert all(KEYS <= set(r) for r in out['rows'])
    assert {r['Status'] for r in out['rows']} <= {'OK', 'Warning', 'Fail', 'n/a'}
    assert len(assess_network(ns, es, res, edge_ids={'TRUNK'})['lines']) == 1
