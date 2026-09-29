"""FieldNet v25 reduced-order reservoir-network coupling.

Planning-level material balance: communicating tanks, pressure-dependent aquifer
support, explicit injector connectivity and auditable voidage accounting.
Canonical units are bar, m3 and days.
"""
from dataclasses import dataclass, asdict
from typing import Dict, Iterable, List, Mapping, Sequence
import copy

from network.reservoir import ReservoirTank, tank_from_dict

APPLICATION = "FieldNet v25"

@dataclass(frozen=True)
class AquiferSpec:
    tank_id: str
    productivity_m3d_bar: float = 0.0
    reference_pressure_bar: float = 250.0
    max_influx_m3d: float = 1.0e30

@dataclass(frozen=True)
class CommunicationLink:
    tank_a: str
    tank_b: str
    transmissibility_m3d_bar: float
    max_transfer_m3d: float = 1.0e30

@dataclass(frozen=True)
class InjectorConnection:
    injector_id: str
    tank_id: str
    weight: float = 1.0


def validate_model(tanks: Mapping[str, ReservoirTank], aquifers=(), links=(), connections=()):
    ids=set(tanks)
    errors=[]
    for a in aquifers:
        if a.tank_id not in ids: errors.append(f"Aquifer target {a.tank_id} does not exist")
        if a.productivity_m3d_bar < 0 or a.max_influx_m3d < 0: errors.append(f"Aquifer {a.tank_id} has negative capacity")
    for l in links:
        if l.tank_a not in ids or l.tank_b not in ids: errors.append(f"Communication link {l.tank_a}-{l.tank_b} references unknown tank")
        if l.tank_a == l.tank_b: errors.append(f"Communication link {l.tank_a} is a self-link")
        if l.transmissibility_m3d_bar < 0 or l.max_transfer_m3d < 0: errors.append(f"Communication link {l.tank_a}-{l.tank_b} has negative capacity")
    for c in connections:
        if c.tank_id not in ids: errors.append(f"Injector {c.injector_id} target {c.tank_id} does not exist")
        if c.weight < 0: errors.append(f"Injector {c.injector_id} has negative connectivity weight")
    if errors: raise ValueError("; ".join(errors))
    return True


def allocate_connected_injection(injector_volumes_m3: Mapping[str,float], connections: Sequence[InjectorConnection], tank_ids: Iterable[str]):
    out={k:0.0 for k in tank_ids}
    byinj={}
    for c in connections: byinj.setdefault(c.injector_id,[]).append(c)
    for iid, vol in injector_volumes_m3.items():
        vol=max(float(vol),0.0); cs=byinj.get(iid,[]); sw=sum(max(c.weight,0.0) for c in cs)
        if sw <= 0: continue
        for c in cs: out[c.tank_id]+=vol*max(c.weight,0.0)/sw
    return out


def _aquifer_volume(tank: ReservoirTank, spec: AquiferSpec, dt_days: float):
    draw=max(float(spec.reference_pressure_bar)-float(tank.pressure_bar),0.0)
    rate=min(float(spec.productivity_m3d_bar)*draw,float(spec.max_influx_m3d))
    return max(rate,0.0)*max(float(dt_days),0.0)


def step_coupled_tanks(tanks: Mapping[str, ReservoirTank], withdrawals_m3: Mapping[str,float],
                       direct_injection_m3: Mapping[str,float]|None=None, dt_days: float=1.0,
                       aquifers: Sequence[AquiferSpec]=(), links: Sequence[CommunicationLink]=()):
    """Advance all tanks simultaneously from the same pre-step pressure state.

    Communication is conservative: a positive transfer A->B is withdrawal from A
    and support to B. Pressure is updated from net voidage / PVct.
    """
    direct_injection_m3=direct_injection_m3 or {}
    validate_model(tanks,aquifers,links,())
    pre={k:float(t.pressure_bar) for k,t in tanks.items()}
    aq={k:0.0 for k in tanks}
    for a in aquifers: aq[a.tank_id]+=_aquifer_volume(tanks[a.tank_id],a,dt_days)
    comm_in={k:0.0 for k in tanks}; comm_out={k:0.0 for k in tanks}; transfers=[]
    for l in links:
        pa,pb=pre[l.tank_a],pre[l.tank_b]
        if pa >= pb: src,dst,dp=l.tank_a,l.tank_b,pa-pb
        else: src,dst,dp=l.tank_b,l.tank_a,pb-pa
        rate=min(max(l.transmissibility_m3d_bar,0.0)*dp,max(l.max_transfer_m3d,0.0))
        vol=rate*max(float(dt_days),0.0)
        comm_out[src]+=vol; comm_in[dst]+=vol
        transfers.append({'from':src,'to':dst,'delta_p_bar':dp,'rate_m3d':rate,'volume_m3':vol})
    ledger=[]
    for tid,t in tanks.items():
        w=max(float(withdrawals_m3.get(tid,0.0)),0.0)
        inj=max(float(direct_injection_m3.get(tid,0.0)),0.0)
        support=inj+aq[tid]+comm_in[tid]
        effective_withdrawal=w+comm_out[tid]
        net=effective_withdrawal-support  # signed; support may repressurize
        capacity=max(float(t.pore_volume_m3)*float(t.total_compressibility_1bar),1e-12)
        dp=-net/capacity
        pnew=max(float(t.min_pressure_bar),pre[tid]+dp)
        actual_dp=pnew-pre[tid]
        t.pressure_bar=pnew
        t.cumulative_withdrawal_m3 += w
        t.cumulative_injection_m3 += inj
        ledger.append({'tank_id':tid,'pressure_before_bar':pre[tid],'pressure_after_bar':pnew,
                       'pressure_change_bar':actual_dp,'withdrawal_m3':w,'injection_m3':inj,
                       'aquifer_m3':aq[tid],'communication_in_m3':comm_in[tid],
                       'communication_out_m3':comm_out[tid],'net_voidage_m3':net,
                       'capacity_m3_per_bar':capacity})
    return {'application':APPLICATION,'ledger':ledger,'transfers':transfers,
            'communication_balance_m3':sum(comm_in.values())-sum(comm_out.values())}


def clone_tanks(tanks):
    return {t['id']:tank_from_dict(copy.deepcopy(t)) if isinstance(t,dict) else copy.deepcopy(t) for t in tanks}
