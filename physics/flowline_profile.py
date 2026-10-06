"""Flowline / riser elevation profiles (bathymetry) as pure geometry. No heavy deps.

Data model (a flowline edge's ``params``)
    profile : list of dict rows ``{'x_m': horizontal distance from source [m], 'z_m': elevation [m, +up,
              seabed negative]}``.
    riser   : dict ``{'enabled': bool, 'shape': 'vertical'|'catenary'|'lazy_wave'|'steep_wave',
              'water_depth_m', 'horizontal_offset_m', 'sag_depth_m', 'buoyancy_length_m', 'hog_height_m',
              'seabed_run_m', 'top_elevation_m', 'n'}``.
    fallback: straight line from the edge's ``length_m`` (TRUE length) and ``elevation_change_m``.

Flow direction is source -> target (increasing x).  Units: m and degrees.  Slope angles are measured
from the horizontal, positive = rising along the flow, range -90..90.

IMPORTANT: ``riser_profile`` shapes are geometric SCREENING profiles for hydraulics (elevation vs
distance).  They are NOT riser structural/dynamic designs (no tension, current, buoyancy sizing or
fatigue is considered).  ``slug_indicators`` is likewise a simple geometric flag, not a slugging model.
"""
import math

EPS = 1e-9


# ----------------------------------------------------------------------------- helpers
def _num(v):
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


def _truthy(v):
    if isinstance(v, str):
        return v.strip().lower() in ('1', 'true', 'yes', 'on', 'y')
    return bool(v)


def _sign(d):
    return 1 if d > EPS else (-1 if d < -EPS else 0)


def _clean_rows(rows):
    pts = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        x, z = _num(r.get('x_m')), _num(r.get('z_m'))
        if x is None or z is None:
            continue
        pts.append((x, z))
    pts.sort(key=lambda t: t[0])  # stable: equal-x rows (vertical steps) keep their input order
    out = []
    for q in pts:
        if out and abs(q[0] - out[-1][0]) < EPS and abs(q[1] - out[-1][1]) < EPS:
            continue
        out.append(q)
    return out


# ----------------------------------------------------------------------------- riser geometry
def _catenary_a(X, H):
    """Catenary parameter a [m] with a*(cosh(X/a)-1) = H (vertex tangent horizontal, rises H over run X)."""
    f = lambda a: a * (math.cosh(X / a) - 1.0) - H
    lo = X / 700.0
    hi = max(X, H, 1.0) * 10.0
    while f(hi) > 0:
        hi *= 2.0
    for _ in range(300):
        mid = math.sqrt(lo * hi)
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi)


def _catenary_points(X, H, n):
    """Points (x, z) from the vertex (0,0) to the hang-off (X, H), equally spaced in arc length."""
    a = _catenary_a(X, H)
    S = math.sqrt(H * H + 2.0 * a * H)  # = a*sinh(X/a)
    pts = []
    for i in range(n):
        s = S * i / (n - 1)
        pts.append((a * math.asinh(s / a), a * (math.sqrt(1.0 + (s / a) ** 2) - 1.0)))
    pts[-1] = (X, H)
    return pts


def _ease(x0, z0, x1, z1, n):
    """Cosine-eased S-curve (horizontal tangent at both ends), n points incl. ends."""
    return [(x0 + (x1 - x0) * k / (n - 1), z0 + (z1 - z0) * (1 - math.cos(math.pi * k / (n - 1))) / 2.0) for k in range(n)]


def _ease_len(run, rise, m=200):
    s, px, pz = 0.0, 0.0, 0.0
    for k in range(1, m + 1):
        t = k / m
        x, z = run * t, rise * (1 - math.cos(math.pi * t)) / 2.0
        s += math.hypot(x - px, z - pz)
        px, pz = x, z
    return s


