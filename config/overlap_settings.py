"""BTC only among crypto; verified common equities, ETFs and screened oil."""
TRACKS = {"short": ("4h", "1h"), "medium": ("1d", "4h"),
          "long": ("1w", "1d")}
TRACK_LABELS = {"short": "단기", "medium": "중기", "long": "장기", "auxiliary": "4H·1H 보조"}
BASES = ("BTC", "SKHYNIX", "NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "QQQ", "SPY")
BASES += ("NDX100", "SP500")
DIVERSIFIER_BASES = ("CL", "BZ")
BASES += DIVERSIFIER_BASES
HISTORY_BARS = 400
POLL_SECONDS = 60
MIN_HISTORY_BARS = 8
# A rolling local dealing range, rather than every small pivot or all-time highs.
EXTERNAL_LIQUIDITY_BARS = 20
