"""Durable observation/outbox state, per-zone stages, acknowledged delivery."""
import copy
import hashlib
import json
import os
import time
from pathlib import Path

from src.ict_overlap import VERSION, format_notice,compact_notice
from src.ict_engine import TF_MS


class OverlapLedger:
    def __init__(self, path, require_images=False,overlap_only=False,approach_only=False):
        self.path = Path(path)
        self.state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"version":VERSION,"tracks":{},"delivered":{},"pending":{}}
        if self.state.get("version") != VERSION:
            raise ValueError("Incompatible overlap state")
        self.state.setdefault("locks",{})
        self.state.setdefault("images_delivered",{})
        self.require_images=require_images
        self.overlap_only=overlap_only
        self.approach_only=approach_only
        if overlap_only:
            wanted='approach' if approach_only else 'prepared'
            self.state['pending']={k:e for k,e in self.state['pending'].items() if e['stage']==wanted}

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix+".tmp")
        temporary.write_text(json.dumps(self.state,ensure_ascii=False,indent=2),encoding="utf-8")
        for attempt in range(4):
            try:
                os.replace(temporary,self.path)
                break
            except PermissionError:
                if attempt==3:raise
                time.sleep(.03*(attempt+1))

    @staticmethod
    def key(candidate,stage):
        return hashlib.sha256(f"{candidate['symbol']}|{candidate['track']}|{candidate['id']}|{stage}".encode()).hexdigest()[:24]

    def observe(self,snapshot,display_name=None,persist=True):
        old = copy.deepcopy(self.state)
        stamp=snapshot["as_of"];valid=set(snapshot["valid_source_ids"])
        def add(candidate,stage,previous,reason=None):
            key=self.key(candidate,stage)
            image_due=self.require_images and stage in ('prepared','touched','approach') and key not in self.state['images_delivered']
            if key not in self.state["delivered"] or image_due:
                self.state["pending"][key]={"candidate":candidate,"stage":stage,"timestamp":stamp,
                    "new_formation":bool(previous and candidate["formed_at"]>previous["checked_at"]),"display_name":display_name or candidate["symbol"],"reason":reason}
        for name,current in snapshot["tracks"].items():
            if name=="auxiliary":continue  # report-only; avoid a second short alert
            scope=f"{snapshot['symbol']}|{name}";previous=self.state["tracks"].get(scope);before=previous.get("candidate") if previous else None
            if current["status"]=="data_unavailable":
                # Missing/stale data cannot cancel a valid zone or send its
                # previously queued observation with an old price.
                for key,event in list(self.state["pending"].items()):
                    if event["candidate"]["symbol"]==snapshot["symbol"] and event["candidate"]["track"]==name and event["stage"]!="cancelled":
                        del self.state["pending"][key]
                continue
            candidate=copy.deepcopy(current["candidate"])
            locks=self.state["locks"].setdefault(scope,[])
            remaining=[]
            for lock in locks:
                held=lock["latest"]
                context_matches=current["context"] and held["context_id"]==current["context"]["id"]
                sources_valid=set(held["source_ids"]).issubset(valid)
                starts=snapshot.get('history_starts',{})
                missing=[z for z in held.get('sources',[]) if z['id'] not in valid]
                outside=bool(missing) and all(z['timeframe'] in starts and z['origin']<starts[z['timeframe']] for z in missing)
                top=held['pair'][0]
                context_outside=not current['context'] and top in starts and held.get('context',{}).get('sweep_time',2**63)-TF_MS[top]<starts[top]
                # A fresh Actions runner may lose its optional candle cache.
                # Unavailable history is not proof of price invalidation.
                if (context_matches or context_outside) and (sources_valid or outside):
                    remaining.append(lock)
                else:
                    was_sent=any(self.key(held,stage) in self.state["delivered"] for stage in ("prepared","touched","caution","approach"))
                    for key,event in list(self.state["pending"].items()):
                        if event["candidate"]["id"]==held["id"] and event["stage"]!="cancelled":del self.state["pending"][key]
                    if was_sent and not self.overlap_only:
                        add({**held,"price":snapshot["price"]},"cancelled",previous,"상위 스윕·MSS 흐름 종료/변경" if not context_matches else "중첩 근거의 해당 시간대 종가 무효화")
            self.state["locks"][scope]=remaining
            if candidate:
                matched=next((lock for lock in remaining if lock["latest"]["context_id"]==candidate["context_id"] and lock["latest"]["direction"]==candidate["direction"] and max(lock["anchor_low"],candidate["low"])<min(lock["anchor_high"],candidate["high"])),None)
                if matched:
                    candidate["id"]=matched["latest"]["id"]
                    matched["latest"]=copy.deepcopy(candidate)
                else:
                    remaining.append({"anchor_low":candidate["low"],"anchor_high":candidate["high"],"latest":copy.deepcopy(candidate)})
            # Never retry an outdated selection after another band takes priority.
            for key,event in list(self.state["pending"].items()):
                c=event["candidate"]
                if c["symbol"]==snapshot["symbol"] and c["track"]==name and event["stage"]!="cancelled" and (not candidate or c["id"]!=candidate["id"]):del self.state["pending"][key]
            if candidate:
                stage=(None if candidate['context_price_breach'] else 'prepared') if self.overlap_only else "caution" if candidate["context_price_breach"] else "touched" if candidate["location"]=="inside" else "prepared"
                if self.approach_only:
                    stage='approach' if candidate.get('equilibrium',{}).get('eligible') and not candidate['context_price_breach'] else None
                    if stage is None:self.state['pending'].pop(self.key(candidate,'approach'),None)
                # Touch subsumes a pending unannounced formation.
                if stage!="prepared":self.state["pending"].pop(self.key(candidate,"prepared"),None)
                if stage=="prepared" and self.key(candidate,"touched") in self.state["delivered"]:stage=None
                if stage:add(candidate,stage,previous)
            self.state["tracks"][scope]={"candidate":candidate,"checked_at":stamp}
        pending=copy.deepcopy(self.state["pending"])
        if persist:self.save()
        else:self.state=old
        return pending

    def unlock(self,symbol,track):
        scope=f"{symbol}|{track}"
        locks=self.state["locks"].pop(scope,[])
        identifiers={lock["latest"]["id"] for lock in locks}
        self.state["tracks"].pop(scope,None)
        for collection in ("pending","delivered","images_delivered"):
            for key,event in list(self.state[collection].items()):
                if collection=="pending":matches=event["candidate"]["id"] in identifiers
                else:matches=key in {self.key(lock["latest"],stage) for lock in locks for stage in ("prepared","touched","caution","cancelled","approach")}
                if matches:del self.state[collection][key]
        self.save()

    def dispatch_media(self,text_sender,image_sender,renderer):
        result={'sent':0,'failed':0,'images_sent':0}
        pictures=[]
        for key,event in list(self.state['pending'].items()):
            if event['stage'] in ('prepared','touched'):
                try:
                    path=renderer(event);caption=format_notice(event)
                    if len(caption)>1024:raise ValueError('Caption too long')
                    pictures.append((key,event,path,caption))
                except Exception:result['failed']+=1
            else:
                try:success=text_sender(format_notice(event)) is True
                except Exception:success=False
                if success:
                    self.state['delivered'][key]={'symbol':event['candidate']['symbol'],'stage':event['stage'],'timestamp':event['timestamp']}
                    del self.state['pending'][key];self.save();result['sent']+=1
                else:result['failed']+=1
        for start in range(0,len(pictures),10):
            batch=pictures[start:start+10]
            try:success=image_sender([(path,caption) for _,_,path,caption in batch]) is True
            except Exception:success=False
            if success:
                for key,event,_,_ in batch:
                    record={'symbol':event['candidate']['symbol'],'stage':event['stage'],'timestamp':event['timestamp']}
                    self.state['delivered'][key]=record;self.state['images_delivered'][key]=record
                    del self.state['pending'][key]
                self.save();result['sent']+=len(batch);result['images_sent']+=len(batch)
            else:result['failed']+=len(batch)
        return result

    def dispatch_symbols(self,image_sender,renderer,contexts):
        """A single image/caption per symbol; acknowledge its pending scopes."""
        result={'sent':0,'failed':0,'images_sent':0};grouped={}
        for key,event in self.state['pending'].items():
            if event['stage'] not in ('prepared','approach'):continue
            grouped.setdefault(event['candidate']['symbol'],[]).append((key,event))
        ready=[]
        for symbol,pending in grouped.items():
            events=contexts.get(symbol) or [e for _,e in pending]
            try:
                caption=compact_notice(events)
                if len(caption)>1024:raise ValueError('Caption too long')
                path=renderer(events);ready.append((pending,path,caption))
            except Exception:result['failed']+=len(pending)
        for offset in range(0,len(ready),10):
            batch=ready[offset:offset+10]
            try:success=image_sender([(p,c) for _,p,c in batch]) is True
            except Exception:success=False
            if not success:
                result['failed']+=sum(len(events) for events,_,_ in batch);continue
            for pending,_,_ in batch:
                for key,event in pending:
                    record={'symbol':event['candidate']['symbol'],'stage':event['stage'],'timestamp':event['timestamp']}
                    self.state['delivered'][key]=record;self.state['images_delivered'][key]=record;del self.state['pending'][key]
                    result['sent']+=1
                result['images_sent']+=1
            self.save()
        return result

    def dispatch(self,sender,batch=False):
        result={"sent":0,"failed":0}
        groups=[];current=[];length=0
        for key,event in list(self.state["pending"].items()):
            message=format_notice(event)
            if current and (not batch or length+len(message)+2>3700):groups.append(current);current=[];length=0
            current.append((key,event,message));length+=len(message)+2
        if current:groups.append(current)
        for group in groups:
            try:success=sender('\n\n'.join(item[2] for item in group)) is True
            except Exception:success=False
            if success:
                for key,event,_ in group:
                    self.state["delivered"][key]={"symbol":event["candidate"]["symbol"],"stage":event["stage"],"timestamp":event["timestamp"]}
                    del self.state["pending"][key]
                self.save();result["sent"]+=len(group)
            else:result["failed"]+=len(group)
        return result
