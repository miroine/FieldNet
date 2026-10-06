"""Execute app.py end-to-end against a fake Streamlit (no browser). Widgets return their stored/default value;
``st.rerun()`` re-executes the script like Streamlit does. Catches NameErrors / wrong calls in code paths that
unit tests do not reach. It does NOT prove the real widgets behave - see the report."""
import sys, types, datetime, runpy, io, contextlib
from unittest.mock import MagicMock
import pandas as pd


class Rerun(BaseException): pass   # like Streamlit's RerunException


class _State(dict):
    def __getattr__(self, k):
        try: return self[k]
        except KeyError: raise AttributeError(k)
    def __setattr__(self, k, v): self[k] = v
    def pop(self, k, *d): return super().pop(k, *d)


class _Child:
    """Columns / tabs / containers: like real Streamlit DeltaGenerators they have NO session_state (attribute falls through to a fake element function)."""


class DG:
    """Fake DeltaGenerator / module. All containers return self; widgets return stored or default values."""
    def __init__(self, root=None): self.root = root or self
    @property
    def session_state(self):
        if self.root is not self: return lambda *a, **k: DG(self.root)   # child generators: no session_state (real Streamlit behaviour)
        return self.root._state
    def _store(self, key, default):
        s = self.root._state
        if key is not None:
            if key in s: return s[key]
        return default
    def _log(self, name, label): self.root.calls.append((name, label))
    def _reg(self, kind, label, key):
        """Real Streamlit raises DuplicateWidgetID when the same widget (same key, or same label/params without a key) is created twice in one run."""
        ident = ('key', key) if key is not None else (kind, str(label))
        if ident in self.root.widget_ids and not getattr(self.root, 'allow_dups', False):
            raise AssertionError(f'DuplicateWidgetID: {kind} {label!r} key={key!r}')
        self.root.widget_ids.add(ident)
    # ---- containers
    def columns(self, spec, **kw): n = len(spec) if isinstance(spec, (list, tuple)) else int(spec); return [DG(self.root) for _ in range(n)]
    def tabs(self, names): return [DG(self.root) for _ in names]
    def expander(self, *a, **kw): return DG(self.root)
    popover = container = form = status = expander
    def spinner(self, *a, **kw): return DG(self.root)
    def empty(self): return DG(self.root)
    def __enter__(self): return self
    def __exit__(self, *a): return False
    @property
    def sidebar(self): return DG(self.root)
    # ---- widgets
    def button(self, label, key=None, **kw):
        self._reg('button', label, key); self._log('button', label); hit = key if key in self.root.pressed else (label if label in self.root.pressed else None)
        if hit is None: return False
        self.root.pressed.discard(hit); return True   # a real click is consumed by the run that sees it
    def download_button(self, label, *a, key=None, **kw): self._reg('download_button', label, key); self._log('download_button', label); return False
    def checkbox(self, label, value=False, key=None, **kw): self._reg('checkbox', label, key); return bool(self._store(key, value))
    toggle = checkbox
    def selectbox(self, label, options=(), index=0, key=None, format_func=None, **kw):
        self._reg('selectbox', label, key); options = list(options); d = options[index] if options else None; v = self._store(key, d)
        return v if (not options or v in options) else d
    radio = selectbox
    def multiselect(self, label, options=(), default=None, key=None, **kw): self._reg('multiselect', label, key); return self._store(key, list(default or []))
    def number_input(self, label, min_value=None, max_value=None, value=None, step=None, key=None, **kw):
        self._reg('number_input', label, key); v = value if value is not None else (min_value if min_value is not None else 0.0); return self._store(key, v)
    def select_slider(self, label, options=(), value=None, key=None, **kw):
        self._reg('select_slider', label, key); options = list(options); v = self._store(key, value if value is not None else (options[0] if options else None)); return v if v in options else (options[0] if options else None)
    def slider(self, label, min_value=0.0, max_value=1.0, value=None, step=None, key=None, **kw): self._reg('slider', label, key); return self._store(key, value if value is not None else min_value)
    def text_input(self, label, value='', key=None, **kw): self._reg('text_input', label, key); return self._store(key, value)
    text_area = text_input
    def date_input(self, label, value=None, key=None, **kw): self._reg('date_input', label, key); return self._store(key, value or datetime.date(2026, 1, 1))
    def file_uploader(self, *a, **kw): return None
    def data_editor(self, df, **kw): return df
    def dataframe(self, *a, **kw): self._log('dataframe', ''); 
    def progress(self, *a, **kw): return DG(self.root)
    def rerun(self): raise Rerun()
    def stop(self): raise Rerun()
    def set_page_config(self, *a, **kw): pass
    def cache_data(self, *a, **kw):
        if a and callable(a[0]): return a[0]
        return lambda f: f
    cache_resource = cache_data
    def __getattr__(self, name):
        if name.startswith('__'): raise AttributeError(name)
        def f(*a, **kw): self._log(name, a[0] if a and isinstance(a[0], str) else ''); return DG(self.root)
        return f
    def __iter__(self): return iter([])


def run_app(path, state=None, pressed=(), max_reruns=6, plotly=True):
    root = DG(); root.root = root; root._state = _State(state or {}); root.calls = []; root.pressed = set(pressed); root.widget_ids = set()
    mod = types.ModuleType('streamlit'); st_proxy = root
    sys.modules['streamlit'] = st_proxy
    comp = types.ModuleType('streamlit.components.v1'); comp.declare_component = lambda *a, **k: (lambda **kw: None); comp.html = lambda *a, **k: None
    pkg = types.ModuleType('streamlit.components'); pkg.v1 = comp
    sys.modules['streamlit.components'] = pkg; sys.modules['streamlit.components.v1'] = comp
    root.__dict__['components'] = pkg
    root.__dict__['column_config'] = MagicMock()
    if plotly:
        from tests.support.fake_streamlit import install_fake_plotly; install_fake_plotly()
    for _ in range(max_reruns):
        root.widget_ids = set()
        try:
            for k in [m for m in sys.modules if m == 'ui.charts']: del sys.modules[k]
            runpy.run_path(path, run_name='__main__'); return root
        except Rerun: pass
    return root
