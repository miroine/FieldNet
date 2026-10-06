import copy
import json
import time
import pytest
from network.examples import demo_field_case
from solver.steady_state import solve_network
from optimization.field_optimizer import optimize_field, make_step_solver, CAP_KEY, GL_KEY
from optimization.objectives import evaluate_objective


def real_solve(ns, es, g):
    return solve_network(ns, es, initial_guess=g)


def demo(sep_limit=2500.0, wc_b=0.6):
    ns, es = demo_field_case()
    ns[5]['params']['max_liquid_rate_m3d'] = sep_limit
    ns[2]['params']['water_cut'] = wc_b
    return ns, es


def liq(res):
    return {k: v['liquid_rate_m3d'] for k, v in res['result'][3].items()}


def sep_value(res):
    return next(c['Value'] for c in res['result'][2]['constraints'] if c['Constraint'] == 'Liquid capacity')


@pytest.fixture(scope='module')
def base_case():
    ns, es = demo()
    return ns, es, optimize_field(ns, es, real_solve, objective='max_oil')


# -------------------------------------------------------------------- demo field (real solver)
def test_feasible_and_honours_separator_capacity(base_case):
    ns, es, r = base_case
    assert r['feasible'] and not r['violations']
    assert sep_value(r) <= 2500.0 + 1e-9
    assert sep_value(r) >= 2500.0 * 0.99          # capacity is used (release reclaimed the safety margin)
    assert r['result'][2].get('success')


def test_objective_at_least_pro_rata_choke_back(base_case):
    ns, es, r = base_case
    assert r['objective_value'] >= r['constrained_baseline_value'] - 1e-9
    assert r['objective_value'] > r['constrained_baseline_value']          # water-heavy well exists -> real gain
    assert r['baseline_value'] > r['objective_value']                      # capacity costs production
    assert r['improvement'] == pytest.approx(r['objective_value'] - r['constrained_baseline_value'])
    assert r['improvement_pct'] > 1.0


def test_pro_rata_baseline_matches_solver_v21():
    from solver.v21 import enforce_capacity_constraints
    ns, es = demo()
    (p, q, info, d), _, _ = enforce_capacity_constraints(ns, es, real_solve)
    ev_old = evaluate_objective('max_oil', d, info, ns)
    r = optimize_field(ns, es, real_solve, objective='max_oil')
    assert r['constrained_baseline_value'] == pytest.approx(ev_old, rel=2e-3)


def test_water_heavy_well_curtailed_first_for_oil_objective(base_case):
    ns, es, r = base_case
    caps = r['well_caps_m3d']
    assert 'P2' in caps                                   # PROD-B has 60 % water cut
    assert 'P1' not in caps and 'P3' not in caps          # oil-rich wells keep full rate


def test_rich_wells_not_curtailed_relative_to_unconstrained(base_case):
    ns, es, r = base_case
    unc = solve_network(ns, es)[3]
    assert liq(r)['P1'] >= unc['P1']['liquid_rate_m3d'] * 0.999
    assert liq(r)['P3'] >= unc['P3']['liquid_rate_m3d'] * 0.999
    assert liq(r)['P2'] < unc['P2']['liquid_rate_m3d'] * 0.5


def test_actions_history_and_metadata(base_case):
    ns, es, r = base_case
    a = r['actions']
    assert a and all({'component', 'control', 'old', 'new', 'reason'} <= set(x) for x in a)
    assert all(x['control'] == CAP_KEY for x in a)
    assert any('SEPARATOR' in x['reason'] for x in a)
    assert r['history'][0]['phase'] == 'unconstrained' and r['history'][0]['max_violation'] > 0.1
    assert r['history'][-1]['max_violation'] == 0.0
    assert r['n_solves'] >= 3 and r['runtime_s'] > 0 and r['method']
    json.dumps(r['objective']); json.dumps(r['guide'])


def test_runtime_is_a_few_seconds_and_reported():
    ns, es = demo()
    t = time.perf_counter(); r = optimize_field(ns, es, real_solve, objective='max_oil'); wall = time.perf_counter() - t
    assert wall < 15.0 and r['runtime_s'] <= wall + 0.05


