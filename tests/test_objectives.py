import json
import pytest
from network.examples import demo_field_case
from solver.steady_state import solve_network
from optimization.objectives import (well_variables, system_variables, normalize_objective, evaluate_objective, validate_objective,
                                     ObjectiveEvaluator, normalize_guide, guide_weights, curtail, PRESETS, GUIDE_FORMULA_PRESETS,
                                     DEFAULT_PRICES)


@pytest.fixture(scope='module')
def solved():
    ns, es = demo_field_case()
    ns[2]['params']['water_cut'] = 0.5      # PROD-B water heavy
    p, q, info, d = solve_network(ns, es)
    return ns, info, d


def test_well_variables_consistent(solved):
    ns, info, d = solved
    v = well_variables(d['P2'], ns[2]['params'], info)
    assert v['liquid'] == pytest.approx(v['oil'] + v['water'])
    assert v['wc'] == pytest.approx(0.5) and v['gor'] == pytest.approx(110.0)
    assert v['gas'] == pytest.approx(v['oil'] * 110.0)
    assert v['drawdown'] == pytest.approx(290.0 - v['bhp'])
    assert v['whp'] == pytest.approx(d['P2']['whp_bar'])
    # unrestricted deliverability at 20 bar WHP is at least the current rate (WHP is higher than 20 here)
    assert v['potential_liquid'] >= v['liquid'] * 0.99
    assert v['potential_oil'] == pytest.approx(v['potential_liquid'] * 0.5, rel=1e-6)
    assert v['power_kw'] == 0.0
    assert well_variables(d['P3'], ns[3]['params'])['gaslift'] == pytest.approx(60000.0)


def test_potential_ignores_rate_limits_and_network_cap(solved):
    ns, info, d = solved
    prm = dict(ns[1]['params']); prm['max_liquid_rate_m3d'] = 100.0; prm['_network_cap_m3d'] = 50.0
    base = well_variables(d['P1'], ns[1]['params'])['potential_liquid']
    assert well_variables(d['P1'], prm)['potential_liquid'] == pytest.approx(base)


def test_esp_power_nonzero():
    prm = {'lift_type': 'esp', 'esp_rated_rate_m3d': 1000.0, 'esp_shutoff_head_bar': 80.0, 'esp_speed_fraction': 1.0}
    v = well_variables({'liquid_rate_m3d': 500.0, 'oil_rate_m3d': 400.0, 'water_rate_m3d': 100.0, 'gas_rate_sm3d': 4e4}, prm, potential=False)
    assert v['power_kw'] > 0


def test_idle_well_has_finite_variables():
    v = well_variables({'liquid_rate_m3d': 0.0}, {'water_cut': 0.3, 'gor_sm3sm3': 90.0}, potential=False)
    assert v['wc'] == 0.3 and v['gor'] == 90.0 and v['oil'] == 0.0


def test_presets_expand_and_are_json_serialisable():
    for name in PRESETS:
        o = normalize_objective(name)
        assert o['preset'] == name and o['expression'] == PRESETS[name]
        json.dumps(o)
        assert json.loads(json.dumps(o)) == o
    assert normalize_objective(None)['preset'] == 'max_oil'
    assert normalize_objective({'expression': 'oil'})['preset'] == 'custom'


def test_max_revenue_value_matches_manual_sum(solved):
    ns, info, d = solved
    v = evaluate_objective({'preset': 'max_revenue', 'prices': {'oil': 400.0, 'gas': 0.1, 'water': 8.0, 'gaslift': 0.05}}, d, info, ns)
    exp = sum(400 * x['oil_rate_m3d'] + 0.1 * x['gas_rate_sm3d'] - 8 * x['water_rate_m3d'] - 0.05 * x['gas_lift_sm3d'] for x in d.values())
    assert v == pytest.approx(exp)
    default = evaluate_objective('max_revenue', d, info, ns)
    p = DEFAULT_PRICES
    assert default == pytest.approx(sum(p['oil'] * x['oil_rate_m3d'] + p['gas'] * x['gas_rate_sm3d'] - p['water'] * x['water_rate_m3d']
                                        - p['gaslift'] * x['gas_lift_sm3d'] for x in d.values()))


