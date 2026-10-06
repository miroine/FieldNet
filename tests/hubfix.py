"""Shared fixture: demo field + 2-year forecast + steady solve + hub (cached per process)."""
import functools, datetime as dt


@functools.lru_cache(maxsize=1)
def demo_hub():
    from network.examples import demo_field_case
    from network.forecast import run_forecast
    from network import groups as grp, data_hub as dh
    from ui.graph_contract import solver_input
    from solver.steady_state import solve_network
    nodes, edges = demo_field_case()
    tank = next(n for n in nodes if n['kind'] == 'reservoir')
    grp.group_from_tank(nodes, tank['id'], 'North')
    fc = run_forecast(nodes, edges, dt.date(2027, 1, 1), years=2, step_days=180)
    ns, es = solver_input(nodes, edges)
    sol = solve_network(ns, es)
    hub = dh.build_hub(nodes, edges, sol, fc, 'm', 'm', 'm')
    return nodes, edges, sol, fc, hub
