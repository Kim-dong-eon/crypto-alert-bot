"""Closed-candle observation zones. No entry, OTE, RR or order execution."""
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
import hashlib
import math

from config.overlap_settings import TRACKS, TRACK_LABELS, MIN_HISTORY_BARS
from src.ict_engine import TF_MS, frame_open
from src.ict_nested import PDZone
from src.overlap_structure import ExternalSweepMSS

VERSION = "ict-two-frame-ob-fvg-v10-external"
LABELS = {"15m": "15분", "1h": "1시간", "4h": "4시간", "1d": "1일", "1w": "1주"}


class Registry:
    def __init__(self):
        self.zones = {}
        self.recent = {}

    def step(self, tf, bar):
        stamp = bar.timestamp + TF_MS[tf]
        for key, z in list(self.zones.items()):
            if z.timeframe == tf and z.direction * (bar.close - z.invalidation) < 0:
                del self.zones[key]
        recent = self.recent.get(tf, [])
        made = []
        if recent:
            prev = recent[-1]
            d = 1 if prev.close < prev.open and bar.close > bar.open and bar.close > prev.open else -1 if prev.close > prev.open and bar.close < bar.open and bar.close < prev.open else 0
            if d:
                lo, hi = sorted((prev.open, prev.close))
                stop = min(prev.low, bar.low) if d == 1 else max(prev.high, bar.high)
                made.append(PDZone(f"{tf}:OB:{prev.timestamp}:{stamp}:{d}", tf, "OB", d, lo, hi, stamp, prev.timestamp, stop, lo if d == 1 else hi))
        if len(recent) == 2:
            left = recent[0]
            d = 1 if bar.low > left.high else -1 if bar.high < left.low else 0
            if d:
                lo, hi = (left.high, bar.low) if d == 1 else (bar.high, left.low)
                boundary = lo if d == 1 else hi
                made.append(PDZone(f"{tf}:FVG:{left.timestamp}:{stamp}:{d}", tf, "FVG", d, lo, hi, stamp, left.timestamp, boundary, boundary))
        for z in made:
            if z.low < z.high:
                self.zones[z.id] = z
        self.recent[tf] = (recent + [bar])[-2:]
        return made


def overlaps(zones, pair, anchor):
    """Actual common-price segments, unique TF/type evidence, max score four."""
    edges = sorted({x for z in zones for x in (z.low, z.high)})
    segments = []
    for low, high in zip(edges, edges[1:]):
        center = (low + high) / 2
        active = [z for z in zones if z.low < center < z.high]
        groups = {(z.timeframe, z.kind) for z in active}
        if not set(pair).issubset({tf for tf, _ in groups}):
            continue
        sources = [max((z for z in active if (z.timeframe, z.kind) == group), key=lambda z: (z.created, z.id)) for group in sorted(groups)]
        identifiers = [z.id for z in sources]
        if segments and segments[-1]["high"] == low and segments[-1]["source_ids"] == identifiers:
            segments[-1]["high"] = high
        else:
            segments.append({"low": low, "high": high, "score": len(groups), "groups": [f"{tf}:{kind}" for tf, kind in sorted(groups)],
                             "source_ids": identifiers, "sources": [asdict(z) for z in sources], "latest_source": max(z.created for z in sources)})
    return sorted(segments, key=lambda s: (-s["score"], max(s["low"]-anchor, anchor-s["high"], 0), -s["latest_source"], -(s["high"]-s["low"]), s["low"]))


def equilibrium_gate(low,high,price,direction,bottom,top):
    if direction not in (-1,1) or not all(math.isfinite(x) for x in (low,high,price,bottom,top)) or bottom>=top or low>=high:
        return {'midpoint':None,'eligible':False}
    mid=(bottom+top)/2
    eligible=(low<mid and low<=price<=mid) if direction==1 else (high>mid and mid<=price<=high)
    return {'midpoint':mid,'eligible':eligible,'range_low':bottom,'range_high':top,
            'distance_to_zone':max(low-price,price-high,0.)}


def alert_eligible(candidate):
    return bool(candidate and not candidate['context_price_breach']
                and candidate.get('equilibrium',{}).get('eligible')
                and candidate.get('monitor_eligible',True))


