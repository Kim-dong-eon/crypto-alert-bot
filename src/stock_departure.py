"""Reference OBs from Nasdaq-listed and NYSE-listed US cash equities."""
from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict, replace
from datetime import datetime, time, timedelta
from pathlib import Path
import json
import math
from zoneinfo import ZoneInfo

import requests

from src.ict_engine import Bar, TF_MS, frame_open
from src.ict_overlap import LABELS, Registry, kst
from src.overlap_entry import MINUTE
from src.pullback_alerts import TOP_COUNTS
from src.star_alerts import UPPER_ATR_SCALES, atr_label, confirmed_direction, formation_pivot, observation_wave
from src.structure_review import turns


US_EQUITIES = frozenset(('NVDA', 'TSLA', 'AAPL', 'MSFT', 'AMZN', 'GOOGL', 'META', 'QQQ', 'SPY',
                         'V', 'LLY', 'WEN', 'MCD', 'TMF', 'ADBE', 'EWZ', 'MRK'))
US_ETFS = frozenset(('QQQ', 'SPY', 'TMF', 'EWZ'))
NEW_YORK = ZoneInfo('America/New_York')


def cash_close(stamp, tf):
    date = datetime.fromtimestamp(stamp / 1000, NEW_YORK).date()
    if tf == '1w':
        date += timedelta(days=(4 - date.weekday()) % 7)
    return int(datetime.combine(date, time(16), NEW_YORK).timestamp() * 1000)


def stock_zones(rows, now=None):
    frames = {'1d': [], '1w': []}
    weeks = defaultdict(list)
    for row in rows:
        date = datetime.strptime(row['date'], '%m/%d/%Y').date()
        closed = int(datetime.combine(date, time(16), NEW_YORK).timestamp() * 1000)
        if now is not None and closed > now:
            continue
        stamp = int(datetime.combine(date, time(), NEW_YORK).timestamp() * 1000)
        o, h, l, c = (float(row[key].replace('$', '').replace(',', ''))
                      for key in ('open', 'high', 'low', 'close'))
        volume = row['volume'].replace(',', '')
        v = 0. if volume == 'N/A' else float(volume)
        if not all(map(math.isfinite, (o, h, l, c, v))) or not 0 < l <= min(o,c) <= max(o,c) <= h or v < 0:
            raise ValueError('Invalid stock OHLC')
        frames['1d'].append(Bar(stamp, o, h, l, c, v))
    frames['1d'].sort(key=lambda b: b.timestamp)
    if any(a.timestamp >= b.timestamp for a, b in zip(frames['1d'], frames['1d'][1:])):
        raise ValueError('Duplicate stock session')
    for bar in frames['1d']:
        weeks[frame_open(bar.timestamp, '1w')].append(bar)
    for stamp, bars in weeks.items():
        if now is None or cash_close(stamp, '1w') > now:
            continue
        frames['1w'].append(Bar(stamp, bars[0].open, max(b.high for b in bars),
                                min(b.low for b in bars), bars[-1].close, sum(b.volume for b in bars)))
    registry = Registry(frames)
    for tf, bars in frames.items():
        for bar in bars:
            registry.step(tf, bar)
    zones = [replace(z, created=cash_close(z.created - TF_MS[z.timeframe], z.timeframe))
             for z in registry.zones.values() if z.kind == 'OB']
    return frames, zones


class NasdaqHistory:
    def __init__(self, cache):
        self.cache = Path(cache) / 'stock-history'
        self.cache.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json',
                                     'Referer': 'https://www.nasdaq.com/'})

    def rows(self, ticker, now):
        local = datetime.fromtimestamp(now / 1000, NEW_YORK)
        through = (local - timedelta(days=local.hour < 17)).date()
        path = self.cache / f'{ticker}.json'
        if path.is_file():
            saved = json.loads(path.read_bytes())
            if saved['through'] == through.isoformat():
                return saved['rows']
        response = self.session.get(f'https://api.nasdaq.com/api/quote/{ticker}/historical',
                                    params={'assetclass':'etf' if ticker in US_ETFS else 'stocks',
                                            'fromdate':through-timedelta(days=1826),
                                            'todate':through, 'limit':2000}, timeout=5)
        response.raise_for_status()
        payload = response.json().get('data') or {}
        rows = (payload.get('tradesTable') or {}).get('rows')
        if not rows:
            raise ValueError('Missing Nasdaq history')
        stock_zones(rows, now)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps({'through': through.isoformat(), 'rows': rows}), encoding='utf-8')
        temp.replace(path)
        return rows

    def load(self, ticker, now):
        return stock_zones(self.rows(ticker, now), now)


