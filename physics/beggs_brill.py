import math
from physics.multiphase import mixture_properties
from physics.hydraulics import friction_factor, G
from physics.units import DAY_TO_S as DAY, pa_to_bar

# Beggs & Brill (1973, revised 1977) inclination-correction coefficients (e, f, g, h)
# for C = (1-lambda) ln(e * lambda^f * NLV^g * NFR^h).
_UPHILL={'segregated':(0.011,-3.768,3.539,-1.614),'intermittent':(2.96,0.305,-0.4473,0.0978)}
_DOWNHILL=(4.70,-0.3692,0.1244,-0.5056)
_SIGMA_N_M=0.025  # screening gas/liquid surface tension used in the liquid velocity number
_BLEND=0.15      # half-width in ln(NFR) of the smoothed distributed-regime boundary

def _regime(lambda_l, nfr):
    l1=316*lambda_l**0.302; l2=0.0009252*lambda_l**-2.4684; l3=0.1*lambda_l**-1.4516; l4=0.5*lambda_l**-6.738
    if (lambda_l<0.01 and nfr<l1) or (lambda_l>=0.01 and nfr<l2): return 'segregated'
    if lambda_l>=0.01 and l2<=nfr<=l3: return 'transition'
    if (0.01<=lambda_l<0.4 and l3<nfr<=l1) or (lambda_l>=0.4 and l3<nfr<=l4): return 'intermittent'
    return 'distributed'

def _h0(reg, lam, nfr):
    c={'segregated':(0.98,0.4846,0.0868),'intermittent':(0.845,0.5351,0.0173),'distributed':(1.065,0.5824,0.0609)}[reg]
    return min(1.0,max(lam,c[0]*lam**c[1]/max(nfr,1e-12)**c[2]))

def _psi(reg, lam, nfr, nlv, theta):
    """Inclination correction factor psi for one flow pattern."""
    if abs(theta)<1e-10: return 1.0
    if theta>0:
        if reg=='distributed': return 1.0
        e,f,g,h=_UPHILL[reg]
    else:
        e,f,g,h=_DOWNHILL
    arg=e*lam**f*max(nlv,1e-8)**g*max(nfr,1e-8)**h
    c=max(0.0,(1-lam)*math.log(max(arg,1e-12)))
    s=math.sin(1.8*theta)
    return 1.0+c*(s-(s**3)/3.0)

def _two_phase_s(lam, hl):
    """Beggs-Brill friction-factor ratio exponent S (ftp = fn * exp(S))."""
    y=lam/max(hl*hl,1e-12)
    if y<=0: return 0.0
    if 1.0<y<1.2: return math.log(2.2*y-1.2)
    ly=math.log(y)
    den=-0.0523+3.182*ly-0.8725*ly*ly+0.01853*ly**4
    if abs(den)<1e-9: return 0.0
    return max(-2.0,min(2.0,ly/den))

def beggs_brill_dp_bar(liquid_rate_m3d,length_m,diameter_m,roughness_m,dz_m,pressure_bar,temperature_c,water_cut=0,gor_sm3sm3=0,api=35,gas_sg=.75,water_sg=1.03,
                       extra_free_gas_sm3d=0.0):
    """Beggs-Brill pressure drop [bar] over one segment evaluated at ``pressure_bar``.

    Positive result = pressure loss in the direction of positive flow. Screening
    implementation: acceleration is neglected, surface tension is a fixed constant.
    """
    if diameter_m<=0 or length_m<0: raise ValueError('Invalid pipe geometry')
    pr=mixture_properties(liquid_rate_m3d,water_cut,gor_sm3sm3,pressure_bar,temperature_c,api,gas_sg,water_sg,extra_free_gas_sm3d)
    area=math.pi*diameter_m**2/4; vm=pr['q_line_m3s']/area; lam=max(1e-6,min(.999999,pr['liquid_holdup'])); nfr=vm*vm/(G*diameter_m)
    st=pr['state']; wc=min(max(float(water_cut),0.0),0.9999)
    rho_l=(1-wc)*st.oil_density_kgm3+wc*st.water_density_kgm3
    vsl=lam*vm; nlv=vsl*(rho_l/(G*_SIGMA_N_M))**0.25
    # Flow direction matters for the inclination: a segment rising along +flow is uphill.
    theta=math.asin(max(-1,min(1,dz_m/max(length_m,1e-12)))) if length_m else 0.0
    if liquid_rate_m3d<0: theta=-theta
    reg=_regime(lam,nfr)
    def hl_of(r):
        if r=='transition':
            l2=.0009252*lam**-2.4684; l3=.1*lam**-1.4516; a=min(max((l3-nfr)/max(l3-l2,1e-12),0.0),1.0)
            return a*_h0('segregated',lam,nfr)*_psi('segregated',lam,nfr,nlv,theta)+(1-a)*_h0('intermittent',lam,nfr)*_psi('intermittent',lam,nfr,nlv,theta)
        return _h0(r,lam,nfr)*_psi(r,lam,nfr,nlv,theta)
    hl=hl_of(reg)
    # The original map jumps from intermittent/segregated to distributed at NFR = L1 (or L4),
    # giving a step change in holdup. Such steps leave well IPR/VLP equations without an exact
    # root, so the network solver stalls. Blend the two sides over a narrow band in ln(NFR).
    upper=316*lam**0.302 if lam<0.4 else 0.5*lam**-6.738
    below='segregated' if lam<0.01 else 'intermittent'
    x=(math.log(max(nfr,1e-12))-math.log(max(upper,1e-12)))/_BLEND
    if -1.0<x<1.0:
        w=0.5+0.5*math.sin(0.5*math.pi*x)  # 0 below band, 1 above
        hl=(1-w)*hl_of(below)+w*hl_of('distributed')
    hl=min(1.0,max(1e-6,hl))
    rho_s=hl*rho_l+(1-hl)*st.gas_density_kgm3
    re=max(1,pr['rho']*abs(vm)*diameter_m/pr['mu']); fn=friction_factor(re,roughness_m/diameter_m)
    f=fn*math.exp(_two_phase_s(lam,hl))
    friction=f*(length_m/diameter_m)*(pr['rho']*vm*vm/2); hydro=rho_s*G*dz_m; sign=1 if liquid_rate_m3d>=0 else -1
    return pa_to_bar(sign*friction+hydro),{**pr,'liquid_holdup':hl,'no_slip_holdup':lam,'flow_regime':reg,'mixture_velocity_ms':vm,'reynolds':re,'friction_factor':f,'no_slip_friction_factor':fn}

def pressure_profile(q,length_m,diameter_m,roughness_m,dz_m,p_in_bar,temperature_c,water_cut=0,gor_sm3sm3=0,api=35,gas_sg=.75,segments=30):
    xs=[0.0]; ps=[float(p_in_bar)]; hs=[]; regs=[]
    for i in range(segments):
        p=max(ps[-1],1.0); dp,pr=beggs_brill_dp_bar(q,length_m/segments,diameter_m,roughness_m,dz_m/segments,p,temperature_c,water_cut,gor_sm3sm3,api,gas_sg)
        ps.append(p-dp); xs.append(length_m*(i+1)/segments); hs.append(pr['liquid_holdup']); regs.append(pr['flow_regime'])
    return {'distance_m':xs,'pressure_bar':ps,'holdup':hs,'regime':regs}
