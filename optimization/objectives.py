"""User-definable objective functions and Eclipse-style guide-rate / allocation rules.

OBJECTIVE (plain JSON-serialisable dict, stored in the case)
    {'preset': 'max_oil'|'max_liquid'|'max_gas'|'max_revenue'|'min_water'|'custom',
     'expression': str,                       # used only when preset == 'custom'
     'prices': {'oil':..,'gas':..,'water':..,'gaslift':..},   # become variables price_oil, ...
     'params': {'K': 3.0}}                    # optional extra constants usable as bare names
    The optimiser MAXIMISES the objective.  Presets expand to expressions (see PRESETS).
    Default prices (USD, documented, overridable): oil 500 /Sm3 (~80 USD/bbl), gas 0.15 /Sm3,
    water 5 /m3 (handling cost), gaslift 0.02 /Sm3 (compression cost).  'min_water' maximises
    ``oil - water*price_water`` with defaults oil 1, water 10 (10 m3 oil are given up to avoid 1 m3 water).

    Evaluation rule: an expression that uses only per-well names is evaluated once per well and the
    results are summed; an expression that uses ``total_*`` / ``water_injection`` names is evaluated
    once on the system dictionary.  Mixing both kinds in one expression is rejected (ValueError).
    Per-well division by zero on an idle well (liquid rate ~ 0) contributes 0 instead of failing.

GUIDE (allocation rule when a capacity must be shared between wells)
    {'mode': 'pro_rata'|'potential'|'priority'|'formula', 'phase': 'oil'|'liquid'|'gas',
     'formula': str, 'preset': optional key of GUIDE_FORMULA_PRESETS, 'params': {'A':1,'B':0.1,'C':2},
     'convention': 'inverse'|'allocation'  (formula mode only), 'priority': {well_id: rank}}
    Conventions (see :func:`curtail`):
      pro_rata   reduction_i  ~ rate_i                      (what solver.v21.enforce_capacity_constraints does)
      potential  new rate_i   ~ potential_i                 (guide-rate allocation, capped at current rate)
      priority   rank 1 = most important; the HIGHEST rank number is curtailed completely first,
                 unlisted wells get the lowest priority; ties are shared pro rata to rate
      formula    G_i = formula(well variables, params); high G = served preferentially.
                 convention 'inverse' (default): reduction_i ~ 1/G_i (G_i = 0 is curtailed first);
                 convention 'allocation' (Eclipse GUIDERAT style): new rate_i ~ G_i.
"""
from __future__ import annotations
import json
import math
from network.expressions import compile_expression, expression_names, ExpressionEvalError

WELL_NAMES = ('oil', 'water', 'gas', 'liquid', 'wc', 'gor', 'whp', 'bhp', 'drawdown', 'gaslift',
              'potential_liquid', 'potential_oil', 'power_kw')
TOTAL_NAMES = ('total_oil', 'total_water', 'total_gas', 'total_liquid', 'total_gaslift', 'water_injection')
DEFAULT_PRICES = {'oil': 500.0, 'gas': 0.15, 'water': 5.0, 'gaslift': 0.02}
DEFAULT_POTENTIAL_WHP_BAR = 20.0   # reference WHP for potential_* unless params['potential_whp_bar'] is set
IDLE_RATE = 1e-9

PRESETS = {
    'max_oil': 'oil',
    'max_liquid': 'liquid',
    'max_gas': 'gas',
    'max_revenue': 'oil*price_oil + gas*price_gas - water*price_water - gaslift*price_gaslift',
    'min_water': 'oil*price_oil - water*price_water',
}
PRESET_PRICES = {'min_water': {'oil': 1.0, 'water': 10.0, 'gas': 0.0, 'gaslift': 0.0}}

# Eclipse GUIDERAT-inspired:  G = Q^A / (B + (Qother/Q)^C)
GUIDE_FORMULA_PRESETS = {
    'oil_potential_gor_penalised': {'formula': 'potential_oil**A/(B + (gor/GORREF)**C)',
                                    'params': {'A': 1.0, 'B': 0.1, 'C': 1.0, 'GORREF': 100.0}},
    'oil_potential_wc_penalised': {'formula': 'potential_oil**A/(B + wc**C)',
                                   'params': {'A': 1.0, 'B': 0.1, 'C': 2.0}},
}
GUIDE_MODES = ('pro_rata', 'potential', 'priority', 'formula', 'objective')
PHASES = ('oil', 'liquid', 'gas')


