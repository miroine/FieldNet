"""Guided schedule-event builder.

Replaces typing raw ``target_id`` / ``params.xxx`` strings with dropdown selection:
pick an event TYPE, pick the TARGET (network elements shown by name, filtered to the kinds
that support that event), enter a typed value in the user's display units, pick a date, press
Add.  The stored data structure is unchanged: a list of
:class:`network.field_development.DevelopmentEvent` (date, target_id, field, value,
description) whose ``field`` strings are applied by :func:`network.forecast.apply_events`
and whose values are CANONICAL (bar, m3/d, Sm3/d, m, kW ...).

The first half of this module is pure logic (no Streamlit import) so it can be unit tested;
``render_event_builder`` at the bottom is the thin Streamlit layer.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable

import pandas as pd

from network.field_development import DevelopmentEvent, _coerce_value
from physics import unit_system as us
from ui.widgets import clean_text

SHARED_KEY = "schedule_events"      # st.session_state key shared by every tab's builder
ALL_TARGETS = "__all__"            # sentinel target meaning "every applicable element"

WELL_KINDS = ("well",)
INJECTOR_KINDS = ("water_injector", "gas_injector")
BOUNDARY_KINDS = ("separator", "separator_stage", "sink", "oil_export", "gas_export", "water_disposal")
SEPARATOR_KINDS = ("separator", "separator_stage")
VALVE_KINDS = ("choke", "control_valve")

_PROFILE_ALIASES = {"english_oilfield": "field", "english": "field", "oilfield": "field", "us_field": "field", "si": "norwegian_si"}


def canon_profile(profile: str | None) -> str:
    p = str(profile or "norwegian_si").strip().lower()
    p = _PROFILE_ALIASES.get(p, p)
    if p not in us.PROFILES:
        raise ValueError(f"Unknown unit profile {profile!r}")
    return p


# --------------------------------------------------------------------------- unit helpers
# unit key -> (to_display, from_display, label function)
def _lbl(key):
    return lambda p: us.labels(p)[key]


_UNITS = {
    "liquid_rate": (us.liquid_rate_to_display, us.liquid_rate_from_display, _lbl("liquid_rate")),
    "gas_rate": (us.gas_rate_to_display, us.gas_rate_from_display, _lbl("gas_rate")),
    "pressure": (us.pressure_to_display, us.pressure_from_display, _lbl("pressure")),
    "power": (us.power_to_display, us.power_from_display, _lbl("power")),
    "diameter": (us.diameter_to_display, us.diameter_from_display, _lbl("diameter")),
    "length": (us.length_to_display, us.length_from_display, _lbl("length")),
    "pi": (us.pi_to_display, us.pi_from_display, lambda p: "Sm³/d/bar" if p == "norwegian_si" else "stb/d/psi"),
    "velocity": (us.velocity_to_display, us.velocity_from_display, lambda p: "m/s" if p == "norwegian_si" else "ft/s"),
    "fraction": (lambda v, p: float(v), lambda v, p: float(v), lambda p: "fraction (0–1)"),
    "none": (lambda v, p: float(v), lambda v, p: float(v), lambda p: ""),
}


def unit_label(unit: str, profile: str, unit_text: str | None = None) -> str:
    if unit == "none" and unit_text:
        return unit_text
    return _UNITS[unit][2](canon_profile(profile))


def to_display(unit: str, canonical: float, profile: str) -> float:
    return float(_UNITS[unit][0](float(canonical), canon_profile(profile)))


def from_display(unit: str, shown: float, profile: str) -> float:
    return float(f"{_UNITS[unit][1](float(shown), canon_profile(profile)):.10g}")


# --------------------------------------------------------------------------- catalog
@dataclass(frozen=True)
class EventType:
    id: str
    label: str                       # friendly dropdown text, e.g. 'Well: set maximum oil rate'
    prop: str                        # short property name used in sentences, e.g. 'Maximum oil rate'
    field: str                       # 'params.xxx' (or a top-level key such as 'pressure_bar'); '' for custom
    applies_to: tuple = ()           # NODE kinds
    edge_kinds: tuple = ()           # EDGE kinds
    value_kind: str = "number"       # 'number' | 'bool' | 'choice'
    unit: str = "none"               # key into the display-unit helpers (see _UNITS)
    min: float | None = None         # canonical units
    max: float | None = None
    default: float | None = None
    step: float | None = None
    help: str = ""
    choices: tuple = ()              # ((value, label), ...) for 'choice'
    bool_labels: tuple = ("Yes", "No")   # (label for True, label for False)
    fixed_value: Any = None          # not None => no value widget, event always stores this value
    bulk_label: str | None = None    # e.g. 'All wells' => offer a bulk target
    unit_text: str | None = None     # label for unit 'none' quantities (e.g. 'Cv', 'skin')
    custom: bool = False

    @property
    def all_kinds(self) -> tuple:
        """Union of node and edge kinds this event applies to."""
        return tuple(dict.fromkeys([*self.applies_to, *self.edge_kinds]))


def _t(**kw) -> EventType:
    return EventType(**kw)


_OPEN_SHUT = ("Open (in service)", "Shut in")

EVENT_CATALOG: list[EventType] = [
    # ---- wells
    _t(id="well_shut_in", label="Well: shut in", prop="Status", field="params.available", applies_to=WELL_KINDS,
       value_kind="bool", fixed_value=False, bool_labels=_OPEN_SHUT, bulk_label="All wells",
       help="Closes the well from this date (e.g. workover, outage, abandonment)."),
    _t(id="well_open", label="Well: open / start-up", prop="Status", field="params.available", applies_to=WELL_KINDS,
       value_kind="bool", fixed_value=True, bool_labels=_OPEN_SHUT, bulk_label="All wells",
       help="Opens the well from this date (new well on stream, end of a shut-in)."),
    _t(id="well_max_oil", label="Well: set maximum oil rate", prop="Maximum oil rate", field="params.max_oil_rate_m3d",
       applies_to=WELL_KINDS, unit="liquid_rate", min=0.0, max=1e5, default=1000.0, step=50.0, bulk_label="All wells",
       help="Well oil rate (stock-tank) is capped at this value."),
    _t(id="well_max_liquid", label="Well: set maximum liquid rate", prop="Maximum liquid rate", field="params.max_liquid_rate_m3d",
       applies_to=WELL_KINDS, unit="liquid_rate", min=0.0, max=2e5, default=2000.0, step=100.0, bulk_label="All wells",
       help="Well total liquid rate (oil + water) is capped at this value."),
    _t(id="well_max_water", label="Well: set maximum water rate", prop="Maximum water rate", field="params.max_water_rate_m3d",
       applies_to=WELL_KINDS, unit="liquid_rate", min=0.0, max=2e5, default=1000.0, step=50.0, bulk_label="All wells",
       help="Well water rate is capped at this value (water-handling or coning control)."),
    _t(id="well_max_gas", label="Well: set maximum gas rate", prop="Maximum gas rate", field="params.max_gas_rate_sm3d",
       applies_to=WELL_KINDS, unit="gas_rate", min=0.0, max=5e7, default=500000.0, step=10000.0, bulk_label="All wells",
       help="Well gas rate is capped at this value (GOR / gas-handling control)."),
    _t(id="well_max_drawdown", label="Well: set maximum drawdown", prop="Maximum drawdown", field="params.max_drawdown_bar",
       applies_to=WELL_KINDS, unit="pressure", min=0.0, max=500.0, default=100.0, step=5.0, bulk_label="All wells",
       help="Maximum reservoir-to-bottomhole pressure difference (sand / coning control)."),
    _t(id="well_min_bhp", label="Well: set minimum flowing BHP", prop="Minimum flowing BHP", field="params.min_bhp_bar",
       applies_to=WELL_KINDS, unit="pressure", min=0.0, max=1000.0, default=50.0, step=5.0, bulk_label="All wells",
       help="Lowest allowed bottomhole flowing pressure."),
    _t(id="well_gas_lift", label="Well: gas-lift injection rate", prop="Gas-lift injection rate", field="params.gas_lift_injection_sm3d",
       applies_to=WELL_KINDS, unit="gas_rate", min=0.0, max=2e6, default=50000.0, step=5000.0, bulk_label="All wells",
       help="Lift-gas rate injected into the well (needs lift type = gas lift)."),
    _t(id="well_skin", label="Well: stimulation / skin change", prop="Skin", field="params.skin",
       applies_to=WELL_KINDS, unit="none", unit_text="skin", min=-7.0, max=100.0, default=0.0, step=0.5, bulk_label="All wells",
       help="New skin factor after stimulation (negative) or damage (positive)."),
    _t(id="well_choke", label="Well: choke / opening factor", prop="Opening factor", field="params.availability_factor",
       applies_to=WELL_KINDS, unit="fraction", min=0.0, max=1.0, default=1.0, step=0.05, bulk_label="All wells",
       help="Fraction of the well rate that is allowed through (1 = fully open, 0.5 = choked back by half)."),
    _t(id="well_esp_speed", label="Well: change artificial lift (ESP speed)", prop="ESP speed", field="params.esp_speed_fraction",
       applies_to=WELL_KINDS, unit="fraction", min=0.3, max=1.2, default=1.0, step=0.05, bulk_label="All wells",
       help="ESP speed as a fraction of rated speed (needs lift type = ESP)."),
    _t(id="well_lift_type", label="Well: change lift method", prop="Lift method", field="params.lift_type",
       applies_to=WELL_KINDS, value_kind="choice", choices=(("none", "Natural flow"), ("gas_lift", "Gas lift"), ("esp", "ESP")),
       bulk_label="All wells", help="Switches the artificial lift method from the event date."),
    # ---- injectors
    _t(id="inj_max_rate", label="Injector: set rate limit", prop="Injection rate limit", field="params.max_rate_m3d",
       applies_to=INJECTOR_KINDS, unit="liquid_rate", min=0.0, max=2e5, default=3000.0, step=100.0, bulk_label="All injectors",
       help="Maximum injection rate (flowing m³/d for water; Sm³/d for gas)."),
    _t(id="inj_availability", label="Injector: open / shut in", prop="Status", field="params.available",
       applies_to=INJECTOR_KINDS, value_kind="bool", bool_labels=_OPEN_SHUT, default=1.0, bulk_label="All injectors",
       help="Opens or shuts in the injector."),
    # ---- separators / boundaries
    _t(id="sep_pressure", label="Separator: set operating pressure", prop="Operating pressure", field="pressure_bar",
       applies_to=BOUNDARY_KINDS, unit="pressure", min=1.0, max=300.0, default=20.0, step=1.0,
       help="Boundary pressure of the separator / export node (e.g. lower for a low-pressure stage)."),
    _t(id="sep_oil", label="Separator: set oil handling capacity", prop="Oil handling capacity", field="params.max_oil_rate_m3d",
       applies_to=SEPARATOR_KINDS, unit="liquid_rate", min=0.0, max=5e5, default=5000.0, step=100.0,
       help="Maximum oil rate the separator can process."),
    _t(id="sep_water", label="Separator: set water handling capacity", prop="Water handling capacity", field="params.max_water_rate_m3d",
       applies_to=SEPARATOR_KINDS, unit="liquid_rate", min=0.0, max=5e5, default=5000.0, step=100.0,
       help="Maximum produced-water rate the separator / water treatment can process."),
    _t(id="sep_gas", label="Separator: set gas handling capacity", prop="Gas handling capacity", field="params.max_gas_rate_sm3d",
       applies_to=SEPARATOR_KINDS, unit="gas_rate", min=0.0, max=1e8, default=1e6, step=50000.0,
       help="Maximum gas rate the separator / compression train can process."),
    _t(id="sep_liquid", label="Separator: set liquid handling capacity", prop="Liquid handling capacity", field="params.max_liquid_rate_m3d",
       applies_to=SEPARATOR_KINDS, unit="liquid_rate", min=0.0, max=5e5, default=4500.0, step=100.0,
       help="Maximum total liquid rate (oil + water) the separator can process."),
    # ---- flowlines / risers
    _t(id="line_max_rate", label="Flowline/Riser: set maximum rate", prop="Maximum rate", field="params.max_rate_m3d",
       edge_kinds=("pipeline",), unit="liquid_rate", min=0.0, max=5e5, default=5000.0, step=100.0,
       help="Maximum liquid rate through the flowline or riser."),
    _t(id="line_max_gas", label="Flowline/Riser: set maximum gas rate", prop="Maximum gas rate", field="params.max_gas_rate_sm3d",
       edge_kinds=("pipeline",), unit="gas_rate", min=0.0, max=1e8, default=1e6, step=50000.0,
       help="Maximum gas rate through the flowline or riser."),
    _t(id="line_max_velocity", label="Flowline: set maximum velocity", prop="Maximum velocity", field="params.max_velocity_ms",
       edge_kinds=("pipeline",), unit="velocity", min=0.1, max=60.0, default=10.0, step=0.5,
       help="Erosional / operational velocity limit."),
    _t(id="line_debottleneck", label="Flowline: debottleneck (new diameter)", prop="Inner diameter", field="diameter_m",
       edge_kinds=("pipeline",), unit="diameter", min=0.025, max=1.5, default=0.3, step=0.005,
       help="Replaces the line inner diameter from the event date (loop line, pipe replacement)."),
    # ---- chokes / valves
    _t(id="valve_opening", label="Choke / valve: set opening", prop="Opening", field="params.opening",
       applies_to=VALVE_KINDS, edge_kinds=VALVE_KINDS, unit="fraction", min=0.0, max=1.0, default=1.0, step=0.05,
       help="Valve opening, 0 = closed, 1 = fully open."),
    _t(id="valve_cv", label="Choke / valve: set Cv", prop="Cv", field="params.cv",
       applies_to=VALVE_KINDS, edge_kinds=VALVE_KINDS, unit="none", unit_text="Cv", min=0.0, max=1e5, default=80.0, step=5.0,
       help="Valve flow coefficient (larger = less pressure drop)."),
    # ---- pumps
    _t(id="pump_power", label="Pump: set maximum power", prop="Maximum power", field="params.max_power_kw",
       applies_to=("pump",), edge_kinds=("pump",), unit="power", min=0.0, max=1e5, default=2000.0, step=50.0,
       help="Driver power limit of the pump."),
    _t(id="pump_speed", label="Pump: set speed", prop="Speed", field="params.speed_fraction",
       applies_to=("pump",), edge_kinds=("pump",), unit="fraction", min=0.3, max=1.2, default=1.0, step=0.05,
       help="Pump speed as a fraction of rated speed."),
    # ---- compressors
    _t(id="comp_ratio", label="Compressor: set pressure ratio", prop="Pressure ratio", field="params.pressure_ratio",
       applies_to=("compressor",), edge_kinds=("compressor",), unit="none", unit_text="–", min=1.0, max=10.0, default=1.8, step=0.1,
       help="Discharge / suction pressure ratio."),
    _t(id="comp_speed", label="Compressor: set speed", prop="Speed", field="params.speed_fraction",
       applies_to=("compressor",), edge_kinds=("compressor",), unit="fraction", min=0.3, max=1.2, default=1.0, step=0.05,
       help="Compressor speed as a fraction of rated speed."),
    _t(id="comp_max_discharge", label="Compressor: set maximum discharge pressure", prop="Maximum discharge pressure",
       field="params.max_discharge_bar", applies_to=("compressor",), edge_kinds=("compressor",), unit="pressure",
       min=1.0, max=500.0, default=100.0, step=1.0, help="Discharge pressure limit."),
    # ---- reservoir tank
    _t(id="tank_aquifer", label="Tank: aquifer strength", prop="Aquifer productivity index", field="params.aquifer_pi_m3d_bar",
       applies_to=("reservoir",), unit="pi", min=0.0, max=1e5, default=150.0, step=10.0,
       help="Aquifer influx productivity (m³/d per bar of pressure drop)."),
    # ---- custom
    _t(id="custom", label="Custom (advanced): type any path", prop="Custom", field="", applies_to=("*",), edge_kinds=("*",),
       value_kind="number", custom=True,
       help="Any field path, e.g. 'params.max_rate_m3d' or 'pressure_bar'. The value is stored exactly as typed (canonical units)."),
]
CATALOG_BY_ID: dict[str, EventType] = {t.id: t for t in EVENT_CATALOG}
CUSTOM = CATALOG_BY_ID["custom"]
_BY_LABEL = {t.label.lower(): t for t in EVENT_CATALOG}


def get_event_type(ref: str | EventType) -> EventType:
    if isinstance(ref, EventType):
        return ref
    key = str(ref).strip()
    if key in CATALOG_BY_ID:
        return CATALOG_BY_ID[key]
    if key.lower() in _BY_LABEL:
        return _BY_LABEL[key.lower()]
    raise ValueError(f"Unknown event type {ref!r}")


# --------------------------------------------------------------------------- targets
def _element(target_id: Any, nodes, edges) -> tuple[dict | None, bool]:
    """(object, is_edge). Nodes win over edges, exactly as network.forecast.apply_events does."""
    tid = str(target_id)
    for n in nodes or []:
        if str(n.get("id")) == tid:
            return n, False
    for e in edges or []:
        if str(e.get("id")) == tid:
            return e, True
    return None, False


def _applies(et: EventType, obj: dict, is_edge: bool) -> bool:
    kinds = et.edge_kinds if is_edge else et.applies_to
    return "*" in kinds or str(obj.get("kind")) in kinds


def target_label(obj: dict, is_edge: bool) -> str:
    name = obj.get("name") or obj.get("id")
    kind = str(obj.get("kind", "?"))
    if is_edge and kind == "pipeline" and (obj.get("params") or {}).get("role") == "riser":
        kind = "riser"
    elif is_edge and kind == "pipeline":
        kind = "flowline"
    return f"{name} ({kind.replace('_', ' ')})"


def applicable_targets(event_type: str | EventType, nodes, edges) -> list[tuple[str, str]]:
    """[(id, label)] of network elements this event type can act on (nodes first, then edges)."""
    et = get_event_type(event_type)
    out = []
    for n in nodes or []:
        if n.get("id") is not None and _applies(et, n, False):
            out.append((str(n["id"]), target_label(n, False)))
    for e in edges or []:
        if e.get("id") is not None and _applies(et, e, True):
            out.append((str(e["id"]), target_label(e, True)))
    return out


def available_event_types(nodes, edges) -> list[EventType]:
    """Catalog entries with at least one applicable target (custom is always offered)."""
    return [t for t in EVENT_CATALOG if t.custom or applicable_targets(t, nodes, edges)]


def expand_targets(event_type, target_id, nodes, edges) -> list[str]:
    """Resolve the ALL_TARGETS sentinel to concrete ids."""
    if target_id == ALL_TARGETS:
        return [i for i, _ in applicable_targets(event_type, nodes, edges)]
    return [str(target_id)]


def current_value(event_type, target_id, nodes, edges):
    """Canonical value of the event's field on the target right now (None if unset)."""
    et = get_event_type(event_type)
    obj, _ = _element(target_id, nodes, edges)
    if obj is None or not et.field:
        return None
    if et.field.startswith("params."):
        return (obj.get("params") or {}).get(et.field.split(".", 1)[1])
    return obj.get(et.field)


