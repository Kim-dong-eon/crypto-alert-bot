"""Three-timeframe ICT profiles and causal maximum PD confluence selection."""
from dataclasses import asdict, dataclass

from config.ict_settings import DEFAULTS
from src.ict_engine import FrameEngine, TF_MS, TERMINAL, inside_ote, trade_target


TRIPLE_VERSION = "ict-triple-ote-v4-role-zones"
TRACKS = {"short": ("4h", "1h", "15m"), "medium": ("1d", "4h", "1h"),
          "long": ("1w", "1d", "4h")}
TRACK_NAMES = {"short": "단기", "medium": "중기", "long": "장기"}


@dataclass(frozen=True)
class Zone:
    id: str
    timeframe: str
    kind: str
    direction: int
    low: float
    high: float
    created: int
    expiry: int
    stop: float
    target: float


def maximum_overlap(zones, low, high, ote_mid, required_frames):
    """Count unique (TF, type), not repeated blocks from the same evidence."""
    relevant = [z for z in zones if z.low < high and z.high > low]
    edges = sorted({low, high} | {max(low, z.low) for z in relevant} | {min(high, z.high) for z in relevant})
    segments = []
    for left, right in zip(edges, edges[1:]):
        if left >= right:
            continue
        center = (left + right) / 2
        active = [z for z in relevant if z.low <= center <= z.high]
        groups = {(z.timeframe, z.kind) for z in active}
        if not set(required_frames).issubset({tf for tf, _ in groups}):
            continue
        # One deterministic representative per group suffices to keep the score.
        representatives = []
        for group in sorted(groups):
            choices = [z for z in active if (z.timeframe, z.kind) == group]
            representatives.append(max(choices, key=lambda z: (z.expiry, z.created, z.id)))
        segments.append({"low": left, "high": right, "score": len(groups),
                         "groups": [f"{tf}:{kind}" for tf, kind in sorted(groups)],
                         "sources": [z.id for z in representatives]})
    if not segments:
        return None
    # Coalesce neighboring segments with identical evidence representatives.
    merged = []
    for segment in segments:
        if merged and merged[-1]["high"] == segment["low"] and merged[-1]["sources"] == segment["sources"]:
            merged[-1]["high"] = segment["high"]
        else:
            merged.append(dict(segment))
    return min(merged, key=lambda s: (-s["score"], abs((s["low"] + s["high"]) / 2 - ote_mid),
                                      -(s["high"] - s["low"]), s["low"]))


