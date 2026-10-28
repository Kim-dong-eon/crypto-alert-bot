"""Causal, closed-bar ICT interpretation. No exchange, credentials or orders.

This is an explicit mechanical interpretation of the user's lecture transcripts,
not an assertion about actual institutional positions or a proven trading edge.
"""
from dataclasses import asdict, dataclass
import math

from config.ict_settings import DEFAULTS


TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000,
         "1d": 86_400_000, "1w": 604_800_000}
TERMINAL = {"cancelled", "target_reached"}
RULE_VERSION = "ict-ote-v2"


@dataclass(frozen=True)
class Bar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def frame_open(timestamp, timeframe):
    offset = 4 * TF_MS["1d"] if timeframe == "1w" else 0
    return (timestamp - offset) // TF_MS[timeframe] * TF_MS[timeframe] + offset


def closed_bars(rows, timeframe, now_ms):
    """Reject corrupt, unordered or gapped inputs; exclude the open last bar."""
    duration = TF_MS[timeframe]
    result = []
    previous = None
    for row in rows:
        if len(row) < 6:
            raise ValueError("OHLCV requires six columns")
        t = int(row[0])
        values = tuple(float(x) for x in row[1:6])
        o, h, l, c, v = values
        if not all(math.isfinite(x) for x in values) or min(o, h, l, c) <= 0 or v < 0:
            raise ValueError("Non-finite or invalid OHLCV")
        if l > min(o, c) or h < max(o, c) or l > h or t != frame_open(t, timeframe):
            raise ValueError("Invalid OHLC range or timestamp")
        if previous is not None and t != previous + duration:
            raise ValueError("Missing, duplicate or unordered candles")
        previous = t
        if t + duration <= now_ms:
            result.append(Bar(t, o, h, l, c, v))
    return result


@dataclass
class Swing:
    kind: str
    price: float
    origin: int
    confirmed: int
    consumed: bool = False


@dataclass
class Setup:
    id: str
    direction: int
    formation: int
    formed_index: int
    swept_level: float
    structure_level: float
    stop: float
    target: float
    zone_low: float
    zone_high: float
    ob_low: float
    ob_high: float
    midpoint: float
    fvg_overlap: bool
    swing_low: float
    swing_high: float
    ote_low: float
    ote_high: float
    ote_mid: float
    liquidity_target: float
    pd_zones: tuple
    entry_reference: float | None = None
    trade_target: float | None = None
    status: str = "prepared"
    touched_index: int | None = None


def estimated_rr(reference, stop, target, direction, cfg=DEFAULTS):
    """Net reward / net stop loss with estimated entry and exit costs."""
    risk = direction * (reference - stop)
    reward = direction * (target - reference)
    if min(reference, risk, reward) <= 0:
        return None
    cost = cfg.fee_rate + cfg.slippage_rate
    return (reward - cost * (reference + target)) / (risk + cost * (reference + stop))


def ote_band(bottom, top, direction, cfg=DEFAULTS):
    """Image convention: swing low=0, high=1; short is mirrored long."""
    if not math.isfinite(bottom + top) or bottom >= top or direction not in (-1, 1):
        raise ValueError("Invalid OTE swing")
    ratios = (cfg.ote_long_low, cfg.ote_long_high, cfg.ote_long_mid)
    if direction == -1:
        ratios = (1 - ratios[1], 1 - ratios[0], 1 - ratios[2])
    return tuple(bottom + (top - bottom) * ratio for ratio in ratios)


def inside_ote(reference, setup):
    return setup["ote_low"] <= reference <= setup["ote_high"]


def trade_target(reference, stop, liquidity, direction, cfg=DEFAULTS):
    """Exact nominal 3R target, accepted only before the nearest liquidity."""
    if not all(math.isfinite(v) and v > 0 for v in (reference, stop, liquidity)):
        return None
    risk = direction * (reference - stop)
    if direction not in (-1, 1) or risk <= 0:
        return None
    target = reference + direction * cfg.reward_risk_ratio * risk
    if target <= 0 or direction * (liquidity - target) < -1e-10 * reference:
        return None
    return target


