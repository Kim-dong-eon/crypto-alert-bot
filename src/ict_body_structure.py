"""Closed-body structure updates, with wick liquidity kept separately."""
from src.ict_engine import Swing, TF_MS
from src.ict_swings import ConfirmedSwings


def close_confirmed_leg(detector, direction):
    """A range endpoint advances only after a close clears all prior leg wicks."""
    anchor = detector.last_low if direction == 1 else detector.last_high
    if anchor is None:
        return None
    since = detector.bars[detector.indices[anchor.origin]:]
    running = since[0].high if direction == 1 else since[0].low
    accepted = proof = None
    for bar in since[1:]:
        if direction * (bar.close-anchor.price) < 0:
            return None
        if direction * (bar.close-running) > 0:
            accepted = bar.high if direction == 1 else bar.low
            proof = {"extreme_confirmed":bar.timestamp+TF_MS[detector.tf],
                     "prior_wick":running,"break_close":bar.close}
        running = max(running,bar.high) if direction == 1 else min(running,bar.low)
    if accepted is None:
        return None
    low, high = (anchor.price,accepted) if direction == 1 else (accepted,anchor.price)
    if low >= high:
        return None
    return {"low":low,"high":high,"origin":anchor.origin,"confirmed":anchor.confirmed,
            "midpoint":(low+high)/2,"basis":"close_beyond_prior_wick",**proof}


class CloseRangeSwings(ConfirmedSwings):
    """Control case: old pivots, only the dealing-range update changes."""
    def leg(self, direction):
        return close_confirmed_leg(self,direction)


class CloseTurnSwings(CloseRangeSwings):
    """One wick extremum per leg; turn confirmed by a closed-body reversal.

    A close above the previous completed candle's high establishes an up leg;
    a close below its low establishes a down leg. Wicks alone never flip legs.
    The unconfirmed wick extremum can move, but published pivots never move.
    """
    def __init__(self,timeframe):
        super().__init__(timeframe)
        self.direction = 0
        self.extreme = None

    def confirm(self,bar):
        previous = self.previous
        found = []
        self.indices[bar.timestamp] = len(self.bars)
        self.bars.append(bar)
        if previous is not None:
            up = bar.close > previous.high
            down = bar.close < previous.low
            if self.direction == 0:
                if up or down:
                    self.direction = 1 if up else -1
                    self.extreme = bar
            else:
                if (bar.high > self.extreme.high if self.direction == 1 else bar.low < self.extreme.low):
                    self.extreme = bar
                turns = down if self.direction == 1 else up
                # A fresh wick extreme and a reversal inside one outside candle
                # have unknown order. Require a later candle to confirm it.
                if turns and self.extreme.timestamp < bar.timestamp:
                    kind = "high" if self.direction == 1 else "low"
                    price = self.extreme.high if kind == "high" else self.extreme.low
                    swing = Swing(kind,price,self.extreme.timestamp,bar.timestamp+TF_MS[self.tf])
                    found.append(swing)
                    if kind == "high":self.last_high = swing
                    else:self.last_low = swing
                    start = self.indices[self.extreme.timestamp]+1
                    after = self.bars[start:]
                    self.direction *= -1
                    self.extreme = min(after,key=lambda b:b.low) if self.direction == -1 else max(after,key=lambda b:b.high)
        self.previous = bar
        self.pools.extend(found)
        self.all_confirmed.extend(found)
        return found
