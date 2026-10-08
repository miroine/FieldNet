"""One consistent set of result tables ("data hub") behind every plot, export, group sum and post-processing script.

``build_hub`` derives every table from the same three inputs - the model, the current steady-state solve and the forecast - so two
tabs can never show different numbers for the same quantity. Each dataset carries metadata (what it is, units, where it came from,
the model hash it was computed for); ``check_consistency`` cross-checks the tables against each other and flags stale results."""
from __future__ import annotations
import copy, hashlib, json
from collections import OrderedDict
from dataclasses import dataclass, field
import pandas as pd
from network import annual, groups as grp


@dataclass
class Hub:
    datasets: 'OrderedDict[str, pd.DataFrame]' = field(default_factory=OrderedDict)
    meta: dict = field(default_factory=dict)
    info: dict = field(default_factory=dict)
    forecast: object = None     # the forecast dict the tables were built from (None when absent / stale)

    def add(self, name, df, description='', source='', units=''):
        if df is None or (hasattr(df, 'empty') and df.empty): return
        self.datasets[name] = df.reset_index(drop=True) if isinstance(df, pd.DataFrame) else pd.DataFrame(df)
        self.meta[name] = {'description': description, 'source': source, 'units': units, 'rows': int(len(self.datasets[name])), 'columns': list(self.datasets[name].columns)}

    def catalogue(self):
        return pd.DataFrame([{'Dataset': k, 'Rows': m['rows'], 'Columns': len(m['columns']), 'Source': m['source'], 'Description': m['description']} for k, m in self.meta.items()])

    def get(self, name): return self.datasets[name].copy()


def _well_pi(n):
    """PI shown in the tables: the Darcy-derived value when the well computes its inflow from reservoir properties."""
    p = n.get('params') or {}
    if p.get('darcy') in (True, 'true', 'True', 1):
        try:
            from physics.darcy_ipr import darcy_ipr
            return round(float(darcy_ipr(p, float(p.get('reservoir_pressure_bar') or 200.0), 'gas' if str(p.get('ipr_model')) == 'Gas' else 'oil')['pi']), 3)
        except Exception:
            pass
    return p.get('pi_m3d_bar')


def model_tables(nodes, edges):
    wells, tanks, lines = [], [], []
    for n in nodes:
        p = n.get('params') or {}
        if n.get('kind') == 'well':
            wells.append({'Well': n.get('name') or n['id'], 'Well ID': n['id'], 'Tank': p.get('reservoir_id'), 'Group': p.get('group', ''), 'PI [m3/d/bar]': _well_pi(n), 'IPR': ('Darcy ' if p.get('darcy') in (True, 'true', 'True', 1) else '') + str(p.get('ipr_model', 'PI')), 'Skin': p.get('skin', 0.0),
                          'Depth [m]': p.get('depth_m'), 'Tubing ID [m]': p.get('tubing_id_m'), 'Water cut': p.get('water_cut'), 'GOR [Sm3/Sm3]': p.get('gor_sm3sm3'), 'API': p.get('api'), 'Lift': p.get('lift_type', 'none'), 'Fluid': p.get('fluid_name', '')})
        elif n.get('kind') == 'reservoir':
            tanks.append({'Tank': n.get('name') or n['id'], 'Tank ID': n['id'], 'Group': p.get('group', ''), 'Phase': p.get('fluid_phase', 'oil'), 'Pi [bar]': p.get('reservoir_pressure_bar'), 'STOIIP [Sm3]': p.get('stoiip_sm3'),
                          'GIIP [Sm3]': p.get('giip_sm3'), 'Boi': p.get('boi_rm3_sm3'), 'Rsi': p.get('rsi_sm3_sm3'), 'Pb [bar]': p.get('bubble_point_bar'), 'Aquifer PI [m3/d/bar]': p.get('aquifer_pi_m3d_bar'), 'Fluid': p.get('fluid_name', '')})
    for e in edges:
        p = e.get('params') or {}
        if e.get('kind', 'pipeline') == 'pipeline':
            lines.append({'Line': e.get('name') or e['id'], 'Line ID': e['id'], 'From': e['source'], 'To': e['target'], 'Length [m]': e.get('length_m'), 'Diameter [m]': e.get('diameter_m'), 'Roughness [m]': e.get('roughness_m'),
                          'Water cut': p.get('water_cut'), 'GOR [Sm3/Sm3]': p.get('gor_sm3sm3'), 'Fluid': p.get('fluid_name', '')})
    return pd.DataFrame(wells), pd.DataFrame(tanks), pd.DataFrame(lines)


