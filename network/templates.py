"""Template and example library: ready-made development concepts that load into the editor and run.

Every builder returns ``(nodes, edges)`` in the same format as ``network.examples``; ``TEMPLATES`` describes them (category, what it shows,
key assumptions, suggested forecast). All numbers are *illustrative round numbers for a screening model*, not data of any real field -
replace tanks, PI / C, depths, line lengths and capacities with your own before drawing conclusions.

Every template is tested: it must solve (converged) and run a short forecast (``tests/test_templates.py``)."""
from __future__ import annotations
import copy
from ui.topology import auto_layout

PIPE_OIL = {'temperature_c': 55.0, 'water_cut': 0.1, 'gor_sm3sm3': 110.0, 'api': 34.0, 'gas_sg': 0.72, 'correlation': 'Beggs-Brill', 'initial_rate_m3d': 800.0}
PIPE_GAS = {'temperature_c': 25.0, 'water_cut': 0.0, 'gor_sm3sm3': 5e5, 'api': 50.0, 'gas_sg': 0.68, 'correlation': 'Beggs-Brill', 'initial_rate_m3d': 100.0}


# ----------------------------------------------------------------------------- building blocks
def node(i, kind, name, params=None, pressure=None): return {'id': i, 'kind': kind, 'name': name, 'pressure_bar': pressure, 'x': 0, 'y': 0, 'params': params or {}}
def pipe(i, s, t, length, d, dz=0.0, **prm): return {'id': i, 'source': s, 'target': t, 'kind': 'pipeline', 'length_m': float(length), 'diameter_m': float(d), 'roughness_m': 4.5e-5, 'elevation_change_m': float(dz), 'params': dict(prm)}
def manifold(i, name): return node(i, 'manifold', name)
def separator(i, name, p, **lim): return node(i, 'separator', name, lim, p)


def oil_tank(i, name, pr=290.0, t=90.0, stoiip=30e6, boi=1.3, rsi=110.0, pb=160.0, aquifer=0.0, **kw):
    p = {'fluid_phase': 'oil', 'reservoir_pressure_bar': pr, 'temperature_c': t, 'stoiip_sm3': stoiip, 'boi_rm3_sm3': boi, 'rsi_sm3_sm3': rsi, 'bubble_point_bar': pb, 'swi': 0.2, 'ct_1bar': 1.5e-4,
         'aquifer_pi_m3d_bar': aquifer, 'min_pressure_bar': 40.0, 'water_breakthrough_rf': 0.06, 'max_water_cut': 0.9, 'rf_at_max_water_cut': 0.40, 'gor_rise_factor': 3.0}; p.update(kw)
    return node(i, 'reservoir', name, p)


def gas_tank(i, name, pr=350.0, t=100.0, giip=100e9, phase='gas', **kw):
    p = {'fluid_phase': phase, 'reservoir_pressure_bar': pr, 'temperature_c': t, 'giip_sm3': giip, 'gas_sg': 0.68, 'swi': 0.2, 'min_pressure_bar': 40.0, 'aquifer_pi_m3d_bar': 0.0}
    if phase == 'gas_condensate': p['cgr_sm3_per_msm3'] = 60.0
    p.update(kw); return node(i, 'reservoir', name, p)


def oil_well(i, name, tank, **kw):
    p = {'reservoir_id': tank, 'ipr_model': 'PI', 'pi_m3d_bar': 14.0, 'depth_m': 2400.0, 'tubing_id_m': 0.1, 'tubing_roughness_m': 4.5e-5, 'temperature_c': 80.0, 'water_cut': 0.05, 'gor_sm3sm3': 110.0, 'api': 34.0,
         'gas_sg': 0.72, 'vlp_model': 'Beggs-Brill', 'available': True}; p.update(kw); return node(i, 'well', name, p)


def gas_well(i, name, tank, **kw):
    p = {'reservoir_id': tank, 'ipr_model': 'Gas', 'gas_c_sm3d_bar2n': 120.0, 'gas_n': 1.0, 'depth_m': 3500.0, 'tubing_id_m': 0.1143, 'tubing_roughness_m': 4.5e-5, 'temperature_c': 60.0, 'water_cut': 0.0,
         'gor_sm3sm3': 1e5, 'api': 50.0, 'gas_sg': 0.68, 'vlp_model': 'Beggs-Brill', 'available': True}; p.update(kw); return node(i, 'well', name, p)


def water_injector(i, name, tank, **kw):
    p = {'reservoir_id': tank, 'injection_fluid': 'water', 'injectivity_m3d_bar': 40.0, 'depth_m': 2400.0, 'max_rate_m3d': 3500.0, 'available': True}; p.update(kw); return node(i, 'water_injector', name, p)


