"""v32.6 round 7: Darcy IPR, single palette items, registry-driven schedule events, tank events, input and unit consistency."""
import math, inspect
import pytest
from network.examples import demo_field_case


# ---- Darcy ------------------------------------------------------------------------------------------------------------
def _w(**kw):
    p = dict(darcy_perm_md=100, darcy_net_pay_m=30, darcy_drainage_radius_m=500, darcy_wellbore_radius_m=0.1, darcy_visc_cp=1.0, darcy_bo=1.2, skin=0.0)
    p.update(kw); return p


def test_darcy_vertical_matches_hand_calculation():
    from physics.darcy_ipr import darcy_ipr
    pi = darcy_ipr(_w(), 250.0, 'oil')['pi']
    k, h, mu, B = 100e-3 * 9.869233e-13 / 1.0, 30.0, 1e-3, 1.2           # m2, m, Pa.s
    expect = 2 * math.pi * k * h / (mu * B * (math.log(500 / 0.1) - 0.75)) * 1e5 * 86400
    assert pi == pytest.approx(expect, rel=0.01) and pi == pytest.approx(17.24, rel=0.01)


def test_horizontal_beats_vertical_and_layers_add():
    from physics.darcy_ipr import darcy_ipr
    v = darcy_ipr(_w(), 250.0, 'oil')['pi']
    h = darcy_ipr(_w(darcy_orientation='horizontal', darcy_lateral_length_m=1000, darcy_kv_kh=1.0), 250.0, 'oil')['pi']
    assert h > 5 * v
    two = darcy_ipr(_w(darcy_layers=[{'perm_md': 100, 'net_pay_m': 15}, {'perm_md': 100, 'net_pay_m': 15}]), 250.0, 'oil')['pi']
    assert two == pytest.approx(v, rel=0.02)
    lowkv = darcy_ipr(_w(darcy_orientation='horizontal', darcy_lateral_length_m=1000, darcy_kv_kh=0.1), 250.0, 'oil')['pi']
    assert lowkv < h


def test_well_settings_uses_darcy_only_when_switched_on():
    from physics.well_model import well_settings
    p = {'reservoir_pressure_bar': 250.0, 'ipr_model': 'PI', 'pi_m3d_bar': 10.0, **_w()}
    off = well_settings(dict(p))
    on = well_settings({**p, 'darcy': True})
    assert off['pi'] == pytest.approx(10.0, rel=1e-6) and on['pi'] == pytest.approx(17.24, rel=0.02) and on.get('darcy')
    hz = well_settings({**p, 'darcy': True, 'darcy_orientation': 'horizontal', 'darcy_lateral_length_m': 1000, 'darcy_kv_kh': 1.0})
    assert hz['pi'] > 5 * on['pi']


def test_darcy_gas_well():
    from physics.darcy_ipr import darcy_ipr
    r = darcy_ipr(_w(darcy_perm_md=10, darcy_net_pay_m=50), 300.0, 'gas')
    assert 20 < r['gas_c'] < 200 and r['gas_n'] == 1.0


# ---- palette: one item per family ---------------------------------------------------------------------------------------
def test_palette_has_single_tank_well_separator_items():
    from network.features import palette
    items = [i for g in palette() for i in g['items']] if isinstance(palette(), list) else []
    names = [i.get('label') or i.get('name') for i in items]
    for want in ('Tank', 'Well', 'Separator'):
        assert names.count(want) == 1, names
    for gone in ('Oil producer', 'Gas producer', 'Water injector', 'Gas injector', 'Separator stage', 'Reservoir tank'):
        assert gone not in names


# ---- registry and schedule ------------------------------------------------------------------------------------------
def test_registry_is_consistent_and_feeds_the_schedule():
    from network import param_registry as pr
    from ui import schedule_builder as sb
    ids = [t.id for t in sb.EVENT_CATALOG]
    assert len(ids) == len(set(ids)) and ids[-1] == 'custom'
    for prm in pr.PARAMS:
        assert prm.unit in sb._UNITS, prm
        if prm.min is not None and prm.max is not None:
            assert prm.min <= prm.max, prm
            if prm.default is not None:
                assert prm.min <= prm.default <= prm.max, prm
        field = prm.key if prm.top_level else 'params.' + prm.key
        assert any(t.field == field for t in sb.EVENT_CATALOG), field            # every parameter can be scheduled