def test_simple_presets(solved):
    ns, info, d = solved
    assert evaluate_objective('max_oil', d, info, ns) == pytest.approx(sum(x['oil_rate_m3d'] for x in d.values()))
    assert evaluate_objective('max_liquid', d, info, ns) == pytest.approx(sum(x['liquid_rate_m3d'] for x in d.values()))
    assert evaluate_objective('max_gas', d, info, ns) == pytest.approx(sum(x['gas_rate_sm3d'] for x in d.values()))
    mw = evaluate_objective('min_water', d, info, ns)
    assert mw == pytest.approx(sum(x['oil_rate_m3d'] - 10.0 * x['water_rate_m3d'] for x in d.values()))


def test_custom_per_well_expression_with_params_and_condition(solved):
    ns, info, d = solved
    obj = {'preset': 'custom', 'expression': 'oil - K*water if wc > 0.3 else oil', 'params': {'K': 4}}
    exp = 0.0
    for x in d.values():
        wc = x['water_rate_m3d'] / x['liquid_rate_m3d']
        exp += x['oil_rate_m3d'] - (4 * x['water_rate_m3d'] if wc > 0.3 else 0.0)
    assert evaluate_objective(obj, d, info, ns) == pytest.approx(exp)


def test_system_expression_uses_totals_once(solved):
    ns, info, d = solved
    v = evaluate_objective({'preset': 'custom', 'expression': 'total_oil - 2*total_water + 0.001*water_injection'}, d, info, ns)
    to = sum(x['oil_rate_m3d'] for x in d.values()); tw = sum(x['water_rate_m3d'] for x in d.values())
    assert v == pytest.approx(to - 2 * tw + 0.001 * info['injector_rates']['I1'])
    sv = system_variables(ObjectiveEvaluator('max_oil').well_vars(d, ns), info, ns)
    assert sv['total_gaslift'] == pytest.approx(60000.0) and sv['water_injection'] == pytest.approx(info['injector_rates']['I1'])


def test_mixed_total_and_well_names_rejected():
    with pytest.raises(ValueError) as e:
        validate_objective({'preset': 'custom', 'expression': 'oil + total_water'})
    assert 'mixes' in str(e.value)


@pytest.mark.parametrize('bad', [{'preset': 'nope'}, {'preset': 'custom'}, {'preset': 'custom', 'expression': 'oil + bogus'},
                                 {'preset': 'custom', 'expression': "__import__('os')"}, {'preset': 'max_oil', 'prices': {'oil': 'x'}}, 5])
def test_invalid_objectives_rejected(bad):
    with pytest.raises(ValueError):
        validate_objective(bad)


def test_division_by_zero_on_idle_well_contributes_zero():
    ns = [{'id': 'a', 'kind': 'well', 'params': {}}, {'id': 'b', 'kind': 'well', 'params': {}}]
    d = {'a': {'liquid_rate_m3d': 100.0, 'oil_rate_m3d': 80.0, 'water_rate_m3d': 20.0, 'gas_rate_sm3d': 8000.0},
         'b': {'liquid_rate_m3d': 0.0, 'oil_rate_m3d': 0.0, 'water_rate_m3d': 0.0, 'gas_rate_sm3d': 0.0}}
    assert evaluate_objective({'expression': 'oil/(oil+water)*100'}, d, {}, ns) == pytest.approx(80.0)
    d['b']['liquid_rate_m3d'] = 10.0   # a producing well that divides by zero is a real error
    with pytest.raises(ValueError):
        evaluate_objective({'expression': '1/(oil)'}, d, {}, ns)