# --------------------------------------------------------------------------- display ranges
def display_range(event_type, profile) -> tuple[float | None, float | None, float | None, float | None]:
    """(min, max, default, step) in DISPLAY units for a number event type."""
    et = get_event_type(event_type)
    p = canon_profile(profile)

    def conv(v):
        return None if v is None else round(to_display(et.unit, v, p), 6)

    step = None
    if et.step is not None:
        base = et.min if et.min is not None else 0.0
        step = abs(to_display(et.unit, base + et.step, p) - to_display(et.unit, base, p))
        step = float(f"{step:.3g}") or None
    return conv(et.min), conv(et.max), conv(et.default), step


# --------------------------------------------------------------------------- events
def _iso_or_none(value) -> str | None:
    if value is None or (not isinstance(value, (str, date)) and pd.isna(value)) or value is pd.NaT:
        return None
    try:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        text = str(value).strip()
        return date.fromisoformat(text[:10]).isoformat() if text else None
    except (ValueError, TypeError):
        try:
            return pd.Timestamp(value).date().isoformat()
        except Exception:
            return None


def _to_event(x) -> DevelopmentEvent:
    if isinstance(x, DevelopmentEvent):
        return x
    if isinstance(x, dict):
        d = x.get("date")
        return DevelopmentEvent(_iso_or_none(d) or str(d), str(x.get("target_id", "")), str(x.get("field", "")),
                                x.get("value"), str(x.get("description", "") or ""))
    raise TypeError(f"Cannot interpret {type(x).__name__} as a schedule event")


