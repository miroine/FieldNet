"""Well trajectory (deviation survey) and completion geometry. Pure functions, no heavy deps.

Data model (stored in a well node's ``params``)
    trajectory : list of dict rows with ``md_m`` plus ``tvd_m`` or ``inc_deg`` (azimuth ignored,
                 the well is treated as planar / 2-D).  Wellhead is at MD = TVD = 0.
    completion : list of dict rows ``{'from_md_m','to_md_m','id_m','roughness_m'(opt),'label'(opt)}``.
    fallback   : with neither present the well is vertical from ``depth_m`` (TVD, default 2000 m),
                 ``tubing_id_m`` (default 0.0762 m) and ``tubing_roughness_m`` (default 4.5e-5 m).

Conventions: lengths in m, angles in degrees, inclination measured from vertical
(0 = vertical, 90 = horizontal, >90 = upward-going).  Segments are listed from the WELLHEAD to the
BOTTOM; ``dz_m`` is the TVD gained going UP the well along the production flow direction
(= TVD difference of the segment, normally >= 0; negative only if the well drops/goes up-hole
inclination > 90).  Geometry only - nothing here is a drilling design tool.

Completion rules: gap -> previous section's ID continues (gap before the first row -> first row's
ID); overlap -> the LATER row in the list wins; beyond the last row -> last section's ID.
"""
import math
from bisect import bisect_right

DEFAULT_DEPTH_M = 2000.0
DEFAULT_TUBING_ID_M = 0.0762
DEFAULT_ROUGHNESS_M = 4.5e-5
MIN_SEGMENT_M = 25.0
TVD_TOL_M = 0.5


# ----------------------------------------------------------------------------- helpers
def _num(v):
    """float(v) or None for None/blank/NaN/inf/unparsable (comma decimals tolerated)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        s = v.strip().replace(',', '.')
        if not s:
            return None
        try:
            v = float(s)
        except ValueError:
            return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _get(p, *names, default=None):
    for n in names:
        if n in p and p[n] is not None:
            return p[n]
    return default


def _req(p, *names):
    v = _num(_get(p, *names))
    if v is None:
        raise ValueError(f"build_survey: missing or invalid parameter '{names[0]}'")
    return v


def _arc_dtvd(dmd, inc1_deg, inc2_deg):
    """TVD gain of a planar circular arc (minimum curvature, 2-D) of measured length dmd [m]
    going from inclination inc1 to inc2 [deg]:  dmd*(sin i2 - sin i1)/(i2 - i1)."""
    a, b = math.radians(inc1_deg), math.radians(inc2_deg)
    d = b - a
    if abs(d) < 1e-12:
        return dmd * math.cos(a)
    return dmd * (math.sin(b) - math.sin(a)) / d


def _row(md, tvd, inc):
    return {'md_m': float(md), 'tvd_m': float(tvd), 'inc_deg': float(inc)}


# ----------------------------------------------------------------------------- survey generators
def _emit(rows, length, inc1, step):
    """Append a section of measured length ``length`` going from the last row's inclination to inc1."""
    if length <= 1e-12:
        return
    md0, tvd0, inc0 = rows[-1]['md_m'], rows[-1]['tvd_m'], rows[-1]['inc_deg']
    n = 1 if abs(inc1 - inc0) < 1e-12 else max(1, int(math.ceil(length / step - 1e-9)))
    dl = length / n
    prev, tvd = inc0, tvd0
    for k in range(1, n + 1):
        inc = inc0 + (inc1 - inc0) * k / n
        tvd += _arc_dtvd(dl, prev, inc)
        rows.append(_row(md0 + length * k / n, tvd, inc))
        prev = inc