def stock_contacts(snapshot, ticker, frames, zones, minutes, since, cutoff):
    """A closed Bitget minute supplies contact evidence for a cash-market zone."""
    pairs = [(prev, bar) for prev, bar in zip(minutes, minutes[1:])
             if since <= bar.timestamp and bar.timestamp + MINUTE <= cutoff
             and prev.timestamp + MINUTE == bar.timestamp and bar.volume > 0]
    snapshot['valid_source_ids'].extend(f'cash:{ticker}:{zone.id}' for zone in zones)
    if not pairs:
        return
    # Re-evaluate on either side of a cash-market close inside the catch-up window.
    cached = {}
    for track, tf in (('long', '1w'), ('medium', '1d')):
        state = snapshot['tracks'].get(track)
        if not state or state['status'] == 'data_unavailable':
            continue
        bars = frames.get(tf, ())
        close_times = [cash_close(b.timestamp, tf) for b in bars]
        hits = {}
        for zone in sorted((z for z in zones if z.timeframe == tf), key=lambda z: z.created, reverse=True):
            if zone.direction in hits:
                continue
            for prev, bar in pairs:
                if (bar.timestamp < zone.created or zone.low <= prev.close <= zone.high
                        or bar.low > zone.high or bar.high < zone.low):
                    continue
                stamp = bar.timestamp
                end = bisect_right(close_times, stamp)
                if not end:
                    continue
                start = max(0, end - TOP_COUNTS[tf])
                closed = bars[start:end]
                key = tf, end
                if key not in cached:
                    times = close_times[start:end]
                    views = {}
                    for policy in UPPER_ATR_SCALES.values():
                        multiplier = policy['atr_multiple']
                        points = turns(closed, multiplier, policy['min_fraction'], tf)
                        points = [{**p, 'confirmed_at':times[p['confirmed_index']]} for p in points]
                        direction, known_at = confirmed_direction(closed, points, tf, close_times=times)
                        views[multiplier] = direction, known_at, points
                    cached[key] = views
                views = cached[key]
                matches = {}
                for multiplier, (direction, known_at, points) in views.items():
                    if direction != zone.direction or known_at > stamp:
                        continue
                    pivot = formation_pivot(zone, closed, points, stamp)
                    if (pivot and not any(b.timestamp >= pivot['confirmed_at']
                                         and direction * (b.close - pivot['price']) < 0 for b in closed)
                            and all(direction * (snapshot.get('price', bar.close) - bound) >= 0
                                    for bound in (pivot['price'], zone.stop))):
                        matches[multiplier] = pivot, points
                if not matches:
                    continue
                selected = min(matches)
                pivot, points = matches[selected]
                upper = observation_wave(closed, points, stamp, zone.origin)
                chart_start = min(upper['chart_start'], zone.origin) - 7 * TF_MS[tf]
                parent = asdict(zone)
                parent['id'] = f'cash:{ticker}:{zone.id}'
                hits[zone.direction] = {
                    'id':parent['id'], 'context_id':parent['id'], 'symbol':snapshot['symbol'],
                    'level':1, 'score':1, 'track':track, 'pair':state['pair'], 'direction':zone.direction,
                    'lenses':['stock'], 'parent':parent, 'source_ids':[parent['id']], 'sources':[parent],
                    'upper':upper, 'departure_pivot':pivot, 'stock_atr':selected,
                    'stock_matched_atrs':list(matches),
                    'stock_bars':[asdict(b) for b in closed if b.timestamp >= chart_start],
                    'path':state['path'], 'arm':bar.timestamp+MINUTE, 'arm_price':min(zone.high, max(zone.low, bar.close)),
                    'low':zone.low, 'high':zone.high, 'band':[zone.low,zone.high],
                    'touch':{'at':bar.timestamp+MINUTE, 'bar_open':bar.timestamp, 'observed_low':bar.low,
                             'observed_high':bar.high},
                    'now':snapshot['as_of'], 'stock_reference':ticker, 'first_touch_text':True,
                    'context_price_breach':False, 'equilibrium':{'eligible':True}, 'ruleset':snapshot['ruleset'],
                }
                break
        state['candidates'].extend(hits.values())
        if hits:
            state['status'] = 'watching'


def stock_text(c):
    side = '지지' if c['direction'] == 1 else '저항'
    return (f"[{c['symbol']}] {LABELS[c['pair'][0]]} 원주식 OB 접촉\n"
            f"{c['stock_reference']} {side} {c['low']:g}~{c['high']:g} USD\n"
            f"원주식 구조 · {', '.join(map(atr_label, c.get('stock_matched_atrs', ())))}\n"
            f"Bitget 접촉 확인 · {kst(c['touch']['at'])}")
