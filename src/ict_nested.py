"""Image-based nested PD zones: top sweep/MSS, independent OB/FVG below.

Zone notification and hypothetical entry eligibility are separate. No exchange
requests, trades, hidden lower-frame MSS, or arbitrary 8/16/3-bar deadlines.
"""
from collections import defaultdict
from dataclasses import asdict, dataclass
import hashlib
import heapq
import math

from config.ict_settings import DEFAULTS
from src.ict_engine import Swing, TF_MS, ote_band
from src.ict_triple import TRACKS, maximum_overlap

VERSION = "ict-nested-pd-v5"


@dataclass
class Direction:
    id: str
    direction: int
    formed: int
    sweep_time: int
    swept_level: float
    structure_level: float
    invalidation: float
    swing_low: float
    swing_high: float


class SweepMSS:
    def __init__(self, timeframe):
        self.tf = timeframe
        self.high_candidate = self.low_candidate = None
        self.last_high = self.last_low = None
        self.pools = []
        self.pending = None
        self.flow = None

    def step(self, bar):
        stamp = bar.timestamp + TF_MS[self.tf]
        if self.flow and self.flow.direction * (bar.close - self.flow.invalidation) < 0:
            self.flow = None
        crossed = [s for s in self.pools if (bar.high > s.price if s.kind == "high" else bar.low < s.price)]
        reclaimed = [s for s in crossed if (bar.close < s.price if s.kind == "high" else bar.close > s.price)]
        self.pools = [s for s in self.pools if s not in crossed]
        if len({s.kind for s in crossed}) > 1:
            self.pending = None
        elif reclaimed:
            swept = max(reclaimed, key=lambda s: s.origin)
            direction = 1 if swept.kind == "low" else -1
            structure = self.last_high if direction == 1 else self.last_low
            if structure:
                self.pending = {"direction": direction, "sweep": stamp, "level": swept.price,
                                "structure": structure.price, "low": bar.low, "high": bar.high}
        if self.pending:
            p = self.pending
            p["low"], p["high"] = min(p["low"], bar.low), max(p["high"], bar.high)
            if p["direction"] * (bar.close - p["structure"]) > 0:
                self.flow = Direction(f"{self.tf}:{p['sweep']}:{stamp}:{p['direction']}", p["direction"],
                                      stamp, p["sweep"], p["level"], p["structure"],
                                      p["low"] if p["direction"] == 1 else p["high"], p["low"], p["high"])
                self.pending = None
        hc, lc = self.high_candidate, self.low_candidate
        if hc is None or bar.high > hc.high:
            self.high_candidate = bar
        elif bar.low < hc.low:
            self.last_high = Swing("high", hc.high, hc.timestamp, stamp)
            self.pools.append(self.last_high)
            self.high_candidate = bar
        if lc is None or bar.low < lc.low:
            self.low_candidate = bar
        elif bar.high > lc.high:
            self.last_low = Swing("low", lc.low, lc.timestamp, stamp)
            self.pools.append(self.last_low)
            self.low_candidate = bar


@dataclass(frozen=True)
class PDZone:
    id: str
    timeframe: str
    kind: str
    direction: int
    low: float
    high: float
    created: int
    origin: int
    stop: float
    invalidation: float
    # Compatibility with the shared, deterministic interval selector.
    expiry: int = 2**63 - 1