def build_survey(kind, **p):
    """Generate a planar survey: list of rows ``{'md_m','tvd_m','inc_deg'}`` from MD 0.

    kinds (parameters, [unit]; ``_m`` suffixes are accepted as aliases):
      'vertical'   depth [m]
      'J'          kickoff_md [m], build_rate_deg_per_30m, tangent_inc_deg (0..90], target_tvd [m]
                   vertical -> circular build to tangent_inc -> straight tangent to target_tvd
      'S'          as 'J' plus drop_rate_deg_per_30m (default = build rate), final_inc_deg (default 0),
                   final_hold_m (default 0): the tangent length is solved so the well ends at target_tvd
      'horizontal' kickoff_md [m], build_rate_deg_per_30m, lateral_length [m]
                   (build to 90 deg then a horizontal lateral; optional lateral_inc_deg, default 90)
    ``step_m`` (default 10) is the maximum station spacing inside curved sections.
    Raises ValueError for missing/inconsistent parameters (e.g. target TVD shallower than the end of build).
    """
    kind = str(kind).strip().lower()
    step = max(_num(p.get('step_m')) or 10.0, 0.5)
    rows = [_row(0.0, 0.0, 0.0)]
    if kind == 'vertical':
        depth = _req(p, 'depth', 'depth_m')
        if depth <= 0:
            raise ValueError('build_survey: depth must be > 0')
        _emit(rows, depth, 0.0, step)
        return rows
    if kind not in ('j', 's', 'horizontal'):
        raise ValueError(f"build_survey: unknown kind '{kind}' (use vertical, J, S or horizontal)")
    kop = _req(p, 'kickoff_md', 'kickoff_md_m')
    bur = _req(p, 'build_rate_deg_per_30m', 'build_rate')
    if kop < 0:
        raise ValueError('build_survey: kickoff_md must be >= 0')
    if bur <= 0:
        raise ValueError('build_survey: build_rate_deg_per_30m must be > 0')
    _emit(rows, kop, 0.0, step)
    if kind == 'horizontal':
        lat = _req(p, 'lateral_length', 'lateral_length_m')
        if lat < 0:
            raise ValueError('build_survey: lateral_length must be >= 0')
        inc_l = _num(p.get('lateral_inc_deg'))
        inc_l = 90.0 if inc_l is None else inc_l
        if not 0 < inc_l <= 180:
            raise ValueError('build_survey: lateral_inc_deg must be in (0, 180]')
        _emit(rows, inc_l / bur * 30.0, inc_l, step)
        _emit(rows, lat, inc_l, step)
        return rows
    tinc = _req(p, 'tangent_inc_deg')
    target = _req(p, 'target_tvd', 'target_tvd_m')
    if not 0 < tinc <= 90:
        raise ValueError('build_survey: tangent_inc_deg must be in (0, 90]')
    _emit(rows, tinc / bur * 30.0, tinc, step)
    tvd_build = rows[-1]['tvd_m']
    if kind == 'j':
        remaining = target - tvd_build
        tail = 0.0
        tail_rows = []
    else:
        dr = _num(p.get('drop_rate_deg_per_30m'))
        dr = bur if dr is None else dr
        finc = _num(p.get('final_inc_deg'))
        finc = 0.0 if finc is None else finc
        hold = _num(p.get('final_hold_m')) or 0.0
        if dr <= 0 or not 0 <= finc < tinc or hold < 0:
            raise ValueError('build_survey: S-well needs drop_rate > 0, 0 <= final_inc_deg < tangent_inc_deg, final_hold_m >= 0')
        drop_len = (tinc - finc) / dr * 30.0
        tail = _arc_dtvd(drop_len, tinc, finc) + hold * math.cos(math.radians(finc))
        remaining = target - tvd_build - tail
    if remaining < -1e-6:
        raise ValueError(f'build_survey: target_tvd {target:.1f} m is shallower than the end of the '
                         f'{"build" if kind == "j" else "build+drop"} section ({target - remaining:.1f} m TVD); '
                         'increase target_tvd, lower tangent_inc_deg or raise the build rate')
    c = math.cos(math.radians(tinc))
    if remaining > 1e-6 and c < 1e-9:
        raise ValueError('build_survey: a 90 deg tangent never gains TVD; reduce tangent_inc_deg below 90')
    _emit(rows, max(remaining, 0.0) / c if c > 1e-9 else 0.0, tinc, step)
    if kind == 's':
        _emit(rows, drop_len, finc, step)
        _emit(rows, hold, finc, step)
    return rows