# ----------------------------------------------------------------------------------------------
# per-well variables
# ----------------------------------------------------------------------------------------------
_POT_CACHE = {}


def _f(x, default=0.0):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _potential_liquid(params, whp_ref):
    """Unrestricted deliverability [m3/d liquid] at a reference WHP (no rate limits / network caps)."""
    prm = {k: v for k, v in (params or {}).items() if k not in ('_network_cap_m3d', 'max_liquid_rate_m3d')}
    key = (json.dumps(prm, sort_keys=True, default=str), round(whp_ref, 6))
    if key in _POT_CACHE: return _POT_CACHE[key]
    try:
        from physics.well_model import well_settings, solve_well_rate
        s = well_settings(prm); s['max_rate'] = math.inf
        val = float(solve_well_rate(whp_ref, s)[0])
    except Exception:
        val = None
    if len(_POT_CACHE) > 512: _POT_CACHE.clear()
    _POT_CACHE[key] = val
    return val


def _power_kw(detail, params):
    if str((params or {}).get('lift_type', '')).lower() != 'esp': return 0.0
    try:
        from physics.well_model import well_settings, esp_head_bar_simple
        s = well_settings(params)
        q = max(_f(detail.get('liquid_rate_m3d')), 0.0)
        head = esp_head_bar_simple(q, s['esp'])
        eff = min(max(_f((params or {}).get('esp_efficiency'), 0.6), 0.05), 1.0)
        return head * 1e5 * q / 86400.0 / 1000.0 / eff
    except Exception:
        return 0.0


def effective_params(detail, params):
    """Well params with the reservoir pressure / water cut / GOR actually used in the solve (taken from the
    solved detail), so tank-linked wells (whose params carry no reservoir pressure) are modelled consistently."""
    d = detail or {}; prm = dict(params or {})
    pr = _f(d.get('reservoir_pressure_bar'), 0.0)
    if pr > 0: prm['reservoir_pressure_bar'] = pr
    liq = max(_f(d.get('liquid_rate_m3d')), 0.0)
    if liq > IDLE_RATE and d.get('water_rate_m3d') is not None: prm['water_cut'] = min(max(_f(d['water_rate_m3d']) / liq, 0.0), 0.9999)
    oil = _f(d.get('oil_rate_m3d'), 0.0)
    if oil > IDLE_RATE and d.get('gas_rate_sm3d') is not None: prm['gor_sm3sm3'] = _f(d['gas_rate_sm3d']) / oil
    return prm


def well_variables(detail, params, info=None, *, potential=True):
    """Variables of one well for objective / guide expressions.

    oil, water, liquid [m3/d]; gas, gaslift [Sm3/d]; wc [fraction]; gor [Sm3/Sm3]; whp, bhp, drawdown [bar]
    (drawdown = reservoir pressure - bhp); potential_liquid / potential_oil [m3/d] = deliverability at
    ``params['potential_whp_bar']`` (default 20 bar) ignoring rate limits and network caps (falls back to the
    current rate if it cannot be computed); power_kw = ESP hydraulic power (0 for other lift types).
    ``info`` is accepted for future use. ``potential=False`` skips the (more expensive) potential solve.
    """
    d = detail or {}; prm = params or {}
    liq = max(_f(d.get('liquid_rate_m3d')), 0.0)
    oil = _f(d.get('oil_rate_m3d'), None); wat = _f(d.get('water_rate_m3d'), None); gas = _f(d.get('gas_rate_sm3d'), None)
    wc_p = min(max(_f(prm.get('water_cut'), 0.0), 0.0), 1.0); gor_p = max(_f(prm.get('gor_sm3sm3'), 0.0), 0.0)
    if oil is None: oil = liq * (1.0 - wc_p)
    if wat is None: wat = liq * wc_p
    if gas is None: gas = oil * gor_p
    wc = wat / liq if liq > IDLE_RATE else wc_p
    gor = gas / oil if oil > IDLE_RATE else gor_p
    bhp = _f(d.get('bhp_bar')); pr = _f(d.get('reservoir_pressure_bar'), _f(prm.get('reservoir_pressure_bar'), 0.0))
    out = {'oil': oil, 'water': wat, 'gas': gas, 'liquid': liq, 'wc': wc, 'gor': gor, 'whp': _f(d.get('whp_bar')),
           'bhp': bhp, 'drawdown': pr - bhp, 'gaslift': max(_f(d.get('gas_lift_sm3d')), 0.0),
           'potential_liquid': liq, 'potential_oil': oil, 'power_kw': _power_kw(d, prm)}
    if potential:
        # the solved detail carries the actually used reservoir pressure / fluids (e.g. tank-linked wells)
        peff = effective_params(d, prm)
        pot = _potential_liquid(peff, _f(prm.get('potential_whp_bar'), DEFAULT_POTENTIAL_WHP_BAR))
        if pot is not None:
            pot = max(pot, 0.0)
            out['potential_liquid'] = pot; out['potential_oil'] = pot * (1.0 - wc_p if liq <= IDLE_RATE else 1.0 - wc)
    return out