def gas_injector(i, name, tank, **kw):
    p = {'reservoir_id': tank, 'injection_fluid': 'gas', 'injectivity_m3d_bar': 30.0, 'depth_m': 2400.0, 'max_rate_m3d': 2.0e6, 'available': True}; p.update(kw); return node(i, 'gas_injector', name, p)


def pump(i, s, t, shutoff=120.0, rated=6000.0, eff=0.75): return {'id': i, 'source': s, 'target': t, 'kind': 'pump', 'length_m': 0.0, 'diameter_m': 0.2, 'roughness_m': 4.5e-5, 'elevation_change_m': 0.0,
                                                                  'params': {'shutoff_head_bar': shutoff, 'rated_rate_m3d': rated, 'efficiency': eff}}


def compressor(i, s, t, ratio=2.5, gor=1e5, max_discharge=250.0, eff=0.75): return {'id': i, 'source': s, 'target': t, 'kind': 'compressor', 'length_m': 0.0, 'diameter_m': 0.3, 'roughness_m': 4.5e-5, 'elevation_change_m': 0.0,
                                                                                    'params': {'pressure_ratio': ratio, 'gor_sm3sm3': gor, 'max_discharge_bar': max_discharge, 'efficiency': eff}}


def _finish(nodes, edges):
    auto_layout(nodes, edges); return nodes, edges


# ----------------------------------------------------------------------------- templates
def hpht_4slot_gas():
    """4-slot subsea template on an HPHT gas field, one export flowline to a host platform."""
    hp = {'pvt': {'model': 'correlation', 'co2': 0.03, 'h2s': 0.0, 'n2': 0.01, 'z_corr': 'dak'}, 'bottomhole_temperature_c': 170.0}
    n = [gas_tank('G1', 'HPHT-GAS', pr=750.0, t=170.0, giip=150e9, min_pressure_bar=120.0)]
    n += [gas_well(f'W{i}', f'SLOT-{i}', 'G1', depth_m=4800.0, gas_c_sm3d_bar2n=120.0, reservoir_pressure_bar=750.0, **hp) for i in range(1, 5)]
    n += [manifold('TPL', '4-SLOT TEMPLATE'), separator('HOST', 'HOST PLATFORM', 80.0, max_gas_rate_sm3d=26e6)]
    e = [pipe(f'JMP{i}', f'W{i}', 'TPL', 150, 0.3, 0, **PIPE_GAS) for i in range(1, 5)] + [pipe('EXPORT', 'TPL', 'HOST', 25000, 0.6, 80, **PIPE_GAS)]
    return _finish(n, e)


def daisy_chain_oil():
    """Three subsea templates in series (daisy chain): one trunk line to the host, each template adds its wells to the same line."""
    n = [oil_tank('T1', 'DAISY-RES', stoiip=45e6, aquifer=250.0)]
    names = ['A', 'B', 'C']
    for k, c in enumerate(names):
        n += [oil_well(f'{c}1', f'{c}-1', 'T1', pi_m3d_bar=14.0 - 2 * k), oil_well(f'{c}2', f'{c}-2', 'T1', pi_m3d_bar=12.0 - 2 * k, water_cut=0.08)]
        n.append(manifold(f'M{c}', f'TEMPLATE {c}'))
    n.append(separator('HOST', 'HOST FPSO', 20.0, max_liquid_rate_m3d=9000.0, max_gas_rate_sm3d=2.0e6))
    n.append(water_injector('I1', 'INJ-1', 'T1')); n.append(node('WS', 'water_source', 'SEAWATER', {}, 5.0)); 
    e = []
    for c in names: e += [pipe(f'J{c}1', f'{c}1', f'M{c}', 400, 0.12, 0, **PIPE_OIL), pipe(f'J{c}2', f'{c}2', f'M{c}', 400, 0.12, 0, **PIPE_OIL)]
    e += [pipe('DC-CB', 'MC', 'MB', 4500, 0.2, 0, **PIPE_OIL), pipe('DC-BA', 'MB', 'MA', 4500, 0.25, 0, **PIPE_OIL), pipe('TRUNK', 'MA', 'HOST', 12000, 0.3, 120, **PIPE_OIL), pump('WIP', 'WS', 'I1', 260.0, 8000.0)]
    return _finish(n, e)


