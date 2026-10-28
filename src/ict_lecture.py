"""User-supplied full lecture iwDPnAg-VsU: ordered reversal, then reaction."""
from collections import defaultdict
from dataclasses import asdict

from config.ict_settings import DEFAULTS
from src.ict_engine import TF_MS
from src.ict_swings import ConfirmedSwings
from src.ict_body_structure import CloseRangeSwings
from src.ict_nested import PDRegistry,PDZone
from src.ict_short import ShortMonitor,RealSweepMSS,scan_short,CHAIN
from src.ict_exit_plan import exit_plan

VERSION="ict-lecture-ordered-reaction-v8"
LEVEL_TOLERANCE=.0005
STOP_ATR_BUFFER=.1


class LectureFlow(RealSweepMSS):
    def __init__(self):
        super().__init__("4h",CloseRangeSwings("4h"),close_ranges=True)
        self.counter_target=None
        self.strength=1

    def select_swept(self,reclaimed):
        def strength(point):
            return len({s.origin for s in self.swings.all_confirmed if s.kind==point.kind and abs(s.price-point.price)<=point.price*LEVEL_TOLERANCE})
        chosen=max(reclaimed,key=lambda s:(strength(s),s.origin))
        self.strength=strength(chosen)
        return chosen

    def step(self,bar):
        previous=self.flow.id if self.flow else None
        available=list(self.swings.pools)
        reached=self.counter_target and (bar.high>=self.counter_target.price if self.counter_target.kind=="high" else bar.low<=self.counter_target.price)
        super().step(bar)
        if self.flow and self.flow.id!=previous:
            d=self.flow.direction
            points=[s for s in available if s.kind==("high" if d==1 else "low") and (s.price>bar.high if d==1 else s.price<bar.low)]
            self.counter_target=min(points,key=lambda s:d*s.price) if points else None
        elif reached and self.flow and self.flow.id==previous:
            self.flow=None
            self.counter_target=None


class LectureRegistry(PDRegistry):
    def __init__(self):
        super().__init__()
        self.raw=PDRegistry()
        self.swings=ConfirmedSwings("15m")
        self.originals={}
        self.patterns={}
        self.counts=defaultdict(int)
        self.atr=None
        self.warm=[]
        self.previous=None
        self.flow=None

    def query(self,frames,direction,low,high):
        if not self.flow:return []
        candidates=super().query(frames,direction,low,high)
        valid=[z for z in candidates if self.patterns[z.id]["raid_time"]>=self.flow.sweep_time-TF_MS["4h"]]
        obs=[z for z in valid if z.kind=="OB"]
        if not obs:return []
        return obs+[z for z in valid if z.kind=="FVG" and any(max(low,z.low,ob.low)<min(high,z.high,ob.high) for ob in obs)]

    def step(self,tf,bar):
        if tf!="15m":return []
        stamp=bar.timestamp+TF_MS[tf]
        tr=bar.high-bar.low if self.previous is None else max(bar.high-bar.low,abs(bar.high-self.previous.close),abs(bar.low-self.previous.close))
        if self.atr is None:
            self.warm.append(tr)
            if len(self.warm)==14:self.atr=sum(self.warm)/14
        else:self.atr=(self.atr*13+tr)/14
        self.previous=bar
        for z in list(self.zones.values()):
            if z.direction*(bar.close-z.invalidation)<0:self.remove(z.id)
        taken=self.swings.consume(bar)
        ambiguous=len({s.kind for s in taken})>1
        if ambiguous:self.counts["ambiguous_liquidity_bar"]+=1
        made=self.raw.step(tf,bar)
        gaps=[z for z in made if z.kind=="FVG"]
        result=[]
        for key,state in list(self.originals.items()):
            source=state["ob"]
            d=source.direction
            raids=[] if ambiguous else [s for s in taken if s.kind==("high" if d==1 else "low")]
            if "breach_time" not in state and state.get("gap") and state["gap"].created<stamp and raids:
                point=max(raids,key=lambda s:s.origin)
                state.update(raid_time=stamp,raid_level=point.price,
                             raid_extreme=bar.high if d==1 else bar.low)
                self.counts["source_with_liquidity_raid"]+=1
            boundary=source.low if d==1 else source.high
            broken=d*(bar.close-boundary)<0
            if broken and "breach_time" not in state:
                if "raid_time" not in state:
                    del self.originals[key]
                    continue
                state["breach_time"]=stamp
            breach=state.get("breach_time")
            if breach:
                opposite=[g for g in gaps if g.direction==-d and g.origin<breach<=g.created and state["raid_time"]<=breach]
                if opposite:
                    gap=opposite[-1]
                    invalid=max(state["raid_extreme"],source.high) if d==1 else min(state["raid_extreme"],source.low)
                    identifier=f"lecture:{source.id}:{gap.id}"
                    zone=PDZone(identifier,"15m","OB",-d,source.low,source.high,stamp,source.origin,invalid,invalid)
                    metadata={"source_ob":asdict(source),"source_fvg":asdict(state["gap"]),
                              "raid_time":state["raid_time"],"raid_level":state["raid_level"],
                              "raid_extreme":state["raid_extreme"],"breach_time":breach,
                              "opposite_fvg":asdict(gap),"confirmed":stamp}
                    self.add(zone);self.patterns[zone.id]=metadata;result.append(zone)
                    companion=PDZone(identifier+":FVG","15m","FVG",-d,gap.low,gap.high,stamp,gap.origin,invalid,invalid)
                    self.add(companion);self.patterns[companion.id]=metadata;result.append(companion)
                    self.counts["ordered_reversals"]+=1
                    del self.originals[key]
                    continue
                if stamp-breach>=2*TF_MS[tf]:
                    self.counts["breach_without_opposite_fvg"]+=1
                    del self.originals[key]
                    continue
            if source.id not in self.raw.zones and not breach:
                del self.originals[key]
        for zone in made:
            if zone.kind=="OB":
                self.originals[zone.id]={"ob":zone}
                self.counts["original_ob"]+=1
        for gap in gaps:
            eligible=[s for s in self.originals.values() if "raid_time" not in s and "breach_time" not in s and s["ob"].direction==gap.direction and s["ob"].origin<=gap.origin
                      and (s["ob"].low<gap.high if gap.direction==1 else s["ob"].high>gap.low)]
            if eligible:
                source=max(eligible,key=lambda s:s["ob"].created)
                if "gap" not in source:self.counts["original_ob_with_fvg"]+=1
                source["gap"]=gap
        self.swings.confirm(bar)
        return result