def _is_injector_water(node):
    prm = node.get('params') or {}
    return node.get('kind') == 'water_injector' or (node.get('kind') in ('injector',) and str(prm.get('injection_fluid', 'water')).lower() != 'gas')


def system_variables(wvars, info=None, nodes=None):
    """Totals over per-well variable dicts plus water injection [m3/d] from ``info['injector_rates']``."""
    tot = {'total_oil': sum(v['oil'] for v in wvars.values()), 'total_water': sum(v['water'] for v in wvars.values()),
           'total_gas': sum(v['gas'] for v in wvars.values()), 'total_liquid': sum(v['liquid'] for v in wvars.values()),
           'total_gaslift': sum(v['gaslift'] for v in wvars.values())}
    inj = (info or {}).get('injector_rates') or {}
    if nodes is not None:
        kinds = {n['id']: n for n in nodes}
        wi = sum(max(_f(r), 0.0) for k, r in inj.items() if k in kinds and _is_injector_water(kinds[k]))
    else:
        wi = sum(max(_f(r), 0.0) for r in inj.values())
    tot['water_injection'] = wi
    return tot


# ----------------------------------------------------------------------------------------------
# objective
# ----------------------------------------------------------------------------------------------
def normalize_objective(objective=None):
    """Return a complete, JSON-serialisable objective dict (raises ValueError on invalid input)."""
    if objective is None: objective = {'preset': 'max_oil'}
    if isinstance(objective, str): objective = {'preset': objective}
    if not isinstance(objective, dict): raise ValueError('objective must be a dict, a preset name or None')
    preset = str(objective.get('preset') or ('custom' if objective.get('expression') else 'max_oil'))
    if preset not in PRESETS and preset != 'custom':
        raise ValueError(f"unknown objective preset {preset!r}; choose from {', '.join(list(PRESETS) + ['custom'])}")
    prices = dict(DEFAULT_PRICES); prices.update(PRESET_PRICES.get(preset, {}))
    for k, v in (objective.get('prices') or {}).items():
        try: prices[str(k)] = float(v)
        except (TypeError, ValueError): raise ValueError(f"price {k!r} must be a number")
    params = {}
    for k, v in (objective.get('params') or {}).items():
        try: params[str(k)] = float(v)
        except (TypeError, ValueError): raise ValueError(f"objective parameter {k!r} must be a number")
    if preset == 'custom':
        expr = str(objective.get('expression') or '').strip()
        if not expr: raise ValueError("custom objective needs a non-empty 'expression'")
    else:
        expr = PRESETS[preset]
    return {'preset': preset, 'expression': expr, 'prices': prices, 'params': params}


class ObjectiveEvaluator:
    """Compiled objective. ``mode`` is 'well' (sum of per-well terms) or 'system' (once on totals)."""

    def __init__(self, objective):
        self.objective = normalize_objective(objective)
        extra = {f'price_{k}': v for k, v in self.objective['prices'].items()}
        extra.update(self.objective['params'])
        self.constants = extra
        text = self.objective['expression']
        names = expression_names(text)
        used_tot = [n for n in names if n in TOTAL_NAMES]; used_well = [n for n in names if n in WELL_NAMES]
        if used_tot and used_well:
            raise ValueError(f"objective mixes system totals ({', '.join(used_tot)}) with per-well variables "
                             f"({', '.join(used_well)}); use either per-well names or total_* names in one expression")
        self.mode = 'system' if used_tot else 'well'
        allowed = (TOTAL_NAMES if self.mode == 'system' else WELL_NAMES) + tuple(extra)
        self.fn = compile_expression(text, allowed)
        self.uses_potential = any(n in ('potential_liquid', 'potential_oil') for n in names)
        self.names = names

    def well_vars(self, details, nodes, info=None, *, potential=None):
        pot = self.uses_potential if potential is None else potential
        out = {}
        for n in nodes or []:
            if n.get('kind') == 'well' and n['id'] in details:
                out[n['id']] = well_variables(details[n['id']], n.get('params') or {}, info, potential=pot)
        return out

    def from_vars(self, wvars, info=None, nodes=None):
        if self.mode == 'system':
            env = dict(system_variables(wvars, info, nodes)); env.update(self.constants)
            return self.fn(env)
        total = 0.0
        for wid, v in wvars.items():
            env = dict(v); env.update(self.constants)
            try:
                total += self.fn(env)
            except ExpressionEvalError as exc:
                if exc.kind == 'zero_division' and v.get('liquid', 0.0) <= IDLE_RATE: continue
                raise ValueError(f"objective failed for well {wid}: {exc}")
        return total

    def __call__(self, details, info, nodes):
        return self.from_vars(self.well_vars(details, nodes, info), info, nodes)


