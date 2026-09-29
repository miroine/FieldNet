def choke_dp_bar(q_m3d, cv=80.0, rho_kgm3=850.0):
    """Liquid-equivalent control-valve screening equation. Cv is a model coefficient, not API/ISA Cv."""
    cv=max(float(cv),1e-9); q=abs(float(q_m3d)); return (q/cv)**2*(rho_kgm3/1000.0)
