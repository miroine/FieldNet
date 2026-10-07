"""Drainage-strategy recommendation: how many wells, what plateau, what recovery, and what limits it.

Sweeps the number of producers (first k of the drilling order), runs the capacity-constrained forecast for each, and reports per case
the primary-phase plateau rate and length, ultimate recovery (and RF), the binding constraint and, optionally, NPV. The recommendation is
the smallest well count that (a) meets the plateau target if one is given, and (b) still earns its last well (marginal-gain rule, or
NPV when economics are supplied). Everything is derived from the same forecasts the rest of the app shows - no separate model.
"""
from __future__ import annotations
import html, math
from network.prognosis import run_scenarios, forecast_kpis

BIND_MARGIN = 0.02   # a constraint with margin below 2 % of its limit counts as binding


def primary_phase(nodes):
    """'gas' when the in-place gas volume dominates the field (gas / condensate tanks), else 'oil'."""
    oil = gas = 0.0
    for n in nodes:
        if n.get('kind') != 'reservoir': continue
        p = n.get('params') or {}
        if str(p.get('fluid_phase', 'oil')).lower() == 'oil': oil += float(p.get('stoiip_sm3', 20e6) or 0) * 1000.0   # 1 Sm3 oil ~ 1000 Sm3 gas (energy equivalent)
        else: gas += float(p.get('giip_sm3', 5e9) or 0)
    return 'gas' if gas > oil else 'oil'


def _series(fc, phase):
    rk, ck = ('Gas [Sm3/d]', 'Cumulative gas [Sm3]') if phase == 'gas' else ('Oil [m3/d]', 'Cumulative oil [Sm3]')
    rows = fc.get('field') or []
    return [float(r.get('Day', 0.0)) for r in rows], [float(r.get(rk, 0.0)) for r in rows], [float(r.get(ck, 0.0)) for r in rows]


def plateau(days, rate, frac=0.9):
    """(plateau rate = mean rate while within ``frac`` of the peak, plateau length [years], peak). Plateau = the longest run at/above frac*peak."""
    if not rate: return 0.0, 0.0, 0.0
    peak = max(rate)
    if peak <= 0: return 0.0, 0.0, 0.0
    best = (0.0, 0.0); cur_start = None; vals = []
    for i in range(len(rate)):
        up = rate[i] >= frac * peak
        if up and cur_start is None: cur_start = i; vals = []
        if up: vals.append(rate[i])
        if cur_start is not None and (not up or i == len(rate) - 1):
            end = i if not up else i + 1
            length = (days[min(end, len(days) - 1)] - days[cur_start]) if end > cur_start else 0.0
            if i == len(rate) - 1 and up: length = (days[-1] - days[cur_start]) + (days[-1] - days[-2] if len(days) > 1 else 0.0)
            if length > best[1]: best = (sum(vals) / len(vals), length)
            cur_start = None
    return best[0], best[1] / 365.25, peak


def binding_constraint(fc, days, rate, frac=0.9):
    """Name of the constraint that is binding most often while the field is at plateau (or over the whole run if none binds there)."""
    peak = max(rate) if rate else 0.0; plateau_dates = {}
    rows = fc.get('field') or []
    for r, q in zip(rows, rate):
        plateau_dates[r.get('Date')] = q >= frac * peak
    counts = {}; n_all = {}
    for c in fc.get('constraints') or []:
        lim, mar = c.get('Limit'), c.get('Margin')
        try:
            if lim is None or mar is None or float(lim) <= 0: continue
            if str(c.get('Relation', '<=')).strip() not in ('<=', '<'): continue
            binding = float(mar) <= BIND_MARGIN * float(lim)
        except (TypeError, ValueError): continue
        key = f"{c.get('Component', '?')} — {c.get('Constraint', '?')}"
        if plateau_dates.get(c.get('Date'), False): n_all[key] = n_all.get(key, 0) + 1
        if binding and plateau_dates.get(c.get('Date'), False): counts[key] = counts.get(key, 0) + 1
    if not counts: return None, 0.0
    key = max(counts, key=counts.get); return key, counts[key] / max(sum(1 for v in plateau_dates.values() if v), 1)


def npv(days, rate, price, opex_per_year, capex, rate_discount, years_total):
    """Discounted cash flow of a rate profile [price per Sm3] with fixed opex [per year] and up-front capex."""
    tot = -capex
    for i in range(len(days) - 1):
        dt = days[i + 1] - days[i]; t = (days[i] + 0.5 * dt) / 365.25
        tot += (rate[i] * dt * price - opex_per_year * dt / 365.25) / (1.0 + rate_discount) ** t
    return tot


