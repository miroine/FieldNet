import copy
from datetime import datetime
from solver.steady_state import solve_network


def solve_step(nodes, edges, guess=None, enforce_constraints=False):
    """One forecast timestep: warm-started from the previous step and, optionally,
    honouring facility capacity limits by pro-rata well choking."""
    if enforce_constraints:
        from solver.v21 import enforce_capacity_constraints
        (p,q,info,d),_,actions=enforce_capacity_constraints(nodes,edges,lambda ns,es,g: solve_network(ns,es,initial_guess=g),initial_guess=guess)
        info=dict(info); info['constraint_actions']=actions
        return p,q,info,d
    return solve_network(nodes,edges,initial_guess=guess)


def next_guess(p,q,info):
    return {'pressures':p,'flows':q,'well_rates':info.get('well_rates',{})}

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

def _step_rates(nn, details, info, tanks):
    """Instantaneous per-well and per-tank rates from one network solve."""
    wells={}; per_tank={k:{'oil':0.0,'wat':0.0,'gas':0.0,'winj':0.0,'ginj':0.0} for k in tanks}; winj=0.0
    from network.reservoir_mb import bg_rm3_sm3
    for n in nn:
        if n['kind']=='well' and n['id'] in details:
            dd=details[n['id']]; prm=n.get('params',{}); rate=max(float(dd['liquid_rate_m3d']),0.0)*max(0,min(float(prm.get('availability_factor',1.0)),1))
            wc=float(prm.get('water_cut',0.0)); gor=float(prm.get('gor_sm3sm3',0.0)); o=rate*(1-wc); w=rate*wc; g=o*gor
            wells[n['id']]={'liq':rate,'oil':o,'wat':w,'gas':g,'dd':dd,'prm':prm,'name':n.get('name',n['id'])}
            rid=prm.get('reservoir_id')
            if rid in tanks: v=per_tank[rid]; v['oil']+=o; v['wat']+=w; v['gas']+=g
        elif n['kind'] in ('water_injector','gas_injector','injector'):
            qi=float((info.get('injector_rates') or {}).get(n['id'],0.0)); rid=(n.get('params') or {}).get('reservoir_id')
            gas_inj=n['kind']=='gas_injector' or (n.get('params') or {}).get('injection_fluid')=='gas'
            if not gas_inj: winj+=qi
            if rid in tanks:
                if gas_inj: per_tank[rid]['ginj']+=qi/max(bg_rm3_sm3(tanks[rid].p,tanks[rid].t,tanks[rid].gas_sg),1e-6)
                else: per_tank[rid]['winj']+=qi
    return wells, per_tank, winj


