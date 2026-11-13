"""BTC and common TradFi contracts, including low-BTC-correlation picks."""
TRACKS = {"short": ("4h", "1h"), "medium": ("1d", "4h"),
          "long": ("1w", "1d")}
TRACK_LABELS = {"fast":"초단기", "short": "단기", "medium": "중기", "long": "장기", "auxiliary": "4H·1H 보조"}
# 2026-11-12 Bitget screen: 24h and median 30d quote turnover >= $5M,
# with absolute BTC return correlation <= 0.30 on 4h/1d over both 30d and 60d.
CRYPTO_BASES = ("BTC", "ETH", "JCT", "AKE", "BTW", "Q")
BASES = CRYPTO_BASES + ("NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "QQQ", "SPY")
DIVERSIFIER_BASES = ("CL", "BZ")
BASES += DIVERSIFIER_BASES
# Fixed additions from the 2026-11-10 BTC correlation screen (max |r| <= 0.20).
BASES += ("BSP", "V", "LLY", "WEN", "MCD", "POPMART", "UNITREE", "TMF", "ADBE", "MEITUAN", "EWZ", "MRK")
HISTORY_BARS = 400
POLL_SECONDS = 60
MIN_HISTORY_BARS = 8
# A rolling local dealing range, rather than every small pivot or all-time highs.
EXTERNAL_LIQUIDITY_BARS = 20

# Shared live rules for exchange and cash-reference structures.
MULTIBAR_OB_FRAMES = ("5m", "15m", "1h", "4h", "1d", "1w")
ALLOW_MIDDLE_FVG = True
REQUIRE_ENTRY_OTE = True
