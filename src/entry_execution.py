"""Depth-adjusted entry bands for the currently quoted perpetual contract."""

from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import math

from src.staircase_rules import FEE_PER_SIDE, MIN_RR, SLIPPAGE_PER_SIDE, net_rr, risk_band


def fill_average(levels, quantity):
    remaining = quantity
    value = 0.
    for price, size in levels:
        filled = min(size, remaining)
        value += filled * price
        remaining -= filled
        if remaining <= 0:
            return value / quantity
    raise ValueError('insufficient_orderbook_depth')


def depth_quote(book, now, notional):
    stamp = int(book['ts'])
    if not -2000 <= now - stamp <= 30_000:
        raise ValueError('stale_orderbook')
    asks = [tuple(map(float, row)) for row in book['a']]
    bids = [tuple(map(float, row)) for row in book['b']]
    if (not asks or not bids or not math.isfinite(notional) or notional <= 0
            or any(not all(math.isfinite(v) and v > 0 for v in row) for row in asks + bids)):
        raise ValueError('invalid_orderbook')
    if (bids[0][0] >= asks[0][0] or any(a[0] > b[0] for a, b in zip(asks, asks[1:]))
            or any(a[0] < b[0] for a, b in zip(bids, bids[1:]))):
        raise ValueError('invalid_orderbook_order')
    mid = (asks[0][0] + bids[0][0]) / 2
    buy = fill_average(asks, notional / mid)
    sell = fill_average(bids, notional / mid)
    # Use the worse side of today's book for either leg and reserve an extra
    # buffer for price movement between the observation and order execution.
    slippage = max(buy / mid - 1, 1 - sell / mid) + SLIPPAGE_PER_SIDE
    return {'at': stamp, 'notional': notional, 'mid': mid, 'buy_vwap': buy, 'sell_vwap': sell,
            'fee_per_side': FEE_PER_SIDE, 'slippage_per_side': slippage}


def entry_plan(low, high, stop, target, direction, quote, tick_size, *, prior_band=None, require_touch=True):
    if not quote or not 0 <= quote['slippage_per_side'] < 1:
        return None
    mid = quote['mid']
    vwap = quote['buy_vwap'] if direction == 1 else quote['sell_vwap']
    if direction * (mid - stop) <= 0 or direction * (target - mid) <= 0:
        return None
    if require_touch and not low <= vwap <= high:
        return None
    slip = quote['slippage_per_side']
    lo, hi = risk_band(low, high, stop, target, direction, slippage=slip)
    if prior_band is not None:
        lo, hi = max(lo, prior_band[0]), min(hi, prior_band[1])
    # Exclude the stop itself even when the original OB spans the swing.
    if direction == 1:
        lo = max(lo, math.nextafter(stop, math.inf))
    else:
        hi = min(hi, math.nextafter(stop, -math.inf))
    try:
        tick = Decimal(str(tick_size))
    except InvalidOperation:
        return None
    if not tick.is_finite() or tick <= 0:
        return None
    lo = float((Decimal(str(lo)) / tick).to_integral_value(rounding=ROUND_CEILING) * tick)
    hi = float((Decimal(str(hi)) / tick).to_integral_value(rounding=ROUND_FLOOR) * tick)
    if lo > hi or (require_touch and not lo <= mid <= hi):
        return None
    entry = hi if direction == 1 else lo
    rr = net_rr(entry, stop, target, direction, slippage=slip)
    if rr < MIN_RR - 1e-9:
        return None
    return {'band': [lo, hi], 'entry': entry, 'rr': rr, 'execution': {**quote, 'vwap': vwap}}
