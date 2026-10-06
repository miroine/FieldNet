"""Internal-consistency / limit benchmarks for the six multiphase correlations.

IMPORTANT: none of these tests compares against PUBLISHED measured or worked-example data. They check physical
limits (single-phase Darcy-Weisbach with Colebrook, Hagen-Poiseuille, hydrostatic), monotonicity, sign
conventions and the mutual-spread table. Passing them does not validate the correlations. See
docs_correlation_validation.md.
"""
import math
import pytest
from physics.correlations import list_correlations
from physics.pvt import simple_black_oil
from physics.correlation_benchmark import (benchmark_table, REFERENCE_CASES, CASE_DEFAULTS, _fn, _call, darcy_weisbach_dp_bar,
                                           colebrook_friction_factor)

NAMES = list_correlations()
G = 9.80665


def _state(case):
    c = {**CASE_DEFAULTS, **case}
    return c, simple_black_oil(c['pressure_bar'], c['temperature_c'], c['api'], c['gas_sg'], 1.03)


def _ref_single_phase(case, fluid='oil'):
    c, st = _state(case)
    rho, mu, B = (st.oil_density_kgm3, st.oil_viscosity_pas, st.oil_fvf) if fluid == 'oil' else (st.water_density_kgm3, st.water_viscosity_pas, 1.0)
    return darcy_weisbach_dp_bar(c['q_liq_m3d'] * B / 86400.0, c['length_m'], c['diameter_m'], c['roughness_m'], rho, mu, c['dz_m'])


def test_colebrook_helper_against_known_values():
    # Hand check of the implicit equation: residual of the Colebrook-White equation at the returned f
    for re, rr in ((1e4, 1e-3), (1e5, 1e-4), (1e6, 0.0), (5e6, 1e-5)):
        f = colebrook_friction_factor(re, rr)
        lhs = 1 / math.sqrt(f); rhs = -2 * math.log10(rr / 3.7 + 2.51 / (re * math.sqrt(f)))
        assert lhs == pytest.approx(rhs, rel=1e-10)
    assert colebrook_friction_factor(1000, 1e-4) == pytest.approx(0.064)


@pytest.mark.parametrize('name', NAMES)
def test_hydrostatic_limit_zero_flow(name):
    case = {'q_liq_m3d': 0.0, 'length_m': 100.0, 'dz_m': 100.0}
    dp, _ = _call(_fn(name), case)
    c, st = _state(case)
    assert dp == pytest.approx(st.oil_density_kgm3 * G * 100.0 / 1e5, rel=1e-6)


@pytest.mark.parametrize('name', NAMES)
@pytest.mark.parametrize('case,fluid,tol', [
    ({'q_liq_m3d': 2000.0}, 'oil', 0.015),                                   # turbulent, horizontal
    ({'q_liq_m3d': 2000.0, 'water_cut': 1.0}, 'water', 0.015),               # water only
    ({'q_liq_m3d': 6000.0, 'diameter_m': 0.1, 'roughness_m': 1e-4}, 'oil', 0.015),   # rough, small bore
    ({'q_liq_m3d': 2000.0, 'dz_m': 50.0}, 'oil', 0.015),                     # uphill: friction + gravity
])
def test_single_phase_limit_vs_darcy_weisbach_colebrook(name, case, fluid, tol):
    """No free gas: every correlation must reduce to Darcy-Weisbach. Tolerance covers Swamee-Jain vs Colebrook (<~1 %)."""
    dp, _ = _call(_fn(name), case)
    assert dp == pytest.approx(_ref_single_phase(case, fluid), rel=tol)


@pytest.mark.parametrize('name', NAMES)
def test_laminar_limit_hagen_poiseuille(name):
    case = {'q_liq_m3d': 20.0, 'diameter_m': 0.1, 'length_m': 1000.0}
    c, st = _state(case)
    v = c['q_liq_m3d'] * st.oil_fvf / 86400.0 / (math.pi * c['diameter_m'] ** 2 / 4)
    assert st.oil_density_kgm3 * v * c['diameter_m'] / st.oil_viscosity_pas < 2000           # really laminar
    hp = 32.0 * st.oil_viscosity_pas * v * c['length_m'] / c['diameter_m'] ** 2 / 1e5
    assert _call(_fn(name), case)[0] == pytest.approx(hp, rel=0.01)