def normalize_events(events: Iterable | None) -> list[DevelopmentEvent]:
    """Accept DevelopmentEvent objects and/or forecast-style dicts."""
    return [_to_event(e) for e in (events or [])]


def sort_events(events: Iterable) -> list[DevelopmentEvent]:
    """Stable sort by date (undated/invalid last)."""
    return sorted(normalize_events(events), key=lambda e: (_iso_or_none(e.date) is None, _iso_or_none(e.date) or ""))


def events_to_forecast(events: Iterable) -> list[dict]:
    """Plain dicts for network.forecast.run_forecast / prognosis.run_scenarios."""
    return [e.as_forecast_event() for e in sort_events(events)]


def _value_eq(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or (isinstance(a, bool) and isinstance(b, bool) and a == b)
    try:
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)
    except (TypeError, ValueError):
        return str(a) == str(b)


def match_event_type(ev, nodes, edges) -> EventType:
    """Best catalog entry describing an existing event (falls back to Custom)."""
    ev = _to_event(ev)
    obj, is_edge = _element(ev.target_id, nodes, edges)
    cands = [t for t in EVENT_CATALOG if not t.custom and t.field == str(ev.field)]
    if not cands:
        return CUSTOM
    if obj is not None:
        ok = [t for t in cands if _applies(t, obj, is_edge)]
        exact = [t for t in ok if t.fixed_value is not None and _value_eq(t.fixed_value, ev.value)]
        if exact:
            return exact[0]
        free = [t for t in ok if t.fixed_value is None]
        if free:
            return free[0]
        if ok:
            return ok[0]
    exact = [t for t in cands if t.fixed_value is not None and _value_eq(t.fixed_value, ev.value)]
    return (exact or cands)[0]


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in {"true", "1", "yes", "y", "open", "on", "open (in service)", "in service", "available"}:
        return True
    if s in {"false", "0", "no", "n", "shut", "shut in", "off", "closed", "unavailable"}:
        return False
    raise ValueError(f"cannot read {v!r} as open/shut")