class LectureMonitor(ShortMonitor):
    def __init__(self,cfg=DEFAULTS):
        super().__init__(cfg,CloseRangeSwings,True,VERSION)
        self.primary=LectureFlow()
        self.swings["4h"]=self.primary.swings
        self.registry=LectureRegistry()
        self.reviewed=set()

    def step(self,symbol,tf,bar):
        self.registry.flow=self.primary.flow
        events=super().step(symbol,tf,bar)
        self.registry.flow=self.primary.flow
        for event in events:
            event["lecture_profile"]=True
            event["liquidity_strength"]=self.primary.strength
            event["higher_target"]=self.primary.counter_target.price if self.primary.counter_target else None
            event["patterns"]=[self.registry.patterns[key] for key in event["overlap_sources"] if key in self.registry.patterns and self.registry.zones.get(key,{} ) and self.registry.zones[key].kind=="OB"]
        active=self.active
        if active and tf=="15m" and active["id"] not in self.reviewed and bar.timestamp>=active["formation"]:
            d=active["direction"]
            touched=bar.low<=active["zone_high"] and bar.high>=active["zone_low"]
            rejects=(bar.close>bar.open and bar.low<bar.open) if d==1 else (bar.close<bar.open and bar.high>bar.open)
            if touched and rejects and self.registry.atr is not None:
                stamp=bar.timestamp+TF_MS[tf]
                buffer=self.registry.atr*STOP_ATR_BUFFER
                stop=bar.low-buffer if d==1 else bar.high+buffer
                target=active["swing_target"]
                rr=d*(target-bar.close)/abs(bar.close-stop) if target is not None else 0
                bands_ok=active["ote_low"]<=bar.close<=active["ote_high"]
                for key in ("eq4h","eq1h"):
                    r=active[key]
                    bands_ok &= r["low"]<=bar.close<=r["midpoint"] if d==1 else r["midpoint"]<=bar.close<=r["high"]
                self.counts["reaction_confirmed"]+=1
                reason="missing_higher_target" if not self.primary.counter_target else "insufficient_rr" if rr<self.cfg.reward_risk_ratio else "reaction_outside_bands" if not bands_ok else None
                if reason:
                    self.counts["review_excluded:"+reason]+=1
                    events.append({**active,"stage":"cancelled","timestamp":stamp,"reason":reason})
                    self.active=None
                else:
                    plan=exit_plan(bar.close,stop,d,target,self.cfg.reward_risk_ratio)
                    review={**active,"stage":"review","timestamp":stamp,"entry_reference":bar.close,"reference":bar.close,
                            "stop":stop,"trade_target":plan["three_r"],"exit_plan":plan,"trade_eligible":True,
                            "liquidity_rr":rr,"rejection_bar":asdict(bar),"stop_buffer":buffer,"atr15":self.registry.atr}
                    events.append(review)
                    self.reviewed.add(active["id"])
                    self.counts["review"]+=1
        return events


def scan_lecture(symbol,frames,cfg=DEFAULTS):
    return scan_short(symbol,frames,cfg,monitor_factory=LectureMonitor)
