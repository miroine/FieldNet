from network.reservoir import ReservoirTank, update_tank, allocate_injection
from network.coupled_forecast import run_coupled_forecast
from network.examples import demo_case

def test_independent_tanks_deplete_independently():
    a=ReservoirTank('A','A',pressure_bar=200,initial_pressure_bar=200); b=ReservoirTank('B','B',pressure_bar=200,initial_pressure_bar=200)
    update_tank(a,1000); assert a.pressure_bar < 200 and b.pressure_bar == 200

def test_injection_allocation_weights():
    x=allocate_injection(300,['A','B'],{'A':2,'B':1}); assert abs(x['A']-200)<1e-9 and abs(x['B']-100)<1e-9

def test_injection_support_reduces_depletion():
    a=ReservoirTank('A','A',pressure_bar=200,initial_pressure_bar=200); b=ReservoirTank('B','B',pressure_bar=200,initial_pressure_bar=200)
    update_tank(a,1000,0); update_tank(b,1000,500); assert b.pressure_bar > a.pressure_bar

def test_injection_and_aquifer_support_can_repressurize_tank():
    injected=ReservoirTank('I','I',pressure_bar=200,pore_volume_m3=1e6,total_compressibility_1bar=1e-4)
    aquifer=ReservoirTank('A','A',pressure_bar=200,pore_volume_m3=1e6,total_compressibility_1bar=1e-4)

    assert update_tank(injected,500,injection_m3=1000)==205
    assert update_tank(aquifer,500,aquifer_m3=1000)==205
    assert injected.cumulative_withdrawal_m3==aquifer.cumulative_withdrawal_m3==500
    assert injected.cumulative_injection_m3==1000

def test_coupled_forecast_maps_wells_to_tanks():
    nodes,edges=demo_case(); wellids=[n['id'] for n in nodes if n['kind']=='well']
    tanks=[{'id':'T1','name':'Tank 1','initial_pressure_bar':240,'pressure_bar':240},{'id':'T2','name':'Tank 2','initial_pressure_bar':220,'pressure_bar':220}]
    mapping={wid:('T1' if i%2==0 else 'T2') for i,wid in enumerate(wellids)}
    fc=run_coupled_forecast(nodes,edges,tanks,mapping,'2026-01-01',years=0.1,step_days=30)
    assert fc['field'] and set(r['Tank ID'] for r in fc['tanks'])=={'T1','T2'}
