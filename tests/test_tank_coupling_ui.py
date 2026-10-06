import copy
from network.examples import demo_field_case
from ui.tank_coupling import tank_coupling_table, communication_table, apply_communication_table


def test_tables_derived_from_canvas_and_editable():
    n, e = demo_field_case(); t1 = next(x for x in n if x['kind'] == 'reservoir')
    n.append({'id': 'T9', 'kind': 'reservoir', 'name': 'Tank 9', 'params': {'fluid_phase': 'gas', 'giip_sm3': 2e9, 'reservoir_pressure_bar': 200}})
    t1['params']['communication'] = [{'to': 'T9', 'transmissibility_m3d_bar': 50.0}]
    df = tank_coupling_table(n, e); assert len(df) == 2 and df.loc[df['Tank'] == 'Tank 9', 'Communicates with'].iloc[0] == t1['name'] and 'GSm³' in df['In place'].iloc[1]
    assert df['Producers'].iloc[0] != '—'
    cdf = communication_table(n); assert len(cdf) == 1
    cdf['Transmissibility [m3/d/bar]'] = 250.0; cdf['Max transfer [m3/d] (blank = none)'] = 1000.0
    assert apply_communication_table(n, cdf) and t1['params']['communication'][0]['transmissibility_m3d_bar'] == 250.0 and t1['params']['communication'][0]['max_transfer_m3d'] == 1000.0
    assert not apply_communication_table(n, cdf)