def evaluate_objective(objective, details, info, nodes):
    """Objective value (to be maximised) for a solved network: see module docstring for the rules."""
    ev = objective if isinstance(objective, ObjectiveEvaluator) else ObjectiveEvaluator(objective)
    return ev(details, info, nodes)


def validate_objective(objective):
    """Compile-check an objective; returns the normalised dict (raises ValueError with a readable message)."""
    return ObjectiveEvaluator(objective).objective


# ----------------------------------------------------------------------------------------------
# guide rates
# ----------------------------------------------------------------------------------------------
def normalize_guide(guide=None):
    if guide is None: return {'mode': 'pro_rata', 'phase': 'liquid', 'formula': '', 'params': {}, 'priority': {}, 'convention': 'inverse'}
    if isinstance(guide, str): guide = {'mode': guide}
    if not isinstance(guide, dict): raise ValueError('guide must be a dict, a mode name or None')
    g = {'mode': str(guide.get('mode', 'pro_rata')), 'phase': str(guide.get('phase', 'liquid')), 'formula': str(guide.get('formula') or ''),
         'params': {}, 'priority': dict(guide.get('priority') or {}), 'convention': str(guide.get('convention', 'inverse'))}
    if g['mode'] not in GUIDE_MODES: raise ValueError(f"unknown guide mode {g['mode']!r}; choose from {', '.join(GUIDE_MODES)}")
    if g['phase'] not in PHASES: raise ValueError(f"unknown guide phase {g['phase']!r}; choose from {', '.join(PHASES)}")
    if g['convention'] not in ('inverse', 'allocation'): raise ValueError("guide convention must be 'inverse' or 'allocation'")
    preset = guide.get('preset')
    if preset:
        if preset not in GUIDE_FORMULA_PRESETS: raise ValueError(f"unknown guide formula preset {preset!r}")
        g['formula'] = g['formula'] or GUIDE_FORMULA_PRESETS[preset]['formula']; g['params'].update(GUIDE_FORMULA_PRESETS[preset]['params'])
        g['mode'] = 'formula' if 'mode' not in guide else g['mode']
    for k, v in (guide.get('params') or {}).items():
        try: g['params'][str(k)] = float(v)
        except (TypeError, ValueError): raise ValueError(f"guide parameter {k!r} must be a number")
    for k, v in g['priority'].items():
        try: g['priority'][k] = float(v)
        except (TypeError, ValueError): raise ValueError(f"priority rank of {k!r} must be a number")
    if g['mode'] == 'formula':
        if not g['formula'].strip(): raise ValueError("guide mode 'formula' needs a formula")
        compile_expression(g['formula'], tuple(WELL_NAMES) + tuple(g['params']))
    return g


def guide_weights(guide, details, params_by_well):
    """Per-well weights (>= 0) for the guide.

    pro_rata -> current rate of the guide phase; potential -> potential of the guide phase (at the reference WHP);
    priority -> the rank number itself (LARGER = lower priority = curtailed first; unlisted wells rank last);
    formula -> user expression on the well variables plus guide params.  For 'objective' mode the weights are
    the current phase rates (the optimiser supplies value densities itself).
    Only wells present in both ``details`` and ``params_by_well`` are returned.
    """
    g = normalize_guide(guide); ids = [w for w in params_by_well if w in details]; mode = g['mode']; ph = g['phase']
    key = {'oil': 'oil', 'liquid': 'liquid', 'gas': 'gas'}[ph]
    if mode in ('pro_rata', 'objective'):
        return {w: max(well_variables(details[w], params_by_well[w], potential=False)[key], 0.0) for w in ids}
    if mode == 'priority':
        ranks = g['priority']; worst = max([float(v) for v in ranks.values()] + [0.0]) + 1.0
        return {w: float(ranks.get(w, worst)) for w in ids}
    if mode == 'potential':
        out = {}
        for w in ids:
            v = well_variables(details[w], params_by_well[w], potential=True)
            pl = v['potential_liquid']; po = v['potential_oil']
            out[w] = max({'liquid': pl, 'oil': po, 'gas': po * v['gor']}[ph], 0.0)
        return out
    fn = compile_expression(g['formula'], tuple(WELL_NAMES) + tuple(g['params']))
    uses_pot = any(n in ('potential_liquid', 'potential_oil') for n in expression_names(g['formula']))
    out = {}
    for w in ids:
        env = well_variables(details[w], params_by_well[w], potential=uses_pot); env.update(g['params'])
        try: out[w] = max(fn(env), 0.0)
        except ExpressionEvalError as exc:
            raise ValueError(f"guide formula failed for well {w}: {exc}")
    return out