def _canonical_value(et: EventType, value_display, profile: str, field_is_raw: bool = False):
    if et.fixed_value is not None:
        return et.fixed_value
    if et.custom:
        if value_display is None or str(value_display).strip() == "":
            raise ValueError("Custom events need a value")
        return _coerce_value(value_display)
    if et.value_kind == "bool":
        return _as_bool(value_display)
    if et.value_kind == "choice":
        s = str(value_display).strip()
        for val, lab in et.choices:
            if s.lower() in (str(val).lower(), lab.lower()):
                return val
        raise ValueError(f"{s!r} is not one of: " + ", ".join(lab for _, lab in et.choices))
    if isinstance(value_display, str):
        value_display = value_display.replace(",", "").strip()
    v = float(value_display)
    if not math.isfinite(v):
        raise ValueError("value must be a finite number")
    return from_display(et.unit, v, profile)


def build_event(event_type, target_id, value_display, date_, unit_profile="norwegian_si", description: str = "",
                field: str | None = None) -> DevelopmentEvent:
    """Create one DevelopmentEvent. ``value_display`` is in the user's unit profile and is converted to
    canonical units; ``field`` is only used by the Custom type."""
    et = get_event_type(event_type)
    if target_id in (None, "", ALL_TARGETS):
        raise ValueError("Pick a single target (use build_events for 'All')")
    iso = _iso_or_none(date_)
    if iso is None:
        raise ValueError(f"Invalid event date {date_!r}")
    path = (field or "").strip() if et.custom else et.field
    if not path:
        raise ValueError("Custom events need a field path, e.g. 'params.max_rate_m3d'")
    value = _canonical_value(et, value_display, canon_profile(unit_profile))
    return DevelopmentEvent(iso, str(target_id), path, value, str(description or ""))


def build_events(event_type, target_ids, value_display, date_, unit_profile="norwegian_si", description="",
                 nodes=None, edges=None, field=None) -> list[DevelopmentEvent]:
    """Bulk helper: ``target_ids`` may be a list or ALL_TARGETS (needs nodes/edges)."""
    ids = expand_targets(event_type, target_ids, nodes, edges) if target_ids == ALL_TARGETS else list(target_ids)
    return [build_event(event_type, i, value_display, date_, unit_profile, description, field) for i in ids]


# --------------------------------------------------------------------------- describing
def _fmt_num(x: float) -> str:
    a = abs(x)
    if a >= 100:
        return f"{x:,.0f}"
    if a >= 1:
        return f"{x:,.2f}".rstrip("0").rstrip(".")
    return f"{x:.3g}"


def format_value(et: EventType, value, profile) -> str:
    """Human text for a canonical value (with unit)."""
    p = canon_profile(profile)
    if et.custom:
        return str(value)
    if et.value_kind == "bool":
        try:
            return et.bool_labels[0 if _as_bool(value) else 1]
        except ValueError:
            return str(value)
    if et.value_kind == "choice":
        return next((lab for v, lab in et.choices if str(v) == str(value)), str(value))
    try:
        txt = _fmt_num(to_display(et.unit, float(value), p))
    except (TypeError, ValueError):
        return str(value)
    u = unit_label(et.unit, p, et.unit_text)
    return f"{txt} {u}" if u and not u.startswith("fraction") else txt


