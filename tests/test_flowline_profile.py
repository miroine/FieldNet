import math

import numpy as np
import pytest

from physics.flowline_profile import (profile_points, profile_segments, total_length, elevation_change, summarize,
                                      riser_profile, parse_profile_text, profile_to_rows, slug_indicators, _extrema)

NAN = float('nan')


def _rand_profile(seed, n):
    rng = np.random.default_rng(seed)
    x = np.concatenate([[0.0], np.cumsum(rng.uniform(5, 400, n - 1))])
    z = np.cumsum(rng.normal(0, 8, n)) - 300
    return [(float(a), float(b)) for a, b in zip(x, z)]


# ---------------------------------------------------------------- profile_points
def test_fallback_straight_line_uses_true_length():
    pts = profile_points({'length_m': 5000.0, 'elevation_change_m': 300.0})
    assert pts[0] == (0.0, 0.0)
    assert total_length(pts) == pytest.approx(5000.0)
    assert elevation_change(pts) == pytest.approx(300.0)
    assert pts[1][0] == pytest.approx(math.sqrt(5000.0 ** 2 - 300.0 ** 2))
    segs = profile_segments(pts)
    assert len(segs) == 1 and segs[0]['length_m'] == pytest.approx(5000.0) and segs[0]['dz_m'] == pytest.approx(300.0)


def test_fallback_defaults_and_steep_cases():
    assert total_length(profile_points({})) == pytest.approx(1000.0)
    pts = profile_points({'length_m': 100.0, 'elevation_change_m': 150.0})  # |dz| > L -> vertical
    assert pts == [(0.0, 0.0), (0.0, 150.0)]
    with pytest.raises(ValueError):
        profile_points({'length_m': 0})


def test_profile_rows_sorted_cleaned_and_deduped():
    e = {'length_m': 1.0, 'params': {'profile': [
        {'x_m': 1000, 'z_m': -120}, {'x_m': 0, 'z_m': -100}, {'x_m': NAN, 'z_m': 3}, {'x_m': 500, 'z_m': ''},
        {'x_m': 500, 'z_m': -150}, {'x_m': 500, 'z_m': -150}, {}, {'x_m': '2000', 'z_m': '-90'}]}}
    pts = profile_points(e)
    assert pts == [(0.0, -100.0), (500.0, -150.0), (1000.0, -120.0), (2000.0, -90.0)]


def test_profile_too_few_points_error_and_blank_fallback():
    with pytest.raises(ValueError) as ex:
        profile_points({'params': {'profile': [{'x_m': 0, 'z_m': -10}, {'x_m': NAN, 'z_m': 4}]}})
    assert 'at least 2' in str(ex.value)
    # a fully blank editor behaves like no profile
    assert profile_points({'length_m': 800, 'params': {'profile': [{'x_m': NAN, 'z_m': NAN}]}}) == \
        profile_points({'length_m': 800})


def test_vertical_steps_keep_order():
    e = {'params': {'profile': [{'x_m': 0, 'z_m': -50}, {'x_m': 100, 'z_m': -50}, {'x_m': 100, 'z_m': 0}]}}
    assert profile_points(e) == [(0.0, -50.0), (100.0, -50.0), (100.0, 0.0)]


# ---------------------------------------------------------------- segments
@pytest.mark.parametrize('seed', [1, 2, 3, 4, 5, 6, 7, 8])
def test_segment_sum_invariants_random(seed):
    pts = _rand_profile(seed, 60)
    for cap in (2, 5, 24, 100):
        segs = profile_segments(pts, cap)
        assert sum(s['length_m'] for s in segs) == pytest.approx(total_length(pts), rel=1e-12)
        assert sum(s['dz_m'] for s in segs) == pytest.approx(pts[-1][1] - pts[0][1], abs=1e-9)
        assert segs[0]['x0'] == pts[0][0] and segs[-1]['x1'] == pts[-1][0]
        for a, b in zip(segs[:-1], segs[1:]):
            assert a['x1'] == b['x0'] and a['z1'] == b['z0']
        assert all(-90 <= s['inc_deg'] <= 90 for s in segs)


