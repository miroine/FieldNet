import pytest, pandas as pd
from tests.hubfix import demo_hub
from network import annual, groups as grp, data_hub as dh


def test_annual_sums_equal_cumulatives():
    n, e, s, fc, hub = demo_hub()
    a = annual.field_annual(fc)
    assert abs(a['Oil [Sm3]'].sum() - fc['field'][-1]['Cumulative oil [Sm3]']) <= 1e-6 * fc['field'][-1]['Cumulative oil [Sm3]']
    assert (a['Days'] > 0).all()


def test_convert_units_and_oe():
    n, e, s, fc, hub = demo_hub()
    a = annual.field_annual(fc)
    c = annual.convert(a, annual.UNITS[0] if isinstance(annual.UNITS, (list, tuple)) else list(annual.UNITS)[0], oe=True)
    assert len(c) == len(a)


def test_bar_figure_builds():
    import sys
    from unittest.mock import MagicMock
    names = ('plotly', 'plotly.graph_objects', 'plotly.subplots', 'plotly.express')
    saved = {m: sys.modules.get(m) for m in names}
    try:
        import plotly.subplots  # noqa
    except ImportError:
        for m in names: sys.modules[m] = MagicMock()
    try:
        n, e, s_, fc, hub = demo_hub()
        assert annual.bar_figure(annual.field_annual(fc), ['Oil [Sm3]'], 't') is not None
    finally:
        for m, v in saved.items():
            if v is None: sys.modules.pop(m, None)
            else: sys.modules[m] = v


def test_group_from_tank_and_reconcile():
    n, e, s, fc, hub = demo_hub()
    assert 'North' in grp.group_names(n)
    assert len(grp.members(n)['North']) >= 4
    assert all(c['ok'] for c in grp.reconcile(n, fc))


def test_group_sum_equals_field_when_all_in_group():
    n, e, s, fc, hub = demo_hub()
    gp = grp.group_profile(n, fc)
    first = gp[gp['Group'] == 'North'].iloc[0]
    assert first['Oil [m3/d]'] == pytest.approx(fc['field'][0]['Oil [m3/d]'], rel=1e-6)


def test_hierarchy_prefixes():
    assert grp.prefixes('North/Segment A') == ['North', 'North/Segment A']


def test_assign_and_unassign():
    import copy
    n, e, s, fc, hub = demo_hub(); n = copy.deepcopy(n)
    w = next(x for x in n if x['kind'] == 'well')
    grp.assign(n, [w['id']], 'Z/Y'); assert grp.group_of(w) == 'Z/Y'
    grp.assign(n, [w['id']], ''); assert grp.group_of(w) == ''


def test_hub_consistency_all_pass():
    n, e, s, fc, hub = demo_hub()
    c = dh.check_consistency(hub, n, fc, 'm')
    assert len(c) >= 8 and not (c['Status'] == 'FAIL').any()


def test_hub_flags_stale_results():
    n, e, s, fc, hub = demo_hub()
    c = dh.check_consistency(hub, n, fc, 'different')
    assert (c['Status'] == 'WARN').any()


def test_hub_catalogue():
    n, e, s, fc, hub = demo_hub()
    cat = hub.catalogue()
    assert {'forecast_field', 'annual_field', 'groups_profile', 'kpis'} <= set(cat['Dataset'])


def test_link_create_delete_and_dynamics():
    import copy
    from ui import tank_coupling as tc
    n, e, s, fc, hub = demo_hub(); n = copy.deepcopy(n)
    t1 = next(x for x in n if x['kind'] == 'reservoir'); t2 = copy.deepcopy(t1); t2['id'] = 'T2'; t2['name'] = 'SEG-2'; t2['params'] = dict(t1['params'], reservoir_pressure_bar=260.0, communication=[]); t1['params'].pop('communication', None); n.append(t2)
    tc.add_link(n, 'T1', 'T2', 50.0)
    with pytest.raises(ValueError): tc.add_link(n, 'T2', 'T1', 10.0)
    with pytest.raises(ValueError): tc.add_link(n, 'T1', 'T1')
    with pytest.raises(ValueError): tc.add_link(n, 'T1', 'W-X')
    d = tc.link_dynamics(n); assert len(d) == 1 and d['Initial dP [bar]'].iloc[0] == pytest.approx(30.0) and d['Equalisation time constant [days]'].iloc[0] > 0
    assert tc.communication_table(n).shape[0] == 1
    assert tc.remove_link(n, 'T2', 'T1') and not tc.link_exists(n, 'T1', 'T2') and not tc.remove_link(n, 'T1', 'T2')


def test_transmissibility_changes_forecast_pressures():
    import copy, datetime as dt
    from ui import tank_coupling as tc
    from network.forecast import run_forecast
    n, e, s, fc, hub = demo_hub(); out = {}
    for T in (0.0, 5000.0):
        nn = copy.deepcopy(n); t1 = next(x for x in nn if x['kind'] == 'reservoir'); t1['params'].pop('communication', None)
        t2 = copy.deepcopy(t1); t2['id'] = 'T2'; t2['name'] = 'SEG-2'; t2['params'] = dict(t1['params'], communication=[], aquifer_pi_m3d_bar=0.0); nn.append(t2)
        tc.add_link(nn, 'T1', 'T2', T) if T > 0 else None
        f = run_forecast(nn, e, dt.date(2027, 1, 1), years=1, step_days=90); t = f['tanks']
        out[T] = (max(r['Pressure [bar]'] for r in t if r['Tank ID'] == 'T2') - min(r['Pressure [bar]'] for r in t if r['Tank ID'] == 'T2'), [r for r in t if r['Tank ID'] == 'T2'][-1]['Net communication [m3]'])
    assert out[0.0][1] == 0.0 and out[5000.0][1] != 0.0
