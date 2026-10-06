"""Primary phase of the field (oil or gas): which rate leads the cards, charts and nodal plots. 'Auto' follows the model."""
from __future__ import annotations

PREFS = ['Auto', 'Oil', 'Gas']
GAS_PHASES = ('gas', 'gas_condensate', 'condensate')


def detect(nodes, results=None, forecast=None):
    """'Gas' or 'Oil'. Order of evidence: the latest solve, the forecast, then the model (tank fluid phases, wells with a gas IPR)."""
    if results and results[0] and results[3]:
        d = results[3].values(); oil = sum(v.get('oil_rate_m3d', 0) or 0 for v in d); gas = sum(v.get('gas_rate_sm3d', 0) or 0 for v in d)
        if oil + gas > 0: return 'Gas' if gas / 1000.0 > oil else 'Oil'
    rows = (forecast or {}).get('field') or []
    if rows:
        oil = sum(r.get('Oil [m3/d]', 0) or 0 for r in rows); gas = sum(r.get('Gas [Sm3/d]', 0) or 0 for r in rows)
        if oil + gas > 0: return 'Gas' if gas / 1000.0 > oil else 'Oil'
    tanks = [n for n in nodes if n.get('kind') == 'reservoir']; wells = [n for n in nodes if n.get('kind') == 'well']
    gas_t = sum(1 for t in tanks if str((t.get('params') or {}).get('fluid_phase', 'oil')).lower() in GAS_PHASES)
    if tanks: return 'Gas' if gas_t > len(tanks) / 2 else 'Oil'
    gas_w = sum(1 for w in wells if str((w.get('params') or {}).get('ipr_model', '')).lower() == 'gas')
    return 'Gas' if wells and gas_w > len(wells) / 2 else 'Oil'


def resolve(pref, nodes, results=None, forecast=None):
    return pref if pref in ('Oil', 'Gas') else detect(nodes, results, forecast)


def current(st):
    """Resolved primary phase stored by the app for this run (defaults to Oil)."""
    return st.session_state.get('_phase_resolved', 'Oil')
