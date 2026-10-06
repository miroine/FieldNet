import math
import pytest
from network.expressions import compile_expression, expression_names, ExpressionEvalError

NAMES = ['oil', 'water', 'gor', 'A', 'B']
V = {'oil': 100.0, 'water': 20.0, 'gor': 110.0, 'A': 2.0, 'B': 0.5}


def ev(text, **kw):
    v = dict(V); v.update(kw)
    return compile_expression(text, NAMES)(v)


@pytest.mark.parametrize('text,expected', [
    ('oil + water', 120.0), ('oil - water*2', 60.0), ('oil/water', 5.0), ('oil**0.5', 10.0),
    ('-oil + 1', -99.0), ('oil % 30', 10.0), ('(oil+water)*B', 60.0), ('2**3**2', 512.0),
    ('min(oil, water)', 20.0), ('max(oil, water, 500)', 500.0), ('abs(-3)', 3.0), ('sqrt(oil)', 10.0),
    ('log10(oil)', 2.0), ('exp(0)', 1.0), ('pow(2, 10)', 1024.0), ('clip(oil, 0, 50)', 50.0),
    ('where(oil > 50, 1, 2)', 1.0), ('oil if oil > water else water', 100.0),
    ('1 if (oil > 10 and water < 5) else 0', 0.0), ('1 if not (oil < 10) else 0', 1.0),
    ('1 if (oil > 10 or water < 5) else 0', 1.0), ('oil > water', 1.0), ('0 < water < 10', 0.0),
    ('oil**A/(B+(water/oil)**2)', 100.0 ** 2 / (0.5 + 0.04)),
])
def test_valid_expressions(text, expected):
    assert ev(text) == pytest.approx(expected)


def test_expression_names_in_order_excluding_functions():
    assert expression_names('max(oil, 0)*price + oil*gor - water') == ['oil', 'price', 'gor', 'water']
    assert expression_names('3.5') == []


@pytest.mark.parametrize('text', [
    "__import__('os').system('ls')", "oil.real", "oil.__class__", "oil[0]", "(1,2)[0]", "lambda x: x",
    "[x for x in (1,2)]", "{x: 1 for x in (1,)}", "(x for x in (1,))", "'abc'", "f'{oil}'", "oil if oil else __import__",
    "open('/etc/passwd')", "eval('1')", "exec('1')", "oil // 2", "oil @ 2", "oil << 2", "~oil", "oil in (1,2)",
    "oil is None", "None", "min(oil, key=abs)", "min(*[1,2])", "(y := 3)", "max.__call__(1)", "a.b.c", "1j",
    "sqrt()", "clip(1,2)", "oil()", "min", "sorted([1])", "[1,2]", "{1,2}", "{'a':1}", "oil; water", "import os",
])
def test_injection_and_forbidden_constructs_rejected(text):
    with pytest.raises(ValueError):
        compile_expression(text, NAMES)


def test_unknown_name_message_has_position_and_hint():
    with pytest.raises(ValueError) as e:
        compile_expression('oil + wter', NAMES + ['water'])
    msg = str(e.value)
    assert 'wter' in msg and 'position 7' in msg and 'water' in msg


def test_syntax_error_readable():
    with pytest.raises(ValueError) as e:
        compile_expression('oil +* 2', NAMES)
    assert 'syntax error' in str(e.value)


@pytest.mark.parametrize('text', ['', '   ', 'x' * 2000, '(' * 300 + '1' + ')' * 300, '+'.join(['1'] * 300)])
def test_empty_or_oversized_rejected(text):
    with pytest.raises(ValueError):
        compile_expression(text, NAMES)


def test_non_string_rejected():
    with pytest.raises(ValueError):
        compile_expression(5, NAMES)


@pytest.mark.parametrize('text', ['oil/0', 'oil/(water-20)', 'oil % 0', '0**-1'])
def test_division_by_zero_is_an_error(text):
    with pytest.raises(ExpressionEvalError) as e:
        ev(text)
    assert e.value.kind == 'zero_division'


@pytest.mark.parametrize('text', ['10**1001', '10**400', '(-8)**0.5', 'pow(10, 5000)', 'exp(1000)', 'sqrt(-1)', 'log(0)',
                                  'log10(-5)', 'clip(1, 5, 0)', '1e200*1e200', 'oil**oil**oil'])
def test_numeric_hazards_are_errors(text):
    with pytest.raises(ValueError):
        ev(text)


def test_missing_variable_value_is_error():
    f = compile_expression('oil + water', NAMES)
    with pytest.raises(ValueError):
        f({'oil': 1.0})


def test_nonfinite_variable_is_error():
    f = compile_expression('oil', NAMES)
    with pytest.raises(ValueError):
        f({'oil': float('nan')})


def test_lazy_conditional_avoids_zero_division():
    assert ev('oil/water if water > 0 else 0', water=0.0) == 0.0


def test_function_name_cannot_be_used_as_value_and_variables_cannot_call():
    with pytest.raises(ValueError):
        compile_expression('max + 1', NAMES)
    with pytest.raises(ValueError):
        compile_expression('oil(2)', NAMES)
