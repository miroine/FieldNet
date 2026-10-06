"""Safe arithmetic expression evaluator for user-defined objectives and guide-rate formulas.

No ``eval``/``exec``: the text is parsed with :mod:`ast`, every node is checked against a strict
whitelist and compiled into a tree of closures.

Allowed syntax
    numbers (int/float/True/False), names from the ``allowed_names`` given by the caller,
    ``+ - * / ** %``, unary ``-``/``+``/``not``, comparisons (``< <= > >= == !=``, chained),
    ``and``/``or``, conditional expressions (``a if cond else b``) and calls to
    ``min max abs sqrt log log10 exp pow clip(x,lo,hi) where(c,a,b)``.
Everything else (attribute access, subscripts, lambdas, comprehensions, strings, keyword
arguments, f-strings, walrus, ...) is rejected with a ``ValueError`` naming the position.

Numerical policy (documented, deliberately strict -- no silent epsilons)
    * division / modulo by zero      -> :class:`ExpressionEvalError` (``kind='zero_division'``)
    * pow: exponent magnitude > 1000, result magnitude > 1e300, complex results
      (negative base with fractional exponent), ``0**negative``  -> :class:`ExpressionEvalError`
    * sqrt of a negative, log/log10 of a non-positive number, exp overflow -> error
    * NaN / inf anywhere in the final value -> error
Booleans are returned as 1.0 / 0.0. ``compile_expression`` returns a callable
``f(vars: dict) -> float``. Syntax / unknown-name errors are raised at compile time as
``ValueError``; numeric errors are raised at evaluation time as :class:`ExpressionEvalError`
(a ``ValueError`` subclass).
"""
from __future__ import annotations
import ast
import difflib
import math

MAX_TEXT_LENGTH = 1000
MAX_NODES = 200
MAX_EXPONENT = 1000.0
MAX_ABS_VALUE = 1e300

FUNCTION_NAMES = ('min', 'max', 'abs', 'sqrt', 'log', 'log10', 'exp', 'pow', 'clip', 'where')


class ExpressionEvalError(ValueError):
    """Raised when a compiled expression cannot be evaluated for the given variable values."""
    def __init__(self, message, kind='numeric'):
        super().__init__(message)
        self.kind = kind


def _err(text, node, msg):
    col = getattr(node, 'col_offset', None)
    where = f" at position {col + 1}" if col is not None else ''
    return ValueError(f"{msg}{where} in expression {text!r}")


def _check(x, what='result'):
    if isinstance(x, complex):
        raise ExpressionEvalError(f"{what} is complex (negative base with a fractional exponent?)")
    if isinstance(x, bool):
        return 1.0 if x else 0.0
    x = float(x)
    if math.isnan(x) or math.isinf(x):
        raise ExpressionEvalError(f"{what} is not finite ({x})")
    return x


def _pow(a, b):
    a = float(a); b = float(b)
    if abs(b) > MAX_EXPONENT:
        raise ExpressionEvalError(f"exponent {b:g} exceeds the allowed magnitude {MAX_EXPONENT:g}")
    if a == 0.0 and b < 0.0:
        raise ExpressionEvalError("0 raised to a negative power", kind='zero_division')
    try:
        r = math.pow(a, b)
    except OverflowError:
        raise ExpressionEvalError(f"pow({a:g}, {b:g}) overflows")
    except ValueError:
        raise ExpressionEvalError(f"pow({a:g}, {b:g}) is not a real number")
    if abs(r) > MAX_ABS_VALUE:
        raise ExpressionEvalError(f"pow({a:g}, {b:g}) exceeds the allowed magnitude")
    return r


def _sqrt(x):
    if x < 0: raise ExpressionEvalError(f"sqrt of negative number {x:g}")
    return math.sqrt(x)


def _log(x):
    if x <= 0: raise ExpressionEvalError(f"log of non-positive number {x:g}")
    return math.log(x)


def _log10(x):
    if x <= 0: raise ExpressionEvalError(f"log10 of non-positive number {x:g}")
    return math.log10(x)


def _exp(x):
    try: return math.exp(x)
    except OverflowError: raise ExpressionEvalError(f"exp({x:g}) overflows")


def _clip(x, lo, hi):
    if lo > hi: raise ExpressionEvalError(f"clip lower bound {lo:g} exceeds upper bound {hi:g}")
    return min(max(x, lo), hi)


def _where(c, a, b):
    return a if c else b


# name -> (callable, min_args, max_args)
_FUNCS = {
    'min': (min, 1, 16), 'max': (max, 1, 16), 'abs': (abs, 1, 1), 'sqrt': (_sqrt, 1, 1),
    'log': (_log, 1, 1), 'log10': (_log10, 1, 1), 'exp': (_exp, 1, 1), 'pow': (_pow, 2, 2),
    'clip': (_clip, 3, 3), 'where': (_where, 3, 3),
}

_BINOPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b}
_CMPS = {ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b,
         ast.GtE: lambda a, b: a >= b, ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b}


def _parse(text):
    if not isinstance(text, str):
        raise ValueError(f"expression must be a string, got {type(text).__name__}")
    if not text.strip():
        raise ValueError("expression is empty")
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f"expression is longer than {MAX_TEXT_LENGTH} characters")
    try:
        tree = ast.parse(text.strip(), mode='eval')
    except SyntaxError as exc:
        pos = f" at position {exc.offset}" if exc.offset else ''
        raise ValueError(f"syntax error{pos} in expression {text!r}: {exc.msg}")
    except (RecursionError, MemoryError, ValueError) as exc:
        raise ValueError(f"expression {text!r} cannot be parsed ({type(exc).__name__})")
    n = sum(1 for _ in ast.walk(tree))
    if n > MAX_NODES:
        raise ValueError(f"expression is too complex ({n} nodes, limit {MAX_NODES})")
    return tree