def riser_profile(water_depth_m, shape='vertical', horizontal_offset_m=None, n=40, **p):
    """Geometric screening profile of a riser from the seabed (x=0, z=-water_depth) to the top
    (z = ``top_elevation_m``, default 0 = deck/sea level).  Returns ``[(x_m, z_m), ...]`` (about ``n`` points).

    shape
      'vertical'   straight vertical pipe, length = height.
      'catenary'   free-hanging catenary, horizontal tangent at the seabed, hang-off ``horizontal_offset_m``
                   from the touchdown point (default = height).  Parameter solved numerically;
                   length = sqrt(H^2 + 2 a H) >= H.
      'lazy_wave'  seabed -> rises to a hog bend -> falls through the buoyancy section to a sag bend ->
                   catenary up to the top.  Parameters: ``sag_depth_m`` (depth of the sag bend below the top,
                   default 0.6 H, must be < H), ``buoyancy_length_m`` (arc length between hog and sag, default
                   3x hog height), ``hog_height_m`` (hog above sag, default min(0.25 H, 0.5 sag_depth)),
                   ``seabed_run_m`` (horizontal run from touchdown to hog, default 0.8 x hog elevation above
                   seabed), ``horizontal_offset_m`` total run (default adds 0.5 x sag_depth for the upper
                   catenary).  Local high = hog, local low = sag (liquid accumulation site).
      'steep_wave' as lazy_wave but with a steep lower leg (default seabed_run_m = 0.1 x hog elevation above seabed).
    Raises ValueError for non-positive height or infeasible parameters.
    """
    wd = _num(water_depth_m)
    top = _num(p.get('top_elevation_m')) or 0.0
    if wd is None or wd + top <= 0:
        raise ValueError('riser: water_depth_m + top_elevation_m must be > 0')
    H = wd + top
    shape = str(shape or 'vertical').strip().lower().replace('-', '_').replace(' ', '_')
    n = max(int(n), 2)
    X = _num(horizontal_offset_m)
    base = -wd
    if shape == 'vertical' or (shape == 'catenary' and X is not None and X <= 0):
        return [(0.0, base + H * i / (n - 1)) for i in range(n)]
    if shape == 'catenary':
        X = H if X is None else X
        return [(x, base + z) for x, z in _catenary_points(X, H, n)]
    if shape not in ('lazy_wave', 'steep_wave'):
        raise ValueError(f"riser: unknown shape '{shape}' (vertical, catenary, lazy_wave, steep_wave)")
    sag_d = _num(p.get('sag_depth_m'))
    sag_d = 0.6 * H if sag_d is None else sag_d
    if not 0 < sag_d < H:
        raise ValueError(f'riser: sag_depth_m must be between 0 and the riser height {H:g} m (got {sag_d:g})')
    hb = _num(p.get('hog_height_m'))
    hb = min(0.25 * H, 0.5 * sag_d) if hb is None else hb
    if not 0 < hb < sag_d:
        raise ValueError(f'riser: hog_height_m must be between 0 and sag_depth_m {sag_d:g} m (got {hb:g})')
    lb = _num(p.get('buoyancy_length_m'))
    lb = 3.0 * hb if lb is None else lb
    if lb <= hb * (1 + 1e-6):
        raise ValueError(f'riser: buoyancy_length_m ({lb:g}) must exceed the hog height ({hb:g} m)')
    z_sag = top - sag_d
    z_hog = z_sag + hb
    run_d = _num(p.get('seabed_run_m'))
    if run_d is None:
        run_d = (0.8 if shape == 'lazy_wave' else 0.1) * (z_hog - base)
    if run_d <= 0:
        raise ValueError('riser: seabed_run_m must be > 0')
    lo, hi = 0.0, lb  # solve the horizontal run of the buoyancy section for the requested arc length
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if _ease_len(mid, hb) < lb:
            lo = mid
        else:
            hi = mid
    run_b = 0.5 * (lo + hi)
    x1 = 0.5 * sag_d if X is None else X - run_d - run_b
    if x1 <= 0:
        raise ValueError(f'riser: horizontal_offset_m {X:g} is too small for this {shape} '
                         f'(needs more than {run_d + run_b:g} m before the upper catenary)')
    k = max(n // 4, 8)
    pts = _ease(0.0, base, run_d, z_hog, k)
    pts += _ease(run_d, z_hog, run_d + run_b, z_sag, k)[1:]
    pts += [(run_d + run_b + x, z_sag + z) for x, z in _catenary_points(x1, sag_d, max(n - 2 * k + 2, 8))][1:]
    return pts


# ----------------------------------------------------------------------------- profile from edge
def profile_points(edge):
    """``[(x_m, z_m), ...]`` for a flowline edge (see module doc for the data model).

    Priority: ``params['profile']`` rows (blank/NaN rows skipped, sorted by x, exact duplicate points dropped,
    >= 2 points required); if ``params['riser']['enabled']`` the riser profile is generated (appended to the
    seabed profile if one is also given, shifted to start at its end; otherwise alone, x from 0);
    fallback straight line from ``length_m`` (true length, default 1000) and ``elevation_change_m``:
    x = sqrt(max(L^2 - dz^2, 0)).  Raises ValueError for unusable input."""
    e = edge or {}
    prm = e.get('params') or {}
    rows = prm.get('profile')
    riser = prm.get('riser')
    riser_on = isinstance(riser, dict) and _truthy(riser.get('enabled', False))
    pts = _clean_rows(rows) if rows else []
    if rows and len(pts) < 2 and not riser_on and any(isinstance(r, dict) and (_num(r.get('x_m')) is not None or
                                                                              _num(r.get('z_m')) is not None) for r in rows):
        raise ValueError('Flowline profile needs at least 2 valid points (x_m, z_m); found %d' % len(pts))
    if len(pts) < 2:
        pts = []
    if riser_on:
        wd = _num(riser.get('water_depth_m'))
        if wd is None or wd <= 0:
            raise ValueError('Riser is enabled but water_depth_m is missing or not positive')
        kw = {k: riser[k] for k in ('sag_depth_m', 'buoyancy_length_m', 'hog_height_m', 'seabed_run_m', 'top_elevation_m')
              if k in riser and riser[k] is not None}
        rp = riser_profile(wd, riser.get('shape') or 'vertical', riser.get('horizontal_offset_m'),
                           int(_num(riser.get('n')) or 40), **kw)
        if not pts:
            return rp
        dx, dz = pts[-1][0] - rp[0][0], pts[-1][1] - rp[0][1]
        return pts + [(x + dx, z + dz) for x, z in rp[1:]]
    if pts:
        return pts
    L = _num(e.get('length_m'))
    L = _num(prm.get('length_m')) if L is None else L
    L = 1000.0 if L is None else L
    dz = _num(e.get('elevation_change_m'))
    dz = _num(prm.get('elevation_change_m')) if dz is None else dz
    dz = 0.0 if dz is None else dz
    if L <= 0:
        raise ValueError('Flowline length_m must be > 0')
    return [(0.0, 0.0), (math.sqrt(max(L * L - dz * dz, 0.0)), dz)]


def profile_to_rows(points):
    """[(x, z)] -> [{'x_m','z_m'}] (for a data editor)."""
    return [{'x_m': float(x), 'z_m': float(z)} for x, z in points]


def total_length(points):
    """True (along-the-line) length [m]."""
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points[:-1], points[1:]))


