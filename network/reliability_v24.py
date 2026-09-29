"""FieldNet v29 reliability & availability planning layer.
Screening-level exponential failure/repair simulation around deterministic production forecasts.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
import copy, math
import numpy as np

@dataclass(frozen=True)
class ReliabilitySpec:
    target_id: str
    mtbf_days: float
    mttr_days: float
    planned_outages: tuple[tuple[int,int], ...] = ()
    redundancy_group: str = ""
    required_online: int = 1
    def validate(self):
        if not self.target_id: raise ValueError("target_id is required")
        if self.mtbf_days <= 0 or self.mttr_days <= 0: raise ValueError("MTBF and MTTR must be > 0")
        if self.required_online < 1: raise ValueError("required_online must be >= 1")
        for a,b in self.planned_outages:
            if a < 0 or b <= a: raise ValueError("planned outage must satisfy 0 <= start < end")

@dataclass
class ReliabilityStudy:
    years: float = 1.0
    step_days: int = 1
    realizations: int = 100
    seed: int = 2401
    specs: list[ReliabilitySpec] = field(default_factory=list)
    def validate(self):
        if self.years <= 0 or self.step_days <= 0 or self.realizations < 1: raise ValueError("invalid study horizon/step/realizations")
        for s in self.specs: s.validate()

def theoretical_availability(mtbf_days, mttr_days):
    if mtbf_days <= 0 or mttr_days <= 0: raise ValueError("MTBF and MTTR must be > 0")
    return mtbf_days/(mtbf_days+mttr_days)

def _planned_down(spec, day):
    return any(a <= day < b for a,b in spec.planned_outages)

def simulate_component(spec: ReliabilitySpec, days: int, step_days: int, rng):
    spec.validate(); up=True; remaining=0.0; out=[]; failures=0
    p_fail=1-math.exp(-step_days/spec.mtbf_days)
    for day in range(0, days, step_days):
        planned=_planned_down(spec, day)
        if planned:
            out.append(False); continue
        if up and rng.random() < p_fail:
            up=False; failures += 1; remaining=float(rng.exponential(spec.mttr_days))
        if not up:
            out.append(False); remaining -= step_days
            if remaining <= 0: up=True
        else: out.append(True)
    return np.asarray(out,dtype=bool), failures

def _group_effective(specs, states, i):
    groups={}
    effective={}
    for s in specs:
        if s.redundancy_group:
            groups.setdefault(s.redundancy_group,[]).append(s)
        else: effective[s.target_id]=bool(states[s.target_id][i])
    for _, members in groups.items():
        n=sum(bool(states[s.target_id][i]) for s in members); req=max(s.required_online for s in members)
        ok=n>=req
        for s in members: effective[s.target_id]=ok
    return effective

def run_reliability(study: ReliabilityStudy, base_rate_m3d: float=1.0):
    """Screening reliability simulation. Production proxy scales by critical availability.
    A caller may replace proxy rates with hydraulic re-solves in a future high-fidelity workflow.
    """
    study.validate(); days=max(1,int(round(study.years*365.25))); rng=np.random.default_rng(study.seed)
    realiz=[]
    for r in range(study.realizations):
        states={}; failures={}
        for s in study.specs:
            states[s.target_id], failures[s.target_id]=simulate_component(s,days,study.step_days,rng)
        nsteps=min((len(x) for x in states.values()), default=math.ceil(days/study.step_days))
        online=[]
        for i in range(nsteps):
            eff=_group_effective(study.specs,states,i)
            online.append(all(eff.values()) if eff else True)
        availability=float(np.mean(online)) if online else 1.0
        realiz.append({'realization':r,'availability':availability,'production_m3':base_rate_m3d*study.step_days*sum(online),'failures':sum(failures.values())})
    vals=np.array([x['availability'] for x in realiz],float)
    prod=np.array([x['production_m3'] for x in realiz],float)
    return {'application':'FieldNet v29','seed':study.seed,'realizations':realiz,'summary':{
        'mean_availability':float(vals.mean()),'p90_availability':float(np.quantile(vals,.10)),'p50_availability':float(np.quantile(vals,.50)),'p10_availability':float(np.quantile(vals,.90)),
        'mean_production_m3':float(prod.mean()),'mean_deferred_m3':float(base_rate_m3d*days-prod.mean()),'probability_below_90pct_availability':float(np.mean(vals<.9))},
        'study':{'years':study.years,'step_days':study.step_days,'realizations':study.realizations,'seed':study.seed,'specs':[asdict(s) for s in study.specs]}}
