"""Three alert grades for a single higher-timeframe OB reaction."""

from bisect import bisect_left
from collections import defaultdict
from dataclasses import asdict
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import hashlib
from itertools import product

from src.ict_engine import TF_MS, frame_open
from src.entry_execution import entry_plan
from src.alert_confluence import source_allowed
from src.pullback_data import live_frames
from src.ict_overlap import LABELS, kst, overlaps
from src.overlap_entry import MINUTE
from src.pullback_alerts import POLICY, REFINEMENT, TOP_COUNTS, prepare, rooted_context
from src.staircase_rules import band_touch, net_rr, opposing_upper_contact, risk_band, staircases, ote_band, wick_breach
from src.structure_review import turns
from src.star_retests import retest_entries, target_at_contact, target_spent_at
from config.overlap_settings import TRACK_LABELS


RULESET = 'star-ob-stair-retest-v2'
TITLES = {1: '하위 OB 관찰', 2: '3중 겹침 첫 접촉', 3: '진입 조건 충족'}
UPPER_ATR_SCALES = {
    'small': {'atr_multiple': 1.5, 'min_fraction': 0.},
    'recent': {'atr_multiple': 2., 'min_fraction': 0.},
    'broad': {'atr_multiple': 3., 'min_fraction': 0.},
}


def atr_label(multiplier):
    return f'{float(multiplier):g} ATR'


def confirmed_direction(bars, points, tf, *, close_times=None):
    """Most recent body-close break of a level already known at that close."""
    confirmations = defaultdict(list)
    for point in points:
        confirmations[point['confirmed_index']].append(point)
    levels = {}
    direction = 0
    known_at = 0
    for i, bar in enumerate(bars):
        if i:
            previous = bars[i - 1].close
            closed_at = close_times[i] if close_times is not None else bar.timestamp + TF_MS[tf]
            if direction != 1 and 'high' in levels and previous <= levels['high'] < bar.close:
                direction, known_at = 1, closed_at
            elif direction != -1 and 'low' in levels and previous >= levels['low'] > bar.close:
                direction, known_at = -1, closed_at
        for point in confirmations.get(i, ()):
            levels[point['kind']] = point['price']
    return direction, known_at


def first_contact(low, high, known, price, now, minutes, since):
    """Save the first causal contact, even if later minutes move away."""
    for bar in minutes:
        if (bar.timestamp >= max(known, since) and bar.timestamp + MINUTE <= now
                and bar.volume > 0 and bar.low <= high and bar.high >= low):
            return {'hit': True, 'at': bar.timestamp + MINUTE, 'bar_open': bar.timestamp,
                    'valid_from': known, 'observed_low': bar.low, 'observed_high': bar.high,
                    'evidence': 'completed_1m_range'}
    return band_touch(low, high, known, price, now, (), since)


def contact_price(touch, low, high):
    if 'observed_price' in touch:
        return touch['observed_price']
    return (max(low, touch['observed_low']) + min(high, touch['observed_high'])) / 2


def choose_stair(bars, points, tf, direction):
    for end in range(len(points), 3, -1):
        found = staircases(bars, points[end - 4:end], tf, direction)
        if found:
            return found[0]
    return None


def overview_wave(bars, points, origin, staircase):
    if staircase:
        return staircase
    index = next((i for i, p in enumerate(points) if bars[p['index']].timestamp >= origin), len(points))
    relevant = points[max(0, index - 2):index + 2]
    display = [{**p, 'timestamp': bars[p['index']].timestamp} for p in relevant]
    return {'points': display, 'chart_start': display[0]['timestamp'] if display else origin, 'target': None}


def observation_wave(bars, points, at, origin=None):
    known = [p for p in points if p['confirmed_at'] <= at]
    if origin is None:
        known = known[-4:]
    else:
        before = sum(bars[p['index']].timestamp < origin for p in known)
        known = known[max(0, before - 3):]
    display = [{**p, 'timestamp': bars[p['index']].timestamp} for p in known]
    return {'points': display, 'chart_start': display[0]['timestamp'] if display else at, 'target': None}