# ----------------------------------------------------------------------------- survey from user rows
def _clean_traj_rows(rows):
    out = []
    for i, r in enumerate(rows or [], 1):
        if not isinstance(r, dict):
            continue
        md, tvd, inc = _num(r.get('md_m')), _num(r.get('tvd_m')), _num(r.get('inc_deg'))
        if md is None or (tvd is None and inc is None):
            continue
        out.append((i, md, tvd, inc))
    return out


def survey_from_rows(rows, tvd_tol_m=TVD_TOL_M):
    """Validate user survey rows and return ``[{'md_m','tvd_m','inc_deg'}, ...]``.

    Rows need ``md_m`` and ``tvd_m`` and/or ``inc_deg`` (extra keys ignored; rows with blank/NaN md or
    with neither tvd nor inc are skipped).  If every row has TVD, TVD is used (inclination taken
    from the row or from the chord of the following interval); otherwise every row must have
    inclination and TVD is computed by 2-D minimum curvature.  A station is added at MD 0 / TVD 0
    when the first row is deeper (inc rows: it keeps the first row's inclination, i.e. a straight
    hole to the first station).
    Raises ValueError (message cites the 1-based row number) for non-increasing MD, TVD decreasing by
    more than ``tvd_tol_m``, TVD gaining faster than MD, inclination outside 0..180, negative MD, mixed
    row types or fewer than 2 stations.
    """
    cl = _clean_traj_rows(rows)
    if not cl:
        raise ValueError('Trajectory has no valid rows (need md_m plus tvd_m or inc_deg)')
    for k, (i, md, tvd, inc) in enumerate(cl):
        if md < 0:
            raise ValueError(f'Trajectory row {i}: MD {md:g} m is negative')
        if k and md <= cl[k - 1][1]:
            raise ValueError(f'Trajectory row {i}: MD {md:g} m is not greater than the previous MD {cl[k - 1][1]:g} m '
                             '(MD must be strictly increasing)')
        if inc is not None and not 0 <= inc <= 180:
            raise ValueError(f'Trajectory row {i}: inclination {inc:g} deg outside 0..180')
    tvd_mode = all(r[2] is not None for r in cl)
    inc_mode = all(r[3] is not None for r in cl)
    if not tvd_mode and not inc_mode:
        raise ValueError('Trajectory rows mix TVD-only and inclination-only entries; give TVD for every row '
                         'or inclination for every row')
    if tvd_mode:
        st = [[md, tvd, inc] for (_, md, tvd, inc) in cl]
        idx = [r[0] for r in cl]
        if st[0][0] > 1e-9:
            st.insert(0, [0.0, 0.0, None]); idx.insert(0, 0)
        elif abs(st[0][1]) > tvd_tol_m:
            raise ValueError(f'Trajectory row {idx[0]}: the station at MD 0 must have TVD 0 (wellhead), got {st[0][1]:g}')
        st[0][1] = 0.0 if st[0][0] <= 1e-9 else st[0][1]
        chords = []
        for k in range(1, len(st)):
            dmd, dtvd = st[k][0] - st[k - 1][0], st[k][1] - st[k - 1][1]
            if dtvd < -tvd_tol_m:
                raise ValueError(f'Trajectory row {idx[k]}: TVD decreases from {st[k - 1][1]:g} to {st[k][1]:g} m '
                                 f'(MD {st[k - 1][0]:g} -> {st[k][0]:g} m)')
            if dtvd > dmd + tvd_tol_m:
                raise ValueError(f'Trajectory row {idx[k]}: TVD changes by {dtvd:g} m over only {dmd:g} m of MD '
                                 '(TVD cannot increase faster than MD)')
            chords.append(math.degrees(math.acos(min(max(dtvd / dmd, -1.0), 1.0))))
        if len(st) < 2:
            raise ValueError('Trajectory needs at least 2 stations')
        out = []
        for k, (md, tvd, inc) in enumerate(st):
            c = chords[k] if k < len(chords) else chords[-1]
            out.append(_row(md, tvd, c if inc is None else inc))
        return out
    st = [[md, inc] for (_, md, _t, inc) in cl]
    if st[0][0] > 1e-9:
        st.insert(0, [0.0, st[0][1]])
    if len(st) < 2:
        raise ValueError('Trajectory needs at least 2 stations')
    out = [_row(st[0][0], 0.0, st[0][1])]
    for md, inc in st[1:]:
        o = out[-1]
        out.append(_row(md, o['tvd_m'] + _arc_dtvd(md - o['md_m'], o['inc_deg'], inc), inc))
    return out


