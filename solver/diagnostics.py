def solver_diagnostics(info, pressures=None, rates=None, tolerance=1e-4):
    out=[]
    if not info.get('success',False): out.append({'severity':'error','code':'NONCONVERGED','message':'Nonlinear network solve did not report convergence.'})
    r=float(info.get('max_abs_residual',0.0) or 0.0)
    if r>tolerance: out.append({'severity':'error','code':'RESIDUAL','message':f'Max residual {r:.3e} exceeds tolerance {tolerance:.1e}.'})
    for k,v in (pressures or {}).items():
        if v<=0: out.append({'severity':'error','code':'PRESSURE','message':f'Node {k} has non-positive pressure ({v:.3g} bar).'})
    for k,v in (rates or {}).items():
        if abs(v)>1e7: out.append({'severity':'warning','code':'RATE_RANGE','message':f'Connection {k} has an unusually large absolute rate ({v:.3g} m3/d).'})
    if info.get('violations',0): out.append({'severity':'warning','code':'CONSTRAINTS','message':f"{info['violations']} operating constraint(s) violated."})
    if not out: out.append({'severity':'ok','code':'OK','message':'Convergence and basic numerical checks passed.'})
    return out
