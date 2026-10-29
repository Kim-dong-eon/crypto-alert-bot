"""Anonymous Bitget market GETs only, with per-frame closed-data caching."""
import json
import math
from pathlib import Path
import time
import requests

from config.overlap_settings import BASES, HISTORY_BARS
from src.ict_engine import TF_MS, frame_open, closed_bars

INTERVALS = {"15m": "15m", "1h": "1H", "4h": "4H", "1d": "1Dutc", "1w": "1Wutc"}


def select_instruments(rows, bases=BASES):
    chosen = []
    for base in bases:
        matches = [r for r in rows if r.get("baseCoin") == base and r.get("category") == "USDT-FUTURES" and r.get("quoteCoin") == "USDT" and r.get("status") == "online" and r.get("type") == "perpetual"]
        matches = [r for r in matches if (base == "BTC" and r.get("symbolType") == "crypto") or
                   (base != "BTC" and r.get("isRwa") == "YES" and
                    ((r.get("symbolType") == "stock" and r.get("instrumentType") in ("stock", "etf", "index")) or
                     (r.get("symbolType") == "commodity" and r.get("instrumentType") == "energy") or
                     (r.get("symbolType") == "metal" and r.get("instrumentType") == "metal")))]
        if matches:
            chosen.append(matches[0])
    return chosen


class PublicMarket:
    def __init__(self, cache, history=HISTORY_BARS):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.history = history
        self.session = requests.Session()
        self.session.trust_env = False
        self.last_request = 0.

    def get(self, path, **params):
        # <=5 requests/second; no account endpoints or credential headers.
        time.sleep(max(0., .2-(time.monotonic()-self.last_request)))
        self.last_request = time.monotonic()
        response = self.session.get("https://api.bitget.com/api/v3/market/"+path, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        if data.get("code") != "00000":
            raise ValueError("Public market API returned non-success")
        return data

    def instruments(self):
        result = self.get("instruments", category="USDT-FUTURES")
        (self.cache/"instruments.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result["data"]

    def tickers(self):
        result = self.get("tickers", category="USDT-FUTURES")
        stamp = int(result["requestTime"])
        now = int(time.time()*1000)
        if not -5000 <= now-stamp <= 120000:
            raise ValueError("Delayed market response")
        return {r["symbol"]: r for r in result["data"]}, stamp

    def frame(self, symbol, tf, now_ms):
        path = self.cache/f"{symbol}_{tf}.json"
        expected = frame_open(now_ms, tf)
        if path.exists():
            previous = json.loads(path.read_text(encoding="utf-8"))
            # Never promote a cached, once-open candle into a confirmed one
            # just because wall time advanced since its partial snapshot.
            bars = closed_bars(previous["data"], tf, min(now_ms,previous["fetched_at"]))
            if bars and bars[-1].timestamp + TF_MS[tf] == expected:
                return bars
        result = self.get("candles", category="USDT-FUTURES", symbol=symbol, interval=INTERVALS[tf], limit=str(self.history))
        rows = sorted(([int(r[0])]+[float(x) for x in r[1:6]] for r in result["data"]), key=lambda r:r[0])
        if path.exists():
            previous=json.loads(path.read_text(encoding="utf-8"))
            prior_closed=closed_bars(previous["data"],tf,min(now_ms,previous["fetched_at"]))
            merged={b.timestamp:[b.timestamp,b.open,b.high,b.low,b.close,b.volume] for b in prior_closed}
            merged.update({int(r[0]):r for r in rows})
            rows=[merged[key] for key in sorted(merged)][-10000:]
        bars = closed_bars(rows, tf, now_ms)
        if not bars or bars[-1].timestamp+TF_MS[tf] != expected:
            raise ValueError("Latest closed candle missing; pause this timeframe")
        closed_rows=[[b.timestamp,b.open,b.high,b.low,b.close,b.volume] for b in bars]
        path.write_text(json.dumps({"fetched_at":now_ms,"data":closed_rows}),encoding="utf-8")
        return bars

    def daily_turnovers(self, symbol, now_ms):
        """Actual USDT quote turnover, kept separately from base-volume bars."""
        path=self.cache/f'{symbol}_daily_turnover.json'
        expected=frame_open(now_ms,'1d')
        if path.exists():
            try:
                saved=json.loads(path.read_text(encoding='utf-8'))['data']
                if (len(saved)>=30 and saved[-1][0]+TF_MS['1d']==expected
                        and all(b[0]-a[0]==TF_MS['1d'] for a,b in zip(saved,saved[1:]))
                        and all(math.isfinite(r[1]) and r[1]>=0 for r in saved)):
                    return saved[-30:]
            except (ValueError,KeyError,TypeError,IndexError):
                pass
        result=self.get('candles',category='USDT-FUTURES',symbol=symbol,interval='1Dutc',limit='31')
        rows=sorted([[int(r[0]),float(r[6])] for r in result['data'] if int(r[0])+TF_MS['1d']<=now_ms])[-30:]
        if (len(rows)!=30 or rows[-1][0]+TF_MS['1d']!=expected
                or any(b[0]-a[0]!=TF_MS['1d'] for a,b in zip(rows,rows[1:]))
                or any(not math.isfinite(r[1]) or r[1]<0 for r in rows)):
            raise ValueError('Incomplete daily quote turnover history')
        path.write_text(json.dumps({'fetched_at':now_ms,'data':rows}),encoding='utf-8')
        return rows


def quote_price(ticker, now_ms):
    price = float(ticker["lastPrice"])
    stamp = int(ticker["ts"])
    if not math.isfinite(price) or price <= 0 or not -5000 <= now_ms-stamp <= 120000:
        raise ValueError("Invalid or stale ticker")
    return price
