"""Compute settings shared by the Network tab, the forecast and the development studies.

compute = {'honour': bool,                     # honour constraints (False = unconstrained solve, violations only reported)
           'workers': int,                     # parallel worker processes for independent systems / MC / scenarios
           'optimizer': {'enabled': bool, 'objective': {...}, 'guide': {...}|None, 'gas_lift': bool}}
"""
from __future__ import annotations
import copy

DEFAULT_COMPUTE = {'honour': True, 'workers': 1, 'optimizer': {'enabled': False, 'objective': {'preset': 'max_oil'}, 'guide': None, 'gas_lift': False}}


def normalize_compute(c=None):
    out = copy.deepcopy(DEFAULT_COMPUTE)
    if isinstance(c, dict):
        out['honour'] = bool(c.get('honour', out['honour'])); out['workers'] = max(1, int(c.get('workers') or 1))
        o = c.get('optimizer') or {}
        out['optimizer'].update({k: o[k] for k in ('enabled', 'objective', 'guide', 'gas_lift') if k in o})
        out['optimizer']['enabled'] = bool(out['optimizer']['enabled'])
    if out['optimizer']['enabled']:
        from optimization.objectives import validate_objective, normalize_guide
        out['optimizer']['objective'] = validate_objective(out['optimizer']['objective'])
        out['optimizer']['guide'] = normalize_guide(out['optimizer']['guide']) if out['optimizer']['guide'] else None
        out['honour'] = True      # the optimiser works on the constraints, so they are always honoured
    return out


def _quality_solver(attempts=2):
    from solver.v21 import solve_v21
    return lambda ns, es, g: solve_v21(ns, es, warm_start=g, attempts=attempts, enforce_constraints=False)


def make_step_solver(compute):
    """None (plain solve) or ``f(nodes, edges, guess)`` running the field optimiser (for forecast / development steps)."""
    c = normalize_compute(compute)
    if not c['optimizer']['enabled']: return None
    from optimization.field_optimizer import make_step_solver as _mk
    o = c['optimizer']
    return _mk(o['objective'], o['guide'], optimize_gas_lift=bool(o.get('gas_lift')), solve_fn=_quality_solver())


def make_network_solver(compute):
    """Solver for the Network tab (single steady-state solve): ``f(nodes, edges, warm_start=None, attempts=3, enforce_constraints=None)``."""
    from solver.v21 import solve_v21
    c = normalize_compute(compute); step = make_step_solver(c)
    def solve(nodes, edges, warm_start=None, attempts=3, enforce_constraints=None, **kw):
        if step is not None: return step(nodes, edges, warm_start)
        return solve_v21(nodes, edges, warm_start=warm_start, attempts=attempts, enforce_constraints=c['honour'] if enforce_constraints is None else bool(enforce_constraints), **kw)
    from network.thermal_network import with_thermal
    return with_thermal(solve)   # no-op unless an element uses thermal_model 'heat_loss' / 'ramey'
