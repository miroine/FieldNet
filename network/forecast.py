import copy
from datetime import datetime
from solver.steady_state import solve_network

DAYS_PER_YEAR=365.25

def _days(date0,date1):
    return (datetime.fromisoformat(str(date1))-datetime.fromisoformat(str(date0))).days

def apply_events(nodes, edges, events, date):
    n=copy.deepcopy(nodes); e=copy.deepcopy(edges); d=datetime.fromisoformat(str(date))
    for ev in sorted(events or [], key=lambda x:x.get('date','')):
        if datetime.fromisoformat(str(ev['date']))>d: continue
        target=ev.get('target_id'); field=ev.get('field'); value=ev.get('value')
        obj=next((x for x in n if x['id']==target),None) or next((x for x in e if x['id']==target),None)
        if obj is None: continue
        if field.startswith('params.'):
            obj.setdefault('params',{})[field.split('.',1)[1]]=value
        else: obj[field]=value
    return n,e

def run_forecast(nodes, edges, start_date, years=5, step_days=30, events=None, depletion=None):
    base=copy.deepcopy(nodes); dep=depletion or {}; state={}
    for n in base:
        if n['kind']=='well':
            p=n.get('params',{}); state[n['id']]={'pr':float(p.get('reservoir_pressure_bar',200.0)),'cum_liq':0.0}
    rows=[]; well_rows=[]; constraint_rows=[]; t=0
    horizon_days=max(int(round(years*DAYS_PER_YEAR)),0)
    while t <= horizon_days:
        dt_days=min(step_days, max(horizon_days-t, 0))
        date=(datetime.fromisoformat(str(start_date)) + __import__('datetime').timedelta(days=t)).date().isoformat()
        nn,ee=apply_events(base,edges,events,date)
        for n in nn:
            if n['kind']=='well' and n['id'] in state:
                n.setdefault('params',{})['reservoir_pressure_bar']=state[n['id']]['pr']
                if not n['params'].get('available',True): n['params']['pi_m3d_bar']=0.0
        try: result=solve_network(nn,ee); p,q,info,details=result
        except Exception as exc:
            rows.append({'Date':date,'Day':t,'Total liquid [m3/d]':0.0,'Oil [m3/d]':0.0,'Water [m3/d]':0.0,'Gas [Sm3/d]':0.0,'Cumulative liquid [m3]':sum(s['cum_liq'] for s in state.values()),'Violations':0,'Converged':False,'Message':str(exc)}); t+=step_days; continue
        tl=oil=wat=gas=0.0
        for n in nn:
            if n['kind']!='well' or n['id'] not in details: continue
            dd=details[n['id']]; prm=n.get('params',{}); rate=max(float(dd['liquid_rate_m3d']),0.0); avail=float(prm.get('availability_factor',1.0)); rate*=max(0,min(avail,1))
            wc=float(prm.get('water_cut',0.0)); gor=float(prm.get('gor_sm3sm3',0.0)); o=rate*(1-wc); w=rate*wc; g=o*gor
            tl+=rate; oil+=o; wat+=w; gas+=g
            state[n['id']]['cum_liq'] += rate*dt_days
            cfg=dep.get(n['id'],{}); decline=float(cfg.get('pressure_decline_bar_per_1000m3',prm.get('pressure_decline_bar_per_1000m3',0.03)))
            support=float(cfg.get('pressure_support_bar_per_day',prm.get('pressure_support_bar_per_day',0.0)))
            state[n['id']]['pr']=max(float(cfg.get('min_reservoir_pressure_bar',prm.get('min_reservoir_pressure_bar',20.0))), state[n['id']]['pr']-decline*(rate*dt_days/1000.0)+support*dt_days)
            well_rows.append({'Date':date,'Well':n['name'],'Well ID':n['id'],'Liquid [m3/d]':rate,'Oil [m3/d]':o,'Water [m3/d]':w,'Gas [Sm3/d]':g,'Reservoir pressure [bar]':state[n['id']]['pr'],'WHP [bar]':dd['whp_bar'],'BHP [bar]':dd['bhp_bar'],'Cumulative liquid [m3]':state[n['id']]['cum_liq']})
        for c in info.get('constraints',[]): constraint_rows.append({'Date':date,**c})
        rows.append({'Date':date,'Day':t,'Total liquid [m3/d]':tl,'Oil [m3/d]':oil,'Water [m3/d]':wat,'Gas [Sm3/d]':gas,'Cumulative liquid [m3]':sum(s['cum_liq'] for s in state.values()),'Violations':info.get('violations',0),'Converged':info.get('success',False),'Message':info.get('message','')})
        t+=step_days
    return {'field':rows,'wells':well_rows,'constraints':constraint_rows,'final_state':state}
