"""Drilling / well-phasing plan used by the Development schedule tab (wells come on stream when their rig slot finishes)."""
from __future__ import annotations
import pandas as pd
from network.development_v26 import DevelopmentTask, DevelopmentPlan, run_development_plan, APPLICATION
from network.forecast import run_forecast
from ui.widgets import clean_num, clean_text

WELL_KINDS = ('well', 'water_injector', 'gas_injector')


def default_schedule(nodes):
    rows = []; order = 1
    for kind in WELL_KINDS:
        for n in nodes:
            if n.get('kind') != kind: continue
            rows.append({'Include': True, 'Order': order, 'ID': n['id'], 'Well': n.get('name', n['id']), 'Type': 'Producer' if kind == 'well' else 'Injector',
                         'Drill + complete [days]': 60 if kind == 'well' else 45, 'Not before (YYYY-MM-DD)': ''})
            order += 1
    return pd.DataFrame(rows)


def table(st, nodes):
    """Rigs input + editable drilling order table. Returns (rigs, dataframe) or (None, None) when there is nothing to drill."""
    base = default_schedule(nodes)
    if base.empty:
        st.info('Add wells or injectors to the network to plan their drilling sequence.'); return None, None
    sig = '|'.join(base['ID'])
    if st.session_state.get('sched_sig') != sig: st.session_state.sched_sig = sig; st.session_state.sched_df = base
    rigs = st.number_input('Rigs', 1, 10, 1, 1, key='sch_rigs', help='Rigs work through the list in order; each well comes on stream when its rig slot finishes.')
    df = st.data_editor(st.session_state.sched_df, hide_index=True, use_container_width=True, key='sched_editor_' + sig, disabled=['ID', 'Well', 'Type'],
                        column_config={'Include': st.column_config.CheckboxColumn(help='Untick to leave the well out of the plan (never drilled)'),
                                       'Order': st.column_config.NumberColumn(min_value=1, step=1, help='Drilling sequence'),
                                       'Drill + complete [days]': st.column_config.NumberColumn(min_value=0, step=5)})
    return int(rigs), df


def _plan(nodes, df, rigs, start, years, step):
    """-> (nodes with excluded wells off, DevelopmentPlan)."""
    rows = sorted([r for r in df.to_dict('records') if bool(r.get('Include'))], key=lambda r: clean_num(r.get('Order'), 999)); tasks = []
    for i, r in enumerate(rows):
        nb = clean_text(r.get('Not before (YYYY-MM-DD)')); nb = pd.Timestamp(nb).date().isoformat() if nb else start
        tasks.append(DevelopmentTask(f"T{i+1}", f"Drill {r['Well']}", 'drill_well', str(r['ID']), nb, int(clean_num(r.get('Drill + complete [days]'), 60)), (), f"RIG-{(i % int(rigs)) + 1}"))
    excluded = [r for r in df.to_dict('records') if not bool(r.get('Include'))]
    ns = [dict(n, params={**(n.get('params') or {}), 'available': False}) if any(n['id'] == r['ID'] for r in excluded) else n for n in nodes]
    return ns, DevelopmentPlan('Development plan', start, float(years), int(step), tasks)


def _base_job(args):
    """Top-level (picklable) all-wells-at-start forecast, run in a worker process."""
    ns, edges, start, years, step, events, caps, kw = args
    return run_forecast(ns, edges, start, years, step, events or None, None, enforce_constraints=caps, **kw)


