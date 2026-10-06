import math

import pytest

from physics.trajectory import (build_survey, survey_from_rows, tubing_segments, validate_completion,
                                well_total_depth, trajectory_summary)

NAN = float('nan')


def _sum(segs, k):
    return sum(s[k] for s in segs)


# ---------------------------------------------------------------- fallback equivalence
def test_fallback_is_todays_vertical_march():
    p = {'depth_m': 2400.0, 'tubing_id_m': 0.1, 'tubing_roughness_m': 3e-5}
    segs = tubing_segments(p, max_segments=12)
    assert len(segs) == 12
    for i, s in enumerate(segs):
        assert s['length_m'] == pytest.approx(200.0)
        assert s['dz_m'] == pytest.approx(200.0)
        assert s['id_m'] == 0.1 and s['roughness_m'] == 3e-5
        assert s['md_top'] == pytest.approx(i * 200.0) and s['md_bot'] == pytest.approx((i + 1) * 200.0)
        assert s['inc_deg'] == 0.0
    assert well_total_depth(p) == (2400.0, 2400.0)


def test_fallback_defaults_and_blank_inputs():
    segs = tubing_segments({}, max_segments=10)
    assert len(segs) == 10 and _sum(segs, 'length_m') == pytest.approx(2000.0)
    assert segs[0]['id_m'] == pytest.approx(0.0762)
    # blank trajectory/completion editors (NaN rows) behave like absent
    p = {'depth_m': 1000.0, 'trajectory': [{'md_m': NAN, 'tvd_m': NAN}, {}], 'completion': [{'from_md_m': None}]}
    assert tubing_segments(p, 5) == tubing_segments({'depth_m': 1000.0}, 5)


def test_vertical_survey_with_one_completion_row_matches_vertical_totals():
    p = {'depth_m': 1800.0, 'completion': [{'from_md_m': 0, 'to_md_m': 1800, 'id_m': 0.1}]}
    segs = tubing_segments(p, 24)
    assert _sum(segs, 'length_m') == pytest.approx(1800.0) and _sum(segs, 'dz_m') == pytest.approx(1800.0)
    assert all(s['id_m'] == 0.1 for s in segs)


# ---------------------------------------------------------------- generators
def test_vertical_generator():
    s = build_survey('vertical', depth=1500)
    assert s[0]['md_m'] == 0 and s[-1]['md_m'] == pytest.approx(1500) and s[-1]['tvd_m'] == pytest.approx(1500)
    assert all(r['inc_deg'] == 0 for r in s)


def test_J_well_geometry():
    bur, tinc, kop, tgt = 3.0, 45.0, 500.0, 2500.0
    s = build_survey('J', kickoff_md=kop, build_rate_deg_per_30m=bur, tangent_inc_deg=tinc, target_tvd=tgt)
    R = 30 * 180 / (math.pi * bur)
    tvd_build = kop + R * math.sin(math.radians(tinc))
    # find end of build
    end_build = next(r for r in s if r['inc_deg'] >= tinc - 1e-9)
    assert end_build['md_m'] == pytest.approx(kop + tinc / bur * 30)
    assert end_build['tvd_m'] == pytest.approx(tvd_build, rel=1e-9)
    assert s[-1]['tvd_m'] == pytest.approx(tgt)
    tan_len = (tgt - tvd_build) / math.cos(math.radians(tinc))
    assert s[-1]['md_m'] == pytest.approx(end_build['md_m'] + tan_len)
    assert max(r['inc_deg'] for r in s) == pytest.approx(tinc)
    assert min(r['inc_deg'] for r in s) == 0
    mds = [r['md_m'] for r in s]
    assert all(b > a for a, b in zip(mds[:-1], mds[1:]))
    assert all(b['tvd_m'] >= a['tvd_m'] for a, b in zip(s[:-1], s[1:]))
    assert all(b['tvd_m'] - a['tvd_m'] <= b['md_m'] - a['md_m'] + 1e-9 for a, b in zip(s[:-1], s[1:]))


def test_S_well_geometry():
    s = build_survey('S', kickoff_md=400, build_rate_deg_per_30m=2.0, tangent_inc_deg=30, target_tvd=3000,
                     drop_rate_deg_per_30m=1.5, final_inc_deg=5, final_hold_m=200)
    assert s[-1]['tvd_m'] == pytest.approx(3000)
    assert s[-1]['inc_deg'] == pytest.approx(5)
    assert max(r['inc_deg'] for r in s) == pytest.approx(30)
    assert s[-1]['md_m'] > 3000
    d = build_survey('S', kickoff_md=400, build_rate_deg_per_30m=2.0, tangent_inc_deg=30, target_tvd=3000)
    assert d[-1]['inc_deg'] == pytest.approx(0)  # default: drop back to vertical
    assert d[-1]['tvd_m'] == pytest.approx(3000)