def multi_tank_commingled():
    """Three stacked, communicating tanks (sands). Wells produce each tank; two dual-zone wells commingle two tanks at one wellhead (modelled as two zone wells joined at a joint)."""
    n = [oil_tank('TU', 'UPPER SAND', pr=280.0, stoiip=12e6, aquifer=60.0), oil_tank('TM', 'MIDDLE SAND', pr=300.0, stoiip=18e6, aquifer=40.0), oil_tank('TL', 'LOWER SAND', pr=320.0, stoiip=25e6, aquifer=200.0)]
    n[0]['params']['communication'] = [{'to': 'TM', 'transmissibility_m3d_bar': 80.0, 'max_transfer_m3d': None}]
    n[1]['params']['communication'] = [{'to': 'TL', 'transmissibility_m3d_bar': 40.0, 'max_transfer_m3d': None}]
    n += [oil_well('U1', 'U-1', 'TU', pi_m3d_bar=12.0), oil_well('M1', 'M-1', 'TM', pi_m3d_bar=14.0), oil_well('L1', 'L-1', 'TL', pi_m3d_bar=16.0), oil_well('L2', 'L-2', 'TL', pi_m3d_bar=16.0)]
    for c, (a, b) in {'D1': ('TU', 'TM'), 'D2': ('TM', 'TL')}.items():
        n += [oil_well(f'{c}U', f'{c} zone 1', a, pi_m3d_bar=7.0, tubing_id_m=0.1), oil_well(f'{c}L', f'{c} zone 2', b, pi_m3d_bar=7.0, tubing_id_m=0.1), node(f'{c}J', 'joint', f'{c} wellhead')]
    n += [manifold('MAN', 'MANIFOLD'), separator('SEP', 'SEPARATOR', 20.0, max_liquid_rate_m3d=6000.0)]
    e = [pipe(f'F-{w}', w, 'MAN', 2500, 0.154, 0, **PIPE_OIL) for w in ('U1', 'M1', 'L1', 'L2')]
    for c in ('D1', 'D2'): e += [pipe(f'{c}-ZU', f'{c}U', f'{c}J', 100, 0.1, 0, **PIPE_OIL), pipe(f'{c}-ZL', f'{c}L', f'{c}J', 100, 0.1, 0, **PIPE_OIL), pipe(f'F-{c}', f'{c}J', 'MAN', 2500, 0.154, 0, **PIPE_OIL)]
    e.append(pipe('TRUNK', 'MAN', 'SEP', 8000, 0.3, 20, **PIPE_OIL))
    return _finish(n, e)


def oil_gas_injection():
    """Oil field with gas injection for pressure support: gas source -> compressor -> two gas injectors."""
    n = [oil_tank('T1', 'GAS-INJ-RES', pr=300.0, stoiip=40e6, rsi=130.0, pb=220.0, aquifer=0.0)]
    n += [oil_well(f'P{i}', f'PROD-{i}', 'T1', pi_m3d_bar=13.0, gor_sm3sm3=130.0) for i in range(1, 5)]
    n += [manifold('M1', 'MANIFOLD'), separator('SEP', 'SEPARATOR', 20.0, max_liquid_rate_m3d=7000.0, max_gas_rate_sm3d=2.5e6)]
    n += [node('GS', 'gas_source', 'LP GAS HEADER', {}, 40.0), node('MI', 'manifold', 'INJ MANIFOLD'), gas_injector('GI1', 'GAS-INJ-1', 'T1'), gas_injector('GI2', 'GAS-INJ-2', 'T1')]
    e = [pipe(f'FL{i}', f'P{i}', 'M1', 2000 + 300 * i, 0.154, 0, **PIPE_OIL) for i in range(1, 5)] + [pipe('TRUNK', 'M1', 'SEP', 7000, 0.3, 20, **PIPE_OIL)]
    e += [compressor('INJ-COMP', 'GS', 'MI', ratio=6.0, gor=1.0, max_discharge=300.0), pipe('GI-A', 'MI', 'GI1', 3000, 0.15, 0, **dict(PIPE_GAS, gor_sm3sm3=1.0)), pipe('GI-B', 'MI', 'GI2', 3500, 0.15, 0, **dict(PIPE_GAS, gor_sm3sm3=1.0))]
    return _finish(n, e)


def pure_depletion_oil():
    """Undersaturated oil with no aquifer and no injection: pressure falls below the bubble point, GOR rises, plateau then decline."""
    n = [oil_tank('T1', 'DEPLETION-RES', pr=260.0, stoiip=25e6, pb=190.0, aquifer=0.0, rf_at_max_water_cut=0.35, max_water_cut=0.5)]
    n += [oil_well(f'P{i}', f'PROD-{i}', 'T1', pi_m3d_bar=15.0 - i, water_cut=0.02) for i in range(1, 6)]
    n += [manifold('M1', 'MANIFOLD'), separator('SEP', 'SEPARATOR', 25.0, max_liquid_rate_m3d=5000.0, max_gas_rate_sm3d=2.0e6)]
    e = [pipe(f'FL{i}', f'P{i}', 'M1', 1500 + 250 * i, 0.154, 0, **PIPE_OIL) for i in range(1, 6)] + [pipe('TRUNK', 'M1', 'SEP', 6000, 0.3, 10, **PIPE_OIL)]
    return _finish(n, e)


