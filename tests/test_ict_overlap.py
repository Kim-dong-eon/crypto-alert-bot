import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from config.overlap_settings import TRACKS
from src.ict_engine import Bar,TF_MS,frame_open
from src.ict_nested import PDZone
from src.ict_overlap import Registry,overlaps,scan,format_notice
from src.overlap_market import select_instruments,quote_price,PublicMarket
from src.overlap_delivery import OverlapLedger


def zone(key,tf,kind,low,high,d=1):
    return PDZone(key,tf,kind,d,low,high,100,0,low-1 if d==1 else high+1,low if d==1 else high)


def snapshot(price=120,identifier='one',low=100,high=110,stamp=1000):
    c={'id':identifier,'symbol':'BTCUSDT','track':'short','direction':1,'context_id':'flow','context':{'id':'flow','swept_level':90,'structure_level':115},'pair':['4h','1h'],'low':low,'high':high,'score':2,'groups':['4h:OB','1h:FVG'],'sources':[], 'source_ids':['a','b'],'formed_at':900,'location':'inside' if low<=price<=high else 'waiting','price':price,'context_price_breach':False,'resistance_support':[]}
    return {'symbol':'BTCUSDT','as_of':stamp,'price':price,'valid_source_ids':['a','b'],'tracks':{'short':{'status':'watching','candidate':c,'context':{'id':'flow'}}}}


class ZoneTests(unittest.TestCase):
    def test_two_timeframes_and_types_share_actual_price(self):
        z=[zone('a','4h','OB',100,120),zone('b','1h','FVG',105,115),zone('c','4h','FVG',107,113),zone('d','1h','OB',109,111)]
        best=overlaps(z,('4h','1h'),120)[0];self.assertEqual((best['low'],best['high'],best['score']),(109,111,4))

    def test_duplicate_group_does_not_inflate_score(self):
        z=[zone(str(i),'4h','OB',100,120) for i in range(20)]+[zone('b','1h','OB',105,115)]
        self.assertEqual(overlaps(z,('4h','1h'),120)[0]['score'],2)

    def test_disjoint_or_boundary_contact_is_not_overlap(self):
        self.assertEqual(overlaps([zone('a','4h','OB',100,110),zone('b','1h','FVG',110,120)],('4h','1h'),120),[])

    def test_only_source_timeframe_close_invalidates(self):
        r=Registry();r.zones['a']=zone('a','4h','OB',100,110)
        r.step('1h',Bar(0,101,102,98,99));self.assertIn('a',r.zones)
        r.step('4h',Bar(0,101,102,98,101));self.assertIn('a',r.zones)
        r.step('4h',Bar(TF_MS['4h'],101,102,98,99));self.assertNotIn('a',r.zones)

    def test_body_ob_and_three_candle_fvg(self):
        r=Registry();r.step('1h',Bar(0,110,111,99,100));made=r.step('1h',Bar(3600000,100,115,100,114))
        self.assertEqual((made[0].kind,made[0].low,made[0].high),('OB',100,110))
        made=r.step('1h',Bar(7200000,114,119,112,118));self.assertTrue(any(z.kind=='FVG' and z.low==111 and z.high==112 for z in made))

    def test_tracks_match_user_roles(self):
        self.assertEqual(TRACKS['short'],('4h','1h'));self.assertEqual(TRACKS['medium'],('1d','4h'));self.assertEqual(TRACKS['long'],('1w','1d'))
        self.assertNotIn('15m',{tf for pair in TRACKS.values() for tf in pair});self.assertNotIn('auxiliary',TRACKS)

    def test_missing_timeframe_pauses_only_affected_track(self):
        now=8*TF_MS['4h'];frames={'4h':[Bar(i*TF_MS['4h'],100,101,99,100) for i in range(8)]}
        result=scan('TEST',frames,now,100,{'short':('4h','1h')});self.assertEqual(result['tracks']['short']['status'],'data_unavailable')

    def test_weekly_monday_boundary(self):
        self.assertEqual(frame_open(4*TF_MS['1d'],'1w'),4*TF_MS['1d'])


