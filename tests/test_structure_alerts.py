import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from src.structure_alerts import StructureLedger,STRATEGY,scan,notice
from src.structure_review import turns,departure_areas,liquidity_events
from src.ict_engine import Bar,TF_MS
from tests.test_ict_overlap import snapshot


def multi(lenses=('broad',),identifier='new',symbol='BTCUSDT',price=105):
    s=snapshot(price=price,identifier=identifier);s.update(strategy=STRATEGY,history_starts={'4h':0,'1h':0})
    s['symbol']=symbol;c=s['tracks']['short']['candidate'];c['symbol']=symbol
    c.update(lenses=list(lenses),structure_area={'pivot_origin':0},
             equilibrium={'eligible':True,'midpoint':115},score=2)
    s['tracks']['short']['analysis']={'views':{x:{'areas':[]} for x in ('broad','recent')}}
    s['tracks']['short']['candidates']=[copy.deepcopy(c)]
    return s


class StructureDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'state.json'
        self.ledger=StructureLedger(self.path,require_images=True,overlap_only=True,approach_only=True)

    def send(self,sender=lambda _:True):
        return self.ledger.dispatch_symbols(sender,lambda _:[Path('whole.png'),Path('recent.png')],{})

    def test_two_lenses_do_not_double_send_and_restart_is_locked(self):
        s=multi();other=copy.deepcopy(s['tracks']['short']['candidates'][0])
        other.update(id='other',context_id='another-area',lenses=['recent'])
        s['tracks']['short']['candidates'].append(other)
        self.ledger.observe(s);self.assertEqual(len(self.ledger.state['pending']),1)
        self.assertEqual(self.send()['images_sent'],2)
        self.ledger=StructureLedger(self.path,require_images=True,overlap_only=True,approach_only=True)
        self.ledger.observe(s);self.assertEqual(self.send()['sent'],0)
        self.assertFalse(self.ledger.state['pending'])

    def test_sent_legacy_band_is_not_resent_under_new_strategy(self):
        from src.overlap_delivery import OverlapLedger
        old=OverlapLedger(self.path,require_images=True,overlap_only=True,approach_only=True)
        legacy=snapshot();legacy['tracks']['short']['candidate']['equilibrium']={'eligible':True,'midpoint':115}
        old.observe(legacy);old.dispatch_symbols(lambda _:True,lambda _:Path('old.png'),{})
        delivered=copy.deepcopy(old.state['delivered'])
        self.ledger=StructureLedger(self.path,require_images=True,overlap_only=True,approach_only=True)
        self.ledger.observe(multi())
        self.assertFalse(self.ledger.state['pending'])
        self.assertEqual(self.ledger.state['delivered'],delivered)
        self.assertEqual(self.ledger.state['dedup_audits']['BTCUSDT']['legacy_matched'],1)

    def test_price_away_and_back_new_evidence_cannot_unlock(self):
        self.ledger.observe(multi());self.send()
        s=multi(identifier='different');s['tracks']['short']['candidates'][0]['equilibrium']['eligible']=False
        self.ledger.observe(s);self.assertFalse(self.ledger.state['pending'])
        self.ledger.observe(multi(identifier='new-evidence'));self.assertEqual(self.send()['sent'],0)

    def test_failed_delivery_retries_only_failed_symbol(self):
        for symbol in ('BTCUSDT','NVDAUSDT'):self.ledger.observe(multi(symbol=symbol))
        sender=Mock(side_effect=[True,False]);self.assertEqual(self.send(sender)['sent'],1)
        for symbol in ('BTCUSDT','NVDAUSDT'):self.ledger.observe(multi(symbol=symbol))
        sender=Mock(return_value=True);self.assertEqual(self.send(sender)['sent'],1)
        self.assertEqual(sender.call_count,1);self.assertIn('NVDAUSDT',sender.call_args.args[0][0][1])

    def test_missing_history_keeps_lock_and_stale_pending_does_not_send(self):
        self.ledger.observe(multi());self.send()
        s=multi();s['tracks']['short']['status']='data_unavailable'
        self.ledger.observe(s);self.assertTrue(self.ledger.state['locks']['BTCUSDT|short'])
        self.assertFalse(self.ledger.state['pending'])

    def test_source_invalidation_rearms_a_new_zone(self):
        s=multi();c=s['tracks']['short']['candidates'][0]
        c['sources']=[{'id':'a','origin':1,'timeframe':'4h'}]
        self.ledger.observe(s);self.send()
        s=multi(identifier='new-after-invalidation');s['valid_source_ids']=[]
        self.ledger.observe(s);self.assertEqual(self.send()['sent'],1)

    def test_preview_does_not_modify_persistent_state(self):
        self.ledger.observe(multi(),persist=False)
        self.assertFalse(self.path.exists());self.assertFalse(self.ledger.state['tracks'])

    def test_unknown_network_result_is_held_instead_of_duplicated(self):
        class UncertainSender:
            last_receipt={'outcome':'unknown'}
            def send_images(self,items):return False
        self.ledger.observe(multi());self.assertEqual(self.send(UncertainSender().send_images)['uncertain'],1)
        self.ledger=StructureLedger(self.path,require_images=True,overlap_only=True,approach_only=True)
        self.ledger.observe(multi())
        self.assertFalse(self.ledger.state['pending'])
        self.assertEqual(self.ledger.state['dedup_audits']['BTCUSDT']['uncertain_held'],1)

    def test_confirmed_telegram_ids_are_saved_with_the_delivery(self):
        class ConfirmedSender:
            last_receipt={'outcome':'confirmed','message_ids':[101,102]}
            def send_images(self,items):return True
        self.ledger.observe(multi());self.send(ConfirmedSender().send_images)
        self.assertEqual(next(iter(self.ledger.state['delivered'].values()))['message_ids'],[101,102])

    def test_maximum_caption_fits_and_labels_evidence_per_zone(self):
        events=[]
        for track,pair in [('short',['4h','1h']),('medium',['1d','4h']),('long',['1w','1d'])]:
            for lens in ('broad','recent'):
                for d in (-1,1):
                    c=multi()['tracks']['short']['candidates'][0]
                    c.update(track=track,pair=pair,lenses=[lens],direction=d,low=123456.7,high=123999.9,
                             groups=[tf+':'+kind for tf in pair for kind in ('OB','FVG')],score=4)
                    events.append({'candidate':c,'timestamp':1000})
        self.assertLessEqual(len(notice(events)),1024)


class TimeframeTests(unittest.TestCase):
    def test_turn_and_sweep_confirmation_uses_own_timeframe(self):
        from tests.test_structure_review import bars
        for tf in ('4h','1d','1w'):
            values=bars([100]*15+[98,95,90,94,100,110,105,100,95],tf)
            found=turns(values,2,.04,tf)
            self.assertTrue(found)
            for p in found:self.assertEqual(p['confirmed_at'],values[p['confirmed_index']].timestamp+TF_MS[tf])
            for a in departure_areas(values,found,'broad',tf):self.assertIn(a['created'],[p['confirmed_at'] for p in found])


if __name__=='__main__':unittest.main()