def test_schedule_event_in_field_units_round_trips():
    from ui import schedule_builder as sb
    n, e = demo_field_case(); w = next(x for x in n if x['kind'] == 'well')
    et = sb.CATALOG_BY_ID['p_darcy_perm_md_well']
    ev = sb.build_event(et, w['id'], 250.0, '2027-01-01')
    assert ev.field == 'params.darcy_perm_md' and ev.value == 250.0
    et = sb.CATALOG_BY_ID['p_temperature_c_well']
    assert sb.build_event(et, w['id'], 158.0, '2027-01-01', 'field').value == pytest.approx(70.0)
    et = sb.CATALOG_BY_ID['p_gor_sm3sm3_well']
    assert sb.build_event(et, w['id'], 110 * 5.614583, '2027-01-01', 'field').value == pytest.approx(110.0, rel=1e-3)


def test_tank_parameter_event_reaches_the_tank():
    from network.forecast import run_forecast
    n, e = demo_field_case(); t = next(x for x in n if x['kind'] == 'reservoir')
    base = run_forecast(n, e, '2026-01-01', years=0.6, step_days=60)
    ev = [{'date': '2026-01-01', 'target_id': t['id'], 'field': 'params.aquifer_pi_m3d_bar', 'value': 5000.0}]
    boosted = run_forecast(n, e, '2026-01-01', years=0.6, step_days=60, events=ev)
    pb = [r[k] for r in base['tanks'][-1:] for k in r if 'ressure' in k][:1]
    pe = [r[k] for r in boosted['tanks'][-1:] for k in r if 'ressure' in k][:1]
    assert pb and pe and pe[0] > pb[0] + 1e-6


# ---- consistency and units ----------------------------------------------------------------------------------------------
def test_demo_model_is_clean_and_unit_slips_are_named():
    from network.input_check import check_inputs
    n, e = demo_field_case(); assert not [f for f in check_inputs(n, e) if f['severity'] == 'error']
    w = next(x for x in n if x['kind'] == 'well'); w['params']['tubing_id_m'] = 3.5
    f = [x for x in check_inputs(n, e) if x['field'] == 'params.tubing_id_m']
    assert f and f[0]['severity'] == 'error' and 'inch' in f[0]['message']


def test_input_check_flags_conflicts():
    from network.input_check import check_inputs
    n, e = demo_field_case(); w = next(x for x in n if x['kind'] == 'well'); t = next(x for x in n if x['kind'] == 'reservoir')
    w['params']['reservoir_pressure_bar'] = t['params']['reservoir_pressure_bar'] + 40
    w['params']['reservoir_id'] = 'nope'
    msgs = ' '.join(f['message'] for f in check_inputs(n, e))
    assert 'does not exist' in msgs
    w['params']['reservoir_id'] = t['id']; t['params']['min_pressure_bar'] = 9999.0
    assert any('abandonment' in f['message'] for f in check_inputs(n, e))


@pytest.mark.parametrize('profile', ['norwegian_si', 'field'])
def test_every_unit_pair_round_trips(profile):
    from physics import unit_system as us
    pairs = [n[:-len('_to_display')] for n in dir(us) if n.endswith('_to_display')]
    assert len(pairs) >= 9
    for q in pairs:
        to, frm = getattr(us, q + '_to_display'), getattr(us, q + '_from_display')
        for v in (0.5, 1.0, 37.5, 1234.0):
            assert frm(to(v, profile), profile) == pytest.approx(v, rel=1e-9), (q, profile, v)


def test_known_conversion_constants():
    from physics import unit_system as us
    assert us.pressure_to_display(1.0, 'field') == pytest.approx(14.5038, rel=1e-4)
    assert us.length_to_display(1.0, 'field') == pytest.approx(3.28084, rel=1e-5)
    assert us.diameter_to_display(0.0889, 'norwegian_si') == pytest.approx(88.9) and us.diameter_to_display(0.0254, 'field') == pytest.approx(1.0)
    assert us.liquid_rate_to_display(1.0, 'field') == pytest.approx(6.2898, rel=1e-4)
    assert us.temperature_to_display(100.0, 'field') == pytest.approx(212.0)
    assert us.gor_to_display(1.0, 'field') == pytest.approx(5.6146, rel=1e-3)
    assert us.pi_to_display(1.0, 'field') == pytest.approx(6.2898 / 14.5038, rel=1e-3)