def observation_reference(choice, lower_bars, points, direction, *, lower_points=(), tick_size=None,
                          minute_bars=(), price=None):
    source = choice['sources'][-1]
    kind = 'high' if direction == 1 else 'low'
    touch = choice['touch']
    known_at = touch.get('bar_open', touch['at'])
    target = next((p for p in reversed(points) if p['kind'] == kind
                   and p['confirmed_at'] <= known_at), None)
    if target is None:
        return None
    if choice.get('level') != 2 and choice.get('phase') != 'first_contact':
        origin = next((bar for bar in lower_bars if bar.timestamp == source['origin']), None)
        if origin is None:
            return None
        return {'stop': origin.low if direction == 1 else origin.high, 'target': target['price']}

    # Tie the stop to the leg that was in progress at the lower OB's origin.
    # A later confirmed turn may describe a different reaction to that OB.
    pivot = next((p for p in reversed(lower_points) if p['confirmed_at'] <= known_at
                  and lower_bars[p['index']].timestamp <= source['origin']), None)
    stop_kind = 'low' if direction == 1 else 'high'
    if pivot is None or pivot['kind'] != stop_kind:
        return None
    pivot_at = lower_bars[pivot['index']].timestamp
    extreme = pivot['price']
    if price is not None and direction * (price - extreme) < 0:
        return None
    for bars in (lower_bars[pivot['index'] + 1:], minute_bars):
        if any(b.timestamp > pivot_at and direction * ((b.low if direction == 1 else b.high) - extreme) < 0
               for b in bars):
            return None

    try:
        tick = Decimal(str(tick_size))
    except InvalidOperation:
        return None
    if not tick.is_finite() or tick <= 0:
        return None
    rounding = ROUND_CEILING if direction == 1 else ROUND_FLOOR
    units = (Decimal(str(extreme)) / tick).to_integral_value(rounding=rounding)
    stop = float((units - direction) * tick)
    if stop <= 0:
        return None
    return {'stop': stop, 'target': target['price'], 'pivot_at': pivot_at, 'pivot_price': extreme}


def upper_risk_reference(parent, bars, points, direction, at, *, tick_size=None, minute_bars=(), price=None):
    """Star-one risk belongs to the parent's departure swing, not a child OB.

    Retain independent values and failure reasons so a missing stop cannot
    erase a known target. All display bands share this single as-of snapshot.
    """
    target = next((p for p in reversed(points) if p['kind'] == ('high' if direction == 1 else 'low')
                   and p['confirmed_at'] <= at), None)
    pivot = next((p for p in reversed(points) if p['kind'] == ('low' if direction == 1 else 'high')
                  and p['confirmed_at'] <= at
                  and parent.origin <= bars[p['index']].timestamp < parent.created), None)
    result = {'stop': None, 'target': target['price'] if target else None,
              'stop_tf': parent.timeframe, 'as_of': at, 'stop_reason': None,
              'known_at': max(parent.created, target['confirmed_at'] if target else 0,
                              pivot['confirmed_at'] if pivot else 0),
              'target_reason': None if target else '상위 목표 미확정'}
    if pivot is None:
        result['stop_reason'] = '상위 출발 저점·고점 미확정'
        return result
    stamp, extreme = bars[pivot['index']].timestamp, pivot['price']
    result.update(pivot_at=stamp, pivot_price=extreme)
    try:
        tick = Decimal(str(tick_size))
        if not tick.is_finite() or tick <= 0:
            raise ValueError('invalid tick')
        units = (Decimal(str(extreme)) / tick).to_integral_value(
            rounding=ROUND_CEILING if direction == 1 else ROUND_FLOOR)
        result['stop'] = float((units - direction) * tick)
    except (InvalidOperation, ValueError):
        result['stop_reason'] = '호가 단위 미확인'
        return result
    if result['stop'] <= 0:
        result.update(stop=None, stop_reason='유효한 손절 가격 없음')
        return result
    broken = (price is not None and direction * (price - extreme) < 0) or any(
        stamp < b.timestamp < at
        and direction * ((b.low if direction == 1 else b.high) - extreme) < 0
        for b in (*bars[pivot['index'] + 1:], *minute_bars))
    if broken:
        result['stop_reason'] = '상위 구조 기준점 이탈'
    return result


def assess_reference(reference, band, direction, quote, tick_size):
    """A reference is not executable when either structural value is unavailable."""
    result = {**reference, 'plan': None, 'rr': None, 'costs_checked': False}
    if (reference.get('stop') is None or reference.get('target') is None
            or reference.get('stop_reason') or reference.get('target_reason')):
        return result
    if not quote or not tick_size or not 0 <= quote.get('slippage_per_side', -1) < 1:
        return result
    result['costs_checked'] = True
    result['plan'] = entry_plan(*band, reference['stop'], reference['target'], direction,
                                quote, tick_size, require_touch=False)
    result['rr'] = (result['plan']['rr'] if result['plan'] else
                    net_rr(band[1] if direction == 1 else band[0], reference['stop'],
                           reference['target'], direction, slippage=quote['slippage_per_side']))
    return result


def risk_status(risk):
    reasons = [risk.get(key) for key in ('stop_reason', 'target_reason') if risk.get(key)]
    if reasons:
        return ' · '.join(reasons)
    if not risk.get('costs_checked'):
        return '비용 확인 대기 · 1:3 미확정'
    rr = risk.get('rr')
    return (f'1:{rr:.2f} · ' if rr is not None and rr >= 0 else '') + '1:3 미충족'


