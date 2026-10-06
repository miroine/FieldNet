"""Relative permeability, fractional flow, Buckley-Leverett and tank integration."""
import copy
import math
import numpy as np
import pytest
from physics.relperm import RelPerm, relperm_defaults, bl_front, recovery_vs_pvi, DEFAULT_RELPERM
from network.reservoir_mb import Tank


def corey(**kw):
    p = dict(DEFAULT_RELPERM); p.update(kw); return RelPerm.from_params(p)


def test_corey_endpoints_and_monotonicity():
    r = corey()
    assert r.krw(r.swc) == pytest.approx(0.0) and r.kro(r.swc) == pytest.approx(r.kro_max)
    assert r.krw(1 - r.sorw) == pytest.approx(r.krw_max) and r.kro(1 - r.sorw) == pytest.approx(0.0)
    sw = np.linspace(0, 1, 101)
    assert np.all(np.diff(r.krw(sw)) >= -1e-12) and np.all(np.diff(r.kro(sw)) <= 1e-12)
    assert r.krw(0.0) == 0.0 and r.kro(1.0) == 0.0  # clamped outside the mobile range


def test_let_endpoints_and_monotonicity():
    r = RelPerm.from_params(relperm_defaults('let'))
    assert r.krw(r.swc) == pytest.approx(0.0) and r.kro(r.swc) == pytest.approx(r.kro_max)
    assert r.krw(1 - r.sorw) == pytest.approx(r.krw_max) and r.kro(1 - r.sorw) == pytest.approx(0.0)
    sw = np.linspace(0.2, 0.75, 80)
    assert np.all(np.diff(r.krw(sw)) >= -1e-12) and np.all(np.diff(r.kro(sw)) <= 1e-12)


def test_let_differs_from_corey_default_shapes():
    # LET with E=1, L=T=2 is NOT the quadratic Corey curve (do not assert an identity that is false)
    c = corey(nw=2.0, no=2.0); l = RelPerm.from_params({**relperm_defaults('let'), 'nw': 2.0})
    assert abs(float(c.krw(0.5)) - float(l.krw(0.5))) > 1e-3


def test_table_matches_source_curve_and_validates():
    src = corey(); d = relperm_defaults('table'); r = RelPerm.from_params(d)
    assert r.swc == pytest.approx(0.2) and r.sorw == pytest.approx(0.25)
    assert float(r.krw(0.5)) == pytest.approx(float(src.krw(0.5)), abs=0.01)
    assert np.all(np.diff(r.krw(np.linspace(0.2, 0.75, 50))) >= -1e-12)

    def bad(rows, msg):
        with pytest.raises(ValueError) as e: RelPerm.from_params({'model': 'table', 'table': rows})
        assert msg in str(e.value)
    ok = [{'sw': 0.2, 'krw': 0.0, 'kro': 0.9}, {'sw': 0.5, 'krw': 0.1, 'kro': 0.3}, {'sw': 0.8, 'krw': 0.4, 'kro': 0.0}]
    RelPerm.from_params({'model': 'table', 'table': ok})
    bad(ok[:2], 'at least 3')
    bad([ok[0], ok[2], ok[1]], 'strictly increasing')
    bad([ok[0], {'sw': 0.5, 'krw': 0.1, 'kro': 0.95}, ok[2]], 'kro must be non-increasing')
    bad([ok[0], {'sw': 0.5, 'krw': -0.1, 'kro': 0.3}, ok[2]], 'within [0, 1]')
    bad([ok[0], {'sw': 0.5, 'krw': 0.5, 'kro': 0.3}, {'sw': 0.8, 'krw': 0.4, 'kro': 0.0}], 'krw must be non-decreasing')
    bad([{'sw': 0.2, 'krw': 0.1, 'kro': 0.9}, ok[1], ok[2]], 'krw = 0')
    bad([ok[0], ok[1], {'sw': 0.8, 'krw': 0.4, 'kro': 0.2}], 'kro = 0')


def test_invalid_parameters_raise_readable_errors():
    with pytest.raises(ValueError): RelPerm.from_params({'model': 'banana'})
    with pytest.raises(ValueError): corey(swc=0.6, sorw=0.5)
    with pytest.raises(ValueError): corey(nw='abc')


