def choke_dp_bar(q_m3d, cv=80.0, rho_kgm3=850.0):
    """Liquid-equivalent choke screening equation. Cv is a model coefficient, not API/ISA Cv.

    Signed: the pressure drop follows the flow direction (reverse flow gives a negative
    drop). The previous version returned a positive drop for reverse flow, which made the
    network equations inconsistent whenever a choke carried back-flow.
    """
    cv=max(float(cv),1e-9); q=float(q_m3d); return (q*abs(q)/(cv*cv))*(float(rho_kgm3)/1000.0)