def qualify_contact(choice, reference, direction, quote, tick_size, price, now, minutes, since,
                    *, confirmed_only=False):
    """Require contact with the cost-adjusted 3R sub-band, not merely its OB."""
    risk = assess_reference(reference, choice['band'], direction, quote, tick_size)
    plan = risk['plan']
    if not plan:
        return None
    low, high = plan['band']
    if low >= high:
        return None
    known = max(choice['touch']['valid_from'], reference['known_at'])
    touch = first_contact(low, high, known, price, now, minutes, since)
    if not touch['hit'] or (confirmed_only and touch.get('evidence') != 'completed_1m_range'):
        return None
    return {**choice, 'structural_band': choice['band'], 'band': plan['band'],
            'touch': touch, 'observation_risk': risk, 'contact_rr_required': True}


def overlap_contacts(parent, area, lower_obs, zones, pair, arm, price, now, minutes, since):
    top, middle = pair
    evidence = [parent]
    evidence.extend(z for z in zones if z.direction == parent.direction
                    and z.low < parent.high and z.high > parent.low
                    and ((z.timeframe == top and z.kind == 'FVG')
                         or (z.timeframe == middle and z.origin >= area['origin'] - 2 * TF_MS[middle])))
    contacts = []
    for raw in overlaps(evidence, pair, price):
        if parent.id not in raw['source_ids'] or not any(z['kind'] == 'FVG' for z in raw['sources']):
            continue
        for ob in lower_obs:
            lo, hi = max(raw['low'], ob.low), min(raw['high'], ob.high)
            if lo >= hi:
                continue
            known = max(raw['latest_source'], ob.created, arm['at'])
            hit = first_contact(lo, hi, known, price, now, minutes, since)
            if hit['hit']:
                contacts.append({'level': 2, 'touch': hit, 'sources': raw['sources'] + [asdict(ob)],
                                 'band': [lo, hi], 'score': raw['score'] + 1})
    return contacts


def formation_children(parent, zones, path, *, middle_fvg=False):
    """Valid same-direction child zones inside the parent's formation interval."""
    _, middle, final = path
    children = defaultdict(list)
    for ob in zones:
        if (ob.timeframe in (middle, final)
                and source_allowed(path, ob.timeframe, ob.kind)
                and (ob.kind != 'FVG' or middle_fvg)
                and ob.direction == parent.direction
                and parent.origin <= ob.origin < ob.created <= parent.created
                and max(parent.low, ob.low) < min(parent.high, ob.high)):
            children[ob.timeframe].append(ob)
    return children


def formation_contacts(parent, zones, path, direction_at, price, now, minutes, since, *, confirmed_only=False, middle_fvg=False):
    """Both child OBs must have formed fully inside this parent OB's original candles."""
    _, middle, final = path
    children = formation_children(parent, zones, path, middle_fvg=middle_fvg)
    known = max(parent.created, direction_at)
    contacts = []
    for middle_ob, final_ob in product(children[middle], children[final]):
        sources = (parent, middle_ob, final_ob)
        lo = max(ob.low for ob in sources)
        hi = min(ob.high for ob in sources)
        if lo >= hi:
            continue
        hit = first_contact(lo, hi, known, price, now, minutes, since)
        if confirmed_only and hit.get('evidence') != 'completed_1m_range':
            continue
        if hit['hit']:
            contacts.append({'level': 1, 'phase': 'first_contact', 'touch': hit, 'sources': [asdict(ob) for ob in sources],
                             'band': [lo, hi], 'score': 3,
                             'formation': {'start': parent.origin, 'end': parent.created}})
    return contacts


def formation_map(parent, zones, path, trigger_band, *, middle_fvg=False, assess=None):
    """All valid triple bands, independent of contact. Never create alert choices here.

    Only identical intersections are deduplicated. Overlapping bands share a
    chart number but retain their own sources, entry ranges and risk references.
    """
    _, middle, final = path
    children = formation_children(parent, zones, path, middle_fvg=middle_fvg)
    unique = {}
    for mid, child in product(children[middle], children[final]):
        sources = [asdict(z) for z in (parent, mid, child)]
        low, high = max(z['low'] for z in sources), min(z['high'] for z in sources)
        if low >= high:
            continue
        area = unique.setdefault((low, high), {'band': [low, high], 'alternatives': []})
        area['alternatives'].append({'sources': sources,
                                    'risk': assess(sources, [low, high]) if assess else None})
    # Keep the actual trigger separate before grouping overlapping alternatives.
    # Otherwise an overlapping, untouched zone inherits the trigger label and
    # its extension disappears when the chart skips that trigger group.
    current = unique.pop(tuple(trigger_band), None)
    if current is None:
        alternatives = [v for area in unique.values()
                        if area['band'][0] <= trigger_band[0] < trigger_band[1] <= area['band'][1]
                        for v in area['alternatives']]
        if alternatives:
            current = {'band': list(trigger_band), 'alternatives': [
                {**v, 'risk': assess(v['sources'], trigger_band) if assess else None} for v in alternatives]}
    groups = []
    for area in sorted(unique.values(), key=lambda a: a['band']):
        lo, hi = area['band']
        if groups and lo < groups[-1]['band'][1]:
            groups[-1]['band'][1] = max(groups[-1]['band'][1], hi)
            groups[-1]['areas'].append(area)
        else:
            groups.append({'band': [lo, hi], 'areas': [area]})
    groups.sort(key=lambda g: g['band'][0], reverse=parent.direction == 1)
    count = 0
    for group in groups:
        lo, hi = group['band']
        if (lo < trigger_band[0] if parent.direction == 1 else hi > trigger_band[1]):
            count += 1
            name = '지지' if parent.direction == 1 else '저항'
            group.update(label=f'다음 {name} {count}', relation='next')
        else:
            group.update(label='다른 겹침', relation='other')
        group['areas'].sort(key=lambda a: a['band'][0], reverse=parent.direction == 1)
    if current:
        groups.insert(0, {'band': list(trigger_band), 'areas': [current],
                          'label': '이번 알림 구간', 'relation': 'trigger'})
    return groups


