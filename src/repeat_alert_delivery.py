"""Send every qualifying observation; old zone acknowledgements never block."""
import copy
import hashlib
from src.structure_alerts import StructureLedger,candidates
from src.ict_overlap import alert_eligible
from src.pullback_alerts import POLICY


class RepeatAlertLedger(StructureLedger):
    def __init__(self,path,**kwargs):
        super().__init__(path,**kwargs)
        if self.state.get('notification_policy')!=POLICY:
            # Prior pending events were selected using another strategy.
            # Keep their delivery receipts, but do not send them as new alerts.
            self.state['pending']={}
            self.state['notification_policy']=POLICY
        self.state['locks']={}
        self.state['strategy']=POLICY
        self.state['delivery_mode']='every_qualifying_scan'

    def observe(self,snapshot,display_name=None,persist=True):
        if snapshot.get('strategy')!=POLICY:raise ValueError('Unexpected notification policy')
        before=copy.deepcopy(self.state) if not persist else None
        symbol=snapshot['symbol'];stamp=snapshot['as_of']
        # Re-evaluate fresh data each time. Historical delivered/lock records
        # are never consulted to suppress a new eligible observation.
        for key,event in list(self.state['pending'].items()):
            if event['candidate']['symbol']==symbol:del self.state['pending'][key]
        audit={'at':stamp,'queued':0,'suppressed':0,'locking_enabled':False}
        for name,state in snapshot['tracks'].items():
            for candidate in candidates(state):
                if not alert_eligible(candidate):continue
                c=copy.deepcopy(candidate)
                stable=c.get('zone_id',c['id'])
                c['id']=hashlib.sha256(f'{POLICY}|{stable}|observation:{stamp}'.encode()).hexdigest()[:24]
                key=self.key(c,'entry')
                self.state['pending'][key]={'candidate':c,'stage':'entry','timestamp':stamp,
                                           'new_formation':False,'display_name':display_name or symbol}
                audit['queued']+=1
            self.state['tracks'][f'{symbol}|{name}']={'candidate':state.get('candidate'),'checked_at':stamp}
        self.state.setdefault('dedup_audits',{})[symbol]=audit
        pending=copy.deepcopy(self.state['pending'])
        if persist:self.save()
        else:self.state=before
        return pending
