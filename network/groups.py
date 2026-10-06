"""Groups: any set of tanks, wells and injectors can be put in a named group and summed.

A group is stored on each element as ``params['group']`` (a string; ``'North/Segment A'`` makes a hierarchy: the element counts in
'North/Segment A' and in 'North'). One element belongs to one group path, so a group can never double count. The model therefore stays
a single graph - groups travel with cases, exports and shared files.

Group results are *derived* from the forecast / solve result tables (never stored), so they cannot disagree with the field totals:
``group_profile`` sums the member wells of ``forecast['wells']``; injection and pressure come from the member tanks."""
from __future__ import annotations
import pandas as pd
from network.annual import step_table, annual_volumes

PRODUCER, INJECTORS, TANK = 'well', ('water_injector', 'gas_injector', 'injector'), 'reservoir'
UNGROUPED = '(ungrouped)'


def group_of(node): return str(((node.get('params') or {}).get('group') or '')).strip().strip('/')


def prefixes(path):
    parts = [p for p in str(path).split('/') if p]; return ['/'.join(parts[:i + 1]) for i in range(len(parts))]


def group_names(nodes):
    """Every group path including parents, sorted."""
    s = set()
    for n in nodes:
        g = group_of(n)
        if g: s.update(prefixes(g))
    return sorted(s)


def members(nodes, include_children=True):
    """{group path: [node ids]}. With ``include_children`` a parent contains the members of all its sub-groups."""
    out = {}
    for n in nodes:
        g = group_of(n)
        if not g: continue
        for p in (prefixes(g) if include_children else [g]): out.setdefault(p, []).append(n['id'])
    return dict(sorted(out.items()))


def assign(nodes, ids, group):
    """Set (or clear, with '') the group of the given element ids; returns the number of elements changed."""
    group = str(group or '').strip().strip('/'); ids = set(ids); n_changed = 0
    for n in nodes:
        if n['id'] in ids:
            prm = n.setdefault('params', {})
            if group: prm['group'] = group
            else: prm.pop('group', None)
            n_changed += 1
    return n_changed


def tank_members(nodes, tank_id):
    """The tank plus every producer / injector assigned to it."""
    ids = [tank_id] + [n['id'] for n in nodes if (n.get('params') or {}).get('reservoir_id') == tank_id and n.get('kind') in (PRODUCER,) + INJECTORS]
    return ids


def group_from_tank(nodes, tank_id, name=None):
    """Put a tank and its wells / injectors into one group (named after the tank by default). Returns (group name, member ids)."""
    t = next((n for n in nodes if n['id'] == tank_id), None)
    if t is None or t.get('kind') != TANK: raise ValueError('not a reservoir tank')
    name = (name or t.get('name') or tank_id).strip(); ids = tank_members(nodes, tank_id); assign(nodes, ids, name); return name, ids


def member_table(nodes):
    rows = []
    for n in nodes:
        if n.get('kind') in (PRODUCER, TANK) + INJECTORS:
            rows.append({'Element': n.get('name') or n['id'], 'Type': {'well': 'producer', 'reservoir': 'tank'}.get(n['kind'], 'injector'), 'Group': group_of(n), '_id': n['id']})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- results
def _tank_in_place(node):
    from network.reservoir_mb import Tank
    t = Tank(node); return (t.n, t.g, t.phase)


def solve_summary(nodes, solve_result):
    """Group sums at the current steady-state solve: rates of member wells, in-place volumes of member tanks."""
    p, q, info, det = solve_result; byid = {n['id']: n for n in nodes}; rows = []
    for g, ids in members(nodes).items():
        r = {'Group': g, 'Producers': 0, 'Flowing': 0, 'Tanks': 0, 'Injectors': 0, 'Liquid [m3/d]': 0.0, 'Oil [m3/d]': 0.0, 'Water [m3/d]': 0.0, 'Gas [Sm3/d]': 0.0, 'STOIIP [Sm3]': 0.0, 'GIIP [Sm3]': 0.0}
        for i in ids:
            n = byid[i]; k = n.get('kind')
            if k == PRODUCER:
                d = det.get(i) or {}; r['Producers'] += 1; r['Flowing'] += int(float(d.get('liquid_rate_m3d', 0) or 0) > 1e-6)
                for a, b in (('Liquid [m3/d]', 'liquid_rate_m3d'), ('Oil [m3/d]', 'oil_rate_m3d'), ('Water [m3/d]', 'water_rate_m3d'), ('Gas [Sm3/d]', 'gas_rate_sm3d')): r[a] += float(d.get(b, 0.0) or 0.0)
            elif k == TANK:
                r['Tanks'] += 1; nn, gg, ph = _tank_in_place(n); r['STOIIP [Sm3]'] += nn; r['GIIP [Sm3]'] += gg
            elif k in INJECTORS: r['Injectors'] += 1
        rows.append(r)
    return pd.DataFrame(rows)