def run_forecast(nodes, edges, start_date, years=5, step_days=30, events=None, depletion=None, enforce_constraints=False,
                 max_tank_dp_bar=None, max_substeps=24):
    """Quasi-steady life-of-field forecast.

    Each timestep: apply schedule events -> give linked wells their tank pressure ->
    solve the network (warm-started) -> deplete tanks by material balance (in-place volume
    and phase). Steps are sub-divided so no tank pressure moves more than
    ``max_tank_dp_bar`` (default max(2 bar, 3 % of pressure)) before the network is re-solved,
    so large timesteps cannot overshoot the depletion. Reported rates are averages over the
    step. Wells without a tank use the legacy per-well decline (bar per 1000 m3).
    """
    import copy as _copy
    from datetime import timedelta
    from network.reservoir_mb import tanks_from_nodes, apply_tank_links
    base=copy.deepcopy(nodes); dep=depletion or {}; state={}; guess=None
    tanks=tanks_from_nodes(base)
    for n in base:
        if n['kind']=='well':
            p=n.get('params',{}); state[n['id']]={'pr':float(p.get('reservoir_pressure_bar',200.0)),'cum_liq':0.0,'cum_oil':0.0,'cum_gas':0.0,'cum_wat':0.0}
    rows=[]; well_rows=[]; constraint_rows=[]; tank_rows=[]; t=0
    cum={'oil':0.0,'gas':0.0,'wat':0.0,'winj':0.0}
    horizon_days=max(int(round(years*DAYS_PER_YEAR)),0)
    t0=datetime.fromisoformat(str(start_date))

    def solve_now(nn0):
        nonlocal guess
        nn=_copy.deepcopy(nn0)
        for n in nn:
            if n['kind']=='well' and n['id'] in state and not (n.get('params') or {}).get('reservoir_id') in tanks:
                n.setdefault('params',{})['reservoir_pressure_bar']=state[n['id']]['pr']
        nn=apply_tank_links(nn,tanks)
        p,q,info,details=solve_step(nn,ee,guess,enforce_constraints); guess=next_guess(p,q,info)
        return nn,info,details

    while t <= horizon_days:
        dt_days=min(step_days, max(horizon_days-t, 0))
        date=(t0+timedelta(days=t)).date().isoformat()
        nn0,ee=apply_events(base,edges,events,date)
        try: nn,info,details=solve_now(nn0)
        except Exception as exc:
            rows.append({'Date':date,'Day':t,'Total liquid [m3/d]':0.0,'Oil [m3/d]':0.0,'Water [m3/d]':0.0,'Gas [Sm3/d]':0.0,'Water injection [m3/d]':0.0,'Cumulative liquid [m3]':sum(s['cum_liq'] for s in state.values()),'Cumulative oil [Sm3]':cum['oil'],'Cumulative gas [Sm3]':cum['gas'],'Cumulative water [m3]':cum['wat'],'Wells flowing':0,'Violations':0,'Converged':False,'Message':str(exc)})
            if dt_days<=0: break
            t+=dt_days; continue
        first_info=info; first_details=details; first_nn=nn
        vol={'liq':0.0,'oil':0.0,'wat':0.0,'gas':0.0,'winj':0.0}; wvol={}; rem=float(dt_days); nsub=0; converged=bool(info.get('success'))
        while True:
            wells,per_tank,winj=_step_rates(nn,details,info,tanks)
            if rem<=1e-9:  # zero-length final report step: instantaneous rates
                break
            sub=rem
            for tid,tk in tanks.items():
                v=per_tank[tid]; probe=_copy.deepcopy(tk); p0=probe.p
                probe.step(v['oil'],v['wat'],v['gas'],v['winj'],v['ginj'],1.0)
                rate_dp=abs(p0-probe.p)
                lim=max_tank_dp_bar if max_tank_dp_bar else max(2.0,0.03*p0)
                if rate_dp>1e-12: sub=min(sub,max(lim/rate_dp,rem/max_substeps))
            sub=min(sub,rem)
            for tid,tk in tanks.items():
                v=per_tank[tid]; tk.step(v['oil']*sub,v['wat']*sub,v['gas']*sub,v['winj']*sub,v['ginj']*sub,sub)
            for wid,w in wells.items():
                st=state[wid]; st['cum_liq']+=w['liq']*sub; st['cum_oil']+=w['oil']*sub; st['cum_gas']+=w['gas']*sub; st['cum_wat']+=w['wat']*sub
                a=wvol.setdefault(wid,{'liq':0.0,'oil':0.0,'wat':0.0,'gas':0.0}); 
                for k in a: a[k]+=w[k]*sub
                rid=w['prm'].get('reservoir_id')
                if rid in tanks: st['pr']=tanks[rid].p
                else:
                    cfg=dep.get(wid,{}); prm=w['prm']
                    decline=float(cfg.get('pressure_decline_bar_per_1000m3',prm.get('pressure_decline_bar_per_1000m3',0.03)))
                    support=float(cfg.get('pressure_support_bar_per_day',prm.get('pressure_support_bar_per_day',0.0)))
                    st['pr']=max(float(cfg.get('min_reservoir_pressure_bar',prm.get('min_reservoir_pressure_bar',20.0))), st['pr']-decline*(w['liq']*sub/1000.0)+support*sub)
            for k in ('liq','oil','wat','gas'): vol[k]+=sum(w[k] for w in wells.values())*sub
            vol['winj']+=winj*sub
            rem-=sub; nsub+=1
            if rem<=1e-9: break
            try: nn,info,details=solve_now(nn0); converged=converged and bool(info.get('success'))
            except Exception: converged=False; break
        if dt_days>0:
            avg={k:vol[k]/dt_days for k in vol}; wavg={wid:{k:v[k]/dt_days for k in v} for wid,v in wvol.items()}
        else:
            wells,_,winj=_step_rates(nn,details,info,tanks)
            avg={'liq':sum(w['liq'] for w in wells.values()),'oil':sum(w['oil'] for w in wells.values()),'wat':sum(w['wat'] for w in wells.values()),'gas':sum(w['gas'] for w in wells.values()),'winj':winj}
            wavg={wid:{k:w[k] for k in ('liq','oil','wat','gas')} for wid,w in wells.items()}
        cum['oil']+=vol['oil']; cum['gas']+=vol['gas']; cum['wat']+=vol['wat']; cum['winj']+=vol['winj']
        for n in first_nn:
            if n['kind']!='well' or n['id'] not in first_details: continue
            dd=first_details[n['id']]; prm=n.get('params',{}); w=wavg.get(n['id'],{'liq':0.0,'oil':0.0,'wat':0.0,'gas':0.0}); rid=prm.get('reservoir_id')
            well_rows.append({'Date':date,'Well':n['name'],'Well ID':n['id'],'Tank':tanks[rid].name if rid in tanks else '—','Status':dd.get('status'),'Liquid [m3/d]':w['liq'],'Oil [m3/d]':w['oil'],'Water [m3/d]':w['wat'],'Gas [Sm3/d]':w['gas'],'Reservoir pressure [bar]':float(prm.get('reservoir_pressure_bar',0.0)),'WHP [bar]':dd['whp_bar'],'BHP [bar]':dd['bhp_bar'],'Cumulative liquid [m3]':state[n['id']]['cum_liq'],'Cumulative oil [Sm3]':state[n['id']]['cum_oil']})
        for tid,tk in tanks.items(): tank_rows.append({'Date':date,**tk.row()})
        for c in first_info.get('constraints',[]): constraint_rows.append({'Date':date,**c})
        tl=avg['liq']; flowing=sum(1 for w in wavg.values() if w['liq']>1e-6)
        row={'Date':date,'Day':t,'Total liquid [m3/d]':tl,'Oil [m3/d]':avg['oil'],'Water [m3/d]':avg['wat'],'Gas [Sm3/d]':avg['gas'],'Water injection [m3/d]':avg['winj'],
             'Water cut [%]':100*avg['wat']/tl if tl>0 else 0.0,'GOR [Sm3/Sm3]':avg['gas']/avg['oil'] if avg['oil']>0 else 0.0,
             'Cumulative liquid [m3]':sum(s['cum_liq'] for s in state.values()),'Cumulative oil [Sm3]':cum['oil'],'Cumulative gas [Sm3]':cum['gas'],'Cumulative water [m3]':cum['wat'],'Cumulative water injection [m3]':cum['winj'],
             'Wells flowing':int(flowing),'Substeps':nsub,'Violations':first_info.get('violations',0),'Converged':converged,'Message':first_info.get('message','')}
        for tk in tanks.values(): row[f"P {tk.name} [bar]"]=tk.p
        rows.append(row)
        if dt_days<=0: break
        t+=dt_days
    return {'field':rows,'wells':well_rows,'constraints':constraint_rows,'tanks':tank_rows,'final_state':state,
            'recovery':[tk.row() for tk in tanks.values()]}
