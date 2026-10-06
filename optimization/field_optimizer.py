"""Capacity-honouring field optimiser: user objective + Eclipse-style guide rates.

Works on a single steady-state solve and (through :func:`make_step_solver`) on every timestep of a
forecast.  Controls written to the returned nodes are ONLY ``params['_network_cap_m3d']`` (per-well liquid
rate cap, the same temporary key ``solver.v21.enforce_capacity_constraints`` uses) and, when gas-lift
optimisation is on, ``params['gas_lift_injection_sm3d']``.  Inputs are never mutated; any pre-existing
``_network_cap_m3d`` on the input nodes is cleared (the optimiser owns it).

Algorithm
    1. unconstrained solve  (= ``baseline_value``; well-level limits only, facility capacities ignored)
    2. while relievable constraint rows are violated: for every violated '<=' row whose component has upstream
       wells, compute the needed reduction (in the row's phase; ratio-type rows such as velocity / power /
       erosional ratio use the fractional exceedance), share it over the upstream wells and write
       per-well caps (never raised, never lower than needed), re-solve warm-started.
       Sharing rule = the guide: ``None``/'objective' (default) curtails wells in increasing order of
       marginal objective value per unit of the binding phase (an exact LP vertex for a single linear
       capacity, hence water-heavy wells go first for an oil objective); 'pro_rata', 'potential',
       'priority', 'formula' follow :mod:`optimization.objectives`.
    3. release: caps are over-conservative by the safety margin / network coupling.  The best-value capped
       well (objective mode) or all caps together (guide modes) are given back with a bracketed secant /
       bisection on a scalar factor until a constraint becomes active (verified by solves).
    4. optional gas-lift allocation: response curves from the well model at the current WHP, concave-hull
       marginal allocation under the total, then a full verified solve + steps 2-3; kept only if it improves
       the verified objective.
    5. the returned result is a verified solve.  With the default objective-driven sharing the better of the
       optimised and the pro-rata choke-back (``constrained_baseline_value``) feasible candidates is returned,
       so the optimiser is never worse; an explicit guide (pro_rata/potential/priority/formula) is a user rule
       and is returned as is.
Pressure ('>=') rows and rows whose component has no upstream wells are reported, not relieved.
"""
from __future__ import annotations
import time
from collections import defaultdict, deque
from copy import deepcopy
import math

from optimization.objectives import (effective_params, ObjectiveEvaluator, normalize_guide, guide_weights, curtail, IDLE_RATE)

CAP_KEY = '_network_cap_m3d'
GL_KEY = 'gas_lift_injection_sm3d'
PHASE_KEY = {'liquid': 'liquid_rate_m3d', 'oil': 'oil_rate_m3d', 'water': 'water_rate_m3d', 'gas': 'gas_rate_sm3d'}


# ----------------------------------------------------------------------------------------------
# topology / constraint helpers
# ----------------------------------------------------------------------------------------------
def _upstream_index(nodes, edges):
    up = defaultdict(list)
    for e in edges: up[e.get('target')].append(e.get('source'))
    return up


def _wells_upstream(start_ids, byid, up):
    seen = set(start_ids); dq = deque(start_ids); wells = []
    while dq:
        x = dq.popleft()
        if byid.get(x, {}).get('kind') == 'well': wells.append(x)
        for y in up.get(x, ()):
            if y not in seen: seen.add(y); dq.append(y)
    return wells


def _row_wells(row, byid, ebyid, up, ebyname, nbyname):
    cid = row.get('ComponentId')
    if cid is None:
        cid = nbyname.get(row.get('Component')) or ebyname.get(row.get('Component'))
    if cid in byid: return _wells_upstream([cid], byid, up)
    if cid in ebyid: return _wells_upstream([ebyid[cid].get('source')], byid, up)
    return []


def _row_phase(row):
    name = str(row.get('Constraint', '')).lower(); unit = str(row.get('Unit', '')).lower()
    if 'oil' in name: return 'oil'
    if 'water' in name: return 'water'
    if 'gas' in name: return 'gas'
    if 'sm3' in unit: return 'gas'
    if unit.startswith('m3') or unit.startswith('sm3'): return 'liquid'
    if 'liquid' in name: return 'liquid'
    return None   # dimensionless / other units: treated as proportional to flow


