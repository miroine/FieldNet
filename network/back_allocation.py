"""Back-allocation of metered production to wells, and well-test scheduling.

SCREENING-LEVEL tools. Back-allocation here is the common "well-test potential x uptime" scheme: each well's theoretical
production is (latest well-test rate) x (uptime fraction) x (period length); the fiscal / metered total of the period is
then shared pro rata, per phase, so that the allocated volumes ALWAYS sum exactly to the metered total
(allocation factor = metered / sum of theoretical). It does not model shrinkage, fuel/flare gas, commingled-stream
corrections, test-separator measurement uncertainty or choke-based/virtual-metering allocation.

Units are whatever the caller uses consistently (typically Sm3/d for rates and Sm3 for volumes); ``period_days`` links
rates to period volumes.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta

PHASES = ('oil', 'water', 'gas')


# ----------------------------------------------------------------------------------------------
# Back-allocation
# ----------------------------------------------------------------------------------------------
def _is_multi(measured):
    return bool(measured) and all(isinstance(v, dict) for v in measured.values())


def _latest_tests(well_tests):
    """Latest test per well (by 'date' when present, otherwise the last one listed)."""
    best = {}
    for i, t in enumerate(well_tests or []):
        w = t.get('well')
        if w is None: continue
        key = (str(t.get('date') or ''), i)
        if w not in best or key >= best[w][0]: best[w] = (key, t)
    return {w: t for w, (_, t) in best.items()}


def _uptime_for(uptime, period, well):
    if uptime is None: return 1.0
    u = uptime
    if period in u and isinstance(u[period], dict): u = u[period]
    v = u.get(well, 1.0)
    return min(max(float(v), 0.0), 1.0)


def _allocate_period(measured, tests, uptime, period, period_days):
    tests = _latest_tests(tests)
    wells = list(tests)
    theo = {w: {} for w in wells}
    for w in wells:
        up = _uptime_for(uptime, period, w)
        for ph in PHASES:
            theo[w][ph] = max(float(tests[w].get(ph, 0.0) or 0.0), 0.0) * up * period_days
    allocated = {w: {} for w in wells}; factors = {}; imb = {}; warnings = []
    for ph in PHASES:
        m = float(measured.get(ph, 0.0) or 0.0); s = sum(theo[w][ph] for w in wells)
        if s > 0:
            af = m / s
            for w in wells: allocated[w][ph] = theo[w][ph] * af
        else:
            af = None
            for w in wells: allocated[w][ph] = 0.0
            if m > 0: warnings.append(f"{ph}: metered {m:g} but zero test-potential x uptime -> {m:g} UNALLOCATED")
        factors[ph] = af
        a = sum(allocated[w][ph] for w in wells)
        imb[ph] = {'measured': m, 'theoretical': s, 'allocated': a, 'unallocated': m - a,
                   'theoretical_minus_measured': s - m,
                   'imbalance_pct': (100.0 * (s - m) / m) if m else None}
        if af is not None and (af < 0.9 or af > 1.1): warnings.append(f"{ph}: allocation factor {af:.3f} outside 0.90-1.10 -> check well tests / uptime / metering")
    return {'period': period, 'period_days': period_days, 'allocated': allocated,
            'allocated_rates': {w: {ph: allocated[w][ph] / period_days for ph in PHASES} for w in wells} if period_days else {},
            'theoretical': theo, 'allocation_factor': factors, 'imbalance': imb, 'warnings': warnings}


def back_allocate(measured_totals, well_tests, uptime=None, period_days=1.0):
    """Allocate metered totals to wells by well-test potential x uptime.

    ``measured_totals``: ``{'oil':V,'water':V,'gas':V}`` for one period, or ``{period: {...}}`` for several.
    ``well_tests``: list of ``{'well', 'oil', 'water', 'gas' [rates/day], optional 'date'}``; the latest test per well is used.
    ``uptime``: ``{well: fraction}`` (or ``{period: {well: fraction}}``); missing wells default to 1.0.
    ``period_days``: period length so test rates convert to volumes (default 1 -> totals are daily).

    Returns ``{'periods': {period: {...}}}`` where each period holds ``allocated`` (volumes per well/phase),
    ``allocated_rates``, ``theoretical``, ``allocation_factor`` per phase, and ``imbalance`` per phase
    (measured, theoretical, allocated, unallocated, imbalance_pct = (theoretical-measured)/measured), ``warnings``.
    For a single period the period's keys are also exposed at the top level. Wells without a test are listed in
    ``wells_without_test`` for periods where uptime references them.
    """
    if any(float(v) <= 0 for v in (period_days.values() if isinstance(period_days, dict) else [period_days])): raise ValueError('period_days must be > 0')
    multi = _is_multi(measured_totals)
    periods = measured_totals if multi else {'period': measured_totals}
    out = {'periods': {}}
    for p, m in periods.items():
        pd_ = float(period_days.get(p, 1.0)) if isinstance(period_days, dict) else float(period_days)
        res = _allocate_period(m, well_tests, uptime, p, pd_)
        up_src = (uptime or {}).get(p, uptime) if isinstance(uptime, dict) else {}
        tested = set(res['allocated'])
        res['wells_without_test'] = sorted(w for w in (up_src or {}) if not isinstance(up_src[w], dict) and w not in tested)
        out['periods'][p] = res
    if not multi: out.update(out['periods']['period'])
    return out


# ----------------------------------------------------------------------------------------------
# Well-test scheduling
# ----------------------------------------------------------------------------------------------
def _d(x):
    if x is None: return None
    if isinstance(x, datetime): return x.date()
    if isinstance(x, date): return x
    return datetime.fromisoformat(str(x)[:10]).date()


def schedule_well_tests(wells, last_test_dates, test_interval_days, test_separator_capacity=None, max_tests_per_day=1,
                        start_date=None, horizon_days=None):
    """Ordered well-test calendar, most-overdue (then highest-rate) wells first.

    ``wells``: list of ids, or dicts ``{'id', 'rate'}`` (rate = liquid m3/d, used as tie-break/priority and for the
    separator check). ``last_test_dates``: ``{well: date|ISO|None}`` (None/missing = never tested -> top priority).
    A well is *due* ``test_interval_days`` after its last test. ``test_separator_capacity`` (liquid m3/d) is the maximum
    rate the test separator can take: wells above it are NOT scheduled and reported in ``skipped`` (they need a
    different test method). ``max_tests_per_day`` caps tests per day. ``horizon_days`` defaults to the test interval,
    so every well gets one test inside it if capacity allows; wells that do not fit are listed in ``unscheduled``.

    Returns ``{'calendar': [{'date','well','rate','overdue_days','last_test'}...], 'skipped': [...], 'unscheduled': [...]}``.
    Screening model: no rig/lift-gas/downtime coupling and no test-duration stabilisation modelling (one slot = one test).
    """
    start = _d(start_date) or date.today()
    horizon = int(horizon_days if horizon_days is not None else test_interval_days)
    ws = [{'id': w, 'rate': 0.0} if not isinstance(w, dict) else {'id': w.get('id', w.get('well')), 'rate': float(w.get('rate', 0.0) or 0.0)} for w in wells]
    skipped = []; todo = []
    for w in ws:
        if test_separator_capacity is not None and w['rate'] > float(test_separator_capacity):
            skipped.append({'well': w['id'], 'rate': w['rate'], 'reason': f"rate exceeds test separator capacity {float(test_separator_capacity):g}"}); continue
        last = _d((last_test_dates or {}).get(w['id']))
        todo.append({**w, 'last': last, 'due': (last + timedelta(days=int(test_interval_days))) if last else None})
    cal = []; maxper = max(int(max_tests_per_day), 1)
    for off in range(max(horizon, 1)):
        day = start + timedelta(days=off)
        cands = [w for w in todo if w['due'] is None or w['due'] <= day]
        if not cands: continue
        def over(w): return 10 ** 6 if w['due'] is None else (day - w['due']).days
        cands.sort(key=lambda w: (-over(w), -w['rate'], str(w['id'])))
        for w in cands[:maxper]:
            cal.append({'date': day.isoformat(), 'well': w['id'], 'rate': w['rate'], 'overdue_days': None if w['due'] is None else (day - w['due']).days,
                        'last_test': w['last'].isoformat() if w['last'] else None})
            todo.remove(w)
        if not todo: break
    unscheduled = [{'well': w['id'], 'due': w['due'].isoformat() if w['due'] else None} for w in todo]
    return {'calendar': cal, 'skipped': skipped, 'unscheduled': unscheduled}
