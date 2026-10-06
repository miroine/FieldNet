import copy, pytest
from network.reservoir_mb import Tank, communication_transfers
from network.examples import demo_case
from network.forecast import run_forecast
from ui.graph_contract import normalize_graph


def tank(tid, p=250.0, **kw):
    prm = {'fluid_phase': 'oil', 'reservoir_pressure_bar': p, 'stoiip_sm3': 10e6, 'bubble_point_bar': 100.0}; prm.update(kw)
    return {'id': tid, 'kind': 'reservoir', 'name': tid, 'params': prm}


def test_pressures_equalise_and_volume_is_conserved():
    a, b = Tank(tank('A', 250.0, communication=[{'to': 'B', 'transmissibility_m3d_bar': 500.0}])), Tank(tank('B', 200.0))
    tanks = {'A': a, 'B': b}
    for _ in range(200):
        x = communication_transfers(tanks, 5.0)
        assert x['A'] + x['B'] == pytest.approx(0.0, abs=1e-6)
        for k, v in x.items(): tanks[k].exchange(v)
    assert abs(a.p - b.p) < 0.5 and 200.0 < b.p < 250.0 and a.xin == pytest.approx(-b.xin, rel=1e-9)


def test_no_overshoot_with_huge_step_and_max_transfer_respected():
    a, b = Tank(tank('A', 250.0, communication=[{'to': 'B', 'transmissibility_m3d_bar': 1e9}])), Tank(tank('B', 200.0))
    x = communication_transfers({'A': a, 'B': b}, 1000.0)
    a.exchange(x['A']); b.exchange(x['B']); assert a.p >= b.p - 1e-6
    c = Tank(tank('C', 250.0, communication=[{'to': 'D', 'transmissibility_m3d_bar': 1e9, 'max_transfer_m3d': 10.0}])); d = Tank(tank('D', 150.0))
    assert abs(communication_transfers({'C': c, 'D': d}, 1.0)['D']) <= 10.0 + 1e-9


def test_gas_tank_exchange_changes_pressure():
    g = Tank({'id': 'G', 'kind': 'reservoir', 'params': {'fluid_phase': 'gas', 'giip_sm3': 1e9, 'reservoir_pressure_bar': 250.0, 'temperature_c': 90.0}})
    p0 = g.p; g.exchange(-5e5); assert g.p < p0; g.exchange(5e5); assert g.p == pytest.approx(p0, rel=1e-3)


def test_forecast_with_two_communicating_tanks_supports_the_depleted_one():
    n, e = demo_case(); n.append(tank('T1', 240.0, stoiip_sm3=3e6)); n.append(tank('T2', 240.0, stoiip_sm3=60e6))
    n[0]['params']['reservoir_id'] = 'T1'; n[1]['params']['reservoir_id'] = 'T1'
    alone = run_forecast(copy.deepcopy(n), e, '2026-01-01', 2, 60)
    n2 = copy.deepcopy(n); next(x for x in n2 if x['id'] == 'T2')['params']['communication'] = [{'to': 'T1', 'transmissibility_m3d_bar': 2000.0}]
    n2, e2, _ = normalize_graph(n2, e); linked = run_forecast(n2, e2, '2026-01-01', 2, 60)
    p1 = lambda fc: fc['tanks'][-1]['Pressure [bar]'] if False else [r for r in fc['tank_rows' if 'tank_rows' in fc else 'tanks'] if r['Tank ID'] == 'T1'][-1]['Pressure [bar]']
    assert p1(linked) > p1(alone) + 1.0
    assert linked['field'][-1]['Cumulative oil [Sm3]'] >= alone['field'][-1]['Cumulative oil [Sm3]'] * 0.999


def test_normalize_drops_invalid_links():
    n = [tank('A', communication=[{'to': 'A'}, {'to': 'ZZ'}, {'to': 'B', 'transmissibility_m3d_bar': 1}]), tank('B', communication=[{'to': 'A'}])]
    n2, _, issues = normalize_graph(n, [])
    assert len(n2[0]['params']['communication']) == 1 and 'communication' not in n2[1]['params'] and len(issues) == 3
