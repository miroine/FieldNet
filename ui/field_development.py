"""Streamlit adapter and export helpers for FieldNet v16 field development."""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import pandas as pd
import plotly.express as px

from network.field_development import DevelopmentEvent, DevelopmentScenario, run_development_scenarios
from ui.widgets import clean_text

EVENT_COLUMNS = ["date", "target_id", "field", "value", "description"]


def parse_event_rows(rows: list[dict[str, Any]]) -> list[DevelopmentEvent]:
    events: list[DevelopmentEvent] = []
    for row in rows:
        d, tid, fld = clean_text(row.get("date")), clean_text(row.get("target_id")), clean_text(row.get("field"))
        if not d or not tid or not fld:
            continue  # blank editor rows arrive as NaN, which is truthy; skip them explicitly
        try:
            d = pd.Timestamp(d).date().isoformat()
        except Exception as exc:
            raise ValueError(f"Invalid event date {d!r}") from exc
        val = row.get("value")
        if not isinstance(val, (list, dict)) and pd.isna(val):
            val = None
        events.append(DevelopmentEvent(d, tid, fld, val, clean_text(row.get("description"))))
    return events


def results_frames(results: list[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    field, constraints, summary = [], [], []
    for result in results:
        name = result["name"]
        for row in result["forecast"].get("field", []): field.append({"Scenario": name, **row})
        for row in result["forecast"].get("constraints", []): constraints.append({"Scenario": name, **row})
        k = result["kpis"]
        summary.append({
            "Scenario": name,
            "Final liquid [m3/d]": k["final_liquid_m3d"],
            "Final oil [m3/d]": k["final_oil_m3d"],
            "Cumulative liquid [m3]": k["cumulative_liquid_m3"],
            "Cumulative oil [m3]": k["cumulative_oil_m3"],
            "Cumulative water [m3]": k["cumulative_water_m3"],
            "Cumulative gas [Sm3]": k["cumulative_gas_sm3"],
            "Water injection [m3]": k["cumulative_water_injection_m3"],
            "Gas injection [Sm3]": k["cumulative_gas_injection_sm3"],
            "Convergence [%]": 100.0 * k["convergence_fraction"],
            "Constraint events": k["constraint_events"],
        })
    return pd.DataFrame(field), pd.DataFrame(constraints), pd.DataFrame(summary)


def export_field_csv(results: list[dict[str, Any]]) -> str:
    return results_frames(results)[0].to_csv(index=False)


def export_summary_json(results: list[dict[str, Any]]) -> str:
    payload = {"application": "FieldNet v29", "scenarios": []}
    for result in results:
        scenario = result["scenario"]
        payload["scenarios"].append({"definition": asdict(scenario), "kpis": result["kpis"]})
    return json.dumps(payload, indent=2, default=str)


def render_field_development(st, nodes: list[dict], edges: list[dict]) -> None:
    st.subheader("v16.1 Field Development & Integrated Forecasting")
    st.caption("Schedule-driven, quasi-steady field planning. Each scenario reuses the network forecast kernel; this is not a transient reservoir simulator or reserves certification workflow.")
    c1, c2, c3, c4 = st.columns(4)
    start = c1.date_input("Development start", key="v16_start").isoformat()
    years = c2.number_input("Horizon [years]", 0.1, 50.0, 5.0, 0.5, key="v16_years")
    step = c3.selectbox("Timestep [days]", [7, 14, 30, 60, 90], index=2, key="v16_step")
    names_text = c4.text_input("Scenarios", "Base, Low, High", key="v16_names")
    names = [x.strip() for x in names_text.split(",") if x.strip()] or ["Base"]

    st.markdown("**Development schedule**")
    st.caption("Examples: `params.available=False` for an outage; `pressure_bar=30` for a boundary change; `params.max_rate_m3d=4000` for a capacity expansion. Target IDs are shown below.")
    ids = pd.DataFrame([{"ID": n.get("id"), "Name": n.get("name"), "Type": n.get("kind")} for n in nodes] + [{"ID": e.get("id"), "Name": e.get("id"), "Type": e.get("kind")} for e in edges])
    with st.expander("Network target IDs"):
        st.dataframe(ids, hide_index=True, use_container_width=True)
    default_events = pd.DataFrame(columns=EVENT_COLUMNS)
    event_df = st.data_editor(default_events, num_rows="dynamic", use_container_width=True, key="v16_events")

    st.markdown("**Scenario depletion multipliers**")
    st.caption("Multiplier is applied to each well's configured pressure-decline coefficient. Values below are planning assumptions, not probabilities.")
    multipliers = {}
    cols = st.columns(min(3, len(names)))
    for i, name in enumerate(names):
        default = 1.0 if name.lower() == "base" else (1.25 if name.lower() == "low" else 0.75 if name.lower() == "high" else 1.0)
        multipliers[name] = cols[i % len(cols)].number_input(f"{name} decline multiplier", 0.0, 10.0, float(default), 0.05, key=f"v16_mult_{i}")

    if st.button("▶ Run v16 development scenarios", type="primary", use_container_width=True):
        try:
            events = parse_event_rows(event_df.to_dict("records"))
        except ValueError as exc:
            st.error(str(exc)); events = None
        scenarios = []
        for name in names:
            dep = {}
            for well in [n for n in nodes if n.get("kind") == "well"]:
                prm = well.get("params", {})
                dep[well["id"]] = {
                    "pressure_decline_bar_per_1000m3": float(prm.get("pressure_decline_bar_per_1000m3", 0.03)) * multipliers[name],
                    "pressure_support_bar_per_day": float(prm.get("pressure_support_bar_per_day", 0.0)),
                    "min_reservoir_pressure_bar": float(prm.get("min_reservoir_pressure_bar", 20.0)),
                }
            scenarios.append(DevelopmentScenario(name=name, start_date=start, years=float(years), step_days=int(step), events=events, depletion=dep))
        if events is not None:
            with st.spinner("Running development scenarios..."):
                try:
                    st.session_state.v16_results = run_development_scenarios(nodes, edges, scenarios)
                except Exception as exc:
                    st.error(f"Development scenarios failed: {exc}")

    results = st.session_state.get("v16_results")
    if not results:
        return
    field_df, constraint_df, summary_df = results_frames(results)
    st.markdown("### Scenario comparison")
    st.dataframe(summary_df, hide_index=True, use_container_width=True)
    if not summary_df.empty:
        base_rows = summary_df[summary_df["Scenario"].str.lower() == "base"]
        best = (base_rows if not base_rows.empty else summary_df).iloc[0]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Scenarios", len(summary_df)); m2.metric(f"{best['Scenario']} cumulative oil", f"{best['Cumulative oil [m3]']/1e6:.3f} MMm³")
        m3.metric(f"{best['Scenario']} convergence", f"{best['Convergence [%]']:.1f}%"); m4.metric(f"{best['Scenario']} constraint events", int(best["Constraint events"]))
    if not field_df.empty:
        st.plotly_chart(px.line(field_df, x="Date", y="Oil [m3/d]", color="Scenario", title="Oil production by development scenario"), use_container_width=True)
        st.plotly_chart(px.line(field_df, x="Date", y="Total liquid [m3/d]", color="Scenario", title="Liquid production by development scenario"), use_container_width=True)
        if "Cumulative oil [m3]" in field_df:
            st.plotly_chart(px.line(field_df, x="Date", y="Cumulative oil [m3]", color="Scenario", title="Cumulative oil"), use_container_width=True)
        inj_cols = [c for c in ["Water injection [m3/d]", "Gas injection [Sm3/d]"] if c in field_df and field_df[c].abs().sum() > 0]
        if inj_cols:
            st.plotly_chart(px.line(field_df, x="Date", y=inj_cols, color="Scenario", title="Injection profiles"), use_container_width=True)
        with st.expander("Forecast timestep table"):
            st.dataframe(field_df, hide_index=True, use_container_width=True)
    st.markdown("### Constraint history")
    if constraint_df.empty:
        st.success("No constraint-history rows were reported by the forecast kernel.")
    else:
        st.dataframe(constraint_df, hide_index=True, use_container_width=True)
    d1, d2 = st.columns(2)
    d1.download_button("Download v16.1 timestep CSV", export_field_csv(results), "fieldnet_v16_1_development_forecast.csv", "text/csv", use_container_width=True)
    d2.download_button("Download v16.1 scenario JSON", export_summary_json(results), "fieldnet_v16_1_scenarios.json", "application/json", use_container_width=True)
