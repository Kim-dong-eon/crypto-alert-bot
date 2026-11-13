"""Review-only two-scale 4H context. No alerts, orders or ledger writes.

Turns require a body-close reversal measured at the candidate's historical
ATR. Wicks locate extremes, but do not by themselves confirm a reversal.
Structural departure areas are NOT relabelled as OB/FVG; those retain the
existing Registry definitions and only actual intersections score out of 4.
"""
from dataclasses import asdict
from src.ict_engine import TF_MS, frame_open
from src.ict_overlap import Registry, overlaps, equilibrium_gate

SCALES = {'broad': {'atr_multiple':4., 'min_fraction':.06},
          'recent': {'atr_multiple':2., 'min_fraction':.025}}


def atrs(bars, period=14):
    result=[]
    for i,b in enumerate(bars):
        prev=bars[i-1].close if i else b.open
        tr=max(b.high-b.low,abs(b.high-prev),abs(b.low-prev))
        result.append(tr if not result else ((period-1)*result[-1]+tr)/period)
    return result


def turns(bars, multiple, fraction, timeframe='4h', close_times=None):
    """Append-only confirmed turns; no hindsight or terminal fake pivot."""
    atr=atrs(bars);out=[]
    if len(bars)<15:return out
    lo=hi=14;seeking=0
    prior_high=[];prior_low=[]
    for b in bars:
        prior_high.append(max(b.high,prior_high[-1]) if prior_high else b.high)
        prior_low.append(min(b.low,prior_low[-1]) if prior_low else b.low)
    for i in range(15,len(bars)):
        b=bars[i]
        # A new extreme cannot confirm itself on the same OHLC candle.
        if seeking in (0,-1) and b.low<bars[lo].low:lo=i
        if seeking in (0,1) and b.high>bars[hi].high:hi=i
        up=i>lo and b.close-bars[lo].low>=max(atr[lo]*multiple,bars[lo].low*fraction)
        down=i>hi and bars[hi].high-b.close>=max(atr[hi]*multiple,bars[hi].high*fraction)
        if seeking==0:
            # Do not invent a peak at the first warm-up candle of an
            # uninterrupted decline (or a floor in an uninterrupted rise).
            up=up and lo>0 and prior_high[lo-1]-bars[lo].low>=max(atr[lo]*multiple,bars[lo].low*fraction)
            down=down and hi>0 and bars[hi].high-prior_low[hi-1]>=max(atr[hi]*multiple,bars[hi].high*fraction)
        if seeking==0 and up and down:continue
        if seeking in (0,-1) and up:
            out.append({'kind':'low','index':lo,'price':bars[lo].low,
                        'confirmed_index':i,'confirmed_at':close_times[b.timestamp] if close_times is not None else b.timestamp+TF_MS[timeframe],
                        'threshold':max(atr[lo]*multiple,bars[lo].low*fraction)})
            seeking=1;hi=i
        elif seeking in (0,1) and down:
            out.append({'kind':'high','index':hi,'price':bars[hi].high,
                        'confirmed_index':i,'confirmed_at':close_times[b.timestamp] if close_times is not None else b.timestamp+TF_MS[timeframe],
                        'threshold':max(atr[hi]*multiple,bars[hi].high*fraction)})
            seeking=-1;lo=i
    return out


