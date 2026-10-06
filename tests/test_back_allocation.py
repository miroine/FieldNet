from network.back_allocation import back_allocate, schedule_well_tests

TESTS = [{'well': 'A', 'oil': 500, 'water': 50, 'gas': 50000}, {'well': 'B', 'oil': 300, 'water': 150, 'gas': 36000},
         {'well': 'C', 'oil': 200, 'water': 20, 'gas': 30000}]


def test_conservation_single_period():
    m = {'oil': 900.0, 'water': 200.0, 'gas': 100000.0}
    r = back_allocate(m, TESTS, uptime={'A': 0.95, 'B': 0.8, 'C': 0.0})
    for ph in m: assert abs(sum(w[ph] for w in r['allocated'].values()) - m[ph]) < 1e-9
    assert r['allocated']['C']['oil'] == 0.0 and r['allocated']['A']['oil'] > r['allocated']['B']['oil']
    assert r['allocation_factor']['oil'] > 0 and abs(r['imbalance']['oil']['unallocated']) < 1e-9


def test_proportional_and_factor():
    r = back_allocate({'oil': 800.0, 'water': 220.0, 'gas': 116000.0}, TESTS)   # theoretical equals measured -> AF = 1
    assert abs(r['allocation_factor']['oil'] - 0.8) < 1e-12
    assert abs(r['allocated']['A']['oil'] - 400.0) < 1e-9


def test_multi_period_and_period_days():
    r = back_allocate({'jan': {'oil': 31000.0, 'water': 0.0, 'gas': 0.0}, 'feb': {'oil': 0.0, 'water': 0.0, 'gas': 0.0}}, TESTS, period_days={'jan': 31, 'feb': 28})
    a = r['periods']['jan']; assert abs(sum(w['oil'] for w in a['allocated'].values()) - 31000.0) < 1e-6
    assert abs(sum(w['oil'] for w in a['allocated_rates'].values()) - 1000.0) < 1e-6
    assert r['periods']['feb']['allocated']['A']['oil'] == 0.0


def test_unallocated_reported_when_no_potential():
    r = back_allocate({'oil': 100.0, 'water': 0.0, 'gas': 0.0}, TESTS, uptime={'A': 0, 'B': 0, 'C': 0})
    assert r['imbalance']['oil']['unallocated'] == 100.0 and r['warnings']


def test_latest_test_used():
    t = TESTS + [{'well': 'A', 'date': '2027-02-01', 'oil': 1000, 'water': 0, 'gas': 0}, {'well': 'A', 'date': '2027-01-01', 'oil': 1, 'water': 0, 'gas': 0}]
    r = back_allocate({'oil': 1500.0, 'water': 0, 'gas': 0}, t)
    assert abs(r['theoretical']['A']['oil'] - 1000.0) < 1e-9


def test_schedule_overdue_first_and_capacity():
    wells = [{'id': 'A', 'rate': 500}, {'id': 'B', 'rate': 900}, {'id': 'C', 'rate': 200}, {'id': 'D', 'rate': 5000}]
    last = {'A': '2027-01-01', 'B': '2027-02-20', 'C': '2026-12-01'}   # D never tested but too big
    s = schedule_well_tests(wells, last, 60, test_separator_capacity=2000, max_tests_per_day=1, start_date='2027-03-01')
    order = [r['well'] for r in s['calendar']]
    assert order[0] == 'C' and order[1] == 'A' and 'D' not in order and s['skipped'][0]['well'] == 'D'
    assert len({r['date'] for r in s['calendar']}) == len(s['calendar'])   # 1 per day


def test_schedule_never_tested_first_and_max_per_day():
    s = schedule_well_tests(['A', 'B', 'C'], {'A': '2027-03-01'}, 30, max_tests_per_day=2, start_date='2027-03-02')
    day0 = [r['well'] for r in s['calendar'] if r['date'] == '2027-03-02']
    assert set(day0) == {'B', 'C'} and all(len([r for r in s['calendar'] if r['date'] == d]) <= 2 for d in {r['date'] for r in s['calendar']})
