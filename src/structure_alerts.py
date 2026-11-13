"""Production two-scale observations and durable physical-zone deduplication."""
import copy
import hashlib
from config.overlap_settings import TRACKS,TRACK_LABELS
from src.ict_engine import TF_MS,frame_open
from src.ict_overlap import VERSION,alert_eligible,kst,LABELS
from src.structure_review import analyze
from src.overlap_delivery import OverlapLedger
from src.overlap_entry import ENTRY_POLICY,entry_touch,mixed_evidence
from src.anchored_overlap import ANCHOR_POLICY,valid_anchor,anchor_start

STRATEGY='two-scale-structure-v1'


def candidates(state):
    return state.get('candidates',[state['candidate']] if state.get('candidate') else [])


def scan(symbol,frames,now_ms,price,tracks=None,equilibrium_filter=True,minute_bars=(),touch_since=0):
    tracks=TRACKS if tracks is None else tracks
    result={'version':VERSION,'strategy':STRATEGY,'entry_policy':ENTRY_POLICY,'anchor_policy':ANCHOR_POLICY,'symbol':symbol,'exchange':'bitget',
            'price':price,'as_of':now_ms,'tracks':{},'valid_source_ids':[],
            'valid_zones':[],'history_starts':{},'unavailable_frames':{}}
    for tf,values in frames.items():
        if values:result['history_starts'][tf]=values[0].timestamp
    for name,pair in tracks.items():
        state={'pair':list(pair),'candidate':None,'candidates':[],'context':None,
               'direction':0,'status':'data_unavailable'}
        result['tracks'][name]=state
        if any(len(frames.get(tf,[]))<16 or frames[tf][-1].timestamp+TF_MS[tf]!=frame_open(now_ms,tf) for tf in pair):
            continue
        review=analyze(frames,now_ms,price,pair,symbol,require_ob_fvg=True,same_candle=True)
        state['analysis']=review
        result['valid_zones'].extend(review['source_zones'])
        found=[]
        for lens,view in review['views'].items():
            for d in (1,-1):
                areas=[a for a in view['selected'] if a['direction']==d and a['overlaps']]
                if not areas:continue
                area=areas[0]
                if not area['overlaps']:continue
                choices=[]
                for original in area['overlaps']:
                    touch=entry_touch(original,area,price,now_ms,minute_bars,touch_since)
                    eq={**original['equilibrium'],'eligible':touch['hit'],'entry_required':True}
                    choices.append({**original,'equilibrium':eq,'entry_touch':touch})
                choices.sort(key=lambda c:(not c['entry_touch']['hit'],-c['score'],max(c['low']-price,price-c['high'],0)))
                choice=choices[0]
                context_id=f"parent:{pair[0]}:{choice['parent_window']['start']}:{d}"
                raw=f"{symbol}|{name}|{context_id}|{choice['low']:.12g}|{choice['high']:.12g}|{'|'.join(choice['source_ids'])}"
                c={**choice,'id':hashlib.sha256(raw.encode()).hexdigest()[:24],
                   'symbol':symbol,'track':name,'pair':list(pair),'exchange':'bitget',
                   'direction':d,'price':price,'context_id':context_id,'context':{'id':context_id},
                   'structure_area':area,'lenses':[lens],'formed_at':max(area['created'],choice['latest_source']),
                   'structure_states':{k:v['structure'] for k,v in review['views'].items()},
                   'context_price_breach':price<area['low'] if d==1 else price>area['high'],
                   'location':'inside' if choice['low']<=price<=choice['high'] else 'waiting'}
                c['lens_details']={lens:{k:c[k] for k in ('structure_area','entry_touch','equilibrium')}}
                # One physical overlap can belong to both lenses. Its
                # original identity, band and four evidence checks stay one.
                duplicate=next((old for old in found if old['context_id']==context_id and
                                old['source_ids']==c['source_ids'] and old['low']==c['low'] and old['high']==c['high']),None)
                if duplicate and duplicate['entry_touch']['hit']==c['entry_touch']['hit']:
                    duplicate['lenses'].append(lens);duplicate['lens_details'].update(c['lens_details'])
                else:found.append(c)
        found.sort(key=lambda c:(not alert_eligible(c),-c['score'],max(c['low']-price,price-c['high'],0)))
        state.update(status='watching' if found else 'no_overlap',candidates=found,
                     candidate=found[0] if found else None)
    unique={z['id']:z for z in result['valid_zones']}
    result['valid_zones']=list(unique.values());result['valid_source_ids']=sorted(unique)
    return result