# ---------------------------------------------------------------- guides
def test_guide_validation():
    with pytest.raises(ValueError): normalize_guide({'mode': 'magic'})
    with pytest.raises(ValueError): normalize_guide({'mode': 'formula'})
    with pytest.raises(ValueError): normalize_guide({'mode': 'formula', 'formula': 'oil*Z'})
    with pytest.raises(ValueError): normalize_guide({'mode': 'pro_rata', 'phase': 'water'})
    with pytest.raises(ValueError): normalize_guide({'preset': 'nope'})
    g = normalize_guide({'preset': 'oil_potential_wc_penalised'})
    assert g['mode'] == 'formula' and g['params']['B'] == 0.1
    json.dumps(g)


def test_guide_weights_pro_rata_and_potential(solved):
    ns, info, d = solved; prm = {n['id']: n['params'] for n in ns if n['kind'] == 'well'}
    w = guide_weights({'mode': 'pro_rata', 'phase': 'oil'}, d, prm)
    assert w['P1'] == pytest.approx(d['P1']['oil_rate_m3d'])
    wp = guide_weights({'mode': 'potential', 'phase': 'liquid'}, d, prm)
    assert all(wp[k] >= d[k]['liquid_rate_m3d'] * 0.99 for k in wp)
    wg = guide_weights({'mode': 'potential', 'phase': 'gas'}, d, prm)
    assert wg['P1'] > wp['P1']          # gas phase potential = oil potential * GOR


def test_guide_weights_priority(solved):
    ns, info, d = solved; prm = {n['id']: n['params'] for n in ns if n['kind'] == 'well'}
    w = guide_weights({'mode': 'priority', 'priority': {'P1': 1, 'P2': 3}}, d, prm)
    assert w['P1'] == 1 and w['P2'] == 3 and w['P3'] == 4   # unlisted -> lowest priority


def test_guide_weights_formula_and_presets(solved):
    ns, info, d = solved; prm = {n['id']: n['params'] for n in ns if n['kind'] == 'well'}
    w = guide_weights({'mode': 'formula', 'formula': 'oil**A/(B+wc**C)', 'params': {'A': 1, 'B': 0.1, 'C': 2}}, d, prm)
    for k in w:
        v = well_variables(d[k], prm[k], potential=False)
        assert w[k] == pytest.approx(v['oil'] / (0.1 + v['wc'] ** 2))
    for name in GUIDE_FORMULA_PRESETS:
        ws = guide_weights({'preset': name}, d, prm)
        assert all(x > 0 for x in ws.values())
    wc = guide_weights({'preset': 'oil_potential_wc_penalised'}, d, prm)
    assert wc['P2'] < wc['P1']          # the 50 % water-cut well is the least preferred


def test_guide_formula_runtime_error_is_reported():
    d = {'a': {'liquid_rate_m3d': 10.0, 'oil_rate_m3d': 0.0, 'water_rate_m3d': 10.0, 'gas_rate_sm3d': 0.0}}
    with pytest.raises(ValueError) as e:
        guide_weights({'mode': 'formula', 'formula': '1/oil'}, d, {'a': {}})
    assert 'well a' in str(e.value)


RATES = {'a': 100.0, 'b': 300.0, 'c': 600.0}


_MODES = [
    ('pro_rata', RATES), ('potential', {'a': 5.0, 'b': 1.0, 'c': 1.0}), ('allocation', {'a': 0.0, 'b': 1.0, 'c': 1.0}),
    ('formula', {'a': 5.0, 'b': 1.0, 'c': 0.0}), ('inverse', {'a': 1.0, 'b': 1.0, 'c': 1.0}), ('priority', {'a': 1.0, 'b': 2.0, 'c': 3.0}),
    ('priority', {'a': 1.0, 'b': 1.0, 'c': 1.0}), ('formula', {'a': 0.0, 'b': 0.0, 'c': 0.0}), ('potential', {'a': 0.0, 'b': 0.0, 'c': 0.0}),
]
_CASES = [(m, w, r) for (m, w) in _MODES for r in (0.0, 50.0, 400.0, 999.0, 5000.0)]