def _vertical(depth):
    return [_row(0.0, 0.0, 0.0), _row(depth, depth, 0.0)]


def _f(p, key, default):
    v = _num(p.get(key))
    return default if v is None else v


def _has_trajectory(params):
    return bool(_clean_traj_rows((params or {}).get('trajectory')))


def _survey(params):
    p = params or {}
    if _has_trajectory(p):
        return survey_from_rows(p.get('trajectory'))
    return _vertical(max(_f(p, 'depth_m', DEFAULT_DEPTH_M), 1.0))


def _tvd_at(survey, md):
    mds = [r['md_m'] for r in survey]
    if md <= mds[0]:
        return survey[0]['tvd_m']
    if md >= mds[-1]:
        return survey[-1]['tvd_m']
    i = bisect_right(mds, md) - 1
    a, b = survey[i], survey[i + 1]
    return a['tvd_m'] + (b['tvd_m'] - a['tvd_m']) * (md - a['md_m']) / (b['md_m'] - a['md_m'])


def well_total_depth(params):
    """(total measured depth, total vertical depth) [m] of the well."""
    s = _survey(params)
    return s[-1]['md_m'], s[-1]['tvd_m']


# ----------------------------------------------------------------------------- completion
def _completion(params):
    """(valid rows, warnings).  Fully blank rows are ignored silently."""
    p = params or {}
    default_r = max(_f(p, 'tubing_roughness_m', DEFAULT_ROUGHNESS_M), 0.0)
    rows, warns = [], []
    for i, r in enumerate(p.get('completion') or [], 1):
        if not isinstance(r, dict):
            continue
        a, b, d = _num(r.get('from_md_m')), _num(r.get('to_md_m')), _num(r.get('id_m'))
        if a is None and b is None and d is None:
            continue
        if a is None or b is None or d is None:
            warns.append(f'Completion row {i} skipped: from_md_m, to_md_m and id_m are all required')
            continue
        if d > 1.0:
            warns.append(f'Completion row {i}: ID {d:g} m is not plausible for a tubing string (0.0889 m = 3.5 in). If you entered inches or millimetres, change the ID unit above the table.')
        if d <= 0:
            warns.append(f'Completion row {i} skipped: id_m must be > 0 (got {d:g})')
            continue
        if b <= a:
            warns.append(f'Completion row {i} skipped: to_md_m ({b:g}) must be greater than from_md_m ({a:g})')
            continue
        rough = _num(r.get('roughness_m'))
        label = r.get('label')
        label = '' if label is None or (isinstance(label, float) and math.isnan(label)) else str(label)
        rows.append({'row': i, 'from': a, 'to': b, 'id': d, 'rough': default_r if rough is None else max(rough, 0.0),
                     'label': label or f'ID {d * 1000:.1f} mm'})
    return rows, warns


