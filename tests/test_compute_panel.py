from tests.support.fake_streamlit import FakeSt
from ui.compute_panel import render_compute_settings
from tests.test_properties_ui import W


def test_defaults_roundtrip():
    st = FakeSt(); c = render_compute_settings(st); assert c['honour'] is True and c['optimizer']['enabled'] is False and c['workers'] == 1


def test_optimizer_custom_objective_and_formula_guide():
    st = FakeSt()
    W(st, 'cmp_opt', True, False); W(st, 'cmp_obj', 'custom', 'max_oil'); W(st, 'cmp_expr', 'oil*price_oil - 3*water', 'oil*price_oil - water*price_water')
    W(st, 'cmp_guide', 'formula', 'objective'); W(st, 'cmp_gform', 'potential_oil/(1+wc)', 'potential_oil')
    c = render_compute_settings(st)
    assert c['optimizer']['enabled'] and c['optimizer']['objective']['expression'] == 'oil*price_oil - 3*water' and c['optimizer']['guide']['mode'] == 'formula' and c['honour']


def test_invalid_expression_is_reported_not_raised():
    st = FakeSt(); W(st, 'cmp_opt', True, False); W(st, 'cmp_obj', 'custom', 'max_oil'); W(st, 'cmp_expr', 'oil +', 'oil*price_oil - water*price_water')
    c = render_compute_settings(st); assert c['optimizer']['enabled'] is False and any(k == 'error' for k, *_ in st.calls)
