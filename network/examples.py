def demo_case():
    wp={'reservoir_pressure_bar':240.0,'ipr_model':'PI','pi_m3d_bar':12.0,'qmax_m3d':1800.0,'initial_pressure_bar':90.0,'initial_rate_m3d':700.0,'depth_m':2200.0,'tubing_id_m':0.0889,'tubing_roughness_m':4.5e-5,'temperature_c':75.0,'water_cut':0.25,'gor_sm3sm3':110.0,'api':36.0,'gas_sg':0.72,'correlation':'Beggs-Brill'}
    wp2=dict(wp); wp2.update({'reservoir_pressure_bar':225.0,'pi_m3d_bar':9.0,'water_cut':0.40,'gor_sm3sm3':85.0})
    nodes=[{'id':'w1','kind':'well','name':'PROD-01','pressure_bar':None,'x':0,'y':0,'params':wp}, {'id':'w2','kind':'well','name':'PROD-02','pressure_bar':None,'x':0,'y':0,'params':wp2}, {'id':'m1','kind':'manifold','name':'MAN-01','pressure_bar':None,'x':0,'y':0,'params':{'initial_pressure_bar':55}}, {'id':'s1','kind':'sink','name':'SEP-01','pressure_bar':35.0,'x':0,'y':0,'params':{}}]
    pp={'temperature_c':50.0,'water_cut':0.30,'gor_sm3sm3':100.0,'api':36.0,'gas_sg':0.72,'initial_rate_m3d':600,'correlation':'Beggs-Brill'}
    edges=[{'id':'fl1','source':'w1','target':'m1','kind':'pipeline','length_m':3500.0,'diameter_m':0.154,'roughness_m':4.5e-5,'elevation_change_m':20.0,'params':dict(pp)}, {'id':'fl2','source':'w2','target':'m1','kind':'pipeline','length_m':2800.0,'diameter_m':0.154,'roughness_m':4.5e-5,'elevation_change_m':10.0,'params':dict(pp)}, {'id':'trunk','source':'m1','target':'s1','kind':'pipeline','length_m':8000.0,'diameter_m':0.254,'roughness_m':4.5e-5,'elevation_change_m':-15.0,'params':dict(pp)}]
    return nodes,edges


def demo_field_case():
    """Realistic field demo: one oil tank (in-place volume, aquifer) drained by three
    producers (one on gas lift) through a manifold to a capacity-limited separator,
    with water injection for pressure support."""
    def well(i,name,x,y,**kw):
        p={'reservoir_id':'T1','ipr_model':'PI','pi_m3d_bar':14.0,'depth_m':2400.0,'tubing_id_m':0.1,'tubing_roughness_m':4.5e-5,
           'temperature_c':80.0,'water_cut':0.05,'gor_sm3sm3':110.0,'api':34.0,'gas_sg':0.72,'vlp_model':'Beggs-Brill','available':True}
        p.update(kw); return {'id':f'P{i}','kind':'well','name':name,'pressure_bar':None,'x':x,'y':y,'params':p}
    nodes=[
        {'id':'T1','kind':'reservoir','name':'MAIN-RES','pressure_bar':None,'x':40,'y':260,'params':{
            'fluid_phase':'oil','reservoir_pressure_bar':290.0,'temperature_c':90.0,'stoiip_sm3':30e6,'boi_rm3_sm3':1.3,'rsi_sm3_sm3':110.0,
            'bubble_point_bar':160.0,'swi':0.2,'ct_1bar':1.5e-4,'aquifer_pi_m3d_bar':150.0,'min_pressure_bar':60.0,
            'water_breakthrough_rf':0.06,'max_water_cut':0.9,'rf_at_max_water_cut':0.40,'gor_rise_factor':3.0}},
        well(1,'PROD-A',300,80),
        well(2,'PROD-B',300,260,pi_m3d_bar=10.0,water_cut=0.10),
        well(3,'PROD-C',300,440,pi_m3d_bar=8.0,lift_type='gas_lift',gas_lift_injection_sm3d=60000.0),
        {'id':'M1','kind':'manifold','name':'MANIFOLD','pressure_bar':None,'x':560,'y':260,'params':{}},
        {'id':'SEP','kind':'separator','name':'SEPARATOR','pressure_bar':20.0,'x':820,'y':260,'params':{'max_liquid_rate_m3d':4500.0}},
        {'id':'WS','kind':'water_source','name':'SEAWATER','pressure_bar':5.0,'x':40,'y':560,'params':{}},
        {'id':'I1','kind':'water_injector','name':'INJ-1','pressure_bar':None,'x':300,'y':640,'params':{
            'reservoir_id':'T1','injection_fluid':'water','injectivity_m3d_bar':40.0,'depth_m':2400.0,'max_rate_m3d':3500.0,'available':True}},
    ]
    pp={'temperature_c':60.0,'water_cut':0.1,'gor_sm3sm3':110.0,'api':34.0,'gas_sg':0.72,'correlation':'Beggs-Brill','initial_rate_m3d':800.0}
    def pipe(i,s,t,L,D,dz=0.0): return {'id':i,'source':s,'target':t,'kind':'pipeline','length_m':L,'diameter_m':D,'roughness_m':4.5e-5,'elevation_change_m':dz,'params':dict(pp)}
    edges=[pipe('FL-A','P1','M1',2500,0.154),pipe('FL-B','P2','M1',1800,0.154),pipe('FL-C','P3','M1',3200,0.154),
           pipe('TRUNK','M1','SEP',9000,0.3,20.0),
           {'id':'WIP','source':'WS','target':'I1','kind':'pump','length_m':0.0,'diameter_m':0.2,'roughness_m':4.5e-5,'elevation_change_m':0.0,
            'params':{'shutoff_head_bar':260.0,'rated_rate_m3d':8000.0,'efficiency':0.75}}]
    return nodes,edges
