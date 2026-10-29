"""Additional commodity eligibility; never changes the ICT direction or 0.5.

These are initial screening thresholds, not a claim of permanent independence
or validated trading profitability. Core instruments do not use this gate.
"""
import math
import statistics
from src.ict_engine import TF_MS,frame_open

DAY=TF_MS['1d']


def return_series(bars,tf,now_ms):
    width=TF_MS[tf]
    bars=[b for b in bars if b.timestamp+width<=now_ms]
    return {bar.timestamp+width:math.log(bar.close/previous.close)
            for previous,bar in zip(bars,bars[1:])
            if bar.timestamp-previous.timestamp==width and bar.close>0 and previous.close>0
            and bar.volume>0 and previous.volume>0}


def correlation(left,right,days,now_ms):
    stamps=sorted(stamp for stamp in left.keys()&right.keys() if now_ms-days*DAY<stamp<=now_ms)
    x=[left[stamp] for stamp in stamps];y=[right[stamp] for stamp in stamps]
    if len(stamps)<3 or statistics.pstdev(x)==0 or statistics.pstdev(y)==0:
        return None,len(stamps)
    return statistics.correlation(x,y),len(stamps)


def diversification_gate(frames,btc_frames,turnovers,ticker,now_ms):
    proof={'eligible':False,'as_of':now_ms,'exchange':'bitget','reasons':[],
           'method':'simultaneous_closed_log_returns_30d_60d','correlations':{}}
    reasons=proof['reasons']
    for tf,limit,counts in [('4h',.3,{30:100,60:200}),('1d',.4,{30:20,60:40})]:
        bars=[b for b in frames.get(tf,[]) if b.timestamp+TF_MS[tf]<=now_ms]
        reference=[b for b in btc_frames.get(tf,[]) if b.timestamp+TF_MS[tf]<=now_ms]
        if (not bars or not reference or bars[-1].timestamp+TF_MS[tf]!=frame_open(now_ms,tf)
                or reference[-1].timestamp+TF_MS[tf]!=frame_open(now_ms,tf)
                or max(bars[0].timestamp,reference[0].timestamp)>frame_open(now_ms-60*DAY,tf)):
            reasons.append(f'{tf}_history_insufficient_or_stale')
            continue
        series=return_series(bars,tf,now_ms);btc=return_series(reference,tf,now_ms)
        for days in (30,60):
            rho,samples=correlation(btc,series,days,now_ms)
            proof['correlations'][f'{tf}_{days}d']={'rho':rho,'samples':samples,'max_abs_rho':limit}
            if rho is None or samples<counts[days]:reasons.append(f'{tf}_{days}d_samples_insufficient')
            elif abs(rho)>limit:reasons.append(f'{tf}_{days}d_correlation_high')
    rows=sorted((int(stamp),float(value)) for stamp,value in turnovers if stamp+DAY<=now_ms)[-30:]
    if (len(rows)!=30 or rows[-1][0]+DAY!=frame_open(now_ms,'1d')
            or any(b[0]-a[0]!=DAY for a,b in zip(rows,rows[1:]))
            or any(not math.isfinite(v) or v<0 for _,v in rows)):
        reasons.append('quote_turnover_history_incomplete')
    else:
        median30=statistics.median(v for _,v in rows)
        median7=statistics.median(v for _,v in rows[-7:])
        liquid_days=sum(v>=5e6 for _,v in rows)
        proof.update(median_daily_turnover_30d=median30,median_daily_turnover_7d=median7,days_above_5m_30d=liquid_days)
        if median30<5e6 or median7<5e6 or liquid_days<20:reasons.append('daily_turnover_not_persistent')
    bid,ask=float(ticker.get('bid1Price',0)),float(ticker.get('ask1Price',0))
    volume=float(ticker.get('turnover24h',0))
    spread=1e4*(ask-bid)/((ask+bid)/2) if 0<bid<=ask else None
    proof.update(turnover_24h_usdt=volume,spread_bps=spread)
    if not math.isfinite(volume) or volume<5e6:reasons.append('current_turnover_low')
    if spread is None or not math.isfinite(spread) or spread>10:reasons.append('spread_wide_or_missing')
    proof['eligible']=not reasons
    return proof
