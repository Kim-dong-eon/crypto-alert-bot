"""Rooted upper OB pullbacks. Observation alerts, not automated entries.

An OB must belong to a structural departure/base, rather than merely being
near the latest price. Still-valid lower evidence from that original base is
retained. A new sweep/MSS at the retest is descriptive, never a required gate.
"""
from dataclasses import asdict
import hashlib
import math
from config.overlap_settings import TRACKS as CORE_TRACKS
from src.ict_engine import TF_MS,frame_open
from src.ict_overlap import Registry,VERSION,overlaps,alert_eligible
from src.structure_review import turns,departure_areas,liquidity_events,SCALES
from src.overlap_entry import entry_touch

POLICY='root-ob-pullback-v1'
TRACKS={'fast':('1h','15m'),**CORE_TRACKS}
TOP_COUNTS={'1h':1080,'4h':1080,'1d':1102,'1w':156}
REFINEMENT={'fast':'5m','short':'15m','medium':'1h','long':'4h'}
TRIPLE_TRACKS={name:(*pair,REFINEMENT[name]) for name,pair in TRACKS.items()}


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


def prepare(symbol,frames,now,price,tracks=None,*,build_overlaps=True,scales=None,departure_ob_frames=()):
    """Closed-bar causal context; returned views also drive the chart."""
    if not math.isfinite(price) or price<=0:raise ValueError('Invalid price')
    tracks=TRACKS if tracks is None else tracks
    scales=SCALES if scales is None else scales
    usable={};unavailable={};registry=Registry(departure_ob_frames)
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
            'unavailable_frames':unavailable,'deduplication_enabled':True}
    zones=list(registry.zones.values())
    for name,pair in tracks.items():
        top,lower=pair
        state={'pair':list(pair),'path':[*pair,REFINEMENT[name]],'candidate':None,'candidates':[],'status':'data_unavailable','direction':0,'context':None}
        result['tracks'][name]=state
        if any(tf not in usable or len(usable[tf])<16 for tf in pair):continue
        bars=usable[top][-TOP_COUNTS[top]:]
        analysis={'pair':list(pair),'frame_starts':{top:bars[0].timestamp,lower:usable[lower][0].timestamp},
                  'source_zones':[asdict(z) for z in zones if z.timeframe in pair],
                  'views':{},'as_of':now,'price':price}
        state['analysis']=analysis
        parents=[z for z in zones if z.timeframe==top and z.kind=='OB']
        for lens,policy in scales.items():
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
            if not build_overlaps:continue
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


def refinement_history_starts(symbol,frames,now,price,minute_bars=(),touch_since=0):
    """Backfill the final frame only for rooted boxes touched in this check."""
    from src.staircase_rules import staircases,ote_band,band_touch
    prepared,usable,_=prepare(symbol,frames,now,price)
    starts={}
    for name,state in prepared['tracks'].items():
        final=REFINEMENT[name]
        for view in state.get('analysis',{}).get('views',{}).values():
            top=state['pair'][0];bars=usable[top][-TOP_COUNTS[top]:]
            stairs={d:staircases(bars,view['turns'],top,d) for d in (1,-1)}
            for area in view['selected']:
                upper=next((s for s in stairs[area['direction']] if s['origin']==area['pivot_origin'] and not s['invalidated_at']),None)
                if upper is None:continue
                ote_low,ote_high=ote_band(upper)
                for c in area['overlaps']:
                    if band_touch(max(c['low'],ote_low),min(c['high'],ote_high),
                                  max(upper['known_at'],c['latest_source']),price,now,minute_bars,touch_since)['hit']:
                        required=area['origin']-2*TF_MS[final]
                        starts[final]=min(starts.get(final,required),required)
    return starts


def short_history_start(symbol,frames,now,price,minute_bars=(),touch_since=0):
    return refinement_history_starts(symbol,frames,now,price,minute_bars,touch_since).get('15m')


def refine_overlap(raw,area,zones,usable,final):
    """Require a third same-direction OB/FVG in the actual common price band."""
    bars=usable.get(final,[]);required=area['origin']-2*TF_MS[final]
    if not bars or bars[0].timestamp>required:return []
    result=[]
    for z in zones:
        if z.timeframe!=final or z.direction!=raw['direction'] or z.origin<required:continue
        low=max(raw['low'],z.low);high=min(raw['high'],z.high)
        if low>=high:continue
        sources=raw['sources']+[asdict(z)]
        if not {'OB','FVG'}.issubset(s['kind'] for s in sources):continue
        result.append({**raw,'low':low,'high':high,'sources':sources,
                       'source_ids':raw['source_ids']+[z.id],
                       'latest_source':max(raw['latest_source'],z.created),'refinement_tf':final})
    return result


