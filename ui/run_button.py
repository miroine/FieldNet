"""Run buttons with a progress bar that turn green when the job has finished.

Usage (no re-indenting of the existing body is needed)::

    for rb in run_button(st, '▶ Run calibration', key='cal_run', model_hash=graph_hash(nodes, edges)):
        ... long job ...
        rb.progress(0.4, 'Fitting well P2')       # optional, drives the bar
        # rb.fail('message') marks the run as failed (red) instead of green

The loop body only runs on the click. The button turns green once the body completes (and stays green on later reruns while the
model is unchanged), amber while running, red after ``rb.fail``. Colours use Streamlit's ``st-key-<key>`` container class
(Streamlit >= 1.39); on older versions the button just keeps its normal colour and the progress bar / status line still work.
"""
from __future__ import annotations
import time

COLORS = {'done': ('#1b8a3a', '#157030'), 'running': ('#d98e04', '#b87700'), 'failed': ('#c0392b', '#992d22'),
          'pending': ('#e8710a', '#b85a08'), 'applied': ('#1b8a3a', '#157030'), 'idle': ('#5b6573', '#454d59')}


def _css(key, status):
    bg, border = COLORS[status]
    sel = f'.st-key-{key} button, .st-key-{key} [data-testid="stBaseButton-primary"], .st-key-{key} [data-testid="stBaseButton-secondary"]'
    return f'<style>{sel}{{background-color:{bg} !important;border-color:{border} !important;color:#fff !important;}}</style>'


def style_button(st, key, status):
    """Colour an already-rendered or to-be-rendered keyed button ('done' | 'running' | 'failed')."""
    if status in COLORS: st.markdown(_css(key, status), unsafe_allow_html=True)


class RunHandle:
    def __init__(self, st, key, label, model_hash=None):
        self.st, self.key, self.label, self.model_hash = st, key, label, model_hash; self.t0 = time.perf_counter(); self.failed = None
        self._bar = st.progress(0.0, text='Starting…'); self._frac = 0.0
        style_button(st, key, 'running')

    def progress(self, fraction, text=''):
        self._frac = min(max(float(fraction), 0.0), 1.0)
        self._bar.progress(self._frac, text=f'{text}  ·  {time.perf_counter() - self.t0:.0f} s' if text else f'{time.perf_counter() - self.t0:.0f} s')

    def fail(self, message):
        self.failed = str(message); self.st.error(self.failed)

    def finish(self):
        """Mark the run finished (green, or red after ``fail``). Idempotent; called automatically by the ``for`` form."""
        if getattr(self, '_finished', False): return
        self._finished = True
        status = 'failed' if self.failed else 'done'
        self.st.session_state[f'_rb_{self.key}'] = {'status': status, 'hash': self.model_hash, 'elapsed': self.elapsed}
        self._bar.progress(1.0, text=('Failed' if self.failed else f'Finished in {self.elapsed:.1f} s'))
        style_button(self.st, self.key, status)

    @property
    def elapsed(self): return time.perf_counter() - self.t0


def start_run(st, label, key, model_hash=None, type='secondary', use_container_width=True, disabled=False, help=None):
    """Non-loop form: returns a :class:`RunHandle` when clicked (else None). Call ``rb.finish()`` after the job (``rb.fail(msg)`` first on error)."""
    ss = st.session_state; rec = ss.get(f'_rb_{key}')
    if rec and rec.get('status') in ('done', 'failed') and (model_hash is None or rec.get('hash') == model_hash): style_button(st, key, rec['status'])
    kw = {'key': key, 'type': type, 'use_container_width': use_container_width, 'disabled': disabled}
    if help: kw['help'] = help
    if not st.button(label, **kw): return None
    return RunHandle(st, key, label, model_hash)


def run_button(st, label, key, model_hash=None, **kw):
    """Loop form (see module docstring): the body runs once, only when clicked; finishing is automatic."""
    rb = start_run(st, label, key, model_hash, **kw)
    if rb is None: return
    yield rb
    rb.finish()


def apply_notice(st, text, kind='applied'):
    """Coloured message: 'applied' green, 'pending' orange (changes waiting for Apply), 'failed' red."""
    c = {'applied': ('#e6f4ea', '#1b8a3a', '✔'), 'pending': ('#fff1e0', '#e8710a', '●'), 'failed': ('#fdecea', '#c0392b', '✖')}[kind]
    st.markdown(f'<div style="background:{c[0]};border-left:5px solid {c[1]};padding:.5rem .8rem;border-radius:4px;color:#1d2530;margin:.25rem 0"><b style="color:{c[1]}">{c[2]}</b> {text}</div>', unsafe_allow_html=True)


def style_form_submit(st, status='pending'):
    """Colour the primary submit buttons of forms (Apply buttons of the data tables) - orange while changes wait for Apply."""
    bg, border = COLORS[status]
    st.markdown(f'<style>[data-testid="stBaseButton-primaryFormSubmit"],button[kind="primaryFormSubmit"]{{background-color:{bg} !important;border-color:{border} !important;color:#fff !important;font-weight:700}}</style>', unsafe_allow_html=True)