class MarketTests(unittest.TestCase):
    def test_crypto_only_btc_and_verified_stock_metadata(self):
        def item(base,kind='crypto',asset='crypto'):
            return {'baseCoin':base,'symbol':base+'USDT','category':'USDT-FUTURES','quoteCoin':'USDT','status':'online','type':'perpetual','symbolType':kind,'instrumentType':asset,'isRwa':'YES'}
        rows=[item('BTC'),item('ETH'),item('SKHYNIX','stock','stock'),item('QQQ','stock','etf'),item('ONDO')]
        self.assertEqual([x['baseCoin'] for x in select_instruments(rows,('BTC','ETH','SKHYNIX','QQQ','ONDO'))],['BTC','SKHYNIX','QQQ'])

    def test_stale_ticker_rejected(self):
        with self.assertRaises(ValueError):quote_price({'lastPrice':'100','ts':'0'},121000)

    def test_unclosed_candle_not_cached_as_structure(self):
        with tempfile.TemporaryDirectory() as directory:
            m=PublicMarket(directory);m.get=Mock(return_value={'data':[[0,100,101,99,100,1],[3600000,100,110,99,109,1]]})
            b=m.frame('BTCUSDT','1h',3600001);self.assertEqual(len(b),1);self.assertEqual(b[0].close,100)

    def test_clock_advance_cannot_promote_cached_partial_candle(self):
        with tempfile.TemporaryDirectory() as directory:
            m=PublicMarket(directory);m.get=Mock(side_effect=[{'data':[[0,100,101,99,100,1],[3600000,100,110,99,105,1]]},{'data':[[0,100,101,99,100,1],[3600000,100,110,99,109,2],[7200000,109,111,108,110,1]]}])
            m.frame('BTCUSDT','1h',3600001)
            bars=m.frame('BTCUSDT','1h',7200001)
            self.assertEqual(m.get.call_count,2);self.assertEqual(bars[-1].close,109)


