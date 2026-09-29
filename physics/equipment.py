import math
from physics.units import m3d_to_m3s, bar_to_pa

def pump_head_bar(q_m3d, shutoff_head_bar=35.0, rated_rate_m3d=1500.0, min_head_bar=0.0):
    """Simple quadratic pump curve: head falls from shutoff head to zero near rated flow."""
    q=max(abs(float(q_m3d)),0.0); qr=max(float(rated_rate_m3d),1e-9)
    return max(float(min_head_bar), float(shutoff_head_bar)*(1.0-(q/qr)**2))

def pump_power_kw(q_m3d, head_bar, rho_kgm3=850.0, efficiency=0.75):
    q_m3s=m3d_to_m3s(abs(float(q_m3d))); dp_pa=bar_to_pa(max(float(head_bar),0.0))
    return q_m3s*dp_pa/max(float(efficiency),1e-6)/1000.0

def compressor_discharge_bar(p_suction_bar, ratio=1.8, max_discharge_bar=250.0):
    return min(max(float(p_suction_bar),0.01)*max(float(ratio),1.0), float(max_discharge_bar))

def compressor_power_kw(qgas_sm3d, p_suction_bar, p_discharge_bar, efficiency=0.75, temperature_k=323.15, k=1.28, z=0.9):
    """Screening ideal-gas polytropic power from standard gas rate. Not a compressor-map model."""
    qs=m3d_to_m3s(max(abs(float(qgas_sm3d)),0.0))
    # approximate standard density for SG 0.7 folded into volumetric conversion; intentionally screening-level
    rho_std=0.84; mdot=qs*rho_std
    r_spec=8.314/0.0202
    pr=max(float(p_discharge_bar)/max(float(p_suction_bar),1e-6),1.0)
    if pr<=1: return 0.0
    work=(k/(k-1.0))*r_spec*float(temperature_k)/max(float(efficiency),1e-6)*(pr**((k-1.0)/k)-1.0)
    return mdot*work/1000.0