@pytest.mark.parametrize('seed', range(10))
def test_merging_never_crosses_high_or_low_points(seed):
    pts = _rand_profile(100 + seed, 50)
    ex = [i for i, _ in _extrema(pts)]
    segs = profile_segments(pts, 24)
    bounds = {(s['x0'], s['z0']) for s in segs} | {(segs[-1]['x1'], segs[-1]['z1'])}
    assert all(pts[i] in bounds for i in ex)
    assert len(segs) <= max(24, len(ex) + 1)


def test_all_vertices_kept_up_to_cap():
    pts = _rand_profile(9, 20)
    assert len(profile_segments(pts, 24)) == 19
    assert len(profile_segments(pts, 19)) == 19


def test_merge_prefers_similar_slopes():
    # gentle straight run split into 10 pieces then a sharp drop-off: merging must remove the straight pieces first
    pts = [(100.0 * i, 0.5 * i) for i in range(11)] + [(1100.0, -200.0), (1200.0, -210.0)]
    segs = profile_segments(pts, 3)
    assert len(segs) == 3
    assert segs[0]['x1'] == 1000.0 and segs[1]['x1'] == 1100.0  # straight run merged into one segment
    assert segs[0]['length_m'] == pytest.approx(total_length(pts[:11]))
    assert segs[0]['dz_m'] == pytest.approx(5.0)


def test_signs_and_inclination():
    segs = profile_segments([(0, 0), (1000, 100), (2000, 0)])
    assert segs[0]['dz_m'] == pytest.approx(100) and segs[1]['dz_m'] == pytest.approx(-100)
    assert segs[0]['inc_deg'] == pytest.approx(math.degrees(math.atan2(100, 1000)))
    assert segs[1]['inc_deg'] == pytest.approx(-segs[0]['inc_deg'])
    assert profile_segments([(0, 0), (0, 100)])[0]['inc_deg'] == pytest.approx(90)
    with pytest.raises(ValueError):
        profile_segments([(0, 0)])


# ---------------------------------------------------------------- summaries
def test_summarize_sag_and_hog():
    pts = [(0, 0), (1000, -50), (2000, -20), (3000, -80), (4000, -10)]
    s = summarize(pts)
    assert s['true_length_m'] == pytest.approx(total_length(pts))
    assert s['horizontal_length_m'] == 4000 and s['net_elevation_change_m'] == -10
    assert s['min_elevation_m'] == -80 and s['max_elevation_m'] == 0
    assert [lp['x_m'] for lp in s['low_points']] == [1000, 3000]
    assert s['low_points'][0]['depth_m'] == pytest.approx(30)   # start 0 / high -20 -> lower is -20
    assert s['low_points'][1]['depth_m'] == pytest.approx(60)   # high -20 / end -10 -> -20 - (-80)
    assert [hp['x_m'] for hp in s['high_points']] == [2000]
    assert s['high_points'][0]['height_m'] == pytest.approx(30)
    assert s['max_upslope_deg'] == pytest.approx(math.degrees(math.atan2(70, 1000)))
    assert s['max_downslope_deg'] == pytest.approx(math.degrees(math.atan2(60, 1000)))


def test_summarize_monotonic_has_no_turning_points():
    s = summarize([(0, 0), (100, 10), (200, 30)])
    assert s['low_points'] == [] and s['high_points'] == [] and s['max_downslope_deg'] == 0.0


# ---------------------------------------------------------------- risers
def test_vertical_riser_length_equals_water_depth():
    pts = riser_profile(350.0, 'vertical')
    assert pts[0] == (0.0, -350.0) and pts[-1] == (0.0, 0.0)
    assert total_length(pts) == pytest.approx(350.0)
    assert len(riser_profile(350, 'vertical', n=10)) == 10
    top = riser_profile(300, top_elevation_m=25)
    assert top[-1][1] == 25 and total_length(top) == pytest.approx(325)


def test_catenary_matches_analytic_case():
    a, H = 500.0, 300.0
    X = a * math.acosh(1 + H / a)
    pts = riser_profile(H, 'catenary', horizontal_offset_m=X, n=800)
    S = math.sqrt(H * H + 2 * a * H)
    assert total_length(pts) == pytest.approx(S, rel=1e-5)
    assert pts[0] == (0.0, -H) and pts[-1][0] == pytest.approx(X) and pts[-1][1] == pytest.approx(0.0, abs=1e-9)
    # on-curve check at the midpoint of the arc
    s = S / 2
    x_mid, z_mid = a * math.asinh(s / a), -H + a * (math.cosh(math.asinh(s / a)) - 1)
    k = min(range(len(pts)), key=lambda i: abs(pts[i][0] - x_mid))
    assert pts[k][1] == pytest.approx(-H + a * (math.cosh(pts[k][0] / a) - 1), abs=1e-6)
    assert z_mid > -H