def departure_areas(bars, points, scale, timeframe='4h'):
    atr=atrs(bars);areas=[]
    for j,p in enumerate(points):
        i=p['index'];d=1 if p['kind']=='low' else -1
        # At most four contiguous base candles, from information already
        # available at the turn's origin. Stop when the base grows >1.5 ATR.
        base=[bars[i]]
        for b in reversed(bars[max(0,i-3):i]):
            proposed=[b]+base
            if max(x.high for x in proposed)-min(x.low for x in proposed)>1.5*atr[i]:break
            base=proposed
        low=min(b.low for b in base) if d==1 else min(min(b.open,b.close) for b in base)
        high=max(max(b.open,b.close) for b in base) if d==1 else max(b.high for b in base)
        if low>=high:continue
        invalid=next((b.timestamp+TF_MS[timeframe] for b in bars[p['confirmed_index']+1:]
                      if (b.close<low if d==1 else b.close>high)),None)
        following=points[j+1] if j+1<len(points) else None
        # A completed departure leg has its own fixed 0.5. For an unfinished
        # leg, a provisional endpoint advances only on a body close beyond
        # the previously observed wick extreme.
        end_index=following['index'] if following else p['confirmed_index']
        endpoint=bars[end_index].high if d==1 else bars[end_index].low
        range_known_at=following['confirmed_at'] if following else p['confirmed_at']
        if not following:
            extreme=endpoint
            for b in bars[end_index+1:]:
                if (b.close>extreme if d==1 else b.close<extreme):
                    endpoint=b.high if d==1 else b.low
                    range_known_at=b.timestamp+TF_MS[timeframe]
                extreme=max(extreme,b.high) if d==1 else min(extreme,b.low)
        bottom,top=sorted((p['price'],endpoint))
        areas.append({'id':f"{scale}:{bars[i].timestamp}:{d}",'scale':scale,
                      'direction':d,'low':low,'high':high,'origin':base[0].timestamp,
                      'pivot_origin':bars[i].timestamp,'created':p['confirmed_at'],
                      'invalidated_at':invalid,'range_low':bottom,'range_high':top,
                      'range_complete':following is not None,'range_known_at':range_known_at,'base_bars':len(base)})
    return areas


def liquidity_events(bars, points, timeframe='4h'):
    """Historical raids of already-confirmed turns for this particular scale.

    Body breakouts consume pools too, but are never labelled reclaimed sweeps.
    Future turns cannot become liquidity retroactively. Opposite structure is
    frozen at each raid; later turns never move its MSS target.
    """
    ready={}
    for p in points:ready.setdefault(p['confirmed_index'],[]).append(p)
    pools=[];known=[];events=[];pending=[]
    for i,b in enumerate(bars):
        crossed=[p for p in pools if (b.high>p['price'] if p['kind']=='high' else b.low<p['price'])]
        pools=[p for p in pools if p not in crossed]
        reclaimed=[p for p in crossed if (b.close<p['price'] if p['kind']=='high' else b.close>p['price'])]
        both=len({p['kind'] for p in crossed})>1
        if both:
            for e in pending:
                e.update(status='ambiguous_both_sides',ended_at=b.timestamp+TF_MS[timeframe])
            pending=[]
        elif reclaimed:
            p=max(reclaimed,key=lambda p:p['index'])
            d=1 if p['kind']=='low' else -1
            opposite=[q for q in known if q['kind']!=p['kind'] and q['index']>p['index']]
            structure=max(opposite,key=lambda q:q['index']) if opposite else None
            e={'direction':d,'index':i,'timestamp':b.timestamp+TF_MS[timeframe],
               'swept_kind':p['kind'],'level':p['price'],'origin_index':p['index'],
               'origin':bars[p['index']].timestamp,'target_confirmed_at':p['confirmed_at'],
               'tip':b.low if d==1 else b.high,'close':b.close,
               'mss_level':structure['price'] if structure else None,
               'mss_origin_index':structure['index'] if structure else None,
               'mss_index':None,'mss_at':None,'status':'sweep_only'}
            for old in pending:
                old.update(status='superseded_by_new_sweep',ended_at=e['timestamp'])
            pending=[e];events.append(e)
        remaining=[]
        for e in pending:
            d=e['direction']
            if (b.close<e['tip'] if d==1 else b.close>e['tip']):
                e.update(status='raid_tip_close_broken',ended_at=b.timestamp+TF_MS[timeframe])
            elif e['mss_level'] is not None and d*(b.close-e['mss_level'])>0:
                e.update(status='mss_confirmed',mss_index=i,mss_at=b.timestamp+TF_MS[timeframe])
            else:remaining.append(e)
        pending=remaining
        known.extend(ready.get(i,[]));pools.extend(ready.get(i,[]))
    return events


