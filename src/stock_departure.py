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


def contract_reference(market, symbol, frames):
    """Translate USD levels using the contract's minute at the same cash close."""
    daily = frames.get('1d', ())
    if not daily:
        raise ValueError('Missing cash daily reference')
    last = daily[-1]
    closed = cash_close(last.timestamp, '1d')
    path = market.cache / 'stock-history' / f'{symbol}_basis.json'
    saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if saved.get('at') != closed:
        payload = market.get('candles', category='USDT-FUTURES', symbol=symbol,
                             interval='1m', endTime=str(closed), limit='2')['data']
        rows = [r for r in payload if int(r[0]) + MINUTE == closed and float(r[5]) > 0]
        if len(rows) != 1:
            raise ValueError('Missing synchronous contract price at cash close')
        saved = {'at': closed, 'contract_close': float(rows[0][4])}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(saved), encoding='utf-8')
        temporary.replace(path)
    ratio = saved['contract_close'] / last.close
    if not math.isfinite(ratio) or ratio <= 0:
        raise ValueError('Invalid cross-market price ratio')
    projected = {tf: [replace(b, open=b.open*ratio, high=b.high*ratio,
                              low=b.low*ratio, close=b.close*ratio) for b in bars]
                 for tf, bars in frames.items()}
    return projected, {**saved, 'cash_close': last.close, 'ratio': ratio}


def stock_contacts(snapshot, ticker, frames, zones, minutes, since, cutoff, *,
                   exchange_frames=None, contexts=None, execution_quote=None, tick_size=None, price_basis=None):
    """Run cash upper frames and original exchange child frames through the shared scanner."""
    from config.overlap_settings import MULTIBAR_OB_FRAMES, ALLOW_MIDDLE_FVG, REQUIRE_ENTRY_OTE
    from src.star_alerts import scan

    combined = dict(exchange_frames or {})
    combined.update(frames)
    closes = {tf: {b.timestamp: cash_close(b.timestamp, tf) for b in bars}
              for tf, bars in frames.items()}
    prefix = {tf: f'cash:{ticker}:' for tf in frames}
    tracks = {'medium': ('1d', '4h'), 'long': ('1w', '1d')}
    result = scan(snapshot['symbol'], combined, snapshot['as_of'], snapshot['price'],
                  tracks=tracks, formation_tracks=tuple(tracks), origin_tracks=tuple(tracks),
                  upper_atr=True, departure_ob_frames=MULTIBAR_OB_FRAMES,
                  session_closes=closes, source_prefix=prefix,
                  middle_fvg=ALLOW_MIDDLE_FVG, require_ote=REQUIRE_ENTRY_OTE,
                  minute_bars=minutes, touch_since=since, first_touch_cutoff=cutoff,
                  contexts=contexts, execution_quote=execution_quote, tick_size=tick_size)
    snapshot['valid_source_ids'].extend(result['valid_source_ids'])
    snapshot['star_contexts'].update(result['star_contexts'])
    snapshot['cash_tracks'] = result['tracks']
    for track, state in result['tracks'].items():
        target = snapshot['tracks'][track]
        for candidate in state['candidates']:
            candidate.update(stock_reference=ticker, signal_version=2, price_basis=price_basis,
                             source_labels={tf: f'Nasdaq {ticker}' for tf in frames},
                             atr_directions=state.get('atr_directions', {}),
                             stock_atr=candidate['upper_atr'],
                             stock_matched_atrs=[candidate['upper_atr']])
            top = candidate['pair'][0]
            chart_start = min(candidate['upper']['chart_start'], candidate['parent']['origin']) - 4*TF_MS[top]
            chart_end = candidate.get('reaction', {}).get('at', snapshot['as_of'])
            candidate['stock_bars'] = [asdict(b) for b in frames[top]
                                       if b.timestamp >= chart_start and closes[top][b.timestamp] <= chart_end]
        target['candidates'].extend(state['candidates'])
        if state['candidates']:
            target['status'] = 'watching'
    return result


def stock_text(candidate):
    from src.star_alerts import compact_atr_text
    text = compact_atr_text(candidate)
    return text + f"\n원주식 Nasdaq {candidate['stock_reference']} · 하위/접촉 Bitget · 가격 USDT 환산"