def solve_tables(nodes, edges, solve_result):
    p, q, info, det = solve_result; names = {n['id']: n.get('name') or n['id'] for n in nodes}
    wells = pd.DataFrame([{'Well': names.get(k, k), 'Well ID': k, **{kk: v for kk, v in d.items() if isinstance(v, (int, float, str, bool))}} for k, d in det.items()])
    pr = pd.DataFrame([{'Node': names.get(k, k), 'Node ID': k, 'Pressure [bar]': v} for k, v in p.items()])
    fl = pd.DataFrame([{'Element': (next((e.get('name') for e in edges if e['id'] == k), None) or k), 'Element ID': k, 'Flow [m3/d]': v} for k, v in q.items()])
    return wells, pr, fl


def kpis(forecast):
    from network.prognosis import forecast_kpis
    return forecast_kpis(forecast) if forecast and forecast.get('field') else {}


def build_hub(nodes, edges, solve_result=None, forecast=None, model_hash=None, forecast_hash=None, solve_hash=None):
    """All tables for the current model. ``solve_result`` = (pressures, flows, info, details); ``forecast`` = ``run_forecast`` dict."""
    hub = Hub(); hub.info = {'model_hash': model_hash, 'forecast_hash': forecast_hash, 'solve_hash': solve_hash}; hub.forecast = forecast
    w, t, l = model_tables(nodes, edges)
    hub.add('model_wells', w, 'Well inputs', 'model'); hub.add('model_tanks', t, 'Tank inputs', 'model'); hub.add('model_pipelines', l, 'Pipeline inputs', 'model')
    if solve_result and solve_result[0]:
        sw, sp, sf = solve_tables(nodes, edges, solve_result)
        hub.add('solve_wells', sw, 'Steady-state well results (current solve)', 'solve'); hub.add('solve_pressures', sp, 'Node pressures [bar]', 'solve'); hub.add('solve_flows', sf, 'Element flows [m3/d]', 'solve')
        if grp.group_names(nodes): hub.add('groups_solve', grp.solve_summary(nodes, solve_result), 'Group sums at the current solve', 'groups+solve')
        th = (solve_result[2] or {}).get('thermal')
        if th: hub.add('solve_temperatures', pd.DataFrame([{'Node ID': k, 'Temperature [C]': v} for k, v in th['node_temperature_c'].items()]), 'Node temperatures (thermal model)', 'solve')
    if forecast and forecast.get('field'):
        hub.add('forecast_field', pd.DataFrame(forecast['field']), 'Field profile (rates are averages over the step starting at Date)', 'forecast', 'm3/d, Sm3/d, cumulative in m3/Sm3')
        hub.add('forecast_wells', pd.DataFrame(forecast.get('wells') or []), 'Well profiles', 'forecast'); hub.add('forecast_tanks', pd.DataFrame(forecast.get('tanks') or []), 'Tank state and cumulative volumes', 'forecast')
        hub.add('forecast_constraints', pd.DataFrame(forecast.get('constraints') or []), 'Constraint margins per step', 'forecast')
        hub.add('annual_field', annual.field_annual(forecast), 'Calendar-year volumes of the field (exact integral of the profile)', 'forecast', 'Sm3, m3')
        hub.add('annual_wells', annual.wells_annual(forecast), 'Calendar-year volumes per well', 'forecast'); hub.add('annual_tanks', annual.tanks_annual(forecast), 'Calendar-year volumes and pressure per tank', 'forecast')
        k = kpis(forecast)
        if k: hub.add('kpis', pd.DataFrame([{'KPI': a, 'Value': b} for a, b in k.items()]), 'Forecast KPIs', 'forecast')
        if grp.group_names(nodes):
            hub.add('groups_profile', grp.group_profile(nodes, forecast), 'Group profiles (sum of member wells / tanks)', 'groups+forecast'); hub.add('groups_annual', grp.group_annual(nodes, forecast), 'Calendar-year volumes per group', 'groups+forecast')
    return hub


