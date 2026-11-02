"""Display live higher candles without using their close as confirmation."""
from src.ict_engine import Bar,TF_MS,frame_open


def aggregate(stamp,bars):
    return Bar(stamp,bars[0].open,max(b.high for b in bars),min(b.low for b in bars),bars[-1].close,sum(b.volume for b in bars))


def live_frames(frames,minutes,now,price):
    result={tf:list(bars) for tf,bars in frames.items()}
    end=now//60000*60000
    for tf in ('15m','1h','4h'):
        if tf not in result:continue
        start=frame_open(now,tf);values=[b for b in minutes if start<=b.timestamp and b.timestamp+60000<=end]
        if not values or values[0].timestamp!=start or values[-1].timestamp+60000!=end:continue
        if any(b.timestamp-a.timestamp!=60000 for a,b in zip(values,values[1:])):continue
        bar=aggregate(start,values)
        result[tf]=[b for b in result[tf] if b.timestamp<start]+[Bar(start,bar.open,max(bar.high,price),min(bar.low,price),price,bar.volume)]
    for tf,lower in (('1d','4h'),('1w','1d')):
        if tf not in result or lower not in result:continue
        start=frame_open(now,tf);values=[b for b in result[lower] if start<=b.timestamp<=now]
        if not values or values[0].timestamp!=start:continue
        if any(b.timestamp-a.timestamp!=TF_MS[lower] for a,b in zip(values,values[1:])):continue
        # Require a genuine lower partial through the current minute, rather
        # than fabricating the missing rest of today's/week's candle.
        if values[-1].timestamp!=frame_open(now,lower):continue
        result[tf]=[b for b in result[tf] if b.timestamp<start]+[aggregate(start,values)]
    return result
