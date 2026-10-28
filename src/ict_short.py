"""BTC short profile: real pivots, HTF equilibrium, LTF-only PD/OTE."""
from collections import defaultdict
from dataclasses import asdict
import hashlib

from config.ict_settings import DEFAULTS
from src.ict_engine import TF_MS, ote_band
from src.ict_swings import ConfirmedSwings
from src.ict_nested import Direction, PDRegistry
from src.ict_triple import maximum_overlap
from src.ict_exit_plan import exit_plan

VERSION = "ict-short-eq50-ote15-split-v6"
CHAIN = ("4h", "1h", "15m")


class RealSweepMSS:
    def __init__(self, timeframe="4h", swings=None, close_ranges=False):
        self.tf = timeframe
        self.swings = swings if swings is not None else ConfirmedSwings(timeframe)
        self.close_ranges = close_ranges
        self.highest_wick = self.lowest_wick = None
        self.range_proof = None
        self.pending = self.flow = None

    def select_swept(self,reclaimed):
        return max(reclaimed,key=lambda s:s.origin)

    def step(self, bar):
        stamp = bar.timestamp + TF_MS[self.tf]
        if self.flow:
            if self.flow.direction*(bar.close-self.flow.invalidation) < 0:
                self.flow = None
            else:
                if self.close_ranges:
                    if self.flow.direction == 1 and bar.close > self.highest_wick:
                        self.flow.swing_high = bar.high
                        self.range_proof = {"basis":"close_beyond_prior_wick","extreme_confirmed":stamp,
                                            "prior_wick":self.highest_wick,"break_close":bar.close}
                    elif self.flow.direction == -1 and bar.close < self.lowest_wick:
                        self.flow.swing_low = bar.low
                        self.range_proof = {"basis":"close_beyond_prior_wick","extreme_confirmed":stamp,
                                            "prior_wick":self.lowest_wick,"break_close":bar.close}
                    self.highest_wick = max(self.highest_wick,bar.high)
                    self.lowest_wick = min(self.lowest_wick,bar.low)
                else:
                    self.flow.swing_low = min(self.flow.swing_low, bar.low)
                    self.flow.swing_high = max(self.flow.swing_high, bar.high)
        crossed = self.swings.consume(bar)
        reclaimed = [s for s in crossed if (bar.close < s.price if s.kind == "high" else bar.close > s.price)]
        if len({s.kind for s in crossed}) > 1:
            self.pending = None
        elif reclaimed:
            swept = self.select_swept(reclaimed)
            direction = 1 if swept.kind == "low" else -1
            structure = self.swings.last_high if direction == 1 else self.swings.last_low
            if structure:
                self.pending = {"direction": direction, "sweep": stamp, "level": swept.price,
                                "structure": structure.price, "low": bar.low, "high": bar.high}
        if self.pending:
            p = self.pending
            p["low"], p["high"] = min(p["low"], bar.low), max(p["high"], bar.high)
            if p["direction"]*(bar.close-p["structure"]) > 0:
                self.flow = Direction(f"real:{self.tf}:{p['sweep']}:{stamp}:{p['direction']}", p["direction"],
                                      stamp, p["sweep"], p["level"], p["structure"],
                                      p["low"] if p["direction"] == 1 else p["high"], p["low"], p["high"])
                self.highest_wick, self.lowest_wick = p["high"],p["low"]
                self.range_proof = {"basis":"sweep_to_mss","extreme_confirmed":stamp}
                self.pending = None
        self.swings.confirm(bar)


def half_zone(low, high, direction):
    midpoint = (low+high)/2
    return (low, midpoint) if direction == 1 else (midpoint, high)


