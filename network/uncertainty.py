"""FieldNet v29.1 uncertainty and Monte-Carlo validation layer.

Planning-level uncertainty orchestration around deterministic field-development scenarios.
Sampling never changes hydraulic equations; every realization is explicit and reproducible.
"""
from __future__ import annotations
import copy, math
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Iterable
import numpy as np
from scipy.stats import norm, spearmanr
from network.field_development import DevelopmentScenario, run_development_scenario

APPLICATION = "FieldNet v29.1"

@dataclass(frozen=True)
class UncertainParameter:
    name: str
    path: str
    distribution: str = "triangular"
    low: float = 0.8
    mode: float = 1.0
    high: float = 1.2
    mean: float = 1.0
    std: float = 0.1
    target_id: str | None = None
    operation: str = "multiply"  # multiply | set
    physical_min: float | None = None
    physical_max: float | None = None
    bound_policy: str = "clip"  # clip | reject

    def validate(self):
        d=self.distribution.lower()
        if d not in {"uniform","triangular","normal","lognormal"}: raise ValueError(f"Unsupported distribution: {self.distribution}")
        if self.operation not in {"multiply","set"}: raise ValueError("operation must be multiply or set")
        if d in {"uniform","triangular"} and self.high < self.low: raise ValueError(f"{self.name}: high must be >= low")
        if d=="triangular" and not (self.low <= self.mode <= self.high): raise ValueError(f"{self.name}: mode must lie within low/high")
        if d in {"normal","lognormal"} and self.std < 0: raise ValueError(f"{self.name}: std must be >= 0")
        if d=="lognormal" and self.mean <= 0: raise ValueError(f"{self.name}: lognormal mean must be > 0")
        if self.physical_min is not None and self.physical_max is not None and self.physical_max < self.physical_min: raise ValueError(f"{self.name}: physical_max must be >= physical_min")
        if self.bound_policy not in {"clip","reject"}: raise ValueError("bound_policy must be clip or reject")

@dataclass
class MonteCarloConfig:
    samples: int = 100
    seed: int = 1701
    method: str = "lhs"  # lhs | random
    parameters: list[UncertainParameter] = field(default_factory=list)
    correlation: list[list[float]] | None = None
    def validate(self):
        if not (1 <= int(self.samples) <= 10000): raise ValueError("samples must be 1..10000")
        if self.method not in {"lhs","random"}: raise ValueError("method must be lhs or random")
        names=[p.name for p in self.parameters]
        if len(names) != len(set(names)): raise ValueError("uncertain parameter names must be unique")
        for p in self.parameters: p.validate()
        if self.correlation is not None:
            c=np.asarray(self.correlation,dtype=float); n=len(self.parameters)
            if c.shape != (n,n): raise ValueError("correlation matrix shape must match parameters")
            if not np.all(np.isfinite(c)): raise ValueError("correlation matrix must contain finite values")
            if np.any(np.abs(c)>1+1e-12): raise ValueError("correlation coefficients must lie within [-1, 1]")
            if not np.allclose(c,c.T,atol=1e-10) or not np.allclose(np.diag(c),1.0,atol=1e-10): raise ValueError("correlation matrix must be symmetric with unit diagonal")
            if np.min(np.linalg.eigvalsh(c)) < -1e-8: raise ValueError("correlation matrix must be positive semidefinite")

def _unit_samples(cfg: MonteCarloConfig) -> np.ndarray:
    cfg.validate(); n,m=int(cfg.samples),len(cfg.parameters); rng=np.random.default_rng(int(cfg.seed))
    if m==0: return np.empty((n,0))
    if cfg.method=="lhs":
        u=np.empty((n,m))
        for j in range(m): u[:,j]=(rng.permutation(n)+rng.random(n))/n
    else: u=rng.random((n,m))
    if cfg.correlation is not None and m>1:
        z=norm.ppf(np.clip(u,1e-12,1-1e-12)); c=np.asarray(cfg.correlation,float)
        vals,vecs=np.linalg.eigh(c); root=vecs@np.diag(np.sqrt(np.clip(vals,0,None)))@vecs.T
        u=norm.cdf(z@root.T)
    return np.clip(u,1e-12,1-1e-12)

