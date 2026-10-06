from network.examples import demo_field_case
from network.uncertainty import UncertainParameter, apply_sample
from network.field_development import DevelopmentScenario
from network.uncertainty_catalog import target_options, parameters_for, make_parameter


def test_catalog_discovers_targets_and_parameters():
    n, e = demo_field_case(); opts = dict(target_options(n, e))
    assert 'kind:well' in opts and any('reservoir' in v or 'tank' in v.lower() for v in opts.values())
    labels = {p['label'] for p in parameters_for(n, e, 'kind:well')}; assert {'Productivity index (PI)', 'Water cut'} <= labels


def test_group_target_applies_one_shared_factor_to_every_well():
    n, e = demo_field_case(); spec = next(p for p in parameters_for(n, e, 'kind:well') if p['label'].startswith('Productivity'))
    row = make_parameter('kind:well', 'All wells', spec, 0.7, 1.0, 1.3)
    up = UncertainParameter(name=row['name'], path=row['path'], target_id=row['target_id'], operation='multiply', distribution='triangular', low=0.7, mode=1.0, high=1.3)
    nn, ee, ss = apply_sample(n, e, DevelopmentScenario('x', '2026-01-01', 1.0, 90), [up], {row['name']: 1.2})
    for a, b in zip([x for x in n if x['kind'] == 'well'], [x for x in nn if x['kind'] == 'well']):
        assert abs(b['params']['pi_m3d_bar'] - 1.2 * a['params']['pi_m3d_bar']) < 1e-9


def test_normal_uses_p10_p90_and_validates_order():
    spec = {'label': 'X', 'path': 'params.x'}; r = make_parameter('A', 'A', spec, 0.8, 1.0, 1.2, 'normal'); assert abs(r['std'] - 0.4 / 2.5631) < 1e-3
    try: make_parameter('A', 'A', spec, 1.2, 1.0, 0.8); assert False
    except ValueError: pass