def test_horizontal_geometry():
    s = build_survey('horizontal', kickoff_md=1000, build_rate_deg_per_30m=6, lateral_length=1500)
    R = 30 * 180 / (math.pi * 6)
    heel = next(r for r in s if r['inc_deg'] >= 90 - 1e-9)
    assert heel['md_m'] == pytest.approx(1000 + 15 * 30)
    assert heel['tvd_m'] == pytest.approx(1000 + R, rel=1e-9)
    assert s[-1]['md_m'] == pytest.approx(heel['md_m'] + 1500)
    assert s[-1]['tvd_m'] == pytest.approx(heel['tvd_m'])  # lateral gains no TVD
    assert max(r['inc_deg'] for r in s) == pytest.approx(90)


@pytest.mark.parametrize('kind,kw,msg', [
    ('J', dict(kickoff_md=500, build_rate_deg_per_30m=3, tangent_inc_deg=45, target_tvd=600), 'shallower'),
    ('J', dict(kickoff_md=500, build_rate_deg_per_30m=0, tangent_inc_deg=45, target_tvd=2000), 'build_rate'),
    ('J', dict(kickoff_md=500, build_rate_deg_per_30m=3, tangent_inc_deg=45), 'target_tvd'),
    ('J', dict(kickoff_md=500, build_rate_deg_per_30m=3, tangent_inc_deg=120, target_tvd=2000), 'tangent_inc_deg'),
    ('zigzag', dict(), 'unknown kind'),
    ('vertical', dict(depth=-5), 'depth'),
])
def test_generator_errors(kind, kw, msg):
    with pytest.raises(ValueError) as e:
        build_survey(kind, **kw)
    assert msg in str(e.value)


# ---------------------------------------------------------------- survey_from_rows
def test_inc_rows_min_curvature_matches_generator():
    gen = build_survey('J', kickoff_md=300, build_rate_deg_per_30m=3, tangent_inc_deg=30, target_tvd=2000, step_m=10)
    sparse = [{'md_m': r['md_m'], 'inc_deg': r['inc_deg'], 'azimuth': 12} for r in gen]
    out = survey_from_rows(sparse)
    assert out[-1]['tvd_m'] == pytest.approx(gen[-1]['tvd_m'], rel=1e-9)
    # a single arc spanning the whole build is exact in the 2-D circular form
    big = survey_from_rows([{'md_m': 0, 'inc_deg': 0}, {'md_m': 300, 'inc_deg': 0}, {'md_m': 600, 'inc_deg': 30}])
    ref = build_survey('J', kickoff_md=300, build_rate_deg_per_30m=3, tangent_inc_deg=30, target_tvd=2000)
    ref_build = next(r for r in ref if r['inc_deg'] >= 30 - 1e-9)
    assert big[-1]['tvd_m'] == pytest.approx(300 + 300 * (math.sin(math.radians(30)) / math.radians(30)))
    assert big[-1]['tvd_m'] == pytest.approx(ref_build['tvd_m'] - 0.0, rel=1e-6)


def test_tvd_rows_compute_inclination_and_prepend_surface():
    out = survey_from_rows([{'md_m': 1000, 'tvd_m': 1000}, {'md_m': 2000, 'tvd_m': 1500}])
    assert [r['md_m'] for r in out] == [0.0, 1000.0, 2000.0]
    assert out[0]['inc_deg'] == pytest.approx(0)
    assert out[1]['inc_deg'] == pytest.approx(60)
    assert out[2]['inc_deg'] == pytest.approx(60)


def test_nan_and_blank_rows_are_skipped():
    rows = [{'md_m': 0, 'tvd_m': 0}, {'md_m': NAN, 'tvd_m': 5}, {'md_m': 500, 'tvd_m': NAN}, {'md_m': '', 'tvd_m': ''},
            None, {'md_m': 1000, 'tvd_m': '1000'}, {}]
    out = survey_from_rows(rows)
    assert len(out) == 2 and out[-1]['tvd_m'] == 1000.0


