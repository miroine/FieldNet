from physics.units import DAY_TO_S as DAY

def pi_rate_m3s(pr_bar,pwf_bar,pi_m3d_bar): return max(0.0,pi_m3d_bar*(pr_bar-pwf_bar))/DAY

def vogel_rate_m3s(pr_bar,pwf_bar,qmax_m3d):
    if pr_bar<=0: return 0.0
    x=min(max(pwf_bar/pr_bar,0.0),1.0)
    return max(0.0,qmax_m3d*(1-0.2*x-0.8*x*x))/DAY

def ipr_rate_m3d(pr_bar,pwf_bar,model='PI',pi_m3d_bar=10.0,qmax_m3d=1000.0):
    return (vogel_rate_m3s(pr_bar,pwf_bar,qmax_m3d) if model=='Vogel' else pi_rate_m3s(pr_bar,pwf_bar,pi_m3d_bar))*DAY