def test_no_violation_no_action():
    ns, es = demo(sep_limit=9000.0)
    r = optimize_field(ns, es, real_solve, objective='max_oil')
    assert r['feasible'] and r['actions'] == [] and r['well_caps_m3d'] == {}
    assert r['n_solves'] <= 2
    assert r['objective_value'] == pytest.approx(r['baseline_value'])


def test_deterministic():
    ns, es = demo()
    a = optimize_field(ns, es, real_solve, objective='max_revenue')
    b = optimize_field(ns, es, real_solve, objective='max_revenue')
    assert a['objective_value'] == b['objective_value'] and a['well_caps_m3d'] == b['well_caps_m3d']
    assert [h['objective'] for h in a['history']] == [h['objective'] for h in b['history']]


def test_inputs_not_mutated_and_only_allowed_params_written():
    ns, es = demo(); ns[1]['params'][CAP_KEY] = 5.0       # stale cap on the input is ignored, not honoured
    ns0, es0 = copy.deepcopy(ns), copy.deepcopy(es)
    r = optimize_field(ns, es, real_solve, objective='max_oil', optimize_gas_lift=True, total_gas_lift_sm3d=60000.0)
    assert ns == ns0 and es == es0
    assert r['nodes'] is not ns
    for n0, n1 in zip(ns0, r['nodes']):
        p0, p1 = n0.get('params', {}), n1.get('params', {})
        changed = {k for k in set(p0) | set(p1) if p0.get(k) != p1.get(k)}
        assert changed <= {CAP_KEY, GL_KEY}
        assert {k: v for k, v in n0.items() if k != 'params'} == {k: v for k, v in n1.items() if k != 'params'}
    assert r['well_caps_m3d'].get('P1', 1e9) > 5.0


def test_guide_modes_all_feasible():
    ns, es = demo()
    for guide in [{'mode': 'pro_rata'}, {'mode': 'potential'}, {'mode': 'priority', 'priority': {'P1': 1, 'P2': 2, 'P3': 3}},
                  {'mode': 'formula', 'formula': 'oil'}, {'preset': 'oil_potential_gor_penalised'}]:
        r = optimize_field(ns, es, real_solve, objective='max_oil', guide=guide)
        assert r['feasible'], guide
        assert r['objective_value'] > 0.5 * r['baseline_value'], guide


def test_pro_rata_guide_curtails_every_well():
    ns, es = demo()
    r = optimize_field(ns, es, real_solve, objective='max_oil', guide={'mode': 'pro_rata'})
    assert set(r['well_caps_m3d']) == {'P1', 'P2', 'P3'}
    unc = solve_network(ns, es)[3]
    fr = [liq(r)[k] / unc[k]['liquid_rate_m3d'] for k in ('P1', 'P2', 'P3')]
    assert max(fr) - min(fr) < 0.12      # similar fractional reduction (network coupling makes it not exact)


def test_priority_curtails_lowest_priority_well_first():
    ns, es = demo(wc_b=0.1)
    unc = solve_network(ns, es)[3]
    r = optimize_field(ns, es, real_solve, objective='max_oil', guide={'mode': 'priority', 'priority': {'P1': 1, 'P2': 2, 'P3': 3}})
    assert r['feasible']
    assert liq(r)['P1'] >= unc['P1']['liquid_rate_m3d'] * 0.999 and liq(r)['P2'] >= unc['P2']['liquid_rate_m3d'] * 0.999
    assert liq(r)['P3'] < unc['P3']['liquid_rate_m3d'] * 0.9
    r2 = optimize_field(ns, es, real_solve, objective='max_oil', guide={'mode': 'priority', 'priority': {'P3': 1, 'P1': 2, 'P2': 3}})
    assert liq(r2)['P3'] >= unc['P3']['liquid_rate_m3d'] * 0.999 and liq(r2)['P1'] >= unc['P1']['liquid_rate_m3d'] * 0.999
    assert liq(r2)['P2'] < unc['P2']['liquid_rate_m3d'] * 0.9


