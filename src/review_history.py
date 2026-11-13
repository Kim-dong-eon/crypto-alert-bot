"""Bounded public history backfill, used by review and production scanners."""
import json
from src.ict_engine import Bar, TF_MS, closed_bars, frame_open
from src.overlap_market import INTERVALS


def history_frame(market, symbol, tf, now_ms, count):
    if tf == '1w':
        raise ValueError('Aggregate complete UTC daily bars for weekly review')
    path = market.cache / f'{symbol}_{tf}_review.json'
    expected = frame_open(now_ms, tf)
    start = expected - count * TF_MS[tf]
    rows = {}
    exhausted=False
    if path.exists():
        old = json.loads(path.read_text(encoding='utf-8'))
        for b in closed_bars(old['data'], tf, min(now_ms, old['fetched_at'])):
            rows[b.timestamp] = [b.timestamp,b.open,b.high,b.low,b.close,b.volume]
        # Exhaustion applies only to the retained oldest timestamp. A smaller
        # request must not truncate the file and then make a wider request
        # falsely believe that the exchange has no older history.
        exhausted=old.get('history_exhausted',False)
        if (rows and max(rows)+TF_MS[tf]==expected and
                (min(rows)<=start or exhausted)):
            return closed_bars([rows[t] for t in sorted(rows) if start<=t<expected],tf,now_ms)
    fresh = market.get('candles', category='USDT-FUTURES', symbol=symbol,
                       interval=INTERVALS[tf], limit='1000')['data']
    for r in fresh:
        t = int(r[0])
        if t + TF_MS[tf] <= now_ms:
            rows[t] = [t]+[float(v) for v in r[1:6]]
    # A cache may be older than the latest endpoint's window. Fill the hole
    # between the two windows before validating or saving either one together.
    bridge_pages = 0
    while True:
        stamps = sorted(rows)
        gaps = [(a, b) for a, b in zip(stamps, stamps[1:]) if b-a > TF_MS[tf]]
        if not gaps:
            break
        left, right = gaps[-1]
        if bridge_pages >= count // 90 + 16:
            raise ValueError('History gap backfill exceeded page budget')
        batch = market.get('history-candles', category='USDT-FUTURES',
                           symbol=symbol, interval=INTERVALS[tf],
                           endTime=str(right), limit='100')['data']
        missing = [r for r in batch if left < int(r[0]) < right]
        if not missing:
            raise ValueError(f'Missing exchange history between {left} and {right}')
        for r in missing:
            t = int(r[0])
            rows[t] = [t]+[float(v) for v in r[1:6]]
        bridge_pages += 1
    pages = 0
    cursor = min(rows) if rows else expected
    def checkpoint():
        saved=[rows[t] for t in sorted(rows) if t<expected]
        closed_bars(saved,tf,now_ms)
        temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'fetched_at':now_ms,'data':saved,'history_exhausted':exhausted}),encoding='utf-8')
        temporary.replace(path)
    while cursor > start and not exhausted and pages < (count // 90 + 8):
        batch = market.get('history-candles', category='USDT-FUTURES',
                           symbol=symbol, interval=INTERVALS[tf],
                           endTime=str(cursor), limit='100')['data']
        older = [r for r in batch if int(r[0]) < cursor]
        if not older:
            exhausted=True
            break
        for r in older:
            t = int(r[0])
            rows[t] = [t]+[float(v) for v in r[1:6]]
        cursor = min(int(r[0]) for r in older)
        pages += 1
        if pages%5==0:checkpoint()
    data = [rows[t] for t in sorted(rows) if start <= t < expected]
    bars = closed_bars(data, tf, now_ms)
    if not bars or bars[-1].timestamp + TF_MS[tf] != expected:
        raise ValueError('Review requires a fresh, continuous completed history')
    checkpoint()
    return bars


def weekly_from_daily(daily, now_ms):
    groups = {}
    for b in daily:
        groups.setdefault(frame_open(b.timestamp,'1w'), []).append(b)
    result = []
    for stamp, values in sorted(groups.items()):
        if (len(values) != 7 or values[0].timestamp != stamp or
                values[-1].timestamp + TF_MS['1d'] != stamp + TF_MS['1w'] or
                stamp + TF_MS['1w'] > now_ms):
            continue
        if any(b.timestamp-a.timestamp != TF_MS['1d'] for a,b in zip(values,values[1:])):
            raise ValueError('Incomplete weekly source')
        result.append(Bar(stamp, values[0].open, max(b.high for b in values),
                          min(b.low for b in values), values[-1].close,
                          sum(b.volume for b in values)))
    return result


class HistoricalMarket:
    """PublicMarket interface; three-year weekly context without 1W API gaps."""
    def __init__(self,cache):
        from src.overlap_market import PublicMarket
        self.public=PublicMarket(cache)
        self.cache=self.public.cache

    def __getattr__(self,key):return getattr(self.public,key)

    def frame(self,symbol,tf,now_ms):
        if tf=='1w':
            return weekly_from_daily(self.frame(symbol,'1d',now_ms),now_ms)
        return history_frame(self.public,symbol,tf,now_ms,{'5m':1000,'15m':4368,'1h':4368,'4h':6624,'1d':1102}[tf])

    def quarter_frame(self,symbol,now_ms,start=None):
        return self.refinement_frame(symbol,'15m',now_ms,start)

    def refinement_frame(self,symbol,tf,now_ms,start=None):
        minimum={'5m':1000,'15m':1000,'1h':4368,'4h':6624}[tf]
        count=max(minimum,(frame_open(now_ms,tf)-start)//TF_MS[tf]+3) if start is not None else minimum
        return history_frame(self.public,symbol,tf,now_ms,count)


def formation_history_starts(frames, tracks, price, minutes=(), contexts=None, session_closes=None):
    """Fetch the children of touched or already armed parents from their actual origin."""
    from config.overlap_settings import MULTIBAR_OB_FRAMES
    from src.ict_overlap import Registry
    registry = Registry(MULTIBAR_OB_FRAMES)
    session_closes = session_closes or {}
    for tf in {path[0] for path in tracks.values()}:
        for bar in frames.get(tf, ()):
            closes = session_closes.get(tf)
            registry.step(tf, bar, closes[bar.timestamp] if closes is not None else None)
    low = min([price] + [b.low for b in minutes])
    high = max([price] + [b.high for b in minutes])
    armed = {e['parent_id'] for e in (contexts or {}).values()}
    starts = {}
    for path in tracks.values():
        for parent in registry.zones.values():
            if parent.timeframe != path[0] or parent.kind != 'OB':
                continue
            is_armed = any(key == parent.id or key.endswith(':' + parent.id) for key in armed)
            if (parent.high < low or parent.low > high) and not is_armed:
                continue
            for tf in path[1:]:
                if tf in session_closes:
                    continue
                start = parent.origin - 14 * TF_MS[tf]
                starts[tf] = min(starts.get(tf, start), start)
    return starts
