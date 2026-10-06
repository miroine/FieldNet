"""Production-prognosis analysis on top of the life-of-field forecast.

* ``forecast_kpis``      plateau, peak, cumulative, recovery factor, water cut ...
* ``apply_scenario``     one development scenario = explicit, auditable edits of a copy
* ``run_scenarios``      compare scenarios on identical forecast settings
* ``well_count_study``   production and RF versus number of producers, with a
                         concrete recommended well count (marginal-gain rule)
"""
from __future__ import annotations
import copy, math
from network.forecast import run_forecast

BOUNDARY = ('sink', 'separator', 'separator_stage', 'oil_export', 'gas_export', 'water_disposal')


def forecast_kpis(fc, plateau_fraction=0.9):
    rows = fc.get('field') or []
    if not rows: return {}
    oil = [float(r.get('Oil [m3/d]', 0.0)) for r in rows]; days = [float(r.get('Day', 0.0)) for r in rows]
    peak = max(oil) if oil else 0.0
    plateau_days = 0.0
    if peak > 0:
        for i in range(len(rows) - 1):
            if oil[i] >= plateau_fraction * peak: plateau_days += days[i + 1] - days[i]
    rec = fc.get('recovery') or []
    stoiip = sum((r.get('Cum oil [Sm3]', 0.0) / (r['RF oil [%]'] / 100.0)) for r in rec if r.get('Phase') == 'oil' and r.get('RF oil [%]', 0) > 0)
    cum_oil = float(rows[-1].get('Cumulative oil [Sm3]', 0.0))
    first = next((r['Date'] for r in rows if float(r.get('Oil [m3/d]', 0.0)) > 1e-6), None)
    gas = [float(r.get('Gas [Sm3/d]', 0.0)) for r in rows]; gpeak = max(gas) if gas else 0.0; gplat = 0.0
    if gpeak > 0:
        for i in range(len(rows) - 1):
            if gas[i] >= plateau_fraction * gpeak: gplat += days[i + 1] - days[i]
    giip = sum((r.get('Cum gas [Sm3]', 0.0) / (r['RF gas [%]'] / 100.0)) for r in rec if r.get('Phase') in ('gas', 'gas_condensate', 'condensate') and r.get('RF gas [%]', 0) > 0)
    cum_gas = float(rows[-1].get('Cumulative gas [Sm3]', 0.0))
    return {'peak_gas_sm3d': gpeak, 'plateau_gas_years': gplat / 365.25, 'final_gas_sm3d': gas[-1], 'rf_gas_pct': 100.0 * cum_gas / giip if giip > 0 else None,
            'first_oil': first, 'peak_oil_m3d': peak, 'plateau_years': plateau_days / 365.25,
            'cum_oil_sm3': cum_oil, 'cum_gas_sm3': float(rows[-1].get('Cumulative gas [Sm3]', 0.0)),
            'cum_water_m3': float(rows[-1].get('Cumulative water [m3]', 0.0)),
            'rf_oil_pct': 100.0 * cum_oil / stoiip if stoiip > 0 else None,
            'final_oil_m3d': oil[-1], 'final_water_cut_pct': float(rows[-1].get('Water cut [%]', 0.0)),
            'max_wells_flowing': max(int(r.get('Wells flowing', 0)) for r in rows),
            'converged_fraction': sum(bool(r.get('Converged')) for r in rows) / len(rows)}


def producers(nodes):
    return [n for n in nodes if n.get('kind') == 'well']


