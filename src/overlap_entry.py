"""Actual mixed OB/FVG entries; one-minute bars are price evidence only."""
import math
from src.ict_engine import Bar

MINUTE=60000
ENTRY_POLICY='mixed-ob-fvg-touch-v1'


def mixed_evidence(choice):
    if choice.get('sources'):
        return {'OB','FVG'}.issubset({z['kind'] for z in choice['sources']})
    return {'OB','FVG'}.issubset({g.split(':',1)[1] for g in choice.get('groups',[])})


def entry_touch(choice,area,price,now_ms,minute_bars=(),since=0):
    """Intersect the real common box with its own causal 0.5 half-range."""
    mid=(area['range_low']+area['range_high'])/2
    low,high=choice['low'],choice['high']
    if area['direction']==1:high=min(high,mid)
    else:low=max(low,mid)
    known=max(area['created'],area.get('range_known_at',area['created']),choice['latest_source'])
    result={'hit':False,'midpoint':mid,'band_low':low,'band_high':high,'valid_from':known}
    if low>high or not mixed_evidence(choice):return result
    if now_ms>=known and low<=price<=high:
        return {**result,'hit':True,'at':now_ms,'evidence':'current_price','observed_price':price}
    start=max(since,known)
    for b in reversed(minute_bars):
        if (b.timestamp>=start and b.timestamp+MINUTE<=now_ms and b.volume>0
                and b.low<=high and b.high>=low):
            return {**result,'hit':True,'at':b.timestamp+MINUTE,'bar_open':b.timestamp,
                    'evidence':'completed_1m_range','observed_low':b.low,'observed_high':b.high}
    return result


def recent_minutes(market,symbol,since,now_ms):
    """Completed, contiguous price bars since the previous examined minute.

    At most twelve hours are recovered after an outage. Older missing coverage
    is reported explicitly rather than silently claimed to have been checked.
    """
    end=now_ms//MINUTE*MINUTE
    requested=since//MINUTE*MINUTE
    start=max(requested,end-720*MINUTE)
    limit=min(1000,(end-start)//MINUTE+2)
    response=market.get('candles',category='USDT-FUTURES',symbol=symbol,interval='1m',limit=str(limit),endTime=str(end))
    values={}
    for row in response['data']:
        t=int(row[0]);o,h,l,c,v=[float(x) for x in row[1:6]]
        if not all(math.isfinite(x) for x in (o,h,l,c,v)) or min(o,h,l,c)<=0 or v<0:
            raise ValueError('Invalid minute prices')
        if t%MINUTE or l>min(o,c) or h<max(o,c) or l>h:raise ValueError('Invalid minute candle')
        if start<=t<end:
            if t in values:raise ValueError('Duplicate minute candle')
            values[t]=Bar(t,o,h,l,c,v)
    bars=[values[t] for t in sorted(values)]
    if end>start and (not bars or bars[0].timestamp!=start or bars[-1].timestamp+MINUTE!=end
                      or any(b.timestamp-a.timestamp!=MINUTE for a,b in zip(bars,bars[1:]))):
        raise ValueError('Minute touch coverage incomplete')
    return bars,{'start':start,'end':end,'requested_start':requested,'truncated':start>requested,'bars':len(bars)}
