"""Exact USDT perpetual intersection; Binance metadata never supplies prices."""
import json
from pathlib import Path
import time
import requests

SOURCE = 'https://fapi.binance.com/fapi/v1/exchangeInfo'
FIELDS = ('symbol','baseAsset','quoteAsset','status','contractType','underlyingType')


def select_common_instruments(bitget_instruments, binance_rows):
    allowed = {}
    for row in binance_rows:
        if (row.get('status') == 'TRADING' and row.get('quoteAsset') == 'USDT'
                and row.get('contractType') in ('PERPETUAL','TRADIFI_PERPETUAL')):
            allowed[row.get('symbol')] = row
    common = []
    for instrument in bitget_instruments:
        row = allowed.get(instrument['symbol'])
        if not row or row.get('baseAsset') != instrument.get('baseCoin'):
            continue
        crypto = instrument.get('baseCoin') == 'BTC' and row.get('underlyingType') == 'COIN'
        equity = instrument.get('instrumentType') in ('stock','etf') and row.get('underlyingType') in ('EQUITY','KR_EQUITY')
        index = instrument.get('instrumentType') == 'index' and row.get('underlyingType') == 'INDEX'
        if crypto or equity or index:
            common.append(instrument)
    return common


def listing_snapshot(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    now = int(time.time()*1000)
    if (data.get('source') != SOURCE or not isinstance(data.get('symbols'),list)
            or not isinstance(data.get('verified_at'),int) or not 0 < data['verified_at'] <= now+5000):
        raise ValueError('Invalid official Binance listing snapshot')
    return data


def binance_listings(snapshot_path, cache_path):
    """Try the official endpoint once. On restricted hosts use dated evidence.

    A cached/saved catalog is explicitly labelled, never called a live check.
    Missing/malformed saved evidence does not broaden the instrument list.
    """
    session = requests.Session()
    session.trust_env = False
    fallback_reason = None
    try:
        response = session.get(SOURCE, timeout=8)
        response.raise_for_status()
        payload = response.json()
        rows = payload['symbols']
        if not isinstance(rows,list):
            raise ValueError('Malformed Binance catalog')
        data = {'source':SOURCE,'verified_at':int(time.time()*1000),
                'symbols':[{k:row.get(k) for k in FIELDS} for row in rows]}
        path = Path(cache_path)
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        return rows, {'mode':'live_official','source':SOURCE,'verified_at':data['verified_at']}
    except requests.RequestException as error:
        fallback_reason = f"HTTP {error.response.status_code}" if error.response is not None else type(error).__name__
    except (ValueError,KeyError,TypeError):
        fallback_reason = 'invalid_official_response'
    saved = [listing_snapshot(snapshot_path)]
    if Path(cache_path).exists():
        try:
            saved.append(listing_snapshot(cache_path))
        except (ValueError,KeyError,TypeError,OSError):
            pass
    data = max(saved,key=lambda item:item['verified_at'])
    return data['symbols'], {'mode':'saved_official_snapshot','source':SOURCE,
                             'verified_at':data['verified_at'],'live_check_unavailable':fallback_reason}