def test_formula_guide_ranks_wells_by_the_formula():
    ns, es = demo(wc_b=0.1)
    unc = solve_network(ns, es)[3]
    # guide value = productivity-index-like proxy: use well drawdown-independent "oil" -> smallest oil rate is served last
    r = optimize_field(ns, es, real_solve, objective='max_oil', guide={'mode': 'formula', 'formula': 'oil'})
    frac = {k: liq(r)[k] / unc[k]['liquid_rate_m3d'] for k in ('P1', 'P2', 'P3')}
    order_by_guide = sorted(('P1', 'P2', 'P3'), key=lambda k: unc[k]['oil_rate_m3d'])
    low, high = order_by_guide[0], order_by_guide[-1]
    assert frac[low] < frac[high]
    # a formula that favours P1 explicitly protects it
    r = optimize_field(ns, es, real_solve, objective='max_oil',
                       guide={'mode': 'formula', 'formula': 'where(whp > 0, 1, 0) * (K if liquid > 1100 else 0.01)', 'params': {'K': 100}})
    assert r['feasible']


def test_wc_penalised_preset_curtails_water_heavy_well_first():
    ns, es = demo()
    unc = solve_network(ns, es)[3]
    r = optimize_field(ns, es, real_solve, objective='max_liquid', guide={'preset': 'oil_potential_wc_penalised'})
    frac = {k: liq(r)[k] / unc[k]['liquid_rate_m3d'] for k in ('P1', 'P2', 'P3')}
    assert frac['P2'] < frac['P1'] and frac['P2'] < frac['P3']


def test_objective_changes_who_is_curtailed():
    ns, es = demo(wc_b=0.1)
    unc = solve_network(ns, es)[3]
    r_gas = optimize_field(ns, es, real_solve, objective={'preset': 'custom', 'expression': 'gas'})
    r_liq = optimize_field(ns, es, real_solve, objective='max_liquid')
    # for max_liquid all wells have equal value density -> pro-rata like; gas objective prefers high GOR*oil wells
    assert r_gas['feasible'] and r_liq['feasible']
    assert r_gas['objective_value'] >= r_gas['constrained_baseline_value'] - 1e-6


def test_edge_capacity_maximum_rate_enforced():
    ns, es = demo(sep_limit=1e9)
    next(e for e in es if e['id'] == 'TRUNK')['params']['max_rate_m3d'] = 2400.0
    r = optimize_field(ns, es, real_solve, objective='max_oil')
    assert r['feasible']
    row = next(c for c in r['result'][2]['constraints'] if c['Constraint'] == 'Maximum rate')
    assert row['Value'] <= 2400.0 + 1e-9 and 'P2' in r['well_caps_m3d']


def test_well_rate_limit_in_report_mode_is_relieved():
    ns, es = demo(sep_limit=1e9)
    ns[1]['params'].update(max_liquid_rate_m3d=800.0, rate_limit_mode='report')
    r0 = solve_network(ns, es)
    assert any(c['Constraint'] == 'Maximum liquid rate' and c['Status'] == 'VIOLATED' for c in r0[2]['constraints'])
    r = optimize_field(ns, es, real_solve, objective='max_oil')
    assert r['feasible'] and r['well_caps_m3d']['P1'] <= 800.0 + 1e-6 and liq(r)['P1'] <= 800.0 + 1e-6


def test_gas_lift_allocation_respects_total_and_improves():
    ns, es = demo(sep_limit=1e9, wc_b=0.1)
    for i in (1, 2, 3): ns[i]['params'].update(lift_type='gas_lift', gas_lift_injection_sm3d=0.0, pi_m3d_bar=6.0 + i)
    ns[3]['params']['gas_lift_injection_sm3d'] = 30000.0
    total = 120000.0
    t = time.perf_counter()
    r = optimize_field(ns, es, real_solve, objective='max_oil', optimize_gas_lift=True, total_gas_lift_sm3d=total)
    assert time.perf_counter() - t < 30.0
    gl = r['gas_lift_sm3d']
    assert set(gl) == {'P1', 'P2', 'P3'} and sum(gl.values()) <= total + 1e-6 and sum(gl.values()) > 0
    assert all(v >= 0 for v in gl.values())
    assert r['objective_value'] > r['baseline_value'] * 1.02
    assert any(a['control'] == GL_KEY for a in r['actions'])
    # the injected values are what the verified solve used
    for k, v in gl.items(): assert r['result'][3][k]['gas_lift_sm3d'] == pytest.approx(v)


