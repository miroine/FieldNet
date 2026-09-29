import numpy as np
from scipy.optimize import least_squares
from physics.ipr import ipr_rate_m3d
from physics.multiphase import homogeneous_dp_bar
from physics.beggs_brill import beggs_brill_dp_bar
from physics.choke import choke_dp_bar
from physics.vlp import tubing_bhp_bar
from physics.equipment import pump_head_bar, pump_power_kw, compressor_power_kw
from physics.controls import control_valve_dp_bar, compressor_map_ratio
from solver.constraints import evaluate_constraints

def solve_network(nodes, edges, *, x_scale="jac", max_nfev=3000):
    byid={n['id']:n for n in nodes}; links=[e for e in edges if e.get('kind','pipeline') in ('pipeline','choke','control_valve','pump','compressor')]
    unknown_p=[n['id'] for n in nodes if n.get('pressure_bar') is None and n['kind'] not in ('reservoir','sink')]
    pidx={nid:i for i,nid in enumerate(unknown_p)}; qidx={e['id']:len(pidx)+i for i,e in enumerate(links)}; nvar=len(pidx)+len(links)
    if nvar==0: return {},{},{'success':True,'cost':0,'message':'No unknowns','max_abs_residual':0,'constraints':[]},{}
    x0=np.zeros(nvar)
    for nid,i in pidx.items(): x0[i]=float(byid[nid].get('params',{}).get('initial_pressure_bar',80.0))
    for e in links: x0[qidx[e['id']]]=max(float(e.get('params',{}).get('initial_rate_m3d',500.0)),0.01)
    def P(nid,x):
        n=byid[nid]
        if n.get('pressure_bar') is not None:return float(n['pressure_bar'])
        if n['kind']=='reservoir':return float(n.get('params',{}).get('reservoir_pressure_bar',200.0))
        return x[pidx[nid]]
    def well_source(n,x):
        prm=n.get('params',{}); whp=P(n['id'],x); pr=float(prm.get('reservoir_pressure_bar',200)); model=prm.get('ipr_model','PI')
        def rr(z):
            q=max(float(z[0]),0.0); bhp,_=tubing_bhp_bar(q,whp,float(prm.get('depth_m',2000)),float(prm.get('tubing_id_m',.0762)),float(prm.get('tubing_roughness_m',4.5e-5)),float(prm.get('temperature_c',70)),float(prm.get('water_cut',.2)),float(prm.get('gor_sm3sm3',100)),float(prm.get('api',35)),float(prm.get('gas_sg',.75)),prm.get('correlation','Beggs-Brill'))
            return [(q-ipr_rate_m3d(pr,bhp,model,float(prm.get('pi_m3d_bar',10)),float(prm.get('qmax_m3d',1000))))/1000]
        return float(least_squares(rr,[max(float(prm.get('initial_rate_m3d',500)),.1)],bounds=(0,np.inf),max_nfev=200).x[0])
    def link_dp(e,q,x):
        prm=e.get('params',{}); kind=e.get('kind','pipeline'); pav=max((P(e['source'],x)+P(e['target'],x))/2,1.0)
        if kind=='choke': return choke_dp_bar(q,float(prm.get('cv',80)),float(prm.get('rho_kgm3',850)))
        if kind=='control_valve': return control_valve_dp_bar(q,float(prm.get('cv',80)),float(prm.get('rho_kgm3',850)),float(prm.get('opening',1.0)))
        if kind=='pump': return -pump_head_bar(q,float(prm.get('shutoff_head_bar',35)),float(prm.get('rated_rate_m3d',1500)),float(prm.get('min_head_bar',0)))
        if kind=='compressor':
            qg=abs(q)*float(prm.get('gor_sm3sm3',100)); ratio=compressor_map_ratio(qg,float(prm.get('rated_gas_rate_sm3d',150000)),float(prm.get('pressure_ratio',1.8)),float(prm.get('speed_fraction',1.0))) if prm.get('map_enabled',False) else max(float(prm.get('pressure_ratio',1.8)),1.0); maxdis=float(prm.get('max_discharge_bar',250)); target=min(P(e['source'],x)*ratio,maxdis); return P(e['source'],x)-target
        fn=beggs_brill_dp_bar if prm.get('correlation','Beggs-Brill')=='Beggs-Brill' else homogeneous_dp_bar
        return fn(q,float(e.get('length_m',1000)),float(e.get('diameter_m',.154)),float(e.get('roughness_m',4.5e-5)),float(e.get('elevation_change_m',0)),pav,float(prm.get('temperature_c',50)),float(prm.get('water_cut',.2)),float(prm.get('gor_sm3sm3',100)),float(prm.get('api',35)),float(prm.get('gas_sg',.75)))[0]
    def residual(x):
        r=[]
        for e in links:r.append((P(e['source'],x)-P(e['target'],x)-link_dp(e,x[qidx[e['id']]],x))/10)
        for n in nodes:
            if n['kind'] in ('sink','reservoir') or n.get('pressure_bar') is not None:continue
            inflow=sum(x[qidx[e['id']]] for e in links if e['target']==n['id']); outflow=sum(x[qidx[e['id']]] for e in links if e['source']==n['id']); src=well_source(n,x) if n['kind']=='well' else 0
            r.append((inflow+src-outflow)/1000)
        return np.asarray(r)
    sol=least_squares(residual,x0,max_nfev=int(max_nfev),xtol=1e-9,ftol=1e-9,gtol=1e-9,bounds=(-1e6,1e6),x_scale=x_scale);
    try:
        sv=np.linalg.svd(np.asarray(sol.jac),compute_uv=False); jac_cond=float(sv[0]/sv[-1]) if len(sv) and sv[-1]>1e-15 else float('inf')
    except Exception: jac_cond=float('nan')
    pressures={n['id']:P(n['id'],sol.x) for n in nodes}; flows={e['id']:float(sol.x[qidx[e['id']]]) for e in links}; details={}
    for n in nodes:
        if n['kind']=='well':
            q=well_source(n,sol.x); prm=n.get('params',{}); bhp,props=tubing_bhp_bar(q,P(n['id'],sol.x),float(prm.get('depth_m',2000)),float(prm.get('tubing_id_m',.0762)),float(prm.get('tubing_roughness_m',4.5e-5)),float(prm.get('temperature_c',70)),float(prm.get('water_cut',.2)),float(prm.get('gor_sm3sm3',100)),float(prm.get('api',35)),float(prm.get('gas_sg',.75)),prm.get('correlation','Beggs-Brill')); details[n['id']]={'liquid_rate_m3d':q,'bhp_bar':bhp,'whp_bar':P(n['id'],sol.x),'liquid_holdup':props['liquid_holdup'],'gas_fraction':props['gas_fraction']}
    equipment=[]
    for e in links:
        prm=e.get('params',{}); q=flows[e['id']]
        if e.get('kind')=='pump':
            h=pump_head_bar(q,prm.get('shutoff_head_bar',35),prm.get('rated_rate_m3d',1500),prm.get('min_head_bar',0)); equipment.append({'Equipment':e.get('name',e['id']),'Type':'Pump','Rate [m3/d]':q,'Suction [bar]':pressures[e['source']],'Discharge [bar]':pressures[e['target']],'Power [kW]':pump_power_kw(q,h,prm.get('rho_kgm3',850),prm.get('efficiency',.75))})
        elif e.get('kind')=='compressor':
            qg=abs(q)*float(prm.get('gor_sm3sm3',100)); equipment.append({'Equipment':e.get('name',e['id']),'Type':'Compressor','Rate [m3/d]':q,'Suction [bar]':pressures[e['source']],'Discharge [bar]':pressures[e['target']],'Power [kW]':compressor_power_kw(qg,pressures[e['source']],pressures[e['target']],prm.get('efficiency',.75))})
    res=residual(sol.x); info={'success':bool(sol.success),'cost':float(sol.cost),'message':str(sol.message),'max_abs_residual':float(np.max(np.abs(res))) if len(res) else 0.0,'equipment':equipment,'nfev':int(sol.nfev),'optimality':float(sol.optimality),'status':int(sol.status),'jacobian_condition':jac_cond,'variable_scaling':str(x_scale)}; info['constraints']=evaluate_constraints(nodes,links,pressures,flows,details); info['violations']=sum(r['Status']=='VIOLATED' for r in info['constraints'])
    return pressures,flows,info,details