def wellhead_platform_tieback():
    """Wellhead platform A (6 wells) tied back through a 14 km line to host platform B, which has its own wells and the only separator."""
    n = [oil_tank('TA', 'FIELD A', pr=280.0, stoiip=28e6, aquifer=120.0), oil_tank('TB', 'FIELD B', pr=300.0, stoiip=35e6, aquifer=180.0)]
    n += [oil_well(f'A{i}', f'A-{i}', 'TA', pi_m3d_bar=10.0 + i) for i in range(1, 7)] + [oil_well(f'B{i}', f'B-{i}', 'TB', pi_m3d_bar=14.0) for i in range(1, 5)]
    n += [manifold('MA', 'WHP-A MANIFOLD'), manifold('MB', 'HOST-B MANIFOLD'), separator('SEP', 'HOST-B SEPARATOR', 18.0, max_liquid_rate_m3d=12000.0, max_gas_rate_sm3d=3.0e6)]
    e = [pipe(f'FA{i}', f'A{i}', 'MA', 600, 0.154, 0, **PIPE_OIL) for i in range(1, 7)] + [pipe(f'FB{i}', f'B{i}', 'MB', 800, 0.154, 0, **PIPE_OIL) for i in range(1, 5)]
    e += [pipe('TIEBACK', 'MA', 'MB', 14000, 0.3, -40, **PIPE_OIL), pipe('HOSTLINE', 'MB', 'SEP', 300, 0.35, 30, **PIPE_OIL)]
    return _finish(n, e)


def subsea_booster_pump():
    """Subsea multiphase booster pump on a long tieback of a low-pressure oil field (remove the pump edge to see the wells choke back)."""
    n = [oil_tank('T1', 'LOW-PRESSURE-RES', pr=260.0, stoiip=30e6, rsi=60.0, pb=90.0, aquifer=100.0, boi=1.15, min_pressure_bar=50.0)]
    n += [oil_well(f'P{i}', f'SUBSEA-{i}', 'T1', pi_m3d_bar=14.0, gor_sm3sm3=60.0, depth_m=2200.0, water_cut=0.15, api=26.0) for i in range(1, 5)]
    n += [manifold('M1', 'SUBSEA MANIFOLD'), manifold('M2', 'PUMP DISCHARGE'), separator('HOST', 'HOST FPSO', 20.0, max_liquid_rate_m3d=6000.0)]
    pf = dict(PIPE_OIL, gor_sm3sm3=60.0, api=26.0)
    e = [pipe(f'FL{i}', f'P{i}', 'M1', 1500, 0.154, 0, **pf) for i in range(1, 5)] + [pump('BOOSTER', 'M1', 'M2', 40.0, 5500.0, 0.6), pipe('TIEBACK', 'M2', 'HOST', 18000, 0.254, 100, **pf)]
    return _finish(n, e)


def subsea_compressor_gas():
    """Subsea wet-gas compressor on a long gas tieback: boosts the manifold pressure so the field produces below the economic limit of natural flow."""
    n = [gas_tank('G1', 'GAS-FIELD', pr=300.0, t=110.0, giip=90e9, min_pressure_bar=30.0)]
    n += [gas_well(f'W{i}', f'SUBSEA-{i}', 'G1', depth_m=3200.0, gas_c_sm3d_bar2n=70.0, reservoir_pressure_bar=300.0) for i in range(1, 5)]
    n += [manifold('M1', 'SUBSEA MANIFOLD'), manifold('M2', 'COMPRESSOR DISCHARGE'), separator('HOST', 'ONSHORE PLANT', 70.0, max_gas_rate_sm3d=14e6)]
    e = [pipe(f'FL{i}', f'W{i}', 'M1', 1000, 0.15, 0, **PIPE_GAS) for i in range(1, 5)] + [compressor('SUBSEA-COMP', 'M1', 'M2', ratio=2.2, gor=5e5, max_discharge=140.0), pipe('TIEBACK', 'M2', 'HOST', 60000, 0.4, 0, **PIPE_GAS)]
    return _finish(n, e)


def topside_compressor_gas():
    """Gas wells produce into a low-pressure inlet header; a topside compressor lifts the gas to the export pipeline pressure (late-life compression)."""
    n = [gas_tank('G1', 'GAS-FIELD', pr=220.0, t=95.0, giip=70e9, min_pressure_bar=15.0)]
    n += [gas_well(f'W{i}', f'WELL-{i}', 'G1', depth_m=3000.0, gas_c_sm3d_bar2n=60.0, reservoir_pressure_bar=220.0) for i in range(1, 6)]
    n += [manifold('M1', 'INLET MANIFOLD'), manifold('M2', 'COMPRESSOR DISCHARGE'), separator('EXP', 'EXPORT METERING (pipeline pressure)', 90.0, max_gas_rate_sm3d=12e6)]
    e = [pipe(f'FL{i}', f'W{i}', 'M1', 2500, 0.15, 0, **PIPE_GAS) for i in range(1, 6)] + [compressor('EXP-COMP', 'M1', 'M2', ratio=3.0, gor=5e5, max_discharge=130.0), pipe('EXPORT', 'M2', 'EXP', 3000, 0.3, 0, **PIPE_GAS)]
    return _finish(n, e)