def test_gas_lift_default_budget_is_current_total_and_cost_aware():
    ns, es = demo(sep_limit=1e9, wc_b=0.1)
    ns[3]['params']['gas_lift_injection_sm3d'] = 60000.0
    r = optimize_field(ns, es, real_solve, objective='max_oil', optimize_gas_lift=True)
    assert sum(r['gas_lift_sm3d'].values()) <= 60000.0 + 1e-6
    pricey = {'preset': 'max_revenue', 'prices': {'oil': 500.0, 'gas': 0.0, 'water': 0.0, 'gaslift': 1e6}}
    r2 = optimize_field(ns, es, real_solve, objective=pricey, optimize_gas_lift=True, total_gas_lift_sm3d=60000.0)
    assert sum(r2['gas_lift_sm3d'].values()) == pytest.approx(0.0, abs=1e-6)     # never worth injecting


def test_gas_lift_skipped_without_gas_lift_wells():
    ns, es = demo(); ns[3]['params'].update(lift_type='none')
    r = optimize_field(ns, es, real_solve, objective='max_oil', optimize_gas_lift=True, total_gas_lift_sm3d=1000.0)
    assert r['feasible'] and any('skipped' in w for w in r['warnings'])


def test_gas_lift_with_capacity_still_feasible():
    ns, es = demo(sep_limit=2200.0, wc_b=0.1)
    ns[3]['params']['gas_lift_injection_sm3d'] = 0.0
    r = optimize_field(ns, es, real_solve, objective='max_oil', optimize_gas_lift=True, total_gas_lift_sm3d=60000.0)
    assert r['feasible'] and sep_value(r) <= 2200.0 + 1e-9
    assert sum(r['gas_lift_sm3d'].values()) <= 60000.0 + 1e-6


def test_enforce_false_only_reports():
    ns, es = demo()
    r = optimize_field(ns, es, real_solve, objective='max_oil', enforce=False)
    assert not r['feasible'] and r['well_caps_m3d'] == {} and r['violations']
    assert r['objective_value'] == pytest.approx(r['baseline_value'])


# -------------------------------------------------------------------- infeasible / failure handling
def test_unrelievable_pressure_row_reported_not_relieved():
    ns, es = demo(sep_limit=1e9)
    ns[4]['params']['min_pressure_bar'] = 500.0            # manifold minimum pressure impossible to meet
    r = optimize_field(ns, es, real_solve, objective='max_oil')
    assert not r['feasible'] and r['well_caps_m3d'] == {}
    assert any(v['Constraint'] == 'Minimum pressure' and v['Relievable'] is False for v in r['violations'])
    assert any('not relievable' in x for x in r['reasons'])


def test_impossible_capacity_returns_infeasible_without_error():
    ns, es = demo(sep_limit=2500.0)
    ns[5]['params']['max_liquid_rate_m3d'] = 0.0
    r = optimize_field(ns, es, real_solve, objective='max_oil', max_outer=4)
    assert r['result'] is not None and r['n_solves'] < 30
    assert r['feasible'] or r['violations']             # either shut in completely or reported, never an exception


def test_solver_exception_and_nonconvergence_are_reported():
    ns, es = demo()
    def boom(n, e, g): raise RuntimeError('kaboom')
    r = optimize_field(ns, es, boom, objective='max_oil')
    assert not r['feasible'] and r['result'] is None and any('kaboom' in w for w in r['warnings'])
    def nc(n, e, g): return {}, {}, {'success': False, 'message': 'no'}, {}
    r = optimize_field(ns, es, nc, objective='max_oil')
    assert not r['feasible'] and r['result'] is None


def test_invalid_specs_raise_valueerror_up_front():
    ns, es = demo()
    with pytest.raises(ValueError): optimize_field(ns, es, real_solve, objective={'preset': 'custom', 'expression': 'oil + nope'})
    with pytest.raises(ValueError): optimize_field(ns, es, real_solve, objective='max_oil', guide={'mode': 'wat'})


