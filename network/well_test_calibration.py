"""Well-test calibration of per-well IPR productivity and VLP friction (screening level).

Each well is calibrated independently against its own well tests using the single-well model in
``physics.well_model`` (IPR + tubing VLP) at the measured wellhead pressure. Two bounded multipliers are tuned by
least squares (``scipy.optimize.least_squares`` on log-multipliers, same family of method as
``optimization.calibration_v23``, whose ``CalParameter`` / ``apply_parameters`` are reused for the PI multiplier):

* ``pi_mult``       - scales the IPR productivity (PI, or qmax for Vogel, or C for gas backpressure). It lumps skin and
                      any other inflow error; ``equivalent_skin`` reports the skin that would give the same PI change.
* ``friction_mult`` - multiplies ``tubing_roughness_m`` of the well. Roughness is a *weak* lever on tubing pressure drop
                      unless the multiplier is large (hydrostatic head dominates), so the default bounds are wide and a
                      fit that hits a bound is flagged rather than trusted.

Residuals per test: predicted liquid rate at the measured WHP minus measured rate (sigma = ``rate_rel_sigma`` of
the rate, at least ``rate_abs_sigma``), plus predicted minus measured BHP when a BHP is given.

Limitations: reservoir pressure, GOR and API are taken from a baseline network solve (or the node params) and held
fixed unless a test carries ``reservoir_pressure_bar``; no depletion over the test dates is modelled. The two
multipliers are only separately identifiable with >= 3 residuals spanning different rates / WHP (or BHP data);
otherwise only ``pi_mult`` is fitted. The tuned parameters reproduce the tests; they do not prove the physics.
"""
from __future__ import annotations
import copy
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.optimize import least_squares

from optimization.calibration_v23 import CalParameter, apply_parameters
from optimization.objectives import effective_params
from physics.well_model import well_settings, solve_well_rate, well_state, _ipr_name

DEFAULT_PI_BOUNDS = (0.2, 5.0)
DEFAULT_FRICTION_BOUNDS = (0.2, 50.0)


@dataclass
class WellTest:
    well_id: str
    whp_bar: float
    liquid_rate_m3d: Optional[float] = None
    oil_rate_m3d: Optional[float] = None
    bhp_bar: Optional[float] = None
    water_cut: Optional[float] = None
    date: Optional[str] = None
    reservoir_pressure_bar: Optional[float] = None


def _as_test(t):
    if isinstance(t, WellTest): return t
    d = dict(t)
    if 'well_id' not in d and 'well' in d: d['well_id'] = d.pop('well')
    if 'whp_bar' not in d and 'whp' in d: d['whp_bar'] = d.pop('whp')
    allowed = WellTest.__dataclass_fields__.keys()
    return WellTest(**{k: v for k, v in d.items() if k in allowed})


def _liquid(t):
    if t.liquid_rate_m3d is not None: return float(t.liquid_rate_m3d)
    if t.oil_rate_m3d is not None:
        wc = min(max(float(t.water_cut or 0.0), 0.0), 0.99)
        return float(t.oil_rate_m3d) / (1.0 - wc)
    raise ValueError(f'Well test for {t.well_id} needs liquid_rate_m3d or oil_rate_m3d')


def _baseline_params(nodes, edges, solve_fn):
    """Per-well params with reservoir pressure / wc / GOR from a baseline solve (falls back to node params)."""
    details = {}
    try:
        if solve_fn is None:
            from solver.v21 import solve_v21 as solve_fn
        details = solve_fn(nodes, edges)[3] or {}
    except Exception:
        details = {}
    out = {}
    for n in nodes:
        if n.get('kind') == 'well':
            prm = dict(n.get('params') or {})
            d = details.get(n['id'])
            # effective_params copies solved wc/GOR only when the well flows; keep node values otherwise
            out[n['id']] = effective_params(d, prm) if d else prm
    return out


def _ipr_key(prm):
    m = _ipr_name(prm.get('ipr_model', 'PI'))
    return {'PI': 'pi_m3d_bar', 'Vogel': 'qmax_m3d', 'Gas': 'gas_c_sm3d_bar2n'}[m]


def _trial_params(base, pi_mult, fr_mult, test):
    p = dict(base)
    k = _ipr_key(p); p[k] = float(p.get(k, 10.0 if k == 'pi_m3d_bar' else 1500.0 if k == 'qmax_m3d' else 50.0)) * pi_mult
    p['tubing_roughness_m'] = float(p.get('tubing_roughness_m', 4.5e-5)) * fr_mult
    if test.water_cut is not None: p['water_cut'] = float(test.water_cut)
    if test.reservoir_pressure_bar is not None: p['reservoir_pressure_bar'] = float(test.reservoir_pressure_bar)
    # tests are interpreted as the well flowing naturally at the measured WHP; no rate caps
    p['available'] = True; p.pop('max_liquid_rate_m3d', None); p['rate_limit_mode'] = 'report'
    p.pop('_network_cap_m3d', None); p.pop('_potential_cap_m3d', None)
    return p