def test_fractional_flow_limits_and_surface_conversion():
    r = corey()
    assert r.fractional_flow_w(r.swc) == pytest.approx(0.0)
    assert r.fractional_flow_w(1 - r.sorw) == pytest.approx(1.0)
    sw = np.linspace(r.swc, 1 - r.sorw, 40)
    assert np.all(np.diff(r.fractional_flow_w(sw)) >= -1e-12)
    # hand calculation at sw=0.5: Swn=0.3/0.55
    s = 0.3 / 0.55; krw = 0.35 * s ** 2.5; kro = 0.9 * (1 - s) ** 2
    fw = 1 / (1 + kro / krw * (0.5 / 2.0))
    assert r.fractional_flow_w(0.5) == pytest.approx(fw)
    wc = r.water_cut_surface(0.5, bo=1.3)
    assert wc == pytest.approx((fw / 1.0) / (fw / 1.0 + (1 - fw) / 1.3))
    assert wc > fw  # shrinkage of oil raises the surface water cut
    assert r.water_cut_surface(0.5, bo=1.0) == pytest.approx(fw)


def test_mobility_ratio_and_summary_and_curves():
    r = corey()
    assert r.mobility_ratio == pytest.approx((0.35 / 0.5) / (0.9 / 2.0))
    s = r.summary(); assert s['model'] == 'corey' and s['mobility_ratio'] == pytest.approx(r.mobility_ratio) and s['pvi_breakthrough'] > 0
    df = r.curves(30); assert {'Sw', 'krw', 'kro', 'fw'} <= set(df.columns) and df['Sw'].iloc[0] == 0 and df['Sw'].iloc[-1] == 1


def test_gas_oil_curves():
    r = corey(gas={'sgc': 0.05, 'sorg': 0.15, 'krg_max': 0.8, 'ng': 2.0, 'nog': 2.0})
    assert r.krg(0.05) == 0.0 and r.krog(0.05) == pytest.approx(r.kro_max)
    sg_max = 1 - r.swc - 0.15
    assert r.krg(sg_max) == pytest.approx(0.8) and r.krog(sg_max) == pytest.approx(0.0)
    with pytest.raises(ValueError): corey().krg(0.2)


def test_welge_quadratic_corey_front_matches_analytic():
    # Quadratic Corey, unit end points, swc=sorw=0: fw = M s^2/(M s^2 + (1-s)^2); Welge tangent gives
    # s_f = 1/sqrt(1+M) (e.g. Dake, Fundamentals of Reservoir Engineering, ch. 10; Lake, Enhanced Oil Recovery)
    for M in (0.5, 1.0, 4.0):
        r = corey(swc=0.0, sorw=0.0, krw_max=1.0, kro_max=1.0, nw=2.0, no=2.0, mu_w_cp=1.0, mu_o_cp=M)
        assert r.mobility_ratio == pytest.approx(M)
        f = bl_front(r)
        sf = 1 / math.sqrt(1 + M)
        assert f['sw_front'] == pytest.approx(sf, abs=1e-3)
        assert f['pvi_bt'] == pytest.approx(1 / f['dfw_dsw'])
        assert f['sw_avg_bt'] == pytest.approx(f['pvi_bt'] + f['swi'])
        assert f['fw_front'] / (f['sw_front'] - f['swi']) == pytest.approx(f['dfw_dsw'])


def test_welge_piston_for_unit_mobility_straight_lines():
    r = corey(swc=0.2, sorw=0.2, krw_max=1.0, kro_max=1.0, nw=1.0, no=1.0, mu_w_cp=1.0, mu_o_cp=1.0)
    f = bl_front(r)
    assert f['sw_front'] == pytest.approx(0.8, abs=1e-3) and f['pvi_bt'] == pytest.approx(0.6, rel=1e-3)
    df = recovery_vs_pvi(r, pvi_max=1.5, n=151)
    # piston displacement: recovery = PVI/(1-swi) until breakthrough at 0.6 PV, then plateau at 0.6/0.8 = 0.75 of OIIP
    assert df['Recovery'].iloc[-1] == pytest.approx(0.75, abs=1e-3)
    pre = df[df['PVI'] <= 0.55]; assert np.all(pre['WC_res'] == 0)
    assert np.allclose(pre['Recovery'], pre['PVI'] / 0.8)


def test_recovery_vs_pvi_physical_trends():
    r = corey(); df = recovery_vs_pvi(r, bo=1.2, pvi_max=4.0, n=300)
    assert np.all(np.diff(df['Recovery']) >= -1e-9) and np.all(np.diff(df['WC_res']) >= -1e-9)
    bt = bl_front(r)['pvi_bt']
    assert df[df['PVI'] < bt * 0.95]['WC_res'].max() == 0.0 and df[df['PVI'] > bt * 1.3]['WC_res'].min() > 0.5
    assert df['Recovery'].iloc[-1] <= (1 - r.swc - r.sorw) / (1 - r.swc) + 1e-9
    assert np.all(df['WC_surface'] >= df['WC_res'] - 1e-12)  # bo > 1
    # unfavourable mobility ratio breaks through earlier
    assert bl_front(r, mu_o=20.0)['pvi_bt'] < bl_front(r, mu_o=0.5)['pvi_bt']


