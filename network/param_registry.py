"""One table of every user-editable input parameter: label, unit class, valid range, where it applies.

Used by the schedule builder (any listed parameter can be changed by a dated event, in the user's display units) and by the input
consistency checker (``network/input_check.py``: range / unit-sanity checks). Values are CANONICAL units: m, m3/d, Sm3/d, bar, degC,
fraction 0-1. ``unit`` is the display-unit class understood by ``ui.schedule_builder`` ('none' = shown as typed, with ``unit_text``).
"""
from __future__ import annotations
from dataclasses import dataclass

NODE_GROUPS = {'well': ('well',), 'injector': ('water_injector', 'gas_injector', 'injector'), 'tank': ('reservoir',), 'separator': ('separator', 'separator_stage'),
               'boundary': ('sink', 'oil_export', 'gas_export', 'water_disposal', 'water_source', 'gas_source'), 'manifold': ('manifold', 'joint'),
               'valve': ('choke', 'control_valve'), 'pump': ('pump',), 'compressor': ('compressor',)}
EDGE_GROUPS = {'line': ('pipeline',), 'valve': ('choke', 'control_valve'), 'pump': ('pump',), 'compressor': ('compressor',)}
GROUP_LABEL = {'well': 'Well', 'injector': 'Injector', 'tank': 'Tank', 'separator': 'Separator', 'boundary': 'Boundary', 'manifold': 'Manifold / joint',
               'valve': 'Choke / valve', 'pump': 'Pump', 'compressor': 'Compressor', 'line': 'Flowline/Riser'}


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    groups: tuple
    unit: str = 'none'
    unit_text: str | None = None
    min: float | None = None
    max: float | None = None
    default: float | None = None
    step: float | None = None
    kind: str = 'number'             # number | bool | choice
    choices: tuple = ()
    top_level: bool = False          # a node field (e.g. pressure_bar) rather than params.<key>
    warn_min: float | None = None    # outside [warn_min, warn_max] the input checker warns (plausibility, not validity)
    warn_max: float | None = None


def P(key, label, groups, **kw):
    return Param(key, label, tuple(groups), **kw)


