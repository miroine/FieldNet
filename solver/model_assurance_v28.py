"""FieldNet v29 engineering QA and model assurance.

Conservative, read-only checks.  The checker never modifies the engineering case.
Severity: error = definite invariant/solvability violation; warning = suspicious or
outside a recommended screening range; info = traceability note.
"""
from __future__ import annotations
from copy import deepcopy
from collections import Counter, defaultdict
import math
from solver.v21 import topology_precheck
from network.interchange_v27 import validate_project
from physics.unit_system import STANDARD_CONDITIONS

APPLICATION='FieldNet v29'
SCHEMA_VERSION='28.0'

def _issue(severity, code, message, component=None, field=None, value=None, recommendation=None, stage='pre_solve'):
    d={'severity':severity,'code':code,'stage':stage,'message':message}
    if component is not None:d['component']=component
    if field is not None:d['field']=field
    if value is not None:d['value']=value
    if recommendation:d['recommendation']=recommendation
    return d

def pre_solve_assurance(nodes, edges, *, unit_profile='norwegian_si'):
    issues=[]
    for x in validate_project(nodes,edges):
        sev=x.get('severity','warning'); issues.append(_issue(sev,'INTERCHANGE_'+str(x.get('field','')).upper(),x['message'],field=x.get('field')))
    issues += [dict(x,stage='pre_solve') for x in topology_precheck(nodes,edges)]
    names=[str(n.get('name','')).strip() for n in nodes if str(n.get('name','')).strip()]
    for name,c in Counter(names).items():
        if c>1: issues.append(_issue('warning','DUPLICATE_NAME',f'Component name {name!r} is used {c} times.',recommendation='Use unique engineering names to improve audit traceability.'))
    if unit_profile not in ('norwegian_si','field','canonical'):
        issues.append(_issue('error','UNIT_PROFILE','Unknown engineering unit profile.',value=unit_profile))
    for n in nodes:
        p=n.get('params') or {}; cid=n.get('id'); kind=n.get('kind')
        pb=n.get('pressure_bar')
        if pb is not None and float(pb)<=0: issues.append(_issue('error','NONPOSITIVE_PRESSURE','Boundary pressure must be positive.',cid,'pressure_bar',pb))
        for key in ('reservoir_pressure_bar','initial_pressure_bar'):
            if key in p and float(p[key])<=0: issues.append(_issue('error','NONPOSITIVE_PRESSURE',f'{key} must be positive.',cid,key,p[key]))
        if kind=='well':
            wc=float(p.get('water_cut',0.0)); gor=float(p.get('gor_sm3sm3',0.0)); temp=float(p.get('temperature_c',20.0)); depth=float(p.get('depth_m',0.0)); tid=float(p.get('tubing_id_m',0.0)); pi=float(p.get('pi_m3d_bar',0.0))
            if not 0<=wc<=1: issues.append(_issue('error','WATER_CUT_RANGE','Water cut must be between 0 and 1.',cid,'water_cut',wc))
            elif wc>0.98: issues.append(_issue('warning','EXTREME_WATER_CUT','Water cut is above 98%; validate multiphase/PVT applicability.',cid,'water_cut',wc))
            if gor<0: issues.append(_issue('error','NEGATIVE_GOR','GOR cannot be negative.',cid,'gor_sm3sm3',gor))
            elif gor>5000: issues.append(_issue('warning','EXTREME_GOR','GOR is unusually high for the screening liquid-well model.',cid,'gor_sm3sm3',gor))
            if temp<-20 or temp>200: issues.append(_issue('warning','TEMPERATURE_RANGE','Well temperature is outside the normal screening range.',cid,'temperature_c',temp))
            if depth<=0: issues.append(_issue('error','WELL_DEPTH','Well depth must be positive.',cid,'depth_m',depth))
            if tid<=0: issues.append(_issue('error','TUBING_DIAMETER','Tubing ID must be positive.',cid,'tubing_id_m',tid))
            elif tid<0.025 or tid>0.30: issues.append(_issue('warning','TUBING_DIAMETER_RANGE','Tubing ID is unusual; verify units/geometry.',cid,'tubing_id_m',tid))
            if str(p.get('ipr_model','PI')).upper()=='PI' and pi<=0: issues.append(_issue('error','PI_RANGE','PI model requires positive productivity index.',cid,'pi_m3d_bar',pi))
    for e in edges:
        cid=e.get('id'); L=float(e.get('length_m',0)); D=float(e.get('diameter_m',0)); eps=float(e.get('roughness_m',0)); p=e.get('params') or {}
        if e.get('kind')=='pipeline':
            if L<=0 or D<=0: continue
            if D<0.025 or D>1.5: issues.append(_issue('warning','PIPE_DIAMETER_RANGE','Pipeline diameter is unusual; verify units/geometry.',cid,'diameter_m',D))
            if eps<0: issues.append(_issue('error','ROUGHNESS_NEGATIVE','Absolute roughness cannot be negative.',cid,'roughness_m',eps))
            if D>0 and eps/D>0.05: issues.append(_issue('warning','RELATIVE_ROUGHNESS','Relative roughness exceeds 5%; verify roughness units.',cid,'roughness_m',eps))
            if L/D<10: issues.append(_issue('warning','SHORT_PIPE','Pipeline L/D < 10; distributed-flow correlation may be inappropriate.',cid,'length_m',L))
        wc=float(p.get('water_cut',0.0)) if 'water_cut' in p else None
        if wc is not None and not 0<=wc<=1: issues.append(_issue('error','WATER_CUT_RANGE','Connection water cut must be between 0 and 1.',cid,'water_cut',wc))
        if float(p.get('opening',1.0))<0 or float(p.get('opening',1.0))>1: issues.append(_issue('error','OPENING_RANGE','Control opening must be between 0 and 1.',cid,'opening',p.get('opening')))
        if float(p.get('speed_fraction',1.0))<=0: issues.append(_issue('error','SPEED_RANGE','Equipment speed fraction must be positive.',cid,'speed_fraction',p.get('speed_fraction')))
    if not any(i['severity']=='error' for i in issues): issues.append(_issue('info','PRECHECK_COMPLETE','Pre-solve structural, units and plausibility checks completed.'))
    return issues

