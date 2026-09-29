"""FieldNet v25 quasi-steady reservoir/network forecast with reduced-order tank coupling."""
import copy
from datetime import datetime, timedelta
from network.forecast import apply_events, DAYS_PER_YEAR
from network.reservoir import tank_from_dict
from network.reservoir_v25 import (AquiferSpec, CommunicationLink, InjectorConnection,
    allocate_connected_injection, step_coupled_tanks, validate_model)
from solver.steady_state import solve_network


def run_coupled_forecast_v25(nodes, edges, tanks, well_to_tank, start_date, years=5, step_days=30,
                             events=None, injector_schedule=None, injector_connections=(),
                             aquifers=(), communication_links=(), facility_capacity_m3d=None):
    base=copy.deepcopy(nodes); ts={t['id']:tank_from_dict(copy.deepcopy(t)) for t in tanks}
    validate_model(ts,aquifers,communication_links,injector_connections)
    field=[]; wells=[]; tank_rows=[]; transfers=[]; constraints=[]; tday=0
    end_days=float(years)*DAYS_PER_YEAR
    while tday < end_days-1e-12:
        dt=min(float(step_days),end_days-tday)
        date=(datetime.fromisoformat(str(start_date))+timedelta(days=tday)).date().isoformat()
        nn,ee=apply_events(base,edges,events,date)
        for n in nn:
            tid=well_to_tank.get(n['id'])
            if n.get('kind')=='well' and tid in ts:
                n.setdefault('params',{})['reservoir_pressure_bar']=ts[tid].pressure_bar
                if not n['params'].get('available',True): n['params']['pi_m3d_bar']=0.0
        try: p,q,info,details=solve_network(nn,ee)
        except Exception as exc:
            field.append({'Date':date,'Day':tday,'dt_days':dt,'Total liquid [m3/d]':0.0,'Converged':False,'Message':str(exc)}); tday+=dt; continue
        wd={k:0.0 for k in ts}; tl=oil=wat=gas=0.0
        for n in nn:
            if n.get('kind')!='well' or n['id'] not in details: continue
            d=details[n['id']]; prm=n.get('params',{}); rate=max(float(d['liquid_rate_m3d']),0.0)*max(0,min(float(prm.get('availability_factor',1.0)),1))
            wc=float(prm.get('water_cut',0.0)); o=rate*(1-wc); w=rate*wc; g=o*float(prm.get('gor_sm3sm3',0.0)); tl+=rate; oil+=o; wat+=w; gas+=g
            tid=well_to_tank.get(n['id']);
            if tid in wd: wd[tid]+=rate*dt
            wells.append({'Date':date,'Well':n.get('name',n['id']),'Well ID':n['id'],'Tank':tid,'Liquid [m3/d]':rate,'Reservoir pressure [bar]':ts[tid].pressure_bar if tid in ts else None})
        inj_rates={}
        for item in injector_schedule or []:
            if str(item.get('date'))<=date: inj_rates[str(item.get('injector_id','INJ'))]=max(float(item.get('rate_m3d',0.0)),0.0)
        injvol={k:v*dt for k,v in inj_rates.items()}
        alloc=allocate_connected_injection(injvol,injector_connections,ts.keys())
        audit=step_coupled_tanks(ts,wd,alloc,dt,aquifers,communication_links)
        for row in audit['ledger']:
            tank_rows.append({'Date':date,**row})
        for tr in audit['transfers']: transfers.append({'Date':date,**tr})
        for c in info.get('constraints',[]): constraints.append({'Date':date,**c})
        field.append({'Date':date,'Day':tday,'dt_days':dt,'Total liquid [m3/d]':tl,'Oil [m3/d]':oil,'Water [m3/d]':wat,'Gas [Sm3/d]':gas,
                      'Injection [m3/d]':sum(inj_rates.values()),'Converged':info.get('success',False),'Violations':info.get('violations',0),'Message':info.get('message','')})
        tday+=dt
    return {'application':'FieldNet v29','field':field,'wells':wells,'tanks':tank_rows,'transfers':transfers,'constraints':constraints,
            'final_tanks':{k:v.to_dict() for k,v in ts.items()}}
