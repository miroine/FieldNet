"""FieldNet v15 completion and well deliverability helpers.

Engineering-planning models: explicit geometry/skin/non-Darcy terms with transparent
assumptions. These are not a substitute for calibrated well-test or vendor lift models.
"""
from dataclasses import dataclass, asdict
import math

@dataclass
class CompletionResult:
    kh_md_m: float
    effective_skin: float
    non_darcy_skin: float
    pi_m3d_bar: float
    drawdown_bar: float
    pwf_bar: float
    warnings: list


def radial_pi_m3d_bar(k_md, h_m, mu_cp, bo, re_m, rw_m, skin=0.0):
    """Pseudo-steady radial oil PI using field-equation conversion.

    Converts SI geometry to ft and returns m3/d/bar. The denominator is guarded
    against nonphysical geometry and excessive negative skin.
    """
    if min(k_md,h_m,mu_cp,bo,re_m,rw_m) <= 0 or re_m <= rw_m:
        raise ValueError('Invalid completion/reservoir inputs')
    h_ft=h_m*3.280839895; re_ft=re_m*3.280839895; rw_ft=rw_m*3.280839895
    denom=math.log(re_ft/rw_ft)-0.75+float(skin)
    if denom <= 0.05: raise ValueError('Radial-flow denominator is nonphysical')
    # q(stb/d)=0.00708*k(md)*h(ft)*dp(psi)/(mu(cp)*B*denom)
    j_stbd_psi=0.00708*k_md*h_ft/(mu_cp*bo*denom)
    return j_stbd_psi*0.158987294928/0.0689475729


def non_darcy_skin(rate_m3d, d_factor_per_m3d=0.0):
    """Rate-dependent skin S_nd = D*|q| for transparent screening."""
    return max(0.0,float(d_factor_per_m3d))*abs(float(rate_m3d))


def completion_deliverability(rate_m3d, reservoir_pressure_bar, *, k_md, h_m,
                               mu_cp=1.0, bo=1.2, re_m=300.0, rw_m=0.1,
                               skin=0.0, d_factor_per_m3d=0.0,
                               completion_efficiency=1.0):
    eff=float(completion_efficiency)
    if not (0 < eff <= 1.0): raise ValueError('completion_efficiency must be in (0,1]')
    snd=non_darcy_skin(rate_m3d,d_factor_per_m3d)
    seff=float(skin)+snd
    j=radial_pi_m3d_bar(k_md,h_m,mu_cp,bo,re_m,rw_m,seff)*eff
    dd=abs(float(rate_m3d))/max(j,1e-12)
    warnings=[]
    if seff > 20: warnings.append('high_effective_skin')
    if dd > 0.8*float(reservoir_pressure_bar): warnings.append('extreme_drawdown')
    return asdict(CompletionResult(k_md*h_m,seff,snd,j,dd,max(float(reservoir_pressure_bar)-dd,0.0),warnings))


def combine_completion_intervals(intervals):
    """Combine independent completion intervals by summing their PIs."""
    if not intervals: raise ValueError('At least one interval is required')
    total=0.0; details=[]
    for item in intervals:
        j=radial_pi_m3d_bar(**item)
        total += j; details.append(j)
    return {'pi_m3d_bar':total,'interval_pi_m3d_bar':details,'n_intervals':len(details)}
