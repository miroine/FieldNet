"""FieldNet v29 development-planning compiler.

Deterministic project/resource scheduling wrapped around the existing quasi-steady
field-development forecast.  It is not a drilling simulator or project economics model.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, timedelta
import copy
from typing import Any, Iterable
from network.field_development import DevelopmentEvent, DevelopmentScenario, run_development_scenario

APPLICATION = "FieldNet v29"
RIG_TASKS = {"drill_well", "workover"}

@dataclass(frozen=True)
class DevelopmentTask:
    id: str
    name: str
    task_type: str
    target_id: str
    earliest_start: str
    duration_days: int = 0
    predecessors: tuple[str, ...] = ()
    resource: str | None = None
    field: str | None = None
    value: Any = None
    description: str = ""

@dataclass
class DevelopmentPlan:
    name: str
    start_date: str
    years: float
    step_days: int = 30
    tasks: list[DevelopmentTask] = field(default_factory=list)
    depletion: dict[str, dict[str, float]] = field(default_factory=dict)


def _d(x: str) -> date: return date.fromisoformat(str(x))
def _iso(x: date) -> str: return x.isoformat()


def validate_plan(plan: DevelopmentPlan, nodes: list[dict], edges: list[dict]) -> None:
    if plan.years <= 0 or plan.step_days <= 0: raise ValueError("Plan years and step_days must be > 0")
    ids=[t.id for t in plan.tasks]
    if any(not x for x in ids) or len(ids)!=len(set(ids)): raise ValueError("Development task IDs must be unique and non-empty")
    targets={str(x.get('id')) for x in [*nodes,*edges]}
    known=set(ids)
    for t in plan.tasks:
        _d(t.earliest_start)
        if t.duration_days < 0: raise ValueError(f"Task {t.id}: duration_days must be >= 0")
        if t.target_id not in targets: raise ValueError(f"Task {t.id}: unknown target_id {t.target_id}")
        missing=[p for p in t.predecessors if p not in known]
        if missing: raise ValueError(f"Task {t.id}: unknown predecessor(s): {', '.join(missing)}")
        if t.id in t.predecessors: raise ValueError(f"Task {t.id}: cannot depend on itself")
    # explicit cycle check
    deps={t.id:set(t.predecessors) for t in plan.tasks}; done=set()
    while len(done)<len(deps):
        ready=[k for k,v in deps.items() if k not in done and v <= done]
        if not ready: raise ValueError("Development-plan dependency cycle detected")
        done.update(ready)


def compile_plan(plan: DevelopmentPlan, nodes: list[dict], edges: list[dict]) -> dict:
    """Resolve dependencies/resources and compile tasks to executable forecast events."""
    validate_plan(plan,nodes,edges)
    tasks={t.id:t for t in plan.tasks}; scheduled={}; resource_free={}; remaining=set(tasks)
    while remaining:
        ready=sorted((tasks[k] for k in remaining if all(p in scheduled for p in tasks[k].predecessors)), key=lambda t:(t.earliest_start,t.id))
        if not ready: raise ValueError("Development-plan dependency cycle detected")
        t=ready[0]; start=max(_d(plan.start_date),_d(t.earliest_start))
        if t.predecessors: start=max(start,max(_d(scheduled[p]['finish']) for p in t.predecessors))
        resource=t.resource or ("RIG-1" if t.task_type in RIG_TASKS else None)
        if resource: start=max(start,resource_free.get(resource,start))
        finish=start+timedelta(days=int(t.duration_days))
        if resource: resource_free[resource]=finish
        scheduled[t.id]={'task_id':t.id,'name':t.name,'task_type':t.task_type,'target_id':t.target_id,'start':_iso(start),'finish':_iso(finish),'duration_days':int(t.duration_days),'resource':resource or '', 'predecessors':list(t.predecessors)}
        remaining.remove(t.id)
    events=[]
    for t in plan.tasks:
        s=scheduled[t.id]; typ=t.task_type
        # Explicit field/value always wins; otherwise compile common production-development semantics.
        fld=t.field; val=t.value
        if fld is None:
            if typ in {'drill_well','tieback','commission','first_production'}: fld,val='params.available',True
            elif typ in {'abandon','shutdown'}: fld,val='params.available',False
            elif typ=='facility_expansion': fld,val='params.max_rate_m3d',t.value
            elif typ=='compression_start': fld,val='params.available',True
            else: continue
        # For drilling/tieback tasks, ensure the target is unavailable from plan start until finish.
        if typ in {'drill_well','tieback'}:
            events.append(DevelopmentEvent(plan.start_date,t.target_id,'params.available',False,f'{t.name}: pre-start unavailable'))
        events.append(DevelopmentEvent(s['finish'],t.target_id,fld,val,t.description or t.name))
    events.sort(key=lambda e:(e.date,e.target_id,e.field))
    return {'application':APPLICATION,'plan':plan.name,'schedule':list(scheduled.values()),'events':events}


def run_development_plan(nodes: list[dict], edges: list[dict], plan: DevelopmentPlan, forecast_runner=None) -> dict:
    compiled=compile_plan(plan,nodes,edges)
    scenario=DevelopmentScenario(plan.name,plan.start_date,plan.years,plan.step_days,compiled['events'],copy.deepcopy(plan.depletion),metadata={'source':'v26 development plan'})
    kwargs={}
    if forecast_runner is not None: kwargs['forecast_runner']=forecast_runner
    result=run_development_scenario(copy.deepcopy(nodes),copy.deepcopy(edges),scenario,**kwargs)
    result['application']=APPLICATION; result['development_plan']=compiled
    return result
