from physics.controls import control_valve_dp_bar, compressor_map_ratio, controller_opening
from network.injection import injection_summary

def test_control_valve_opening_increases_loss_when_closed():
    assert control_valve_dp_bar(1000,80,850,.3) > control_valve_dp_bar(1000,80,850,1.0)

def test_compressor_map_droops_with_rate():
    lo=compressor_map_ratio(20000,100000,2.0,1.0); hi=compressor_map_ratio(90000,100000,2.0,1.0)
    assert lo > hi >= 1.0

def test_controller_bounded():
    assert 0.02 <= controller_opening(100,50) <= 1.0

def test_injection_summary():
    n=[{'id':'s','kind':'water_source','name':'WS','params':{}},{'id':'i','kind':'water_injector','name':'WI-1','params':{'injection_fluid':'water'}}]
    e=[{'id':'x','source':'s','target':'i'}]
    r=injection_summary(n,e,{'x':123})
    assert r[0]['Rate']==123 and r[0]['Fluid']=='water'