def horizontal_wells():
    """Four horizontal oil producers: vertical section, build to 90 degrees and a 1500 m lateral, larger liner in the horizontal section (Vogel inflow)."""
    traj = [{'md_m': 0.0, 'inc_deg': 0.0}, {'md_m': 1800.0, 'inc_deg': 0.0}, {'md_m': 2300.0, 'inc_deg': 45.0}, {'md_m': 2700.0, 'inc_deg': 90.0}, {'md_m': 4200.0, 'inc_deg': 90.0}]
    comp = [{'from_md_m': 0.0, 'to_md_m': 2700.0, 'id_m': 0.1}, {'from_md_m': 2700.0, 'to_md_m': 4200.0, 'id_m': 0.1143}]
    n = [oil_tank('T1', 'THIN-OIL-COLUMN', pr=250.0, stoiip=22e6, aquifer=300.0, pb=140.0)]
    n += [oil_well(f'H{i}', f'HZ-{i}', 'T1', ipr_model='Vogel', qmax_m3d=3200.0, pi_m3d_bar=0.0, trajectory=copy.deepcopy(traj), completion=copy.deepcopy(comp), depth_m=2000.0, skin=1.0) for i in range(1, 5)]
    n += [manifold('M1', 'MANIFOLD'), separator('SEP', 'SEPARATOR', 20.0, max_liquid_rate_m3d=8000.0)]
    e = [pipe(f'FL{i}', f'H{i}', 'M1', 1800, 0.154, 0, **PIPE_OIL) for i in range(1, 5)] + [pipe('TRUNK', 'M1', 'SEP', 5000, 0.3, 10, **PIPE_OIL)]
    return _finish(n, e)


def hpht_tight_gas_frac():
    """HPHT tight gas developed with hydraulically fractured horizontal wells: four drainage compartments (tanks), two fracture-stimulated wells each. The fracture is represented by a strong negative skin and a high deliverability coefficient - there is no transient fracture model."""
    pvt = {'model': 'correlation', 'co2': 0.04, 'h2s': 0.0, 'n2': 0.01, 'z_corr': 'dak'}
    n = []
    for c in range(1, 5):
        n.append(gas_tank(f'C{c}', f'COMPARTMENT {c}', pr=780.0, t=175.0, giip=9e9, min_pressure_bar=100.0, aquifer_pi_m3d_bar=0.0))
        n[-1]['params']['communication'] = [{'to': f'C{c + 1}', 'transmissibility_m3d_bar': 2.0, 'max_transfer_m3d': None}] if c < 4 else []
        for k in (1, 2): n.append(gas_well(f'F{c}{k}', f'FRAC-{c}{k}', f'C{c}', depth_m=4600.0, gas_c_sm3d_bar2n=14.0, gas_n=0.9, skin=-4.5, tubing_id_m=0.1016, reservoir_pressure_bar=780.0, pvt=dict(pvt),
                                           bottomhole_temperature_c=175.0, frac_half_length_m=120.0, frac_stages=12))
    n += [manifold('M1', 'WELL PAD MANIFOLD'), separator('SEP', 'PLANT', 70.0, max_gas_rate_sm3d=16e6)]
    e = [pipe(f'FL{w["id"]}', w['id'], 'M1', 1500, 0.15, 0, **PIPE_GAS) for w in n if w['kind'] == 'well'] + [pipe('TRUNK', 'M1', 'SEP', 12000, 0.4, 0, **PIPE_GAS)]
    return _finish(n, e)


def gas_lift_field():
    """Oil field on gas lift: lift gas is a well parameter (injection rate per well) and the gas lift curve shows the optimum."""
    n = [oil_tank('T1', 'GAS-LIFT-RES', pr=230.0, stoiip=26e6, pb=150.0, aquifer=80.0)]
    n += [oil_well(f'P{i}', f'GL-{i}', 'T1', pi_m3d_bar=11.0, water_cut=0.35, lift_type='gas_lift', gas_lift_injection_sm3d=60000.0, depth_m=2600.0) for i in range(1, 6)]
    n += [manifold('M1', 'MANIFOLD'), separator('SEP', 'SEPARATOR', 22.0, max_liquid_rate_m3d=6500.0, max_gas_rate_sm3d=3.0e6)]
    e = [pipe(f'FL{i}', f'P{i}', 'M1', 2000, 0.154, 0, **dict(PIPE_OIL, water_cut=0.35)) for i in range(1, 6)] + [pipe('TRUNK', 'M1', 'SEP', 6000, 0.3, 10, **dict(PIPE_OIL, water_cut=0.35))]
    return _finish(n, e)


