import copy, datetime as dt, pytest, pandas as pd
from tests.hubfix import demo_hub
from network import mb_analysis as mb
from network.reservoir_mb import Tank


def _tank(**kw):
    p = {'fluid_phase': 'oil', 'reservoir_pressure_bar': 300.0, 'stoiip_sm3': 20e6, 'boi_rm3_sm3': 1.3, 'rsi_sm3_sm3': 100.0, 'bubble_point_bar': 150.0, 'ct_1bar': 2e-4, 'swi': 0.2, 'temperature_c': 90.0, 'gas_sg': 0.7}; p.update(kw)
    return Tank({'id': 'T', 'name': 'T', 'params': p})


def _depletion_series(tank, qo=20000.0, steps=24, dt_d=30, gor=100.0, winj_frac=0.0):
    rows = [{'Date': pd.Timestamp('2030-01-01'), 'Pressure [bar]': tank.p, **{c: 0.0 for c in mb.CUM}}]
    for i in range(steps):
        o = qo * dt_d; tank.step(o, o * 0.05, o * gor, o * 1.3 * winj_frac, 0.0, dt_d)
        rows.append({'Date': rows[0]['Date'] + pd.Timedelta(days=dt_d * (i + 1)), 'Pressure [bar]': tank.p, 'Cum oil [Sm3]': tank.np, 'Cum gas [Sm3]': tank.gp, 'Cum water [m3]': tank.wp,
                     'Cum water inj [m3]': tank.winj, 'Cum gas inj [Sm3]': 0.0, 'Aquifer influx [m3]': tank.we, 'Net communication [m3]': 0.0})
    return pd.DataFrame(rows)


def test_recovers_stoiip_volumetric_above_pb():
    t = _tank(aquifer_pi_m3d_bar=0.0); s = _depletion_series(t, qo=15000.0, steps=24)
    r = mb.analyse(s, _tank(aquifer_pi_m3d_bar=0.0), with_aquifer=False)
    assert r['fit']['N'] == pytest.approx(20e6, rel=0.05) and r['fit']['r2'] > 0.98


def test_recovers_N_and_aquifer_together():
    t = _tank(aquifer_pi_m3d_bar=200.0); s = _depletion_series(t, qo=15000.0, steps=30)
    r = mb.analyse(s, _tank(aquifer_pi_m3d_bar=200.0), with_aquifer=True)
    assert r['fit']['N'] == pytest.approx(20e6, rel=0.12) and r['fit']['J'] == pytest.approx(200.0, rel=0.3)


def test_known_influx_mode():
    t = _tank(aquifer_pi_m3d_bar=200.0); s = _depletion_series(t, qo=15000.0, steps=30)
    r = mb.analyse(s, _tank(aquifer_pi_m3d_bar=200.0), known_influx=True)
    assert r['fit']['N'] == pytest.approx(20e6, rel=0.08)


def test_identifiability_warning_when_pressure_supported():
    t = _tank(aquifer_pi_m3d_bar=0.0); s = _depletion_series(t, qo=3000.0, steps=6, winj_frac=1.0)
    r = mb.analyse(s, _tank(aquifer_pi_m3d_bar=0.0))
    assert 'not identifiable' in r['fit'].get('note', '') or 'error' in r['fit']


def test_voidage_replacement_with_injection():
    t = _tank(aquifer_pi_m3d_bar=0.0); s = _depletion_series(t, qo=10000.0, steps=12, winj_frac=1.0)
    r = mb.analyse(s, _tank(aquifer_pi_m3d_bar=0.0))
    iv = r['voidage']; assert (iv['Voidage [rm3]'] > 0).all() and 0.5 < iv['Cum VRR'].iloc[-1] < 1.6
    va = r['voidage_annual']; assert va['Voidage [rm3]'].sum() == pytest.approx(iv['Voidage [rm3]'].sum(), rel=1e-6)


def test_drive_indices_close_for_model_data():
    t = _tank(aquifer_pi_m3d_bar=100.0); s = _depletion_series(t, qo=15000.0, steps=24)
    r = mb.analyse(s, _tank(aquifer_pi_m3d_bar=100.0), known_influx=True)
    last = r['drive'].iloc[-1]; assert last['Closure'] == pytest.approx(1.0, abs=0.05)


def test_forecast_series_prepends_initial_and_drops_dt0():
    n, e, sol, fc, hub = demo_hub(); s = mb.series_from_forecast(fc, 'T1')
    assert s['Cum oil [Sm3]'].iloc[0] == 0 and not s['Date'].duplicated().any()
    assert s['Cum oil [Sm3]'].iloc[-1] == pytest.approx(fc['tanks'][-1]['Cum oil [Sm3]'])


def test_forecast_analysis_runs_on_demo():
    from network.reservoir_mb import tanks_from_nodes
    n, e, sol, fc, hub = demo_hub(); r = mb.analyse(mb.series_from_forecast(fc, 'T1'), tanks_from_nodes(n)['T1'], known_influx=True)
    assert len(r['balance']) >= 3 and set(r['lines']) >= {'havlena_odeh', 'campbell', 'cole'} and len(r['voidage_annual']) >= 2


def test_gas_tank_pz_line():
    p = {'fluid_phase': 'gas', 'reservoir_pressure_bar': 300.0, 'giip_sm3': 5e9, 'swi': 0.2, 'temperature_c': 100.0, 'gas_sg': 0.65, 'aquifer_pi_m3d_bar': 0.0}
    t = Tank({'id': 'G', 'name': 'G', 'params': p}); rows = [{'Date': pd.Timestamp('2030-01-01'), 'Pressure [bar]': t.p, **{c: 0.0 for c in mb.CUM}}]
    for i in range(20):
        t.step(0, 0, 5e6 * 30, 0, 0, 30)
        rows.append({'Date': rows[0]['Date'] + pd.Timedelta(days=30 * (i + 1)), 'Pressure [bar]': t.p, 'Cum oil [Sm3]': 0.0, 'Cum gas [Sm3]': t.gp, 'Cum water [m3]': 0.0, 'Cum water inj [m3]': 0.0, 'Cum gas inj [Sm3]': 0.0, 'Aquifer influx [m3]': 0.0, 'Net communication [m3]': 0.0})
    t2 = Tank({'id': 'G', 'name': 'G', 'params': p}); r = mb.analyse(pd.DataFrame(rows), t2, with_aquifer=False)
    assert r['pz']['G_apparent'] == pytest.approx(5e9, rel=0.1) and r['fit']['N'] == pytest.approx(5e9, rel=0.1)


def test_history_loader_flags_decreasing_cumulative():
    df = pd.DataFrame({'Date': ['2030-01-01', '2030-02-01', '2030-03-01'], 'Pressure [bar]': [300, 295, 290], 'Cum oil [Sm3]': [0, 100, 50]})
    assert mb.series_from_history(df).attrs.get('warnings')


def test_drive_basis_falls_back_to_input_when_pressure_supported():
    t = _tank(aquifer_pi_m3d_bar=0.0); s = _depletion_series(t, qo=3000.0, steps=6, winj_frac=1.0)
    r = mb.analyse(s, _tank(aquifer_pi_m3d_bar=0.0)); assert r['drive_basis'] == 'input' and r['drive_basis_N'] == pytest.approx(20e6)
    t = _tank(aquifer_pi_m3d_bar=0.0); r2 = mb.analyse(_depletion_series(t, qo=15000.0, steps=24), _tank(aquifer_pi_m3d_bar=0.0), with_aquifer=False); assert r2['drive_basis'] == 'fitted'
