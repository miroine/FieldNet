"""Model-synchronised Streamlit widgets and data-editor cleaning helpers.

Streamlit keeps a keyed widget's own value across reruns and ignores ``value=`` once the
widget exists. FieldNet writes widget values straight back into the case model on every
run, so a stale widget silently overwrote any change made elsewhere (canvas, bulk table,
JSON/CSV import, calibration). These helpers re-seed the widget whenever the model value
changed outside the widget, and otherwise leave the user's edit alone.
"""
from __future__ import annotations
import math


def clean_num(v, default=None):
    """None/''/NaN/unparseable -> default. Data editors return NaN for blank cells, and
    ``NaN or default`` is NaN because NaN is truthy."""
    if v is None: return default
    if isinstance(v, str):
        if not v.strip(): return default
        try: v=float(v)
        except ValueError: return default
    try: f=float(v)
    except (TypeError, ValueError): return default
    return f if math.isfinite(f) else default


def clean_text(v, default=''):
    if v is None: return default
    if isinstance(v, float) and not math.isfinite(v): return default
    s=str(v).strip()
    return default if s.lower() in ('', 'nan', 'none', 'nat') else s


def to_builtin(x):
    """Recursively convert numpy/pandas scalars so the case stays JSON-serialisable."""
    if isinstance(x, dict): return {k: to_builtin(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)): return [to_builtin(v) for v in x]
    if hasattr(x, 'item') and not isinstance(x, (str, bytes)):
        try: return x.item()
        except Exception: return x
    return x


def _state(st=None):
    """The real session state. ``st`` arguments of the synced_* helpers may be a column/container (widget host), which has no session_state."""
    ss = getattr(st, 'session_state', None)
    if ss is not None and not callable(ss): return ss
    import streamlit
    return streamlit.session_state


def _seed(st, key, model_value):
    mk='_model__'+key
    if key not in _state(st) or _state(st).get(mk)!=model_value:
        _state(st)[key]=model_value
    return mk


def synced_number(st, label, model_value, key, min_value=None, max_value=None, step=None, fmt=None, container=None):
    v=float(model_value)
    if min_value is not None: v=max(v,float(min_value))
    if max_value is not None: v=min(v,float(max_value))
    mk=_seed(st,key,v)
    kw={'key':key}
    if min_value is not None: kw['min_value']=float(min_value)
    if max_value is not None: kw['max_value']=float(max_value)
    if step is not None: kw['step']=float(step)
    if fmt is not None: kw['format']=fmt
    out=float((container or st).number_input(label,**kw))
    _state(st)[mk]=out
    return out


def synced_slider(st, label, min_value, max_value, model_value, key):
    v=min(max(float(model_value),float(min_value)),float(max_value))
    mk=_seed(st,key,v)
    out=float(st.slider(label,float(min_value),float(max_value),key=key))
    _state(st)[mk]=out
    return out


def synced_select(st, label, options, model_value, key, format_func=str):
    options=list(options); v=model_value if model_value in options else options[0]
    mk=_seed(st,key,v)
    out=st.selectbox(label,options,key=key,format_func=format_func)
    _state(st)[mk]=out
    return out


def synced_text(st, label, model_value, key):
    v=str(model_value); mk=_seed(st,key,v)
    out=st.text_input(label,key=key)
    _state(st)[mk]=out
    return out


def synced_checkbox(st, label, model_value, key):
    v=bool(model_value); mk=_seed(st,key,v)
    out=bool(st.checkbox(label,key=key))
    _state(st)[mk]=out
    return out


def synced_multiselect(st, label, options, model_value, key, format_func=str):
    options=list(options); v=[x for x in (model_value or []) if x in options]
    mk=_seed(st,key,v)
    out=list(st.multiselect(label,options,key=key,format_func=format_func))
    _state(st)[mk]=out
    return out
