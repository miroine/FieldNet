"""Small Python interface to post-process result tables before exporting them.

The script sees copies of the data-hub tables and writes new / replaced tables into ``out``::

    d = ds['annual_field'].copy()
    d['Oil [MSm3]'] = d['Oil [Sm3]'] / 1e6
    d['Plateau flag'] = d['Oil [MSm3]'] > 0.9 * d['Oil [MSm3]'].max()
    out['my_table'] = d

Available names: ``ds`` (dict of DataFrames, copies), ``out`` (dict you fill), ``pd``, ``np``, ``math``, ``datetime``, ``kpis`` (dict), ``print``.
This is a convenience filter for honest mistakes (imports, file access, dunder tricks are rejected before running), NOT a security boundary:
only run scripts you wrote or trust. Scripts cannot modify the model or the stored results - they receive copies.
"""
from __future__ import annotations
import ast, builtins, io, contextlib, math, datetime
import numpy as np, pandas as pd

BANNED_NAMES = {'open', 'exec', 'eval', 'compile', '__import__', 'globals', 'locals', 'vars', 'getattr', 'setattr', 'delattr', 'input', 'breakpoint', 'exit', 'quit', 'memoryview', 'help'}
SAFE_BUILTINS = ['abs', 'all', 'any', 'bool', 'dict', 'enumerate', 'filter', 'float', 'int', 'isinstance', 'len', 'list', 'map', 'max', 'min', 'range', 'reversed', 'round', 'set', 'sorted', 'str', 'sum', 'tuple', 'zip', 'print', 'ValueError', 'KeyError', 'Exception', 'True', 'False', 'None']
MAX_SOURCE = 20000

EXAMPLES = {
    'Convert annual oil to MSm3': "d = ds['annual_field'].copy()\nd['Oil [MSm3]'] = d['Oil [Sm3]'] / 1e6\nout['annual_oil_MSm3'] = d[['Year', 'Oil [MSm3]']]\n",
    'Plateau and decline summary': ("d = ds['forecast_field']\nq = d['Oil [m3/d]']\nplateau = q.max()\nyears_on_plateau = (q >= 0.95 * plateau).sum() * d['Step [days]'].mean() / 365.25\n"
                                    "out['plateau'] = pd.DataFrame([{'Plateau [m3/d]': plateau, 'Years at >=95% plateau': years_on_plateau}])\n"),
    'Apply 95% uptime to the profile': "d = ds['forecast_field'].copy()\nfor c in [c for c in d.columns if c.endswith('/d]')]:\n    d[c] = d[c] * 0.95\nout['forecast_field_uptime95'] = d\n",
}


class ScriptError(Exception): pass


def validate(source: str):
    """Raise ScriptError for anything outside the allowed subset."""
    if len(source) > MAX_SOURCE: raise ScriptError(f'script is longer than {MAX_SOURCE} characters')
    try: tree = ast.parse(source)
    except SyntaxError as e: raise ScriptError(f'syntax error line {e.lineno}: {e.msg}')
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)): raise ScriptError('imports are not allowed - pd, np, math and datetime are already available')
        if isinstance(node, ast.Attribute) and node.attr.startswith('_'): raise ScriptError(f"attribute '{node.attr}' is not allowed (leading underscore)")
        if isinstance(node, ast.Name) and (node.id in BANNED_NAMES or (node.id.startswith('__'))): raise ScriptError(f"name '{node.id}' is not allowed")
        if isinstance(node, (ast.Global, ast.Nonlocal, ast.AsyncFunctionDef, ast.Await, ast.ClassDef)): raise ScriptError(f'{type(node).__name__} is not allowed')
        if isinstance(node, ast.Attribute) and node.attr in ('read_csv', 'read_excel', 'read_pickle', 'to_pickle', 'to_csv', 'to_excel', 'read_json', 'to_json', 'eval', 'query', 'system', 'popen', 'read_sql', 'read_html', 'read_parquet', 'to_parquet'):
            raise ScriptError(f"'{node.attr}' is not allowed - the exporter writes the files")
    return tree


def run(source: str, datasets: dict, kpis: dict | None = None):
    """Execute ``source``. Returns (new_tables: dict[str, DataFrame], log: str). Raises ScriptError with a readable message."""
    tree = validate(source)
    safe = {k: getattr(builtins, k) for k in SAFE_BUILTINS if hasattr(builtins, k)}
    out = {}; buf = io.StringIO()
    env = {'__builtins__': safe, 'ds': {k: v.copy() for k, v in datasets.items()}, 'out': out, 'pd': pd, 'np': np, 'math': math, 'datetime': datetime, 'kpis': dict(kpis or {})}
    try:
        with contextlib.redirect_stdout(buf): exec(compile(tree, '<postprocess>', 'exec'), env)
    except ScriptError: raise
    except Exception as e:
        ln = getattr(e, 'lineno', None) or (e.__traceback__.tb_next.tb_lineno if e.__traceback__ and e.__traceback__.tb_next else '?')
        raise ScriptError(f'{type(e).__name__}: {e} (line {ln})')
    tables = {}
    for k, v in out.items():
        if isinstance(v, pd.Series): v = v.to_frame()
        if isinstance(v, dict): v = pd.DataFrame(v)
        if not isinstance(v, pd.DataFrame): raise ScriptError(f"out['{k}'] must be a DataFrame, dict or Series (got {type(v).__name__})")
        if v.empty: continue
        tables[str(k)] = v.reset_index(drop=True)
    if not tables: raise ScriptError("the script produced no table - assign one with out['name'] = dataframe")
    return tables, buf.getvalue()
