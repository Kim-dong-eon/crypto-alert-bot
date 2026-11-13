"""Once per upper OB, with an explicit one-run resend and durable receipts."""
import copy
import hashlib
from src.structure_alerts import StructureLedger,candidates
from src.ict_overlap import alert_eligible
from src.pullback_alerts import POLICY,REFINEMENT
from src.staircase_rules import RULESET

MODE='once_per_parent_ob'


def delivery_started(presentation):
    return any(r.get('image_sent') or r.get('text_sent') or r.get('in_flight')
               for r in presentation.get('units',{}).values())


def physical_id(candidate):
    parent=candidate['parent']
    raw=f"{POLICY}|{candidate['symbol']}|{candidate['track']}|{parent['id']}|{candidate['direction']}"
    if candidate.get('signal_version') == 2:
        overlap = candidate.get('overlap', candidate)
        raw += '|sources:' + '|'.join(sorted(z['id'] for z in overlap['sources']))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


class PullbackAlertLedger(StructureLedger):
    def __init__(self,path,**kwargs):
        super().__init__(path,**kwargs)
        if self.state.get('notification_policy')!=POLICY:
            self.state['pending']={}
            self.state['notification_policy']=POLICY
        if self.state.get('delivery_mode')!=MODE:
            # Seed the restored locks from acknowledged root-policy messages.
            # Thus a normal scheduled run cannot spam before the one-off resend.
            self.state['pending']={};self.state['locks']={}
            for p in self.state.get('presentations',{}).values():
                c=p.get('candidate')
                if not p.get('complete'):
                    p['retired_by_lock_migration']=True;continue
                if not p.get('complete') or not c or c.get('alert_policy')!=POLICY or not c.get('parent'):continue
                c=copy.deepcopy(c);c['id']=physical_id(c)
                scope=f"{c['symbol']}|{c['track']}";locks=self.state['locks'].setdefault(scope,[])
                if any(lock['id']==c['id'] for lock in locks):continue
                locks.append(self.lock(c,sent=True))
        self.state['strategy']=POLICY
        self.state['delivery_mode']=MODE
        # Do not resume a half-delivered two-frame middle/long alert after
        # upgrading all horizons to mandatory three-frame confluence. Keep
        # completed receipts and their parent locks intact.
        presentations=self.state.setdefault('presentations',{})
        for key,p in list(presentations.items()):
            c=p.get('candidate') or {}
            if (not p.get('complete') and c.get('alert_policy')==POLICY
                    and (c.get('refinement_tf')!=REFINEMENT.get(c.get('track')) or c.get('ruleset')!=RULESET)):
                # Do not turn an acknowledged/ambiguous old photo into a new
                # automatic send merely because the filter version changed.
                if delivery_started(p) and c.get('parent'):
                    scope=f"{c['symbol']}|{c['track']}";locks=self.state['locks'].setdefault(scope,[])
                    lock=next((item for item in locks if item['id']==physical_id(c)),None)
                    if lock is None:lock=self.lock(c);locks.append(lock)
                    lock.update(sent=True,held_after_policy_change=True)
                self.state.setdefault('retired_presentations',{})[f"{key}:{p.get('attempt',0)}"]=p
                presentations.pop(key);self.state['pending'].pop(key,None)

    @staticmethod
    def lock(c,sent=False):
        return {'id':physical_id(c),'parent_id':c['parent']['id'],'direction':c['direction'],
                'anchor_low':c['parent']['low'],'anchor_high':c['parent']['high'],
                'sent':sent,'latest':copy.deepcopy(c)}

    def observe(self,snapshot,display_name=None,persist=True):
        if snapshot.get('strategy')!=POLICY:raise ValueError('Unexpected notification policy')
        before=copy.deepcopy(self.state) if not persist else None
        symbol=snapshot['symbol'];stamp=snapshot['as_of'];valid=set(snapshot['valid_source_ids'])
        for key,event in list(self.state['pending'].items()):
            if event['candidate']['symbol']==symbol:del self.state['pending'][key]
        audit={'at':stamp,'queued':0,'suppressed':0,'retired':0,'locking_enabled':True,
               'one_off_resend':self.force_resend}
        presentations=self.state.setdefault('presentations',{})
        for name,state in snapshot['tracks'].items():
            if state['status']=='data_unavailable':continue
            scope=f'{symbol}|{name}';remaining=[]
            for lock in self.state['locks'].get(scope,[]):
                parent=lock['latest']['parent'];start=snapshot['history_starts'].get(parent['timeframe'])
                if parent['id'] not in valid and start is not None and parent['origin']>=start:
                    audit['retired']+=1;continue
                remaining.append(lock)
            groups={}
            for candidate in candidates(state):
                if not alert_eligible(candidate):continue
                identity=physical_id(candidate)
                if identity not in groups:
                    c=copy.deepcopy(candidate);c['id']=identity;c['lenses']=[];c['lens_details']={}
                    groups[identity]=c
                c=groups[identity]
                for lens in candidate['lenses']:
                    if lens not in c['lenses']:c['lenses'].append(lens)
                    c['lens_details'][lens]={k:copy.deepcopy(v) for k,v in candidate.items()
                                              if k not in ('id','lenses','lens_details')}
            for identity,c in groups.items():
                lock=next((lock for lock in remaining if lock['id']==identity),None)
                if lock is None:lock=self.lock(c);remaining.append(lock)
                lock['latest']=copy.deepcopy(c)
                key=self.key(c,'entry');p=presentations.get(key)
                unfinished=bool(p and not p['complete'] and not p.get('retired_by_lock_migration'))
                if unfinished and p.get('candidate',{}).get('trade_setup'):
                    old=p['candidate']
                    same_step=(old['trade_setup']['lower']['origin']==c.get('trade_setup',{}).get('lower',{}).get('origin')
                               and set(old['source_ids'])==set(c['source_ids']))
                    if not same_step:
                        if delivery_started(p):lock.update(sent=True,held_after_step_change=True)
                        self.state.setdefault('retired_presentations',{})[f"{key}:{p['attempt']}"]=p
                        presentations.pop(key,None);p=None;unfinished=False
                if lock['sent'] and not self.force_resend and not unfinished:
                    audit['suppressed']+=1;continue
                if unfinished and p.get('candidate'):
                    c=copy.deepcopy(p['candidate'])
                self.state['pending'][key]={'candidate':c,'stage':'entry','timestamp':p['attempt'] if unfinished else stamp,
                                           'new_formation':False,'display_name':display_name or symbol}
                audit['queued']+=1
            # Complete the text of an acknowledged photo even after price has
            # left, while refusing to deliver an invalidated/stale source set.
            for key,p in presentations.items():
                c=p.get('candidate')
                if (p['complete'] or p.get('retired_by_lock_migration') or p['symbol']!=symbol or p['track']!=name or key in self.state['pending'] or not c):continue
                if c.get('alert_policy')!=POLICY or c.get('ruleset')!=RULESET or not set(c['source_ids']).issubset(valid):continue
                refreshed=[x for x in candidates(state) if physical_id(x)==physical_id(c) and alert_eligible(x)]
                if c.get('trade_setup'):
                    refreshed=[x for x in refreshed if x.get('trade_setup',{}).get('lower',{}).get('origin')==c['trade_setup']['lower']['origin']
                               and set(x['source_ids'])==set(c['source_ids'])]
                if not refreshed:continue
                self.state['pending'][key]={'candidate':copy.deepcopy(c),'stage':'entry','timestamp':p['attempt'],
                                           'new_formation':False,'display_name':display_name or symbol}
            self.state['locks'][scope]=remaining
            self.state['tracks'][scope]={'candidate':state.get('candidate'),'checked_at':stamp}
        self.state.setdefault('dedup_audits',{})[symbol]=audit
        pending=copy.deepcopy(self.state['pending'])
        if persist:self.save()
        else:self.state=before
        return pending

    def dispatch_units(self,image_sender,text_sender,renderer):
        result=super().dispatch_units(image_sender,text_sender,renderer)
        for locks in self.state['locks'].values():
            for lock in locks:
                key=self.key(lock['latest'],'entry');p=self.state.get('presentations',{}).get(key)
                if p and p['complete']:
                    lock['sent']=True;lock['last_sent_at']=p['attempt']
        # Explicit resend is scoped to this dispatch, never a persisted mode.
        self.force_resend=False
        self.save()
        return result