def check_consistency(hub, nodes=None, forecast=None, current_model_hash=None, tol=1e-6):
    """Cross-checks between tables. Returns a DataFrame(check, status, detail); status is OK / WARN / FAIL."""
    rows = []
    def add(c, ok, d, warn=False): rows.append({'Check': c, 'Status': 'OK' if ok else ('WARN' if warn else 'FAIL'), 'Detail': d})
    ds = hub.datasets
    if current_model_hash is not None:
        for k, label in (('solve_hash', 'steady-state solve'), ('forecast_hash', 'forecast')):
            h = hub.info.get(k)
            if h is not None: add(f'{label} belongs to the model on screen', h == current_model_hash, 'results are current' if h == current_model_hash else 'the model was edited after this result was computed - re-run it', warn=True)
    if 'forecast_field' in ds and 'forecast_wells' in ds:
        f, w = ds['forecast_field'], ds['forecast_wells']
        for rate in ('Oil [m3/d]', 'Water [m3/d]', 'Gas [Sm3/d]'):
            s = w.groupby('Date')[rate].sum().reindex(f['Date']).fillna(0).values; d = float(abs(s - f[rate].values).max()); ref = max(float(f[rate].abs().max()), 1.0)
            add(f'field {rate.split(" [")[0].lower()} rate = sum of wells', d <= 1e-6 * ref + 1e-6, f'max difference {d:.3g}')
    if 'forecast_field' in ds and 'annual_field' in ds:
        f, a = ds['forecast_field'], ds['annual_field']
        for cum, vol in (('Cumulative oil [Sm3]', 'Oil [Sm3]'), ('Cumulative gas [Sm3]', 'Gas [Sm3]'), ('Cumulative water [m3]', 'Water [m3]')):
            if cum in f and vol in a: d = abs(float(f[cum].iloc[-1]) - float(a[vol].sum())); add(f'annual {vol.split(" [")[0].lower()} sums to the cumulative', d <= 1e-6 * max(abs(float(f[cum].iloc[-1])), 1.0), f'difference {d:.3g}')
    if 'forecast_tanks' in ds and 'forecast_wells' in ds and 'forecast_field' in ds:
        t, f = ds['forecast_tanks'], ds['forecast_field']; last = t['Date'].iloc[-1]
        d = abs(float(t[t['Date'] == last]['Cum oil [Sm3]'].sum()) - float(f['Cumulative oil [Sm3]'].iloc[-1])); add('tank cumulative oil = field cumulative oil', d <= 1e-6 * max(float(f['Cumulative oil [Sm3]'].iloc[-1]), 1.0) + 1e-6, f'difference {d:.3g} Sm3', warn=True)
    if nodes and forecast and grp.group_names(nodes):
        for c in grp.reconcile(nodes, forecast): add(c['check'], c['ok'], c['detail'])
    if 'solve_wells' in ds and 'forecast_wells' in ds and 'liquid_rate_m3d' in ds['solve_wells']:
        w = ds['forecast_wells']; first = w['Date'].iloc[0]; fw = w[w['Date'] == first].set_index('Well ID')['Liquid [m3/d]']; sw = ds['solve_wells'].set_index('Well ID')['liquid_rate_m3d']
        common = fw.index.intersection(sw.index)
        if len(common): rel = float(((fw[common] - sw[common]).abs() / (sw[common].abs() + 1.0)).max()); add('first forecast step agrees with the steady-state solve', rel < 0.05, f'max relative difference {rel:.1%} (tank pressure and fluid may legitimately differ slightly)', warn=True)
    return pd.DataFrame(rows)


def hub_signature(hub):
    return hashlib.sha256(json.dumps({k: [int(len(v)), list(map(str, v.columns))] for k, v in hub.datasets.items()}, sort_keys=True).encode()).hexdigest()[:12]
