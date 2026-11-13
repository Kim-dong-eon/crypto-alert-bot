"""Three alert grades for a single higher-timeframe OB reaction."""

from bisect import bisect_left
from collections import defaultdict
from dataclasses import asdict
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import hashlib
from itertools import product

from src.ict_engine import TF_MS, frame_open
from src.entry_execution import entry_plan
from src.ict_overlap import LABELS, kst, overlaps
from src.overlap_entry import MINUTE
from src.pullback_alerts import POLICY, REFINEMENT, TOP_COUNTS, prepare, rooted_context
from src.staircase_rules import band_touch, net_rr, opposing_upper_contact, risk_band, staircases
from src.structure_review import turns
from config.overlap_settings import TRACK_LABELS


RULESET = 'star-ob-reaction-v1'
TITLES = {1: '하위 OB 관찰', 2: '3중 겹침 첫 접촉', 3: '진입 조건 충족'}
UPPER_ATR_SCALES = {
    'small': {'atr_multiple': 1.5, 'min_fraction': 0.},
    'recent': {'atr_multiple': 2., 'min_fraction': 0.},
    'broad': {'atr_multiple': 3., 'min_fraction': 0.},
}


def atr_label(multiplier):
    return f'{float(multiplier):g} ATR'


def confirmed_direction(bars, points, tf):
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
            if direction != 1 and 'high' in levels and previous <= levels['high'] < bar.close:
                direction, known_at = 1, bar.timestamp + TF_MS[tf]
            elif direction != -1 and 'low' in levels and previous >= levels['low'] > bar.close:
                direction, known_at = -1, bar.timestamp + TF_MS[tf]
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
    if choice.get('level') != 2:
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