def scan_overlaps(symbol,frames,now_ms,price,tracks=None,equilibrium_filter=True,minute_bars=(),touch_since=0):
    result,usable,zones=prepare(symbol,frames,now_ms,price,tracks)
    for name,state in result['tracks'].items():
        if 'analysis' not in state:continue
        found=[];top,lower=state['pair']
        for lens,view in state['analysis']['views'].items():
            for area in view['selected']:
                choices=[]
                for raw in area['overlaps']:
                    refinements=refine_overlap(raw,area,zones,usable,REFINEMENT[name])
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
                c={**choice,'id':identifier,'zone_id':identifier,'alert_policy':POLICY,'path':state['path'],
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


def scan(symbol,frames,now_ms,price,tracks=None,equilibrium_filter=True,minute_bars=(),touch_since=0):
    """Production: all segments must pass both stairs, OTE and net 3R."""
    from src.staircase_rules import RULESET,evaluate,opposing_upper_contact
    result,usable,zones=prepare(symbol,frames,now_ms,price,tracks)
    result['ruleset']=RULESET
    for name,state in result['tracks'].items():
        if 'analysis' not in state:continue
        top,middle=state['pair'];final=REFINEMENT[name];found=[];rejected={}
        upper=usable[top][-TOP_COUNTS[top]:]
        lower_points=turns(usable.get(final,[]),1.5,0.,final)
        for lens,view in state['analysis']['views'].items():
            for area in view['selected']:
                choices=[]
                for raw in area['overlaps']:
                    for c in refine_overlap(raw,area,zones,usable,final):
                        c={**c,'pair':list(state['pair']),'formed_at':max(area['created'],c['latest_source'])}
                        setup=evaluate(c,upper,view['turns'],usable[final],now_ms,price,minute_bars,touch_since,lower_points)
                        reason=setup['reason']
                        if 'upper' not in setup:
                            rejected[reason]=rejected.get(reason,0)+1;continue
                        c.update(trade_setup=setup,ruleset=RULESET)
                        obstacle=opposing_upper_contact(c,usable,zones,now_ms,price,minute_bars)
                        if obstacle:setup.update(eligible=False,reason='higher_opposing_ob_contact',blocking_ob=obstacle)
                        identity=f"{POLICY}|{RULESET}|{symbol}|{name}|{lens}|{'|'.join(c['source_ids'])}|{setup['lower']['origin']}"
                        identifier=hashlib.sha256(identity.encode()).hexdigest()[:24]
                        c.update(id=identifier,zone_id=identifier,alert_policy=POLICY,path=state['path'],
                                 symbol=symbol,track=name,exchange='bitget',lenses=[lens],price=price,
                                 context_id=f"root:{top}:{c['parent']['id']}:{lens}",
                                 context={'id':f"root:{top}:{c['parent']['id']}:{lens}"},
                                 structure_states={key:v['structure'] for key,v in state['analysis']['views'].items()},
                                 low=setup['low'],high=setup['high'],formed_at=setup['known_at'],
                                 entry_touch=setup['touch'],context_price_breach=False,
                                 equilibrium={'eligible':setup['eligible'],'midpoint':(setup['ote_low']+setup['ote_high'])/2,
                                              'range_low':area['range_low'],'range_high':area['range_high'],'mode':'OTE'},
                                 location='inside' if setup['low']<=price<=setup['high'] else 'outside_after_touch' if setup['touch']['hit'] else 'waiting')
                        rejected[setup['reason']]=rejected.get(setup['reason'],0)+1
                        choices.append(c)
                choices.sort(key=lambda c:(not alert_eligible(c),-c['score'],-c['trade_setup']['rr']))
                if choices:found.append(choices[0])
        found.sort(key=lambda c:(not alert_eligible(c),-c['score'],-c['trade_setup']['rr']))
        state.update(candidates=found,candidate=found[0] if found else None,
                     status='watching' if found else 'no_staircase_setup',filter_counts=rejected)
    consolidate_horizons(result)
    return result


def consolidate_horizons(snapshot):
    """A qualifying higher alert owns an intersecting same-side lower alert.

    Never fabricate an ineligible higher setup, combine disjoint boxes, or
    mix opposing directions/risk calculations. Preserve suppression evidence.
    """
    all_candidates=[c for state in snapshot['tracks'].values() for c in state.get('candidates',[])]
    eligible=sorted((c for c in all_candidates if alert_eligible(c)),
                    key=lambda c:-TF_MS[c['pair'][0]])
    kept=[]
    for c in eligible:
        owners=[h for h in kept if h['symbol']==c['symbol'] and h['direction']==c['direction']
                and TF_MS[h['pair'][0]]>TF_MS[c['pair'][0]]
                and max(h['low'],c['low'])<min(h['high'],c['high'])]
        if not owners:kept.append(c);continue
        c.update(monitor_eligible=False,suppressed_by_higher=[h['id'] for h in owners])
        for h in owners:
            notes=h.setdefault('also_overlaps',[])
            if not any(note['track']==c['track'] for note in notes):
                notes.append({'track':c['track'],'timeframe':c['pair'][0],
                              'low':max(h['low'],c['low']),'high':min(h['high'],c['high'])})
    for state in snapshot['tracks'].values():
        found=state.get('candidates',[])
        found.sort(key=lambda c:not alert_eligible(c))
        if found:state['candidate']=found[0]
    return snapshot


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
    if c.get('refinement_tf'):
        final=c['refinement_tf']
        proof.append(LABELS[final]+' '+ '·'.join(kind for kind in ('OB','FVG') if (final,kind) in groups))
    area=c['structure_area']
    if c.get('trade_setup'):
        s=c['trade_setup'];trend='상승' if c['direction']==1 else '하락'
        extra=''
        if c.get('also_overlaps'):
            extra='\n함께 중첩: '+', '.join(f"{LABELS[n['timeframe']]} {TRACK_LABELS[n['track']]}" for n in c['also_overlaps'])
        return (f"[Bitget · {c['symbol']}]\n{LABELS[c['pair'][0]]} {TRACK_LABELS[c['track']]} {name} · {side}\n"
                f"상위·하위 {trend} 계단 · OTE · 예상 순손익비 1:{s['rr']:.2f}\n"
                f"{' + '.join(proof)}{extra}\n구간 접촉 확인 · {kst(c['entry_touch']['at'])}")
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
