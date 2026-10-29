"""Causal external raids and protected structure for the observation alerts.

An external level must be a real confirmed turn AND the extreme of the
preceding rolling range. A close outside is a breakout, not a reclaimed raid.
Internal turns can protect an established trend, but cannot initiate a raid.
"""
from collections import deque
from config.overlap_settings import EXTERNAL_LIQUIDITY_BARS, MIN_HISTORY_BARS
from src.ict_engine import TF_MS
from src.ict_nested import Direction
from src.ict_swings import ConfirmedSwings


class ExternalSweepMSS:
    def __init__(self, timeframe, close_ranges=True, lookback=EXTERNAL_LIQUIDITY_BARS):
        if lookback < MIN_HISTORY_BARS:
            raise ValueError("External range needs sufficient closed history")
        self.tf = timeframe
        self.close_ranges = close_ranges
        self.lookback = lookback
        self.history = deque(maxlen=lookback)
        self.swings = ConfirmedSwings(timeframe)
        self.flow = self.pending = None
        self.highest_wick = self.lowest_wick = None
        self.break_origin = None
        self.flow_proof = None
        self.last_transition = None

    def transition(self, reason, stamp, **details):
        self.last_transition = {"reason": reason, "confirmed_at": stamp, **details}

    def bounds(self):
        if len(self.history) < MIN_HISTORY_BARS:
            return None
        return {"low": min(b.low for b in self.history),
                "high": max(b.high for b in self.history),
                "start": self.history[0].timestamp,
                "end": self.history[-1].timestamp + TF_MS[self.tf],
                "bars": len(self.history)}

    def step(self, bar):
        stamp = bar.timestamp + TF_MS[self.tf]
        before = self.bounds()  # Freeze before the raid: no current/future bar.
        if self.flow:
            flow = self.flow
            if flow.direction * (bar.close - flow.invalidation) < 0:
                self.flow = None
                self.transition("protected_structure_close_broken", stamp, direction=flow.direction,level=flow.invalidation,close=bar.close)
            else:
                advanced = (bar.close > self.highest_wick if flow.direction == 1
                            else bar.close < self.lowest_wick)
                if advanced:
                    self.break_origin = bar.timestamp
                    if flow.direction == 1:
                        flow.swing_high = bar.high
                    else:
                        flow.swing_low = bar.low
                self.highest_wick = max(self.highest_wick, bar.high)
                self.lowest_wick = min(self.lowest_wick, bar.low)

        crossed = self.swings.consume(bar)
        external = [] if before is None else [s for s in crossed
                    if s.origin >= before["start"]
                    and s.price == before["high" if s.kind == "high" else "low"]]
        reclaimed = [s for s in external if (bar.close < s.price if s.kind == "high" else bar.close > s.price)]
        if len({s.kind for s in external}) > 1:
            # OHLC cannot establish which external side was taken first.
            self.pending = self.flow = None
            self.transition("both_external_sides_taken_order_unknown", stamp)
        elif reclaimed:
            swept = max(reclaimed, key=lambda s: s.origin)
            direction = 1 if swept.kind == "low" else -1
            structure = self.swings.last_high if direction == 1 else self.swings.last_low
            if self.flow and self.flow.direction != direction:
                self.flow = None
                self.transition("opposite_external_raid_mss_waiting", stamp)
            self.pending = None
            if structure and structure.origin > swept.origin:
                self.pending = {"direction": direction, "sweep": stamp,
                                "level": swept.price, "origin": swept.origin,
                                "structure": structure.price, "structure_origin": structure.origin,
                                "low": bar.low, "high": bar.high,
                                "raid_low": bar.low, "raid_high": bar.high,
                                "external_range": before}
                self.transition("external_raid_mss_waiting", stamp)
        if self.pending:
            p = self.pending
            # A reclaimed raid cannot survive a body close through its tip.
            invalid = bar.close < p["raid_low"] if p["direction"] == 1 else bar.close > p["raid_high"]
            if invalid:
                self.pending = None
                self.transition("pending_raid_extreme_close_broken", stamp,direction=p['direction'],
                                level=p['raid_low'] if p['direction']==1 else p['raid_high'],close=bar.close,sweep=p['sweep'])
            else:
                p["low"], p["high"] = min(p["low"], bar.low), max(p["high"], bar.high)
                if p["direction"] * (bar.close - p["structure"]) > 0:
                    self.flow = Direction(f"external:{self.tf}:{p['sweep']}:{stamp}:{p['direction']}",
                                          p["direction"], stamp, p["sweep"], p["level"], p["structure"],
                                          p["low"] if p["direction"] == 1 else p["high"], p["low"], p["high"])
                    self.highest_wick, self.lowest_wick = p["high"], p["low"]
                    self.break_origin = bar.timestamp
                    self.flow_proof = {"basis": "confirmed_turn_at_rolling_external_extreme",
                                       "swept_origin": p["origin"], "external_range": p["external_range"],
                                       "mss_origin": p["structure_origin"],
                                       "protected_origin": p["sweep"] - TF_MS[self.tf]}
                    self.pending = None
                    self.transition("external_raid_and_close_mss_confirmed", stamp)

        self.swings.confirm(bar)
        if self.flow and self.break_origin is not None:
            # Only a real pullback that preceded a new body-confirmed trend
            # extreme becomes protected. An arbitrary recent wick does not.
            kind = "low" if self.flow.direction == 1 else "high"
            eligible = [s for s in self.swings.all_confirmed
                        if s.kind == kind and self.flow.formed <= s.origin < self.break_origin
                        and self.flow.direction * (s.price - self.flow.invalidation) > 0]
            if eligible:
                protected = max(eligible, key=lambda s: s.origin)
                self.flow.invalidation = protected.price
                self.flow_proof["protected_origin"] = protected.origin
                if self.flow.direction * (bar.close - protected.price) < 0:
                    self.flow = None
                    self.transition("protected_structure_close_broken", stamp)
        self.history.append(bar)

    def diagnostics(self):
        return {"lookback_bars": self.lookback, "minimum_history": MIN_HISTORY_BARS,
                "external_range": self.bounds(), "last_transition": self.last_transition,
                "protected_level": self.flow.invalidation if self.flow else None,
                "flow_proof": self.flow_proof if self.flow else None}
