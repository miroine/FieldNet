"""FieldNet v8 control/equipment helpers. Screening-level engineering models."""
import math

def control_valve_dp_bar(q_m3d, cv, rho_kgm3=850.0, opening=1.0):
    eff=max(float(cv)*max(min(float(opening),1.0),0.01),1e-6)
    q_m3h=float(q_m3d)/24.0
    return (q_m3h*abs(q_m3h)/(eff*eff)) * max(float(rho_kgm3),1.0)/1000.0

def compressor_map_ratio(q_gas_sm3d, rated_rate_sm3d, design_ratio, speed_fraction=1.0, min_ratio=1.0):
    """Simple normalized compressor map surrogate for screening studies."""
    r=max(float(rated_rate_sm3d),1.0); s=max(min(float(speed_fraction),1.2),0.2)
    phi=abs(float(q_gas_sm3d))/(r*s)
    head_factor=max(0.0,1.0-0.35*phi*phi)
    return max(float(min_ratio), 1.0+(float(design_ratio)-1.0)*s*s*head_factor)

def controller_opening(measured, setpoint, action='reverse', gain=0.04, bias=0.5):
    err=float(setpoint)-float(measured)
    sign=-1.0 if action=='reverse' else 1.0
    return max(0.02,min(1.0,float(bias)+sign*float(gain)*err))