def map_risk(sources, band, lower_bars, upper_points, direction, now, *,
             lower_points=(), tick_size=None, minute_bars=(), price=None, quote=None):
    """Hypothetical entry at a mapped band, assessed with information known now."""
    reference = observation_reference(
        {'sources': sources, 'phase': 'first_contact', 'touch': {'at': now}},
        lower_bars, upper_points, direction, lower_points=lower_points,
        tick_size=tick_size, minute_bars=minute_bars, price=price)
    if not reference:
        return None
    plan = entry_plan(*band, reference['stop'], reference['target'], direction,
                      quote, tick_size, require_touch=False) if quote and tick_size else None
    return {**reference, 'plan': plan, 'costs_checked': bool(quote and tick_size)}


def formation_reaction(overlap, bars, points, tf, direction):
    touch = overlap['touch']
    cutoff = touch.get('bar_open', touch['at'])
    kind = 'high' if direction == 1 else 'low'
    pivot = next((p for p in reversed(points) if p['kind'] == kind and p['confirmed_at'] <= cutoff), None)
    if pivot is None:
        return None
    level = pivot['price']
    width = TF_MS[tf]
    start = max(1, bisect_left(bars, touch['at'] - width + 1, key=lambda b: b.timestamp))
    for i in range(start, len(bars)):
        if direction * (bars[i - 1].close - level) <= 0 < direction * (bars[i].close - level):
            return {'at': bars[i].timestamp + width, 'bar_open': bars[i].timestamp,
                    'price': bars[i].close, 'level': level}
    return None


def formation_pivot(parent, bars, points, now):
    """The upper swing's extreme must belong to the candles that formed the OB."""
    kind = 'low' if parent.direction == 1 else 'high'
    for point in reversed(points):
        stamp = bars[point['index']].timestamp
        if (point['kind'] == kind and parent.origin <= stamp < parent.created
                and point['confirmed_at'] <= now):
            return {**point, 'timestamp': stamp}
    return None