class LockTests(unittest.TestCase):
    def test_v9_migration_preserves_acknowledgements_and_identical_external_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'s.json'
            s=snapshot();c=s['tracks']['short']['candidate']
            context={'id':'real:4h:500:900:1','direction':1,'formed':900,'sweep_time':500,'swept_level':90,'structure_level':115}
            c.update(context=copy.deepcopy(context),context_id=context['id'],equilibrium={'eligible':True,'midpoint':115})
            s['tracks']['short']['context']=copy.deepcopy(context)
            ledger=OverlapLedger(path,require_images=True,overlap_only=True,approach_only=True)
            ledger.observe(s);ledger.dispatch_symbols(lambda _:True,lambda _:Path('x'),{})
            old=json.loads(path.read_text());old['version']='ict-two-frame-ob-fvg-v9'
            old['pending']['unvalidated-old-rule-event']={'candidate':c,'stage':'approach'}
            path.write_text(json.dumps(old))
            migrated=OverlapLedger(path,require_images=True,overlap_only=True,approach_only=True)
            self.assertEqual(migrated.state['delivered'],old['delivered'])
            self.assertEqual(migrated.state['images_delivered'],old['images_delivered'])
            self.assertFalse(migrated.state['pending'])
            s['tracks']['short']['context']['id']='external:4h:500:900:1'
            c['context']=copy.deepcopy(s['tracks']['short']['context']);c['context_id']=c['context']['id'];c['id']='new-rule-hash'
            migrated.observe(s)
            self.assertFalse(migrated.state['pending'])
            self.assertEqual(migrated.state['locks']['BTCUSDT|short'][0]['latest']['id'],'one')

    def test_unknown_state_version_remains_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'s.json';path.write_text(json.dumps({'version':'unknown'}))
            with self.assertRaises(ValueError):OverlapLedger(path)

    def test_transient_file_replace_lock_preserves_durable_delivery(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'s.json');replace=os.replace;attempts=[]
            def transient(source,target):
                attempts.append(1)
                if len(attempts)==1:raise PermissionError('temporary file scanner lock')
                return replace(source,target)
            with patch('src.overlap_delivery.os.replace',side_effect=transient):ledger.observe(snapshot())
            self.assertTrue(ledger.path.exists());self.assertEqual(len(attempts),2)

    def test_once_formation_once_touch_and_restart_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.json';ledger=OverlapLedger(path);sender=Mock(return_value=True)
            ledger.observe(snapshot());self.assertEqual(ledger.dispatch(sender)['sent'],1)
            ledger=OverlapLedger(path);ledger.observe(snapshot(stamp=2000));self.assertEqual(ledger.dispatch(sender)['sent'],0)
            ledger.observe(snapshot(price=105,stamp=3000));self.assertEqual(ledger.dispatch(sender)['sent'],1)
            ledger.observe(snapshot(price=120,stamp=4000));ledger.dispatch(sender)
            ledger.observe(snapshot(price=105,stamp=5000));self.assertEqual(ledger.dispatch(sender)['sent'],0)
            self.assertEqual(sender.call_count,2)

    def test_new_evidence_on_same_band_does_not_unlock(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'s.json');sender=Mock(return_value=True);ledger.observe(snapshot());ledger.dispatch(sender)
            s=snapshot(identifier='new-evidence',low=103,high=108,stamp=2000);s['valid_source_ids'].append('c');s['tracks']['short']['candidate']['source_ids']=['a','c'];ledger.observe(s)
            self.assertEqual(ledger.dispatch(sender)['sent'],0)

    def test_selection_away_then_back_does_not_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'s.json');sender=Mock(return_value=True)
            for s in [snapshot(),snapshot(identifier='two',low=80,high=85,stamp=2000),snapshot(stamp=3000)]:ledger.observe(s);ledger.dispatch(sender)
            self.assertEqual(sender.call_count,2)

    def test_invalidated_source_unlocks_and_sends_one_end(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'s.json');sender=Mock(return_value=True);ledger.observe(snapshot());ledger.dispatch(sender)
            s=snapshot(stamp=2000);s['valid_source_ids']=['a'];s['tracks']['short']['candidate']=None;s['tracks']['short']['status']='no_overlap'
            ledger.observe(s);self.assertEqual(ledger.dispatch(sender)['sent'],1);ledger.observe(s);self.assertEqual(ledger.dispatch(sender)['sent'],0)

    def test_failure_retries_but_never_retries_invalidated_unseen_alert(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'s.json');ledger.observe(snapshot());self.assertEqual(ledger.dispatch(lambda _:False)['failed'],1)
            ledger.observe(snapshot(stamp=2000));self.assertEqual(ledger.dispatch(lambda _:True)['sent'],1)
            unseen=snapshot(identifier='two',low=80,high=85,stamp=3000);ledger.observe(unseen);ledger.dispatch(lambda _:False)
            unseen['tracks']['short']['candidate']=None;unseen['tracks']['short']['context']=None;unseen['tracks']['short']['status']='sweep_mss_unconfirmed';ledger.observe(unseen)
            self.assertFalse(any(e['candidate']['id']=='two' for e in ledger.state['pending'].values()))

    def test_missing_data_does_not_unlock_or_send_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'s.json');ledger.observe(snapshot());s=snapshot();s['tracks']['short']['status']='data_unavailable';ledger.observe(s)
            self.assertFalse(ledger.state['pending']);self.assertTrue(ledger.state['locks']['BTCUSDT|short'])

    def test_preview_has_no_state_side_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'s.json';ledger=OverlapLedger(p);self.assertTrue(ledger.observe(snapshot(),persist=False));self.assertFalse(p.exists());self.assertFalse(ledger.state['tracks'])

    def test_manual_unlock_rearms_one_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'s.json');ledger.observe(snapshot());ledger.dispatch(lambda _:True);ledger.unlock('BTCUSDT','short');ledger.observe(snapshot());self.assertEqual(ledger.dispatch(lambda _:True)['sent'],1)

    def test_auxiliary_is_report_only(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'s.json');s=snapshot();s['tracks']={'auxiliary':s['tracks']['short']};self.assertFalse(ledger.observe(s))

    def test_first_observation_does_not_claim_an_old_zone_just_formed(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'s.json');e=next(iter(ledger.observe(snapshot()).values()));self.assertIn('현재 유효 중첩 구간',format_notice(e));self.assertNotIn('OTE',format_notice(e))

    def test_batched_delivery_acknowledges_each_zone_once(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'s.json');s=snapshot();ledger.observe(s)
            second=snapshot();second['symbol']='NVDAUSDT';second['tracks']['short']['candidate']['symbol']='NVDAUSDT';ledger.observe(second)
            sender=Mock(return_value=True);self.assertEqual(ledger.dispatch(sender,batch=True)['sent'],2);self.assertEqual(sender.call_count,1);self.assertEqual(len(ledger.state['delivered']),2)

    def test_actions_cache_miss_does_not_imply_old_zone_invalidated(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'s.json');s=snapshot();s['tracks']['short']['candidate']['sources']=[{'id':'a','timeframe':'4h','origin':0,'kind':'OB','low':100,'high':110},{'id':'b','timeframe':'1h','origin':0,'kind':'FVG','low':100,'high':110}]
            ledger.observe(s);ledger.dispatch(lambda _:True)
            s['valid_source_ids']=[];s['history_starts']={'4h':100,'1h':100};s['tracks']['short']['candidate']=None;s['tracks']['short']['status']='no_overlap';ledger.observe(s)
            self.assertFalse(ledger.state['pending']);self.assertEqual(len(ledger.state['locks']['BTCUSDT|short']),1)


if __name__=='__main__':unittest.main()
