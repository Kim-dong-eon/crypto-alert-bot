"""Reference OBs from Nasdaq-listed and NYSE-listed US cash equities."""
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, time, timedelta
from pathlib import Path
import json
import math
from zoneinfo import ZoneInfo

import requests

from src.ict_engine import Bar, frame_open
from src.ict_overlap import LABELS, Registry, kst
from src.overlap_entry import MINUTE


US_EQUITIES = frozenset(('NVDA', 'TSLA', 'AAPL', 'MSFT', 'AMZN', 'GOOGL', 'META', 'QQQ', 'SPY',
                         'V', 'LLY', 'WEN', 'MCD', 'TMF', 'ADBE', 'EWZ', 'MRK'))
US_ETFS = frozenset(('QQQ', 'SPY', 'TMF', 'EWZ'))
NEW_YORK = ZoneInfo('America/New_York')


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
        friday = datetime.fromtimestamp(bars[0].timestamp / 1000, NEW_YORK).date()
        friday += timedelta(days=4-friday.weekday())
        if now is None or datetime.combine(friday, time(16), NEW_YORK).timestamp() * 1000 > now:
            continue
        frames['1w'].append(Bar(stamp, bars[0].open, max(b.high for b in bars),
                                min(b.low for b in bars), bars[-1].close, sum(b.volume for b in bars)))
    registry = Registry(frames)
    for tf, bars in frames.items():
        for bar in bars:
            registry.step(tf, bar)
    return frames, [zone for zone in registry.zones.values() if zone.kind == 'OB']


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


def stock_contacts(snapshot, ticker, zones, minutes, since, cutoff):
    """A closed Bitget minute supplies contact evidence for a cash-market zone."""
    pairs = [(prev, bar) for prev, bar in zip(minutes, minutes[1:])
             if since <= bar.timestamp and bar.timestamp + MINUTE <= cutoff
             and prev.timestamp + MINUTE == bar.timestamp and bar.volume > 0]
    for track, tf in (('long', '1w'), ('medium', '1d')):
        state = snapshot['tracks'].get(track)
        if not state or state['status'] == 'data_unavailable':
            continue
        hits = {}
        for zone in sorted((z for z in zones if z.timeframe == tf), key=lambda z: z.created, reverse=True):
            if zone.direction in hits:
                continue
            for prev, bar in pairs:
                if (bar.timestamp < zone.created or zone.low <= prev.close <= zone.high
                        or bar.low > zone.high or bar.high < zone.low):
                    continue
                parent = asdict(zone)
                parent['id'] = f'cash:{ticker}:{zone.id}'
                hits[zone.direction] = {
                    'id':parent['id'], 'context_id':parent['id'], 'symbol':snapshot['symbol'],
                    'level':1, 'score':1, 'track':track, 'pair':state['pair'], 'direction':zone.direction,
                    'lenses':['stock'], 'parent':parent, 'source_ids':[parent['id']], 'sources':[parent],
                    'upper':{'chart_start': zone.origin, 'points':[], 'target':None},
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
    snapshot['valid_source_ids'].extend(f'cash:{ticker}:{zone.id}' for zone in zones)


def stock_text(c):
    side = '지지' if c['direction'] == 1 else '저항'
    return (f"[{c['symbol']}] {LABELS[c['pair'][0]]} 원주식 OB 접촉\n"
            f"{c['stock_reference']} {side} {c['low']:g}~{c['high']:g} USD\n"
            f"Bitget 접촉 확인 · {kst(c['touch']['at'])}")