class PDRegistry:
    def __init__(self):
        self.zones = {}
        self.bins = defaultdict(set)
        self.thresholds = defaultdict(list)
        self.recent = defaultdict(list)

    @staticmethod
    def price_bins(low, high):
        # An index only; exact interval intersection is checked after lookup.
        return range(math.floor(math.log(low) * 512), math.floor(math.log(high) * 512) + 1)

    def add(self, zone):
        if zone.low >= zone.high:
            return
        self.zones[zone.id] = zone
        for key in self.price_bins(zone.low, zone.high):
            self.bins[(zone.timeframe, zone.direction, key)].add(zone.id)
        threshold = -zone.invalidation if zone.direction == 1 else zone.invalidation
        heapq.heappush(self.thresholds[(zone.timeframe, zone.direction)], (threshold, zone.id))

    def remove(self, identifier):
        zone = self.zones.pop(identifier, None)
        if zone:
            for key in self.price_bins(zone.low, zone.high):
                bucket = self.bins[(zone.timeframe, zone.direction, key)]
                bucket.discard(identifier)
                if not bucket:
                    del self.bins[(zone.timeframe, zone.direction, key)]

    def query(self, frames, direction, low, high):
        if low <= 0 or low >= high:
            return []
        identifiers = set()
        for tf in frames:
            for key in self.price_bins(low, high):
                identifiers.update(self.bins.get((tf, direction, key), ()))
        return [self.zones[key] for key in identifiers if self.zones[key].low < high and self.zones[key].high > low]

    def step(self, tf, bar):
        stamp = bar.timestamp + TF_MS[tf]
        for direction in (1, -1):
            heap = self.thresholds[(tf, direction)]
            while heap and (bar.close < -heap[0][0] if direction == 1 else bar.close > heap[0][0]):
                _, identifier = heapq.heappop(heap)
                self.remove(identifier)
        recent = self.recent[tf]
        new = []
        if recent:
            prev = recent[-1]
            direction = 1 if prev.close < prev.open and bar.close > bar.open and bar.close > prev.open else (-1 if prev.close > prev.open and bar.close < bar.open and bar.close < prev.open else 0)
            if direction:
                lo, hi = min(prev.open, prev.close), max(prev.open, prev.close)
                stop = min(prev.low, bar.low) if direction == 1 else max(prev.high, bar.high)
                new.append(PDZone(f"{tf}:OB:{prev.timestamp}:{stamp}:{direction}", tf, "OB", direction,
                                  lo, hi, stamp, prev.timestamp, stop, stop))
        if len(recent) >= 2:
            left = recent[-2]
            direction = 1 if bar.low > left.high else (-1 if bar.high < left.low else 0)
            if direction:
                lo, hi = (left.high, bar.low) if direction == 1 else (bar.high, left.low)
                far = lo if direction == 1 else hi
                new.append(PDZone(f"{tf}:FVG:{left.timestamp}:{stamp}:{direction}", tf, "FVG", direction,
                                  lo, hi, stamp, left.timestamp, far, far))
        recent.append(bar)
        self.recent[tf] = recent[-2:]
        for zone in new:
            self.add(zone)
        return new


