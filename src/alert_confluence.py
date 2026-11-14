"""Fail closed on old single-OB alerts, including archived resend payloads."""

import math
from itertools import product


PATHS = {'fast': ('1h', '15m', '5m'), 'short': ('4h', '1h', '15m'),
         'medium': ('1d', '4h', '1h'), 'long': ('1w', '1d', '4h')}


def _touches(touch, low, high, known):
    if not touch.get('hit') or touch.get('bar_open', touch.get('at', -1)) < known:
        return False
    if touch.get('evidence') == 'completed_1m_range':
        a, b = touch.get('observed_low'), touch.get('observed_high')
        return (a is not None and b is not None and math.isfinite(a) and math.isfinite(b)
                and a <= b and a <= high and b >= low)
    price = touch.get('observed_price')
    return price is not None and math.isfinite(price) and low <= price <= high


def confluence_error(candidate):
    """Validate the original three-frame band; later stair entries retain that proof."""
    try:
        path = PATHS[candidate['track']]
        if tuple(candidate['path']) != path:
            return 'wrong_timeframe_path'
        parent = candidate['parent']
        if parent['timeframe'] != path[0] or parent['kind'] != 'OB':
            return 'missing_upper_ob'
        base = candidate.get('overlap') or candidate
        formation = base.get('formation')
        if not formation or (formation['start'], formation['end']) != (parent['origin'], parent['created']):
            return 'missing_parent_formation'
        low, high = base['band']
        if not all(math.isfinite(v) for v in (low, high)) or low >= high:
            return 'invalid_common_band'
        sources = base.get('sources', [])
        if not any(s['id'] == parent['id'] and s == parent for s in sources):
            return 'missing_parent_source'
        children = []
        for tf, kinds in ((path[1], ('OB', 'FVG')), (path[2], ('OB',))):
            children.append([s for s in sources if s['timeframe'] == tf and s['kind'] in kinds
                             and s['direction'] == parent['direction'] == candidate['direction']
                             and parent['origin'] <= s['origin'] < s['created'] <= parent['created']])
        if not all(children):
            return 'missing_formation_child'
        for middle, final in product(*children):
            zones = (parent, middle, final)
            if not all(math.isfinite(z['low']) and math.isfinite(z['high']) for z in zones):
                continue
            a, b = max(z['low'] for z in zones), min(z['high'] for z in zones)
            if a <= low < high <= b:
                if not _touches(base['touch'], low, high, parent['created']):
                    return 'common_band_not_touched'
                if base is not candidate:
                    if not _touches(candidate['touch'], *candidate['band'], base['touch']['at']):
                        return 'entry_band_not_touched'
                return None
        return 'no_three_frame_intersection'
    except (KeyError, TypeError, ValueError, OverflowError):
        return 'incomplete_confluence_proof'