def _rel_violation(row):
    return -float(row.get('Margin', 0.0)) / max(abs(float(row.get('Limit', 0.0))), 1e-9)


class _State:
    __slots__ = ('ns', 'p', 'q', 'info', 'd', 'obj', 'rows', '_delta')

    def __init__(self, ns, res, obj, rows):
        self.ns = ns; self.p, self.q, self.info, self.d = res; self.obj = obj; self.rows = rows; self._delta = None

    @property
    def result(self): return (self.p, self.q, self.info, self.d)

    @property
    def guess(self):
        return {'pressures': self.p, 'flows': self.q,
                'well_rates': {k: v.get('liquid_rate_m3d', 0.0) for k, v in (self.d or {}).items()}}


class _Optimizer:
    def __init__(self, edges, solve_fn, evaluator, guide, tol, max_outer, release_solves):
        self.edges = edges; self.solve_fn = solve_fn; self.ev = evaluator; self.guide = guide
        self.tol = max(float(tol), 1e-6); self.max_outer = max(int(max_outer), 0); self.release_solves = max(int(release_solves), 0)
        self.n_solves = 0; self.warnings = []; self.history = []; self.log = []
        self.up = _upstream_index(None, edges); self.ebyid = {e['id']: e for e in edges}
        self.ebyname = {e.get('name', e['id']): e['id'] for e in edges}

    # ---- solving -------------------------------------------------------------------------
    def solve(self, ns, guess, tag=''):
        self.n_solves += 1
        try:
            res = self.solve_fn(ns, self.edges, guess)
            p, q, info, d = res
        except Exception as exc:   # solver failures must never escape
            self.warnings.append(f'solve failed ({tag}): {exc}')
            return None
        info = info or {}
        if info.get('success') is False:
            self.warnings.append(f"solve did not converge ({tag}): {info.get('message', '')}")
            return None
        obj = self.ev(d or {}, info, ns)
        return _State(ns, (p, q, info, d or {}), obj, self.rows_of(info, ns))

    def rows_of(self, info, ns):
        byid = {n['id']: n for n in ns}; nbyname = {n.get('name', n['id']): n['id'] for n in ns}; out = []
        for r in info.get('constraints', []) or []:
            r = dict(r); wells = _row_wells(r, byid, self.ebyid, self.up, self.ebyname, nbyname)
            r['_wells'] = wells; r['_relievable'] = r.get('Relation') == '<=' and bool(wells)
            out.append(r)
        return out

    @staticmethod
    def violated(st): return [r for r in st.rows if r.get('Status') == 'VIOLATED']

    def relievable(self, st): return [r for r in self.violated(st) if r['_relievable']]

    def n_unrelievable(self, st): return sum(1 for r in self.violated(st) if not r['_relievable'])

    def max_violation(self, st):
        v = [_rel_violation(r) for r in self.violated(st)]
        return max(v) if v else 0.0

    def feasible(self, st, base_unrel=0): return not self.relievable(st) and self.n_unrelievable(st) <= base_unrel

    def slack_g(self, st):
        """Scalar >0 when a relievable row is violated, <0 (negative margin fraction) when all have slack."""
        g = [_rel_violation(r) for r in st.rows if r['_relievable']]
        return max(g) if g else -1.0

    def note(self, st, phase):
        self.history.append({'iteration': len(self.history), 'phase': phase, 'objective': st.obj, 'max_violation': self.max_violation(st),
                             'n_solves': self.n_solves})

    # ---- value densities -----------------------------------------------------------------
    def deltas(self, st):
        """Objective loss per well when its rates are cut by 10 % (gas-lift cost is not saved by choking)."""
        if st._delta is not None: return st._delta
        out = {}; scale = 0.9
        wvars = self.ev.well_vars(st.d, st.ns, st.info)
        base = self.ev.from_vars(wvars, st.info, st.ns)
        for wid, v in wvars.items():
            v2 = dict(v)
            for k in ('oil', 'water', 'gas', 'liquid'): v2[k] = v[k] * scale
            w2 = dict(wvars); w2[wid] = v2
            try: out[wid] = (base - self.ev.from_vars(w2, st.info, st.ns)) / 0.1
            except ValueError: out[wid] = 0.0
        st._delta = out
        return out

    def phase_rates(self, st, wells, phase):
        key = PHASE_KEY[phase]
        return {w: max(float(st.d.get(w, {}).get(key, 0.0)), 0.0) for w in wells}

    # ---- enforcement -----------------------------------------------------------------------
    def enforce(self, st, base_unrel=0, mode=None, tag='enforce'):
        """Curtail until relievable rows are satisfied. Returns the last good state (feasible or not)."""
        mode = mode or self.guide['mode']
        for it in range(self.max_outer):
            rows = sorted(self.relievable(st), key=_rel_violation, reverse=True)
            if not rows: break
            caps = self.compute_caps(st, rows, mode)
            if not caps: break
            ns2 = deepcopy(st.ns); byid = {n['id']: n for n in ns2}
            for w, c in caps.items(): byid[w].setdefault('params', {})[CAP_KEY] = c
            st2 = self.solve(ns2, st.guess, f'{tag} {it + 1}')
            if st2 is None: break
            st = st2; self.note(st, tag)
        return st

    def compute_caps(self, st, rows, mode):
        ns_by = {n['id']: n for n in st.ns}; new_liq = {}
        guide = self.guide
        for row in rows:
            phase = _row_phase(row); wells = row['_wells']; value = float(row['Value']); limit = float(row['Limit'])
            target = limit * (1.0 - self.tol)
            use_phase = phase or 'liquid'
            base_r = self.phase_rates(st, wells, use_phase)
            liq0 = self.phase_rates(st, wells, 'liquid')
            # rates after reductions already decided for earlier rows in this pass
            cur = {w: base_r[w] * (new_liq.get(w, liq0[w]) / liq0[w] if liq0[w] > IDLE_RATE else 1.0) for w in wells}
            if phase is None:
                f = target / max(value, 1e-12); achieved = sum(base_r[w] - cur[w] for w in wells)
                need = (1.0 - min(f, 1.0)) * sum(base_r.values()) - achieved
            else:
                need = value - target - sum(base_r[w] - cur[w] for w in wells)
            if need <= 0: continue
            rates = {w: cur[w] for w in wells if cur[w] > IDLE_RATE}
            if not rates: continue
            if mode == 'objective':
                dl = self.deltas(st)
                keys = {w: -(dl.get(w, 0.0) / max(base_r[w], IDLE_RATE)) for w in rates}   # high key = low value density = first
                red = curtail(need, rates, keys, 'priority')
            else:
                try:
                    wts = guide_weights(dict(guide, mode=mode), st.d, {w: ns_by[w].get('params') or {} for w in rates})
                except ValueError as exc:
                    self.warnings.append(f'guide failed ({exc}); pro-rata used'); wts = {w: rates[w] for w in rates}; mode = 'pro_rata'
                m = {'pro_rata': 'pro_rata', 'potential': 'allocation', 'priority': 'priority',
                     'formula': 'allocation' if guide['convention'] == 'allocation' else 'inverse'}[mode]
                red = curtail(need, rates, wts, m)
            for w, r in red.items():
                if r <= 0: continue
                new_liq[w] = new_liq.get(w, liq0[w]) * max(cur[w] - r, 0.0) / cur[w]
        caps = {}
        for w, c in new_liq.items():
            old = (ns_by[w].get('params') or {}).get(CAP_KEY)
            c = max(c, 0.0); c = 0.0 if c < 1e-9 else c; c = c if old is None else min(float(old), c)
            if old is None or c < float(old) - 1e-9: caps[w] = c
        return caps

    # ---- release -------------------------------------------------------------------------
    def _regula(self, make_state, st_lo, budget, base_unrel):
        """Largest x in [0,1] (x=0: current feasible state ``st_lo``) with a feasible state, by secant/bisection.
        ``make_state(x)`` -> _State or None.  Returns best feasible state found."""
        best = st_lo; lo, glo = 0.0, self.slack_g(st_lo); hi, ghi = 1.0, None; used = 0
        target = -0.25 * self.tol
        x = 1.0
        while used < budget:
            st = make_state(x); used += 1
            if st is None: hi, ghi = x, 1.0
            else:
                g = self.slack_g(st)
                if self.feasible(st, base_unrel) and st.obj >= best.obj - 1e-9 * max(1.0, abs(best.obj)):
                    best = st; lo, glo = x, g
                    if x >= 1.0 or -0.5 * self.tol <= g <= 0.0: break   # fully released or constraint active
                    if hi - lo < 5e-3: break
                else:
                    hi, ghi = x, max(g, 1e-6) if st is not None else 1.0
            if ghi is None: x = 1.0; continue
            if abs(hi - lo) < 5e-3: break
            frac = (target - glo) / (ghi - glo) if ghi != glo else 0.5
            x = lo + (hi - lo) * min(max(frac, 0.1), 0.9)
        return best

    def release(self, st, q0, base_unrel, mode):
        """Give back over-curtailment (see module docstring). ``q0``: unconstrained liquid rates."""
        if self.release_solves <= 0 or not self.relievable_capacity(st): return st
        ns_by = {n['id']: n for n in st.ns}
        capped = [w for w, n in ns_by.items() if (n.get('params') or {}).get(CAP_KEY) is not None]
        if not capped: return st
        start_solves = self.n_solves; budget = lambda: self.release_solves - (self.n_solves - start_solves)
        if mode == 'objective':
            dl = self.deltas(st)
            order = sorted(capped, key=lambda w: -(dl.get(w, 0.0) / max(q0.get(w, 1.0), 1e-9)))
            for w in order:
                if budget() <= 0 or dl.get(w, 0.0) <= 0: break
                cap0 = float(ns_by[w]['params'][CAP_KEY]); full = max(q0.get(w, cap0), cap0) * 1.5 + 1.0
                def make(x, w=w, cap0=cap0, full=full, st=st):
                    ns2 = deepcopy(st.ns); prm = ns2[[n['id'] for n in ns2].index(w)].setdefault('params', {})
                    if x >= 1.0: prm.pop(CAP_KEY, None)
                    else: prm[CAP_KEY] = cap0 + x * (full - cap0)
                    return self.solve(ns2, st.guess, 'release')
                new = self._regula(make, st, budget(), base_unrel)
                improved = new is not st; st = new
                if improved: self.note(st, 'release')
                if self.slack_g(st) > -0.5 * self.tol * 3 or not improved: break
        else:
            def make(x, st=st):
                ns2 = deepcopy(st.ns)
                for n in ns2:
                    prm = n.get('params') or {}
                    if prm.get(CAP_KEY) is None: continue
                    c = float(prm[CAP_KEY]); full = max(q0.get(n['id'], c), c) * 1.5 + 1.0
                    if x >= 1.0: prm.pop(CAP_KEY, None)
                    else: prm[CAP_KEY] = c + x * (full - c)
                return self.solve(ns2, st.guess, 'release')
            new = self._regula(make, st, budget(), base_unrel)
            if new is not st: st = new; self.note(st, 'release')
        return st

    def relievable_capacity(self, st):
        return any(r['_relievable'] for r in st.rows)

    # ---- full pipeline -----------------------------------------------------------------------
    def run(self, st0, q0, base_unrel, enforce, do_release):
        st = st0
        if enforce and self.relievable(st):
            st = self.enforce(st, base_unrel, self.guide['mode'])
            if do_release and self.feasible(st, base_unrel): st = self.release(st, q0, base_unrel, self.guide['mode'])
        return st