@pytest.mark.parametrize('wd,off', [(100, 20), (300, 100), (1500, 1500), (1500, 4000), (50, 800)])
def test_catenary_length_at_least_depth_and_monotone(wd, off):
    pts = riser_profile(wd, 'catenary', horizontal_offset_m=off, n=60)
    L = total_length(pts)
    assert L >= wd - 1e-9 and L >= off - 1e-9 and L >= math.hypot(wd, off) - 1e-6 * L
    zs = [p[1] for p in pts]
    assert all(b >= a - 1e-12 for a, b in zip(zs[:-1], zs[1:]))
    assert pts[-1][0] == pytest.approx(off) and pts[-1][1] == pytest.approx(0.0, abs=1e-9)
    assert pts[1][1] - pts[0][1] < pts[-1][1] - pts[-2][1] + 1e-9  # horizontal at seabed, steeper at top


def test_catenary_default_offset_and_zero_offset():
    pts = riser_profile(400, 'catenary')
    assert pts[-1][0] == pytest.approx(400)
    assert total_length(riser_profile(400, 'catenary', horizontal_offset_m=0)) == pytest.approx(400)


@pytest.mark.parametrize('shape', ['lazy_wave', 'steep_wave'])
def test_wave_risers_have_one_hog_and_one_sag(shape):
    pts = riser_profile(1000.0, shape, sag_depth_m=600, buoyancy_length_m=400, hog_height_m=120)
    assert pts[0] == (0.0, -1000.0) and pts[-1][1] == pytest.approx(0.0, abs=1e-9)
    s = summarize(pts)
    assert len(s['high_points']) == 1 and len(s['low_points']) == 1
    assert s['low_points'][0]['z_m'] == pytest.approx(-600, abs=1e-9)
    assert s['high_points'][0]['z_m'] == pytest.approx(-480, abs=1e-9)
    xs = [p[0] for p in pts]
    assert all(b >= a for a, b in zip(xs[:-1], xs[1:]))
    # arc length of the buoyancy section (hog -> sag) ~ requested
    hog_i = [i for i, p in enumerate(pts) if p == (s['high_points'][0]['x_m'], s['high_points'][0]['z_m'])][0]
    sag_i = [i for i, p in enumerate(pts) if p == (s['low_points'][0]['x_m'], s['low_points'][0]['z_m'])][0]
    assert total_length(pts[hog_i:sag_i + 1]) == pytest.approx(400, rel=0.01)
    assert total_length(pts) > 1000


def test_steep_wave_lower_leg_is_steeper_than_lazy():
    lazy = riser_profile(800, 'lazy_wave')
    steep = riser_profile(800, 'steep_wave')
    assert summarize(steep)['max_upslope_deg'] > summarize(lazy)['max_upslope_deg']


def test_lazy_wave_offset_respected_and_errors():
    pts = riser_profile(800, 'lazy_wave', horizontal_offset_m=1500)
    assert pts[-1][0] == pytest.approx(1500)
    for kw, msg in [(dict(horizontal_offset_m=10), 'too small'), (dict(sag_depth_m=900), 'sag_depth_m'),
                    (dict(sag_depth_m=500, hog_height_m=600), 'hog_height_m'),
                    (dict(buoyancy_length_m=10, hog_height_m=100), 'buoyancy_length_m')]:
        with pytest.raises(ValueError) as e:
            riser_profile(800, 'lazy_wave', **kw)
        assert msg in str(e.value)
    with pytest.raises(ValueError):
        riser_profile(800, 'spiral')
    with pytest.raises(ValueError):
        riser_profile(0, 'vertical')


def test_profile_points_riser_mode():
    e = {'length_m': 3000, 'elevation_change_m': 10, 'params': {'riser': {'enabled': True, 'shape': 'vertical', 'water_depth_m': 250}}}
    pts = profile_points(e)
    assert total_length(pts) == pytest.approx(250) and elevation_change(pts) == pytest.approx(250)
    e['params']['riser']['enabled'] = False
    assert total_length(profile_points(e)) == pytest.approx(3000)
    e['params']['riser'] = {'enabled': 'true', 'shape': 'catenary', 'water_depth_m': 250, 'horizontal_offset_m': 200}
    assert total_length(profile_points(e)) > 250
    e['params']['riser'] = {'enabled': True, 'shape': 'vertical'}
    with pytest.raises(ValueError) as ex:
        profile_points(e)
    assert 'water_depth_m' in str(ex.value)