def iter_drill(nodes, edges, start, years, step, caps, df, rigs, user_events, compare, step_solver=None, workers=1, vlp_segments=None, sink=None):
    """Generator version of the development-plan run, so it can be driven by ``RunController`` (Pause / Continue / Stop).

    Yields the same events as ``iter_forecast``; with ``compare`` the step counter spans both phases (scheduled plan, then the
    all-wells-at-start baseline). ``'step'`` events carry the partial scheduled-plan snapshot. The finished
    ``(plan result, baseline)`` is stored in ``sink['res']`` / ``sink['base']``.

    ``workers>=2`` runs the baseline in a second process at the same time as the scheduled plan (plain solver only: an
    optimiser closure cannot be sent to another process), and independent connected systems inside every step are solved in parallel.
    """
    from network.development_v26 import compile_plan
    from network.field_development import run_development_scenario, DevelopmentScenario
    from network.forecast import iter_forecast
    import copy
    ns, plan = _plan(nodes, df, rigs, start, years, step)
    compiled = compile_plan(plan, ns, edges)
    evs = [e.as_forecast_event() for e in sorted(compiled['events'], key=lambda e: str(e.date))] + list(user_events or [])
    kw = dict(workers=workers, vlp_segments=vlp_segments)
    fut = ex = None
    if compare and int(workers or 1) >= 2 and step_solver is None:
        try:
            from network.uncertainty import _mp_context
            from concurrent.futures import ProcessPoolExecutor
            ctxmp, _ = _mp_context(); ex = ProcessPoolExecutor(max_workers=1, mp_context=ctxmp)
            fut = ex.submit(_base_job, (ns, edges, start, float(years), int(step), list(user_events or []), bool(caps), dict(kw, workers=1)))
        except Exception: fut = ex = None
    try:
        fc = None; n_one = None
        for ev in iter_forecast(ns, edges, start, float(years), int(step), evs, None, enforce_constraints=bool(caps), step_solver=step_solver, workers=workers, vlp_segments=vlp_segments):
            n_one = ev['n_steps']
            if ev['type'] == 'done': fc = ev['result']; break
            ev = dict(ev, stage='Scheduled plan: ' + str(ev['stage'])); 
            if compare: ev['n_steps'] = 2 * n_one
            yield ev
        scen = DevelopmentScenario(plan.name, plan.start_date, plan.years, plan.step_days, compiled['events'], copy.deepcopy(plan.depletion), metadata={'source': 'v26 development plan'})
        res = run_development_scenario(copy.deepcopy(ns), copy.deepcopy(edges), scen, forecast_runner=lambda *a, **k: fc)
        res['application'] = APPLICATION; res['development_plan'] = compiled
        base = None
        if compare:
            if fut is not None:
                try: base = fut.result()
                except Exception: fut = None
            if fut is None:
                for ev in iter_forecast(ns, edges, start, float(years), int(step), list(user_events or []) or None, None, enforce_constraints=bool(caps), step_solver=step_solver, workers=workers, vlp_segments=vlp_segments):
                    if ev['type'] == 'done': base = ev['result']; break
                    yield dict(ev, stage='All wells at start: ' + str(ev['stage']), step=n_one + ev['step'], n_steps=2 * n_one, result=None)
        if sink is not None: sink['res'] = res; sink['base'] = base
        yield {'type': 'done', 'stage': 'Finished', 'step': n_one or 0, 'n_steps': n_one or 0, 'date': None, 'day': 0, 'horizon_days': 0, 'substep': 0, 'elapsed_s': 0.0, 'eta_s': 0.0, 'result': res['forecast']}
    finally:
        if fut is not None: fut.cancel()
        if ex is not None: ex.shutdown(wait=False, cancel_futures=True)


def run(nodes, edges, start, years, step, caps, df, rigs, user_events, compare, step_solver=None, progress=None):
    """-> (development plan result, all-wells-at-start forecast or None). ``progress(fraction, text)`` is optional."""
    prog = lambda off, share, label: ((lambda ev: progress(off + share * ev['step'] / max(ev['n_steps'], 1), f"{label}: {ev['stage']}") and None) if progress else None)
    rows = sorted([r for r in df.to_dict('records') if bool(r.get('Include'))], key=lambda r: clean_num(r.get('Order'), 999)); tasks = []
    for i, r in enumerate(rows):
        nb = clean_text(r.get('Not before (YYYY-MM-DD)')); nb = pd.Timestamp(nb).date().isoformat() if nb else start
        tasks.append(DevelopmentTask(f"T{i+1}", f"Drill {r['Well']}", 'drill_well', str(r['ID']), nb, int(clean_num(r.get('Drill + complete [days]'), 60)), (), f"RIG-{(i % int(rigs)) + 1}"))
    excluded = [r for r in df.to_dict('records') if not bool(r.get('Include'))]
    ns = [dict(n, params={**(n.get('params') or {}), 'available': False}) if any(n['id'] == r['ID'] for r in excluded) else n for n in nodes]
    share = 0.5 if compare else 1.0
    res = run_development_plan(ns, edges, DevelopmentPlan('Development plan', start, float(years), int(step), tasks),
                               forecast_runner=lambda nn, ee, d0, yr, st_, evs, dep, *a: run_forecast(nn, ee, d0, yr, st_, [*(evs or []), *user_events], dep, *a, enforce_constraints=bool(caps),
                                                                                                    step_solver=step_solver, progress=prog(0.0, share, 'Scheduled plan')))
    base = run_forecast(ns, edges, start, float(years), int(step), user_events or None, None, enforce_constraints=bool(caps), step_solver=step_solver, progress=prog(0.5, 0.5, 'All wells at start')) if compare else None
    return res, base