def compile_expression(text, allowed_names):
    """Compile ``text`` into ``f(vars) -> float``; ``allowed_names`` is any iterable of names.

    Raises ``ValueError`` (with position / offending name) for syntax errors, unknown names and
    forbidden constructs. The returned callable raises :class:`ExpressionEvalError` on numeric
    problems and ``ValueError`` if a variable is missing from ``vars``.
    """
    allowed = set(allowed_names or ())
    src = text.strip() if isinstance(text, str) else text
    tree = _parse(text)

    def build(node):
        t = type(node)
        if t is ast.Expression: return build(node.body)
        if t is ast.Constant:
            v = node.value
            if isinstance(v, bool): return lambda env, v=float(v): v
            if isinstance(v, (int, float)):
                if not math.isfinite(float(v)): raise _err(src, node, "non-finite number literal")
                return lambda env, v=float(v): v
            raise _err(src, node, f"literal of type {type(v).__name__} is not allowed")
        if t is ast.Name:
            nm = node.id
            if nm in allowed:
                def getv(env, nm=nm):
                    try: return _check(env[nm], f"variable {nm!r}")
                    except KeyError: raise ValueError(f"variable {nm!r} has no value")
                return getv
            if nm in _FUNCS:
                raise _err(src, node, f"function {nm!r} must be called, e.g. {nm}(...)")
            hint = difflib.get_close_matches(nm, sorted(allowed), n=3)
            raise _err(src, node, f"unknown name {nm!r}" + (f" (did you mean {', '.join(hint)}?)" if hint else
                                                               f"; available names: {', '.join(sorted(allowed)) or 'none'}"))
        if t is ast.BinOp:
            l, r = build(node.left), build(node.right); op = type(node.op)
            if op in _BINOPS:
                f = _BINOPS[op]; return lambda env: _check(f(l(env), r(env)))
            if op is ast.Div:
                def div(env):
                    a, b = l(env), r(env)
                    if b == 0.0: raise ExpressionEvalError("division by zero", kind='zero_division')
                    return _check(a / b)
                return div
            if op is ast.Mod:
                def mod(env):
                    a, b = l(env), r(env)
                    if b == 0.0: raise ExpressionEvalError("modulo by zero", kind='zero_division')
                    return _check(math.fmod(a, b))
                return mod
            if op is ast.Pow: return lambda env: _check(_pow(l(env), r(env)))
            raise _err(src, node, f"operator {op.__name__} is not allowed")
        if t is ast.UnaryOp:
            o = build(node.operand); op = type(node.op)
            if op is ast.USub: return lambda env: -o(env)
            if op is ast.UAdd: return lambda env: o(env)
            if op is ast.Not: return lambda env: 0.0 if o(env) else 1.0
            raise _err(src, node, f"unary operator {op.__name__} is not allowed")
        if t is ast.BoolOp:
            vals = [build(v) for v in node.values]
            if isinstance(node.op, ast.And):
                def _and(env):
                    r = 1.0
                    for v in vals:
                        r = v(env)
                        if not r: return 0.0
                    return r
                return _and
            def _or(env):
                for v in vals:
                    r = v(env)
                    if r: return r
                return 0.0
            return _or
        if t is ast.Compare:
            left = build(node.left); comps = [build(c) for c in node.comparators]; ops = []
            for o in node.ops:
                if type(o) not in _CMPS: raise _err(src, node, f"comparison {type(o).__name__} is not allowed")
                ops.append(_CMPS[type(o)])
            def cmp(env):
                a = left(env)
                for f, c in zip(ops, comps):
                    b = c(env)
                    if not f(a, b): return 0.0
                    a = b
                return 1.0
            return cmp
        if t is ast.IfExp:
            c, a, b = build(node.test), build(node.body), build(node.orelse)
            return lambda env: a(env) if c(env) else b(env)
        if t is ast.Call:
            if not isinstance(node.func, ast.Name):
                raise _err(src, node, "only direct calls to whitelisted functions are allowed")
            fn = node.func.id
            if fn not in _FUNCS:
                raise _err(src, node, f"function {fn!r} is not allowed (allowed: {', '.join(FUNCTION_NAMES)})")
            if node.keywords: raise _err(src, node, "keyword arguments are not allowed")
            if any(isinstance(a, ast.Starred) for a in node.args): raise _err(src, node, "star-arguments are not allowed")
            f, lo, hi = _FUNCS[fn]
            if not lo <= len(node.args) <= hi:
                raise _err(src, node, f"{fn}() takes {lo}{'' if lo == hi else f'-{hi}'} argument(s), got {len(node.args)}")
            args = [build(a) for a in node.args]
            if fn == 'pow': return lambda env: _check(_pow(args[0](env), args[1](env)))
            return lambda env: _check(f(*[a(env) for a in args]), f"{fn}()")
        raise _err(src, node, f"{t.__name__} is not allowed")

    fn = build(tree)

    def evaluate(variables):
        try:
            return _check(fn(variables), 'expression value')
        except RecursionError:
            raise ExpressionEvalError("expression nesting is too deep")
    evaluate.text = src
    return evaluate


def expression_names(text):
    """Variable names used by the expression, in order of first appearance (function names excluded)."""
    tree = _parse(text)
    out = []
    callee = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and id(n) not in callee and n.id not in out:
            out.append(n.id)
    # ast.walk is breadth-first; re-sort by source position for a stable, readable order.
    pos = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and id(n) not in callee:
            pos.setdefault(n.id, (n.lineno, n.col_offset))
            pos[n.id] = min(pos[n.id], (n.lineno, n.col_offset))
    return sorted(out, key=lambda k: pos[k])
