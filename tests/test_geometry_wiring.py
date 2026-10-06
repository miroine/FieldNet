"""Trajectory / completion / bathymetry / riser / correlation choice must actually change the hydraulics."""
import pytest
from solver.equations import pipeline_dp_bar, flowline_segments
from physics.well_model import well_settings, vlp_bhp, solve_well_rate
from physics.trajectory import build_survey


def _edge(**params):
    return {'id': 'e', 'source': 'a', 'target': 'b', 'kind': 'pipeline', 'length_m': 4000.0, 'diameter_m': 0.2, 'roughness_m': 4.5e-5,
            'elevation_change_m': 0.0, 'params': {'water_cut': 0.3, 'gor_sm3sm3': 120.0, 'temperature_c': 40.0, **params}}


def test_default_flowline_path_unchanged():
    e = _edge(); segs = flowline_segments(e); assert sum(s[0] for s in segs) == pytest.approx(4000.0)
    assert pipeline_dp_bar(e, 3000.0, 60.0, 50.0) > 0


def test_bathymetry_profile_with_a_dip_changes_dp():
    flat = pipeline_dp_bar(_edge(), 3000.0, 60.0, 50.0)
    prof = [{'x_m': 0, 'z_m': 0}, {'x_m': 1500, 'z_m': -120}, {'x_m': 2500, 'z_m': -120}, {'x_m': 4000, 'z_m': 0}]
    dip = pipeline_dp_bar(_edge(profile=prof), 3000.0, 60.0, 50.0)
    assert dip != pytest.approx(flat, rel=1e-3)


def test_riser_adds_hydrostatic_head():
    base = pipeline_dp_bar(_edge(), 3000.0, 60.0, 50.0)
    e = _edge(riser={'enabled': True, 'shape': 'vertical', 'water_depth_m': 300.0}); e['length_m'] = 300.0
    r = pipeline_dp_bar(e, 3000.0, 60.0, 50.0)
    assert r > 10.0  # ~300 m of two-phase column
    e2 = _edge(); e2['length_m'] = 300.0; assert r > pipeline_dp_bar(e2, 3000.0, 60.0, 50.0) + 5.0


def test_flowline_correlation_choice_is_used():
    a = pipeline_dp_bar(_edge(correlation='Beggs-Brill'), 3000.0, 60.0, 50.0)
    b = pipeline_dp_bar(_edge(correlation='Homogeneous'), 3000.0, 60.0, 50.0)
    c = pipeline_dp_bar(_edge(correlation='Drift-flux'), 3000.0, 60.0, 50.0)
    assert len({round(a, 4), round(b, 4), round(c, 4)}) == 3


def test_tubing_correlation_choice_changes_bhp():
    p = {'depth_m': 2500.0, 'tubing_id_m': 0.1, 'water_cut': 0.3, 'gor_sm3sm3': 150.0, 'reservoir_pressure_bar': 250.0, 'pi_m3d_bar': 15.0}
    vals = {}
    for c in ('Beggs-Brill', 'Hagedorn-Brown', 'Drift-flux', 'Hasan-Kabir'):
        vals[c] = vlp_bhp(1500.0, 30.0, well_settings({**p, 'vlp_model': c}))[0]
    assert len({round(v, 3) for v in vals.values()}) == 4 and all(100 < v < 400 for v in vals.values())


def test_deviated_trajectory_and_completion_change_vlp_and_rate():
    v = {'depth_m': 2500.0, 'tubing_id_m': 0.1, 'water_cut': 0.3, 'gor_sm3sm3': 150.0, 'reservoir_pressure_bar': 250.0, 'pi_m3d_bar': 15.0}
    survey = build_survey('J', kickoff_md=500.0, build_rate_deg_per_30m=3.0, tangent_inc_deg=60.0, target_tvd=2500.0)
    dev = {**v, 'trajectory': survey}
    s0, s1 = well_settings(v), well_settings(dev)
    assert s1['geometry'] and s1['depth'] == pytest.approx(2500.0, rel=0.02)
    b0, b1 = vlp_bhp(1500.0, 30.0, s0)[0], vlp_bhp(1500.0, 30.0, s1)[0]
    assert b0 != pytest.approx(b1, rel=1e-4)
    comp = {**dev, 'completion': [{'from_md_m': 0, 'to_md_m': 1500, 'id_m': 0.12}, {'from_md_m': 1500, 'to_md_m': 9000, 'id_m': 0.0762}]}
    assert vlp_bhp(1500.0, 30.0, well_settings(comp))[0] != pytest.approx(b1, rel=1e-4)
    assert solve_well_rate(30.0, s1)[0] > 0
