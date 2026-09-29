"""FieldNet v15.1 multi-speed pump/compressor maps with explicit envelope diagnostics.

Numeric map values remain backward compatible with v15: flow interpolation is delegated to
performance_maps and speed outside the supplied speed range is clamped to the nearest speed line.
v15.1 makes that clamping/extrapolation status explicit so it cannot be mistaken for an in-map point.
"""
import numpy as np
from physics.performance_maps import pump_map, compressor_map


def _speed_lines(lines):
    if len(lines) < 2:
        raise ValueError('At least two speed lines are required')
    out = sorted(lines, key=lambda x: float(x['speed_pct']))
    speeds = [float(x['speed_pct']) for x in out]
    if len(set(speeds)) != len(speeds):
        raise ValueError('speed_pct must be unique')
    for line in out:
        if not line.get('points') or len(line['points']) < 2:
            raise ValueError('Each speed line requires at least two map points')
    return out, speeds


def _interp_lines(rate, speed_pct, lines, fn, rate_key):
    lines, speeds = _speed_lines(lines)
    s = float(speed_pct)
    speed_in = speeds[0] <= s <= speeds[-1]
    lo = max([x for x in speeds if x <= s], default=speeds[0])
    hi = min([x for x in speeds if x >= s], default=speeds[-1])
    l0 = lines[speeds.index(lo)]
    l1 = lines[speeds.index(hi)]
    r0 = fn(rate, l0['points'])
    r1 = fn(rate, l1['points'])
    w = 0.0 if hi == lo else (s - lo) / (hi - lo)
    keys = ('head_bar', 'efficiency') if fn is pump_map else ('pressure_ratio', 'efficiency')
    result = {k: (1-w)*r0[k] + w*r1[k] for k in keys}
    flow_in = bool(r0['in_envelope'] and r1['in_envelope'])
    in_env = bool(speed_in and flow_in)
    reasons = []
    if not speed_in:
        reasons.append('speed_below_map' if s < speeds[0] else 'speed_above_map')
    if not flow_in:
        reasons.append('flow_outside_map')
    result.update({
        'speed_pct': s,
        'min_speed_pct': speeds[0],
        'max_speed_pct': speeds[-1],
        'speed_in_envelope': speed_in,
        'flow_in_envelope': flow_in,
        'in_envelope': in_env,
        'map_status': 'interpolated' if in_env else 'out_of_envelope',
        'extrapolated': not in_env,
        'extrapolation_reasons': reasons,
        'speed_clamped': not speed_in,
        'evaluated_speed_pct': min(max(s, speeds[0]), speeds[-1]),
    })
    return result


def pump_map_2d(rate_m3d, speed_pct, speed_lines):
    return _interp_lines(float(rate_m3d), speed_pct, speed_lines, pump_map, 'rate_m3d')


def compressor_map_2d(rate_sm3d, speed_pct, speed_lines):
    return _interp_lines(float(rate_sm3d), speed_pct, speed_lines, compressor_map, 'rate_sm3d')


def compressor_envelope(rate_sm3d, surge_rate_sm3d, choke_rate_sm3d, power_kw=None, max_power_kw=None):
    q=float(rate_sm3d); surge=float(surge_rate_sm3d); choke=float(choke_rate_sm3d)
    if surge < 0 or choke <= surge:
        raise ValueError('Invalid compressor envelope')
    power_ok=max_power_kw is None or power_kw is None or float(power_kw)<=float(max_power_kw)
    margin_surge=(q-surge)/max(surge,1e-12); margin_choke=(choke-q)/max(choke,1e-12)
    return {'above_surge':q>=surge,'below_choke':q<=choke,'power_ok':power_ok,
            'in_envelope':q>=surge and q<=choke and power_ok,
            'surge_margin_fraction':margin_surge,'choke_margin_fraction':margin_choke}


def pump_envelope(rate_m3d,min_rate_m3d,max_rate_m3d,npsha_m=None,npshr_m=None):
    q=float(rate_m3d); lo=float(min_rate_m3d); hi=float(max_rate_m3d)
    if lo < 0 or hi <= lo:
        raise ValueError('Invalid pump envelope')
    npsh_ok=npsha_m is None or npshr_m is None or float(npsha_m)>=float(npshr_m)
    return {'flow_ok':lo<=q<=hi,'npsh_ok':npsh_ok,'in_envelope':lo<=q<=hi and npsh_ok,
            'npsh_margin_m':None if npsha_m is None or npshr_m is None else float(npsha_m)-float(npshr_m)}