@pytest.mark.parametrize('rows,msg', [
    ([{'md_m': 0, 'tvd_m': 0}, {'md_m': 100, 'tvd_m': 100}, {'md_m': 100, 'tvd_m': 120}], 'row 3'),
    ([{'md_m': 0, 'tvd_m': 0}, {'md_m': 200, 'tvd_m': 190}, {'md_m': 150, 'tvd_m': 190}], 'strictly increasing'),
    ([{'md_m': 0, 'tvd_m': 0}, {'md_m': 100, 'tvd_m': 100}, {'md_m': 200, 'tvd_m': 50}], 'TVD decreases'),
    ([{'md_m': 0, 'tvd_m': 0}, {'md_m': 100, 'tvd_m': 400}], 'faster than MD'),
    ([{'md_m': 0, 'inc_deg': 0}, {'md_m': 100, 'inc_deg': 200}], 'outside 0..180'),
    ([{'md_m': 0, 'tvd_m': 0}, {'md_m': 100, 'inc_deg': 10}], 'mix'),
    ([{'md_m': NAN, 'tvd_m': NAN}], 'no valid rows'),
    ([{'md_m': 0, 'tvd_m': 0}], 'at least 2'),
])
def test_survey_errors(rows, msg):
    with pytest.raises(ValueError) as e:
        survey_from_rows(rows)
    assert msg.lower() in str(e.value).lower()


def test_tvd_small_decrease_within_tolerance_ok():
    out = survey_from_rows([{'md_m': 0, 'tvd_m': 0}, {'md_m': 100, 'tvd_m': 100}, {'md_m': 200, 'tvd_m': 99.8}])
    assert len(out) == 3


# ---------------------------------------------------------------- tubing_segments, deviated
def _deviated(extra=None):
    p = {'trajectory': build_survey('J', kickoff_md=500, build_rate_deg_per_30m=3, tangent_inc_deg=45, target_tvd=2500),
         'tubing_id_m': 0.1}
    p.update(extra or {})
    return p


def test_deviated_sums_and_ordering():
    p = _deviated()
    md, tvd = well_total_depth(p)
    assert tvd == pytest.approx(2500)
    segs = tubing_segments(p, 24)
    assert 1 < len(segs) <= 24
    assert _sum(segs, 'length_m') == pytest.approx(md) and _sum(segs, 'dz_m') == pytest.approx(tvd)
    assert segs[0]['md_top'] == 0 and segs[-1]['md_bot'] == pytest.approx(md)
    for a, b in zip(segs[:-1], segs[1:]):
        assert b['md_top'] == pytest.approx(a['md_bot'])
    assert all(s['dz_m'] <= s['length_m'] + 1e-9 and s['dz_m'] >= 0 for s in segs)
    assert segs[0]['inc_deg'] == pytest.approx(0, abs=1e-6)
    assert segs[-1]['inc_deg'] == pytest.approx(45, abs=1e-6)
    maxlen = max(md / 24, 25.0)
    assert max(s['length_m'] for s in segs) <= maxlen * 1.2


def test_max_segments_respected_and_small_wells():
    p = _deviated()
    for n in (1, 3, 6, 12, 40):
        segs = tubing_segments(p, n)
        assert len(segs) <= n
        assert _sum(segs, 'dz_m') == pytest.approx(2500)
    short = tubing_segments({'trajectory': build_survey('vertical', depth=60)}, 24)
    assert len(short) == 3  # 25 m floor: ceil(60/25)


def test_horizontal_well_segments_have_zero_dz_in_lateral():
    p = {'trajectory': build_survey('horizontal', kickoff_md=800, build_rate_deg_per_30m=6, lateral_length=1200)}
    segs = tubing_segments(p, 24)
    lateral = [s for s in segs if s['md_top'] >= 800 + 15 * 30 - 1e-6]
    assert lateral and all(abs(s['dz_m']) < 1e-9 and s['inc_deg'] == pytest.approx(90) for s in lateral)


# ---------------------------------------------------------------- completion
def test_completion_boundaries_and_ids():
    p = _deviated({'completion': [
        {'from_md_m': 0, 'to_md_m': 1500, 'id_m': 0.1143, 'label': 'Tubing 4.5in'},
        {'from_md_m': 1500, 'to_md_m': 4000, 'id_m': 0.0762, 'roughness_m': 1e-5, 'label': 'Liner'}]})
    segs = tubing_segments(p, 24)
    assert any(abs(s['md_bot'] - 1500) < 1e-9 for s in segs)
    for s in segs:
        if s['md_bot'] <= 1500 + 1e-9:
            assert s['id_m'] == 0.1143 and s['label'] == 'Tubing 4.5in'
        else:
            assert s['id_m'] == 0.0762 and s['roughness_m'] == 1e-5 and s['label'] == 'Liner'
    md, tvd = well_total_depth(p)
    assert _sum(segs, 'length_m') == pytest.approx(md) and _sum(segs, 'dz_m') == pytest.approx(tvd)
    assert trajectory_summary(p)['n_completion_sections'] == 2
    assert trajectory_summary(p)['min_id_m'] == 0.0762
    assert validate_completion(p) != [] or md <= 4000  # ends at 4000 beyond TD -> reported
    assert any('below TD' in w for w in validate_completion(p)) == (md < 4000)