def validate_completion(params):
    """Human-readable problems with the well geometry inputs ([] when clean).

    Reports skipped/invalid completion rows, gaps (previous ID continues), overlaps (later row wins),
    a first row that starts below the wellhead, rows ending short of / extending beyond TD, and an invalid
    trajectory.  Never raises."""
    p = params or {}
    rows, warns = _completion(p)
    try:
        md_tot, _ = well_total_depth(p)
    except ValueError as e:
        return warns + [f'Trajectory invalid: {e}']
    if not rows:
        return warns
    srt = sorted(rows, key=lambda r: (r['from'], r['row']))
    if srt[0]['from'] > 1e-6:
        warns.append(f"Completion starts at MD {srt[0]['from']:g} m: wellhead to that depth uses the first row's ID")
    cover = srt[0]['to']
    for r in srt[1:]:
        if r['from'] > cover + 1e-6:
            warns.append(f"Completion gap MD {cover:g}-{r['from']:g} m: previous ID continues through the gap")
        elif r['from'] < cover - 1e-6:
            warns.append(f"Completion rows overlap MD {r['from']:g}-{min(cover, r['to']):g} m: the later row in the list wins")
        cover = max(cover, r['to'])
    if cover < md_tot - 1e-6:
        warns.append(f'Completion ends at MD {cover:g} m, above TD {md_tot:g} m: last section ID continues to TD')
    if max(r['to'] for r in rows) > md_tot + 1e-6:
        warns.append(f'Completion extends below TD (MD {md_tot:g} m): the excess is ignored')
    if any(r['from'] >= md_tot - 1e-6 for r in rows):
        warns.append(f'Some completion rows start at or below TD (MD {md_tot:g} m) and have no effect')
    return warns


def _coverage(rows, params, md_tot):
    """Elementary completion intervals [{'a','b','id','rough','label'}] covering 0..md_tot."""
    p = params or {}
    default = {'id': max(_f(p, 'tubing_id_m', DEFAULT_TUBING_ID_M), 1e-3),
               'rough': max(_f(p, 'tubing_roughness_m', DEFAULT_ROUGHNESS_M), 0.0), 'label': 'Tubing'}
    if not rows:
        return [dict(a=0.0, b=md_tot, **default)]
    cuts = {0.0, md_tot}
    for r in rows:
        for v in (r['from'], r['to']):
            if 1e-6 < v < md_tot - 1e-6:
                cuts.add(v)
    cuts = sorted(cuts)
    first = min(rows, key=lambda r: (r['from'], r['row']))
    out, prev = [], None
    for a, b in zip(cuts[:-1], cuts[1:]):
        if b - a < 1e-6:
            continue
        m = 0.5 * (a + b)
        hit = None
        for r in rows:  # later row wins
            if r['from'] <= m < r['to']:
                hit = r
        props = ({'id': hit['id'], 'rough': hit['rough'], 'label': hit['label']} if hit is not None
                 else (dict(prev) if prev is not None else {'id': first['id'], 'rough': first['rough'], 'label': first['label']}))
        prev = props
        if out and all(out[-1][k] == props[k] for k in ('id', 'rough', 'label')):
            out[-1]['b'] = b
        else:
            out.append(dict(a=a, b=b, **props))
    return out


def _merge_pair(x, y):
    lx, ly = x['b'] - x['a'], y['b'] - y['a']
    d_eq = ((lx + ly) / (lx / x['id'] ** 5 + ly / y['id'] ** 5)) ** 0.2  # equal-friction ID (dp ~ L/D^5)
    big = x if lx >= ly else y
    return {'a': x['a'], 'b': y['b'], 'id': d_eq, 'rough': (x['rough'] * lx + y['rough'] * ly) / (lx + ly),
            'label': big['label']}


