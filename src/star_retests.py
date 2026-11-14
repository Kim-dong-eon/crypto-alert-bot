"""Entry grades share one causal lower-stair OB retest; upper stairs upgrade it."""
from dataclasses import asdict

from src.entry_execution import entry_plan
from src.ict_engine import TF_MS, frame_open
from src.staircase_rules import ote_band


def target_at_contact(points, contact):
    """Freeze the already confirmed upper target when the original band is touched."""
    cutoff = contact['touch'].get('bar_open', contact['touch']['at'])
    direction = contact['sources'][0]['direction']
    kind = 'high' if direction == 1 else 'low'
    return next((dict(p) for p in reversed(points)
                 if p['kind'] == kind and p['confirmed_at'] <= cutoff), None)


def target_spent_at(overlap, target_point, direction, lower_bars, minutes, now):
    if not overlap or not target_point:
        return None
    at = overlap['touch']['at']
    target = target_point['price']
    for b in (*lower_bars, *minutes):
        if (b.timestamp >= at and b.timestamp < now and b.volume > 0
                and direction * ((b.high if direction == 1 else b.low) - target) >= 0):
            return b.timestamp
    return None


def retest_entries(parent, overlap, step, target_point, zones, lower_bars, minutes,
                   now, price, execution_quote, tick_size, *,
                   upper_ready=False, require_ote=False):
    if not overlap or not step or not target_point or execution_quote is None:
        return []
    direction, tf = parent.direction, step['timeframe']
    touch_at = overlap['touch']['at']
    touch_open = overlap['touch'].get('bar_open', touch_at)
    # The new staircase must arise from the first contact, not predate it.
    if (step['invalidated_at'] or step['known_at'] > now or step['known_at'] <= touch_at
            or step['origin'] < frame_open(touch_open, tf)):
        return []
    stop, target = step['protected'], target_point['price']
    if direction * (price - stop) <= 0 or direction * (target - price) <= 0:
        return []
    # A spent upper target or a broken lower swing cannot be resurrected by a new OB.
    if any(b.timestamp >= touch_at and direction * ((b.high if direction == 1 else b.low) - target) >= 0
           for b in lower_bars):
        return []
    if any(b.timestamp > step['origin'] and direction * ((b.low if direction == 1 else b.high) - stop) < 0
           for b in lower_bars):
        return []
    if any(b.timestamp >= touch_at and b.timestamp + 60000 <= now and b.volume > 0
           and (direction * ((b.high if direction == 1 else b.low) - target) >= 0
                or b.timestamp >= step['known_at']
                and direction * ((b.low if direction == 1 else b.high) - stop) < 0)
           for b in minutes):
        return []
    choices = []
    quote = None
    for ob in zones:
        if (ob.timeframe != tf or ob.kind != 'OB' or ob.direction != direction
                or not ob.origin <= step['origin'] < ob.created <= step['known_at']):
            continue
        low, high = ob.low, ob.high
        if require_ote:
            a, b = ote_band(step)
            low, high = max(low, a), min(high, b)
        if low >= high:
            continue
        known = max(step['known_at'], ob.created, touch_at)
        if known > now:
            continue
        if quote is None:
            quote = execution_quote()
        # Live depth may be fetched after the candle snapshot. The quote provider
        # checks freshness at receipt; never accept depth predating this setup.
        if not quote or quote['at'] < known or now - quote['at'] > 30000:
            continue
        plan = entry_plan(low, high, stop, target, direction, quote, tick_size)
        if plan is None:
            continue
        # Prefer the original confluence, but permit a later stair OB outside it.
        common_low, common_high = max(low, overlap['band'][0]), min(high, overlap['band'][1])
        common = (entry_plan(common_low, common_high, stop, target, direction, quote, tick_size)
                  if common_low <= common_high else None)
        if common:
            plan, low, high = common, common_low, common_high
        grade = 3 if upper_ready else 2
        choices.append({**plan, 'level': grade, 'phase': 'stair_retest',
                        'touch': {'hit': True, 'at': now, 'valid_from': known,
                                  'observed_price': quote['mid'], 'evidence': 'live_orderbook_mid'},
                        'sources': [asdict(parent), asdict(ob)], 'score': overlap['score'],
                        'lower': step, 'stop': stop, 'target': target, 'target_point': target_point,
                        'overlap': overlap, 'structural_band': [low, high],
                        'in_initial_overlap': bool(common), 'upper_stair_confirmed': bool(upper_ready)})
    return choices