# ----------------------------------------------------------------------------------------------
# gas lift
# ----------------------------------------------------------------------------------------------
def _gl_wells(ns):
    out = []
    for n in ns:
        prm = n.get('params') or {}
        if n.get('kind') != 'well': continue
        avail = prm.get('available', True)
        if isinstance(avail, str): avail = avail.strip().lower() not in ('false', '0', 'no', 'off')
        if str(prm.get('lift_type', '')).lower().replace(' ', '_') == 'gas_lift' and avail: out.append(n['id'])
    return out


def _gas_lift_plan(opt, st, total, step):
    """Concave-hull marginal allocation of ``total`` Sm3/d over the gas-lift wells. Returns {well: Sm3/d}."""
    from physics.well_model import well_settings, solve_well_rate, well_state
    ns_by = {n['id']: n for n in st.ns}; ids = [w for w in _gl_wells(st.ns) if w in st.d]
    if not ids or total <= 0: return {}
    step = step if step and step > 0 else total / 16.0
    segs = []   # (slope, well, length, order)
    base_wv = opt.ev.well_vars(st.d, st.ns, st.info)
    for wid in ids:
        prm = {k: v for k, v in effective_params(st.d[wid], ns_by[wid].get('params')).items() if k != CAP_KEY}
        whp = float(st.d[wid].get('whp_bar', 20.0)); gmax = min(total, float(prm.get('max_gas_lift_sm3d', total)))
        ngrid = max(int(math.ceil(gmax / step)), 1); grid = [min(k * step, gmax) for k in range(ngrid + 1)]
        vals = []
        for g in grid:
            prm2 = dict(prm); prm2[GL_KEY] = g; s = well_settings(prm2)
            q, _ = solve_well_rate(whp, s); det = dict(st.d[wid]); det.update(well_state(q, whp, s))
            det['gas_lift_sm3d'] = g
            d2 = dict(st.d); d2[wid] = det
            try: vals.append(opt.ev(d2, st.info, st.ns))
            except ValueError: vals.append(-1e30)
        # upper concave hull from the first point
        hull = [0]
        for k in range(1, len(grid)):
            while len(hull) >= 2:
                a, b = hull[-2], hull[-1]
                if (vals[b] - vals[a]) * (grid[k] - grid[b]) <= (vals[k] - vals[b]) * (grid[b] - grid[a]): hull.pop()
                else: break
            hull.append(k)
        for j in range(1, len(hull)):
            a, b = hull[j - 1], hull[j]; length = grid[b] - grid[a]
            if length > 0: segs.append(((vals[b] - vals[a]) / length, wid, length, j))
    segs.sort(key=lambda s: (-s[0], s[3], str(s[1])))
    alloc = {w: 0.0 for w in ids}; left = float(total)
    for slope, w, length, _ in segs:
        if slope <= 1e-12 or left <= 1e-9: break
        take = min(length, left); alloc[w] += take; left -= take
    return alloc


