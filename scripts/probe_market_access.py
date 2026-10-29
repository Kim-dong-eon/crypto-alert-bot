"""Read-only production market availability check; never uses API keys."""
import json
from datetime import datetime, timezone
from pathlib import Path
import requests


def probe(url, params=None):
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(url, params=params, timeout=25)
        try:
            data = response.json()
        except ValueError:
            data = {}
        report = {"url": response.url, "status": response.status_code}
        if not response.ok:
            report["message"] = str(data.get("msg", data.get("message", response.text[:250])))[:350]
        return report, data
    except requests.RequestException as error:
        return {"url": url, "error": type(error).__name__}, {}


def main():
    report = {"checked_at": datetime.now(timezone.utc).isoformat()}
    result, data = probe("https://fapi.binance.com/fapi/v1/exchangeInfo")
    report["binance_futures_catalog"] = result
    if result.get("status") == 200:
        bases = {"BTC", "SKHYNIX", "NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "QQQ", "SPY", "NDX100", "SP500", "NDX", "US100"}
        result["matching_symbols"] = [{k: row.get(k) for k in ("symbol", "baseAsset", "quoteAsset", "status", "contractType", "underlyingType")} for row in data.get("symbols", []) if row.get("baseAsset") in bases]
        report["binance_futures_candles"], _ = probe("https://fapi.binance.com/fapi/v1/klines", {"symbol": "BTCUSDT", "interval": "4h", "limit": 2})
    result, data = probe("https://api.bitget.com/api/v3/market/instruments", {"category": "USDT-FUTURES"})
    report["bitget_catalog"] = result
    if result.get("status") == 200:
        result["code"] = data.get("code")
        result["ndx100"] = [row for row in data.get("data", []) if row.get("symbol") == "NDX100USDT"]
    path = Path("runtime/market_access.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
