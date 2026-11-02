"""Rooted upper OB pullbacks. Observation alerts, not automated entries.

An OB must belong to a structural departure/base, rather than merely being
near the latest price. Still-valid lower evidence from that original base is
retained. A new sweep/MSS at the retest is descriptive, never a required gate.
"""
from dataclasses import asdict
import hashlib
import math
from config.overlap_settings import TRACKS
from src.ict_engine import TF_MS,frame_open
from src.ict_overlap import Registry,VERSION,overlaps,alert_eligible
from src.structure_review import turns,departure_areas,liquidity_events,SCALES
from src.overlap_entry import entry_touch

POLICY='root-ob-pullback-v1'
TOP_COUNTS={'4h':1080,'1d':1102,'1w':156}


def rooted_context(parent,bars,points,areas,tf,now):
    """Assign the OB its OWN origin wave, not the latest unrelated wave."""
    d=parent.direction;kind='low' if d==1 else 'high'
    area_by_pivot={(a['pivot_origin'],a['direction']):a for a in areas}
    eligible=[]
    for i,p in enumerate(points):
        if p['kind']!=kind:continue
        pivot=bars[p['index']].timestamp;a=area_by_pivot.get((pivot,d))
        if pivot<=parent.origin or (a and a['origin']<=parent.origin and parent.created>=pivot):eligible.append((i,p))
    if not eligible:return None
    j,p=eligible[-1]
    area=area_by_pivot.get((bars[p['index']].timestamp,d))
    if area is None:return None
    following=points[j+1] if j+1<len(points) else None
    if following and parent.origin>bars[following['index']].timestamp:return None
    previous=[q for q in points[:j] if q['kind']!=kind]
    bos=None;level=None
    if previous:
        q=previous[-1];level=q['price']
        for i in range(p['index']+1,len(bars)):
            stamp=bars[i].timestamp+TF_MS[tf]
            if stamp<q['confirmed_at']:continue
            if d*(bars[i].close-level)>0 and d*(bars[i-1].close-level)<=0:
                bos=stamp;break
    # A pre-break OB is the origin of that break. A base OB is also allowed
    # before a new break; it must overlap the extreme half of the pivot body.
    base=bars[p['index']]
    if d==1:base_low,base_high=base.low,(base.low+max(base.open,base.close))/2
    else:base_low,base_high=(base.high+min(base.open,base.close))/2,base.high
    at_base=max(parent.low,base_low)<min(parent.high,base_high)
    causes_break=bos is not None and parent.created<=bos
    if not (at_base or causes_break):return None
    if area['range_low']>=area['range_high']:return None
    origin=min(area['origin'],parent.origin)
    known=max(parent.created,p['confirmed_at'],bos if causes_break else 0)
    if known>now:return None
    return {**area,'id':f"root:{tf}:{parent.id}",'low':parent.low,'high':parent.high,
            'origin':origin,'created':known,'pivot_origin':bars[p['index']].timestamp,
            'parent_id':parent.id,'bos_at':bos if causes_break else None,'bos_level':level,
            'basis':'body_break_origin' if causes_break else 'pivot_base',
            'invalidated_at':None}