def _tie_groups(keys, ids, rel=1e-9):
    order = sorted(ids, key=lambda i: (-keys[i], str(i))); groups = []
    for i in order:
        if groups and abs(keys[groups[-1][0]] - keys[i]) <= rel * max(1.0, abs(keys[i])): groups[-1].append(i)
        else: groups.append([i])
    return groups


def curtail(requested_reduction, rates, weights, mode):
    """Distribute a required rate reduction over wells. Returns {well_id: reduction >= 0}.

    ``rates``: current rates (any phase unit); ``weights``: from :func:`guide_weights`.  The total reduction is
    min(requested, sum(rates)); no well is reduced by more than its rate; deterministic (ties by id).
      'pro_rata'            reduction ~ rate
      'potential' | 'allocation'  new rate ~ weight, water-filled so no well exceeds its current rate
      'formula' | 'inverse'  reduction ~ 1/weight (weight 0 -> curtailed first), capped at the well's rate
      'priority'            largest weight (= lowest priority) curtailed completely first; ties pro rata to rate
    """
    ids = sorted(rates, key=str); r = {i: max(float(rates[i]), 0.0) for i in ids}
    total = sum(r.values()); R = min(max(float(requested_reduction), 0.0), total)
    red = {i: 0.0 for i in ids}
    if R <= 0.0 or total <= 0.0: return red
    mode = {'pro_rata': 'pro_rata', 'potential': 'allocation', 'allocation': 'allocation', 'formula': 'inverse',
            'inverse': 'inverse', 'priority': 'priority', 'objective': 'priority'}.get(mode)
    if mode is None: raise ValueError('unknown curtailment mode')
    w = {i: max(float(weights.get(i, 0.0)), 0.0) for i in ids}
    active = [i for i in ids if r[i] > 0.0]
    if mode == 'pro_rata' or (mode == 'allocation' and sum(w[i] for i in active) <= 0.0):
        return {i: R * r[i] / total for i in ids}
    if mode == 'priority':
        left = R
        raw = {i: float(weights.get(i, 0.0)) for i in ids}   # priority keys may be negative (value densities)
        for grp in _tie_groups(raw, active):
            gt = sum(r[i] for i in grp); take = min(left, gt)
            for i in grp: red[i] = take * r[i] / gt
            left -= take
            if left <= 1e-12 * total: break
        return red
    if mode == 'inverse':
        c = {i: 1.0 / max(w[i], 1e-12) for i in active}; left = R; act = list(active)
        while act and left > 1e-12:
            lam = left / sum(c[i] for i in act); full = [i for i in act if lam * c[i] >= r[i] - red[i]]
            if not full:
                for i in act: red[i] += lam * c[i]
                break
            for i in full: left -= r[i] - red[i]; red[i] = r[i]
            act = [i for i in act if i not in full]
        return red
    # allocation: new_i = min(r_i, mu*w_i), sum new_i = total - R
    target = total - R; fixed = {}; act = list(active)
    while act:
        sw = sum(w[i] for i in act); rem = target - sum(fixed.values())
        if sw <= 0.0:
            share = rem / len(act)
            for i in act: fixed[i] = min(r[i], share)
            break
        mu = rem / sw; over = [i for i in act if r[i] <= mu * w[i]]
        if not over:
            for i in act: fixed[i] = mu * w[i]
            break
        for i in over: fixed[i] = r[i]
        act = [i for i in act if i not in over]
    for i in active: red[i] = max(r[i] - fixed.get(i, 0.0), 0.0)
    return red