@pytest.mark.parametrize('name', NAMES)
def test_single_phase_linear_in_length_and_antisymmetric_in_flow(name):
    f = _fn(name)
    d1 = _call(f, {'q_liq_m3d': 2000.0, 'length_m': 1000.0})[0]
    d2 = _call(f, {'q_liq_m3d': 2000.0, 'length_m': 3000.0})[0]
    assert d2 == pytest.approx(3 * d1, rel=1e-6)
    dm = _call(f, {'q_liq_m3d': -2000.0, 'length_m': 1000.0})[0]
    assert dm == pytest.approx(-d1, rel=1e-6)


@pytest.mark.parametrize('name', NAMES)
@pytest.mark.parametrize('idx', [0, 1, 4])
def test_rate_monotonicity_horizontal(name, idx):
    """Friction-dominated horizontal flow: dp must increase with rate (5 rates spanning x16)."""
    base = {**REFERENCE_CASES[idx], 'dz_m': 0.0}
    d = [_call(_fn(name), {**base, 'q_liq_m3d': q})[0] for q in (500.0, 1000.0, 2000.0, 4000.0, 8000.0)]
    assert all(b > a > 0 for a, b in zip(d, d[1:]))


@pytest.mark.parametrize('name', NAMES)
def test_vertical_dp_bounded_below_by_gas_column_and_gravity_increases_dp(name):
    base = {'q_liq_m3d': 1500.0, 'length_m': 100.0, 'diameter_m': 0.2, 'pressure_bar': 25.0, 'water_cut': 0.3, 'gor_sm3sm3': 120.0}
    up = _call(_fn(name), {**base, 'dz_m': 100.0})
    flat = _call(_fn(name), {**base, 'dz_m': 0.0})[0]
    c, st = _state(base)
    assert up[0] > flat > 0
    assert up[0] >= st.gas_density_kgm3 * G * 100.0 / 1e5                # at least a gas-filled column
    assert up[0] - flat <= st.water_density_kgm3 * G * 100.0 / 1e5 * 1.02    # gravity part cannot exceed a liquid column


def test_benchmark_table_structure_and_statistics():
    rows = benchmark_table()
    assert len(rows) == len(REFERENCE_CASES)
    for r in rows:
        vals = [r[f'{n} dp [bar]'] for n in NAMES]
        assert all(v is not None and v > 0 for v in vals), r['Case']
        assert r['n_correlations'] == len(NAMES)
        assert r['Min dp [bar]'] == min(vals) and r['Max dp [bar]'] == max(vals)
        assert r['Mean dp [bar]'] == pytest.approx(sum(vals) / len(vals))
        assert r['Spread [%]'] == pytest.approx((max(vals) - min(vals)) / r['Mean dp [bar]'] * 100)
        assert r['Dev vs Beggs-Brill [%]'] == 0.0


def test_benchmark_table_custom_subset_and_error_reporting():
    rows = benchmark_table([{'name': 'x', 'q_liq_m3d': 1000.0}, {'name': 'bad', 'q_liq_m3d': 1000.0, 'diameter_m': -1.0}], correlations=['Beggs-Brill', 'Gray'])
    assert rows[0]['n_correlations'] == 2 and rows[0]['Spread [%]'] >= 0
    assert rows[1]['n_correlations'] == 0 and 'Beggs-Brill error' in rows[1]


def test_mutual_spread_single_phase_zero_and_two_phase_bounded():
    rows = {r['Case']: r for r in benchmark_table()}
    assert rows['Single-phase oil (no gas)']['Spread [%]'] < 1.0
    for r in rows.values():         # regression guard on documented behaviour (max ~165 % on the wet-gas case); not a validity limit
        assert r['Spread [%]'] < 250.0
        mean = r['Mean dp [bar]']
        assert all(0.2 * mean < r[f'{n} dp [bar]'] < 3.0 * mean for n in NAMES), r['Case']


def test_liquid_dominated_vertical_columns_agree_within_envelope():
    """Gravity-dominated, low-GOR vertical flow: correlations should agree much better than in friction-dominated cases."""
    case = {'q_liq_m3d': 800.0, 'length_m': 2000.0, 'dz_m': 2000.0, 'diameter_m': 0.1, 'pressure_bar': 150.0, 'temperature_c': 90.0, 'gor_sm3sm3': 20.0, 'water_cut': 0.1}
    rows = benchmark_table([case])
    assert rows[0]['Spread [%]'] < 30.0