class TripleMonitor:
    def __init__(self, cfg=DEFAULTS, tracks=None, flow_policy="roles"):
        self.cfg = cfg
        self.tracks = tracks or TRACKS
        if flow_policy not in ("roles", "strict"):
            raise ValueError("Invalid flow policy")
        self.flow_policy = flow_policy
        frames = {tf for chain in self.tracks.values() for tf in chain}
        self.context = {tf: FrameEngine(tf, cfg, context_only=True) for tf in frames}
        # Global scoring must see the entire OB∩OTE, not only the first FVG.
        self.entries = {name: FrameEngine(chain[-1], cfg, narrow_fvg=False) for name, chain in self.tracks.items()}
        self.last_close = {}
        self.invalid_flows = set()
        self.zones = {}
        self.tracked = {}
        self.counts = {name: {} for name in self.tracks}
        self.frame_counts = {tf: {} for tf in frames}

    def count(self, name, reason):
        stats = self.counts[name]
        stats[reason] = stats.get(reason, 0) + 1

    def _retire_zones(self, timeframe, bar, stamp):
        for key, zone in list(self.zones.items()):
            expired = stamp >= zone.expiry
            crossed = bar.timestamp >= zone.created and ((bar.low <= zone.stop or bar.high >= zone.target) if zone.direction == 1 else (bar.high >= zone.stop or bar.low <= zone.target))
            if expired or crossed:
                del self.zones[key]

    def _add_zones(self, event, source="context"):
        for n, pd in enumerate(event["pd_zones"]):
            key = f"{event['id']}:{source}:{n}"
            self.zones[key] = Zone(key, event["timeframe"], pd["kind"], event["direction"],
                                   pd["low"], pd["high"], event["formation"],
                                   event["formation"] + self.cfg.setup_lifetime_bars * TF_MS[event["timeframe"]],
                                   event["stop"], event["liquidity_target"])

    def _flows(self, chain, stamp):
        biases = ([self.context[chain[0]].setup] if self.flow_policy == "roles" else [self.context[tf].bias for tf in chain[:2]])
        fresh = all(tf in self.last_close and 0 <= stamp - self.last_close[tf] < TF_MS[tf] for tf in chain[:2])
        if not fresh or any(b is None or b.id in self.invalid_flows for b in biases) or (len(biases) == 2 and biases[0].direction != biases[1].direction):
            return None
        return biases

    def step(self, symbol, timeframe, bar):
        stamp = bar.timestamp + TF_MS[timeframe]
        for engine in self.context.values():
            bias = engine.setup if self.flow_policy == "roles" else engine.bias
            if bias and bar.timestamp >= bias.formation:
                crossed = (bar.low <= bias.stop or bar.high >= bias.liquidity_target) if bias.direction == 1 else (bar.high >= bias.stop or bar.low <= bias.liquidity_target)
                if crossed:
                    self.invalid_flows.add(bias.id)
        self._retire_zones(timeframe, bar, stamp)
        for event in self.context[timeframe].step(bar):
            key = event["stage"] + (":" + event["reason"] if event.get("reason") else "")
            counts = self.frame_counts[timeframe]
            counts[key] = counts.get(key, 0) + 1
            if event["stage"] == "prepared":
                self._add_zones(event)
        self.last_close[timeframe] = stamp
        output = []
        for name, chain in self.tracks.items():
            entry = self.entries[name]
            low_events = entry.step(bar) if chain[-1] == timeframe else []
            flows = self._flows(chain, stamp)
            active = self.tracked.get(name)
            if active:
                flow_ids = [b.id for b in flows] if flows else []
                if flow_ids != active["flow_ids"] or any(key not in self.zones for key in active["overlap_sources"]):
                    reason = "higher_flow_changed" if flow_ids != active["flow_ids"] else "confluence_invalidated"
                    output.append({**active, "stage": "cancelled", "timestamp": stamp, "reason": reason})
                    self.count(name, reason)
                    self.tracked.pop(name)
            for raw in low_events:
                event = {**raw, "symbol": symbol, "track": name, "chain": list(chain),
                         "rule_version": TRIPLE_VERSION}
                active = self.tracked.get(name)
                if event["stage"] == "prepared":
                    self.count(name, "raw_prepared")
                    if not flows or event["direction"] != flows[0].direction:
                        self.count(name, "flow_mismatch")
                        first = self.context[chain[0]].setup if self.flow_policy == "roles" else self.context[chain[0]].bias
                        middle = self.context[chain[1]].bias
                        if first is None:
                            self.count(name, "primary_flow_unconfirmed")
                        elif first.id in self.invalid_flows:
                            self.count(name, "primary_flow_finished")
                        elif self.flow_policy == "strict" and middle is None:
                            self.count(name, "middle_flow_unconfirmed")
                        elif self.flow_policy == "strict" and middle.id in self.invalid_flows:
                            self.count(name, "middle_flow_finished")
                        else:
                            self.count(name, "direction_or_freshness_mismatch")
                        continue
                    # The current final-TF OB must participate, even if context
                    # engine had not produced the same setup for some reason.
                    self._add_zones(event, "entry")
                    zones = [z for z in self.zones.values() if z.direction == event["direction"] and z.timeframe in chain]
                    required_frames = chain[1:] if self.flow_policy == "roles" else chain
                    overlap = maximum_overlap(zones, event["zone_low"], event["zone_high"], event["ote_mid"], required_frames)
                    if overlap is None:
                        self.count(name, "no_middle_final_overlap" if self.flow_policy == "roles" else "no_three_frame_overlap")
                        continue
                    targets = [event["liquidity_target"]] + [b.liquidity_target for b in flows]
                    if self.flow_policy == "roles":
                        targets += [self.zones[key].target for key in overlap["sources"]]
                    liquidity = min(targets) if event["direction"] == 1 else max(targets)
                    best = overlap["low"] if event["direction"] == 1 else overlap["high"]
                    if trade_target(best, event["stop"], liquidity, event["direction"], self.cfg) is None:
                        self.count(name, "no_three_r_space")
                        continue
                    entry.setup.zone_low, entry.setup.zone_high = overlap["low"], overlap["high"]
                    entry.setup.fvg_overlap = f"{chain[-1]}:FVG" in overlap["groups"]
                    entry.setup.target = entry.setup.liquidity_target = liquidity
                    event.update(zone_low=overlap["low"], zone_high=overlap["high"],
                                 fvg_overlap=entry.setup.fvg_overlap,
                                 target=liquidity, liquidity_target=liquidity,
                                 overlap_score=overlap["score"], overlap_groups=overlap["groups"],
                                 overlap_sources=overlap["sources"], flow_ids=[b.id for b in flows],
                                 context_id=flows[0].id, higher_target=flows[0].liquidity_target,
                                 higher_swept=flows[0].swept_level, higher_overlap=True,
                                 higher_zone_low=flows[0].zone_low, higher_zone_high=flows[0].zone_high)
                    self.tracked[name] = event
                    self.count(name, "prepared")
                    output.append(event)
                elif active and event["id"] == active["id"]:
                    extra = {key: active[key] for key in ("overlap_score", "overlap_groups", "overlap_sources", "flow_ids", "context_id", "higher_target", "higher_swept", "higher_overlap", "higher_zone_low", "higher_zone_high")}
                    event.update(extra)
                    if event["stage"] == "review" and not (event["zone_low"] <= event["reference"] <= event["zone_high"] and inside_ote(event["reference"], event)):
                        event.update(stage="cancelled", reason="outside_confluence")
                    self.count(name, event["stage"])
                    output.append(event)
                    if event["stage"] in TERMINAL:
                        self.tracked.pop(name, None)
        return output


def scan_tracks(symbol, frames, cfg=DEFAULTS, tracks=None):
    monitor = TripleMonitor(cfg, tracks)
    timeline = [(bar.timestamp + TF_MS[tf], -TF_MS[tf], tf, bar) for tf, bars in frames.items() for bar in bars]
    timeline.sort(key=lambda x: (x[0], x[1]))
    events = []
    for _, _, tf, bar in timeline:
        events.extend(monitor.step(symbol, tf, bar))
    return {"events": events, "counts": monitor.counts,
            "tracks": {name: {"chain": list(chain), "direction": (monitor._flows(chain, timeline[-1][0])[0].direction if timeline and monitor._flows(chain, timeline[-1][0]) else 0),
                               "setup": monitor.tracked.get(name)} for name, chain in monitor.tracks.items()}}
