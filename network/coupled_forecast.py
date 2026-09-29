"""v11 multi-tank quasi-steady forecast coupling."""
import copy
from datetime import datetime, timedelta
from network.forecast import apply_events, DAYS_PER_YEAR
from network.reservoir import tank_from_dict, update_tank, allocate_injection
from solver.steady_state import solve_network

def _cap_availability(nodes, facility_capacity_m3d=None):
    wells=[n for n in nodes if n.get('kind')=='well' and n.get('params',{}).get('available',True)]
    if facility_capacity_m3d is None or not wells: return
    potential=sum(max(float(n.get('params',{}).get('pi_m3d_bar',0.0)),0.0) for n in wells)
    if potential<=0: return
    factor=min(1.0,max(float(facility_capacity_m3d),0.0)/potential)
    for n in wells: n.setdefault('params',{})['availability_factor']=min(float(n['params'].get('availability_factor',1.0)),factor)

def run_coupled_forecast(nodes, edges, tanks, well_to_tank, start_date, years=5, step_days=30,
                         events=None, injection_schedule=None, injection_weights=None,
                         facility_capacity_m3d=None):
    """Resolve the network at each step while independently updating mapped reservoir tanks."""
    base=copy.deepcopy(nodes); ts={t['id']:tank_from_dict(t) for t in copy.deepcopy(tanks)}
    field=[]; wells=[]; tank_rows=[]; constraints=[]; tday=0
    while tday <= int(years*DAYS_PER_YEAR):
        date=(datetime.fromisoformat(str(start_date))+timedelta(days=tday)).date().isoformat()
        nn,ee=apply_events(base,edges,events,date); _cap_availability(nn,facility_capacity_m3d)
        for n in nn:
            tid=well_to_tank.get(n['id'])
            if n.get('kind')=='well' and tid in ts:
                n.setdefault('params',{})['reservoir_pressure_bar']=ts[tid].pressure_bar
                if not n['params'].get('available',True): n['params']['pi_m3d_bar']=0.0
        try: p,q,info,details=solve_network(nn,ee)
        except Exception as exc:
            field.append({'Date':date,'Day':tday,'Total liquid [m3/d]':0.0,'Oil [m3/d]':0.0,'Water [m3/d]':0.0,'Gas [Sm3/d]':0.0,'Converged':False,'Message':str(exc)}); tday+=step_days; continue
        withdrawals={k:0.0 for k in ts}; tl=oil=wat=gas=0.0
        for n in nn:
            if n.get('kind')!='well' or n['id'] not in details: continue
            d=details[n['id']]; prm=n.get('params',{}); rate=max(float(d['liquid_rate_m3d']),0.0)*max(0,min(float(prm.get('availability_factor',1.0)),1))
            wc=float(prm.get('water_cut',0.0)); o=rate*(1-wc); w=rate*wc; g=o*float(prm.get('gor_sm3sm3',0.0)); tl+=rate; oil+=o; wat+=w; gas+=g
            tid=well_to_tank.get(n['id']);
            if tid in withdrawals: withdrawals[tid]+=rate*step_days
            wells.append({'Date':date,'Well':n.get('name',n['id']),'Well ID':n['id'],'Tank':tid,'Liquid [m3/d]':rate,'Oil [m3/d]':o,'Water [m3/d]':w,'Gas [Sm3/d]':g,'WHP [bar]':d['whp_bar'],'BHP [bar]':d['bhp_bar']})
        inj_rate=0.0
        for item in injection_schedule or []:
            if str(item.get('date'))<=date: inj_rate=float(item.get('rate_m3d',inj_rate))
        inj=allocate_injection(inj_rate*step_days,ts.keys(),injection_weights)
        for tid,tank in ts.items():
            aquifer=tank.aquifer_support_fraction*withdrawals[tid]
            update_tank(tank,withdrawals[tid],inj.get(tid,0.0),aquifer)
            tank_rows.append({'Date':date,'Tank':tank.name,'Tank ID':tid,'Pressure [bar]':tank.pressure_bar,'Withdrawal [m3]':withdrawals[tid],'Injection [m3]':inj.get(tid,0.0),'Cumulative withdrawal [m3]':tank.cumulative_withdrawal_m3})
        for c in info.get('constraints',[]): constraints.append({'Date':date,**c})
        field.append({'Date':date,'Day':tday,'Total liquid [m3/d]':tl,'Oil [m3/d]':oil,'Water [m3/d]':wat,'Gas [Sm3/d]':gas,'Injection [m3/d]':inj_rate,'Violations':info.get('violations',0),'Converged':info.get('success',False),'Message':info.get('message','')})
        tday+=step_days
    return {'field':field,'wells':wells,'tanks':tank_rows,'constraints':constraints,'final_tanks':{k:v.to_dict() for k,v in ts.items()}}