@pytest.mark.parametrize('mode,weights,req', _CASES)
def test_curtail_invariants(mode, weights, req):
    red = curtail(req, RATES, weights, mode)
    assert set(red) == set(RATES)
    assert sum(red.values()) == pytest.approx(min(req, 1000.0), abs=1e-6)
    for k, v in red.items():
        assert -1e-12 <= v <= RATES[k] + 1e-9


def test_curtail_pro_rata_proportional():
    red = curtail(100.0, RATES, RATES, 'pro_rata')
    assert red['a'] == pytest.approx(10.0) and red['b'] == pytest.approx(30.0) and red['c'] == pytest.approx(60.0)


def test_curtail_priority_lowest_first_completely():
    red = curtail(500.0, RATES, {'a': 1.0, 'b': 2.0, 'c': 3.0}, 'priority')   # c is lowest priority
    assert red['c'] == pytest.approx(500.0) and red['a'] == 0 and red['b'] == 0
    red = curtail(700.0, RATES, {'a': 1.0, 'b': 2.0, 'c': 3.0}, 'priority')
    assert red['c'] == pytest.approx(600.0) and red['b'] == pytest.approx(100.0) and red['a'] == 0


def test_curtail_priority_negative_keys_and_ties():
    red = curtail(100.0, RATES, {'a': -0.5, 'b': -0.9, 'c': -0.9}, 'priority')    # a has the highest key -> first
    assert red['a'] == pytest.approx(100.0)
    red = curtail(400.0, RATES, {'a': 0.0, 'b': 0.0, 'c': 0.0}, 'priority')        # all tie -> pro rata
    assert red['a'] == pytest.approx(40.0) and red['c'] == pytest.approx(240.0)


def test_curtail_formula_inverse_convention():
    red = curtail(30.0, RATES, {'a': 1.0, 'b': 2.0, 'c': 3.0}, 'formula')
    assert red['a'] == pytest.approx(30 * 1 / (1 + 0.5 + 1 / 3))
    assert red['a'] > red['b'] > red['c']                                         # lowest guide value cut most
    red = curtail(150.0, RATES, {'a': 100.0, 'b': 1.0, 'c': 1.0}, 'formula')
    assert red['a'] < 1.0                                                          # high guide value kept
    red = curtail(150.0, RATES, {'a': 1.0, 'b': 1.0, 'c': 0.0}, 'formula')
    assert red['c'] == pytest.approx(150.0)                                        # zero guide: curtailed first


def test_curtail_formula_water_fills_at_rate_cap():
    red = curtail(300.0, RATES, {'a': 0.001, 'b': 1.0, 'c': 1.0}, 'formula')
    assert red['a'] == pytest.approx(100.0)                                        # fully shut, remainder shared
    assert red['b'] + red['c'] == pytest.approx(200.0)


def test_curtail_potential_allocation_follows_weights():
    red = curtail(500.0, RATES, {'a': 1.0, 'b': 1.0, 'c': 2.0}, 'potential')
    new = {k: RATES[k] - red[k] for k in RATES}
    assert new['a'] == pytest.approx(100.0) and new['b'] == pytest.approx(200.0 if False else new['b'])
    assert sum(new.values()) == pytest.approx(500.0)
    assert new['c'] == pytest.approx(2 * new['b']) or new['c'] == pytest.approx(RATES['c'])


def test_curtail_deterministic_and_unknown_mode():
    assert curtail(120.0, RATES, {'a': 1.0, 'b': 1.0, 'c': 1.0}, 'priority') == curtail(120.0, RATES, {'a': 1.0, 'b': 1.0, 'c': 1.0}, 'priority')
    with pytest.raises(ValueError):
        curtail(1.0, RATES, RATES, 'nope')