def drainage_strategy(nodes, edges, order, start, years, step_days, enforce_constraints=True, min_gain_fraction=0.05, plateau_target=None,
                      plateau_target_years=None, economics=None, phase=None, progress=None, step_solver=None, workers=1, plateau_fraction=0.9):
    """``plateau_target`` = required primary-phase plateau rate (oil Sm3/d, gas Sm3/d); ``economics`` = {price_per_sm3, capex_per_well,
    opex_per_well_year, discount_rate} (all in one currency). Returns {'rows','recommended_wells','reason','summary','phase', ...}."""
    phase = phase or primary_phase(nodes); order = list(order)
    if not order: raise ValueError('No producers to sweep')
    results = run_scenarios(nodes, edges, [{'name': f'{k} wells', 'wells': order[:k]} for k in range(1, len(order) + 1)], start, years, step_days, enforce_constraints,
                            progress=progress, step_solver=step_solver, workers=workers)
    rows = []; prev = None
    for k, r in enumerate(results, 1):
        fc = r['forecast']; kp = forecast_kpis(fc); days, rate, cum = _series(fc, phase)
        prate, pyears, peak = plateau(days, rate, plateau_fraction); eur = cum[-1] if cum else 0.0
        rf = kp.get('rf_gas_pct') if phase == 'gas' else kp.get('rf_oil_pct')
        bc, share = binding_constraint(fc, days, rate, plateau_fraction)
        gain = eur - prev if prev is not None else eur
        row = {'Wells': k, 'Added well': order[k - 1], 'Plateau rate': prate, 'Plateau [years]': pyears, 'Peak rate': peak, 'EUR': eur, 'RF [%]': rf,
               'Incremental EUR': gain, 'Incremental [%]': (100.0 * gain / prev if prev else None), 'Binding constraint': bc, 'Binding share [%]': 100 * share, '_forecast': fc}
        if economics:
            e = economics; row['NPV'] = npv(days, rate, float(e.get('price_per_sm3', 0.0)), float(e.get('opex_per_well_year', 0.0)) * k, float(e.get('capex_per_well', 0.0)) * k,
                                           float(e.get('discount_rate', 0.08)), years)
        rows.append(row); prev = eur
    # --- recommendation -------------------------------------------------------------------------------------------------------------------
    n = len(rows); rec = 1; why = []
    if economics:
        rec = max(range(1, n + 1), key=lambda k: rows[k - 1]['NPV']); why.append(f"{rec} wells maximise NPV")
    else:
        for r in rows[1:]:
            if r['Incremental [%]'] is not None and r['Incremental [%]'] >= 100 * min_gain_fraction: rec = r['Wells']
        why.append(f"well {rec + 1} would add less than {100 * min_gain_fraction:.0f} % extra {phase}" if rec < n else f"every added well still adds at least {100 * min_gain_fraction:.0f} % - more wells may pay")
    if plateau_target:
        ok = [r['Wells'] for r in rows if r['Plateau rate'] >= plateau_target * 0.98 and (not plateau_target_years or r['Plateau [years]'] >= plateau_target_years)]
        if ok:
            if rec < min(ok): rec = min(ok); why.append(f"{rec} wells are needed to hold the plateau target")
            else: why.append('the plateau target is met')
        else:
            best = max(rows, key=lambda r: r['Plateau rate']); why.append(f"the plateau target is not reachable (best {best['Plateau rate']:.4g} with {best['Wells']} wells); "
                                                                         f"{best['Binding constraint'] or 'reservoir / well deliverability'} limits it")
    r = rows[rec - 1]
    limit = r['Binding constraint'] or ('well deliverability / reservoir pressure' if r['Plateau [years]'] > 0 else 'reservoir pressure')
    unit = 'MSm³/d' if phase == 'gas' else 'Sm³/d'; k = 1e6 if phase == 'gas' else 1.0
    summary = (f"{rec} producer{'s' if rec > 1 else ''}: plateau {r['Plateau rate'] / k:,.2f} {unit} for {r['Plateau [years]']:.1f} years, "
               f"{'RF %.1f %%' % r['RF [%]'] if r['RF [%]'] is not None else 'RF n/a'}, limited by {limit}.")
    tank_targets = [(n_.get('name', n_['id']), (n_.get('params') or {}).get('target_rf')) for n_ in nodes if n_.get('kind') == 'reservoir' and (n_.get('params') or {}).get('target_rf')]
    return {'rows': rows, 'recommended_wells': rec, 'reason': '; '.join(why) + '.', 'summary': summary, 'phase': phase, 'unit': unit, 'unit_scale': k,
            'suggested_rf_pct': r['RF [%]'], 'limit': limit, 'tank_targets': tank_targets, 'years': years, 'economics': economics, 'plateau_target': plateau_target}


