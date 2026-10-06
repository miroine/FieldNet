from types import SimpleNamespace
from tests.support.app_harness import DG, _State
from ui.run_button import run_button, _css


def _st(pressed=()):
    root = DG(); root.root = root; root._state = _State(); root.calls = []; root.pressed = set(pressed); root.widget_ids = set(); root.progress_calls = []
    return root


def test_button_body_runs_only_on_click_and_goes_green():
    st = _st(); ran = []
    for rb in run_button(st, 'Go', key='k1', model_hash='h'): ran.append(1)
    assert not ran and '_rb_k1' not in st.session_state
    st = _st({'k1'})
    for rb in run_button(st, 'Go', key='k1', model_hash='h'): rb.progress(0.5, 'half'); ran.append(1)
    assert ran == [1] and st.session_state['_rb_k1']['status'] == 'done'


def test_failure_and_stale_model_hash():
    st = _st({'k2'})
    for rb in run_button(st, 'Go', key='k2', model_hash='h'): rb.fail('boom')
    assert st.session_state['_rb_k2']['status'] == 'failed'
    st2 = _st(); st2._state['_rb_k3'] = {'status': 'done', 'hash': 'old'}
    n0 = len(st2.calls)
    for rb in run_button(st2, 'Go', key='k3', model_hash='new'): pass
    assert not any(c[0] == 'markdown' for c in st2.calls)       # stale -> not green


def test_css_targets_streamlit_key_class():
    assert '.st-key-abc button' in _css('abc', 'done') and '#1b8a3a' in _css('abc', 'done')
