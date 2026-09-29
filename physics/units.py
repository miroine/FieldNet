"""Centralized FieldNet engineering unit conversions.
Internal hydraulic calculations use SI. Public model pressures are bar and rates are m3/d or Sm3/d.
"""
BAR_TO_PA = 1.0e5
DAY_TO_S = 86400.0
INCH_TO_M = 0.0254
FT_TO_M = 0.3048
PSI_TO_PA = 6894.757293168
STB_TO_M3 = 0.158987294928
SCF_TO_SM3 = 0.028316846592
KW_TO_W = 1000.0

def bar_to_pa(x): return float(x) * BAR_TO_PA
def pa_to_bar(x): return float(x) / BAR_TO_PA
def m3d_to_m3s(x): return float(x) / DAY_TO_S
def m3s_to_m3d(x): return float(x) * DAY_TO_S
def inch_to_m(x): return float(x) * INCH_TO_M
def m_to_inch(x): return float(x) / INCH_TO_M
def ft_to_m(x): return float(x) * FT_TO_M
def m_to_ft(x): return float(x) / FT_TO_M
def psi_to_bar(x): return pa_to_bar(float(x) * PSI_TO_PA)
def bar_to_psi(x): return bar_to_pa(x) / PSI_TO_PA
def stb_d_to_m3d(x): return float(x) * STB_TO_M3
def m3d_to_stb_d(x): return float(x) / STB_TO_M3
def scf_to_sm3(x): return float(x) * SCF_TO_SM3
def sm3_to_scf(x): return float(x) / SCF_TO_SM3