# ----------------------------------------------------------------------------------------------
# public API
# ----------------------------------------------------------------------------------------------
def _consolidate_actions(ns0, ns1, st0, st1, reasons, opt):
    b0 = {n['id']: n for n in ns0}; acts = []
    for n in ns1:
        if n.get('kind') != 'well': continue
        prm = n.get('params') or {}; cap = prm.get(CAP_KEY); name = n.get('name', n['id'])
        q_before = float((st0.d.get(n['id']) or {}).get('liquid_rate_m3d', 0.0)); q_after = float((st1.d.get(n['id']) or {}).get('liquid_rate_m3d', 0.0))
        if cap is not None:
            acts.append({'component': name, 'component_id': n['id'], 'control': CAP_KEY, 'old': None, 'new': float(cap),
                         'rate_before_m3d': q_before, 'rate_after_m3d': q_after,
                         'reason': 'capacity: ' + ('; '.join(reasons.get(n['id'], [])) or 'network capacity')})
        g0 = (b0[n['id']].get('params') or {}).get(GL_KEY); g1 = prm.get(GL_KEY)
        if (g0 or 0.0) != (g1 or 0.0) and g1 is not None:
            acts.append({'component': name, 'component_id': n['id'], 'control': GL_KEY, 'old': g0, 'new': float(g1),
                         'rate_before_m3d': q_before, 'rate_after_m3d': q_after, 'reason': 'gas-lift allocation by marginal response'})
    return acts