# ---- printable report ------------------------------------------------------------------------------------------------------------
def _svg_bars(xs, ys, color, title, ylab, w=520, h=230, mark=None):
    if not ys: return ''
    top = max(max(ys), 1e-12) * 1.15; bw = (w - 70) / len(ys)
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{html.escape(title)}"><text x="8" y="16" font-size="12" font-weight="600" fill="#222">{html.escape(title)}</text>',
         f'<line x1="50" y1="{h-30}" x2="{w-10}" y2="{h-30}" stroke="#999"/>']
    for i, (x, y) in enumerate(zip(xs, ys)):
        bh = (h - 60) * (y / top) if top > 0 else 0; bx = 56 + i * bw
        o.append(f'<rect x="{bx:.1f}" y="{h-30-bh:.1f}" width="{bw*0.7:.1f}" height="{bh:.1f}" fill="{color}" opacity="{1 if x == mark else 0.55}"/>'
                 f'<text x="{bx+bw*0.35:.1f}" y="{h-14}" font-size="10" text-anchor="middle" fill="#444">{x}</text>'
                 f'<text x="{bx+bw*0.35:.1f}" y="{h-34-bh:.1f}" font-size="9" text-anchor="middle" fill="#222">{y:,.3g}</text>')
    o.append(f'<text x="8" y="{h/2:.0f}" font-size="10" fill="#666" transform="rotate(-90 8 {h/2:.0f})" text-anchor="middle">{html.escape(ylab)}</text></svg>')
    return ''.join(o)


def to_html(res, title='Drainage strategy recommendation', field='FieldNet case'):
    """Self-contained, printable HTML page (print to PDF from the browser)."""
    from ui.shapes import PHASE_COLOR
    rows = res['rows']; k = res['unit_scale']; unit = res['unit']; phase = res['phase']; col = PHASE_COLOR[phase]; rec = res['recommended_wells']
    xs = [r['Wells'] for r in rows]
    heads = ['Wells', f'Plateau rate [{unit}]', 'Plateau [years]', f'EUR [{"GSm³" if phase == "gas" else "MSm³"}]', 'RF [%]', 'Incremental [%]', 'Binding constraint'] + (['NPV'] if res.get('economics') else [])
    eur_k = 1e9 if phase == 'gas' else 1e6
    def cell(v, f='{:,.2f}'):
        return '—' if v is None or (isinstance(v, float) and v != v) else (f.format(v) if not isinstance(v, str) else html.escape(v))
    body = ''.join('<tr%s>' % (' class="rec"' if r['Wells'] == rec else '') + ''.join(f'<td>{c}</td>' for c in (
        r['Wells'], cell(r['Plateau rate'] / k), cell(r['Plateau [years]'], '{:.1f}'), cell(r['EUR'] / eur_k, '{:,.3f}'), cell(r['RF [%]'], '{:.1f}'), cell(r['Incremental [%]'], '{:.1f}'),
        cell(r['Binding constraint'] or '—')) + ((cell(r['NPV'], '{:,.1f}'),) if res.get('economics') else ())) + '</tr>' for r in rows)
    tt = ''.join(f"<li>{html.escape(str(n))}: target RF {100*float(t if float(t) <= 1 else float(t)/100):.0f} %</li>" for n, t in res.get('tank_targets', []))
    css = ('body{font:14px/1.45 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1c1c1c;max-width:900px;margin:28px auto;padding:0 18px}h1{font-size:22px;margin:0 0 4px}'
           '.sub{color:#666;margin-bottom:18px}.box{border:2px solid %s;border-radius:10px;padding:14px 18px;margin:14px 0;background:#fafafa}.box b{font-size:17px}'
           'table{border-collapse:collapse;width:100%%;font-size:12.5px;margin:12px 0}th,td{border-bottom:1px solid #ddd;padding:5px 8px;text-align:right}th:first-child,td:first-child,th:last-child,td:last-child{text-align:left}'
           'th{background:#f0f0f0}tr.rec td{background:#e9f6ef;font-weight:600}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}small{color:#666}@media print{body{margin:10mm}}') % col
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title><style>{css}</style></head><body>'
            f'<h1>{html.escape(title)}</h1><div class="sub">{html.escape(field)} · primary phase: {phase} · horizon {res["years"]:g} years · facility capacities honoured</div>'
            f'<div class="box"><b>Recommendation</b><br>{html.escape(res["summary"])}<br><small>{html.escape(res["reason"])}</small></div>'
            f'<div class="grid">{_svg_bars(xs, [r["EUR"]/eur_k for r in rows], col, "Ultimate recovery vs wells", "GSm³" if phase=="gas" else "MSm³", mark=rec)}'
            f'{_svg_bars(xs, [r["Plateau rate"]/k for r in rows], col, "Plateau rate vs wells", unit, mark=rec)}</div>'
            f'<table><tr>{"".join(f"<th>{h}</th>" for h in heads)}</tr>{body}</table>'
            + (f'<p><b>Tank recovery targets in the model</b></p><ul>{tt}</ul>' if tt else '')
            + '<p><small>Screening estimate from the FieldNet quasi-steady forecast (tank material balance + network solve). Plateau = longest period at or above 90 % of peak rate. '
              'A constraint counts as binding when its margin is below 2 % of its limit. Not a reservoir-simulation result.</small></p></body></html>')