def formation_contacts(parent, zones, path, direction_at, price, now, minutes, since):
    """Both child OBs must have formed fully inside this parent OB's original candles."""
    _, middle, final = path
    children = defaultdict(list)
    for ob in zones:
        if (ob.timeframe in (middle, final) and ob.kind == 'OB' and ob.direction == parent.direction
                and parent.origin <= ob.origin < ob.created <= parent.created
                and max(parent.low, ob.low) < min(parent.high, ob.high)):
            children[ob.timeframe].append(ob)
    known = max(parent.created, direction_at)
    contacts = []
    for middle_ob, final_ob in product(children[middle], children[final]):
        sources = (parent, middle_ob, final_ob)
        lo = max(ob.low for ob in sources)
        hi = min(ob.high for ob in sources)
        if lo >= hi:
            continue
        hit = first_contact(lo, hi, known, price, now, minutes, since)
        if hit['hit']:
            contacts.append({'level': 1, 'phase': 'first_contact', 'touch': hit, 'sources': [asdict(ob) for ob in sources],
                             'band': [lo, hi], 'score': 3,
                             'formation': {'start': parent.origin, 'end': parent.created}})
    return contacts


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
         formation_tracks=(), origin_tracks=(), execution_quote=None, tick_size=None, upper_atr=False):
    closed_minutes = tuple(b for b in minute_bars if b.timestamp + MINUTE <= now)
    recent_minutes = tuple(b for b in closed_minutes if b.timestamp >= touch_since)
    scales = {'scales': UPPER_ATR_SCALES} if upper_atr else {}
    result, usable, zones = prepare(symbol, frames, now, price, tracks, build_overlaps=False, **scales)
    result['ruleset'] = RULESET
    previous = contexts or {}
    updated = {}
    for track, state in result['tracks'].items():
        top, middle = state['pair']
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
            direction, direction_at = confirmed_direction(upper_bars, points, top)
            view['structure'] = 'up' if direction == 1 else 'down' if direction == -1 else 'mixed'
            if upper_atr:
                multiplier = UPPER_ATR_SCALES[lens]['atr_multiple']
                state.setdefault('atr_directions', {})[multiplier] = direction
            if lens == 'recent':
                state['direction'] = direction
            if not direction:
                continue
            upper_stairs = {s['origin']: s for s in staircases(upper_bars, points, top, direction)}
            for parent in zones:
                if parent.timeframe != top or parent.kind != 'OB' or parent.direction != direction:
                    continue
                if anchored and parent.origin < max(usable[middle][0].timestamp, lower_bars[0].timestamp):
                    continue
                arm_probe = first_contact(parent.low, parent.high, parent.created, price, now, recent_minutes, touch_since)
                if not arm_probe['hit'] and not any(e['parent_id'] == parent.id and e['track'] == track
                                                   and e['lens'] == lens for e in previous.values()):
                    continue
                pivot = formation_pivot(parent, upper_bars, points, now) if require_origin else None
                if require_origin and pivot is None:
                    continue
                area = rooted_context(parent, upper_bars, points, view['areas'], top, now)
                if area is None and not anchored:
                    continue
                root = pivot['timestamp'] if pivot else area['pivot_origin'] if area else parent.origin
                if pivot and area and area['pivot_origin'] != root:
                    area = None
                scope = 'formation-pivot' if pivot else 'formation-ob' if anchored else root
                if upper_atr:
                    scope = f'atr:{scope}'
                identity = f'{track}|{lens}|{parent.id}|{scope}'
                upper = upper_stairs.get(root) if area or pivot else None
                protected = parent.stop
                if pivot:
                    protected = pivot['price']
                elif area:
                    root_bar = next(b for b in upper_bars if b.timestamp == root)
                    protected = root_bar.low if direction == 1 else root_bar.high
                origin_known = pivot['confirmed_at'] if pivot else area['created'] if area else None
                if origin_known is not None and any(b.timestamp >= origin_known and direction * (b.close - protected) < 0
                                                     for b in upper_bars):
                    continue
                setup_known = max(direction_at, pivot['confirmed_at']) if pivot else direction_at
                if direction * (price - protected) < 0 or direction * (price - parent.stop) < 0:
                    if identity in previous and previous[identity]['arm']['at'] >= setup_known:
                        updated[identity] = previous[identity]
                    continue
                upper_ready = upper and not (
                    any(s['invalidated_at'] for s in upper_stairs.values() if s['origin'] >= root)
                    or any(b.timestamp >= upper['known_at']
                           and direction * ((b.high if direction == 1 else b.low) - upper['target']) >= 0
                           for b in upper_bars))
                if not anchored and upper and not upper_ready:
                    continue
                episode = previous.get(identity)
                if episode is None or episode['arm']['at'] < setup_known:
                    ready_at = parent.created if anchored else area['created']
                    arm = first_contact(parent.low, parent.high, max(ready_at, parent.created, setup_known),
                                        price, now, recent_minutes, touch_since)
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
                                                 price, now, recent_minutes, touch_since)
                else:
                    choices = overlap_contacts(parent, area, lower_obs, zones, (top, middle), arm,
                                               price, now, recent_minutes, touch_since)
                if prior_overlap is None and choices:
                    first_overlap = min(choices, key=lambda c: (c['touch']['at'], -c['score']))
                    episode = {**episode, 'overlap': first_overlap}
                    if anchored:
                        choices = [first_overlap]
                elif anchored:
                    choices = [c for c in choices if c['sources'] == prior_overlap['sources']]
                prior_reaction = episode.get('reaction')
                if anchored and episode['overlap'] and prior_reaction is None:
                    waiting = episode.get('pending_reaction')
                    reaction = waiting or formation_reaction(episode['overlap'], lower_bars, lower_points, final, direction)
                    if reaction:
                        episode = dict(episode)
                        if prior_overlap is None:
                            episode['pending_reaction'] = reaction
                        else:
                            episode.pop('pending_reaction', None)
                            episode['reaction'] = reaction
                            if waiting or reaction['at'] > touch_since:
                                choices.append({**prior_overlap, 'level': 2, 'phase': 'reaction', 'reaction': reaction})
                updated[identity] = episode
                for ob in (() if anchored else lower_obs):
                    hit = first_contact(ob.low, ob.high, max(ob.created, arm['at']), price, now, recent_minutes, touch_since)
                    if hit['hit']:
                        choices.append({'level': 1, 'touch': hit, 'sources': [asdict(parent), asdict(ob)],
                                        'band': [ob.low, ob.high], 'score': 2})
                step = lower_stairs[direction]
                latest_upper = next(reversed(upper_stairs.values()), None)
                followup = prior_overlap if anchored else episode['overlap']
                reaction_at = followup['touch']['at'] if anchored and followup else arm['at']
                if (upper_ready and followup and (not anchored or prior_reaction) and step and not step['invalidated_at']
                        and direction * (price - latest_upper['protected']) >= 0
                        and step['origin'] >= max(upper['end'], frame_open(reaction_at - 1, final))
                        and step['known_at'] > reaction_at and direction * (price - step['protected']) > 0
                        and not opposing_upper_contact({'direction': direction, 'pair': state['pair'], 'trade_setup': {'upper': upper}},
                                                       usable, zones, now, price, minute_bars)):
                    stop, target = step['protected'], upper['target']
                    for ob in lower_obs:
                        if not step['origin'] - 2 * TF_MS[final] <= ob.origin <= step['end']:
                            continue
                        lo, hi = risk_band(ob.low, ob.high, stop, target, direction)
                        if lo >= hi:
                            continue
                        entry = hi if direction == 1 else lo
                        rr = net_rr(entry, stop, target, direction)
                        if rr < 3 - 1e-9:
                            continue
                        known = max(upper['known_at'], step['known_at'], ob.created, episode['overlap']['touch']['at'],
                                    prior_reaction['at'] if prior_reaction else 0)
                        hit = first_contact(lo, hi, known, price, now, recent_minutes, touch_since)
                        if not hit['hit']:
                            continue
                        plan = {'band': [lo, hi], 'entry': entry, 'rr': rr}
                        if execution_quote is not None:
                            quote = execution_quote()
                            if direction == 1:
                                permitted = [max(ob.low, protected, parent.stop, latest_upper['protected']), ob.high]
                            else:
                                permitted = [ob.low, min(ob.high, protected, parent.stop, latest_upper['protected'])]
                            plan = entry_plan(*permitted, stop, target, direction, quote, tick_size)
                            if plan is None:
                                continue
                            plan['structural_band'] = permitted
                            hit = {'hit': True, 'at': now, 'valid_from': known, 'observed_price': quote['mid'],
                                   'evidence': 'live_orderbook_mid'}
                        choices.append({'level': 3, 'touch': hit, 'sources': [asdict(parent), asdict(ob)],
                                        'score': episode['overlap']['score'], 'lower': step, 'stop': stop,
                                        'target': target, 'overlap': episode['overlap'], **plan})
                if choices:
                    best = max(choices, key=lambda c: (c['level'], c['score'], c.get('rr', 0)))
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
                    if best['level'] < 3 and best.get('phase') != 'first_contact':
                        target_points = recent_upper_points if best['level'] == 2 else points
                        reference = observation_reference(best, lower_bars, target_points, direction,
                                                          lower_points=lower_points, tick_size=tick_size,
                                                          minute_bars=closed_minutes, price=price)
                        if reference:
                            quote = execution_quote() if execution_quote else None
                            plan = entry_plan(*best['band'], reference['stop'], reference['target'], direction,
                                              quote, tick_size, require_touch=False)
                            best['observation_risk'] = {**reference, 'plan': plan, 'costs_checked': bool(quote and tick_size)}
                    found.append({**best, 'symbol': symbol, 'exchange': 'bitget', 'track': track, 'path': state['path'],
                                  'pair': state['pair'], 'refinement_tf': final, 'direction': direction, 'price': price,
                                  'now': now, 'id': hashlib.sha256(identity.encode()).hexdigest()[:24],
                                  'context_id': identity, 'lenses': [lens],
                                  'parent': asdict(parent), 'arm': arm['at'], 'arm_price': contact_price(arm, parent.low, parent.high),
                                  'upper': upper_wave, 'source_ids': [z['id'] for z in sources],
                                  'low': best['band'][0], 'high': best['band'][1], 'alert_policy': POLICY, 'ruleset': RULESET,
                                  'context_price_breach': False, 'equilibrium': {'eligible': True}})
        state.update(candidates=found, candidate=None, status='watching' if found else 'no_star_contact')
    result['star_contexts'] = updated
    return result


