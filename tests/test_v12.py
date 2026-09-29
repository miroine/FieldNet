from physics.pvt_v12 import standing_solution_gor, standing_black_oil, tabulated_black_oil
from solver.diagnostics import solver_diagnostics
from ui.theme import THEMES

def test_standing_rs_monotonic_and_capped():
    assert standing_solution_gor(50,60) < standing_solution_gor(100,60) < standing_solution_gor(150,60)
    assert standing_solution_gor(250,60) == standing_solution_gor(150,60)

def test_standing_black_oil_physical():
    s=standing_black_oil(100,70)
    assert 450 < s.oil_density_kgm3 < 1000 and 0.8 < s.oil_fvf < 2.5 and s.gas_density_kgm3>0

def test_tabulated_interpolation():
    base={'water_density_kgm3':1000,'oil_viscosity_pas':.003,'water_viscosity_pas':.001,'gas_viscosity_pas':1e-5,'gas_z':.9}
    rows=[dict(base,pressure_bar=100,oil_density_kgm3=800,gas_density_kgm3=70,solution_gor_sm3sm3=80,oil_fvf=1.1),dict(base,pressure_bar=200,oil_density_kgm3=760,gas_density_kgm3=140,solution_gor_sm3sm3=120,oil_fvf=1.2)]
    s=tabulated_black_oil(150,60,rows)
    assert abs(s.oil_density_kgm3-780)<1e-9 and abs(s.solution_gor_sm3sm3-100)<1e-9

def test_diagnostics_and_themes():
    d=solver_diagnostics({'success':False,'max_abs_residual':1e-2,'violations':1},{'A':-1},{'E':1e8})
    assert len(d)>=4
    assert 'Equinor-inspired Light' in THEMES and 'Equinor-inspired Dark' in THEMES