def group_profile(nodes, forecast):
    """Long table Date x Group with summed rates, cumulative volumes, recovery and (volume-weighted) tank pressure from a forecast."""
    w = pd.DataFrame(forecast.get('wells') or []); t = pd.DataFrame(forecast.get('tanks') or []); f = pd.DataFrame(forecast.get('field') or [])
    if f.empty: return pd.DataFrame()
    mem = members(nodes); byid = {n['id']: n for n in nodes}; out = []
    steps = step_table(f['Date'], f['Step [days]'] if 'Step [days]' in f else None); days = dict(zip(f['Date'], steps['days']))
    for g, ids in mem.items():
        wid = [i for i in ids if byid[i].get('kind') == PRODUCER]; tid = [i for i in ids if byid[i].get('kind') == TANK]
        base = pd.DataFrame({'Date': f['Date']}); base['Day'] = f['Day'].values
        if len(w) and wid:
            gw = w[w['Well ID'].isin(wid)].groupby('Date').agg({'Liquid [m3/d]': 'sum', 'Oil [m3/d]': 'sum', 'Water [m3/d]': 'sum', 'Gas [Sm3/d]': 'sum', 'Status': lambda s: int((s == 'flowing').sum())})
            gw = gw.rename(columns={'Status': 'Wells flowing'}); base = base.merge(gw, left_on='Date', right_index=True, how='left')
        for c in ('Liquid [m3/d]', 'Oil [m3/d]', 'Water [m3/d]', 'Gas [Sm3/d]', 'Wells flowing'):
            if c not in base: base[c] = 0.0
        base = base.fillna(0.0)
        dd = base['Date'].map(days).astype(float)
        for rate, cum in (('Oil [m3/d]', 'Cumulative oil [Sm3]'), ('Gas [Sm3/d]', 'Cumulative gas [Sm3]'), ('Water [m3/d]', 'Cumulative water [m3]'), ('Liquid [m3/d]', 'Cumulative liquid [m3]')):
            base[cum] = (base[rate] * dd).cumsum()
        base['Water cut [%]'] = 100 * base['Water [m3/d]'] / base['Liquid [m3/d]'].where(base['Liquid [m3/d]'] > 0)
        base['GOR [Sm3/Sm3]'] = base['Gas [Sm3/d]'] / base['Oil [m3/d]'].where(base['Oil [m3/d]'] > 0)
        base['Water injection [m3/d]'] = 0.0; base['Pressure [bar]'] = float('nan'); base['RF oil [%]'] = float('nan')
        if len(t) and tid:
            gt = t[t['Tank ID'].isin(tid)]
            winj = gt.groupby('Date')['Cum water inj [m3]'].sum().reindex(base['Date']).values
            cw = pd.Series(winj).diff().fillna(pd.Series(winj).iloc[0]); base['Water injection [m3/d]'] = (cw / dd.where(dd > 0)).fillna(0.0).values
            base['Cumulative water injection [m3]'] = winj
            pv = {}
            for i in tid:
                tk = byid[i]; nn, gg, ph = _tank_in_place(tk); pv[i] = (nn if ph == 'oil' else gg) or 1.0
            gt = gt.assign(_w=gt['Tank ID'].map(pv)); pw = gt.groupby('Date').apply(lambda x: (x['Pressure [bar]'] * x['_w']).sum() / x['_w'].sum(), include_groups=False)
            base['Pressure [bar]'] = pw.reindex(base['Date']).values
            stoiip = sum(_tank_in_place(byid[i])[0] for i in tid if _tank_in_place(byid[i])[2] == 'oil')
            if stoiip > 0: base['RF oil [%]'] = 100 * gt.groupby('Date')['Cum oil [Sm3]'].sum().reindex(base['Date']).values / stoiip
        base.insert(1, 'Group', g); out.append(base)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def group_annual(nodes, forecast):
    gp = group_profile(nodes, forecast)
    if gp.empty: return pd.DataFrame()
    rate_cols = [c for c in ('Oil [m3/d]', 'Gas [Sm3/d]', 'Water [m3/d]', 'Liquid [m3/d]', 'Water injection [m3/d]') if c in gp]; f = pd.DataFrame(forecast['field']); sd = dict(zip(f['Date'], f['Step [days]'])) if 'Step [days]' in f else None
    out = []
    for g, d in gp.groupby('Group', sort=False):
        d = d.reset_index(drop=True)
        if sd and all(x in sd for x in d['Date']): d['Step [days]'] = [sd[x] for x in d['Date']]
        a = annual_volumes(d, rate_cols)
        if len(a): a.insert(0, 'Group', g); out.append(a)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def reconcile(nodes, forecast, tol=1e-6):
    """Checks that groups add up: for every group level, the producers summed here equal the same wells' own table, and the groups of a
    full partition (every producer in exactly one top-level group) add up to the field total. Returns a list of {check, ok, detail}."""
    checks = []; f = pd.DataFrame(forecast.get('field') or []); w = pd.DataFrame(forecast.get('wells') or [])
    if f.empty or w.empty: return [{'check': 'groups', 'ok': True, 'detail': 'no forecast'}]
    gp = group_profile(nodes, forecast); top = sorted({g.split('/')[0] for g in members(nodes)})
    prod = [n['id'] for n in nodes if n.get('kind') == PRODUCER]; grouped = {i for g in top for i in members(nodes).get(g, []) if i in prod}
    if gp.empty: return [{'check': 'groups', 'ok': True, 'detail': 'no groups defined'}]
    s = gp[gp['Group'].isin(top)].groupby('Date')['Oil [m3/d]'].sum(); ref = w[w['Well ID'].isin(grouped)].groupby('Date')['Oil [m3/d]'].sum()
    diff = float((s - ref.reindex(s.index).fillna(0)).abs().max()); checks.append({'check': 'sum of top-level groups = sum of their wells (oil rate)', 'ok': diff <= tol * max(float(ref.abs().max()), 1.0), 'detail': f'max difference {diff:.3g} m3/d'})
    if len(grouped) == len(prod):
        fd = f.set_index('Date')['Oil [m3/d]']; diff = float((s - fd.reindex(s.index)).abs().max())
        checks.append({'check': 'all producers grouped: groups add up to the field oil rate', 'ok': diff <= 1e-6 * max(float(fd.abs().max()), 1.0) + 1e-6, 'detail': f'max difference {diff:.3g} m3/d'})
    else: checks.append({'check': 'ungrouped producers', 'ok': True, 'detail': f'{len(prod) - len(grouped)} producer(s) not in any group (the groups do not cover the field)'})
    return checks