def alert_title(candidate):
    if candidate.get('phase') == 'reaction':
        return '하위 구조 반응 확인'
    if candidate.get('formation'):
        return '3중 OB 첫 접촉'
    if candidate['level'] == 1:
        return f"{LABELS[candidate['refinement_tf']]} OB 관찰"
    return TITLES[candidate['level']]


def unit_text(candidate):
    c = candidate
    level, side = c['level'], 'LONG' if c['direction'] == 1 else 'SHORT'
    label = f"{LABELS[c['pair'][0]]} {TRACK_LABELS[c['track']]}"
    title = alert_title(c)
    exchange = c.get('exchange', 'bitget').title()
    lines = [f"[{exchange} · {c['symbol']}] {side}", f"{'★' * level} {title} · {label}"]
    if c.get('upper_atr') is not None:
        directions = c.get('atr_directions', {})
        matched = c.get('matched_atrs', [c['upper_atr']])
        checks = ' · '.join(f"{atr_label(n)} {'↑' if d == 1 else '↓' if d == -1 else '—'}"
                            for n, d in sorted((float(n), d) for n, d in directions.items()))
        lines.append(f"상위 {LABELS[c['pair'][0]]} · 신호 " + ', '.join(map(atr_label, matched)))
        if checks:
            lines.append(checks)
    if level >= 2 or c.get('formation'):
        evidence = c['overlap']['sources'] if level == 3 else c['sources']
        names = sorted({(z['timeframe'], z['kind']) for z in evidence}, key=lambda key: (-TF_MS[key[0]], key[1]))
        lines.append(' + '.join(f'{LABELS[tf]} {kind}' for tf, kind in names))
        if c.get('reaction'):
            r = c['reaction']
            lines.append(f"{LABELS[c['refinement_tf']]} 종가 {price_text(r['price'])} · 돌파 기준 {price_text(r['level'])}")
        elif c.get('formation'):
            lo, hi = c['band']
            lines.append(f'공통 구간 {price_text(lo)} ~ {price_text(hi)} · 계단은 이후 확인')
    if level == 3:
        edge = '상한' if c['direction'] == 1 else '하한'
        lo, hi = c['band']
        lines.append(f"진입 구간 {price_text(lo)} ~ {price_text(hi)} · {edge} {price_text(c['entry'])}")
        lines.append(f"손절 {price_text(c['stop'])} · {LABELS[c['pair'][0]]} 목표 {price_text(c['target'])}")
        lines.append(f"상위·하위 계단 · 비용 반영 1:{c['rr']:.2f}")
        if x := c.get('execution'):
            lines.append(f"{x['notional']:,.0f} USDT 기준 · 편도 수수료 {x['fee_per_side']:.3%} · 예상 슬리피지 {x['slippage_per_side']:.3%}")
            lines.append(f"호가 기준 예상 체결가 {price_text(x['vwap'])} · {kst(x['at'])}")
    elif risk := c.get('observation_risk'):
        target_kind = '고점' if c['direction'] == 1 else '저점'
        stop_kind = '구조' if 'pivot_at' in risk else 'OB'
        lines.append(f"{LABELS[c['refinement_tf']]} {stop_kind} 손절 {price_text(risk['stop'])} · {LABELS[c['pair'][0]]} 직전 {target_kind} {price_text(risk['target'])}")
        if plan := risk['plan']:
            lo, hi = plan['band']
            lines.append(f"참고 진입 {price_text(lo)} ~ {price_text(hi)} · 비용 반영 1:{plan['rr']:.2f}")
            if level == 1:
                x = plan['execution']
                lines.append(f"{x['notional']:,.0f} USDT 기준 · 편도 수수료 {x['fee_per_side']:.3%} · 예상 슬리피지 {x['slippage_per_side']:.3%}")
        elif level == 2:
            lines.append('공통 구간 내 비용 반영 1:3 미충족' if risk.get('costs_checked') else '비용 확인 대기 · 1:3 미확정')
    elif level == 2:
        lines.append('하위 구조 손절 또는 상위 목표 미확정')
    if level != 2 and not (level == 3 and c.get('execution')):
        lines.append(f"접촉 확인 · {kst(c['touch']['at'])}")
    if warnings := c.get('cross_track_warnings'):
        lines.append('⚠ ' + ' / '.join(warnings))
    return '\n'.join(lines)


def price_text(value):
    return f'{value:,.8f}'.rstrip('0').rstrip('.')
