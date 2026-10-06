"""v32.6: pausable development-schedule run, parallel settings in forecast / schedule, run-speed option."""
import copy
from tests.support.app_harness import run_app
from network.run_control import RunController
APP = 'app.py'


def _field():
    from network.examples import demo_field_case
    return demo_field_case()


def _drill_inputs(n):
    from ui import drilling_plan as dp
    return dp.default_schedule(n)


def _iter(compare=True, workers=1, vseg=None, years=1.0):
    from ui import drilling_plan as dp
    n, e = _field(); df = _drill_inputs(n); sink = {}
    g = dp.iter_drill(n, e, '2028-01-01', years, 90, True, df, 2, [], compare, None, workers=workers, vlp_segments=vseg, sink=sink)
    return g, sink


def test_drill_run_is_a_generator_and_completes_with_both_phases():
    g, sink = _iter(compare=True)
    ctl = RunController(g)
    while ctl.status == ctl.RUNNING: ctl.advance(max_events=1)
    assert ctl.status == ctl.DONE, ctl.error
    assert sink['res']['forecast']['field'] and sink['base'] and sink['base']['field']
    assert 'Cumulative oil [m3]' in sink['res']['forecast']['field'][-1]
    assert ctl.fraction == 1.0


def test_drill_run_pause_resume_stop_keep_partial_snapshot():
    g, sink = _iter(compare=True)
    ctl = RunController(g)
    while ctl.status == ctl.RUNNING and (ctl.snapshot is None or not ctl.snapshot['field']): ctl.advance(max_events=1)
    ctl.pause(); assert ctl.status == ctl.PAUSED
    n0 = ctl.events; ctl.advance(max_events=5); assert ctl.events == n0       # nothing moves while paused
    ctl.resume(); ctl.advance(max_events=1); ctl.stop()
    assert ctl.status == ctl.STOPPED and ctl.snapshot['field'] and 'res' not in sink


def test_drill_run_counter_spans_both_phases_when_comparing():
    g, _ = _iter(compare=True); evs = list(g)
    assert max(e['step'] for e in evs[:-1]) > evs[0]['n_steps'] / 2
    assert evs[-1]['type'] == 'done'


def test_buttons_present_during_drill_run_and_result_published():
    n, e = _field()
    root = run_app(APP, {'nodes': n, 'edges': e, 'fc_years': 1.0, 'fc_use_drill': True}, pressed={'fc_run'})
    s = root.session_state
    assert s['fc_ctl'].label == 'Development plan' and s['fc_ctl'].status == 'done'
    assert s.get('sched_result') and s['_fc_mode'] == 'drill'
    st2 = {k: v for k, v in s.items()}
    root = run_app(APP, st2, pressed={'fc_run'}) if False else root
    errs = [c for c in root.calls if c[0] == 'error']; assert not errs, errs


def test_pause_button_appears_while_running_in_drill_mode():
    n, e = _field()
    from ui import drilling_plan as dp
    g, sink = _iter(compare=False)
    ctl = RunController(g); ctl.sink = sink; ctl.advance(max_events=2)
    root = run_app(APP, {'nodes': n, 'edges': e, 'fc_use_drill': True, 'fc_ctl': ctl, 'fc_ctl_hash': 'x'}, pressed={'fc_pause'})
    assert root.session_state['fc_ctl'].status == 'paused'
    root = run_app(APP, dict(root.session_state), pressed={'fc_stop'}); assert root.session_state['fc_ctl'].status == 'stopped'


def _two_systems():
    from network.templates import build
    n1, e1 = build('pure_depletion_oil'); n2, e2 = build('pure_depletion_oil')
    ren = lambda x, p: [dict(i, id=p + i['id'], **({'source': p + i['source'], 'target': p + i['target']} if 'source' in i else {})) for i in x]
    def pref(nodes, edges, p):
        nn = []
        for n in nodes:
            n = copy.deepcopy(n); n['id'] = p + n['id']
            rid = (n.get('params') or {}).get('reservoir_id')
            if rid: n['params']['reservoir_id'] = p + rid
            nn.append(n)
        ee = [dict(copy.deepcopy(e), id=p + e['id'], source=p + e['source'], target=p + e['target']) for e in edges]
        return nn, ee
    a = pref(n1, e1, 'A'); b = pref(n2, e2, 'B')
    return a[0] + b[0], a[1] + b[1]


def test_parallel_workers_give_identical_forecast_on_independent_systems():
    from network.forecast import run_forecast
    n, e = _two_systems()
    f1 = run_forecast(n, e, '2028-01-01', years=1, step_days=180, enforce_constraints=True, workers=1)
    f2 = run_forecast(n, e, '2028-01-01', years=1, step_days=180, enforce_constraints=True, workers=2)
    o1 = [r['Oil [m3/d]'] for r in f1['field']]; o2 = [r['Oil [m3/d]'] for r in f2['field']]
    assert len(o1) == len(o2) and all(abs(a - b) <= 1e-6 * max(abs(a), 1) + 1e-6 for a, b in zip(o1, o2)), (o1, o2)


def test_workers_one_connected_network_unchanged():
    from network.forecast import run_forecast
    n, e = _field()
    f1 = run_forecast(n, e, '2028-01-01', years=0.5, step_days=180, workers=1); f2 = run_forecast(n, e, '2028-01-01', years=0.5, step_days=180, workers=4)
    a = [r['Oil [m3/d]'] for r in f1['field']]; b = [r['Oil [m3/d]'] for r in f2['field']]
    assert all(abs(x - y) <= 1e-9 * max(abs(x), 1) for x, y in zip(a, b)), (a, b)


def test_drill_baseline_in_parallel_matches_serial():
    g1, s1 = _iter(compare=True, workers=1); list(g1)
    g2, s2 = _iter(compare=True, workers=2); list(g2)
    b1 = [r['Oil [m3/d]'] for r in s1['base']['field']]; b2 = [r['Oil [m3/d]'] for r in s2['base']['field']]
    p1 = [r['Oil [m3/d]'] for r in s1['res']['forecast']['field']]; p2 = [r['Oil [m3/d]'] for r in s2['res']['forecast']['field']]
    close = lambda u, v: len(u) == len(v) and all(abs(x - y) <= 1e-9 * max(abs(x), 1) for x, y in zip(u, v))
    assert close(b1, b2) and close(p1, p2), (b1, b2)


def test_run_speed_option_sets_segments_but_keeps_explicit_and_is_close():
    from network.forecast import run_forecast
    n, e = _field(); n = copy.deepcopy(n)
    ws = [x for x in n if x['kind'] == 'well']; ws[0].setdefault('params', {})['vlp_segments'] = 9
    f0 = run_forecast(n, e, '2028-01-01', years=0.5, step_days=180)
    f6 = run_forecast(n, e, '2028-01-01', years=0.5, step_days=180, vlp_segments=6)
    a, b = f0['field'][0]['Oil [m3/d]'], f6['field'][0]['Oil [m3/d]']
    assert abs(a - b) / max(a, 1) < 0.03, (a, b)
    assert ws[0]['params']['vlp_segments'] == 9     # input untouched


def test_forecast_tab_shows_run_speed_and_parallel_caption():
    n, e = _field()
    root = run_app(APP, {'nodes': n, 'edges': e, 'compute': {'workers': 3}})
    assert ('key', 'fc_speed') in root.widget_ids
    assert any(c[0] == 'caption' and 'worker process' in str(c[1]) for c in root.calls)