def post_solve_assurance(nodes, edges, pressures, flows, info):
    issues=[]; audit=info.get('physical_residual_audit',{}) or {}
    if not info.get('success'): issues.append(_issue('error','SOLVER_NONCONVERGED','Network solver did not converge.',stage='post_solve'))
    mp=float(audit.get('max_pressure_residual_bar',0) or 0); mm=float(audit.get('max_mass_residual_m3d',0) or 0)
    if mp>1e-3: issues.append(_issue('error','PRESSURE_CLOSURE','Physical pressure-equation residual exceeds 0.001 bar.',value=mp,stage='post_solve'))
    if mm>0.1: issues.append(_issue('error','MASS_CLOSURE','Node mass-balance residual exceeds 0.1 m3/d.',value=mm,stage='post_solve'))
    jc=float(info.get('jacobian_condition',float('nan')))
    if math.isfinite(jc) and jc>1e10: issues.append(_issue('warning','JACOBIAN_CONDITION','Jacobian is highly ill-conditioned.',value=jc,stage='post_solve'))
    for c in info.get('constraints',[]):
        if c.get('Status')=='VIOLATED': issues.append(_issue('warning','OPERATING_LIMIT','Operating constraint is violated.',component=c.get('Component'),stage='post_solve'))
    for k,v in pressures.items():
        if float(v)<=0: issues.append(_issue('error','SOLVED_PRESSURE','Solved pressure is non-positive.',k,value=v,stage='post_solve'))
    if not issues: issues.append(_issue('info','POSTSOLVE_COMPLETE','Convergence, residual closure and operating-limit checks passed.',stage='post_solve'))
    return issues

def forecast_assurance(forecast):
    issues=[]; rows=(forecast or {}).get('field',[])
    if not rows: return [_issue('warning','NO_FORECAST','No forecast rows available for assurance.',stage='forecast')]
    dates=[str(r.get('Date','')) for r in rows]
    if dates!=sorted(dates): issues.append(_issue('error','FORECAST_TIME_ORDER','Forecast dates are not monotonic.',stage='forecast'))
    for i,r in enumerate(rows):
        for key in ('Oil [m3/d]','Water [m3/d]','Total liquid [m3/d]','Gas [Sm3/d]','Cumulative liquid [m3]'):
            if key in r and float(r[key]) < -1e-9: issues.append(_issue('error','NEGATIVE_FORECAST','Forecast contains a negative production/cumulative value.',f'row:{i}',key,r[key],stage='forecast'))
    if not issues: issues.append(_issue('info','FORECAST_COMPLETE','Forecast chronology and non-negative production checks passed.',stage='forecast'))
    return issues

def model_quality_report(nodes, edges, *, unit_profile='norwegian_si', solve_result=None, forecast=None):
    ns,es=deepcopy(nodes),deepcopy(edges); issues=pre_solve_assurance(ns,es,unit_profile=unit_profile)
    if solve_result:
        p,q,info=solve_result[:3]; issues.extend(post_solve_assurance(ns,es,p,q,info))
    if forecast is not None: issues.extend(forecast_assurance(forecast))
    counts=Counter(i['severity'] for i in issues); grade='FAIL' if counts['error'] else ('REVIEW' if counts['warning'] else 'PASS')
    return {'application':APPLICATION,'schema_version':SCHEMA_VERSION,'quality_gate':grade,'counts':dict(counts),'standard_conditions':STANDARD_CONDITIONS,'unit_profile':unit_profile,'issues':issues,'node_count':len(ns),'edge_count':len(es)}