def esp_field():
    """Heavier oil produced with electric submersible pumps."""
    n = [oil_tank('T1', 'HEAVY-OIL-RES', pr=190.0, stoiip=20e6, rsi=40.0, pb=70.0, boi=1.1, aquifer=60.0)]
    n += [oil_well(f'P{i}', f'ESP-{i}', 'T1', pi_m3d_bar=9.0, api=22.0, gor_sm3sm3=40.0, water_cut=0.25, lift_type='ESP', esp_rated_rate_m3d=1200.0, esp_shutoff_head_bar=110.0, depth_m=1800.0) for i in range(1, 5)]
    n += [manifold('M1', 'MANIFOLD'), separator('SEP', 'SEPARATOR', 12.0, max_liquid_rate_m3d=4500.0)]
    pf = dict(PIPE_OIL, api=22.0, gor_sm3sm3=40.0, water_cut=0.25)
    e = [pipe(f'FL{i}', f'P{i}', 'M1', 1800, 0.154, 0, **pf) for i in range(1, 5)] + [pipe('TRUNK', 'M1', 'SEP', 5000, 0.254, 10, **pf)]
    return _finish(n, e)


def waterflood_pattern():
    """Waterflood: five producers and three water injectors supplied by a seawater pump; injection replaces the voidage."""
    n = [oil_tank('T1', 'WATERFLOOD-RES', pr=270.0, stoiip=38e6, aquifer=0.0, rf_at_max_water_cut=0.45)]
    n += [oil_well(f'P{i}', f'PROD-{i}', 'T1', pi_m3d_bar=12.0) for i in range(1, 6)] + [water_injector(f'I{i}', f'INJ-{i}', 'T1', injectivity_m3d_bar=45.0, max_rate_m3d=3000.0) for i in range(1, 4)]
    n += [manifold('M1', 'MANIFOLD'), manifold('MI', 'INJ MANIFOLD'), separator('SEP', 'SEPARATOR', 20.0, max_liquid_rate_m3d=7000.0), node('WS', 'water_source', 'SEAWATER', {}, 5.0)]
    e = [pipe(f'FL{i}', f'P{i}', 'M1', 2200, 0.154, 0, **PIPE_OIL) for i in range(1, 6)] + [pipe('TRUNK', 'M1', 'SEP', 7000, 0.3, 10, **PIPE_OIL), pump('WIP', 'WS', 'MI', 280.0, 9000.0)]
    e += [pipe(f'IL{i}', 'MI', f'I{i}', 1500, 0.15, 0, temperature_c=20.0, water_cut=1.0, gor_sm3sm3=0.0) for i in range(1, 4)]
    return _finish(n, e)


def gas_condensate_tieback():
    """Gas-condensate subsea tieback of 35 km to a host: condensate-gas ratio from the tank, liquid loading and flow-assurance screening apply."""
    n = [gas_tank('G1', 'CONDENSATE-GAS', pr=420.0, t=120.0, giip=150e9, phase='gas_condensate', min_pressure_bar=60.0)]
    n += [gas_well(f'W{i}', f'SUBSEA-{i}', 'G1', depth_m=3800.0, gas_c_sm3d_bar2n=80.0, reservoir_pressure_bar=420.0, gor_sm3sm3=16000.0, api=48.0) for i in range(1, 4)]
    n += [manifold('M1', 'SUBSEA MANIFOLD'), separator('HOST', 'HOST', 60.0, max_gas_rate_sm3d=11e6)]
    pf = dict(PIPE_GAS, gor_sm3sm3=16000.0, api=48.0, thermal_model='heat_loss', ambient_temperature_c=4.0)
    e = [pipe(f'FL{i}', f'W{i}', 'M1', 800, 0.2, 0, **dict(PIPE_GAS, gor_sm3sm3=16000.0, api=48.0)) for i in range(1, 4)] + [pipe('TIEBACK', 'M1', 'HOST', 35000, 0.5, 60, **pf)]
    return _finish(n, e)


