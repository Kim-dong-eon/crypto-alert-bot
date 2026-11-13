"""Causal staircases, OTE and estimated net reward/risk for observations."""
from src.ict_engine import TF_MS
from src.structure_review import atrs, turns
from src.overlap_entry import MINUTE

RULESET = 'nested-staircase-ote-3r-v1'
# Conservative estimates, not a claim about the user's fee tier or actual fills.
FEE_PER_SIDE = .0006
SLIPPAGE_PER_SIDE = .0002
STOP_ATR_BUFFER = .10
MIN_RR = 3.


def staircases(bars, points, tf, direction, close_times=None):
    """Four confirmed alternating turns: higher low + close HH, or inverse.

    A target is an already confirmed extreme. Prior bodies must break the
    previous wick; a wick-only raid is never a continuation confirmation.
    """
    result=[];kind='low' if direction==1 else 'high';step=TF_MS[tf]
    close_at = lambda b: close_times[b.timestamp] if close_times is not None else b.timestamp + step
    for j in range(2,len(points)-1):
        a,b,p,q=points[j-2:j+2]
        if [v['kind'] for v in (a,b,p,q)] != [kind,('high' if direction==1 else 'low'),kind,('high' if direction==1 else 'low')]:continue
        if direction*(p['price']-a['price'])<=0 or direction*(q['price']-b['price'])<=0:continue
        broken=next((x for x in bars[p['index']+1:q['index']+1]
                     if close_at(x)>=max(p['confirmed_at'],b['confirmed_at'])
                     and direction*(x.close-b['price'])>0),None)
        if broken is None:continue
        tagged=[{**v,'timestamp':bars[v['index']].timestamp} for v in (a,b,p,q)]
        known=max(q['confirmed_at'],p['confirmed_at'],close_at(broken))
        invalid=next((close_at(x) for x in bars[p['index']+1:]
                      if close_at(x)>=known and direction*(x.close-p['price'])<0),None)
        result.append({'direction':direction,'timeframe':tf,'points':tagged,
                       'origin':tagged[2]['timestamp'],'end':tagged[3]['timestamp'],
                       'chart_start':tagged[0]['timestamp'],'known_at':known,
                       'break_at':close_at(broken),'break_level':b['price'],
                       'protected':p['price'],'target':q['price'],'invalidated_at':invalid})
    return result


def ote_band(stair):
    low,high=sorted((stair['protected'],stair['target']));width=high-low
    if stair['direction']==1:return high-.79*width,high-.62*width
    return low+.62*width,low+.79*width


def net_rr(entry,stop,target,direction,*,slippage=SLIPPAGE_PER_SIDE):
    buy=(1+slippage)*(1+FEE_PER_SIDE)
    sell=(1-slippage)*(1-FEE_PER_SIDE)
    if direction==1:
        risk=entry*buy-stop*sell
        reward=target*sell-entry*buy
    else:
        risk=stop*buy-entry*sell
        reward=entry*sell-target*buy
    return reward/risk if risk>0 and direction*(entry-stop)>0 else -1.


def risk_band(low,high,stop,target,direction,*,slippage=SLIPPAGE_PER_SIDE):
    buy=(1+slippage)*(1+FEE_PER_SIDE)
    sell=(1-slippage)*(1-FEE_PER_SIDE)
    if direction==1:high=min(high,(target+MIN_RR*stop)*sell/((1+MIN_RR)*buy))
    else:low=max(low,(target+MIN_RR*stop)*buy/((1+MIN_RR)*sell))
    return low,high


def band_touch(low,high,known,price,now,minutes,since):
    result={'hit':False,'band_low':low,'band_high':high,'valid_from':known}
    if low>=high:return result
    if now>=known and low<=price<=high:
        return {**result,'hit':True,'at':now,'observed_price':price,'evidence':'current_price'}
    for b in reversed(minutes):
        if b.timestamp>=max(since,known) and b.timestamp+MINUTE<=now and b.volume>0 and b.low<=high and b.high>=low:
            return {**result,'hit':True,'at':b.timestamp+MINUTE,'bar_open':b.timestamp,
                    'observed_low':b.low,'observed_high':b.high,'evidence':'completed_1m_range'}
    return result