def elevation_change(points):
    """Net elevation change source -> target [m] (z_end - z_start)."""
    return points[-1][1] - points[0][1]


# ----------------------------------------------------------------------------- segments
def _seg(pts, i, j):
    ln = sum(math.hypot(pts[k + 1][0] - pts[k][0], pts[k + 1][1] - pts[k][1]) for k in range(i, j))
    dz = pts[j][1] - pts[i][1]
    return {'x0': pts[i][0], 'x1': pts[j][0], 'z0': pts[i][1], 'z1': pts[j][1], 'length_m': ln, 'dz_m': dz,
            'inc_deg': math.degrees(math.asin(min(max(dz / ln, -1.0), 1.0))) if ln > 0 else 0.0}


def _extrema(points):
    """[(vertex_index, 'high'|'low')] for interior turning points (plateaus: first vertex of the plateau)."""
    out, prev, prev_end = [], 0, 0
    for i in range(len(points) - 1):
        d = _sign(points[i + 1][1] - points[i][1])
        if d == 0:
            continue
        if prev != 0 and d != prev:
            out.append((prev_end, 'high' if prev > 0 else 'low'))
        prev, prev_end = d, i + 1
    return out


def profile_segments(points, max_segments=24):
    """Marching segments source -> target: ``{'x0','x1','z0','z1','length_m' (true),'dz_m','inc_deg'}``.

    Every vertex is a boundary when there are <= max_segments segments.  Otherwise neighbouring segments of
    the most similar slope are merged, never across a local high/low point (so the count can stay above
    max_segments only if the profile has more turning points than that).  A merged segment's length is the
    sum of the true lengths it replaces (so sum(length) = total true length) and dz the sum of dz
    (sum(dz) = z_end - z_start exactly); inc_deg = asin(dz/length)."""
    pts = [(float(x), float(z)) for x, z in points]
    if len(pts) < 2:
        raise ValueError('profile_segments needs at least 2 points')
    keep = [i for i in range(len(pts))]  # boundary vertex indices
    protected = {i for i, _ in _extrema(pts)}
    cap = max(int(max_segments), 1)
    while len(keep) - 1 > cap:
        best, best_cost = None, None
        for k in range(1, len(keep) - 1):
            if keep[k] in protected:
                continue
            a, b, c = keep[k - 1], keep[k], keep[k + 1]
            s1, s2 = _seg(pts, a, b), _seg(pts, b, c)
            cost = (abs(s1['inc_deg'] - s2['inc_deg']), s1['length_m'] + s2['length_m'])
            if best_cost is None or cost < best_cost:
                best, best_cost = k, cost
        if best is None:
            break
        del keep[best]
    return [_seg(pts, a, b) for a, b in zip(keep[:-1], keep[1:])]