def analyze(frames,now,price,pair=('4h','1h'),symbol='BTCUSDT',require_ob_fvg=False,same_candle=False):
    top,lower=pair
    if same_candle:
        from src.anchored_overlap import TOP_BARS
        frames={tf:(values[-TOP_BARS[top]:] if tf==top else values) for tf,values in frames.items()}
    for tf in pair:
        bars=frames[tf]
        if not bars or bars[-1].timestamp+TF_MS[tf]!=frame_open(now,tf):
            raise ValueError('Fresh completed candles required')
        if any(b.timestamp+TF_MS[tf]>now for b in bars):raise ValueError('Forming candle')
        if any(b.timestamp-a.timestamp!=TF_MS[tf] for a,b in zip(bars,bars[1:])):
            raise ValueError('Gapped history')
    registry=Registry()
    for tf in pair:
        for b in frames[tf]:registry.step(tf,b)
    output={'as_of':now,'price':price,'exchange':'bitget','symbol':symbol,'pair':list(pair),
            'prototype':True,'policy':SCALES,'views':{},
            'frame_starts':{tf:frames[tf][0].timestamp for tf in pair},
            'source_zones':[asdict(z) for z in registry.zones.values()]}
    if same_candle:
        from src.anchored_overlap import anchored_overlaps
        output['anchor_audit']={}
        nested=anchored_overlaps(list(registry.zones.values()),pair,price,frames[lower],output['anchor_audit'])
    for scale,policy in SCALES.items():
        points=turns(frames[top],policy['atr_multiple'],policy['min_fraction'],top)
        areas=departure_areas(frames[top],points,scale,top)
        active=[a for a in areas if a['invalidated_at'] is None]
        # Keep the two nearest areas per side, independent of latest bias.
        selected=[]
        for d in (1,-1):
            choices=[a for a in active if a['direction']==d]
            choices.sort(key=lambda a:(max(a['low']-price,price-a['high'],0),-a['created']))
            for area in choices[:2]:
                z=[z for z in registry.zones.values() if z.direction==d and
                   z.low<area['high'] and z.high>area['low']]
                common=[]
                choices=(nested if same_candle else overlaps(z,pair,price))
                for c in choices:
                    if same_candle and c['sources'][0]['direction']!=d:continue
                    if require_ob_fvg and not {'OB','FVG'}.issubset({s['kind'] for s in c['sources']}):continue
                    low=max(c['low'],area['low']);high=min(c['high'],area['high'])
                    if low>=high:continue
                    gate=equilibrium_gate(low,high,price,d,area['range_low'],area['range_high'])
                    common.append({**c,'low':low,'high':high,'equilibrium':gate})
                common.sort(key=lambda c:(not c['equilibrium']['eligible'],-c['score'],
                                          max(c['low']-price,price-c['high'],0)))
                area={**area,'overlaps':common if require_ob_fvg else common[:3],
                      'midpoint':(area['range_low']+area['range_high'])/2}
                selected.append(area)
        highs=[p['price'] for p in points if p['kind']=='high']
        lows=[p['price'] for p in points if p['kind']=='low']
        state='mixed'
        if len(highs)>=2 and len(lows)>=2:
            if highs[-1]>highs[-2] and lows[-1]>lows[-2]:state='up'
            elif highs[-1]<highs[-2] and lows[-1]<lows[-2]:state='down'
        # A body break of the latest confirmed opposite turn is visible even
        # before an eventual next reversal confirms a new pivot.
        close=frames[top][-1].close
        if lows and close<lows[-1]:state='down'
        elif highs and close>highs[-1]:state='up'
        output['views'][scale]={'turns':points,'areas':areas,'selected':selected,'structure':state,
                                'sweeps':liquidity_events(frames[top],points,top)}
    return output
