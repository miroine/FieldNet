from network.examples import demo_field_case
from network.forecast import iter_forecast, run_forecast
from network.run_control import RunController


def _gen(years=1.0, step=90, **kw):
    n, e = demo_field_case(); return iter_forecast(n, e, '2026-01-01', years, step, **kw)


def test_progress_events_have_detail():
    c = RunController(_gen()); seen = []
    while c.status == c.RUNNING:
        ev = c.advance(1); seen.append(ev['stage'])
    assert c.status == c.DONE and c.fraction == 1.0
    assert any(s.startswith('Solving network') for s in seen) and any(s.startswith('Completed') for s in seen)
    assert c.snapshot['field']


def test_pause_resume_gives_identical_result_to_uninterrupted_run():
    n, e = demo_field_case(); ref = run_forecast(n, e, '2026-01-01', 1.0, 90)
    c = RunController(_gen())
    while c.status == c.RUNNING and len(c.snapshot['field'] if c.snapshot else []) < 2: c.advance(1)
    c.pause(); assert c.status == c.PAUSED
    k = c.events; c.advance(5); assert c.events == k            # nothing advances while paused
    partial = len(c.snapshot['field']); assert 0 < partial < len(ref['field'])
    c.resume()
    while c.status == c.RUNNING: c.advance(1)
    assert c.status == c.DONE
    assert [r['Cumulative oil [Sm3]'] for r in c.snapshot['field']] == [r['Cumulative oil [Sm3]'] for r in ref['field']]


def test_stop_keeps_partial_result():
    c = RunController(_gen(years=3))
    while c.status == c.RUNNING and len(c.snapshot['field'] if c.snapshot else []) < 3: c.advance(1)
    c.stop(); assert c.status == c.STOPPED and not c.active and 3 <= len(c.snapshot['field']) < 13
    c.resume(); assert c.status == c.STOPPED


def test_run_forecast_progress_callback_can_stop():
    n, e = demo_field_case(); calls = []
    r = run_forecast(n, e, '2026-01-01', 3.0, 90, progress=lambda ev: calls.append(ev['type']) or (len(r_rows(ev)) < 2))
    assert r.get('stopped') and len(r['field']) == 2


def r_rows(ev): return (ev.get('result') or {}).get('field', [])


def test_store_elements_off_skips_element_tables():
    n, e = demo_field_case(); r = run_forecast(n, e, '2026-01-01', 0.5, 90, store_elements=False)
    assert r['field'] and not r['nodes']