def onshore_gas_gathering():
    """Onshore gas: two well clusters, gathering lines, a field compressor station and sales gas export."""
    n = [gas_tank('G1', 'CLUSTER-1 FIELD', pr=160.0, t=70.0, giip=25e9, min_pressure_bar=10.0), gas_tank('G2', 'CLUSTER-2 FIELD', pr=140.0, t=65.0, giip=18e9, min_pressure_bar=10.0)]
    n += [gas_well(f'A{i}', f'A-{i}', 'G1', depth_m=2200.0, gas_c_sm3d_bar2n=40.0, reservoir_pressure_bar=160.0, tubing_id_m=0.0889) for i in range(1, 5)]
    n += [gas_well(f'B{i}', f'B-{i}', 'G2', depth_m=2000.0, gas_c_sm3d_bar2n=35.0, reservoir_pressure_bar=140.0, tubing_id_m=0.0889) for i in range(1, 5)]
    n += [manifold('MA', 'CLUSTER-A HEADER'), manifold('MB', 'CLUSTER-B HEADER'), manifold('MC', 'GATHERING HEADER'), manifold('MD', 'STATION DISCHARGE'), separator('SALES', 'SALES GAS METERING', 70.0, max_gas_rate_sm3d=6e6)]
    e = [pipe(f'FA{i}', f'A{i}', 'MA', 1200, 0.1, 0, **PIPE_GAS) for i in range(1, 5)] + [pipe(f'FB{i}', f'B{i}', 'MB', 1200, 0.1, 0, **PIPE_GAS) for i in range(1, 5)]
    e += [pipe('GA', 'MA', 'MC', 6000, 0.25, 0, **PIPE_GAS), pipe('GB', 'MB', 'MC', 9000, 0.25, 0, **PIPE_GAS), compressor('FIELD-COMP', 'MC', 'MD', ratio=4.0, gor=5e5, max_discharge=100.0), pipe('SALES-LINE', 'MD', 'SALES', 8000, 0.3, 0, **PIPE_GAS)]
    return _finish(n, e)


def simple_well():
    """Two wells to a manifold (the smallest network - a good place to learn nodal analysis)."""
    from network.examples import demo_case
    return demo_case()


def demo_waterflood_field():
    """The standard demo: one oil tank with aquifer, three producers (one on gas lift), water injection, capacity-limited separator."""
    from network.examples import demo_field_case
    return demo_field_case()