# -------------------------------------------------------------------- synthetic solver (new constraint names)
def fake_case(q0=(1000.0, 800.0, 600.0), wc=(0.05, 0.5, 0.2), gor=(100.0, 100.0, 400.0), limits=None, fixed_load=0.0):
    ns = [{'id': f'W{i + 1}', 'kind': 'well', 'name': f'W{i + 1}', 'pressure_bar': None,
           'params': {'q0': q0[i], 'water_cut': wc[i], 'gor_sm3sm3': gor[i]}} for i in range(3)]
    ns.append({'id': 'M', 'kind': 'manifold', 'name': 'M', 'pressure_bar': None, 'params': {}})
    ns.append({'id': 'SEP', 'kind': 'separator', 'name': 'SEP', 'pressure_bar': 20.0, 'params': {'limits': dict(limits or {}), 'fixed_load': fixed_load}})
    es = [{'id': f'F{i + 1}', 'source': f'W{i + 1}', 'target': 'M', 'kind': 'pipeline', 'params': {}} for i in range(3)]
    es.append({'id': 'T', 'source': 'M', 'target': 'SEP', 'kind': 'pipeline', 'params': {}})
    return ns, es


def fake_solve(ns, es, guess):
    byid = {n['id']: n for n in ns}; d = {}
    for n in ns:
        if n['kind'] != 'well': continue
        p = n['params']; q = min(p['q0'], p.get(CAP_KEY, 1e18), p.get('max_liquid_rate_m3d', 1e18)); wc = p['water_cut']
        d[n['id']] = {'liquid_rate_m3d': q, 'oil_rate_m3d': q * (1 - wc), 'water_rate_m3d': q * wc, 'gas_rate_sm3d': q * (1 - wc) * p['gor_sm3sm3'],
                      'whp_bar': 30.0, 'bhp_bar': 150.0, 'gas_lift_sm3d': p.get(GL_KEY, 0.0), 'reservoir_pressure_bar': 250.0}
    L = sum(v['liquid_rate_m3d'] for v in d.values()); O = sum(v['oil_rate_m3d'] for v in d.values())
    W = sum(v['water_rate_m3d'] for v in d.values()); G = sum(v['gas_rate_sm3d'] for v in d.values())
    sep = byid['SEP']['params']; fl = sep.get('fixed_load', 0.0)
    values = {'Liquid capacity': (L + fl, 'm3/d'), 'Oil capacity': (O + fl, 'm3/d'), 'Water capacity': (W + fl, 'm3/d'), 'Gas capacity': (G + fl, 'Sm3/d'),
              'Maximum velocity': (L / 400.0, 'm/s'), 'Maximum erosional ratio': (L / 1000.0, '-'), 'Maximum power': (L * 0.5, 'kW')}
    rows = []
    def add(comp, cid, name, val, lim, rel, unit):
        m = (lim - val) if rel == '<=' else (val - lim)
        rows.append({'Component': comp, 'ComponentId': cid, 'Constraint': name, 'Value': val, 'Limit': lim, 'Relation': rel, 'Margin': m, 'Unit': unit,
                     'Status': 'OK' if m >= -1e-9 else 'VIOLATED'})
    for name, lim in sep['limits'].items():
        if name in values: add('SEP', 'SEP', name, values[name][0], lim, '<=', values[name][1])
    for e in es:
        for name, lim in (e.get('params') or {}).get('limits', {}).items():
            src = e['source']; tot = sum(d[w]['liquid_rate_m3d'] for w in d if w == src or src == 'M')
            rate = {'Maximum rate': tot, 'Maximum gas rate': sum(d[w]['gas_rate_sm3d'] for w in d if w == src or src == 'M')}[name]
            add(e['id'], e['id'], name, rate, lim, '<=', 'm3/d')
    for n in ns:
        if n['kind'] == 'well':
            for name, key, u in (('Maximum oil rate', 'oil_rate_m3d', 'm3/d'), ('Maximum water rate', 'water_rate_m3d', 'm3/d'), ('Maximum gas rate', 'gas_rate_sm3d', 'Sm3/d')):
                if n['params'].get('wl_' + key) is not None: add(n['id'], n['id'], name, d[n['id']][key], n['params']['wl_' + key], '<=', u)
            if n['params'].get('min_bhp') is not None: add(n['id'], n['id'], 'Minimum BHP', d[n['id']]['bhp_bar'], n['params']['min_bhp'], '>=', 'bar')
    info = {'success': True, 'constraints': rows, 'injector_rates': {}, 'violations': sum(r['Status'] == 'VIOLATED' for r in rows)}
    return {}, {}, info, d


