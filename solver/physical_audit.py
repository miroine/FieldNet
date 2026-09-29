"""Independent post-solve physical residual reconstruction for FieldNet v14.1."""
import numpy as np
from scipy.optimize import least_squares
from physics.ipr import ipr_rate_m3d
from physics.multiphase import homogeneous_dp_bar
from physics.beggs_brill import beggs_brill_dp_bar
from physics.choke import choke_dp_bar
from physics.vlp import tubing_bhp_bar
from physics.equipment import pump_head_bar
from physics.controls import control_valve_dp_bar, compressor_map_ratio


def _well_source(n, whp):
    prm=n.get('params',{}); pr=float(prm.get('reservoir_pressure_bar',200)); model=prm.get('ipr_model','PI')
    def rr(z):
        q=max(float(z[0]),0.0)
        bhp,_=tubing_bhp_bar(q,whp,float(prm.get('depth_m',2000)),float(prm.get('tubing_id_m',.0762)),float(prm.get('tubing_roughness_m',4.5e-5)),float(prm.get('temperature_c',70)),float(prm.get('water_cut',.2)),float(prm.get('gor_sm3sm3',100)),float(prm.get('api',35)),float(prm.get('gas_sg',.75)),prm.get('correlation','Beggs-Brill'))
        return [(q-ipr_rate_m3d(pr,bhp,model,float(prm.get('pi_m3d_bar',10)),float(prm.get('qmax_m3d',1000))))/1000]
    return float(least_squares(rr,[max(float(prm.get('initial_rate_m3d',500)),.1)],bounds=(0,np.inf),max_nfev=200).x[0])


def _link_dp(e,q,p):
    prm=e.get('params',{}); kind=e.get('kind','pipeline'); ps=float(p[e['source']]); pt=float(p[e['target']]); pav=max((ps+pt)/2,1.0)
    if kind=='choke': return choke_dp_bar(q,float(prm.get('cv',80)),float(prm.get('rho_kgm3',850)))
    if kind=='control_valve': return control_valve_dp_bar(q,float(prm.get('cv',80)),float(prm.get('rho_kgm3',850)),float(prm.get('opening',1.0)))
    if kind=='pump': return -pump_head_bar(q,float(prm.get('shutoff_head_bar',35)),float(prm.get('rated_rate_m3d',1500)),float(prm.get('min_head_bar',0)))
    if kind=='compressor':
        qg=abs(q)*float(prm.get('gor_sm3sm3',100)); ratio=compressor_map_ratio(qg,float(prm.get('rated_gas_rate_sm3d',150000)),float(prm.get('pressure_ratio',1.8)),float(prm.get('speed_fraction',1.0))) if prm.get('map_enabled',False) else max(float(prm.get('pressure_ratio',1.8)),1.0)
        target=min(ps*ratio,float(prm.get('max_discharge_bar',250))); return ps-target
    fn=beggs_brill_dp_bar if prm.get('correlation','Beggs-Brill')=='Beggs-Brill' else homogeneous_dp_bar
    return fn(q,float(e.get('length_m',1000)),float(e.get('diameter_m',.154)),float(e.get('roughness_m',4.5e-5)),float(e.get('elevation_change_m',0)),pav,float(prm.get('temperature_c',50)),float(prm.get('water_cut',.2)),float(prm.get('gor_sm3sm3',100)),float(prm.get('api',35)),float(prm.get('gas_sg',.75)))[0]


def reconstruct_physical_residuals(nodes, edges, pressures, flows):
    links=[e for e in edges if e.get('kind','pipeline') in ('pipeline','choke','control_valve','pump','compressor')]
    edge_rows=[]
    for e in links:
        q=float(flows[e['id']]); raw=float(pressures[e['source']])-float(pressures[e['target']])-_link_dp(e,q,pressures)
        edge_rows.append({'id':e['id'],'pressure_residual_bar':raw})
    node_rows=[]
    for n in nodes:
        if n['kind'] in ('sink','reservoir') or n.get('pressure_bar') is not None: continue
        inflow=sum(float(flows[e['id']]) for e in links if e['target']==n['id']); outflow=sum(float(flows[e['id']]) for e in links if e['source']==n['id'])
        src=_well_source(n,float(pressures[n['id']])) if n['kind']=='well' else 0.0
        node_rows.append({'id':n['id'],'mass_residual_m3d':inflow+src-outflow})
    return {'edge_residuals':edge_rows,'node_residuals':node_rows,'max_pressure_residual_bar':max([abs(x['pressure_residual_bar']) for x in edge_rows] or [0.0]),'max_mass_residual_m3d':max([abs(x['mass_residual_m3d']) for x in node_rows] or [0.0])}