def scan(symbol, frames, now_ms, price, tracks=None,equilibrium_filter=False):
    tracks = TRACKS if tracks is None else tracks
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Invalid current price")
    primary = {pair[0] for pair in tracks.values()}
    flows = {tf: ExternalSweepMSS(tf,close_ranges=equilibrium_filter) for tf in primary}
    registry = Registry()
    usable = {}
    unavailable = {}
    for tf in {tf for pair in tracks.values() for tf in pair}:
        values = frames.get(tf, [])
        if not values or len(values) < MIN_HISTORY_BARS:
            unavailable[tf] = "history_insufficient"
            continue
        if values[-1].timestamp + TF_MS[tf] != frame_open(now_ms, tf):
            unavailable[tf] = "latest_closed_bar_missing"
            continue
        for previous, bar in zip(values, values[1:]):
            if bar.timestamp != previous.timestamp + TF_MS[tf]:
                raise ValueError("Gapped candles")
        if any(b.timestamp + TF_MS[tf] > now_ms for b in values):
            raise ValueError("Unclosed candle in structure")
        usable[tf] = values
    # Each timeframe's state changes only on its own closed candles.
    for tf, values in usable.items():
        for bar in values:
            if tf in flows:
                flows[tf].step(bar)
            registry.step(tf, bar)
    output = {"version": VERSION, "exchange": "bitget", "symbol": symbol, "as_of": now_ms, "price": price,
              "tracks": {}, "valid_source_ids": sorted(registry.zones), "unavailable_frames": unavailable,
              "history_starts":{tf:values[0].timestamp for tf,values in usable.items()}}
    for name, pair in tracks.items():
        top, lower = pair
        state = {"pair": list(pair), "candidate": None, "direction": 0, "resistance_support": [], "context": None}
        output["tracks"][name] = state
        if any(tf not in usable for tf in pair):
            state["status"] = "data_unavailable"
            continue
        flow = flows[top].flow
        state["liquidity"] = flows[top].diagnostics()
        if flow is None:
            state["pending_sweep"] = flows[top].pending
            state["status"] = "sweep_confirmed_mss_waiting" if flows[top].pending else "sweep_mss_unconfirmed"
            continue
        state.update(direction=flow.direction, context=asdict(flow))
        # Scope zones to the current upper sweep's opening, without arbitrary
        # 8/16bar deadlines. Prior unrelated phases do not inflate confluence.
        since = flow.sweep_time - TF_MS[top]
        zones = [z for z in registry.zones.values() if z.timeframe in pair and z.direction == flow.direction and z.created >= since]
        relevant = [z for z in zones if (z.low <= usable[lower][-1].close if flow.direction == 1 else z.high >= usable[lower][-1].close)]
        choices = overlaps(relevant, pair, usable[lower][-1].close)
        if equilibrium_filter:
            for choice in choices:
                choice['equilibrium']=equilibrium_gate(choice['low'],choice['high'],price,flow.direction,flow.swing_low,flow.swing_high)
            choices.sort(key=lambda choice:not choice['equilibrium']['eligible'])
        state["status"] = "no_overlap" if not choices else "watching"
        state["alternatives"] = choices[1:3]
        opponents = [z for z in registry.zones.values() if z.timeframe == top and z.direction == -flow.direction and (z.high > price if flow.direction == 1 else z.low < price)]
        opponents.sort(key=lambda z: (max(z.low-price, price-z.high, 0), -z.created))
        state["resistance_support"] = [asdict(z) for z in opponents[:2]]
        if choices:
            best = choices[0]
            if equilibrium_filter and not best['equilibrium']['eligible']:state['status']='waiting_eq50'
            raw = f"{VERSION}|{symbol}|{name}|{flow.id}|{best['low']:.12g}|{best['high']:.12g}|{'|'.join(best['source_ids'])}"
            location = "inside" if best["low"] <= price <= best["high"] else "waiting" if flow.direction*(price-(best["high"] if flow.direction==1 else best["low"])) > 0 else "passed"
            state["candidate"] = {**best, "id": hashlib.sha256(raw.encode()).hexdigest()[:24], "track": name, "symbol": symbol,
                                  "direction": flow.direction, "context_id": flow.id, "context": asdict(flow), "pair": list(pair),
                                  "exchange": "bitget", "structure_proof": flows[top].flow_proof,
                                  "formed_at": max(flow.formed, best["latest_source"]), "location": location,
                                  "price": price, "context_price_breach": flow.direction*(price-flow.invalidation) < 0,
                                  "resistance_support": state["resistance_support"]}
    return output