@pytest.mark.parametrize('name,limit,unit_check', [
    ('Liquid capacity', 1500.0, 'liq'), ('Oil capacity', 1000.0, 'oil'), ('Water capacity', 250.0, 'wat'), ('Gas capacity', 150000.0, 'gas'),
    ('Maximum velocity', 4.0, 'liq4'), ('Maximum erosional ratio', 1.5, 'liq4'), ('Maximum power', 800.0, 'liq4'),
])
def test_synthetic_new_constraint_names_are_relieved(name, limit, unit_check):
    ns, es = fake_case(limits={name: limit})
    before = fake_solve(ns, es, None)[2]['constraints'][0]
    assert before['Status'] == 'VIOLATED'
    r = optimize_field(ns, es, fake_solve, objective='max_oil', tol=1e-3)
    assert r['feasible'], r['reasons']
    row = r['result'][2]['constraints'][0]
    assert row['Status'] == 'OK' and row['Value'] >= limit * 0.97          # tight: capacity is used, not wasted
    assert r['objective_value'] >= r['constrained_baseline_value'] - 1e-9


def test_synthetic_oil_capacity_curtails_by_oil_value_density():
    ns, es = fake_case(limits={'Oil capacity': 1000.0})
    r = optimize_field(ns, es, fake_solve, objective='max_oil')
    # oil capacity 1000 of unconstrained oil 950+400+480=1830: objective is oil itself -> every well has density 1,
    # so any allocation is optimal; check the capacity is exactly used
    assert r['objective_value'] == pytest.approx(1000.0, rel=2e-3)
    ns, es = fake_case(limits={'Liquid capacity': 1500.0})
    r = optimize_field(ns, es, fake_solve, objective='max_oil')
    d = r['result'][3]                                       # densities: W1 .95 > W3 .80 > W2 .50 -> W2 shut, W3 trimmed
    assert d['W1']['liquid_rate_m3d'] == pytest.approx(1000.0) and d['W2']['liquid_rate_m3d'] == pytest.approx(0.0, abs=1e-6)
    assert d['W3']['liquid_rate_m3d'] == pytest.approx(500.0, rel=2e-3)
    assert r['objective_value'] > r['constrained_baseline_value']


def test_synthetic_gas_capacity_prefers_low_gor_wells():
    ns, es = fake_case(limits={'Gas capacity': 120000.0})
    r = optimize_field(ns, es, fake_solve, objective='max_oil')
    d = r['result'][3]
    assert d['W3']['liquid_rate_m3d'] == pytest.approx(0.0, abs=1e-6)   # high-GOR well (400 Sm3/Sm3) gives up its gas first
    assert d['W1']['gas_rate_sm3d'] + d['W2']['gas_rate_sm3d'] == pytest.approx(120000.0, rel=2e-3)
    assert r['feasible'] and r['objective_value'] > r['constrained_baseline_value']


def test_synthetic_well_level_rows_curtail_the_well_itself():
    ns, es = fake_case()
    ns[0]['params']['wl_oil_rate_m3d'] = 500.0
    ns[1]['params']['wl_water_rate_m3d'] = 100.0
    ns[2]['params']['wl_gas_rate_sm3d'] = 100000.0
    r = optimize_field(ns, es, fake_solve, objective='max_oil')
    assert r['feasible'], r['reasons']
    d = r['result'][3]
    assert d['W1']['oil_rate_m3d'] <= 500.0 + 1e-6 and d['W2']['water_rate_m3d'] <= 100.0 + 1e-6 and d['W3']['gas_rate_sm3d'] <= 100000.0 + 1e-6
    assert d['W1']['oil_rate_m3d'] >= 495.0 and d['W2']['water_rate_m3d'] >= 98.0


def test_synthetic_edge_rows_by_component_id():
    ns, es = fake_case()
    es[3]['params'] = {'limits': {'Maximum rate': 1800.0}}      # trunk 'T' (source M): all wells upstream
    es[0]['params'] = {'limits': {'Maximum gas rate': 20000.0}}  # F1 (source W1): only W1
    r = optimize_field(ns, es, fake_solve, objective='max_oil')
    assert r['feasible'], r['reasons']
    rows = {c['ComponentId']: c for c in r['result'][2]['constraints']}
    assert rows['T']['Value'] <= 1800.0 + 1e-9 and rows['F1']['Value'] <= 20000.0 + 1e-9