def _ppf(p: UncertainParameter, u: np.ndarray) -> np.ndarray:
    d=p.distribution.lower()
    if d=="uniform": a=p.low+(p.high-p.low)*u
    elif d=="triangular":
        if p.high==p.low: a=np.full_like(u,p.low,dtype=float)
        else:
            c=(p.mode-p.low)/(p.high-p.low); a=np.where(u<c,p.low+np.sqrt(u*(p.high-p.low)*(p.mode-p.low)),p.high-np.sqrt((1-u)*(p.high-p.low)*(p.high-p.mode)))
    elif d=="normal": a=p.mean+p.std*norm.ppf(u)
    else:
        sigma=math.sqrt(math.log(1+(p.std/p.mean)**2)) if p.std>0 else 0.0
        mu=math.log(p.mean)-0.5*sigma*sigma; a=np.exp(mu+sigma*norm.ppf(u))
    if p.bound_policy=="clip":
        if p.physical_min is not None: a=np.maximum(a,p.physical_min)
        if p.physical_max is not None: a=np.minimum(a,p.physical_max)
    return a

def sample_parameters(cfg: MonteCarloConfig) -> list[dict[str,float]]:
    u=_unit_samples(cfg); cols=[_ppf(p,u[:,j]) for j,p in enumerate(cfg.parameters)]
    return [{p.name:float(cols[j][i]) for j,p in enumerate(cfg.parameters)} for i in range(cfg.samples)]

def _find_target(nodes, edges, target_id):
    if target_id is None: return None
    for obj in [*nodes,*edges]:
        if str(obj.get("id"))==str(target_id): return obj
    raise ValueError(f"Unknown uncertainty target_id: {target_id}")

def _get_nested(obj: dict, path: str):
    cur=obj
    for part in path.split("."): cur=cur[part]
    return cur

def _set_nested(obj: dict, path: str, value: Any):
    parts=path.split("."); cur=obj
    for part in parts[:-1]: cur=cur.setdefault(part,{})
    cur[parts[-1]]=value

def _bounded_value(p: UncertainParameter, value: float) -> float:
    if p.physical_min is not None and value < p.physical_min:
        if p.bound_policy=="reject": raise ValueError(f"{p.name}: sampled value {value:g} below physical_min {p.physical_min:g}")
        value=p.physical_min
    if p.physical_max is not None and value > p.physical_max:
        if p.bound_policy=="reject": raise ValueError(f"{p.name}: sampled value {value:g} above physical_max {p.physical_max:g}")
        value=p.physical_max
    return float(value)

def apply_sample(nodes, edges, scenario: DevelopmentScenario, parameters: Iterable[UncertainParameter], sample: dict[str,float]):
    nn,ee,ss=copy.deepcopy(nodes),copy.deepcopy(edges),copy.deepcopy(scenario)
    for p in parameters:
        val=float(sample[p.name])
        if p.path.startswith("scenario."):
            attr=p.path.split(".",1)[1]
            if not hasattr(ss,attr): raise ValueError(f"{p.name}: unknown scenario attribute {attr}")
            base=getattr(ss,attr); final=_bounded_value(p, float(base)*val if p.operation=="multiply" else val); setattr(ss,attr,final); continue
        target=_find_target(nn,ee,p.target_id)
        if target is None: raise ValueError(f"{p.name}: target_id required for path {p.path}")
        try: base=_get_nested(target,p.path)
        except (KeyError,TypeError) as exc: raise ValueError(f"{p.name}: unknown parameter path {p.path}") from exc
        final=_bounded_value(p, float(base)*val if p.operation=="multiply" else val)
        _set_nested(target,p.path,final)
    return nn,ee,ss

def percentile_summary(values: Iterable[float]) -> dict[str,float]:
    a=np.asarray(list(values),dtype=float); a=a[np.isfinite(a)]
    if not len(a): return {"P10":float("nan"),"P50":float("nan"),"P90":float("nan"),"mean":float("nan"),"std":float("nan"),"count":0}
    return {"P90":float(np.percentile(a,10)),"P50":float(np.percentile(a,50)),"P10":float(np.percentile(a,90)),"mean":float(np.mean(a)),"std":float(np.std(a,ddof=1)) if len(a)>1 else 0.0,"count":int(len(a))}

