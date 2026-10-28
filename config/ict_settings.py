"""Explicit implementation choices for the lecture-based ICT monitor."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ICTSettings:
    exchange: str = "bitget"
    symbols: tuple = ("BTC/USDT:USDT", "ETH/USDT:USDT", "XRP/USDT:USDT", "SOL/USDT:USDT")
    high_tf: str = "4h"
    low_tf: str = "15m"
    history_bars: int = 700
    sweep_wait_bars: int = 8
    setup_lifetime_bars: int = 16
    touch_wait_bars: int = 3
    ob_lookback_bars: int = 8
    reward_risk_ratio: float = 3.0
    ote_long_low: float = 0.21
    ote_long_mid: float = 0.295
    ote_long_high: float = 0.38
    require_htf_overlap: bool = True
    max_notice_age_seconds: int = 120
    poll_seconds: int = 60
    # Estimated per-side costs, not the user's confirmed fee tier.
    fee_rate: float = 0.0006
    slippage_rate: float = 0.0001

    def __post_init__(self):
        if (self.high_tf, self.low_tf) != ("4h", "15m"):
            raise ValueError("This implementation supports 4h/15m")
        if not 50 <= self.history_bars <= 1000:
            raise ValueError("history_bars must be between 50 and 1000")
        if min(self.sweep_wait_bars, self.setup_lifetime_bars, self.touch_wait_bars,
               self.ob_lookback_bars, self.max_notice_age_seconds, self.poll_seconds) <= 0:
            raise ValueError("Timing limits must be positive")
        if self.reward_risk_ratio <= 0 or min(self.fee_rate, self.slippage_rate) < 0:
            raise ValueError("Risk and cost settings must be nonnegative")
        if not 0 < self.ote_long_low < self.ote_long_mid < self.ote_long_high < 0.5:
            raise ValueError("Invalid OTE retracement band")


DEFAULTS = ICTSettings()