# ----------------------------------------------------------------------------- summaries
def summarize(points):
    """Dict: n_points, true_length_m, horizontal_length_m, net_elevation_change_m, min_elevation_m,
    max_elevation_m, max_upslope_deg, max_downslope_deg (positive magnitudes, 0 if none), and lists
    ``low_points`` ``[{'x_m','z_m','depth_m'}]`` (depth below the lower of the neighbouring highs/ends: liquid
    holdup sites) and ``high_points`` ``[{'x_m','z_m','height_m'}]`` (height above the higher adjacent low/end)."""
    pts = list(points)
    z = [q[1] for q in pts]
    segs = profile_segments(pts, max_segments=len(pts))
    ups = [s['inc_deg'] for s in segs if s['inc_deg'] > 0]
    downs = [-s['inc_deg'] for s in segs if s['inc_deg'] < 0]
    ex = _extrema(pts)
    idx = [0] + [i for i, _ in ex] + [len(pts) - 1]
    kinds = ['end'] + [k for _, k in ex] + ['end']
    lows, highs = [], []
    for m in range(1, len(idx) - 1):
        i = idx[m]
        nb = (z[idx[m - 1]], z[idx[m + 1]])
        if kinds[m] == 'low':
            lows.append({'x_m': pts[i][0], 'z_m': pts[i][1], 'depth_m': min(nb) - z[i]})
        else:
            highs.append({'x_m': pts[i][0], 'z_m': pts[i][1], 'height_m': z[i] - max(nb)})
    return {'n_points': len(pts), 'true_length_m': total_length(pts), 'horizontal_length_m': pts[-1][0] - pts[0][0],
            'net_elevation_change_m': elevation_change(pts), 'min_elevation_m': min(z), 'max_elevation_m': max(z),
            'max_upslope_deg': max(ups) if ups else 0.0, 'max_downslope_deg': max(downs) if downs else 0.0,
            'low_points': lows, 'high_points': highs}