def percentile_convergence(runs: list[dict], metric: str, checkpoints: Iterable[int]|None=None) -> list[dict]:
    good=[r for r in runs if r.get("success") and np.isfinite(float(r.get(metric,float("nan"))))]
    if not good: return []
    n=len(good)
    cps=sorted(set(int(x) for x in (checkpoints or [10,25,50,100,250,500,1000,n]) if 1 <= int(x) <= n))
    if n not in cps: cps.append(n)
    out=[]
    vals=[float(r[metric]) for r in good]
    for k in cps: out.append({"samples":k,**percentile_summary(vals[:k])})
    return out

def sensitivity_summary(runs: list[dict], parameters: Iterable[UncertainParameter], metric: str) -> list[dict]:
    good=[r for r in runs if r.get("success") and np.isfinite(float(r.get(metric,float("nan"))))]
    if len(good)<3: return []
    y=np.asarray([float(r[metric]) for r in good]); out=[]
    for p in parameters:
        x=np.asarray([float(r[p.name]) for r in good])
        rho,pval=spearmanr(x,y)
        out.append({"parameter":p.name,"spearman_rho":float(rho) if np.isfinite(rho) else 0.0,"p_value":float(pval) if np.isfinite(pval) else 1.0,"abs_rho":abs(float(rho)) if np.isfinite(rho) else 0.0})
    return sorted(out,key=lambda z:z["abs_rho"],reverse=True)

def failure_diagnostics(runs: list[dict], parameters: Iterable[UncertainParameter]) -> dict:
    total=len(runs); failed=[r for r in runs if not r.get("success")]; good=[r for r in runs if r.get("success")]
    errors={}
    for r in failed: errors[r.get("error","Unknown error")]=errors.get(r.get("error","Unknown error"),0)+1
    shifts=[]
    for p in parameters:
        if not failed or not good: continue
        allv=np.asarray([float(r[p.name]) for r in runs]); gv=np.asarray([float(r[p.name]) for r in good])
        scale=float(np.std(allv))
        z=(float(np.mean(gv))-float(np.mean(allv)))/scale if scale>0 else 0.0
        shifts.append({"parameter":p.name,"all_mean":float(np.mean(allv)),"successful_mean":float(np.mean(gv)),"mean_shift_sigma":z})
    frac=len(failed)/total if total else 0.0
    return {"failure_fraction":frac,"failure_causes":errors,"parameter_selection_shift":shifts,"survivor_bias_warning":bool(frac>=0.05 or any(abs(x["mean_shift_sigma"])>=0.25 for x in shifts))}

def run_monte_carlo(nodes, edges, scenario: DevelopmentScenario, config: MonteCarloConfig, forecast_runner=None, progress: Callable[[int,int],None]|None=None):
    config.validate(); samples=sample_parameters(config); runs=[]
    for i,sample in enumerate(samples):
        try:
            nn,ee,ss=apply_sample(nodes,edges,scenario,config.parameters,sample)
            kwargs={} if forecast_runner is None else {"forecast_runner":forecast_runner}
            r=run_development_scenario(nn,ee,ss,**kwargs); k=r["kpis"]
            row={"sample":i,"success":True,**sample,**{k0:k[k0] for k0 in ["cumulative_oil_m3","cumulative_liquid_m3","final_oil_m3d","final_liquid_m3d","convergence_fraction","constraint_events"]}}
        except Exception as exc: row={"sample":i,"success":False,**sample,"error":str(exc)}
        runs.append(row)
        if progress: progress(i+1,len(samples))
    good=[r for r in runs if r.get("success")]
    metric_names=["cumulative_oil_m3","cumulative_liquid_m3","final_oil_m3d","final_liquid_m3d"]
    metrics={m:percentile_summary(r[m] for r in good) for m in metric_names} if good else {}
    convergence={m:percentile_convergence(runs,m) for m in metric_names} if good else {}
    sensitivity={m:sensitivity_summary(runs,config.parameters,m) for m in metric_names} if good else {}
    failures=failure_diagnostics(runs,config.parameters)
    return {"application":APPLICATION,"seed":config.seed,"method":config.method,"requested_samples":config.samples,"successful_samples":len(good),"failed_samples":len(runs)-len(good),"success_fraction":len(good)/len(runs) if runs else 0.0,"parameters":[asdict(p) for p in config.parameters],"correlation":config.correlation,"runs":runs,"metrics":metrics,"percentile_convergence":convergence,"sensitivity":sensitivity,"failure_diagnostics":failures}
