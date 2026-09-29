"""FieldNet v13 advanced well screening models."""

def skin_adjusted_pi(pi_base_m3d_bar, skin=0.0, reference_skin_factor=1.0):
    """Screening productivity correction: J=J0/(1+S/C), bounded positive."""
    j0=max(float(pi_base_m3d_bar),0.0); c=max(float(reference_skin_factor),1e-9)
    denom=max(1.0+float(skin)/c,0.05)
    return j0/denom

def artificial_lift_assist_bar(lift_type='none', setting=0.0, max_assist_bar=150.0):
    """Equivalent pressure assistance for screening nodal analysis.
    ESP/gas-lift inputs are explicit engineering assumptions, not vendor models.
    """
    if str(lift_type).lower() in ('none','off',''): return 0.0
    return min(max(float(setting),0.0),max(float(max_assist_bar),0.0))

def effective_vlp_bhp_bar(natural_bhp_bar, assist_bar):
    return max(float(natural_bhp_bar)-max(float(assist_bar),0.0),0.0)
