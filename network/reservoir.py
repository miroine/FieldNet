"""FieldNet v11 coupled reservoir-tank utilities.
Screening material-balance models intended for integrated-network planning.
"""
from dataclasses import dataclass, asdict

@dataclass
class ReservoirTank:
    id: str
    name: str
    initial_pressure_bar: float = 250.0
    pressure_bar: float = 250.0
    pore_volume_m3: float = 2.0e6
    total_compressibility_1bar: float = 8.0e-5
    min_pressure_bar: float = 20.0
    aquifer_support_fraction: float = 0.0
    cumulative_withdrawal_m3: float = 0.0
    cumulative_injection_m3: float = 0.0
    def to_dict(self): return asdict(self)

def tank_from_dict(d):
    x=dict(d); x.setdefault('pressure_bar',x.get('initial_pressure_bar',250.0)); return ReservoirTank(**x)

def update_tank(tank, withdrawal_m3, injection_m3=0.0, aquifer_m3=0.0):
    """Update a tank using a transparent compressibility material-balance surrogate."""
    tank.cumulative_withdrawal_m3 += max(float(withdrawal_m3),0.0)
    tank.cumulative_injection_m3 += max(float(injection_m3),0.0)
    support=max(float(injection_m3),0.0)+max(float(aquifer_m3),0.0)
    net=max(float(withdrawal_m3)-support,0.0)
    capacity=max(tank.pore_volume_m3*tank.total_compressibility_1bar,1e-9)
    tank.pressure_bar=max(tank.min_pressure_bar,tank.pressure_bar-net/capacity)
    return tank.pressure_bar

def allocate_injection(total_m3, tank_ids, weights=None):
    ids=list(tank_ids); weights=weights or {k:1.0 for k in ids}
    positive={k:max(float(weights.get(k,0.0)),0.0) for k in ids}; s=sum(positive.values())
    if s<=0: return {k:0.0 for k in ids}
    return {k:max(float(total_m3),0.0)*positive[k]/s for k in ids}