def tubing_segments(params, max_segments=24):
    """Marching segments from WELLHEAD to BOTTOM for the VLP calculation.

    Each: ``{'md_top','md_bot','length_m' (measured),'dz_m' (TVD gain going up the well),'id_m',
    'roughness_m','inc_deg' (chord inclination from vertical),'label'}``.

    No trajectory and no completion -> exactly ``max_segments`` equal segments of ``depth_m/n`` with
    dz = length (today's vertical behaviour; pass the ``vlp_segments`` setting as max_segments).
    Otherwise every completion change is a segment boundary; each section is split into equal pieces no
    longer than max(total_md/max_segments, 25 m) (the limit is relaxed in small steps only if needed to
    respect max_segments); if there are more completion sections than max_segments, the smallest
    neighbours are merged using an equal-friction ID.  sum(length) = total MD, sum(dz) = final TVD.
    Raises ValueError if the trajectory rows are invalid (see validate_completion for a soft check).
    """
    p = params or {}
    n_max = max(int(max_segments), 1)
    rows, _ = _completion(p)
    if not rows and not _has_trajectory(p):
        depth = max(_f(p, 'depth_m', DEFAULT_DEPTH_M), 1.0)
        d = max(_f(p, 'tubing_id_m', DEFAULT_TUBING_ID_M), 1e-3)
        r = max(_f(p, 'tubing_roughness_m', DEFAULT_ROUGHNESS_M), 0.0)
        dl = depth / n_max
        return [{'md_top': i * dl, 'md_bot': (i + 1) * dl, 'length_m': dl, 'dz_m': dl, 'id_m': d, 'roughness_m': r,
                 'inc_deg': 0.0, 'label': 'Tubing'} for i in range(n_max)]
    survey = _survey(p)
    md_tot = survey[-1]['md_m']
    secs = _coverage(rows, p, md_tot)
    while len(secs) > n_max:
        k = min(range(len(secs) - 1), key=lambda j: secs[j + 1]['b'] - secs[j]['a'])
        secs[k:k + 2] = [_merge_pair(secs[k], secs[k + 1])]
    lim = max(md_tot / n_max, MIN_SEGMENT_M)

    def pieces(lm):
        return [max(1, int(math.ceil((s['b'] - s['a']) / lm - 1e-9))) for s in secs]
    pc = pieces(lim)
    while sum(pc) > n_max and max(pc) > 1:
        lim *= 1.03
        pc = pieces(lim)
    out = []
    for s, n in zip(secs, pc):
        for k in range(n):
            a = s['a'] + (s['b'] - s['a']) * k / n
            b = s['a'] + (s['b'] - s['a']) * (k + 1) / n if k < n - 1 else s['b']
            dz = _tvd_at(survey, b) - _tvd_at(survey, a)
            ln = b - a
            out.append({'md_top': a, 'md_bot': b, 'length_m': ln, 'dz_m': dz, 'id_m': s['id'], 'roughness_m': s['rough'],
                        'inc_deg': math.degrees(math.acos(min(max(dz / ln, -1.0), 1.0))), 'label': s['label']})
    return out


def trajectory_summary(params):
    """Dict: md_m, tvd_m, max_inc_deg, max_dls_deg_per_30m, true_vertical_fraction (TVD/MD),
    n_completion_sections, min_id_m, is_vertical, has_trajectory."""
    p = params or {}
    survey = _survey(p)
    md, tvd = survey[-1]['md_m'], survey[-1]['tvd_m']
    dls = 0.0
    for a, b in zip(survey[:-1], survey[1:]):
        dls = max(dls, abs(b['inc_deg'] - a['inc_deg']) / (b['md_m'] - a['md_m']) * 30.0)
    rows, _ = _completion(p)
    secs = _coverage(rows, p, md)
    max_inc = max(r['inc_deg'] for r in survey)
    return {'md_m': md, 'tvd_m': tvd, 'max_inc_deg': max_inc, 'max_dls_deg_per_30m': dls,
            'true_vertical_fraction': tvd / md if md > 0 else 1.0,
            'n_completion_sections': len(secs), 'min_id_m': min(s['id'] for s in secs),
            'is_vertical': max_inc < 0.5, 'has_trajectory': _has_trajectory(p)}