def scan(symbol, frames, now, price, *, minute_bars=(), touch_since=0, contexts=None, tracks=None,
         formation_tracks=(), origin_tracks=(), execution_quote=None, tick_size=None, upper_atr=False,
         first_touch_cutoff=None, departure_ob_frames=(), session_closes=None, source_prefix=None,
         middle_fvg=False, require_ote=False, live_weekly=False, require_contact_rr=False):
    session_closes = dict(session_closes or {})
    forming_weekly = False
    if live_weekly and '1w' not in session_closes:
        live = live_frames(frames, minute_bars, now, price)
        weekly = live.get('1w', [])
        if weekly and weekly[-1].timestamp == frame_open(now, '1w'):
            frames = {**frames, '1w': weekly}
            session_closes['1w'] = {b.timestamp: min(b.timestamp + TF_MS['1w'], now) for b in weekly}
            forming_weekly = True
    track_cursors = touch_since if isinstance(touch_since, dict) else {}
    touch_since = min(track_cursors.values(), default=0) if isinstance(touch_since, dict) else touch_since
    closed_minutes = tuple(b for b in minute_bars if b.timestamp + MINUTE <= now)
    recent_minutes = tuple(b for b in closed_minutes if b.timestamp >= touch_since)
    first_touch_minutes = recent_minutes if first_touch_cutoff is None else tuple(
        b for b in recent_minutes if b.timestamp + MINUTE <= first_touch_cutoff)
    scales = {'scales': UPPER_ATR_SCALES} if upper_atr else {}
    if departure_ob_frames:
        scales['departure_ob_frames'] = departure_ob_frames
    session_closes = session_closes or {}
    result, usable, zones = prepare(symbol, frames, now, price, tracks, build_overlaps=False,
                                    session_closes=session_closes, source_prefix=source_prefix, **scales)
    result['ruleset'] = RULESET
    result['forming_weekly'] = forming_weekly
    previous = contexts or {}
    updated = {}
    for track, state in result['tracks'].items():
        track_since = track_cursors.get(track, touch_since)
        recent_minutes = tuple(b for b in closed_minutes if b.timestamp >= track_since)
        first_touch_minutes = recent_minutes if first_touch_cutoff is None else tuple(
            b for b in recent_minutes if b.timestamp + MINUTE <= first_touch_cutoff)
        top, middle = state['pair']
        state['forming_upper'] = forming_weekly and top == '1w'
        final = REFINEMENT[track]
        anchored = track in formation_tracks
        require_origin = anchored and track in origin_tracks
        if 'analysis' not in state or len(usable.get(final, ())) < 16:
            updated.update((key, episode) for key, episode in previous.items() if episode['track'] == track)
            state['status'] = 'data_unavailable'
            continue
        upper_bars = usable[top][-TOP_COUNTS[top]:]
        lower_bars = usable[final]
        lower_points = turns(lower_bars, 1.5, 0., final)
        lower_stairs = {d: choose_stair(lower_bars, lower_points, final, d) for d in (1, -1)}
        recent_upper_points = state['analysis']['views']['recent']['turns']
        found = []
        for lens, view in state['analysis']['views'].items():
            points = view['turns']
            closes = session_closes.get(top)
            direction, direction_at = confirmed_direction(upper_bars, points, top,
                close_times=[closes[b.timestamp] for b in upper_bars] if closes is not None else None)
            view['structure'] = 'up' if direction == 1 else 'down' if direction == -1 else 'mixed'
            if upper_atr:
                multiplier = UPPER_ATR_SCALES[lens]['atr_multiple']
                state.setdefault('atr_directions', {})[multiplier] = direction
                if direction:
                    state.setdefault('atr_waves', {})[multiplier] = observation_wave(upper_bars, points, now)['points']
            if lens == 'recent':
                state['direction'] = direction
            if not direction:
                continue
            upper_stairs = {s['origin']: s for s in staircases(upper_bars, points, top, direction, session_closes.get(top))}
            for parent in zones:
                if parent.timeframe != top or parent.kind != 'OB' or parent.direction != direction:
                    continue
                if anchored and parent.origin < max(usable[middle][0].timestamp, lower_bars[0].timestamp):
                    state.setdefault('missing_formation_history', []).append(parent.origin)
                    continue
                arm_probe = first_contact(parent.low, parent.high, parent.created, price, now, recent_minutes, track_since)
                if not arm_probe['hit'] and not any(e['parent_id'] == parent.id and e['track'] == track
                                                   and e['lens'] == lens for e in previous.values()):
                    continue
                pivot = formation_pivot(parent, upper_bars, points, now) if require_origin else None
                if require_origin and pivot is None:
                    continue
                area = None if top in session_closes else rooted_context(parent, upper_bars, points, view['areas'], top, now)
                if area is None and not anchored:
                    continue
                root = pivot['timestamp'] if pivot else area['pivot_origin'] if area else parent.origin
                if pivot and area and area['pivot_origin'] != root:
                    area = None
                scope = 'formation-pivot' if pivot else 'formation-ob' if anchored else root
                if upper_atr:
                    scope = f'atr:{scope}'
                identity = f'{track}|{lens}|{parent.id}|{scope}'
                if previous.get(identity, {}).get('invalidated_at') is not None:
                    updated[identity] = previous[identity]
                    continue
                upper = upper_stairs.get(root) if area or pivot else None
                protected = parent.stop
                if pivot:
                    protected = pivot['price']
                elif area:
                    root_bar = next(b for b in upper_bars if b.timestamp == root)
                    protected = root_bar.low if direction == 1 else root_bar.high
                # The root candle already defines the extreme. Inspect every
                # later upper wick plus lower candles/minutes in the open upper
                # candle, without waiting for its close or the next contact.
                root_end = closes.get(root, root + TF_MS[top]) if closes else root + TF_MS[top]
                breach = wick_breach(upper_bars, protected, direction, root + 1, now)
                if breach is None:
                    breach = wick_breach((*usable.get(middle, ()), *lower_bars, *closed_minutes),
                                         protected, direction, root_end, now)
                if breach is None and direction * (price - protected) < 0:
                    breach = now
                if breach is not None:
                    updated[identity] = {**previous.get(identity, {'track': track, 'lens': lens,
                        'parent_id': parent.id, 'arm': arm_probe, 'overlap': None}),
                        'invalidated_at': breach, 'invalidated_reason': 'departure_wick_breach'}
                    continue
                setup_known = max(direction_at, pivot['confirmed_at']) if pivot else direction_at
                if direction * (price - protected) < 0 or direction * (price - parent.stop) < 0:
                    if identity in previous and previous[identity]['arm']['at'] >= setup_known:
                        updated[identity] = previous[identity]
                    continue
                upper_ready = upper and not (
                    any(s['invalidated_at'] for s in upper_stairs.values() if s['origin'] >= root)
                    or any(wick_breach((*usable.get(middle, ()), *lower_bars, *closed_minutes),
                                       s['protected'], direction,
                                       closes.get(s['origin'], s['origin'] + TF_MS[top]) if closes
                                       else s['origin'] + TF_MS[top], now) is not None
                           for s in upper_stairs.values() if s['origin'] >= root)
                    or any(b.timestamp >= upper['known_at']
                           and direction * ((b.high if direction == 1 else b.low) - upper['target']) >= 0
                           for b in upper_bars))
                if not anchored and upper and not upper_ready:
                    continue
                episode = previous.get(identity)
                if episode is None or episode['arm']['at'] < setup_known:
                    ready_at = parent.created if anchored else area['created']
                    arm = first_contact(parent.low, parent.high, max(ready_at, parent.created, setup_known),
                                        price, now, recent_minutes, track_since)
                    if not arm['hit']:
                        continue
                    episode = {'track': track, 'lens': lens, 'parent_id': parent.id, 'arm': arm, 'overlap': None}
                arm = episode['arm']
                floor = upper['end'] if upper else area['origin'] if area else parent.origin
                lower_obs = [z for z in zones if z.timeframe == final and z.kind == 'OB'
                             and z.direction == direction and z.origin >= floor]
                prior_overlap = episode['overlap']
                if anchored:
                    choices = formation_contacts(parent, zones, state['path'], setup_known,
                                                 price, now, first_touch_minutes, track_since,
                                                 confirmed_only=first_touch_cutoff is not None and not state['forming_upper'], middle_fvg=middle_fvg)
                else:
                    choices = overlap_contacts(parent, area, lower_obs, zones, (top, middle), arm,
                                               price, now, recent_minutes, track_since)
                if prior_overlap is None and choices:
                    first_overlap = min(choices, key=lambda c: (c['touch']['at'], -c['score']))
                    episode = {**episode, 'overlap': first_overlap}
                    if anchored:
                        choices = [first_overlap]
                elif anchored:
                    choices = [c for c in choices if c['sources'] == prior_overlap['sources']]
                # Old reaction records are retained for audit but never create a new ★★.
                if anchored and episode['overlap'] and 'entry_target' not in episode:
                    episode = {**episode, 'entry_target': target_at_contact(points, episode['overlap'])}
                if anchored and not episode.get('target_spent_at'):
                    spent = target_spent_at(episode['overlap'], episode.get('entry_target'), direction,
                                            lower_bars, closed_minutes, now)
                    if spent is not None:
                        episode = {**episode, 'target_spent_at': spent}
                prior_reaction = None
                updated[identity] = episode
                for ob in (() if anchored else lower_obs):
                    hit = first_contact(ob.low, ob.high, max(ob.created, arm['at']), price, now, recent_minutes, track_since)
                    if hit['hit']:
                        choices.append({'level': 1, 'touch': hit, 'sources': [asdict(parent), asdict(ob)],
                                        'band': [ob.low, ob.high], 'score': 2})
                step = lower_stairs[direction]
                latest_upper = next(reversed(upper_stairs.values()), None)
                followup = prior_overlap if anchored else episode['overlap']
                reaction_at = followup['touch']['at'] if anchored and followup else arm['at']
                if (not anchored and upper_ready and followup and step and not step['invalidated_at']
                        and direction * (price - latest_upper['protected']) >= 0
                        and step['origin'] >= max(upper['end'], frame_open(reaction_at - 1, final))
                        and step['known_at'] > reaction_at and direction * (price - step['protected']) > 0
                        and not opposing_upper_contact({'direction': direction, 'pair': state['pair'], 'trade_setup': {'upper': upper}},
                                                       usable, zones, now, price, minute_bars)):
                    stop, target = step['protected'], upper['target']
                    for ob in lower_obs:
                        if not step['origin'] - 2 * TF_MS[final] <= ob.origin <= step['end']:
                            continue
                        entry_low, entry_high = ob.low, ob.high
                        if require_ote:
                            ote_low, ote_high = ote_band(step)
                            entry_low, entry_high = max(entry_low, ote_low), min(entry_high, ote_high)
                        lo, hi = risk_band(entry_low, entry_high, stop, target, direction)
                        if lo >= hi:
                            continue
                        entry = hi if direction == 1 else lo
                        rr = net_rr(entry, stop, target, direction)
                        if rr < 3 - 1e-9:
                            continue
                        known = max(upper['known_at'], step['known_at'], ob.created, episode['overlap']['touch']['at'],
                                    prior_reaction['at'] if prior_reaction else 0)
                        hit = first_contact(lo, hi, known, price, now, recent_minutes, track_since)
                        if not hit['hit']:
                            continue
                        plan = {'band': [lo, hi], 'entry': entry, 'rr': rr}
                        if execution_quote is not None:
                            quote = execution_quote()
                            if direction == 1:
                                permitted = [max(entry_low, protected, parent.stop, latest_upper['protected']), entry_high]
                            else:
                                permitted = [entry_low, min(entry_high, protected, parent.stop, latest_upper['protected'])]
                            plan = entry_plan(*permitted, stop, target, direction, quote, tick_size)
                            if plan is None:
                                continue
                            plan['structural_band'] = permitted
                            hit = {'hit': True, 'at': now, 'valid_from': known, 'observed_price': quote['mid'],
                                   'evidence': 'live_orderbook_mid'}
                        choices.append({'level': 3, 'touch': hit, 'sources': [asdict(parent), asdict(ob)],
                                        'score': episode['overlap']['score'], 'lower': step, 'stop': stop,
                                        'target': target, 'overlap': episode['overlap'], **plan})
                if anchored and prior_overlap and not episode.get('target_spent_at'):
                    choices.extend(retest_entries(parent, prior_overlap, step, episode.get('entry_target'),
                        zones, lower_bars, closed_minutes, now, price, execution_quote, tick_size,
                        upper_ready=bool(upper_ready and latest_upper
                            and direction * (price - latest_upper['protected']) >= 0),
                        require_ote=require_ote))
                if choices and anchored and require_contact_rr:
                    reference = upper_risk_reference(parent, upper_bars, points, direction, now,
                        tick_size=tick_size, minute_bars=closed_minutes, price=price)
                    try:
                        quote = execution_quote() if execution_quote else None
                    except Exception:
                        quote = None
                    choices = [qualified for choice in choices if (qualified := (
                        qualify_contact(choice, reference, direction, quote, tick_size, price, now,
                                        first_touch_minutes, track_since,
                                        confirmed_only=first_touch_cutoff is not None and not state['forming_upper'])
                        if choice['level'] == 1 else choice)) is not None]
                if choices:
                    best = max(choices, key=lambda c: (c['level'], c.get('in_initial_overlap', False), c['score'], c.get('rr', 0)))
                    sources = best['sources']
                    upper_wave = overview_wave(upper_bars, points, root, upper if upper_ready else None)
                    if best['level'] == 2 or best.get('formation'):
                        at = best.get('reaction', best['touch'])['at']
                        upper_wave = observation_wave(upper_bars, points if pivot else recent_upper_points, at, parent.origin)
                        best['lower_wave'] = observation_wave(lower_bars, lower_points, at)
                    if pivot:
                        best['departure_pivot'] = pivot
                    if upper_atr:
                        best['upper_atr'] = multiplier
                    if anchored:
                        children = formation_children(parent, zones, state['path'], middle_fvg=middle_fvg)
                        best['display_zones'] = [asdict(z) for values in children.values() for z in values]
                    quote = None
                    if anchored or (best['level'] < 3 and best.get('phase') != 'stair_retest'):
                        try:
                            quote = execution_quote() if execution_quote else None
                        except Exception:
                            pass
                    upper_reference = None
                    if anchored:
                        upper_reference = upper_risk_reference(
                            parent, upper_bars, points, direction, now, tick_size=tick_size,
                            minute_bars=closed_minutes, price=price)
                    if best['level'] == 1 and upper_reference is not None:
                        best['observation_risk'] = assess_reference(
                            upper_reference, best['band'], direction, quote, tick_size)
                    elif best['level'] < 3 and best.get('phase') != 'stair_retest':
                        target_points = points
                        reference = observation_reference(best, lower_bars, target_points, direction,
                                                          lower_points=lower_points, tick_size=tick_size,
                                                          minute_bars=closed_minutes, price=price)
                        if reference:
                            plan = entry_plan(*best['band'], reference['stop'], reference['target'], direction,
                                              quote, tick_size, require_touch=False) if quote and tick_size else None
                            best['observation_risk'] = {**reference, 'plan': plan, 'costs_checked': bool(quote and tick_size)}
                        else:
                            best['risk_unavailable'] = '구조 손절·상위 목표 미확정'
                    if anchored:
                        best['formation_map'] = formation_map(
                            parent, zones, state['path'], best['band'], middle_fvg=middle_fvg,
                            assess=lambda sources, band: assess_reference(
                                upper_reference, band, direction, quote, tick_size))
                    found.append({**best, 'symbol': symbol, 'exchange': 'bitget', 'track': track, 'path': state['path'],
                                  'pair': state['pair'], 'refinement_tf': final, 'direction': direction, 'price': price,
                                  'now': now, 'id': hashlib.sha256(identity.encode()).hexdigest()[:24],
                                  'context_id': identity, 'lenses': [lens],
                                  'forming_upper': state['forming_upper'],
                                  'parent': asdict(parent), 'arm': arm['at'], 'arm_price': contact_price(arm, parent.low, parent.high),
                                  'upper': upper_wave, 'source_ids': [z['id'] for z in sources],
                                  'low': best['band'][0], 'high': best['band'][1], 'alert_policy': POLICY, 'ruleset': RULESET,
                                  'context_price_breach': False, 'equilibrium': {'eligible': True}})
        state.update(candidates=found, candidate=None, status='watching' if found else 'no_star_contact')
    result['star_contexts'] = updated
    return result