TEMPLATES = {
    'hpht_4slot_gas': dict(name='HPHT gas - 4-slot subsea template', category='Subsea', builder=hpht_4slot_gas, years=10, step=90, start='2028-01-01',
                           shows='Four HPHT gas wells (750 bar, 170 °C) on one subsea template, 25 km export flowline to a host platform. Correlation PVT with CO2 and N2. Wellbore / flowline thermal models are off for speed and robustness (switch on Ramey / heat-loss per element to see the arrival temperature).',
                           watch='Host gas capacity 26 MSm³/d, tubing / flowline erosional velocity, pressure fall of the tank.'),
    'daisy_chain_oil': dict(name='Daisy chain - three templates in series', category='Subsea', builder=daisy_chain_oil, years=12, step=90, start='2028-01-01',
                            shows='Templates A, B, C joined in series with growing line size; one trunk to the host with water injection support.',
                            watch='Back-pressure of the upstream template wells and the trunk capacity: wells far down the chain are the first to choke back.'),
    'multi_tank_commingled': dict(name='Several tanks, communicating, commingled wells', category='Reservoir', builder=multi_tank_commingled, years=12, step=90, start='2028-01-01',
                                  shows='Three stacked sands with tank-to-tank transmissibility; single-zone wells plus two dual-zone wells (two zone wells joined at a joint) that produce two tanks through one wellhead.',
                                  watch='Pressure equalisation through the links (Tanks & coupling), crossflow is not modelled inside a commingled well; a well draws from one tank, so a dual-zone well is two zone wells.'),
    'oil_gas_injection': dict(name='Oil with gas injection', category='Oil', builder=oil_gas_injection, years=12, step=90, start='2028-01-01',
                              shows='Producers plus two gas injectors fed through an injection compressor from a gas header; the tank sees gas injection in its balance.',
                              watch='Voidage replacement in the Tanks & coupling page, GOR evolution, compressor discharge limit.'),
    'pure_depletion_oil': dict(name='Pure depletion oil', category='Oil', builder=pure_depletion_oil, years=15, step=90, start='2028-01-01',
                               shows='No aquifer and no injection: pressure falls through the bubble point, GOR rises, water cut stays low. Plateau then decline.',
                               watch='Recovery factor (typically low), when the wells can no longer overcome the separator pressure.'),
    'wellhead_platform_tieback': dict(name='Wellhead platform tied back to a host platform', category='Platforms', builder=wellhead_platform_tieback, years=12, step=90, start='2028-01-01',
                                      shows='WHP-A with six wells, a 14 km tieback to host B (own wells, only separator).',
                                      watch='Tieback arrival pressure and capacity; wells at A are set by host B inlet pressure plus the line.'),
    'subsea_booster_pump': dict(name='Subsea booster pump', category='Subsea', builder=subsea_booster_pump, years=12, step=90, start='2028-01-01',
                                shows='Low-pressure heavy oil on a 28 km tieback with a multiphase booster pump; remove the pump to see the wells choke back.',
                                watch='Pump head and power in the equipment table, suction pressure vs bubble point (gas handling).'),
    'subsea_compressor_gas': dict(name='Subsea gas compression', category='Subsea', builder=subsea_compressor_gas, years=15, step=90, start='2028-01-01',
                                  shows='Gas field on a 60 km tieback with a subsea compressor lowering the manifold pressure.',
                                  watch='Compression ratio, discharge limit, power; compare the rate with the compressor ratio set to 1.'),
    'topside_compressor_gas': dict(name='Topside export compressor', category='Platforms', builder=topside_compressor_gas, years=15, step=90, start='2028-01-01',
                                   shows='Low-pressure inlet separation and a topside compressor to the export pipeline pressure.',
                                   watch='Inlet pressure vs compression ratio and power; late-life drop in plateau.'),
    'horizontal_wells': dict(name='Horizontal well development', category='Oil', builder=horizontal_wells, years=12, step=90, start='2028-01-01',
                             shows='Four horizontal wells with trajectory and a two-size completion (Vogel inflow); hydrostatic head uses true vertical depth.',
                             watch='The Element results tab shows the tubing profile along the deviated well.'),
    'hpht_tight_gas_frac': dict(name='HPHT tight gas with hydraulic fractures', category='Gas', builder=hpht_tight_gas_frac, years=12, step=90, start='2028-01-01',
                                shows='Four drainage compartments with two fractured horizontal wells each (negative skin, high deliverability), weak communication, HPHT PVT with CO2 and Ramey temperature.',
                                watch='Steep decline from the small compartments; fracture transient (linear flow) is NOT modelled - calibrate C, n and skin to rate-transient analysis.'),
    'gas_lift_field': dict(name='Gas lift', category='Oil', builder=gas_lift_field, years=12, step=90, start='2028-01-01',
                           shows='Five gas-lifted producers with high water cut; per-well injection rate.', watch='Gas-lift curve per well in Nodal analysis.'),
    'esp_field': dict(name='ESP lifted heavy oil', category='Oil', builder=esp_field, years=12, step=90, start='2028-01-01',
                      shows='Heavy oil with ESPs on every well.', watch='ESP head, power and rate envelope in Nodal analysis.'),
    'waterflood_pattern': dict(name='Waterflood', category='Oil', builder=waterflood_pattern, years=15, step=90, start='2028-01-01',
                               shows='Five producers, three injectors fed by a seawater pump; pressure held by injection.', watch='VRR and drive indices in Tanks & coupling.'),
    'gas_condensate_tieback': dict(name='Gas-condensate tieback', category='Gas', builder=gas_condensate_tieback, years=12, step=90, start='2028-01-01',
                                   shows='Three subsea wells on a 35 km tieback, condensate-gas ratio from the tank.', watch='Liquid loading, arrival temperature and hydrate margin (Profiles & flow assurance).'),
    'onshore_gas_gathering': dict(name='Onshore gas gathering with compression', category='Gas', builder=onshore_gas_gathering, years=15, step=90, start='2028-01-01',
                                  shows='Two clusters, gathering network, field compressor station and sales gas.', watch='Inlet pressure vs compressor power; cluster back-pressure interaction.'),
    'simple_well': dict(name='Two wells (learning example)', category='Learning', builder=simple_well, years=5, step=90, start='2026-01-01', shows='Smallest network.', watch='Nodal analysis tab.'),
    'demo_waterflood_field': dict(name='Standard demo field', category='Learning', builder=demo_waterflood_field, years=10, step=90, start='2026-01-01', shows='Oil tank with aquifer, three producers (one gas lift), water injection.', watch='Every tab.'),
}


def categories(): return list(dict.fromkeys(t['category'] for t in TEMPLATES.values()))


def build(key):
    """Fresh (nodes, edges) of a template."""
    if key not in TEMPLATES: raise KeyError(key)
    from ui.graph_contract import normalize_graph
    n, e = TEMPLATES[key]['builder'](); n, e, _ = normalize_graph(copy.deepcopy(n), copy.deepcopy(e))   # same defaults the canvas adds, so moving a box never changes the model
    return n, e


def catalogue():
    import pandas as pd
    rows = []
    for k, t in TEMPLATES.items():
        n, e = t['builder'](); rows.append({'Key': k, 'Template': t['name'], 'Category': t['category'], 'Wells': sum(1 for x in n if x['kind'] == 'well'), 'Tanks': sum(1 for x in n if x['kind'] == 'reservoir'),
                                            'Injectors': sum(1 for x in n if 'injector' in x['kind']), 'Nodes': len(n), 'Lines': len(e)})
    return pd.DataFrame(rows)