def evaluate(candidate,upper_bars,upper_points,lower_bars,now,price,minutes=(),since=0,lower_points=None):
    """Return a rejected reason or complete, auditable alert calculation."""
    d=candidate['direction'];top=candidate['pair'][0];tf=candidate['refinement_tf']
    stairs=staircases(upper_bars,upper_points,top,d)
    root=candidate['structure_area']['pivot_origin']
    upper=next((s for s in stairs if s['origin']==root),None)
    if not upper:return {'eligible':False,'reason':'upper_staircase_unconfirmed'}
    # Later protected steps cannot be ignored by falling back to an older OB.
    relevant=[s for s in stairs if s['origin']>=root]
    if any(s['invalidated_at'] for s in relevant):return {'eligible':False,'reason':'upper_staircase_broken'}
    if any(b.timestamp>=upper['known_at'] and (b.high>=upper['target'] if d==1 else b.low<=upper['target'])
           for b in upper_bars):return {'eligible':False,'reason':'upper_target_already_revisited'}
    protected=relevant[-1]['protected']
    if d*(price-protected)<0:return {'eligible':False,'reason':'upper_live_breach'}
    ote_low,ote_high=ote_band(upper)
    low=max(candidate['low'],ote_low);high=min(candidate['high'],ote_high)
    if low>=high:return {'eligible':False,'reason':'outside_ote'}
    # Lower thresholds follow local volatility rather than a 2.5% floor.
    points=lower_points if lower_points is not None else turns(lower_bars,1.5,0.,tf)
    lower_stairs=[s for s in staircases(lower_bars,points,tf,d) if s['origin']>=upper['end']]
    if not lower_stairs:return {'eligible':False,'reason':'lower_staircase_unconfirmed'}
    lower=lower_stairs[-1]
    if lower['invalidated_at'] or d*(price-lower['protected'])<0:
        return {'eligible':False,'reason':'lower_staircase_broken'}
    if not any(z['timeframe']==tf and lower['origin']-2*TF_MS[tf]<=z['origin']<=lower['end']
               for z in candidate['sources']):return {'eligible':False,'reason':'lower_source_not_in_staircase'}
    atr=atrs(lower_bars)[lower['points'][2]['index']]
    stop=lower['protected']-d*STOP_ATR_BUFFER*atr;target=upper['target']
    low,high=risk_band(low,high,stop,target,d)
    if low>=high:return {'eligible':False,'reason':'reward_risk_below_three'}
    entry=high if d==1 else low # Worst permissible entry in the notified band.
    rr=net_rr(entry,stop,target,d)
    if rr<MIN_RR-1e-9 or d*(entry-stop)<=0:return {'eligible':False,'reason':'invalid_risk_band'}
    known=max(candidate['formed_at'],candidate['latest_source'],upper['known_at'],lower['known_at'])
    touch=band_touch(low,high,known,price,now,minutes,since)
    return {'eligible':touch['hit'],'reason':'qualified' if touch['hit'] else 'waiting_for_retest',
            'upper':upper,'lower':lower,'ote_low':ote_low,'ote_high':ote_high,
            'low':low,'high':high,'entry':entry,'stop':stop,'target':target,'rr':rr,
            'fee_per_side':FEE_PER_SIDE,'slippage_per_side':SLIPPAGE_PER_SIDE,
            'stop_atr_buffer':STOP_ATR_BUFFER,'touch':touch,'known_at':known}


def opposing_upper_contact(candidate,frames,zones,now,price,minutes=()):
    """Hold continuation at an intact opposing OB on a higher timeframe.

    Only contacts after the selected staircase was known count. Registry's
    own-timeframe close invalidation releases a broken higher OB next scan.
    """
    d=candidate['direction'];top=candidate['pair'][0];s=candidate['trade_setup']
    start=s['upper']['known_at']
    for z in zones:
        if z.kind!='OB' or z.direction==d or TF_MS[z.timeframe]<=TF_MS[top]:continue
        # Only obstacles ahead of this staircase's origin, not behind it.
        if d==1 and z.high<=s['upper']['protected']:continue
        if d==-1 and z.low>=s['upper']['protected']:continue
        known=max(start,z.created)
        bars=frames.get(z.timeframe,[])
        hit=any(b.timestamp>=known and b.timestamp+TF_MS[z.timeframe]<=now
                and b.low<=z.high and b.high>=z.low for b in bars)
        hit=hit or any(b.timestamp>=known and b.timestamp+MINUTE<=now and b.volume>0
                      and b.low<=z.high and b.high>=z.low for b in minutes)
        if hit or (now>=known and z.low<=price<=z.high):return z.id
    return None
