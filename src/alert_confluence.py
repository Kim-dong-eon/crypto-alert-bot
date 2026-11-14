"""Fail closed on old single-OB alerts, including archived resend payloads."""

import math
from itertools import product


PATHS = {'fast': ('1h', '15m', '5m'), 'short': ('4h', '1h', '15m'),
         'medium': ('1d', '4h', '1h'), 'long': ('1w', '1d', '4h')}
SOURCE_KINDS = (('OB',), ('OB', 'FVG'), ('OB',))


def source_allowed(path, timeframe, kind):
    return any(timeframe == tf and kind in kinds for tf, kinds in zip(path, SOURCE_KINDS))


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
        if any(not source_allowed(path, s['timeframe'], s['kind']) for s in sources):
            return 'wrong_source_role'
        if not any(s['id'] == parent['id'] and s == parent for s in sources):
            return 'missing_parent_source'
        children = []
        for tf, kinds in zip(path[1:], SOURCE_KINDS[1:]):
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
                    entry_sources = candidate.get('sources', [])
                    if any(not source_allowed(path, s['timeframe'], s['kind']) for s in entry_sources):
                        return 'wrong_source_role'
                    # Later stair OBs may be outside the original triple band,
                    # but must still be same-direction final-timeframe OBs.
                    if not any(s['timeframe'] == path[2] and s['kind'] == 'OB'
                               and s['direction'] == candidate['direction']
                               and s['low'] <= candidate['band'][0] <= candidate['band'][1] <= s['high']
                               for s in entry_sources):
                        return 'missing_entry_ob'
                    if not _touches(candidate['touch'], *candidate['band'], base['touch']['at']):
                        return 'entry_band_not_touched'
                return None
        return 'no_three_frame_intersection'
    except (KeyError, TypeError, ValueError, OverflowError):
        return 'incomplete_confluence_proof'