class FrameEngine:
    def __init__(self, timeframe, cfg=DEFAULTS, context_only=False, narrow_fvg=True):
        self.tf = timeframe
        self.cfg = cfg
        self.context_only = context_only
        self.narrow_fvg = narrow_fvg
        self.bars = []
        self.swings = []
        self.high_candidate = None
        self.low_candidate = None
        self.pending = None
        self.setup = None

    @property
    def bias(self):
        return self.setup if self.setup and self.setup.status == "review" else None

    def event(self, stage, bar, reason=None):
        return {**asdict(self.setup), "stage": stage, "timeframe": self.tf,
                "timestamp": bar.timestamp + TF_MS[self.tf], "reference": bar.close,
                "reason": reason}

    def _confirm_swings(self, bar):
        # Candidate extreme is confirmed only when its opposite end is broken.
        # An outside bar extending the extreme replaces the candidate first.
        hc, lc = self.high_candidate, self.low_candidate
        if hc is None or bar.high > hc.high:
            self.high_candidate = bar
        elif bar.low < hc.low:
            self.swings.append(Swing("high", hc.high, hc.timestamp, bar.timestamp + TF_MS[self.tf]))
            self.high_candidate = bar
        if lc is None or bar.low < lc.low:
            self.low_candidate = bar
        elif bar.high > lc.high:
            self.swings.append(Swing("low", lc.low, lc.timestamp, bar.timestamp + TF_MS[self.tf]))
            self.low_candidate = bar

    def _advance_setup(self, bar, i):
        if self.setup is None:
            return []
        s = self.setup
        stopped = bar.low <= s.stop if s.direction == 1 else bar.high >= s.stop
        reached = bar.high >= s.target if s.direction == 1 else bar.low <= s.target
        if stopped or reached:
            stage = "cancelled" if stopped else "target_reached"
            # Both touched: no invented intrabar ordering or claimed profit.
            reason = "stop_and_target_same_bar" if stopped and reached else ("ob_invalidated" if stopped else "liquidity_target")
            event = self.event(stage, bar, reason)
            self.setup = None
            return [event]
        if i - s.formed_index >= self.cfg.setup_lifetime_bars:
            event = self.event("cancelled", bar, "expired")
            self.setup = None
            return [event]
        if s.status == "review":
            return []
        events = []
        intersects = bar.low <= s.zone_high and bar.high >= s.zone_low
        if s.status == "prepared" and intersects:
            s.status = "touched"
            s.touched_index = i
            events.append(self.event("touched", bar))
        if s.status == "touched":
            if self.context_only:
                supported = (bar.close > s.zone_high and bar.close > bar.open) if s.direction == 1 else (bar.close < s.zone_low and bar.close < bar.open)
            else:
                # Confirm within OTE; requiring a close beyond its whole upper
                # edge would make entry in a full-width long OTE impossible.
                center = (s.zone_low + s.zone_high) / 2
                supported = inside_ote(bar.close, asdict(s)) and s.direction * (bar.close - bar.open) > 0 and s.direction * (bar.close - center) >= 0
            if supported:
                target = trade_target(bar.close, s.stop, s.liquidity_target, s.direction, self.cfg)
                if not self.context_only and target is None:
                    events.append(self.event("cancelled", bar, "insufficient_rr"))
                    self.setup = None
                else:
                    s.status = "review"
                    if not self.context_only:
                        s.entry_reference = bar.close
                        s.trade_target = target
                    events.append(self.event("review", bar))
            elif i - s.touched_index >= self.cfg.touch_wait_bars:
                events.append(self.event("cancelled", bar, "support_not_confirmed"))
                self.setup = None
        # A gap crossing the whole zone is never treated as a valid retest.
        if self.setup and self.setup.status == "prepared":
            beyond = bar.high < s.zone_low if s.direction == 1 else bar.low > s.zone_high
            if beyond:
                events.append(self.event("cancelled", bar, "gap_over_zone"))
                self.setup = None
        return events

    def _form_setup(self, bar, i):
        p = self.pending
        d = p["direction"]
        threshold = p["structure"]
        if d * (bar.close - threshold) <= 0 or d * (bar.close - bar.open) <= 0:
            return None
        start = max(0, p["index"] - self.cfg.ob_lookback_bars)
        candidates = [b for b in self.bars[start:i] if d * (b.close - b.open) < 0]
        if not candidates:
            return None
        ob = candidates[-1]
        ob_lo, ob_hi = min(ob.open, ob.close), max(ob.open, ob.close)
        leg = self.bars[p["index"]:i + 1]
        bottom, top = min(b.low for b in leg), max(b.high for b in leg)
        midpoint = (bottom + top) / 2
        ote_low, ote_high, ote_mid = ote_band(bottom, top, d, self.cfg)
        if self.context_only:
            lo, hi = (ob_lo, min(ob_hi, midpoint)) if d == 1 else (max(ob_lo, midpoint), ob_hi)
        else:
            lo, hi = max(ob_lo, ote_low), min(ob_hi, ote_high)
        if lo >= hi:
            return None
        pd_zones = [{"kind": "OB", "low": lo, "high": hi}]
        confluence = False
        # Conventional three-candle wick gap, available only at candle 3 close.
        for j in range(max(2, p["index"]), i + 1):
            left, right = self.bars[j - 2], self.bars[j]
            gap_lo, gap_hi = (left.high, right.low) if d == 1 else (right.high, left.low)
            pd_lo, pd_hi = (gap_lo, min(gap_hi, midpoint)) if d == 1 else (max(gap_lo, midpoint), gap_hi)
            if pd_lo < pd_hi:
                pd_zones.append({"kind": "FVG", "low": pd_lo, "high": pd_hi})
            if not confluence and gap_lo < gap_hi and max(lo, gap_lo) < min(hi, gap_hi):
                if self.narrow_fvg:
                    lo, hi = max(lo, gap_lo), min(hi, gap_hi)
                confluence = True
        target_kind = "high" if d == 1 else "low"
        targets = [s.price for s in self.swings if not s.consumed and s.kind == target_kind and d * (s.price - bar.close) > 0]
        if not targets:
            return None
        target = min(targets) if d == 1 else max(targets)
        stop = min(bottom, ob.low) if d == 1 else max(top, ob.high)
        edge = hi if d == 1 else lo
        if d * (edge - stop) <= 0:
            return None
        return Setup(f"{RULE_VERSION}:{self.tf}:{p['origin']}:{bar.timestamp}:{d}", d,
                     bar.timestamp + TF_MS[self.tf], i, p["level"], threshold,
                     stop, target, lo, hi, ob_lo, ob_hi, midpoint, confluence,
                     bottom, top, ote_low, ote_high, ote_mid, target, tuple(pd_zones))

    def step(self, bar):
        i = len(self.bars)
        self.bars.append(bar)
        events = self._advance_setup(bar, i)
        prior = [s for s in self.swings if not s.consumed]
        crossed = [s for s in prior if (bar.high > s.price if s.kind == "high" else bar.low < s.price)]
        for swing in crossed:
            swing.consumed = True
        high_crossed = any(s.kind == "high" for s in crossed)
        low_crossed = any(s.kind == "low" for s in crossed)
        if high_crossed and low_crossed:
            self.pending = None
        else:
            sweeps = [s for s in crossed if (bar.close < s.price if s.kind == "high" else bar.close > s.price)]
            if sweeps:
                swept = max(sweeps, key=lambda s: s.origin)
                d = 1 if swept.kind == "low" else -1
                opposite = [s for s in prior if s.kind != swept.kind]
                if opposite:
                    structure = max(opposite, key=lambda s: s.origin)
                    self.pending = {"direction": d, "index": i, "origin": swept.origin,
                                    "level": swept.price, "structure": structure.price}
        if self.pending:
            if i - self.pending["index"] > self.cfg.sweep_wait_bars:
                self.pending = None
            else:
                setup = self._form_setup(bar, i)
                if setup:
                    if self.setup:
                        events.append(self.event("cancelled", bar, "replaced"))
                    self.setup = setup
                    self.pending = None
                    events.append(self.event("prepared", bar))
        self._confirm_swings(bar)
        # Consumed prices cannot be swept or selected as targets again.
        self.swings = [s for s in self.swings if not s.consumed]
        return events


