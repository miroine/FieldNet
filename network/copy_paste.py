"""Copy the settings of one well (or tank) to other wells (tanks). Identity, position, links and the mask are never copied."""
from __future__ import annotations
import copy

NEVER = {'reservoir_id', 'reservoir_alloc', 'communication', 'masked', '_tank_linked', 'group', 'external_table_name'}
WELL_GROUPS = ['Inflow (PI, IPR, skin, Darcy)', 'Tubing, trajectory & lift', 'Fluid (PVT, water cut, GOR)', 'Constraints & availability', 'Other settings']
TANK_GROUPS = ['In-place volumes', 'Pressure & temperature', 'Fluid (PVT)', 'Drive & recovery', 'Other settings']
_WELL_FLUID = {'water_cut', 'initial_water_cut', 'gor_sm3sm3', 'api', 'gas_sg', 'fluid_name', 'pvt'}
_TANK_FLUID = {'fluid_phase', 'fluid_name', 'pvt', 'api', 'gas_sg', 'boi_rm3_sm3', 'rsi_sm3_sm3', 'bubble_point_bar', 'ct_1bar', 'total_compressibility_1bar', 'gor_sm3sm3', 'cgr_sm3_per_msm3', 'z_factor'}


def group_of(kind, key):
    if kind == 'well':
        if key in _WELL_FLUID: return WELL_GROUPS[2]
        if key in {'ipr_model', 'pi_m3d_bar', 'qmax_m3d', 'gas_c_sm3d_bar2n', 'gas_n', 'skin', 'skin_reference_factor', 'productivity_multiplier', 'darcy', 'initial_rate_m3d', 'reservoir_pressure_bar'} or key.startswith('darcy_'): return WELL_GROUPS[0]
        if key in {'depth_m', 'tubing_id_m', 'tubing_roughness_m', 'temperature_c', 'trajectory', 'completion', 'correlation', 'lift_type', 'lift_assist_bar', 'vlp_model'} or key.startswith(('vlp_', 'esp_', 'gas_lift')): return WELL_GROUPS[1]
        if key.startswith(('max_', 'min_')) or key in {'rate_limit_mode', 'availability_factor', 'available', 'constraints', 'uptime'}: return WELL_GROUPS[3]
        return WELL_GROUPS[4]
    if key in _TANK_FLUID: return TANK_GROUPS[2]
    if key in {'stoiip_sm3', 'giip_sm3', 'pore_volume_m3', 'swi'}: return TANK_GROUPS[0]
    if key in {'reservoir_pressure_bar', 'temperature_c', 'min_pressure_bar'}: return TANK_GROUPS[1]
    if key.startswith(('aquifer', 'rf_', 'water_breakthrough', 'gor_rise')) or key in {'gas_cap_m', 'target_rf', 'sweep_efficiency', 'max_water_cut', 'relperm', 'prediction_mode', 'external_table', 'water_cut_mode', 'rf_taper_days'}: return TANK_GROUPS[3]
    return TANK_GROUPS[4]


def groups_for(kind): return list(WELL_GROUPS if kind == 'well' else TANK_GROUPS)


def preview(src, groups):
    """{group: [keys]} that would be copied from ``src``."""
    out = {}
    for k in (src.get('params') or {}):
        if k in NEVER: continue
        g = group_of(src.get('kind'), k)
        if g in groups: out.setdefault(g, []).append(k)
    return out


def copy_params(src, dst, groups):
    """Copy the ``groups`` of settings from node ``src`` to node ``dst`` (same kind). Returns the number of values written."""
    if src is dst or src.get('id') == dst.get('id'): return 0
    if src.get('kind') != dst.get('kind'): raise ValueError('copy between different component types is not supported')
    n = 0; dp = dst.setdefault('params', {})
    for keys in preview(src, groups).values():
        for k in keys: dp[k] = copy.deepcopy(src['params'][k]); n += 1
    if 'Fluid (PVT, water cut, GOR)' in groups or 'Fluid (PVT)' in groups:       # a fluid is copied as a whole: drop a stale correlation PVT block
        if 'pvt' not in src.get('params', {}): dp.pop('pvt', None)
    return n
