import copy, pytest
from tests.hubfix import demo_hub
from network import fluids as fl


def _model():
    n, e, s, fc, hub = demo_hub(); return copy.deepcopy(n), copy.deepcopy(e), s


def _heavy():
    pvt = {'model': 'correlation', 'pb_corr': 'standing', 'bo_corr': 'standing', 'visc_corr': 'beggs_robinson', 'z_corr': 'dak', 'co2': 0.0, 'h2s': 0.0, 'n2': 0.0}
    return fl.new_fluid('Heavy', 24.0, 0.78, 60.0, pvt)


def test_assign_and_usage_and_tank_sync():
    n, e, s = _model(); f = _heavy(); tank = next(x for x in n if x['kind'] == 'reservoir')
    cnt = fl.assign_tank_system(n, e, f, tank['id'])
    assert cnt >= 4 and tank['params']['fluid_name'] == 'Heavy' and tank['params']['rsi_sm3_sm3'] == pytest.approx(60.0, rel=0.05) and tank['params']['boi_rm3_sm3'] > 1.0
    w = next(x for x in n if x['kind'] == 'well'); assert w['params']['api'] == 24.0 and w['params']['pvt']['model'] == 'correlation'
    assert fl.usage(n, e)['Heavy']['wells'] >= 3


def test_two_fluids_in_one_model_and_blend():
    n, e, s = _model(); wells = [x for x in n if x['kind'] == 'well']; light = fl.new_fluid('Light', 42.0, 0.7, 180.0)
    fl.assign(n, e, _heavy(), [wells[0]['id']]); fl.assign(n, e, light, [wells[1]['id']])
    assert wells[0]['params']['api'] == 24.0 and wells[1]['params']['api'] == 42.0 and 'pvt' not in wells[1]['params']
    c = fl.commingled(n, e, s); node = c[c['Kind'] == 'node']; assert (node['Distinct fluids'] >= 1).all()


def test_propagate_after_edit_keeps_water_cut():
    n, e, s = _model(); lib = {}; f = _heavy(); fl.library_add(lib, f); w = next(x for x in n if x['kind'] == 'well')
    wc = w['params']['water_cut']; fl.assign(n, e, f, [w['id']]); lib['Heavy']['api'] = 20.0
    assert fl.propagate(lib, n, e, 'Heavy') == 1 and w['params']['api'] == 20.0 and w['params']['water_cut'] == wc


def test_library_roundtrip_from_elements():
    n, e, s = _model(); w = next(x for x in n if x['kind'] == 'well'); fl.assign(n, e, _heavy(), [w['id']])
    lib = fl.library_from_elements(n, e); assert lib['Heavy']['api'] == 24.0 and lib['Heavy']['pvt']['model'] == 'correlation'


def test_rename_delete_rules():
    n, e, s = _model(); lib = {}; f = _heavy(); fl.library_add(lib, f); w = next(x for x in n if x['kind'] == 'well'); fl.assign(n, e, f, [w['id']])
    with pytest.raises(ValueError): fl.library_delete(lib, n, e, 'Heavy')
    fl.library_rename(lib, n, e, 'Heavy', 'Tar'); assert w['params']['fluid_name'] == 'Tar' and 'Tar' in lib
    fl.library_delete(lib, n, e, 'Tar', force=True); assert 'fluid_name' not in w['params']
    with pytest.raises(ValueError): fl.library_add(lib, f); fl.library_add(lib, f)


def test_check_flags_mismatch_and_missing():
    n, e, s = _model(); lib = {}; f = _heavy(); fl.library_add(lib, f); tank = next(x for x in n if x['kind'] == 'reservoir')
    fl.assign(n, e, f, [tank['id']])
    ck = fl.check(lib, n, e); assert (ck['Level'] == 'WARN').any()
    w = next(x for x in n if x['kind'] == 'well'); w['params']['fluid_name'] = 'Ghost'
    assert (fl.check(lib, n, e)['Level'] == 'FAIL').any()


def test_tank_properties_legacy_and_correlation():
    a = fl.tank_properties(fl.new_fluid('x', 35, 0.75, 100), 90.0, 300.0); b = fl.tank_properties(_heavy(), 90.0, 300.0)
    assert a['boi_rm3_sm3'] > 1.0 and b['boi_rm3_sm3'] > 1.0 and b['bubble_point_bar'] is not None


def test_fluid_name_required():
    with pytest.raises(ValueError): fl.new_fluid(' ')