def test_riser_appended_to_seabed_profile():
    e = {'params': {'profile': [{'x_m': 0, 'z_m': -300}, {'x_m': 2000, 'z_m': -310}],
                    'riser': {'enabled': True, 'shape': 'vertical', 'water_depth_m': 310}}}
    pts = profile_points(e)
    assert pts[1] == (2000.0, -310.0) and pts[-1] == (2000.0, 0.0)
    assert total_length(pts) == pytest.approx(2000.0 + 10.0 ** 0 * math.hypot(2000, 10) - 2000.0 + 310.0)


# ---------------------------------------------------------------- parsing
@pytest.mark.parametrize('text', [
    'x,z\n0,-100\n500,-120\n1000,-90',
    'x;z\n0;-100\n500;-120\n1000;-90',
    '0 -100\n500 -120\n1000 -90\n',
    '# bathymetry\nx_m\tz_m\n0\t-100\n500\t-120\n1000\t-90',
    '0, -100\r\n500, -120\r\n\r\n1000, -90',
    '0;-100,0\n500;-120,0\n1000;-90,0',
    '0,0 -100,0\n500,0 -120,0\n1000 -90',
])
def test_parse_variants(text):
    assert parse_profile_text(text) == [{'x_m': 0.0, 'z_m': -100.0}, {'x_m': 500.0, 'z_m': -120.0},
                                         {'x_m': 1000.0, 'z_m': -90.0}]


def test_parse_decimal_comma_and_roundtrip():
    rows = parse_profile_text('0,5 -10,25\n100,75 -12,5')
    assert rows == [{'x_m': 0.5, 'z_m': -10.25}, {'x_m': 100.75, 'z_m': -12.5}]
    pts = [(r['x_m'], r['z_m']) for r in rows]
    assert profile_to_rows(pts) == rows


@pytest.mark.parametrize('text,msg', [
    ('', 'No numeric'), ('x,z\n', 'No numeric'), ('0,1\nabc,def', 'Line 2'),
    ('0;1;2\n', 'expected 2 columns'), ('0 1 2', 'expected 2 columns'),
])
def test_parse_errors(text, msg):
    with pytest.raises(ValueError) as e:
        parse_profile_text(text)
    assert msg in str(e.value)


# ---------------------------------------------------------------- slug indicators
def test_slug_indicators_downslope_into_riser_flags_possible():
    pts = [(0, 0), (3000, -60), (3000 + 1e-3, -60.0)] + [(3000.0 + 1e-3, -60.0 + 5 * i) for i in range(1, 40)]
    pts = [(0, -100), (4000, -140)] + [(4000.0 + 0.0, -140 + 10 * i) for i in range(1, 25)]
    ind = slug_indicators(pts)
    assert ind['riser_slugging_flag'] == 'possible'
    assert ind['riser_height_m'] == pytest.approx(240)
    assert ind['downslope_feed_deg'] == pytest.approx(math.degrees(math.atan2(40, 4000)))
    assert ind['downslope_feed_length_m'] == pytest.approx(math.hypot(4000, 40))
    assert 'screening' in ind['disclaimer'].lower()


def test_slug_indicators_catenary_riser_is_low_and_flat_line_none():
    ind = slug_indicators(riser_profile(300, 'catenary', horizontal_offset_m=200))
    assert ind['riser_slugging_flag'] == 'low' and not ind['terrain_slugging_flag']
    assert slug_indicators([(0, 0), (1000, 5)])['riser_slugging_flag'] == 'none'


def test_slug_indicators_terrain_traps():
    pts = [(0, 0), (1000, -40), (2000, -5), (3000, -30), (4000, -2)]
    ind = slug_indicators(pts)
    assert ind['terrain_slugging_flag'] and ind['n_traps'] == 1  # the final low point is the riser base
    assert ind['max_trap_depth_m'] == pytest.approx(35)
    assert not slug_indicators(pts, min_trap_depth_m=100)['terrain_slugging_flag']