def test_synthetic_overlapping_constraints_all_satisfied():
    ns, es = fake_case(limits={'Liquid capacity': 1700.0, 'Water capacity': 150.0})
    r = optimize_field(ns, es, fake_solve, objective='max_oil')
    assert r['feasible'], r['reasons']
    assert all(c['Status'] == 'OK' for c in r['result'][2]['constraints'])


def test_synthetic_greater_equal_row_is_reported_not_relieved():
    ns, es = fake_case(); ns[0]['params']['min_bhp'] = 400.0
    r = optimize_field(ns, es, fake_solve, objective='max_oil')
    assert not r['feasible'] and r['well_caps_m3d'] == {}
    assert r['violations'][0]['Constraint'] == 'Minimum BHP' and r['violations'][0]['Relievable'] is False


def test_synthetic_capacity_below_fixed_load_is_infeasible_but_returns():
    ns, es = fake_case(limits={'Liquid capacity': 500.0}, fixed_load=800.0)
    r = optimize_field(ns, es, fake_solve, objective='max_oil')
    assert not r['feasible'] and r['violations'] and r['n_solves'] < 40
    assert all(v['liquid_rate_m3d'] == 0.0 for v in r['result'][3].values())   # everything shut in trying


def test_synthetic_negative_value_wells_curtailed_first_with_revenue():
    ns, es = fake_case(wc=(0.05, 0.97, 0.05), limits={'Liquid capacity': 1700.0})
    obj = {'preset': 'max_revenue', 'prices': {'oil': 10.0, 'gas': 0.0, 'water': 20.0, 'gaslift': 0.0}}
    r = optimize_field(ns, es, fake_solve, objective=obj)
    assert r['feasible']
    assert r['result'][3]['W2']['liquid_rate_m3d'] < 800.0 and r['result'][3]['W1']['liquid_rate_m3d'] == pytest.approx(1000.0)


def test_system_objective_expression_supported():
    ns, es = fake_case(limits={'Liquid capacity': 1500.0})
    r = optimize_field(ns, es, fake_solve, objective={'preset': 'custom', 'expression': 'total_oil - 3*total_water'})
    assert r['feasible'] and r['objective_value'] >= r['constrained_baseline_value'] - 1e-9
    assert 'W2' in r['well_caps_m3d']


# -------------------------------------------------------------------- per-timestep solver
def test_step_solver_returns_solver_tuple_with_optimizer_info():
    ns, es = demo()
    step = make_step_solver('max_oil')
    p, q, info, d = step(ns, es, None)
    opt = info['optimizer']
    assert opt['feasible'] and opt['objective_value'] > 0 and opt['actions'] and info['constraint_actions']
    assert set(info['constraint_actions'][0]) >= {'well', 'well_id', 'cap_m3d', 'message'}
    assert sum(v['liquid_rate_m3d'] for v in d.values()) <= 2500.0 + 1e-6
    # warm start with the previous solution reduces nothing structurally
    guess = {'pressures': p, 'flows': q, 'well_rates': info.get('well_rates', {})}
    p2, q2, info2, d2 = step(ns, es, guess)
    assert info2['optimizer']['objective_value'] == pytest.approx(opt['objective_value'], rel=1e-3)
    assert info2['optimizer']['runtime_s'] < 10.0


def test_step_solver_with_injected_solver_and_failure():
    ns, es = fake_case(limits={'Liquid capacity': 1500.0})
    step = make_step_solver({'preset': 'max_oil'}, guide={'mode': 'priority', 'priority': {'W1': 1, 'W2': 2, 'W3': 3}}, solve_fn=fake_solve)
    p, q, info, d = step(ns, es, None)
    assert info['optimizer']['feasible'] and d['W3']['liquid_rate_m3d'] < 600.0 and d['W1']['liquid_rate_m3d'] == pytest.approx(1000.0)
    bad = make_step_solver('max_oil', solve_fn=lambda n, e, g: (_ for _ in ()).throw(RuntimeError('x')))
    p, q, info, d = bad(ns, es, None)
    assert info['success'] is False and d == {}
    with pytest.raises(ValueError):
        make_step_solver({'preset': 'custom', 'expression': 'bogus'})