def describe_event(ev, nodes, edges, unit_profile="norwegian_si") -> str:
    """'2027-03-01 · PROD-A · Maximum oil rate → 1,200 Sm³/d' (+ ' — note')."""
    ev = _to_event(ev)
    et = match_event_type(ev, nodes, edges)
    obj, _ = _element(ev.target_id, nodes, edges)
    who = (obj.get("name") or obj.get("id")) if obj is not None else f"{ev.target_id} (unknown)"
    prop = ev.field if et.custom else et.prop
    text = f"{ev.date} · {who} · {prop} → {format_value(et, ev.value, unit_profile)}"
    return text + (f" — {ev.description}" if ev.description else "")


# --------------------------------------------------------------------------- validation
def _well_available_on(target_id, iso, events, nodes, edges) -> bool:
    obj, _ = _element(target_id, nodes, edges)
    avail = bool(((obj or {}).get("params") or {}).get("available", True))
    for e in sort_events(events):
        if e.target_id == target_id and e.field == "params.available" and (_iso_or_none(e.date) or "9") <= iso:
            try:
                avail = _as_bool(e.value)
            except ValueError:
                pass
    return avail


def validate_events(events, nodes, edges, start_date=None) -> list[str]:
    """Human-readable problems with a schedule (empty list = fine)."""
    evs = normalize_events(events)
    msgs: list[str] = []
    start = _iso_or_none(start_date) if start_date else None
    seen: dict[tuple, tuple[int, Any]] = {}
    for i, ev in enumerate(evs, 1):
        who = f"Event {i} ({ev.target_id} · {ev.field or '?'} · {ev.date})"
        iso = _iso_or_none(ev.date)
        if iso is None:
            msgs.append(f"{who}: invalid date {ev.date!r}.")
        elif start and iso < start:
            msgs.append(f"{who}: dated before the schedule start ({start}); it is applied from the first step.")
        if not ev.field:
            msgs.append(f"{who}: no field.")
        obj, is_edge = _element(ev.target_id, nodes, edges)
        if obj is None:
            msgs.append(f"{who}: unknown target '{ev.target_id}'.")
            continue
        et = match_event_type(ev, nodes, edges)
        if not et.custom and not _applies(et, obj, is_edge):
            kinds = ", ".join(et.all_kinds)
            msgs.append(f"{who}: '{et.label}' does not apply to a {obj.get('kind')} (needs: {kinds}).")
        # value checks
        if not et.custom:
            if et.value_kind == "number":
                try:
                    v = float(ev.value)
                    if isinstance(ev.value, bool) or not math.isfinite(v):
                        raise ValueError
                    tol = 1e-9 * max(1.0, abs(v))
                    if (et.min is not None and v < et.min - tol) or (et.max is not None and v > et.max + tol):
                        msgs.append(f"{who}: {et.prop} {format_value(et, v, 'norwegian_si')} is outside the allowed range "
                                    f"{_fmt_num(et.min) if et.min is not None else '-∞'} to {_fmt_num(et.max) if et.max is not None else '∞'} "
                                    f"({unit_label(et.unit, 'norwegian_si', et.unit_text)}).")
                except (TypeError, ValueError):
                    msgs.append(f"{who}: value {ev.value!r} is not a number.")
            elif et.value_kind == "bool":
                try:
                    _as_bool(ev.value)
                except ValueError:
                    msgs.append(f"{who}: value {ev.value!r} must be open/shut (true/false).")
            elif et.value_kind == "choice" and str(ev.value) not in {str(v) for v, _ in et.choices}:
                msgs.append(f"{who}: value {ev.value!r} is not one of " + ", ".join(str(v) for v, _ in et.choices) + ".")
        # same field + same date duplicates
        key = (ev.target_id, ev.field, iso)
        if key in seen:
            j, other = seen[key]
            if _value_eq(other, ev.value):
                msgs.append(f"{who}: duplicate of event {j}.")
            else:
                msgs.append(f"{who}: conflicts with event {j} (same target, property and date, different value; the later one wins).")
        else:
            seen[key] = (i, ev.value)
        # operating a well that is not yet available
        if (iso and obj.get("kind") == "well" and ev.field != "params.available"
                and not _well_available_on(ev.target_id, iso, evs, nodes, edges)):
            msgs.append(f"{who}: the well is not available (not yet started / shut in) on {iso}; "
                        "add a 'Well: open / start-up' event on or before this date.")
    return msgs


# --------------------------------------------------------------------------- tables / CSV
FRIENDLY_COLUMNS = ["No.", "Delete", "Date", "Event", "Target", "Value", "Unit", "Note", "Target ID", "Field"]
RAW_COLUMNS = ["date", "target_id", "field", "value", "description"]


def _value_text(et: EventType, value, profile: str) -> str:
    if et.fixed_value is not None:
        return et.bool_labels[0 if et.fixed_value else 1] if et.value_kind == "bool" else str(value)
    if et.custom:
        return str(value)
    if et.value_kind == "bool":
        try:
            return et.bool_labels[0 if _as_bool(value) else 1]
        except ValueError:
            return str(value)
    if et.value_kind == "choice":
        return next((lab for v, lab in et.choices if str(v) == str(value)), str(value))
    try:
        return f"{to_display(et.unit, float(value), profile):.6g}"
    except (TypeError, ValueError):
        return str(value)


def events_to_frame(events, nodes=None, edges=None, unit_profile="norwegian_si", raw: bool = False) -> pd.DataFrame:
    """Event list -> DataFrame. ``raw=True`` gives the legacy columns (canonical values, no conversion)."""
    evs = normalize_events(events)
    if raw:
        return pd.DataFrame([{"date": e.date, "target_id": e.target_id, "field": e.field, "value": "" if e.value is None else str(e.value),
                              "description": e.description} for e in evs], columns=RAW_COLUMNS, dtype=object)
    p = canon_profile(unit_profile)
    rows = []
    for i, e in enumerate(evs, 1):
        et = match_event_type(e, nodes, edges)
        obj, is_edge = _element(e.target_id, nodes, edges)
        rows.append({"No.": i, "Delete": False, "Date": e.date, "Event": et.label,
                     "Target": (obj.get("name") or obj.get("id")) if obj is not None else f"{e.target_id} (unknown)",
                     "Value": _value_text(et, e.value, p),
                     "Unit": "" if (et.custom or et.value_kind != "number" or et.fixed_value is not None) else unit_label(et.unit, p, et.unit_text),
                     "Note": e.description, "Target ID": e.target_id, "Field": e.field})
    return pd.DataFrame(rows, columns=FRIENDLY_COLUMNS)