def test_completion_overlap_later_row_wins():
    p = {'depth_m': 3000, 'completion': [
        {'from_md_m': 0, 'to_md_m': 2000, 'id_m': 0.10},
        {'from_md_m': 1000, 'to_md_m': 3000, 'id_m': 0.08}]}
    segs = tubing_segments(p, 24)
    for s in segs:
        mid = 0.5 * (s['md_top'] + s['md_bot'])
        assert s['id_m'] == (0.10 if mid < 1000 else 0.08)
    assert any(abs(s['md_top'] - 1000) < 1e-9 for s in segs)
    assert any('overlap' in w for w in validate_completion(p))


def test_completion_gap_uses_previous_id_and_tail_uses_last():
    p = {'depth_m': 3000, 'completion': [
        {'from_md_m': 200, 'to_md_m': 1000, 'id_m': 0.10},
        {'from_md_m': 1500, 'to_md_m': 2000, 'id_m': 0.07}]}
    segs = tubing_segments(p, 24)
    for s in segs:
        mid = 0.5 * (s['md_top'] + s['md_bot'])
        want = 0.10 if mid < 1500 else 0.07  # [0,200) -> first row, gap -> previous, tail -> last
        assert s['id_m'] == want
    w = ' | '.join(validate_completion(p))
    assert 'starts at MD 200' in w and 'gap' in w and 'ends at MD 2000' in w


def test_completion_invalid_rows_skipped_and_reported():
    p = {'depth_m': 1000, 'tubing_id_m': 0.09, 'completion': [
        {'from_md_m': 0, 'to_md_m': 500, 'id_m': 0.0},
        {'from_md_m': 800, 'to_md_m': 600, 'id_m': 0.05},
        {'from_md_m': 0, 'to_md_m': NAN, 'id_m': 0.05},
        {'from_md_m': NAN, 'to_md_m': NAN, 'id_m': NAN},
        {'from_md_m': 0, 'to_md_m': 1000, 'id_m': 0.06}]}
    w = validate_completion(p)
    assert len(w) == 3 and any('id_m must be > 0' in x for x in w) and any('must be greater' in x for x in w)
    assert all(s['id_m'] == 0.06 for s in tubing_segments(p, 10))
    # only invalid rows -> default tubing from params
    q = {'depth_m': 1000, 'tubing_id_m': 0.09, 'completion': [{'from_md_m': 5, 'to_md_m': 1, 'id_m': 0.05}]}
    assert tubing_segments(q, 4)[0]['id_m'] == 0.09


def test_many_sections_merge_to_cap_and_keep_sums():
    comp = [{'from_md_m': 100 * i, 'to_md_m': 100 * (i + 1), 'id_m': 0.05 + 0.002 * i} for i in range(20)]
    p = {'depth_m': 2000, 'completion': comp}
    segs = tubing_segments(p, 8)
    assert len(segs) <= 8
    assert _sum(segs, 'length_m') == pytest.approx(2000) and _sum(segs, 'dz_m') == pytest.approx(2000)
    ids = [s['id_m'] for s in segs]
    assert min(ids) >= 0.05 - 1e-12 and max(ids) <= 0.05 + 0.002 * 19 + 1e-12


def test_completion_with_survey_trims_at_td():
    p = {'depth_m': 1000, 'completion': [{'from_md_m': 0, 'to_md_m': 5000, 'id_m': 0.1}]}
    segs = tubing_segments(p, 10)
    assert segs[-1]['md_bot'] == pytest.approx(1000)
    assert any('below TD' in w for w in validate_completion(p))


# ---------------------------------------------------------------- summary
def test_summary_values():
    p = _deviated()
    s = trajectory_summary(p)
    assert s['max_inc_deg'] == pytest.approx(45)
    assert s['max_dls_deg_per_30m'] == pytest.approx(3.0, rel=1e-6)
    assert s['tvd_m'] == pytest.approx(2500) and s['md_m'] > 2500
    assert s['true_vertical_fraction'] == pytest.approx(s['tvd_m'] / s['md_m'])
    assert s['min_id_m'] == 0.1 and not s['is_vertical'] and s['has_trajectory']
    v = trajectory_summary({'depth_m': 1500})
    assert v['is_vertical'] and v['true_vertical_fraction'] == 1.0 and v['max_dls_deg_per_30m'] == 0
    assert v['n_completion_sections'] == 1 and not v['has_trajectory']


def test_invalid_trajectory_reported_softly():
    p = {'trajectory': [{'md_m': 0, 'tvd_m': 0}, {'md_m': 100, 'tvd_m': 100}, {'md_m': 90, 'tvd_m': 100}]}
    w = validate_completion(p)
    assert len(w) == 1 and w[0].startswith('Trajectory invalid')
    with pytest.raises(ValueError):
        tubing_segments(p)