def optimize_field(nodes, edges, solve_fn, *, objective, guide=None, enforce=True, optimize_gas_lift=False,
                   total_gas_lift_sm3d=None, gas_lift_step_sm3d=None, max_outer=12, guess=None, tol=1e-3,
                   baseline=True, release_solves=8):
    """Optimise well controls so facility / connection capacities are honoured with the best objective.

    ``solve_fn(nodes, edges, guess) -> (pressures, flows, info, details)``.  ``objective`` / ``guide`` are the
    dicts described in :mod:`optimization.objectives` (invalid specs raise ``ValueError`` up front; everything
    else is reported, never raised).  ``baseline=False`` skips the pro-rata comparison solves (cheaper per
    forecast step); ``release_solves`` is the solve budget of the release phase (0 disables it).
    Returns a dict: nodes, result (p,q,info,d), objective_value, baseline_value, constrained_baseline_value,
    improvement, improvement_pct, feasible, violations, reasons, actions, history, n_solves, method, runtime_s,
    warnings, well_caps_m3d, gas_lift_sm3d, objective, guide.
    """
    t0 = time.perf_counter()
    ev = ObjectiveEvaluator(objective); g = normalize_guide(guide)
    if guide is None: g['mode'] = 'objective'
    opt = _Optimizer(edges, solve_fn, ev, g, tol, max_outer, release_solves)
    ns_in = deepcopy(nodes)
    for n in ns_in:
        if isinstance(n.get('params'), dict): n['params'].pop(CAP_KEY, None)

    out = {'nodes': ns_in, 'result': None, 'objective_value': None, 'baseline_value': None, 'constrained_baseline_value': None,
           'improvement': None, 'improvement_pct': None, 'feasible': False, 'violations': [], 'reasons': [], 'actions': [],
           'history': opt.history, 'n_solves': 0, 'method': '', 'runtime_s': 0.0, 'warnings': opt.warnings,
           'well_caps_m3d': {}, 'gas_lift_sm3d': {}, 'objective': ev.objective, 'guide': g}

    def finish(): out['n_solves'] = opt.n_solves; out['runtime_s'] = time.perf_counter() - t0; return out

    st0 = opt.solve(ns_in, guess, 'unconstrained')
    if st0 is None:
        out['reasons'].append('initial network solve failed: ' + ('; '.join(opt.warnings) or 'unknown error')); out['method'] = 'failed'
        return finish()
    opt.note(st0, 'unconstrained')
    out['baseline_value'] = st0.obj; base_unrel = opt.n_unrelievable(st0)
    q0 = {w: float(v.get('liquid_rate_m3d', 0.0)) for w, v in st0.d.items()}
    method = ['unconstrained solve']

    cands = []   # (objective, order, state, feasible)
    if enforce:
        if baseline and opt.relievable(st0):
            h_save = list(opt.history)
            stb = opt.enforce(st0, base_unrel, 'pro_rata', 'pro-rata baseline')
            opt.history[:] = h_save
            out['constrained_baseline_value'] = stb.obj
            if g['mode'] == 'objective':      # an explicit guide is a user rule and is never overridden
                cands.append((stb, opt.feasible(stb, base_unrel), 'pro-rata choke-back'))
        elif baseline:
            out['constrained_baseline_value'] = st0.obj
        method.append(f"curtail by {'marginal objective value' if g['mode'] == 'objective' else g['mode']} guide")
    elif baseline:
        out['constrained_baseline_value'] = st0.obj

    st = opt.run(st0, q0, base_unrel, enforce, True)
    cands.append((st, opt.feasible(st, base_unrel), 'optimised'))
    if enforce and opt.release_solves > 0: method.append('release (secant/bisection)')

    if optimize_gas_lift:
        gl_ids = _gl_wells(ns_in)
        total = total_gas_lift_sm3d
        if total is None: total = sum(float((n.get('params') or {}).get(GL_KEY, 0.0) or 0.0) for n in ns_in if n['id'] in gl_ids)
        if not gl_ids or not total or total <= 0:
            opt.warnings.append('gas-lift optimisation skipped: no gas-lift wells or no gas-lift budget')
        else:
            try:
                alloc = _gas_lift_plan(opt, st0, float(total), gas_lift_step_sm3d)
            except Exception as exc:
                alloc = {}; opt.warnings.append(f'gas-lift planning failed: {exc}')
            if alloc:
                ns_gl = deepcopy(ns_in)
                for n in ns_gl:
                    if n['id'] in alloc: n.setdefault('params', {})[GL_KEY] = alloc[n['id']]
                stg0 = opt.solve(ns_gl, st0.guess, 'gas lift')
                if stg0 is not None:
                    opt.note(stg0, 'gas lift')
                    q0g = {w: float(v.get('liquid_rate_m3d', 0.0)) for w, v in stg0.d.items()}
                    stg = opt.run(stg0, q0g, base_unrel, enforce, True)
                    cands.append((stg, opt.feasible(stg, base_unrel), 'optimised + gas lift'))
                    method.append('gas-lift allocation (concave marginal response)')

    feas = [c for c in cands if c[1]]
    pool = feas if feas else cands
    if feas: best = max(pool, key=lambda c: (c[0].obj, 0 if c[2].startswith('pro-rata') else 1))
    else: best = min(pool, key=lambda c: (opt.max_violation(c[0]), -c[0].obj))
    stf = best[0]
    if best[2].startswith('pro-rata'): method.append('(fell back to pro-rata: better verified objective)')

    # reasons per capped well, remaining violations
    reasons = defaultdict(list)
    for r in opt.violated(st0):
        for w in r['_wells']:
            reasons[w].append(f"{r.get('Component')} {r.get('Constraint')} ({float(r['Value']):.0f} > {float(r['Limit']):.0f} {r.get('Unit', '')})")
    viol = []
    for r in opt.violated(stf):
        z = {k: v for k, v in r.items() if not k.startswith('_')}; z['Relievable'] = r['_relievable']; viol.append(z)
    out.update({'nodes': stf.ns, 'result': stf.result, 'objective_value': stf.obj, 'feasible': not viol, 'violations': viol,
                'actions': _consolidate_actions(ns_in, stf.ns, st0, stf, reasons, opt), 'method': ' -> '.join(method)})
    if viol:
        out['reasons'] = [f"{v['Component']} {v['Constraint']} violated by {abs(v['Margin']):.4g} {v.get('Unit', '')}"
                          + ('' if v['Relievable'] else ' (not relievable by well rates)') for v in viol]
        if enforce and any(v['Relievable'] for v in viol):
            out['reasons'].append('capacity cannot be met with the available wells (all upstream wells at zero rate) or iteration limit reached')
    out['well_caps_m3d'] = {n['id']: float(n['params'][CAP_KEY]) for n in stf.ns if (n.get('params') or {}).get(CAP_KEY) is not None}
    out['gas_lift_sm3d'] = {w: float((n.get('params') or {}).get(GL_KEY, 0.0) or 0.0) for n in stf.ns for w in [n['id']] if w in _gl_wells(stf.ns)}
    cb = out['constrained_baseline_value']
    if cb is not None:
        out['improvement'] = stf.obj - cb; out['improvement_pct'] = 100.0 * (stf.obj - cb) / max(abs(cb), 1e-12)
    return finish()


