"""Short result strings shown on each canvas node after a solve (pure Python, no Streamlit)."""
from __future__ import annotations


def canvas_labels(nodes, results):
    if not results or not results[0]: return {}
    p, q, info, d = results; info = info or {}; inline = info.get('inline_equipment') or {}; out = {}
    inj = info.get('injector_rates') or {}
    for n in nodes:
        nid = n['id']; k = n.get('kind'); dd = (d or {}).get(nid)
        if dd:
            st = dd.get('status')
            if st in ('shut_in', 'dead', 'shut_in_below_min_rate'): out[nid] = f"{st.replace('_', ' ')} · {p.get(nid, 0):.1f} bar"
            else:
                wc = dd['water_rate_m3d'] / max(dd['liquid_rate_m3d'], 1e-9)
                out[nid] = f"{dd['liquid_rate_m3d']:,.0f} m³/d · WC {wc:.0%}" + (' · limited' if st == 'rate_limited' else '')
        elif nid in inline:
            r = inline[nid]
            if r.get('dp_bar') is not None: out[nid] = f"ΔP {r['dp_bar']:+.1f} bar · {abs(r.get('rate_m3d') or 0):,.0f} m³/d"
        elif k in ('water_injector', 'gas_injector', 'injector') and nid in inj: out[nid] = f"{inj[nid]:,.0f} m³/d injected"
        elif nid in p and k != 'reservoir': out[nid] = f"{p[nid]:.1f} bar"
    return out
