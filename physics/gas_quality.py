"""Gas-quality screening for CO2 / H2S content: partial pressures, CO2 corrosion rate and sour-service flag.

Screening only - not a corrosion design. CO2: de Waard-Milliams (1975) uncorrected rate (no scale, pH, glycol or inhibitor credit), which is
conservative. Sour service: ISO 15156 / NACE MR0175 threshold pH2S >= 0.0003 MPa (0.003 bar) at total pressure above 0.4 MPa.
Partial pressure = total pressure x gas-phase mole fraction (ideal; fugacity coefficient ignored); fluids with no free gas (all CO2 dissolved
in oil/water) are still reported because the brine can be acidic."""
from __future__ import annotations
import math

SOUR_PH2S_BAR = 0.003
SOUR_MIN_TOTAL_BAR = 4.0


def partial_pressure_bar(p_bar, mole_fraction): return max(float(p_bar), 0.0) * max(float(mole_fraction), 0.0)


def dewaard_milliams_mm_per_year(pco2_bar, t_c):
    """log10(CR [mm/y]) = 7.96 - 2320/(T+273) - 5.55e-3 T + 0.67 log10(pCO2 [bar])  (T in degC)."""
    if pco2_bar <= 0: return 0.0
    return 10 ** (7.96 - 2320.0 / (t_c + 273.0) - 5.55e-3 * t_c + 0.67 * math.log10(pco2_bar))


def severity(rate_mm_y):
    if rate_mm_y < 0.1: return 'low'
    if rate_mm_y < 1.0: return 'moderate'
    if rate_mm_y < 5.0: return 'high'
    return 'severe'


def screen(p_bar, t_c, co2=0.0, h2s=0.0):
    pc = partial_pressure_bar(p_bar, co2); ph = partial_pressure_bar(p_bar, h2s); cr = dewaard_milliams_mm_per_year(pc, t_c)
    sour = ph >= SOUR_PH2S_BAR and p_bar >= SOUR_MIN_TOTAL_BAR
    return {'pco2_bar': pc, 'ph2s_bar': ph, 'co2_rate_mm_y': cr, 'co2_severity': severity(cr) if pc > 0 else 'n/a', 'sour_service': sour,
            'co2_to_h2s_ratio': (pc / ph) if ph > 0 else None}


def screen_network(nodes, edges, results, thermal=None):
    """Rows for every well and pipeline with a non-zero CO2/H2S content (``params['pvt']``), at the solved pressure / temperature."""
    p, q, info, det = results; rows = []; te = ((thermal or {}).get('edge') or {}); tn = ((thermal or {}).get('node_temperature_c') or {})
    for n in nodes:
        pv = (n.get('params') or {}).get('pvt') or {}
        if n.get('kind') != 'well' or not (float(pv.get('co2') or 0) or float(pv.get('h2s') or 0)): continue
        d = det.get(n['id']) or {}; prm = n.get('params') or {}
        # worst case along a well is at the bottom hole (highest pressure and temperature)
        pb = float(d.get('bhp_bar') or 0.0); tb = float(prm.get('bottomhole_temperature_c', prm.get('temperature_c', 70.0)))
        rows.append({'Element': n.get('name') or n['id'], 'Type': 'well (bottom hole)', 'P [bar]': pb, 'T [C]': tb, **_fmt(screen(pb, tb, float(pv.get('co2') or 0), float(pv.get('h2s') or 0)))})
        pw = float(d.get('whp_bar') or 0.0); tw = float(d.get('wellhead_temperature_c') or prm.get('temperature_c', 50.0))
        rows.append({'Element': n.get('name') or n['id'], 'Type': 'well (wellhead)', 'P [bar]': pw, 'T [C]': tw, **_fmt(screen(pw, tw, float(pv.get('co2') or 0), float(pv.get('h2s') or 0)))})
    for e in edges:
        pv = (e.get('params') or {}).get('pvt') or {}
        if e.get('kind', 'pipeline') != 'pipeline' or not (float(pv.get('co2') or 0) or float(pv.get('h2s') or 0)): continue
        if abs(float(q.get(e['id'], 0.0))) < 1e-9: continue
        pu = max(float(p.get(e['source'], 0.0)), float(p.get(e['target'], 0.0))); t = (te.get(e['id']) or {}).get('t_in', float((e.get('params') or {}).get('temperature_c', 50.0)))
        rows.append({'Element': e.get('name') or e['id'], 'Type': 'flowline (inlet)', 'P [bar]': pu, 'T [C]': t, **_fmt(screen(pu, t, float(pv.get('co2') or 0), float(pv.get('h2s') or 0)))})
    return rows


def _fmt(s):
    return {'pCO2 [bar]': s['pco2_bar'], 'pH2S [bar]': s['ph2s_bar'], 'CO2 corrosion [mm/y]': s['co2_rate_mm_y'], 'Severity': s['co2_severity'], 'Sour service': 'YES' if s['sour_service'] else 'no'}