def _norm_key(k) -> str:
    return str(k).strip().lower().replace(" ", "_").replace(".", "")


_ALIASES = {"event": "event", "event_type": "event", "type": "event", "date": "date", "target_id": "target_id", "id": "target_id",
            "target": "target", "target_name": "target", "field": "field", "path": "field", "value": "value", "unit": "unit",
            "note": "note", "notes": "note", "description": "note", "delete": "delete", "no": "no", "#": "no"}


def _norm_row(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        key = _ALIASES.get(_norm_key(k))
        if key and key not in out:
            out[key] = v
    return out


def _resolve_target(row: dict, nodes, edges, n: int) -> str:
    tid = clean_text(row.get("target_id"))
    if tid:
        return tid
    name = clean_text(row.get("target"))
    if not name:
        return ""
    if _element(name, nodes, edges)[0] is not None:
        return name
    hits = [str(x["id"]) for x in [*(nodes or []), *(edges or [])] if str(x.get("name", "")).strip().lower() == name.lower()]
    if len(hits) == 1:
        return hits[0]
    raise ValueError(f"Row {n}: target {name!r} " + ("is ambiguous (several elements share that name)." if hits else "was not found in the network."))


def _profile_from_unit(et: EventType, unit_text: str, default: str) -> str:
    """A CSV 'Unit' column naming another profile's unit (e.g. psi) selects that profile for conversion."""
    u = unit_text.strip()
    if not u or et.unit in ("none", "fraction"):
        return default
    if u == unit_label(et.unit, default, et.unit_text):
        return default
    for key in us.PROFILES:
        if u == unit_label(et.unit, key, et.unit_text):
            return key
    return default


def frame_to_events(frame, nodes=None, edges=None, unit_profile="norwegian_si", original=None) -> list[DevelopmentEvent]:
    """DataFrame / list of row dicts -> events. Understands both the friendly table (Event/Target/Value in
    display units) and the legacy raw table (target_id/field/value in canonical units).

    ``original`` (the events the friendly table was built from) lets unchanged rows keep their exact
    stored value instead of a display-rounded round-trip. Rows ticked 'Delete' and blank rows are skipped.
    Raises ValueError with the row number for unreadable rows.
    """
    p = canon_profile(unit_profile)
    rows = frame.to_dict("records") if hasattr(frame, "to_dict") else list(frame)
    orig = normalize_events(original) if original is not None else None
    orig_rows = events_to_frame(orig, nodes, edges, p).to_dict("records") if orig else []
    out: list[DevelopmentEvent] = []
    for n, raw_row in enumerate(rows, 1):
        r = _norm_row(raw_row)
        d, ev_label, field = clean_text(r.get("date")), clean_text(r.get("event")), clean_text(r.get("field"))
        delete = r.get("delete")
        if isinstance(delete, str):
            delete = delete.strip().lower() in {"true", "1", "yes", "y"}
        if delete is True or (hasattr(delete, "item") and bool(delete)):
            continue
        tid = _resolve_target(r, nodes, edges, n)
        if not (d or tid or field or ev_label):
            continue
        iso = _iso_or_none(d) if d else None
        if iso is None:
            raise ValueError(f"Row {n}: invalid or missing date {d!r}")
        if not tid:
            raise ValueError(f"Row {n}: missing target")
        note = clean_text(r.get("note"))
        val_raw = r.get("value")
        if isinstance(val_raw, float) and math.isnan(val_raw):
            val_raw = None
        if ev_label:                                      # friendly row
            # unchanged row -> keep the original event
            idx = r.get("no")
            try:
                idx = int(float(idx)) - 1
            except (TypeError, ValueError):
                idx = -1
            if orig and 0 <= idx < len(orig):
                o = orig_rows[idx]
                same = (clean_text(o["Date"]) == iso and clean_text(o["Value"]) == clean_text(val_raw) and clean_text(o["Note"]) == note
                        and clean_text(o["Event"]) == ev_label and clean_text(o["Target ID"]) == tid)
                if same:
                    out.append(orig[idx])
                    continue
            try:
                et = get_event_type(ev_label)
            except ValueError as exc:
                raise ValueError(f"Row {n}: {exc}") from None
            try:
                prof = _profile_from_unit(et, clean_text(r.get("unit")), p)
                out.append(build_event(et, tid, "" if val_raw is None else val_raw, iso, prof, note, field))
            except ValueError as exc:
                raise ValueError(f"Row {n}: {exc}") from None
        else:                                             # legacy raw row
            if not field:
                raise ValueError(f"Row {n}: missing field")
            out.append(DevelopmentEvent(iso, tid, field, None if val_raw is None else _coerce_value(val_raw), note))
    return out


def events_to_csv(events, nodes=None, edges=None, unit_profile="norwegian_si", raw: bool = False) -> str:
    """CSV text. Friendly (default) has display-unit values + Unit column; raw is the legacy format."""
    df = events_to_frame(events, nodes, edges, unit_profile, raw=raw)
    if not raw:
        df = df.drop(columns=["No.", "Delete"])
    return df.to_csv(index=False)


def events_from_csv(text, nodes=None, edges=None, unit_profile="norwegian_si") -> list[DevelopmentEvent]:
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8-sig")
    df = pd.read_csv(io.StringIO(str(text)), dtype=str, keep_default_na=False)
    return frame_to_events(df, nodes, edges, unit_profile)


# --------------------------------------------------------------------------- timeline
def timeline_rows(events, nodes, edges, unit_profile="norwegian_si") -> list[dict]:
    rows = []
    for e in sort_events(events):
        et = match_event_type(e, nodes, edges)
        obj, _ = _element(e.target_id, nodes, edges)
        rows.append({"date": e.date, "target": (obj.get("name") or obj.get("id")) if obj is not None else e.target_id,
                     "event": et.label, "text": describe_event(e, nodes, edges, unit_profile)})
    return rows


def timeline_text(events, nodes, edges, unit_profile="norwegian_si") -> str:
    """Compact markdown summary grouped by date."""
    out, last = [], None
    for r in timeline_rows(events, nodes, edges, unit_profile):
        if r["date"] != last:
            out.append(f"**{r['date']}**")
            last = r["date"]
        out.append(f"- {r['target']} · {r['text'].split(' · ', 2)[-1]}")
    return "\n".join(out)


def timeline_figure(events, nodes, edges, unit_profile="norwegian_si"):
    """Plotly timeline (one row per target), or None when plotly is unavailable / no events."""
    rows = timeline_rows(events, nodes, edges, unit_profile)
    if not rows:
        return None
    try:
        import plotly.graph_objects as go
    except ImportError:
        return None
    fig = go.Figure(go.Scatter(x=[r["date"] for r in rows], y=[r["target"] for r in rows], mode="markers",
                               marker={"size": 12, "symbol": "diamond"}, text=[r["text"] for r in rows], hoverinfo="text"))
    fig.update_layout(title="Schedule events", height=max(220, 60 + 40 * len({r["target"] for r in rows})),
                      margin={"l": 10, "r": 10, "t": 40, "b": 10}, yaxis={"autorange": "reversed"}, showlegend=False)
    return fig


# =========================================================================== Streamlit layer
def get_events(st, store_key: str = SHARED_KEY) -> list[DevelopmentEvent]:
    """Current shared schedule (what every tab's builder edits)."""
    return normalize_events(st.session_state.get(store_key))


def set_events(st, events, store_key: str = SHARED_KEY) -> None:
    st.session_state[store_key] = sort_events(events)


def _sig(events) -> tuple:
    return tuple((e.date, e.target_id, e.field, repr(e.value), e.description) for e in normalize_events(events))


def _commit(st, kp: str, store_key: str, new_events) -> None:
    st.session_state[kp + "_undo"] = list(get_events(st, store_key))
    st.session_state[store_key] = sort_events(new_events)
    st.session_state[kp + "_ver"] = int(st.session_state.get(kp + "_ver", 0)) + 1


def _val_key(kp: str, et: EventType, target: str, profile: str) -> str:
    return f"{kp}_val_{et.id}_{target}_{profile}"


def _cb_add(st, kp, store_key, nodes, edges, profile):
    """Add-button callback: runs before the rerun, so it may reset the note widget safely."""
    ss = st.session_state
    try:
        et = get_event_type(ss.get(kp + "_type"))
        target = ss.get(f"{kp}_tgt_{et.id}")
        if et.value_kind == "bool" and et.fixed_value is None:
            value = ss.get(_val_key(kp, et, target, profile), True)
        elif et.fixed_value is not None:
            value = et.fixed_value
        else:
            value = ss.get(_val_key(kp, et, target, profile), et.default)
        field = ss.get(kp + "_field") if et.custom else None
        when = ss.get(kp + "_date")
        note = ss.get(kp + "_note", "")
        new = build_events(et, target if target == ALL_TARGETS else [target], value, when, profile, note, nodes, edges, field)
        old = get_events(st, store_key)
        _commit(st, kp, store_key, [*old, *new])
        ss[kp + "_note"] = ""
        ss[kp + "_msg"] = ("success", f"Added {len(new)} event(s): " + describe_event(new[0], nodes, edges, profile) + (" …" if len(new) > 1 else ""))
    except Exception as exc:  # surfaced in the UI, never raised from a callback
        ss[kp + "_msg"] = ("error", f"Could not add event: {exc}")


def _cb_table(st, kp, store_key, nodes, edges, profile, editor_key):
    """data_editor on_change: apply edited rows (date / value / note / delete) to the shared schedule."""
    ss = st.session_state
    try:
        frame = ss[kp + "_frame"].copy()
        delta = ss.get(editor_key) or {}
        for idx, changes in (delta.get("edited_rows") or {}).items():
            for col, val in changes.items():
                frame.at[int(idx), col] = val
        old = get_events(st, store_key)
        new = frame_to_events(frame, nodes, edges, profile, original=old)
        if _sig(new) != _sig(old):
            _commit(st, kp, store_key, new)
    except Exception as exc:
        ss[kp + "_msg"] = ("error", f"Could not apply edit: {exc}")


def _cb_undo(st, kp, store_key):
    ss = st.session_state
    prev = ss.get(kp + "_undo")
    if prev is not None:
        cur = get_events(st, store_key)
        ss[store_key] = sort_events(prev)
        ss[kp + "_undo"] = cur
        ss[kp + "_ver"] = int(ss.get(kp + "_ver", 0)) + 1


def _cb_clear(st, kp, store_key):
    _commit(st, kp, store_key, [])


def render_event_builder(st, nodes, edges, events=None, unit_profile="norwegian_si", key_prefix="evb",
                         start_date=None, store_key: str = SHARED_KEY) -> list[DevelopmentEvent]:
    """Guided event editor. Returns the current list of DevelopmentEvent (also kept in
    ``st.session_state[store_key]`` so several tabs can share one schedule).

    ``events`` seeds the schedule (and replaces it if the caller passes a *different* list later).
    ``start_date`` (iso str / date) is the default for the date picker and the 'before start' check.
    """
    ss = st.session_state
    kp = key_prefix
    profile = canon_profile(unit_profile)
    nodes = nodes or []
    edges = edges or []
    # ---- seed / external replacement (events param is authoritative only when it changes)
    if events is not None and (store_key not in ss or _sig(events) != ss.get(kp + "_seen")):
        if store_key not in ss or _sig(events) != _sig(get_events(st, store_key)):
            ss[store_key] = sort_events(events)
        ss[kp + "_seen"] = _sig(events)
    elif store_key not in ss:
        ss[store_key] = []
    ss.setdefault(kp + "_ver", 0)
    default_date = _iso_or_none(start_date) or date.today().isoformat()

    types = available_event_types(nodes, edges)
    type_ids = [t.id for t in types]
    with st.container(border=True):
        st.markdown("**Add a schedule event**")
        msg = ss.pop(kp + "_msg", None)
        if kp + "_type" in ss and ss[kp + "_type"] not in type_ids:
            del ss[kp + "_type"]                              # stale selection (network changed)
        c1, c2, c3, c4, c5 = st.columns([2.3, 2.1, 1.7, 1.4, 1.8])
        et_id = c1.selectbox("Event type", type_ids, format_func=lambda i: CATALOG_BY_ID[i].label, key=kp + "_type")
        et = CATALOG_BY_ID[et_id]
        targets = applicable_targets(et, nodes, edges)
        labels = dict(targets)
        opts = [i for i, _ in targets]
        if et.bulk_label and len(opts) > 1:
            opts = [ALL_TARGETS, *opts]
            labels[ALL_TARGETS] = f"{et.bulk_label} ({len(targets)})"
        tkey = f"{kp}_tgt_{et.id}"
        if tkey in ss and ss[tkey] not in opts:
            del ss[tkey]
        target = c2.selectbox("Target", opts, format_func=lambda i: labels.get(i, i), key=tkey)
        # ---- typed value widget
        vkey = _val_key(kp, et, target, profile)
        cur = None if target == ALL_TARGETS else current_value(et, target, nodes, edges)
        if et.custom:
            c3.text_input("Field path", key=kp + "_field", placeholder="params.max_rate_m3d")
            c3.text_input("Value", key=vkey)
        elif et.fixed_value is not None:
            c3.markdown(f"**Value**  \n{et.bool_labels[0 if et.fixed_value else 1]}")
        elif et.value_kind == "bool":
            if vkey in ss and not isinstance(ss[vkey], bool):
                del ss[vkey]
            c3.selectbox("New status", [True, False], format_func=lambda b: et.bool_labels[0 if b else 1], key=vkey)
        elif et.value_kind == "choice":
            codes = [v for v, _ in et.choices]
            if vkey in ss and ss[vkey] not in codes:
                del ss[vkey]
            c3.selectbox("New value", codes, format_func=lambda v: dict(et.choices)[v], key=vkey)
        else:
            lo, hi, dflt, step = display_range(et, profile)
            if cur is not None:
                try:
                    dflt = round(to_display(et.unit, float(cur), profile), 6)
                except (TypeError, ValueError):
                    pass
            dflt = 0.0 if dflt is None else dflt
            if lo is not None:
                dflt = max(dflt, lo)
            if hi is not None:
                dflt = min(dflt, hi)
            ul = unit_label(et.unit, profile, et.unit_text)
            kw = {"value": float(dflt), "key": vkey, "format": "%g"}
            if lo is not None:
                kw["min_value"] = float(lo)
            if hi is not None:
                kw["max_value"] = float(hi)
            if step:
                kw["step"] = float(step)
            c3.number_input(f"{et.prop}" + (f" [{ul}]" if ul else ""), **kw)
        c4.date_input("Date", value=date.fromisoformat(default_date), key=kp + "_date")
        c5.text_input("Note (optional)", key=kp + "_note")
        caption = et.help
        if cur is not None and not et.custom and et.value_kind == "number":
            caption += f"  Current model value: {format_value(et, cur, profile)}."
        if caption:
            st.caption(caption)
        st.button("➕ Add event", type="primary", key=kp + "_add", on_click=_cb_add, args=(st, kp, store_key, nodes, edges, profile))
        if msg:
            (st.success if msg[0] == "success" else st.error)(msg[1])

    current = get_events(st, store_key)
    # ---- event list
    st.markdown(f"**Scheduled events ({len(current)})**")
    if not current:
        st.info("No events yet. Choose a type, a target and a date above, then press **Add event**.")
    else:
        frame = events_to_frame(current, nodes, edges, profile)
        ss[kp + "_frame"] = frame
        shown = frame.copy()
        shown["Date"] = pd.to_datetime(shown["Date"], errors="coerce").dt.date
        ekey = f"{kp}_tbl_{ss[kp + '_ver']}"
        cc = st.column_config
        st.data_editor(shown, hide_index=True, use_container_width=True, key=ekey,
                       column_order=["Delete", "Date", "Event", "Target", "Value", "Unit", "Note"],
                       disabled=["No.", "Event", "Target", "Unit", "Target ID", "Field"],
                       column_config={"Delete": cc.CheckboxColumn("Delete", help="Tick to remove this event"),
                                      "Date": cc.DateColumn("Date", format="YYYY-MM-DD"),
                                      "Value": cc.TextColumn("Value", help="In the units shown in the Unit column. Open / Shut in / choice names are accepted for status rows."),
                                      "Note": cc.TextColumn("Note")},
                       on_change=_cb_table, args=(st, kp, store_key, nodes, edges, profile, ekey))
        b1, b2, _ = st.columns([1, 1, 4])
        b1.button("↶ Undo last change", key=kp + "_undo_btn", on_click=_cb_undo, args=(st, kp, store_key), disabled=kp + "_undo" not in ss)
        b2.button("Clear all", key=kp + "_clear", on_click=_cb_clear, args=(st, kp, store_key))
        issues = validate_events(current, nodes, edges, start_date)
        if issues:
            st.warning("**Check the schedule:**\n\n" + "\n".join(f"- {m}" for m in issues))
        with st.expander("Timeline", expanded=False):
            fig = timeline_figure(current, nodes, edges, profile)
            if fig is not None:
                st.plotly_chart(fig, use_container_width=True, key=kp + "_timeline")
            st.markdown(timeline_text(current, nodes, edges, profile))

    # ---- expert / interchange
    with st.expander("Advanced: raw table (target_id / field / value)"):
        st.caption("Legacy expert view. Values here are CANONICAL (bar, m³/d, Sm³/d, m, kW) and are not converted to your unit profile.")
        raw = st.data_editor(events_to_frame(current, nodes, edges, profile, raw=True), num_rows="dynamic", hide_index=True,
                             use_container_width=True, key=f"{kp}_raw_{ss[kp + '_ver']}")
        if st.button("Apply raw table", key=kp + "_raw_apply"):
            try:
                _commit(st, kp, store_key, frame_to_events(raw, nodes, edges, profile))
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    with st.expander("Import / export CSV"):
        d1, d2 = st.columns(2)
        d1.download_button("Download events CSV", events_to_csv(current, nodes, edges, profile), "fieldnet_schedule_events.csv", "text/csv",
                           use_container_width=True, key=kp + "_dl")
        d2.download_button("Download raw CSV (legacy columns)", events_to_csv(current, nodes, edges, profile, raw=True),
                           "fieldnet_schedule_events_raw.csv", "text/csv", use_container_width=True, key=kp + "_dl_raw")
        up = st.file_uploader("Upload events CSV (friendly or legacy format)", type=["csv"], key=f"{kp}_up_{ss[kp + '_ver']}")
        mode = st.radio("Import mode", ["Append to current events", "Replace current events"], horizontal=True, key=kp + "_mode")
        if st.button("Import CSV", key=kp + "_import", disabled=up is None):
            try:
                new = events_from_csv(up.getvalue(), nodes, edges, profile)
                _commit(st, kp, store_key, new if mode.startswith("Replace") else [*current, *new])
                st.rerun()
            except Exception as exc:
                st.error(f"Could not import: {exc}")
    return get_events(st, store_key)