class NestedMonitor:
    def __init__(self, cfg=DEFAULTS, tracks=None):
        self.cfg, self.tracks = cfg, tracks or TRACKS
        self.registry = PDRegistry()
        self.gates = {chain[0]: SweepMSS(chain[0]) for chain in self.tracks.values()}
        self.last_close = {}
        self.active, self.seen, self.dirty = {}, set(), set(self.tracks)
        self.full_scan = set(self.tracks)
        self.counts = {name: defaultdict(int) for name in self.tracks}

    def _flow(self, chain, stamp):
        if any(tf not in self.last_close or not 0 <= stamp - self.last_close[tf] < TF_MS[tf] for tf in chain):
            return None
        return self.gates[chain[0]].flow

    def step(self, symbol, tf, bar):
        stamp = bar.timestamp + TF_MS[tf]
        if tf in self.gates:
            before = self.gates[tf].flow
            self.gates[tf].step(bar)
            after = self.gates[tf].flow
            if (before.id if before else None) != (after.id if after else None):
                self.dirty.update(name for name, chain in self.tracks.items() if chain[0] == tf)
                self.full_scan.update(name for name, chain in self.tracks.items() if chain[0] == tf)
        for gate in self.gates.values():
            flow = gate.flow
            if flow and bar.timestamp >= flow.formed:
                flow.swing_low = min(flow.swing_low, bar.low)
                flow.swing_high = max(flow.swing_high, bar.high)
        new_zones = self.registry.step(tf, bar)
        self.last_close[tf] = stamp
        output = []
        for name, chain in self.tracks.items():
            stats = self.counts[name]
            if new_zones and tf in chain:
                self.dirty.add(name)
                if tf != chain[-1]:
                    self.full_scan.add(name)
            # On a shared close, larger frames are processed first. Do not
            # cancel a live zone merely because the same-time smaller bar is
            # still next in the queue. Require full freshness at final checks.
            flow = self._flow(chain, stamp) if tf == chain[-1] else self.gates[chain[0]].flow
            active = self.active.get(name)
            if active:
                valid = flow and active["context_id"] == flow.id and all(key in self.registry.zones for key in active["overlap_sources"])
                if not valid:
                    output.append({**active, "stage": "cancelled", "timestamp": stamp,
                                   "reason": "higher_flow_changed" if not flow or flow.id != active["context_id"] else "confluence_invalidated"})
                    stats["cancelled"] += 1
                    self.active.pop(name)
                    active = None
                elif tf == chain[-1] and bar.timestamp >= active["formation"] and active["stage"] == "prepared" and bar.low <= active["zone_high"] and bar.high >= active["zone_low"]:
                    active = {**active, "stage": "touched", "timestamp": stamp, "reference": bar.close}
                    self.active[name] = active
                    output.append(active)
                    stats["touched"] += 1
            if tf != chain[-1] or name not in self.dirty or not flow:
                continue
            self.dirty.discard(name)
            stats["selection_checks"] += 1
            if active and active["stage"] == "prepared" and active["overlap_score"] == 6:
                continue
            if name in self.full_scan:
                children = self.registry.query((chain[-1],), flow.direction, flow.swing_low, flow.swing_high)
                self.full_scan.discard(name)
            else:
                children = [z for z in new_zones if z.timeframe == chain[-1] and z.direction == flow.direction and z.low < flow.swing_high and z.high > flow.swing_low]
            best = None
            ote_lo, ote_hi, ote_mid = ote_band(flow.swing_low, flow.swing_high, flow.direction, self.cfg)
            for child in children:
                if flow.direction == 1 and bar.close < child.low or flow.direction == -1 and bar.close > child.high:
                    continue
                lo, hi = max(child.low, flow.swing_low), min(child.high, flow.swing_high)
                zones = self.registry.query(chain, flow.direction, lo, hi)
                selected = maximum_overlap(zones, lo, hi, ote_mid, chain)
                if selected is None:
                    continue
                source_zones = [self.registry.zones[key] for key in selected["sources"]]
                local = [zone for zone in source_zones if zone.timeframe == chain[-1]]
                stops = [zone.stop for zone in local if (zone.stop <= selected["low"] if flow.direction == 1 else zone.stop >= selected["high"])]
                if not stops:
                    continue
                selected["stop"] = max(stops) if flow.direction == 1 else min(stops)
                key = (-selected["score"], 0 if max(selected["low"], ote_lo) < min(selected["high"], ote_hi) else 1,
                       abs((selected["low"] + selected["high"]) / 2 - ote_mid), -(selected["high"]-selected["low"]), selected["low"])
                if best is None or key < best[0]:
                    best = (key, selected, source_zones)
            if best is None:
                stats["no_nested_zone"] += 1
                continue
            _, selected, sources = best
            signature = f"{name}|{flow.id}|{selected['low']:.10g}|{selected['high']:.10g}|" + "|".join(sorted(selected["sources"]))
            if signature in self.seen:
                continue
            if active and active["stage"] == "prepared" and selected["score"] <= active["overlap_score"]:
                continue
            self.seen.add(signature)
            if active:
                output.append({**active, "stage": "cancelled", "timestamp": stamp, "reason": "replaced"})
            entry_lo, entry_hi = max(selected["low"], ote_lo), min(selected["high"], ote_hi)
            has_ote = entry_lo < entry_hi
            reference = (entry_lo + entry_hi) / 2 if has_ote else (selected["low"]+selected["high"])/2
            risk = flow.direction * (reference - selected["stop"])
            plan = reference + flow.direction * self.cfg.reward_risk_ratio * risk if risk > 0 else None
            event = {"id": VERSION + ":" + hashlib.sha256(signature.encode()).hexdigest()[:24],
                     "symbol": symbol, "track": name, "chain": list(chain), "timeframe": chain[-1],
                     "rule_version": VERSION, "zone_only": True, "stage": "prepared", "timestamp": stamp,
                     "formation": stamp, "direction": flow.direction, "context_id": flow.id,
                     "swept_level": flow.swept_level, "structure_level": flow.structure_level,
                     "flow_formed": flow.formed, "reference": bar.close,
                     "zone_low": selected["low"], "zone_high": selected["high"], "stop": selected["stop"],
                     "ote_low": ote_lo, "ote_high": ote_hi, "ote_mid": ote_mid,
                     "swing_low": flow.swing_low, "swing_high": flow.swing_high,
                     "entry_zone_low": entry_lo if has_ote else None, "entry_zone_high": entry_hi if has_ote else None,
                     "ote_overlap": has_ote, "entry_reference": reference if has_ote else None,
                     "trade_target": plan if has_ote else None,
                     "overlap_score": selected["score"], "overlap_groups": selected["groups"],
                     "overlap_sources": selected["sources"], "source_zones": [asdict(z) for z in sources],
                     "reason": None}
            self.active[name] = event
            output.append(event)
            stats["prepared"] += 1
            stats["prepared_with_ote" if has_ote else "prepared_without_ote"] += 1
        return output


def scan_nested(symbol, frames, cfg=DEFAULTS, tracks=None):
    monitor = NestedMonitor(cfg, tracks)
    timeline = sorted([(b.timestamp + TF_MS[tf], -TF_MS[tf], tf, b) for tf, values in frames.items() for b in values], key=lambda x: x[:2])
    events = []
    for _, _, tf, bar in timeline:
        events.extend(monitor.step(symbol, tf, bar))
    stamp = timeline[-1][0] if timeline else 0
    return {"events": events, "counts": {k: dict(v) for k, v in monitor.counts.items()},
            "tracks": {name: {"chain": list(chain), "direction": (monitor._flow(chain, stamp).direction if monitor._flow(chain, stamp) else 0),
                               "setup": monitor.active.get(name)} for name, chain in monitor.tracks.items()}}