def _predict(base, pi_mult, fr_mult, test):
    s = well_settings(_trial_params(base, pi_mult, fr_mult, test))
    q, _ = solve_well_rate(float(test.whp_bar), s)
    bhp = well_state(q, float(test.whp_bar), s)['bhp_bar'] if q > 0 else None
    return q, bhp


def _sigmas(t, rel, abs_):
    return max(rel * abs(_liquid(t)), abs_)


def _residuals(base, tests, pi_mult, fr_mult, rel, abs_, bhp_sigma):
    r = []
    for t in tests:
        q, bhp = _predict(base, pi_mult, fr_mult, t)
        r.append((q - _liquid(t)) / _sigmas(t, rel, abs_))
        if t.bhp_bar is not None:
            r.append(((bhp if bhp is not None else float(base.get('reservoir_pressure_bar', 0.0))) - float(t.bhp_bar)) / bhp_sigma)
    return np.asarray(r, float)


def _at_bound(x, lo, hi):
    return bool(x <= lo * (1 + 1e-3) or x >= hi * (1 - 1e-3))


def calibrate_well_tests(nodes, edges, tests, *, solve_fn=None, pi_bounds=DEFAULT_PI_BOUNDS,
                         friction_bounds=DEFAULT_FRICTION_BOUNDS, fit_friction='auto',
                         rate_rel_sigma=0.02, rate_abs_sigma=1.0, bhp_sigma_bar=2.0, max_nfev=60):
    """Calibrate per-well PI and VLP friction multipliers to well tests.

    Parameters
    ----------
    nodes, edges : network graph (not mutated).
    tests : list of WellTest or dicts ``{well_id, whp_bar, liquid_rate_m3d | oil_rate_m3d, [bhp_bar, water_cut, date,
            reservoir_pressure_bar]}``.
    fit_friction : 'auto' (fit friction only if the well has >= 3 residuals), True or False.

    Returns dict with ``nodes`` (tuned copy), ``edges`` (copy), ``test_table`` (one row per test, before/after),
    ``well_table`` (one row per well: multipliers, flags, RMSE before/after), ``summary`` (overall goodness of fit),
    ``flags`` (human-readable warnings) and ``limitations``.
    """
    tests = [_as_test(t) for t in tests]
    if not tests: raise ValueError('At least one well test is required')
    base_params = _baseline_params(nodes, edges, solve_fn)
    wells = {n['id'] for n in nodes if n.get('kind') == 'well'}
    flags, test_rows, well_rows = [], [], []
    tuned_nodes = copy.deepcopy(nodes)
    by_well = {}
    for t in tests:
        if t.well_id not in wells:
            flags.append(f'Test for unknown well {t.well_id!r} ignored'); continue
        by_well.setdefault(t.well_id, []).append(t)

    lo_p, hi_p = pi_bounds; lo_f, hi_f = friction_bounds
    all_before, all_after, all_meas = [], [], []
    for wid, wt in by_well.items():
        base = base_params[wid]
        n_res = len(wt) + sum(1 for t in wt if t.bhp_bar is not None)
        do_fr = (n_res >= 3) if fit_friction == 'auto' else bool(fit_friction)

        def fun(x):
            pm = math.exp(x[0]); fm = math.exp(x[1]) if do_fr else 1.0
            return _residuals(base, wt, pm, fm, rate_rel_sigma, rate_abs_sigma, bhp_sigma_bar)
        x0 = np.zeros(2 if do_fr else 1)
        lo = [math.log(lo_p)] + ([math.log(lo_f)] if do_fr else []); hi = [math.log(hi_p)] + ([math.log(hi_f)] if do_fr else [])
        r0 = fun(x0)
        try:
            opt = least_squares(fun, x0, bounds=(lo, hi), max_nfev=max_nfev, diff_step=1e-3, x_scale=1.0)
            x = opt.x; ok = bool(opt.success)
        except Exception as ex:   # keep the well unchanged and say so
            x = x0; ok = False; flags.append(f'{wid}: fit failed ({ex}); multipliers left at 1.0')
        pm = math.exp(x[0]); fm = math.exp(x[1]) if do_fr else 1.0
        # apply: PI via calibration_v23.apply_parameters for PI-type wells, direct scaling otherwise
        node = next(n for n in tuned_nodes if n['id'] == wid); prm = node.setdefault('params', {})
        if _ipr_name(prm.get('ipr_model', 'PI')) == 'PI':
            par = CalParameter(f'well:{wid}:pi_mult', wid, 'well_pi_mult', lo_p, hi_p, 1.0)
            nn, _ = apply_parameters([node], [], [par], [pm]); prm.update(nn[0]['params'])
        else:
            k = _ipr_key(prm); prm[k] = float(prm.get(k, base.get(k, 1.0))) * pm
        if do_fr: prm['tubing_roughness_m'] = float(prm.get('tubing_roughness_m', 4.5e-5)) * fm
        prm['_wtc_pi_mult'] = pm; prm['_wtc_friction_mult'] = fm

        at_pi = _at_bound(pm, lo_p, hi_p); at_fr = bool(do_fr and _at_bound(fm, lo_f, hi_f))
        if at_pi: flags.append(f'{wid}: PI multiplier {pm:.3g} at bound {pi_bounds}; test data not reproducible within the allowed range')
        if at_fr: flags.append(f'{wid}: friction multiplier {fm:.3g} at bound {friction_bounds}; test data not reproducible within the allowed range')
        if not do_fr: flags.append(f'{wid}: only {n_res} residual(s); friction multiplier not fitted (not identifiable)')
        c = float(base.get('skin_reference_factor', 7.0)); s_old = float(base.get('skin', 0.0))
        eq_skin = (c + s_old) / pm - c if _ipr_name(base.get('ipr_model', 'PI')) == 'PI' else None
        r1 = fun(x)
        for t in wt:
            qb, bb = _predict(base, 1.0, 1.0, t); qa, ba = _predict(base, pm, fm, t); qm = _liquid(t)
            test_rows.append({'well_id': wid, 'date': t.date, 'whp_bar': t.whp_bar, 'measured_rate_m3d': qm, 'rate_before_m3d': qb, 'rate_after_m3d': qa,
                              'resid_before_m3d': qb - qm, 'resid_after_m3d': qa - qm, 'resid_before_pct': 100 * (qb - qm) / max(qm, 1e-9),
                              'resid_after_pct': 100 * (qa - qm) / max(qm, 1e-9), 'measured_bhp_bar': t.bhp_bar, 'bhp_before_bar': bb, 'bhp_after_bar': ba})
            all_before.append(qb - qm); all_after.append(qa - qm); all_meas.append(qm)
        rmse = lambda r: float(np.sqrt(np.mean(np.square(r)))) if len(r) else 0.0
        well_rows.append({'well_id': wid, 'n_tests': len(wt), 'pi_mult': pm, 'friction_mult': fm, 'friction_fitted': do_fr, 'equivalent_skin': eq_skin,
                          'at_bound_pi': at_pi, 'at_bound_friction': at_fr, 'weighted_rmse_before': rmse(r0), 'weighted_rmse_after': rmse(r1),
                          'rmse_rate_before_m3d': rmse([r['resid_before_m3d'] for r in test_rows if r['well_id'] == wid]),
                          'rmse_rate_after_m3d': rmse([r['resid_after_m3d'] for r in test_rows if r['well_id'] == wid]),
                          'converged': ok, 'flag': 'AT_BOUND' if (at_pi or at_fr) else ('NOT_CONVERGED' if not ok else 'OK')})
    b, a, m = np.asarray(all_before), np.asarray(all_after), np.asarray(all_meas)
    def gof(res):
        if not len(res): return {'rmse_m3d': 0.0, 'mae_pct': 0.0, 'bias_m3d': 0.0, 'r2': None}
        ss_tot = float(np.sum((m - m.mean()) ** 2))
        return {'rmse_m3d': float(np.sqrt(np.mean(res ** 2))), 'mae_pct': float(np.mean(np.abs(res) / np.maximum(m, 1e-9)) * 100),
                'bias_m3d': float(np.mean(res)), 'r2': (1.0 - float(np.sum(res ** 2)) / ss_tot) if ss_tot > 1e-12 else None}
    summary = {'n_tests': len(all_meas), 'n_wells': len(well_rows), 'before': gof(b), 'after': gof(a),
               'wells_at_bound': [w['well_id'] for w in well_rows if w['flag'] == 'AT_BOUND']}
    return {'nodes': tuned_nodes, 'edges': copy.deepcopy(edges), 'test_table': test_rows, 'well_table': well_rows, 'summary': summary, 'flags': flags,
            'limitations': ['Screening-level single-well IPR/VLP fit at the measured WHP; network back-pressure is not re-solved.',
                            'Reservoir pressure/GOR/API held at baseline values; no depletion between tests is modelled.',
                            'Roughness multiplier is a weak lever on tubing dP; PI and friction can be correlated with few tests.',
                            'A good fit does not prove the parameters are physically unique.']}


def generate_synthetic_tests(nodes, edges, well_ids=None, whps=(15.0, 25.0, 35.0, 45.0), with_bhp=True, solve_fn=None):
    """Helper for testing/demos: model-generated tests for the wells of ``nodes`` at the given WHPs (no noise)."""
    base = _baseline_params(nodes, edges, solve_fn); out = []
    for wid, prm in base.items():
        if well_ids is not None and wid not in well_ids: continue
        t0 = WellTest(wid, 0.0)
        for i, w in enumerate(whps):
            t = WellTest(wid, float(w), date=f'2026-01-{i + 1:02d}')
            q, bhp = _predict(prm, 1.0, 1.0, t)
            if q <= 0: continue
            t.liquid_rate_m3d = q; t.bhp_bar = bhp if with_bhp else None; out.append(t)
    return out