def prepare(symbol,frames,now,price,tracks=None):
    """Closed-bar causal context; returned views also drive the chart."""
    if not math.isfinite(price) or price<=0:raise ValueError('Invalid price')
    tracks=TRACKS if tracks is None else tracks
    usable={};unavailable={};registry=Registry()
    for tf,values in frames.items():
        values=[b for b in values if b.timestamp+TF_MS[tf]<=now]
        if not values or values[-1].timestamp+TF_MS[tf]!=frame_open(now,tf):
            unavailable[tf]='latest_closed_bar_missing';continue
        if any(b.timestamp-a.timestamp!=TF_MS[tf] for a,b in zip(values,values[1:])):raise ValueError('Gapped history')
        usable[tf]=values
        for b in values:registry.step(tf,b)
    result={'version':VERSION,'strategy':POLICY,'alert_policy':POLICY,
            'symbol':symbol,'exchange':'bitget','price':price,'as_of':now,'tracks':{},
            'valid_zones':[asdict(z) for z in registry.zones.values()],
            'valid_source_ids':sorted(registry.zones),
            'history_starts':{tf:bars[0].timestamp for tf,bars in usable.items()},
            'unavailable_frames':unavailable,'deduplication_enabled':False}
    zones=list(registry.zones.values())
    for name,pair in tracks.items():
        top,lower=pair
        state={'pair':list(pair),'candidate':None,'candidates':[],'status':'data_unavailable','direction':0,'context':None}
        result['tracks'][name]=state
        if any(tf not in usable or len(usable[tf])<16 for tf in pair):continue
        bars=usable[top][-TOP_COUNTS[top]:]
        analysis={'pair':list(pair),'frame_starts':{top:bars[0].timestamp,lower:usable[lower][0].timestamp},
                  'source_zones':[asdict(z) for z in zones if z.timeframe in pair],
                  'views':{},'as_of':now,'price':price}
        state['analysis']=analysis
        parents=[z for z in zones if z.timeframe==top and z.kind=='OB']
        for lens,policy in SCALES.items():
            points=turns(bars,policy['atr_multiple'],policy['min_fraction'],top)
            areas=departure_areas(bars,points,lens,top)
            view={'turns':points,'areas':areas,'selected':[],'sweeps':liquidity_events(bars,points,top),'structure':'mixed'}
            highs=[p['price'] for p in points if p['kind']=='high'];lows=[p['price'] for p in points if p['kind']=='low']
            if len(highs)>=2 and len(lows)>=2:
                if highs[-1]>highs[-2] and lows[-1]>lows[-2]:view['structure']='up'
                elif highs[-1]<highs[-2] and lows[-1]<lows[-2]:view['structure']='down'
            if lows and bars[-1].close<lows[-1]:view['structure']='down'
            elif highs and bars[-1].close>highs[-1]:view['structure']='up'
            analysis['views'][lens]=view
            for parent in parents:
                area=rooted_context(parent,bars,points,areas,top,now)
                if area is None:continue
                # Base context predates the current retest. Include the two
                # preceding lower bars needed to define a boundary FVG.
                floor=area['origin']-2*TF_MS[lower]
                children=[z for z in zones if z.timeframe==lower and z.direction==parent.direction
                          and z.origin>=floor and z.low<parent.high and z.high>parent.low]
                helpers=[z for z in zones if z.timeframe==top and z.kind=='FVG' and z.direction==parent.direction
                         and z.low<parent.high and z.high>parent.low]
                choices=[]
                for choice in overlaps([parent]+helpers+children,pair,price):
                    if parent.id not in choice['source_ids']:continue
                    choices.append({**choice,'parent':asdict(parent),'structure_area':area,
                                    'lens':lens,'direction':parent.direction})
                if choices:view['selected'].append({**area,'overlaps':choices})
        state['status']='no_overlap'
    return result,usable,zones


def short_history_start(symbol,frames,now,price,minute_bars=(),touch_since=0):
    """Fetch old 15m evidence only for rooted 4H/1H boxes touched now."""
    prepared,_,_=prepare(symbol,frames,now,price,{'short':TRACKS['short']})
    starts=[]
    state=prepared['tracks']['short']
    for view in state.get('analysis',{}).get('views',{}).values():
        for area in view['selected']:
            for c in area['overlaps']:
                # Pre-screen physical touch without imposing mixed evidence:
                # the missing FVG may be supplied by the 15m refinement.
                probe={**c,'sources':c['sources']+[{'kind':'FVG'}]}
                if entry_touch(probe,area,price,now,minute_bars,touch_since)['hit']:
                    starts.append(area['origin']-2*TF_MS['15m'])
    return min(starts) if starts else None