def is_entry(candidate):
    return candidate.get('level') == 3 or candidate.get('phase') == 'stair_retest'


def alert_title(candidate):
    if candidate.get('phase') == 'stair_retest':
        return '상위·하위 계단 OB 재접촉' if candidate['level'] == 3 else '하위 계단 OB 재접촉'
    if candidate.get('phase') == 'reaction':
        return '하위 구조 반응 확인'
    if candidate.get('formation'):
        if candidate.get('contact_rr_required'):
            return '3중 겹침 · 1:3 구간 접촉'
        return '3중 겹침 첫 접촉'
    if candidate['level'] == 1:
        return f"{LABELS[candidate['refinement_tf']]} OB 관찰"
    return TITLES[candidate['level']]


def compact_warning(c):
    """Summarize direction conflicts without repeating ATR lists or extra charts."""
    import re
    opposite, overlap = [], []
    for warning in c.get('cross_track_warnings', ()):
        label = warning.split()[0]
        if '구조' in warning:
            opposite.append(label)
        elif '겹침' in warning:
            overlap.append(re.sub(r'\s*\([^)]*\)', '', warning))
    parts = []
    if opposite:
        parts.append('·'.join(dict.fromkeys(opposite)) + ' 반대 방향')
    if overlap:
        parts.append(' / '.join(dict.fromkeys(overlap)))
    return '⚠ ' + ' / '.join(parts) if parts else ''


