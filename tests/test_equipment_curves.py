"""Tests for network/equipment_curves.py."""
import pytest
from network.examples import demo_field_case
from network.equipment_curves import (parse_pump_curve_csv, parse_compressor_curve_csv, check_operating_point, check_network_equipment)
from solver.v21 import solve_v21

PUMP = """# demo pump
Rate (m3/d), Head (bar), Efficiency (%), Power (kW)
500, 300, 50, 400
1000, 280, 68, 800
1500, 250, 76, 1100
2000, 205, 72, 1400
2500, 140, 55, 1700
"""
COMP = """flow_sm3d,pressure_ratio,efficiency,speed_rpm
100000,2.6,0.70,10000
200000,2.4,0.78,10000
300000,2.0,0.74,10000
400000,1.4,0.60,10000
50000,1.6,0.70,7000
100000,1.5,0.78,7000
150000,1.3,0.70,7000
"""


def test_parse_and_interpolate_pump():
    c = parse_pump_curve_csv(PUMP)
    assert c.q_min() == 500 and c.q_max() == 2500
    assert abs(c.head_at(1250) - 265.0) < 1e-9 and abs(c.efficiency_at(1000) - 0.68) < 1e-9
    q, e = c.bep(); assert abs(q - 1500) < 5 and abs(e - 0.76) < 1e-3
    assert c.points()[0]['rate_m3d'] == 500 and c.points()[0]['head_bar'] == 300


def test_pump_operating_point_status():
    c = parse_pump_curve_csv(PUMP)
    ok = check_operating_point(c, 1500, dp_bar=250); assert ok['status'] == 'OK' and abs(ok['pct_of_bep'] - 100) < 1
    assert check_operating_point(c, 300)['status'] == 'VIOLATED' and check_operating_point(c, 2600)['status'] == 'VIOLATED'
    w = check_operating_point(c, 2450); assert w['status'] == 'WARNING' and w['margin_to_max_pct'] < 10
    assert abs(ok['margin_to_min_pct'] - 200.0) < 1e-6
    assert check_operating_point(c, 1500, dp_bar=200)['status'] == 'WARNING'   # head 20 % below curve


def test_bad_csv_and_head_in_metres():
    with pytest.raises(ValueError): parse_pump_curve_csv("a,b\n1,2\n3,4\n")
    with pytest.raises(ValueError): parse_pump_curve_csv("rate,head\n1,2\n")
    c = parse_pump_curve_csv("rate,head_m\n100,1000\n200,500\n", rho_kgm3=1000.0)
    assert abs(c.head_at(100) - 98.0665) < 1e-6


def test_compressor_multispeed_and_affinity():
    c = parse_compressor_curve_csv(COMP)
    assert c.speeds == [7000.0, 10000.0] and c.q_max() == 400000       # default = highest speed
    assert c.q_max(7000) == 150000
    s85 = c.line(9000)                                                  # affinity scaled from nearest line
    assert abs(s85.q[0] - 100000 * 9000 / 10000) < 1e-6
    r = check_operating_point(c, 50000, speed=10000); assert r['status'] == 'VIOLATED'
    assert check_operating_point(c, 200000, p_suction_bar=20, p_discharge_bar=48)['status'] == 'OK'


def _case_with_curves():
    n, e = demo_field_case()
    n = [dict(x) for x in n]
    # make the injection pump an inline node between WS and I1 (legacy edge -> node) using the existing helper
    from network.equipment import convert_edge_equipment_to_nodes
    n, e = convert_edge_equipment_to_nodes(n, e)
    return n, e


def test_check_network_equipment_rows_on_demo_pump():
    n, e = _case_with_curves()
    res = solve_v21(n, e)
    pump = next(x for x in n if x['kind'] == 'pump')
    q = res[1][pump['id']]
    assert abs(q) > 100
    # curve sized around the solved flow -> OK; curve far above the flow -> violated minimum flow
    ok_csv = f"rate,head,eff\n{abs(q)*0.4},300,0.6\n{abs(q)},280,0.78\n{abs(q)*1.8},200,0.6\n"
    bad_csv = f"rate,head,eff\n{abs(q)*3},300,0.6\n{abs(q)*4},280,0.78\n{abs(q)*5},200,0.6\n"
    pump['params']['curve_csv'] = ok_csv
    rows = check_network_equipment(n, e, res)
    assert rows and all(set(['Component', 'Constraint', 'Value', 'Limit', 'Status']) <= set(r) for r in rows)
    assert {r['Constraint'] for r in rows} >= {'Minimum flow', 'Maximum flow (runout)', 'Minimum % of BEP'}
    assert all(r['Status'] == 'OK' for r in rows if r['Severity'] == 'hard')
    pump['params']['curve_csv'] = bad_csv
    rows = check_network_equipment(n, e, res)
    assert any(r['Constraint'] == 'Minimum flow' and r['Status'] == 'VIOLATED' for r in rows)
    pump['params']['curve_csv'] = 'garbage'
    assert check_network_equipment(n, e, res)[0]['Constraint'] == 'Curve parse'
    pump['params'].pop('curve_csv'); assert check_network_equipment(n, e, res) == []
    # dict-form result is accepted too
    pump['params']['curve_csv'] = ok_csv
    d = {'pressures': res[0], 'flows': res[1], 'info': res[2], 'details': res[3]}
    assert len(check_network_equipment(n, e, d)) == len(check_network_equipment(n, e, res))


def test_compressor_node_uses_gor_flow():
    n = [{'id': 'C1', 'kind': 'compressor', 'name': 'C1', 'params': {'gor_sm3sm3': 1000.0, 'curve_csv': COMP}}]
    res = ({'C1': 20.0}, {'C1': 200.0}, {'inline_equipment': {'C1': {'rate_m3d': 200.0, 'p_in_bar': 20.0, 'p_out_bar': 48.0}}}, {})
    rows = check_network_equipment(n, [], res)          # 200 m3/d x 1000 = 200000 Sm3/d
    assert {r['Constraint'] for r in rows} >= {'Minimum flow (surge)', 'Maximum flow (runout)'}
    assert all(r['Status'] == 'OK' for r in rows), rows
    res[2]['inline_equipment']['C1']['rate_m3d'] = 20.0
    assert any(r['Constraint'] == 'Minimum flow (surge)' and r['Status'] == 'VIOLATED' for r in check_network_equipment(n, [], ({'C1': 20.0}, {'C1': 20.0}, res[2], {})))