def make_step_solver(objective, guide=None, **opts):
    """Return ``f(nodes, edges, guess) -> (p, q, info, d)`` running the optimiser; ``info['optimizer']`` summarises it.

    Options are forwarded to :func:`optimize_field` (defaults tuned for per-timestep use: ``baseline=False``,
    ``release_solves=4``, ``max_outer=6``).  ``solve_fn`` may be supplied to replace the default
    ``solver.steady_state.solve_network``.  ``info['constraint_actions']`` is filled in the legacy
    format of ``solver.v21.enforce_capacity_constraints`` so existing reports keep working.
    """
    ObjectiveEvaluator(objective); normalize_guide(guide)   # fail early on invalid specs
    solve_fn = opts.pop('solve_fn', None)
    o = {'baseline': False, 'release_solves': 4, 'max_outer': 6}; o.update(opts)

    def step(nodes, edges, guess=None):
        fn = solve_fn
        if fn is None:
            from solver.steady_state import solve_network_robust
            fn = lambda ns, es, g: solve_network_robust(ns, es, initial_guess=g)
        res = optimize_field(nodes, edges, fn, objective=objective, guide=guide, guess=guess, **o)
        if res['result'] is None:
            return {}, {}, {'success': False, 'message': '; '.join(res['reasons']) or 'optimiser solve failed', 'max_abs_residual': float('inf'),
                            'constraints': [], 'violations': 0, 'optimizer': {'feasible': False, 'reasons': res['reasons']}}, {}
        p, q, info, d = res['result']; info = dict(info)
        names = {n['id']: n.get('name', n['id']) for n in res['nodes']}
        info['optimizer'] = {'objective': res['objective'], 'guide': res['guide'], 'objective_value': res['objective_value'],
                             'baseline_value': res['baseline_value'], 'feasible': res['feasible'], 'n_solves': res['n_solves'],
                             'runtime_s': res['runtime_s'], 'method': res['method'], 'actions': res['actions'], 'well_caps_m3d': res['well_caps_m3d'],
                             'gas_lift_sm3d': res['gas_lift_sm3d'], 'violations': res['violations'], 'warnings': list(res['warnings'])}
        info['constraint_actions'] = [{'well': names.get(a['component_id'], a['component']), 'well_id': a['component_id'], 'constraint': a['reason'],
                                       'cap_m3d': a['new'], 'message': f"{a['component']} {a['control']} -> {a['new']:.1f} ({a['reason']})"}
                                      for a in res['actions'] if a['control'] == CAP_KEY]
        info['constraints_enforced'] = True
        return p, q, info, d
    return step
