from datetime import datetime, timezone

def calculation_audit(nodes,edges,info,details):
    return {
      'application':'FieldNet v29.1','author':'Merouane Hamdani','license_note':'For non-commercial use',
      'generated_utc':datetime.now(timezone.utc).isoformat(),
      'solver_mode':info.get('solver_mode','legacy'),'converged':bool(info.get('success')),
      'quality_gate':info.get('quality_gate','N/A'),'physical_quality_gate':info.get('physical_quality_gate','N/A'),'max_abs_residual':float(info.get('max_abs_residual',0)),'physical_residual_audit':info.get('physical_residual_audit',{}),
      'node_count':len(nodes),'connection_count':len(edges),'well_count':sum(n.get('kind')=='well' for n in nodes),
      'constraint_checks':len(info.get('constraints',[])),'constraint_violations':int(info.get('violations',0)),
      'models':{'multiphase':'Beggs-Brill screening or homogeneous selectable','PVT':'FieldNet selectable screening models','network':'quasi-steady nonlinear least-squares'},
      'warnings':['Independent engineering prototype; validate correlations and inputs for intended use.']
    }