def slug_indicators(points, min_trap_depth_m=5.0, min_riser_height_m=20.0, min_downslope_deg=0.1):
    """SCREENING geometric indicators for terrain / riser-base slugging (not a slugging model; use
    transient simulation to confirm).

    Returns dict: ``terrain_slugging_flag`` (bool: a low point deeper than ``min_trap_depth_m`` exists upstream
    of the riser base), ``n_traps``, ``max_trap_depth_m``, ``riser_height_m`` (rise from the last low point -
    or the global minimum - to the outlet), ``downslope_feed_length_m`` / ``downslope_feed_deg`` (continuous
    downhill run feeding the riser base), ``riser_slugging_flag`` ('none' | 'low' | 'possible'), ``notes``.
    'possible' = riser taller than ``min_riser_height_m`` fed by a downhill run steeper than
    ``min_downslope_deg``; 'low' = tall riser but the base is not fed from above."""
    pts = list(points)
    if len(pts) < 2:
        raise ValueError('slug_indicators needs at least 2 points')
    s = summarize(pts)
    ex = _extrema(pts)
    lows = [i for i, k in ex if k == 'low']
    z = [q[1] for q in pts]
    base = lows[-1] if lows else z.index(min(z))
    riser_h = z[-1] - z[base]
    prev_high = max([i for i, k in ex if k == 'high' and i < base], default=0)
    feed_len = sum(math.hypot(pts[k + 1][0] - pts[k][0], pts[k + 1][1] - pts[k][1]) for k in range(prev_high, base))
    feed_deg = math.degrees(math.asin(min(max((z[prev_high] - z[base]) / feed_len, -1.0), 1.0))) if feed_len > 0 else 0.0
    traps = [lp for lp in s['low_points'] if lp['depth_m'] >= min_trap_depth_m and abs(lp['x_m'] - pts[base][0]) > EPS]
    notes = []
    if riser_h < min_riser_height_m:
        flag = 'none'
    elif feed_len > 0 and feed_deg >= min_downslope_deg:
        flag = 'possible'
        notes.append(f'{feed_len:.0f} m downhill run ({feed_deg:.2f} deg avg) feeds a {riser_h:.0f} m riser: '
                     'liquid can accumulate at the base (severe slugging screen)')
    else:
        flag = 'low'
        notes.append(f'{riser_h:.0f} m riser without a downhill feed section: severe slugging less likely at normal rates')
    if traps:
        notes.append(f'{len(traps)} intermediate low point(s) deeper than {min_trap_depth_m:g} m: terrain slugging / liquid holdup sites')
    return {'terrain_slugging_flag': bool(traps), 'n_traps': len(traps),
            'max_trap_depth_m': max([t['depth_m'] for t in traps], default=0.0), 'riser_height_m': riser_h,
            'downslope_feed_length_m': feed_len, 'downslope_feed_deg': feed_deg, 'riser_slugging_flag': flag,
            'notes': notes, 'disclaimer': 'Geometric screening only - not a slugging simulation'}


# ----------------------------------------------------------------------------- text parsing
def parse_profile_text(text):
    """Parse pasted 2-column text (x, z) -> ``[{'x_m','z_m'}]``.

    Delimiters: semicolon, tab, whitespace or comma (a comma separates columns only when the line has exactly
    two comma-separated fields; otherwise commas are read as decimal marks, e.g. ``'100,5 -20,25'``).
    Blank lines and ``#`` comments are ignored; the first non-numeric line is treated as a header.
    Raises ValueError naming the offending line."""
    out, header_ok = [], True
    for ln, raw in enumerate(str(text or '').splitlines(), 1):
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        if ';' in line:
            cands = [[c.strip() for c in line.split(';')]]
        elif '\t' in line:
            cands = [[c.strip() for c in line.split('\t')]]
        else:
            cands = ([line.split()] if len(line.split()) >= 2 else []) + [[c.strip() for c in line.split(',')]]
        cells = cands[0]
        for c in cands:
            if all(_num(t) is not None for t in c):
                cells = c
                break
        vals = [_num(c) for c in cells]
        if any(v is None for v in vals):
            if header_ok and not out:
                header_ok = False
                continue
            raise ValueError(f"Line {ln}: cannot read numbers from '{raw.strip()}'")
        header_ok = False
        if len(vals) != 2:
            raise ValueError(f"Line {ln}: expected 2 columns (x, z) but found {len(vals)} in '{raw.strip()}'")
        out.append({'x_m': vals[0], 'z_m': vals[1]})
    if not out:
        raise ValueError('No numeric (x, z) rows found in the pasted text')
    return out
