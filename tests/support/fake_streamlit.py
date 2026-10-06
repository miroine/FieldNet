"""Minimal stand-in for ``streamlit`` so UI helper modules can be exercised without a browser / Streamlit install.
Widgets return the value stored in ``session_state[key]`` (seeded by the synced_* helpers) or the call's own default."""
from contextlib import contextmanager


class _State(dict):
    __getattr__ = dict.get
    def __setattr__(self, k, v): self[k] = v


class FakeSt:
    def __init__(self): self.session_state = _State(); self.calls = []; self.buttons = set()
    def _w(self, name, label, key=None, default=None, **kw):
        self.calls.append((name, label, key))
        if key is not None and key in self.session_state: return self.session_state[key]
        return default
    def number_input(self, label, key=None, value=0.0, **kw): return self._w('number_input', label, key, kw.get('min_value', value) if value is None else value)
    def slider(self, label, *a, key=None, **kw): return self._w('slider', label, key, a[2] if len(a) > 2 else (a[0] if a else 0))
    def selectbox(self, label, options, key=None, **kw):
        options = list(options); v = self._w('selectbox', label, key, options[0] if options else None); return v
    def checkbox(self, label, key=None, value=False, **kw): return self._w('checkbox', label, key, value)
    def text_input(self, label, key=None, value='', **kw): return self._w('text_input', label, key, value)
    def text_area(self, label, key=None, value='', **kw): return self._w('text_area', label, key, value)
    def button(self, label, key=None, **kw): self.calls.append(('button', label, key)); return (key or label) in self.buttons
    def data_editor(self, df, **kw): return df
    def file_uploader(self, *a, **kw): return None
    def columns(self, n, **kw): n = len(n) if isinstance(n, (list, tuple)) else n; return [self] * n
    @contextmanager
    def expander(self, *a, **kw): yield self
    popover = expander
    def metric(self, *a, **kw): self.calls.append(('metric', a, None))
    def __getattr__(self, name):
        if name.startswith('_'): raise AttributeError(name)
        return lambda *a, **kw: self.calls.append((name, a, None))


def install_fake_plotly():
    """Register MagicMock plotly modules when plotly is not installed (smoke tests only)."""
    import sys
    from unittest.mock import MagicMock
    try:
        import plotly.express  # noqa
        return False
    except ImportError:
        pass
    for m in ('plotly', 'plotly.express', 'plotly.graph_objects', 'plotly.subplots'): sys.modules[m] = MagicMock(name=m)
    return True
