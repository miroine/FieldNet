from network.examples import demo_field_case
from network.forecast import run_forecast


def _case(mode):
    n, e = demo_field_case(); t = next(x for x in n if x['kind'] == 'reservoir')
    if mode == 'external':
        t['params']['prediction_mode'] = 'external'
        t['params']['external_table'] = [{'date': '2026-01-01', 'reservoir_pressure_bar': 250.0, 'water_cut': 0.3}, {'date': '2027-01-01', 'reservoir_pressure_bar': 200.0, 'water_cut': 0.8},
                                         {'time_days': 1000, 'reservoir_pressure_bar': 150.0}]
    return n, e, t


def test_external_tank_pressure_follows_table_not_material_balance():
    n, e, t = _case('external'); fc = run_forecast(n, e, '2026-01-01', 2.0, 90)
    rows = [r for r in fc['tanks'] if r['Tank ID'] == t['id']] if 'Tank ID' in fc['tanks'][0] else fc['tanks']
    ps = {str(r['Date']): r['Pressure [bar]'] for r in rows}
    # rows are end-of-step values: the step starting 2026-04-01 ends 180 days in -> 250 - 50*180/365 = 225.3 bar
    assert abs(ps['2026-04-01'] - 225.3) < 1.5
    assert abs(ps['2026-12-27'] - 193.3) < 2.0              # step starting day 360 ends at day 450: between 200 bar (day 365) and 150 bar (day 1000)
    n2, e2, _ = _case('mb'); fc2 = run_forecast(n2, e2, '2026-01-01', 2.0, 90)
    assert fc['field'][-1]['Cumulative oil [Sm3]'] != fc2['field'][-1]['Cumulative oil [Sm3]']


def test_external_water_cut_override_reaches_wells():
    n, e, t = _case('external'); fc = run_forecast(n, e, '2026-01-01', 1.5, 90)
    wc = [r['Water cut [%]'] for r in fc['field']]; assert wc[-1] > wc[0] + 5                      # table raises water cut to 80 % by 2027 (demo wells start ~40 %)
