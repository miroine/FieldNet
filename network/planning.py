import copy, math
from network.forecast import run_forecast

def decline_multiplier(days, model='exponential', annual_decline=0.12, b=0.7):
    t=max(float(days),0.0)/365.25; d=max(float(annual_decline),0.0)
    if model=='hyperbolic': return (1.0+max(b,1e-9)*d*t)**(-1.0/max(b,1e-9))
    if model=='harmonic': return 1.0/(1.0+d*t)
    return math.exp(-d*t)

def tank_pressure(initial_bar, cumulative_m3, pore_volume_m3=2e6, total_compressibility_1bar=8e-5, aquifer_strength=0.0, injected_m3=0.0, min_bar=20.0):
    pv=max(float(pore_volume_m3),1.0); ct=max(float(total_compressibility_1bar),1e-9)
    net=max(float(cumulative_m3)-float(injected_m3)*max(float(aquifer_strength),0.0),0.0)
    return max(float(min_bar), float(initial_bar)-net/(pv*ct))

def apply_decline_to_forecast(fc, configs):
    out=copy.deepcopy(fc)
    for r in out.get('wells',[]):
        cfg=configs.get(r['Well ID'],{}); m=decline_multiplier((__import__('datetime').datetime.fromisoformat(r['Date'])-__import__('datetime').datetime.fromisoformat(out['field'][0]['Date'])).days,cfg.get('model','exponential'),cfg.get('annual_decline',0.0),cfg.get('b',0.7))
        for k in ('Liquid [m3/d]','Oil [m3/d]','Water [m3/d]','Gas [Sm3/d]'): r[k]*=m
    return out

def _run_one_scenario(args):
    nodes,edges,start_date,years,step_days,s=args
    fc=run_forecast(copy.deepcopy(nodes),copy.deepcopy(edges),start_date,years,step_days,s.get('events',[]),s.get('depletion',{}))
    for r in fc['field']: r['Scenario']=s.get('name','Scenario')
    return {'name':s.get('name','Scenario'),'forecast':fc}

def run_scenarios(nodes,edges,start_date,years,step_days,scenarios,workers=1):
    """``workers>1`` evaluates scenarios in parallel processes (same results/order as serial)."""
    scenarios=list(scenarios)
    if workers and workers>1 and len(scenarios)>1:
        from network.uncertainty import parallel_map
        return parallel_map(_run_one_scenario,[(nodes,edges,start_date,years,step_days,s) for s in scenarios],workers)
    return [_run_one_scenario((nodes,edges,start_date,years,step_days,s)) for s in scenarios]

def uncertainty_cases(base_depletion, low_factor=0.75, high_factor=1.25):
    def scaled(f):
        d=copy.deepcopy(base_depletion)
        for cfg in d.values():
            if 'pressure_decline_bar_per_1000m3' in cfg: cfg['pressure_decline_bar_per_1000m3']*=f
        return d
    return [{'name':'Low depletion','depletion':scaled(low_factor)},{'name':'Base','depletion':copy.deepcopy(base_depletion)},{'name':'High depletion','depletion':scaled(high_factor)}]