def unit_text(candidate):
    return compact_atr_text(candidate)


def contact_status(c):
    """Snapshot location, never substitute the old touch price for current price."""
    import math
    price = (c.get('execution') or {}).get('mid') if is_entry(c) else c.get('price')
    if price is None or not math.isfinite(price):
        return '현재 위치 미확인'
    low, high = c['band']
    return '검사 시 구간 안' if low <= price <= high else '접촉 후 이탈'


def compact_atr_text(c):
    """One chart and at most five short text rows for every instrument."""
    level = c['level']
    side = '롱' if c['direction'] == 1 else '숏'
    label = LABELS[c['pair'][0]]
    exchange = c.get('exchange', 'bitget').title()
    lo, hi = map(price_text, c['band'])
    title = '3중 겹침 접촉' if c.get('contact_rr_required') and level == 1 else alert_title(c)
    lines = [f"{'★' * level} {c['symbol']} · {side} · {label} ({exchange})",
             title + ' · ' + contact_status(c)]
    if c.get('forming_upper'):
        lines[1] += ' · 주봉 형성 중'
    if is_entry(c):
        lines.append(f"진입 {lo}~{hi} · 순손익비 1:{c['rr']:.2f} (비용 반영)")
        detail = f"손절 {price_text(c['stop'])} · 목표 {price_text(c['target'])}"
        if c.get('phase') == 'stair_retest':
            detail += ' · 최초 겹침 안' if c['in_initial_overlap'] else ' · 최초 겹침 밖 후속 OB'
        lines.append(detail)
    elif risk := c.get('observation_risk'):
        if plan := risk.get('plan'):
            a, b = map(price_text, plan['band'])
            lines.append(f"진입 참고 {a}~{b} · 순손익비 1:{plan['rr']:.2f} (비용 반영)")
            lines.append(f"손절 {price_text(risk['stop'])} · 목표 {price_text(risk['target'])}")
        else:
            lines.append(f"접촉 구간 {lo}~{hi}")
            values = ' / '.join(f'{label} {price_text(risk[key])}' for key, label in
                                (('stop', '손절'), ('target', '목표')) if risk.get(key) is not None)
            lines.append((values + ' · ' if values else '') + risk_status(risk))
    else:
        lines.append(f"접촉 구간 {lo}~{hi}")
        if c.get('risk_unavailable'):
            lines.append('1:3 미확정 · ' + c['risk_unavailable'])
    if warning := compact_warning(c):
        lines.append(warning)
    return '\n'.join(lines)


def price_text(value):
    return f'{value:,.8f}'.rstrip('0').rstrip('.')