class ShortMonitor:
    def __init__(self, cfg=DEFAULTS, swing_factory=ConfirmedSwings, close_ranges=False, version=VERSION):
        self.cfg = cfg
        self.version = version
        self.primary = RealSweepMSS("4h",swing_factory("4h"),close_ranges)
        self.swings = {"4h": self.primary.swings, "1h": swing_factory("1h"), "15m": swing_factory("15m")}
        self.registry = PDRegistry()
        self.last_close = {}
        self.active = None
        self.seen = set()
        self.counts = defaultdict(int)
        self.dirty = True

    def ranges(self, direction):
        flow = self.primary.flow
        middle = self.swings["1h"].leg(direction)
        lower = self.swings["15m"].leg(direction)
        if not flow or not middle or not lower:
            return None
        primary = {"low": flow.swing_low, "high": flow.swing_high,
                   "midpoint": (flow.swing_low+flow.swing_high)/2, "origin": flow.sweep_time}
        if self.primary.close_ranges:
            primary.update(self.primary.range_proof or {})
        hi_lo, hi_hi = half_zone(primary["low"], primary["high"], direction)
        mid_lo, mid_hi = half_zone(middle["low"], middle["high"], direction)
        ote_lo, ote_hi, ote_mid = ote_band(lower["low"], lower["high"], direction, self.cfg)
        lo, hi = max(hi_lo, mid_lo, ote_lo), min(hi_hi, mid_hi, ote_hi)
        return {"primary": primary, "middle": middle, "lower": lower,
                "high_half": (hi_lo,hi_hi), "middle_half": (mid_lo,mid_hi),
                "ote": (ote_lo,ote_hi,ote_mid), "low":lo, "high":hi}

    def step(self, symbol, tf, bar):
        stamp = bar.timestamp + TF_MS[tf]
        if tf == "4h":
            self.primary.step(bar)
        else:
            _, found = self.swings[tf].step(bar)
            if found:
                self.dirty = True
        self.last_close[tf] = stamp
        made = self.registry.step(tf, bar) if tf == "15m" else []
        if tf in ("4h","1h") or made:
            self.dirty = True
        output = []
        flow = self.primary.flow
        ranges = self.ranges(flow.direction) if flow else None
        active = self.active
        if active:
            reason = None
            if not flow or active["context_id"] != flow.id:
                reason = "higher_flow_changed"
            elif self.primary.close_ranges and ranges is None:
                reason = "structure_range_invalidated"
            elif any(key not in self.registry.zones for key in active["overlap_sources"]):
                reason = "confluence_invalidated"
            elif ranges and any(active["zone_low"] < lo or active["zone_high"] > hi for lo,hi in (ranges["high_half"],ranges["middle_half"])):
                reason = "equilibrium_changed"
            elif active["swing_target"] is not None and tf == "15m":
                available = any(s.kind == active["target_kind"] and s.origin == active["target_origin"] for s in self.swings["15m"].pools)
                if not available:
                    reason = "swing_target_consumed"
            if reason:
                output.append({**active,"stage":"cancelled","timestamp":stamp,"reason":reason})
                self.counts["cancelled:"+reason] += 1
                self.active = active = None
                self.dirty = True
            elif tf == "15m" and bar.timestamp >= active["formation"] and active["stage"] == "prepared" and bar.low <= active["zone_high"] and bar.high >= active["zone_low"]:
                active = {**active,"stage":"touched","timestamp":stamp,"reference":bar.close}
                self.active = active
                output.append(active)
                self.counts["touched"] += 1
        if tf != "15m" or not self.dirty or not flow:
            return output
        if any(frame not in self.last_close or not 0 <= stamp-self.last_close[frame] < TF_MS[frame] for frame in CHAIN):
            return output
        self.dirty = False
        self.counts["selection_checks"] += 1
        if not ranges:
            self.counts["missing_real_range_anchor"] += 1
            return output
        if ranges["low"] >= ranges["high"]:
            self.counts["no_eq50_ote_intersection"] += 1
            return output
        zones = self.registry.query(("15m",),flow.direction,ranges["low"],ranges["high"])
        overlap = maximum_overlap(zones,ranges["low"],ranges["high"],ranges["ote"][2],("15m",))
        if not overlap:
            self.counts["no_15m_pd_in_entry_range"] += 1
            return output
        source_zones = [self.registry.zones[key] for key in overlap["sources"]]
        ob = [zone for zone in source_zones if zone.kind == "OB"]
        protective = ob or source_zones
        stops = [zone.stop for zone in protective if (zone.stop <= overlap["low"] if flow.direction == 1 else zone.stop >= overlap["high"])]
        if not stops:
            self.counts["invalid_15m_stop"] += 1
            return output
        stop = max(stops) if flow.direction == 1 else min(stops)
        boundary = overlap["high"] if flow.direction == 1 else overlap["low"]
        target = self.swings["15m"].target(flow.direction,boundary)
        signature = f"{flow.id}:{overlap['low']:.10g}:{overlap['high']:.10g}:"+"|".join(overlap["sources"])+f":{target.origin if target else 'none'}"
        if signature in self.seen:
            return output
        eligible = target is not None
        score = 2+overlap["score"]
        nearer_new_target = bool(active and target and active["swing_target"] is not None and flow.direction*(target.price-active["swing_target"])<0)
        if active and active["stage"]=="prepared" and score<=active["overlap_score"] and (active["trade_eligible"] or not eligible) and not nearer_new_target:
            return output
        self.seen.add(signature)
        if active:
            output.append({**active,"stage":"cancelled","timestamp":stamp,"reason":"replaced"})
        reference = (overlap["low"]+overlap["high"])/2
        plan = exit_plan(reference,stop,flow.direction,target.price,self.cfg.reward_risk_ratio) if target else None
        event = {"id":self.version+":"+hashlib.sha256(signature.encode()).hexdigest()[:24],
                 "symbol":symbol,"track":"short","chain":list(CHAIN),"timeframe":"15m",
                 "rule_version":self.version,"zone_only":True,"short_equilibrium":True,
                 "stage":"prepared","timestamp":stamp,"formation":stamp,"direction":flow.direction,
                 "context_id":flow.id,"swept_level":flow.swept_level,"structure_level":flow.structure_level,
                 "reference":bar.close,"zone_low":overlap["low"],"zone_high":overlap["high"],"stop":stop,
                 "ote_low":ranges["ote"][0],"ote_high":ranges["ote"][1],"ote_mid":ranges["ote"][2],
                 "ote_timeframe":"15m","ote_overlap":True,"entry_zone_low":overlap["low"],"entry_zone_high":overlap["high"],
                 "swing_low":ranges["lower"]["low"],"swing_high":ranges["lower"]["high"],
                 "ltf_range":ranges["lower"],
                 "eq4h":ranges["primary"],"eq1h":ranges["middle"],
                 "overlap_score":score,"overlap_max":4,"overlap_groups":["4h:50%","1h:50%"]+overlap["groups"],
                 "overlap_sources":overlap["sources"],"source_zones":[asdict(zone) for zone in source_zones],
                 "entry_reference":reference,"trade_target":plan["three_r"] if plan else None,
                 "exit_plan":plan,"trade_eligible":eligible and plan is not None,
                 "swing_target":target.price if target else None,"target_kind":target.kind if target else None,
                 "target_origin":target.origin if target else None,"target_confirmed":target.confirmed if target else None,
                 "reason":None}
        self.active=event
        output.append(event)
        self.counts["prepared"] += 1
        self.counts["prepared_trade_eligible" if event["trade_eligible"] else "prepared_no_swing_target"] += 1
        return output


def scan_short(symbol,frames,cfg=DEFAULTS,monitor_factory=ShortMonitor):
    monitor=monitor_factory(cfg)
    timeline=sorted([(b.timestamp+TF_MS[tf],-TF_MS[tf],tf,b) for tf,values in frames.items() if tf in CHAIN for b in values],key=lambda x:x[:2])
    events=[]
    for _,_,tf,bar in timeline:
        events.extend(monitor.step(symbol,tf,bar))
    return {"events":events,"counts":dict(monitor.counts),
            "tracks":{"short":{"chain":list(CHAIN),"direction":monitor.primary.flow.direction if monitor.primary.flow else 0,"setup":monitor.active}}}
