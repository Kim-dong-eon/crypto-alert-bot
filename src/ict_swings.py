"""Causal left-and-right turning points, with the lecture's opposite-end check."""
from src.ict_engine import Swing, TF_MS


class ConfirmedSwings:
    def __init__(self, timeframe):
        self.tf = timeframe
        self.previous = None
        self.high_candidate = self.low_candidate = None
        self.high_candidates = []
        self.low_candidates = []
        self.last_high = self.last_low = None
        self.pools = []
        self.all_confirmed = []
        self.bars = []
        self.indices = {}

    def consume(self, bar):
        taken = [s for s in self.pools if (bar.high > s.price if s.kind == "high" else bar.low < s.price)]
        self.pools = [s for s in self.pools if s not in taken]
        return taken

    def confirm(self, bar):
        stamp = bar.timestamp + TF_MS[self.tf]
        previous = self.previous
        found = []
        if previous is not None:
            remaining = []
            for candidate in self.high_candidates:
                if bar.high > candidate.high:
                    continue
                if bar.high < candidate.high and bar.low < candidate.low:
                    swing = Swing("high", candidate.high, candidate.timestamp, stamp)
                    if self.last_high is None or swing.origin > self.last_high.origin:
                        self.last_high = swing
                    found.append(swing)
                else:
                    remaining.append(candidate)
            if bar.high > previous.high:
                remaining.append(bar)
            self.high_candidates = remaining
            remaining = []
            for candidate in self.low_candidates:
                if bar.low < candidate.low:
                    continue
                if bar.low > candidate.low and bar.high > candidate.high:
                    swing = Swing("low", candidate.low, candidate.timestamp, stamp)
                    if self.last_low is None or swing.origin > self.last_low.origin:
                        self.last_low = swing
                    found.append(swing)
                else:
                    remaining.append(candidate)
            if bar.low < previous.low:
                remaining.append(bar)
            self.low_candidates = remaining
        self.high_candidate = self.high_candidates[-1] if self.high_candidates else None
        self.low_candidate = self.low_candidates[-1] if self.low_candidates else None
        self.previous = bar
        self.indices[bar.timestamp] = len(self.bars)
        self.bars.append(bar)
        self.pools.extend(found)
        self.all_confirmed.extend(found)
        return found

    def step(self, bar):
        taken = self.consume(bar)
        found = self.confirm(bar)
        return taken, found

    def leg(self, direction):
        """Latest real low→known highs, or real high→known lows."""
        anchor = self.last_low if direction == 1 else self.last_high
        if anchor is None:
            return None
        start = self.indices[anchor.origin]
        since = self.bars[start:]
        low = anchor.price if direction == 1 else min(b.low for b in since)
        high = max(b.high for b in since) if direction == 1 else anchor.price
        if low >= high:
            return None
        return {"low": low, "high": high, "origin": anchor.origin, "confirmed": anchor.confirmed,
                "midpoint": (low + high)/2}

    def target(self, direction, boundary):
        kind = "high" if direction == 1 else "low"
        available = [s for s in self.pools if s.kind == kind and direction * (s.price-boundary) > 0]
        if not available:
            return None
        return min(available, key=lambda s: (direction*s.price, -s.confirmed))