def scan(symbol,frames,now_ms,price,tracks=None,equilibrium_filter=True,minute_bars=(),touch_since=0):
    result,usable,zones=prepare(symbol,frames,now_ms,price,tracks)
    for name,state in result['tracks'].items():
        if 'analysis' not in state:continue
        found=[];top,lower=state['pair']
        for lens,view in state['analysis']['views'].items():
            for area in view['selected']:
                choices=[]
                for raw in area['overlaps']:
                    refinements=[]
                    if name=='short':
                        # A short alert includes real 15m evidence from this
                        # root, rather than another arbitrary recent box.
                        quarter=usable.get('15m',[])
                        required=area['origin']-2*TF_MS['15m']
                        if not quarter or quarter[0].timestamp>required:continue
                        for z in zones:
                            if z.timeframe!='15m' or z.direction!=raw['direction'] or z.origin<required:continue
                            low=max(raw['low'],z.low);high=min(raw['high'],z.high)
                            if low>=high:continue
                            all_sources=raw['sources']+[asdict(z)]
                            if not {'OB','FVG'}.issubset(s['kind'] for s in all_sources):continue
                            refinements.append({**raw,'low':low,'high':high,'sources':all_sources,
                                                'source_ids':raw['source_ids']+[z.id],
                                                'latest_source':max(raw['latest_source'],z.created),
                                                'refinement_tf':'15m'})
                    elif {'OB','FVG'}.issubset(s['kind'] for s in raw['sources']):refinements=[raw]
                    for choice in refinements:
                        touch=entry_touch(choice,area,price,now_ms,minute_bars,touch_since)
                        eq={'midpoint':touch['midpoint'],'eligible':touch['hit'],'entry_required':True,
                            'range_low':area['range_low'],'range_high':area['range_high']}
                        choices.append({**choice,'equilibrium':eq,'entry_touch':touch})
                choices.sort(key=lambda c:(not c['entry_touch']['hit'],-c['score'],max(c['low']-price,price-c['high'],0)))
                if not choices:continue
                choice=choices[0];d=choice['direction']
                raw_id=f"{POLICY}|{symbol}|{name}|{lens}|{'|'.join(choice['source_ids'])}|{choice['low']:.12g}|{choice['high']:.12g}"
                identifier=hashlib.sha256(raw_id.encode()).hexdigest()[:24]
                context_id=f"root:{top}:{choice['parent']['id']}:{lens}"
                c={**choice,'id':identifier,'zone_id':identifier,'alert_policy':POLICY,
                   'symbol':symbol,'track':name,'pair':list(state['pair']),'exchange':'bitget',
                   'lenses':[lens],'price':price,'context_id':context_id,'context':{'id':context_id},
                   'formed_at':max(area['created'],choice['latest_source']),
                   'structure_states':{key:v['structure'] for key,v in state['analysis']['views'].items()},
                   # Confirmed source invalidation is handled by Registry.
                   # A transient excursion after touching must still notify.
                   'context_price_breach':False,
                   'location':'inside' if choice['low']<=price<=choice['high'] else 'outside_after_touch' if choice['entry_touch']['hit'] else 'waiting'}
                found.append(c)
        found.sort(key=lambda c:(not alert_eligible(c),max(c['low']-price,price-c['high'],0),-c['score']))
        state.update(status='watching' if found else 'no_overlap',candidates=found,candidate=found[0] if found else None)
    return result


def unit_text(event):
    from src.ict_overlap import LABELS,kst
    from config.overlap_settings import TRACK_LABELS
    c=event['candidate'];name='큰 틀' if c['lenses'][0]=='broad' else '작은 틀'
    side='지지 LONG' if c['direction']==1 else '저항 SHORT'
    groups={(z['timeframe'],z['kind']) for z in c['sources']}
    core=[g for g in groups if g[0] in c['pair']]
    proof=[]
    for tf in c['pair']:
        kinds=[kind for kind in ('OB','FVG') if (tf,kind) in groups]
        if kinds:proof.append(f"{LABELS[tf]} {'·'.join(kinds)}")
    if c.get('refinement_tf'):proof.append('15분 '+ '·'.join(kind for kind in ('OB','FVG') if ('15m',kind) in groups))
    area=c['structure_area']
    direction='상승' if c['direction']==1 else '하락'
    role='지지' if c['direction']==1 else '저항'
    structure=c.get('structure_states',{}).get(c['lenses'][0])
    if structure==('up' if c['direction']==1 else 'down') and area.get('bos_at'):
        narrative=f"{direction} 구조의 {role} OB 재방문"
    elif structure in ('up','down') and structure!=('up' if c['direction']==1 else 'down'):
        narrative=f"{'상승' if structure=='up' else '하락'} 흐름 중 기존 {role} OB 접촉"
    else:narrative=f"기존 {role} OB 재방문"
    return (f"[Bitget · {c['symbol']}]\n{LABELS[c['pair'][0]]} {TRACK_LABELS[c['track']]} {name} · {side} · 상위·중간 {len(core)}/4\n"
            f"{narrative}\n{' + '.join(proof)}\n구간 접촉 확인 · {kst(c['entry_touch']['at'])}")