CORR = (('Beggs-Brill', 'Beggs-Brill'), ('Homogeneous', 'Homogeneous (no slip)'))
LIFT = (('none', 'Natural flow'), ('gas_lift', 'Gas lift'), ('esp', 'ESP'))
PARAMS: list[Param] = [
    # ---- well: inflow
    P('productivity_multiplier', 'productivity multiplier', ['well'], min=0.001, max=1000.0, default=1.0, step=0.05, warn_min=0.05, warn_max=20.0),
    P('pi_m3d_bar', 'productivity index (PI)', ['well'], unit='pi', min=0.0, max=1e5, default=10.0, step=1.0, warn_max=2000.0),
    P('qmax_m3d', 'Vogel qmax', ['well'], unit='liquid_rate', min=0.0, max=1e7, default=1500.0, step=50.0),
    P('gas_c_sm3d_bar2n', 'gas back-pressure C', ['well'], unit_text='Sm³/d/bar²ⁿ', min=0.0, max=1e7, default=50.0, step=5.0),
    P('gas_n', 'gas back-pressure exponent n', ['well'], min=0.5, max=1.0, default=1.0, step=0.05),
    P('skin', 'skin', ['well', 'injector'], unit_text='skin', min=-7.0, max=200.0, default=0.0, step=0.5, warn_min=-6.0, warn_max=50.0),
    P('darcy_perm_md', 'Darcy: permeability kh', ['well'], unit_text='mD', min=0.001, max=1e5, default=100.0, step=10.0, warn_max=20000.0),
    P('darcy_kv_kh', 'Darcy: kv/kh anisotropy', ['well'], min=0.0001, max=1.0, default=0.5, step=0.05),
    P('darcy_net_pay_m', 'Darcy: net pay thickness', ['well'], unit='length', min=0.1, max=2000.0, default=30.0, step=1.0),
    P('darcy_drainage_radius_m', 'Darcy: drainage radius', ['well'], unit='length', min=1.0, max=20000.0, default=500.0, step=10.0),
    P('darcy_wellbore_radius_m', 'Darcy: wellbore radius', ['well'], unit='length', min=0.01, max=1.0, default=0.108, step=0.01, warn_min=0.03, warn_max=0.5),
    P('darcy_lateral_length_m', 'Darcy: horizontal lateral length', ['well'], unit='length', min=10.0, max=10000.0, default=1000.0, step=50.0),
    P('darcy_inclination_deg', 'Darcy: inclination through the pay', ['well'], unit_text='deg', min=0.0, max=89.0, default=0.0, step=5.0),
    P('darcy_visc_cp', 'Darcy: oil viscosity', ['well'], unit_text='cP', min=0.01, max=1e4, default=1.0, step=0.1),
    P('darcy_bo', 'Darcy: oil formation volume factor', ['well'], unit_text='rm³/Sm³', min=0.5, max=5.0, default=1.2, step=0.05),
    P('darcy_orientation', 'Darcy: well geometry', ['well'], kind='choice', choices=(('vertical', 'Vertical'), ('deviated', 'Deviated'), ('horizontal', 'Horizontal'))),
    P('darcy', 'inflow from Darcy properties (on/off)', ['well'], kind='bool'),
    P('ipr_model', 'IPR model', ['well'], kind='choice', choices=(('PI', 'Productivity index'), ('Vogel', 'Vogel'), ('Gas', 'Gas back-pressure'))),
    # ---- well: fluid and tubing
    P('water_cut', 'water cut', ['well', 'line'], unit='fraction', min=0.0, max=0.9999, default=0.2, step=0.05),
    P('gor_sm3sm3', 'producing GOR', ['well', 'line'], unit='gor', min=0.0, max=1e6, default=100.0, step=10.0, warn_max=1e6),
    P('api', 'oil gravity', ['well', 'line'], unit_text='°API', min=5.0, max=70.0, default=35.0, step=1.0, warn_min=10.0, warn_max=60.0),
    P('gas_sg', 'gas specific gravity', ['well', 'line'], min=0.55, max=1.5, default=0.75, step=0.01),
    P('tubing_id_m', 'tubing inner diameter', ['well'], unit='diameter', min=0.01, max=0.5, default=0.0889, step=0.005, warn_min=0.03, warn_max=0.25),
    P('depth_m', 'true vertical depth', ['well', 'injector'], unit='length', min=1.0, max=12000.0, default=2000.0, step=50.0),
    P('temperature_c', 'temperature', ['well'], unit='temperature', min=-20.0, max=300.0, default=70.0, step=1.0),
    P('vlp_model', 'tubing VLP correlation', ['well'], kind='choice', choices=CORR),
    P('vlp_dp_multiplier', 'VLP pressure-drop multiplier', ['well'], min=0.2, max=5.0, default=1.0, step=0.05),
    P('lift_type', 'lift method', ['well'], kind='choice', choices=LIFT),
    P('lift_assist_bar', 'manual lift assistance', ['well'], unit='pressure', min=0.0, max=150.0, default=0.0, step=1.0),
    P('esp_rated_rate_m3d', 'ESP rated liquid rate', ['well'], unit='liquid_rate', min=1.0, max=1e7, default=1000.0, step=50.0),
    P('esp_shutoff_head_bar', 'ESP shut-off head', ['well'], unit='pressure', min=1.0, max=500.0, default=80.0, step=5.0),
    P('gas_lift_depth_m', 'gas-lift injection depth', ['well'], unit='length', min=0.0, max=12000.0, default=1500.0, step=50.0),
    P('min_liquid_rate_m3d', 'minimum liquid rate', ['well'], unit='liquid_rate', min=0.0, max=1e6, default=0.0, step=10.0),
    P('min_oil_rate_m3d', 'minimum oil rate', ['well'], unit='liquid_rate', min=0.0, max=1e6, default=0.0, step=10.0),
    P('min_water_rate_m3d', 'minimum water rate', ['well'], unit='liquid_rate', min=0.0, max=1e6, default=0.0, step=10.0),
    P('min_gas_rate_sm3d', 'minimum gas rate', ['well'], unit='gas_rate', min=0.0, max=1e9, default=0.0, step=1e4),
    P('rate_limit_mode', 'rate-limit mode', ['well'], kind='choice', choices=(('enforce', 'Enforce (cap the rate)'), ('report', 'Report only'))),
    P('max_whp_bar', 'maximum wellhead pressure', ['well', 'injector'], unit='pressure', min=0.0, max=1500.0, default=300.0, step=5.0),
    P('min_whp_bar', 'minimum wellhead pressure', ['well'], unit='pressure', min=0.0, max=1500.0, default=10.0, step=1.0),
    # ---- injector
    P('injectivity_m3d_bar', 'injectivity index', ['injector'], unit='pi', min=0.0, max=1e5, default=10.0, step=1.0),
    P('max_rate_m3d', 'maximum injection rate', ['injector'], unit='liquid_rate', min=0.0, max=2e5, default=3000.0, step=100.0),
    P('available', 'in service (open / shut in)', ['well', 'injector'], kind='bool'),
    # ---- tank (parameters that can change over the life without invalidating the balance)
    P('aquifer_pi_m3d_bar', 'aquifer productivity', ['tank'], unit='pi', min=0.0, max=1e5, default=150.0, step=10.0),
    P('min_pressure_bar', 'abandonment pressure', ['tank'], unit='pressure', min=0.0, max=1500.0, default=20.0, step=1.0),
    P('target_rf', 'target recovery factor', ['tank'], unit='fraction', min=0.0, max=1.0, default=0.4, step=0.01),
    P('rf_taper_days', 'recovery-factor taper time', ['tank'], unit_text='days', min=1.0, max=36500.0, default=730.0, step=30.0),
    P('sweep_efficiency', 'sweep efficiency', ['tank'], unit='fraction', min=0.05, max=1.0, default=0.7, step=0.05),
    P('water_breakthrough_rf', 'water breakthrough at recovery factor', ['tank'], unit='fraction', min=0.0, max=0.9, default=0.05, step=0.01),
    P('rf_at_max_water_cut', 'recovery factor at maximum water cut', ['tank'], unit='fraction', min=0.01, max=0.95, default=0.4, step=0.01),
    P('max_water_cut', 'maximum water cut', ['tank'], unit='fraction', min=0.0, max=0.99, default=0.9, step=0.01),
    P('gor_rise_factor', 'GOR rise factor below bubble point', ['tank'], min=0.0, max=50.0, default=3.0, step=0.5),
    P('gas_cap_m', 'gas-cap size m', ['tank'], min=0.0, max=10.0, default=0.5, step=0.05),
    # ---- separators / boundaries / manifolds
    P('max_pressure_bar', 'maximum pressure', ['separator', 'boundary', 'manifold', 'line'], unit='pressure', min=0.0, max=1500.0, default=100.0, step=5.0),
    P('min_pressure_bar', 'minimum pressure', ['separator', 'boundary', 'manifold'], unit='pressure', min=0.0, max=1500.0, default=10.0, step=1.0),
    P('pressure_bar', 'boundary pressure', ['boundary'], unit='pressure', min=0.1, max=1500.0, default=35.0, step=1.0, top_level=True),
    P('max_liquid_rate_m3d', 'liquid capacity', ['boundary', 'well'], unit='liquid_rate', min=0.0, max=1e6, default=4500.0, step=100.0),
    P('max_oil_rate_m3d', 'oil capacity', ['boundary'], unit='liquid_rate', min=0.0, max=1e6, default=5000.0, step=100.0),
    P('max_water_rate_m3d', 'water capacity', ['boundary'], unit='liquid_rate', min=0.0, max=1e6, default=5000.0, step=100.0),
    P('max_gas_rate_sm3d', 'gas capacity', ['boundary'], unit='gas_rate', min=0.0, max=1e9, default=1e6, step=5e4),
    P('separator_type', 'separator type', ['separator'], kind='choice', choices=(('two_phase', 'Two-phase'), ('three_phase', 'Three-phase'), ('test', 'Test'), ('scrubber', 'Scrubber'), ('water_treatment', 'Water treatment'))),
    # ---- flowline
    P('length_m', 'length', ['line'], unit='length', min=0.0, max=1e7, default=1000.0, step=100.0, top_level=True),
    P('roughness_m', 'roughness', ['line'], min=0.0, max=0.01, default=4.5e-5, step=1e-5, top_level=True),
    P('elevation_change_m', 'elevation change', ['line'], unit='length', min=-5000.0, max=5000.0, default=0.0, step=10.0, top_level=True),
    P('temperature_c', 'inlet temperature', ['line'], unit='temperature', min=-20.0, max=300.0, default=50.0, step=1.0),
    P('ambient_temperature_c', 'ambient temperature', ['line'], unit='temperature', min=-50.0, max=100.0, default=4.0, step=1.0),
    P('overall_u_w_m2k', 'overall heat-transfer coefficient U', ['line'], unit_text='W/m²/K', min=0.0, max=500.0, default=5.0, step=0.5),
    P('erosion_c_factor', 'API-14E erosion C-factor', ['line'], min=1.0, max=500.0, default=100.0, step=5.0),
    P('availability_factor', 'availability factor (uptime)', ['well', 'injector', 'separator', 'line', 'pump', 'compressor', 'valve', 'manifold'], unit='fraction', min=0.0, max=1.0, default=0.95, step=0.01),
    # ---- compressor extras
    P('efficiency', 'efficiency', ['pump', 'compressor'], unit='fraction', min=0.1, max=1.0, default=0.75, step=0.01),
    P('rated_rate_m3d', 'rated rate', ['pump'], unit='liquid_rate', min=1.0, max=1e7, default=1500.0, step=50.0),
    P('shutoff_head_bar', 'shut-off head', ['pump'], unit='pressure', min=0.0, max=500.0, default=35.0, step=1.0),
    P('rated_gas_rate_sm3d', 'rated gas rate', ['compressor'], unit='gas_rate', min=1.0, max=1e9, default=150000.0, step=1e4),
]
BY_KEY: dict[str, list[Param]] = {}
for _p in PARAMS: BY_KEY.setdefault(_p.key, []).append(_p)


def group_of_element(el, is_edge=False):
    """Registry group names that apply to a node / edge dict."""
    kind = str(el.get('kind'))
    table = EDGE_GROUPS if is_edge else NODE_GROUPS
    return [g for g, kinds in table.items() if kind in kinds]


def params_for(el, is_edge=False):
    gs = set(group_of_element(el, is_edge))
    return [p for p in PARAMS if gs & set(p.groups)]


def lookup(key, el, is_edge=False):
    """The registry entry for ``key`` that applies to this element (or None)."""
    gs = set(group_of_element(el, is_edge))
    for p in BY_KEY.get(key, ()):
        if gs & set(p.groups): return p
    return None