def notice(events):
    first=events[0]['candidate'];number=lambda x:f'{x:,.4f}'.rstrip('0').rstrip('.')
    lines=[f"[Bitget · {first['symbol']}] {number(first['price'])}"]
    for track in TRACKS:
        own=[e['candidate'] for e in events if e['candidate']['track']==track]
        if not own:continue
        for c in own:
            lens='·'.join('큰 틀' if x=='broad' else '작은 틀' for x in c['lenses'])
            lines.append(f"{TRACK_LABELS[track]} · {lens} · {'지지 LONG' if c['direction']==1 else '저항 SHORT'} · {c['score']}/4")
            lines.append(f"알림 구간 {number(c['low'])} ~ {number(c['high'])}")
    lines.append(kst(max(e['timestamp'] for e in events)))
    return '\n'.join(lines)


class StructureLedger(OverlapLedger):
    """Retain v10 acknowledgements; aliases match price bands across lenses."""
    def __init__(self,path,require_images=False,overlap_only=False,approach_only=False,force_resend=False):
        super().__init__(path,require_images=require_images,overlap_only=False,approach_only=approach_only,force_resend=force_resend)
        self.overlap_only=overlap_only
        if overlap_only:
            allowed=('entry','approach') if approach_only else ('entry','prepared')
            self.state['pending']={k:e for k,e in self.state['pending'].items() if e['stage'] in allowed}

    def unlock(self,symbol,track):
        for key,event in list(self.state.get('in_flight',{}).items()):
            if event['candidate']['symbol']==symbol and event['candidate']['track']==track:
                del self.state['in_flight'][key]
        for key,p in list(self.state.get('presentations',{}).items()):
            if p['symbol']==symbol and p['track']==track:del self.state['presentations'][key]
        super().unlock(symbol,track)

    def observe(self,snapshot,display_name=None,persist=True):
        if snapshot.get('strategy')!=STRATEGY:
            return super().observe(snapshot,display_name,persist)
        before=copy.deepcopy(self.state);valid=set(snapshot['valid_source_ids'])
        entry_mode=snapshot.get('entry_policy')==ENTRY_POLICY
        anchored=snapshot.get('anchor_policy')==ANCHOR_POLICY
        stage='entry' if entry_mode else 'approach'
        stamp=snapshot['as_of'];audit={'suppressed':0,'queued':0,'retired':0,'legacy_matched':0,'uncertain_held':0}
        self.state['strategy']=STRATEGY
        for name,current in snapshot['tracks'].items():
            scope=f"{snapshot['symbol']}|{name}"
            # Remove stale selections, including pending legacy-rule events.
            for key,e in list(self.state['pending'].items()):
                if e['candidate']['symbol']==snapshot['symbol'] and e['candidate']['track']==name:
                    del self.state['pending'][key]
            if current['status']=='data_unavailable':continue
            locks=self.state['locks'].setdefault(scope,[]);remaining=[]
            invalid_areas={f"area:{current['pair'][0]}:{a['pivot_origin']}:{a['direction']}"
                           for v in current['analysis']['views'].values() for a in v['areas']
                           if a['invalidated_at'] is not None}
            active_areas={f"area:{current['pair'][0]}:{a['pivot_origin']}:{a['direction']}"
                          for v in current['analysis']['views'].values() for a in v['areas']
                          if a['invalidated_at'] is None}
            for lock in locks:
                held=lock['latest'];missing=[z for z in held.get('sources',[]) if z['id'] not in valid]
                if anchored and not valid_anchor(held):audit['retired']+=1;continue
                starts=snapshot['history_starts']
                source_broken=any(z['timeframe'] in starts and z['origin']>=starts[z['timeframe']] for z in missing)
                area_broken=held['context_id'] in invalid_areas and held['context_id'] not in active_areas
                if source_broken or area_broken:audit['retired']+=1
                else:remaining.append(lock)
            queued=[]
            for current_candidate in candidates(current):
                c=copy.deepcopy(current_candidate)
                match=next((lock for lock in remaining if lock['latest']['direction']==c['direction'] and
                            (not anchored or anchor_start(next(s for s in lock['latest']['sources'] if s['timeframe']==c['pair'][0]))==c['parent_window']['start']) and
                            max(lock['anchor_low'],c['low'])<min(lock['anchor_high'],c['high'])),None)
                if match:
                    old=match['latest'];c['id']=old['id']
                    if not old.get('structure_area'):audit['legacy_matched']+=1
                    match['latest']=copy.deepcopy(c)
                else:
                    match={'anchor_low':c['low'],'anchor_high':c['high'],'latest':copy.deepcopy(c)}
                    remaining.append(match)
                key=self.key(c,stage)
                stages=('entry',) if entry_mode else ('approach','prepared','touched')
                sent=any(self.key(c,s) in self.state['delivered'] for s in stages)
                presentation=self.state.get('presentations',{}).get(key)
                unfinished=presentation is not None and not presentation['complete']
                if not alert_eligible(c) and not (unfinished and not c['context_price_breach']):continue
                if key in self.state.get('in_flight',{}) and not self.force_resend:
                    audit['uncertain_held']+=1;continue
                if sent and not self.force_resend and not unfinished:audit['suppressed']+=1;continue
                # Broad/recent aliases in this same run cannot enqueue twice.
                if key in self.state['pending']:
                    pending=self.state['pending'][key]['candidate']
                    pending['lenses']=list(dict.fromkeys(pending['lenses']+c['lenses']))
                    pending.setdefault('lens_details',{}).update(c.get('lens_details',{}))
                    audit['suppressed']+=1;continue
                if unfinished and presentation.get('candidate'):
                    c=copy.deepcopy(presentation['candidate'])
                self.state['pending'][key]={'candidate':c,'stage':stage,'timestamp':stamp,
                                           'new_formation':False,'display_name':display_name or snapshot['symbol']}
                queued.append(c);audit['queued']+=1
            # A successful image still needs its text even if a different
            # price segment becomes the scanner's preferred watch candidate.
            for key,p in self.state.get('presentations',{}).items():
                original=p.get('candidate')
                if (p['complete'] or p['symbol']!=snapshot['symbol'] or p['track']!=name
                        or key in self.state['pending'] or not original):continue
                if entry_mode and not (original.get('entry_touch',{}).get('hit') and mixed_evidence(original)):continue
                if anchored and not valid_anchor(original):continue
                if not set(original['source_ids']).issubset(valid):continue
                if original['context_id'] in invalid_areas and original['context_id'] not in active_areas:continue
                area=original.get('structure_area',{})
                if area.get('low') is not None and (snapshot['price']<area['low'] if original['direction']==1 else snapshot['price']>area['high']):continue
                self.state['pending'][key]={'candidate':copy.deepcopy(original),'stage':p.get('stage',stage),
                                           'timestamp':p['attempt'],'new_formation':False,
                                           'display_name':display_name or snapshot['symbol']}
            self.state['locks'][scope]=remaining
            self.state['tracks'][scope]={'candidate':queued[0] if queued else current.get('candidate'),'checked_at':stamp}
        self.state.setdefault('dedup_audits',{})[snapshot['symbol']]={'at':stamp,**audit}
        pending=copy.deepcopy(self.state['pending'])
        if persist:self.save()
        else:self.state=before
        return pending

    def dispatch_symbols(self,image_sender,renderer,contexts):
        """Reserve before HTTP; do not blindly retry an ambiguous response."""
        result={'sent':0,'failed':0,'images_sent':0,'uncertain':0};groups={}
        for key,event in self.state['pending'].items():
            groups.setdefault(event['candidate']['symbol'],[]).append((key,event))
        for symbol,pending in groups.items():
            events=contexts.get(symbol) or [e for _,e in pending]
            try:
                text=notice(events)
                if len(text)>1024:raise ValueError('Caption too long')
                paths=list(renderer(events))
                if len(paths)!=2:raise ValueError('Two photos required')
            except Exception:result['failed']+=len(pending);continue
            flights=self.state.setdefault('in_flight',{})
            for key,event in pending:
                flights[key]={'symbol':symbol,'timestamp':event['timestamp'],'candidate':event['candidate']}
            self.save()
            try:ok=image_sender([(p,text if i==0 else '') for i,p in enumerate(paths)]) is True
            except Exception:ok=False
            owner=getattr(image_sender,'__self__',None)
            receipt=getattr(owner,'last_receipt',{}) if owner else {}
            if not ok:
                unknown=receipt.get('outcome')=='unknown'
                for key,_ in pending:
                    if not unknown:flights.pop(key,None)
                result['failed']+=len(pending)
                if unknown:result['uncertain']+=len(pending)
                self.save();continue
            for key,event in pending:
                record={'symbol':symbol,'stage':event['stage'],'timestamp':event['timestamp'],
                        'message_ids':receipt.get('message_ids',[])}
                self.state['delivered'][key]=record;self.state['images_delivered'][key]=record
                self.state['pending'].pop(key,None);flights.pop(key,None);result['sent']+=1
            result['images_sent']+=2;self.save()
        return result

    def dispatch_units(self,image_sender,text_sender,renderer):
        """Per lens: one context/detail image, then a separate short text.

        Acknowledge each phase before continuing. A retry can resume a failed
        text without posting the already confirmed image again.
        """
        from src.split_alert_preview import split_units,unit_text
        result={'sent':0,'failed':0,'images_sent':0,'texts_sent':0,'units_sent':0,'uncertain':0}
        presentations=self.state.setdefault('presentations',{})
        def receipt(sender):
            owner=getattr(sender,'__self__',None)
            return getattr(owner,'last_receipt',{}) if owner else {}
        for key,event in list(self.state['pending'].items()):
            c=event['candidate'];p=presentations.get(key)
            if p is None or (self.force_resend and p['complete']):
                p={'symbol':c['symbol'],'track':c['track'],'stage':event['stage'],'attempt':event['timestamp'],'candidate':copy.deepcopy(c),
                   'complete':False,'units':{}}
                presentations[key]=p
                self.save()
            failed=False
            for unit in split_units([event]):
                lens=unit['candidate']['lenses'][0]
                record=p['units'].setdefault(lens,{'image_sent':False,'text_sent':False,
                                                  'image_message_ids':[],'text_message_ids':[]})
                if record['image_sent'] and record['text_sent']:continue
                if record.get('in_flight'):
                    if self.force_resend:record.pop('in_flight')
                    else:result['uncertain']+=1;failed=True;continue
                text=unit_text(unit)
                if unit['candidate'].get('first_touch_text') and not record['text_sent']:
                    record['in_flight']='text';self.save()
                    try:ok=text_sender(text) is True
                    except Exception:ok=False
                    response=receipt(text_sender)
                    if not ok:
                        if response.get('outcome')=='unknown':result['uncertain']+=1
                        else:record.pop('in_flight',None)
                        failed=True;self.save();continue
                    record.update(text_sent=True,text_message_ids=response.get('message_ids',[]))
                    record.pop('in_flight',None);result['texts_sent']+=1;self.save()
                if not record['image_sent']:
                    try:
                        rendered=renderer(unit)
                        images=rendered['images']
                        captions=rendered.get('image_captions',['']*len(images))
                        if not images or len(images)!=len(captions):raise ValueError('Invalid alert album')
                    except Exception:failed=True;continue
                    record['in_flight']='image';self.save()
                    try:ok=image_sender(list(zip(images,captions))) is True
                    except Exception:ok=False
                    response=receipt(image_sender)
                    if not ok:
                        if response.get('outcome')=='unknown':result['uncertain']+=1
                        else:record.pop('in_flight',None)
                        failed=True;self.save();continue
                    record.update(image_sent=True,image_message_ids=response.get('message_ids',[]))
                    record.pop('in_flight',None);result['images_sent']+=len(images);self.save()
                if not record['text_sent']:
                    record['in_flight']='text';self.save()
                    try:ok=text_sender(text) is True
                    except Exception:ok=False
                    response=receipt(text_sender)
                    if not ok:
                        if response.get('outcome')=='unknown':result['uncertain']+=1
                        else:record.pop('in_flight',None)
                        failed=True;self.save();continue
                    record.update(text_sent=True,text_message_ids=response.get('message_ids',[]))
                    record.pop('in_flight',None);result['texts_sent']+=1
                result['units_sent']+=1;self.save()
            if failed:result['failed']+=1;continue
            p['complete']=True
            message_ids=[i for r in p['units'].values() for kind in ('image_message_ids','text_message_ids') for i in r[kind]]
            ack={'symbol':c['symbol'],'stage':event['stage'],'timestamp':event['timestamp'],'message_ids':message_ids}
            self.state['delivered'][key]=ack;self.state['images_delivered'][key]=ack
            self.state['in_flight'].pop(key,None) if 'in_flight' in self.state else None
            self.state['pending'].pop(key,None);result['sent']+=1;self.save()
        return result