def apply_scenario(nodes, edges, sc):
    """Return edited copies. Keys (all optional): wells (list of ids/names or 'all'),
    in_place_mult, pi_mult, separator_pressure_bar, liquid_capacity_m3d, injection (bool)."""
    ns, es = copy.deepcopy(nodes), copy.deepcopy(edges)
    wells = sc.get('wells', 'all')
    if wells not in (None, '', 'all'):
        keep = {str(w).strip() for w in (wells if isinstance(wells, (list, tuple)) else str(wells).split(',')) if str(w).strip()}
        for n in producers(ns):
            if n['id'] not in keep and n.get('name') not in keep: n.setdefault('params', {})['available'] = False
    m = sc.get('in_place_mult')
    if m not in (None, ''):
        for n in ns:
            if n.get('kind') == 'reservoir':
                p = n.setdefault('params', {})
                for k in ('stoiip_sm3', 'giip_sm3'):
                    if k in p: p[k] = float(p[k]) * float(m)
    m = sc.get('pi_mult')
    if m not in (None, ''):
        for n in producers(ns):
            p = n.setdefault('params', {})
            for k in ('pi_m3d_bar', 'qmax_m3d', 'gas_c_sm3d_bar2n'):
                if k in p: p[k] = float(p[k]) * float(m)
    v = sc.get('separator_pressure_bar')
    if v not in (None, ''):
        for n in ns:
            if n.get('kind') in BOUNDARY and n.get('pressure_bar') is not None: n['pressure_bar'] = float(v)
    v = sc.get('liquid_capacity_m3d')
    if v not in (None, ''):
        for n in ns:
            if n.get('kind') in BOUNDARY and n.get('pressure_bar') is not None: n.setdefault('params', {})['max_liquid_rate_m3d'] = float(v)
    if sc.get('injection') is False:
        for n in ns:
            if n.get('kind') in ('water_injector', 'gas_injector', 'injector'): n.setdefault('params', {})['available'] = False
    return ns, es


def _scenario_job(args):
    nodes, edges, sc, start, years, step_days, enforce_constraints, step_solver = args
    ns, es = apply_scenario(nodes, edges, sc)
    fc = run_forecast(ns, es, start, years, step_days, sc.get('events'), None, enforce_constraints=enforce_constraints, step_solver=step_solver)
    return {'scenario': sc, 'forecast': fc, 'kpis': forecast_kpis(fc)}


def run_scenarios(nodes, edges, scenarios, start, years, step_days, enforce_constraints=True, progress=None, step_solver=None, workers=1):
    """``workers>1`` runs the scenarios in parallel processes (not with a ``step_solver`` closure, which cannot be pickled: serial then)."""
    jobs = [(nodes, edges, sc, start, years, step_days, enforce_constraints, step_solver) for sc in scenarios]
    if workers and int(workers) > 1 and len(jobs) > 1 and step_solver is None:
        from network.uncertainty import parallel_map
        res = parallel_map(_scenario_job, jobs, int(workers))
    else:
        res = []
        for i, j in enumerate(jobs):
            res.append(_scenario_job(j))
            if progress: progress(i + 1, len(jobs))
    return [{'name': sc.get('name', f'Case {i+1}'), **r} for i, (sc, r) in enumerate(zip(scenarios, res))]


def well_count_study(nodes, edges, order, start, years, step_days, enforce_constraints=True, min_gain_fraction=0.05, progress=None, step_solver=None, workers=1):
    """Forecast with the first k producers of ``order`` for k = 1..N.

    Recommendation: the largest k whose *last* well still adds at least ``min_gain_fraction``
    of the cumulative oil achieved with k-1 wells (diminishing-returns rule).
    """
    rows = []; prev = None
    results = run_scenarios(nodes, edges, [{'name': f'{k} wells', 'wells': order[:k]} for k in range(1, len(order) + 1)], start, years, step_days, enforce_constraints,
                            progress=progress, step_solver=step_solver, workers=workers)
    for k in range(1, len(order) + 1):
        r = results[k - 1]
        kp = r['kpis']; cum = kp.get('cum_oil_sm3', 0.0)
        gain = cum - prev if prev is not None else cum
        rows.append({'Wells': k, 'Added well': order[k - 1], 'Cum oil [Sm3]': cum, 'Incremental oil [Sm3]': gain,
                     'Incremental [%]': 100.0 * gain / prev if prev else None, 'RF oil [%]': kp.get('rf_oil_pct'),
                     'Peak oil [m3/d]': kp.get('peak_oil_m3d'), 'Plateau [years]': kp.get('plateau_years'), '_forecast': r['forecast']})
        prev = cum
    rec = 1
    for r in rows[1:]:
        if r['Incremental [%]'] is not None and r['Incremental [%]'] >= 100 * min_gain_fraction: rec = r['Wells']
    reason = (f"Well {rec+1} would add less than {100*min_gain_fraction:.0f}% extra oil over {rec} wells." if rec < len(rows)
              else f"Every well up to {rec} adds at least {100*min_gain_fraction:.0f}% more oil; consider testing more wells.")
    return {'rows': rows, 'recommended_wells': rec, 'reason': reason}
