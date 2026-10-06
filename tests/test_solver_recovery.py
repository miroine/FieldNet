"""v32.6: capacity-limited solves when wells' natural rate is far above the facility limit (IPR/VLP cross at a very high rate)."""
import copy
import pytest
from network.templates import build
from network.forecast import solve_step
from solver.v21 import solve_v21


def _scaled(key, m=1.0, f=1.0):
    n, e = build(key)
    for x in n:
        p = x['params']
        if x['kind'] == 'well':
            for k in ('gas_c_sm3d_bar2n', 'pi_m3d_bar'):
                if k in p: p[k] *= m
        if x['kind'] == 'separator':
            for k in list(p):
                if k.startswith('max_') and p[k]: p[k] *= f
    return n, e


def _viol(info): return [c for c in info.get('constraints', []) if c.get('Status') == 'VIOLATED']


@pytest.mark.parametrize('m', [5, 20, 100])
def test_gas_condensate_high_productivity_converges_quickly_and_honours_cap(m):
    import time
    n, e = _scaled('gas_condensate_tieback', m, 0.3); t = time.time()
    p, q, info, d = solve_step(n, e, None, True)
    assert info['success'] and info['max_abs_residual'] < 1e-6, info['max_abs_residual']
    assert not _viol(info) and time.time() - t < 8


def test_compressor_example_capacity_enforcement_converges_not_stalls():
    n, e = _scaled('subsea_compressor_gas', 1.0, 0.3)
    p, q, info, d = solve_step(n, e, None, True)
    assert info['max_abs_residual'] < 1e-6 and not _viol(info)
    p2, q2, info2, d2 = solve_v21(n, e, enforce_constraints=True)
    assert info2['quality_gate'] == 'PASS' and not _viol(info2)


def test_hpht_gas_high_productivity_no_division_by_zero():
    for m in (5, 100):
        n, e = _scaled('hpht_4slot_gas', m)
        p, q, info, d = solve_v21(n, e, enforce_constraints=True)
        assert info['success'] and info['quality_gate'] == 'PASS'


def test_success_flag_requires_small_residual():
    """A stalled least-squares exit (xtol/ftol) with a large residual is not a solution."""
    from solver.steady_state import solve_network
    n, e = _scaled('gas_condensate_tieback', 20)
    p, q, info, d = solve_network(n, e)
    assert (not info['success']) or info['max_abs_residual'] <= 1e-4


def test_oil_formation_volume_factor_never_zero_at_extreme_pressure():
    from physics.pvt_model import FluidModel, FluidSpec
    fm = FluidModel(FluidSpec())
    for p in (1.0, 300.0, 3000.0, 5000.0, 1e5):
        st = fm.state(p, 120.0); assert st.oil_density_kgm3 > 0


def test_template_compressors_use_the_gas_convention_of_their_wells():
    for key in ('subsea_compressor_gas', 'topside_compressor_gas', 'onshore_gas_gathering'):
        n, e = build(key)
        for c in (x for x in e if x['kind'] == 'compressor'): assert c['params']['gor_sm3sm3'] == 5e5, key