def kst(stamp):
    return datetime.fromtimestamp(stamp/1000, timezone.utc).astimezone(timezone(timedelta(hours=9))).strftime("%m/%d %H:%M KST")


def format_notice(event):
    c = event["candidate"]
    title = {"prepared": "중첩 구간 발생" if event["new_formation"] else "현재 유효 중첩 구간", "touched": "중첩 구간 도달", "cancelled": "관찰 구간 종료", "caution": "상위 무효화 가격 침범"}[event["stage"]]
    lines = [f"[ICT {TRACK_LABELS[c['track']]} · {title}] {event.get('display_name', c['symbol'])}",
             f"{' → '.join(LABELS[tf] for tf in c['pair'])} · {'LONG 눌림' if c['direction']==1 else 'SHORT 되돌림'} 관찰",
             f"확인 {kst(event['timestamp'])} / 구간 확정 {kst(c['formed_at'])}",
             f"겹침 가격대: {c['low']:,.4f} ~ {c['high']:,.4f} USDT",
             f"근거 {c['score']}/4: {', '.join(c['groups'])}"]
    for z in c["sources"]:
        lines.append(f"{LABELS[z['timeframe']]} {z['kind']}: {z['low']:,.4f} ~ {z['high']:,.4f}")
    flow = c["context"]
    lines += [f"상위 스윕: {flow['swept_level']:,.4f} / MSS 종가 돌파: {flow['structure_level']:,.4f}", f"현재가: {c['price']:,.4f} USDT"]
    for z in c.get("resistance_support", []):
        lines.append(f"상위 {'저항' if c['direction']==1 else '지지'} {z['kind']}: {z['low']:,.4f} ~ {z['high']:,.4f}")
    if event["stage"] == "cancelled":
        lines.append("종료: " + event["reason"])
    elif c["context_price_breach"]:
        lines.append("상위 무효화 가격 침범 중 · 해당 시간대 종가 확정 대기")
    else:
        lines.append({"waiting": "가격 재방문 대기", "inside": "현재가가 관찰 구간 안에 있음", "passed": "현재가가 구간을 통과함 · 해당 시간대 종가 확인 필요"}[c["location"]])
    lines.append("구간 관찰 알림 · 자동 진입·주문 없음")
    return "\n".join(lines)


def compact_notice(events):
    """One symbol, only actual overlap scopes, without lifecycle narration."""
    ordered=sorted(events,key=lambda e:{'short':0,'medium':1,'long':2}.get(e['candidate']['track'],9))
    if not ordered:raise ValueError('No overlap events')
    symbols={e['candidate']['symbol'] for e in ordered}
    if len(symbols)!=1:raise ValueError('One symbol per notice')
    number=lambda x:f'{x:,.2f}'.rstrip('0').rstrip('.')
    venue=ordered[0]['candidate'].get('exchange')
    lines=[f"[{(venue.title()+' · ') if venue else ''}{ordered[0]['candidate']['symbol']}]",'']
    if ordered[0]['candidate'].get('equilibrium'):
        lines[0]+=f" 현재가 {number(ordered[0]['candidate']['price'])}"
    for event in ordered:
        c=event['candidate'];tf_order={tf:i for i,tf in enumerate(c['pair'])}
        groups={(z['timeframe'],z['kind']) for z in c['sources']}
        if not groups:groups={tuple(x.split(':',1)) for x in c['groups']}
        evidence=' + '.join(f"{tf.upper()} {kind}" for tf,kind in sorted(groups,key=lambda x:(tf_order.get(x[0],9),x[1]!='OB')))
        proof=f"근거: {evidence}"
        if c.get('equilibrium',{}).get('midpoint') is not None:proof+=f" / 0.5 {number(c['equilibrium']['midpoint'])}"
        lines += [f"{TRACK_LABELS[c['track']]} 겹침 · {'강세(LONG)' if c['direction']==1 else '약세(SHORT)'}",f"{number(c['low'])} ~ {number(c['high'])}",proof,'']
    lines.append(kst(max(e['timestamp'] for e in ordered)))
    return '\n'.join(lines)
