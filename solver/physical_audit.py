"""Post-solve physical residual reconstruction.

Re-evaluates every equation from the *reported* pressures and flows. Producer rates are
recomputed independently at the solved wellhead pressure (largest stable IPR/VLP root),
so a network that settled on a dead or unstable well branch is exposed here.
"""
from physics.well_model import well_settings, solve_well_rate
from solver.equations import links_of, link_dp_bar, edge_fluids, fixed_pressure, injector_settings, injector_excess_bar, INJECTOR_KINDS


def reconstruct_physical_residuals(nodes, edges, pressures, flows, injector_rates=None):
    links=links_of(edges); fluids=edge_fluids(nodes,edges); edge_rows=[]
    for e in links:
        q=float(flows[e['id']]); ps=float(pressures[e['source']]); pt=float(pressures[e['target']])
        edge_rows.append({'id':e['id'],'pressure_residual_bar':ps-pt-link_dp_bar(e,q,ps,pt,fluids.get(e['id'],'production'))})
    node_rows=[]; well_rows=[]
    for n in nodes:
        if fixed_pressure(n) is not None: continue
        inflow=sum(float(flows[e['id']]) for e in links if e['target']==n['id']); outflow=sum(float(flows[e['id']]) for e in links if e['source']==n['id'])
        src=0.0
        if n['kind']=='well':
            src,status=solve_well_rate(float(pressures[n['id']]),well_settings(n.get('params',{})))
            well_rows.append({'id':n['id'],'independent_rate_m3d':src,'status':status})
        if n['kind'] in INJECTOR_KINDS:
            if injector_rates and n['id'] in injector_rates: src=-float(injector_rates[n['id']])
            else:
                s=injector_settings(n); src=-max(min(s['ii']*max(injector_excess_bar(0.0,float(pressures[n['id']]),s),0.0),s['max_rate']),0.0) if s['open'] else 0.0
        node_rows.append({'id':n['id'],'mass_residual_m3d':inflow+src-outflow})
    return {'edge_residuals':edge_rows,'node_residuals':node_rows,'well_checks':well_rows,
            'max_pressure_residual_bar':max([abs(x['pressure_residual_bar']) for x in edge_rows] or [0.0]),
            'max_mass_residual_m3d':max([abs(x['mass_residual_m3d']) for x in node_rows] or [0.0])}