def scan_symbol(symbol, high_bars, low_bars, cfg=DEFAULTS):
    """Merge by *close time*, processing 4H before 15m on equal timestamps."""
    high = FrameEngine(cfg.high_tf, cfg, context_only=True)
    low = FrameEngine(cfg.low_tf, cfg)
    timeline = [(b.timestamp + TF_MS[cfg.high_tf], 0, b) for b in high_bars]
    timeline += [(b.timestamp + TF_MS[cfg.low_tf], 1, b) for b in low_bars]
    timeline.sort(key=lambda x: (x[0], x[1]))
    events, tracked = [], {}
    high_last = None
    for stamp, frame, bar in timeline:
        if frame == 0:
            high.step(bar)
            high_last = stamp
            low_events = []
        else:
            low_events = low.step(bar)
        bias = high.bias
        # Never infer a fresh 4H context across an unobserved 4H close.
        fresh = high_last is not None and 0 <= stamp - high_last < TF_MS[cfg.high_tf]
        for setup_id, context in list(tracked.items()):
            if not fresh or bias is None or bias.id != context["context_id"]:
                events.append({**context, "stage": "cancelled", "timestamp": stamp,
                               "reason": "higher_flow_changed"})
                del tracked[setup_id]
        for event in low_events:
            event = {**event, "symbol": symbol}
            setup_id = event["id"]
            if event["stage"] == "prepared":
                if not fresh or bias is None or event["direction"] != bias.direction:
                    continue
                overlap = max(event["zone_low"], bias.zone_low) < min(event["zone_high"], bias.zone_high)
                if cfg.require_htf_overlap and not overlap:
                    continue
                # Apply confluence to the actual watch zone, not only its label.
                if overlap:
                    event["zone_low"] = max(event["zone_low"], bias.zone_low)
                    event["zone_high"] = min(event["zone_high"], bias.zone_high)
                    low.setup.zone_low = event["zone_low"]
                    low.setup.zone_high = event["zone_high"]
                event["target"] = min(event["target"], bias.target) if bias.direction == 1 else max(event["target"], bias.target)
                low.setup.target = event["target"]
                event["liquidity_target"] = event["target"]
                low.setup.liquidity_target = event["target"]
                best_entry = event["zone_low"] if bias.direction == 1 else event["zone_high"]
                if trade_target(best_entry, event["stop"], event["target"], bias.direction, cfg) is None:
                    continue
                event.update(context_id=bias.id, higher_target=bias.target,
                             higher_swept=bias.swept_level, higher_zone_low=bias.zone_low,
                             higher_zone_high=bias.zone_high, higher_overlap=overlap)
                tracked[setup_id] = event
                events.append(event)
            elif setup_id in tracked:
                context = tracked[setup_id]
                # Use the nearer, still-unreached liquidity target for risk checks.
                target = min(event["target"], context["higher_target"]) if event["direction"] == 1 else max(event["target"], context["higher_target"])
                event.update({k: context[k] for k in ("context_id", "higher_target", "higher_swept", "higher_zone_low", "higher_zone_high", "higher_overlap")})
                event["target"] = target
                if event["stage"] == "review":
                    planned = trade_target(event["reference"], event["stop"], target, event["direction"], cfg)
                    if planned is None or not inside_ote(event["reference"], event):
                        event.update(stage="cancelled", reason="insufficient_rr")
                    else:
                        event.update(entry_reference=event["reference"], trade_target=planned)
                events.append(event)
                if event["stage"] in TERMINAL:
                    del tracked[setup_id]
    return {"events": events, "high_bias": high.bias.direction if high.bias else 0,
            "high_setup": asdict(high.bias) if high.bias else None,
            "low_setup": asdict(low.setup) if low.setup else None}