# ---- tank integration ---------------------------------------------------------------------------
def tnode(**kw):
    p = {'fluid_phase': 'oil', 'reservoir_pressure_bar': 250.0, 'stoiip_sm3': 10e6, 'bubble_point_bar': 150.0, 'swi': 0.2}
    p.update(kw); return {'id': 'T', 'kind': 'reservoir', 'name': 'T', 'pressure_bar': None, 'params': p}


def test_tank_without_relperm_is_unchanged():
    n = tnode(); n2 = copy.deepcopy(n)
    a = Tank(n); b = Tank(n2)
    for t in (a, b):
        t.step(50000, 2000, 5e6, 1000, 0, 30)
    wp = {'water_cut': 0.1, 'gor_sm3sm3': 100.0}
    assert a.well_overrides(wp) == b.well_overrides(wp) and a.rp is None
    assert 'Sw avg [-]' not in a.row()
    # relperm present but water_cut_mode='screening' keeps the S-curve exactly
    c = Tank(tnode(relperm=relperm_defaults('corey'), water_cut_mode='screening')); c.step(50000, 2000, 5e6, 1000, 0, 30)
    assert c.well_overrides(wp)['water_cut'] == a.well_overrides(wp)['water_cut']


def test_tank_relperm_water_cut_rises_with_injection_and_is_capped():
    t = Tank(tnode(stoiip_sm3=1e6, relperm=relperm_defaults('corey'), aquifer_pi_m3d_bar=0.0))
    wp = {'water_cut': 0.0}
    assert t.well_overrides(wp)['water_cut'] == pytest.approx(0.0, abs=1e-9)
    assert t.row()['Sw avg [-]'] == pytest.approx(0.2)
    prev = 0.0
    for _ in range(8):
        t.step(1000, 0, 0, water_inj_m3=150000.0, dt_days=30)
        wc = t.well_overrides(wp)['water_cut']; assert wc >= prev - 1e-12; prev = wc
    assert prev > 0.3 and t.sw_avg > 0.2 and t.sw_avg <= 1 - t.rp.sorw + 1e-12
    # producing the water out again lowers Sw_avg (net retained water falls)
    sw1 = t.sw_avg; t.step(1000, 600000.0, 0, 0, 0, 30); assert t.sw_avg < sw1


def test_tank_sweep_efficiency_scales_saturation():
    a = Tank(tnode(relperm=relperm_defaults('corey'))); b = Tank(tnode(relperm=relperm_defaults('corey'), sweep_efficiency=0.5))
    for t in (a, b): t.step(1000, 0, 0, water_inj_m3=300000.0, dt_days=30)
    assert (b.sw_avg - 0.2) == pytest.approx(2 * (a.sw_avg - 0.2), rel=1e-6)


def test_tank_relperm_gor_response_below_bubble_point():
    rp = {**relperm_defaults('corey'), 'gas': {'sgc': 0.03, 'sorg': 0.1, 'krg_max': 0.8, 'ng': 2.0, 'nog': 2.0}}
    t = Tank(tnode(relperm=rp, bubble_point_bar=200.0, rsi_sm3_sm3=100.0, stoiip_sm3=2e6, reservoir_pressure_bar=200.0))
    wp = {'gor_sm3sm3': 100.0}
    for _ in range(12): t.step(60000, 0, 60000 * 100.0, 0, 0, 30)
    assert t.p < t.pb
    base = Tank(tnode(relperm={k: v for k, v in rp.items() if k != 'gas'}, bubble_point_bar=200.0, rsi_sm3_sm3=100.0, stoiip_sm3=2e6, reservoir_pressure_bar=200.0))
    for _ in range(12): base.step(60000, 0, 60000 * 100.0, 0, 0, 30)
    assert t.well_overrides(wp)['gor_sm3sm3'] >= base.well_overrides(wp)['gor_sm3sm3'] - 1e-9


def test_forecast_with_relperm_tank_runs_and_reports_sw_avg():
    from network.examples import demo_field_case
    from network.forecast import run_forecast
    nodes, edges = demo_field_case()
    next(n for n in nodes if n['id'] == 'T1')['params']['relperm'] = relperm_defaults('corey')
    fc = run_forecast(nodes, edges, '2026-01-01', 2.0, 90)
    sw = [r['Sw avg [-]'] for r in fc['tanks']]
    assert sw[-1] > sw[0] >= 0.2 - 1e-12 and fc['recovery'][0]['Sw avg [-]'] == pytest.approx(sw[-1])
    wc = [r['Water cut [%]'] for r in fc['field']]; assert min(wc) >= 6.0  # initial well water cuts are preserved (fw ~ 0 at Sw ~ 0.26 in this large tank)
