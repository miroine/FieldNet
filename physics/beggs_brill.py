import math
from physics.multiphase import mixture_properties
from physics.hydraulics import friction_factor, G
from physics.units import DAY_TO_S as DAY, pa_to_bar

def _regime(lambda_l, nfr):
    l1=316*lambda_l**0.302; l2=0.0009252*lambda_l**-2.4684; l3=0.1*lambda_l**-1.4516; l4=0.5*lambda_l**-6.738
    if (lambda_l<0.01 and nfr<l1) or (lambda_l>=0.01 and nfr<l2): return 'segregated'
    if lambda_l>=0.01 and l2<=nfr<=l3: return 'transition'
    if (lambda_l<0.4 and l3<nfr<=l1) or (lambda_l>=0.4 and l3<nfr<=l4): return 'intermittent'
    return 'distributed'

def _h0(reg, lam, nfr):
    c={'segregated':(0.98,0.4846,0.0868),'intermittent':(0.845,0.5351,0.0173),'distributed':(1.065,0.5824,0.0609)}[reg]
    return min(1.0,max(lam,c[0]*lam**c[1]/max(nfr,1e-12)**c[2]))

def beggs_brill_dp_bar(liquid_rate_m3d,length_m,diameter_m,roughness_m,dz_m,pressure_bar,temperature_c,water_cut=0,gor_sm3sm3=0,api=35,gas_sg=.75,water_sg=1.03):
    if diameter_m<=0 or length_m<0: raise ValueError('Invalid pipe geometry')
    pr=mixture_properties(liquid_rate_m3d,water_cut,gor_sm3sm3,pressure_bar,temperature_c,api,gas_sg,water_sg)
    area=math.pi*diameter_m**2/4; vm=pr['q_line_m3s']/area; lam=max(1e-6,min(.999999,pr['liquid_holdup'])); nfr=vm*vm/(G*diameter_m)
    reg=_regime(lam,nfr)
    if reg=='transition':
        l2=.0009252*lam**-2.4684; l3=.1*lam**-1.4516; a=(l3-nfr)/max(l3-l2,1e-12); hl=a*_h0('segregated',lam,nfr)+(1-a)*_h0('intermittent',lam,nfr)
    else: hl=_h0(reg,lam,nfr)
    # Inclination correction, simplified Beggs-Brill beta form with bounded numerical behavior.
    theta=math.asin(max(-1,min(1,dz_m/max(length_m,1e-12)))) if length_m else 0
    corr=1.0
    if abs(theta)>1e-10:
        coeff={'segregated':(.011,-3.768,.3692,-1.614),'intermittent':(2.96,.305,-.4473,.0978),'distributed':(1,0,0,0)}[reg if reg!='transition' else 'intermittent']
        if reg!='distributed':
            nl=max(1e-8,pr['mu']*1e3) # stable proxy liquid velocity number for screening implementation
            arg=max(1e-12,coeff[0]*lam**coeff[1]*max(nfr,1e-8)**coeff[2]*nl**coeff[3]); beta=max(0,(1-lam)*math.log(arg))
            s=math.sin(1.8*theta); corr=max(.2,1+beta*(s-(s**3)/3))
    hl=max(lam,min(1.0,hl*corr))
    st=pr['state']; rho_l=(1-water_cut)*st.oil_density_kgm3+water_cut*st.water_density_kgm3; rho_s=hl*rho_l+(1-hl)*st.gas_density_kgm3
    re=max(1,pr['rho']*abs(vm)*diameter_m/pr['mu']); f=friction_factor(re,roughness_m/diameter_m)
    friction=f*(length_m/diameter_m)*(pr['rho']*vm*vm/2); hydro=rho_s*G*dz_m; sign=1 if liquid_rate_m3d>=0 else -1
    return pa_to_bar(sign*friction+hydro),{**pr,'liquid_holdup':hl,'flow_regime':reg,'mixture_velocity_ms':vm,'reynolds':re,'friction_factor':f}

def pressure_profile(q,length_m,diameter_m,roughness_m,dz_m,p_in_bar,temperature_c,water_cut=0,gor_sm3sm3=0,api=35,gas_sg=.75,segments=30):
    xs=[0.0]; ps=[float(p_in_bar)]; hs=[]; regs=[]
    for i in range(segments):
        p=max(ps[-1],1.0); dp,pr=beggs_brill_dp_bar(q,length_m/segments,diameter_m,roughness_m,dz_m/segments,p,temperature_c,water_cut,gor_sm3sm3,api,gas_sg)
        ps.append(p-dp); xs.append(length_m*(i+1)/segments); hs.append(pr['liquid_holdup']); regs.append(pr['flow_regime'])
    return {'distance_m':xs,'pressure_bar':ps,'holdup':hs,'regime':regs}
