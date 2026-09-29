"""FieldNet v16 field-development scenario orchestration.

This layer deliberately wraps the validated quasi-steady network forecast rather than
embedding time logic in the hydraulic solver. It is a planning/screening workflow,
not a transient reservoir simulator.
"""
from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable

from network.forecast import DAYS_PER_YEAR, run_forecast


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)).isoformat()


def _coerce_value(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in {"true", "false"}:
            return text.lower() == "true"
        try:
            return float(text) if any(c in text.lower() for c in (".", "e")) else int(text)
        except ValueError:
            return text
    return value


@dataclass(frozen=True)
class DevelopmentEvent:
    date: str
    target_id: str
    field: str
    value: Any
    description: str = ""

    def as_forecast_event(self) -> dict[str, Any]:
        return {"date": _iso(self.date), "target_id": str(self.target_id), "field": str(self.field), "value": _coerce_value(self.value)}


@dataclass
class DevelopmentScenario:
    name: str = "Base"
    start_date: str = "2026-01-01"
    years: float = 5.0
    step_days: int = 30
    events: list[DevelopmentEvent] = dc_field(default_factory=list)
    depletion: dict[str, dict[str, float]] = dc_field(default_factory=dict)
    metadata: dict[str, Any] = dc_field(default_factory=dict)

    def validate(self) -> None:
        _iso(self.start_date)
        if self.years <= 0:
            raise ValueError("Scenario years must be > 0")
        if self.step_days <= 0:
            raise ValueError("Scenario step_days must be > 0")
        for ev in self.events:
            _iso(ev.date)
            if not ev.target_id or not ev.field:
                raise ValueError("Every development event needs target_id and field")


def exact_timeline(start_date: str, years: float, step_days: int) -> list[tuple[str, int]]:
    """Return (date, interval_days) with the last interval clipped to the horizon."""
    start = date.fromisoformat(_iso(start_date))
    horizon = max(int(round(float(years) * DAYS_PER_YEAR)), 0)
    out: list[tuple[str, int]] = []
    elapsed = 0
    while elapsed <= horizon:
        dt = min(int(step_days), max(horizon - elapsed, 0))
        out.append(((start + timedelta(days=elapsed)).isoformat(), dt))
        if elapsed == horizon:
            break
        elapsed += dt
    return out


def _injection_rates(nodes: list[dict], edges: list[dict], flows: dict[str, float]) -> tuple[float, float]:
    byid = {n.get("id"): n for n in nodes}
    water = gas = 0.0
    for edge in edges:
        target = byid.get(edge.get("target"), {})
        kind = target.get("kind")
        if kind not in {"water_injector", "gas_injector", "injector"}:
            continue
        rate = abs(float(flows.get(edge.get("id"), 0.0)))
        fluid = target.get("params", {}).get("injection_fluid", "water")
        if fluid == "gas" or kind == "gas_injector":
            gas += rate
        else:
            water += rate
    return water, gas


def _constraint_name(row: dict[str, Any]) -> str:
    return str(row.get("name") or row.get("constraint") or row.get("type") or row.get("id") or "constraint")


def run_development_scenario(
    nodes: list[dict],
    edges: list[dict],
    scenario: DevelopmentScenario,
    forecast_runner: Callable[..., dict] = run_forecast,
) -> dict[str, Any]:
    """Run one immutable development scenario and enrich the legacy forecast output."""
    scenario.validate()
    # v16.1 audit: fail fast on schedule targets that do not exist. The legacy
    # forecast event applier intentionally ignores unknown IDs, which is unsafe for
    # development planning because a typo can otherwise look like a successful run.
    valid_ids = {str(x.get("id")) for x in [*nodes, *edges] if x.get("id") is not None}
    unknown = sorted({str(ev.target_id) for ev in scenario.events if str(ev.target_id) not in valid_ids})
    if unknown:
        raise ValueError("Unknown development event target_id(s): " + ", ".join(unknown))
    base_nodes, base_edges = copy.deepcopy(nodes), copy.deepcopy(edges)
    events = [ev.as_forecast_event() for ev in sorted(scenario.events, key=lambda e: _iso(e.date))]
    fc = forecast_runner(base_nodes, base_edges, _iso(scenario.start_date), float(scenario.years), int(scenario.step_days), events, copy.deepcopy(scenario.depletion))
    field_rows = copy.deepcopy(fc.get("field", []))
    timeline = exact_timeline(scenario.start_date, scenario.years, scenario.step_days)
    cum_oil = cum_water = cum_gas = cum_water_inj = cum_gas_inj = 0.0

    # Forecast runner does not expose solved edge flows per timestep, so explicit injection
    # accounting accepts optional rate columns from an injected/custom runner. Default is 0.
    for idx, row in enumerate(field_rows):
        dt = timeline[idx][1] if idx < len(timeline) else int(scenario.step_days)
        oil = float(row.get("Oil [m3/d]", 0.0)); water = float(row.get("Water [m3/d]", 0.0)); gas = float(row.get("Gas [Sm3/d]", 0.0))
        winj = float(row.get("Water injection [m3/d]", 0.0)); ginj = float(row.get("Gas injection [Sm3/d]", 0.0))
        cum_oil += oil * dt; cum_water += water * dt; cum_gas += gas * dt; cum_water_inj += winj * dt; cum_gas_inj += ginj * dt
        row.update({
            "Scenario": scenario.name,
            "Interval days": dt,
            "Cumulative oil [m3]": cum_oil,
            "Cumulative water [m3]": cum_water,
            "Cumulative gas [Sm3]": cum_gas,
            "Water injection [m3/d]": winj,
            "Gas injection [Sm3/d]": ginj,
            "Cumulative water injection [m3]": cum_water_inj,
            "Cumulative gas injection [Sm3]": cum_gas_inj,
        })

    constraints = copy.deepcopy(fc.get("constraints", []))
    counts = Counter(_constraint_name(x) for x in constraints)
    converged = [bool(r.get("Converged", False)) for r in field_rows]
    final = field_rows[-1] if field_rows else {}
    kpis = {
        "scenario": scenario.name,
        "timesteps": len(field_rows),
        "converged_timesteps": sum(converged),
        "convergence_fraction": (sum(converged) / len(converged)) if converged else 0.0,
        "final_liquid_m3d": float(final.get("Total liquid [m3/d]", 0.0)),
        "final_oil_m3d": float(final.get("Oil [m3/d]", 0.0)),
        "cumulative_liquid_m3": float(final.get("Cumulative liquid [m3]", 0.0)),
        "cumulative_oil_m3": cum_oil,
        "cumulative_water_m3": cum_water,
        "cumulative_gas_sm3": cum_gas,
        "cumulative_water_injection_m3": cum_water_inj,
        "cumulative_gas_injection_sm3": cum_gas_inj,
        "constraint_events": len(constraints),
        "top_constraints": [{"constraint": k, "occurrences": v} for k, v in counts.most_common(10)],
    }
    return {"name": scenario.name, "scenario": scenario, "forecast": {**fc, "field": field_rows, "constraints": constraints}, "kpis": kpis}


def run_development_scenarios(nodes: list[dict], edges: list[dict], scenarios: Iterable[DevelopmentScenario], forecast_runner: Callable[..., dict] = run_forecast) -> list[dict[str, Any]]:
    return [run_development_scenario(copy.deepcopy(nodes), copy.deepcopy(edges), s, forecast_runner=forecast_runner) for s in scenarios]
