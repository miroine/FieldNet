from network.examples import demo_field_case
from network.sensitivity import tornado, envelope, default_parameters, SensParam, total_oil_rate, apply_value


def _case():
    return demo_field_case()


def test_default_parameters_discovered():
    n, e = _case(); labels = [p.label for p in default_parameters(n, e)]
    for need in ('Reservoir pressure', 'Productivity index', 'Water cut', 'GOR', 'Separator pressure', 'Flowline diameter', 'Flowline roughness'):
        assert need in labels, labels
    assert any('max_liquid_rate_m3d' in l for l in labels)


def test_tornado_sorted_and_directional():
    n, e = _case(); rows = tornado(n, e, [('Reservoir pressure', 'kind:reservoir.params.reservoir_pressure_bar', 0.9, 1.1, 'mult'),
                                          ('PI', 'kind:well.params.pi_m3d_bar', 0.8, 1.2, 'mult'),
                                          ('Sep capacity', 'SEP.params.max_liquid_rate_m3d', 0.5, 1.5, 'mult')], enforce_constraints=True)
    sw = [r['swing'] for r in rows]; assert sw == sorted(sw, reverse=True) and len(rows) == 3
    by = {r['parameter']: r for r in rows}
    assert by['PI']['metric_high'] > by['PI']['metric_low']
    assert by['Sep capacity']['metric_low'] < by['Sep capacity']['metric_base'] - 1.0      # 50 % cap (2250) chokes the field
    assert abs(by['Sep capacity']['metric_high'] - by['Sep capacity']['metric_base']) < 1e-3 * by['Sep capacity']['metric_base']  # not binding when raised
    assert by['PI']['metric_base'] > 1000


def test_input_graph_not_mutated():
    n, e = _case(); before = n[0]['params']['reservoir_pressure_bar']
    tornado(n, e, [('RP', 'kind:reservoir.params.reservoir_pressure_bar', 0.9, 1.1, 'mult')]); assert n[0]['params']['reservoir_pressure_bar'] == before


def test_tornado_parallel_matches_serial():
    n, e = _case(); ps = [('PI', 'kind:well.params.pi_m3d_bar', 0.8, 1.2, 'mult')]
    a = tornado(n, e, ps, workers=1); b = tornado(n, e, ps, workers=2)
    assert abs(a[0]['metric_low'] - b[0]['metric_low']) < 1e-6 and abs(a[0]['metric_high'] - b[0]['metric_high']) < 1e-6


def test_envelope_grid_and_active_constraint():
    n, e = _case()
    env = envelope(n, e, ('PI mult', 'kind:well.params.pi_m3d_bar', 'mult'), ('Sep capacity', 'SEP.params.max_liquid_rate_m3d', 'abs'),
                   {'x': [0.7, 1.0, 1.5], 'y': [2000.0, 4500.0]})
    assert len(env['metric']) == 2 and len(env['metric'][0]) == 3
    assert env['metric'][0][2] <= 2000.0 * 1.0001
    assert 'SEPARATOR: Liquid capacity' in env['active'][0][2]        # 2000 m3/d cap binds when PI is high
    assert env['metric'][1][0] < env['metric'][1][2]                  # more PI, more oil under the 4500 cap
    assert env['metric'][0][2] < env['metric'][1][2]


def test_custom_getter_setter():
    n, e = _case()
    def get(ns, es): return ns[0]['params']['reservoir_pressure_bar']
    def setv(ns, es, v): ns[0]['params']['reservoir_pressure_bar'] = v
    rows = tornado(n, e, [SensParam('RP custom', (get, setv), 260.0, 300.0)]); assert rows[0]['metric_high'] > rows[0]['metric_low'] and rows[0]['base_value'] == 290.0
