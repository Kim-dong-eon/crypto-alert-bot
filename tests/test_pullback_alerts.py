import copy
import json
from pathlib import Path
import tempfile
import unittest
from src.ict_engine import Bar,TF_MS
from src.ict_overlap import alert_eligible
from src.pullback_alerts import scan,prepare,rooted_context,POLICY,unit_text
from src.pullback_delivery import PullbackAlertLedger


def fixture():
    data=json.loads((Path(__file__).parent/'fixtures/aapl_origin_pullback.json').read_text(encoding='utf-8'))
    frames={tf:[Bar(*r) for r in rows] for tf,rows in data['frames'].items()}
    minutes=[Bar(*r) for r in data['minutes']]
    return data,frames,minutes


def observation():
    data,frames,minutes=fixture()
    return scan(data['symbol'],frames,data['as_of'],data['price'],tracks={'short':('4h','1h')},
                minute_bars=minutes,touch_since=data['touch_since'])


def approved_candidate(state):
    return next(c for c in state['tracks']['short']['candidates'] if c['parent']['low']==342.96 and alert_eligible(c))


class PullbackOriginTests(unittest.TestCase):
    def test_actual_aapl_origin_survives_old_lower_sources_and_needs_no_new_mss(self):
        s=observation();c=approved_candidate(s)
        self.assertEqual((c['low'],c['high']),(343.23,343.33))
        self.assertEqual(c['score'],3)
        self.assertEqual(c['parent']['kind'],'OB')
        self.assertTrue(any(z['timeframe']=='15m' and z['kind']=='FVG' for z in c['sources']))
        self.assertTrue(c['structure_area']['bos_at'])
        self.assertLess(c['latest_source'],s['as_of'])
        self.assertTrue(c['entry_touch']['hit'])

    def test_between_scan_touch_still_sends_when_current_price_has_left_the_box(self):
        data,frames,minutes=fixture()
        s=scan(data['symbol'],frames,data['as_of'],342.5,tracks={'short':('4h','1h')},
               minute_bars=minutes,touch_since=data['touch_since'])
        c=approved_candidate(s)
        self.assertEqual(c['entry_touch']['evidence'],'completed_1m_range')
        self.assertEqual(c['location'],'outside_after_touch')

    def test_an_old_touch_before_cursor_does_not_replay_outside_the_box(self):
        data,frames,minutes=fixture()
        s=scan(data['symbol'],frames,data['as_of'],342.5,tracks={'short':('4h','1h')},
               minute_bars=minutes,touch_since=data['as_of'])
        c=next(c for c in s['tracks']['short']['candidates'] if c['parent']['low']==342.96)
        self.assertFalse(alert_eligible(c))

    def test_no_15m_no_short_alert_and_no_fvg_only_parent(self):
        data,frames,minutes=fixture();frames.pop('15m')
        s=scan(data['symbol'],frames,data['as_of'],data['price'],tracks={'short':('4h','1h')},minute_bars=minutes,touch_since=data['touch_since'])
        self.assertFalse(any(alert_eligible(c) for c in s['tracks']['short']['candidates']))
        prepared,_,_=prepare(data['symbol'],frames,data['as_of'],data['price'],{'short':('4h','1h')})
        for v in prepared['tracks']['short']['analysis']['views'].values():
            for a in v['selected']:
                for c in a['overlaps']:self.assertEqual(c['parent']['kind'],'OB')

    def test_short_mirrors_long_in_price_and_direction(self):
        data,frames,minutes=fixture();ceiling=700
        mirror=lambda b:Bar(b.timestamp,ceiling-b.open,ceiling-b.low,ceiling-b.high,ceiling-b.close,b.volume)
        reflected={tf:[mirror(b) for b in values] for tf,values in frames.items()}
        s=scan('MIRROR',reflected,data['as_of'],ceiling-data['price'],tracks={'short':('4h','1h')},
               minute_bars=[mirror(b) for b in minutes],touch_since=data['touch_since'])
        c=next(c for c in s['tracks']['short']['candidates'] if c['direction']==-1 and abs(c['parent']['high']-(ceiling-342.96))<1e-8 and alert_eligible(c))
        self.assertAlmostEqual(c['low'],ceiling-343.33)
        self.assertAlmostEqual(c['high'],ceiling-343.23)

    def test_notice_has_no_risk_prices_and_counts_15m_separately(self):
        s=observation();c=approved_candidate(s);text=unit_text({'candidate':c,'timestamp':s['as_of']})
        self.assertIn('상위·중간 3/4',text)
        self.assertIn('15분 FVG',text)
        for word in ('손절','익절','3R'):self.assertNotIn(word,text)

    def test_new_ob_after_opposite_extreme_cannot_replace_origin_ob(self):
        from src.ict_nested import PDZone
        width=TF_MS['4h']
        bars=[Bar(i*width,100,130,90,105,1) for i in range(8)]
        points=[{'kind':'high','index':0,'price':120,'confirmed_at':width},
                {'kind':'low','index':2,'price':90,'confirmed_at':4*width},
                {'kind':'high','index':5,'price':130,'confirmed_at':7*width}]
        areas=[{'pivot_origin':2*width,'direction':1,'origin':width,'range_low':90,'range_high':130}]
        later=PDZone('later','4h','OB',1,100,104,8*width,6*width,90,100)
        self.assertIsNone(rooted_context(later,bars,points,areas,'4h',8*width))

    def test_approved_chart_renders_without_trade_risk_annotations(self):
        from src.pullback_charts import render_unit
        from PIL import Image
        data,frames,_=fixture();state=observation();c=approved_candidate(state)
        with tempfile.TemporaryDirectory() as directory:
            rendered=render_unit(state,{'candidate':c,'timestamp':state['as_of']},frames,Path(directory)/'aapl')
            self.assertEqual(len(rendered['images']),1)
            self.assertEqual(rendered['image_captions'],[''])
            with Image.open(rendered['images'][0]) as im:self.assertGreater(im.width,2000)
            self.assertNotIn('손절',rendered['text_after_images'])

    def test_chart_keeps_old_origin_even_when_more_than_96_bars_ago(self):
        from src.pullback_charts import origin_window
        width=TF_MS['4h'];bars=[Bar(i*width,100,110,90,105,1) for i in range(350)]
        c={'parent':{'origin':20*width},'structure_area':{'origin':10*width,'pivot_origin':12*width}}
        shown=origin_window(bars,c)
        self.assertEqual(shown[0].timestamp,7*width)
        self.assertEqual(shown[-1].timestamp,349*width)
        self.assertIn(20*width,[b.timestamp for b in shown])
        self.assertIn(12*width,[b.timestamp for b in shown])


class LockedDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'state.json'

    def sample(self):
        s=observation();c=approved_candidate(s)
        s['tracks']['short'].update(candidate=c,candidates=[c]);return s

    def test_same_parent_stays_locked_after_restart(self):
        ledger=PullbackAlertLedger(self.path,require_images=True,overlap_only=True,approach_only=True)
        s=self.sample();ledger.observe(s)
        renderer=lambda _: {'images':['a.png']}
        first=ledger.dispatch_units(lambda _:True,lambda _:True,renderer)
        self.assertEqual(first['units_sent'],1)
        s['as_of']+=300000
        restarted=PullbackAlertLedger(self.path,require_images=True,overlap_only=True,approach_only=True)
        restarted.observe(s)
        second=restarted.dispatch_units(lambda _:True,lambda _:True,renderer)
        self.assertEqual(second['units_sent'],0)
        self.assertEqual(len(restarted.state['locks']['AAPLUSDT|short']),1)
        self.assertEqual(restarted.state['dedup_audits']['AAPLUSDT']['suppressed'],1)
        self.assertEqual(len(restarted.state['delivered']),1)

    def test_explicit_resend_bypasses_once_then_restores_the_lock(self):
        renderer=lambda _:{'images':['a.png']}
        ledger=PullbackAlertLedger(self.path,require_images=True,overlap_only=True,approach_only=True)
        s=self.sample();ledger.observe(s);ledger.dispatch_units(lambda _:True,lambda _:True,renderer)
        s['as_of']+=300000
        resend=PullbackAlertLedger(self.path,require_images=True,overlap_only=True,approach_only=True,force_resend=True)
        resend.observe(s)
        self.assertEqual(resend.dispatch_units(lambda _:True,lambda _:True,renderer)['units_sent'],1)
        self.assertFalse(resend.force_resend)
        s['as_of']+=300000;resend.observe(s)
        self.assertEqual(resend.dispatch_units(lambda _:True,lambda _:True,renderer)['units_sent'],0)

    def test_receipts_from_unlocked_mode_seed_restored_locks(self):
        ledger=PullbackAlertLedger(self.path,require_images=True,overlap_only=True,approach_only=True)
        s=self.sample();ledger.observe(s);ledger.dispatch_units(lambda _:True,lambda _:True,lambda _:{'images':['a.png']})
        ledger.state['delivery_mode']='every_qualifying_scan';ledger.state['locks']={};ledger.save()
        restored=PullbackAlertLedger(self.path,require_images=True,overlap_only=True,approach_only=True)
        s['as_of']+=300000
        self.assertFalse(restored.observe(s))
        self.assertTrue(restored.state['locks']['AAPLUSDT|short'][0]['sent'])

    def test_changed_lower_zone_does_not_unlock_the_same_upper_ob(self):
        ledger=PullbackAlertLedger(self.path,require_images=True,overlap_only=True,approach_only=True)
        s=self.sample();ledger.observe(s);ledger.dispatch_units(lambda _:True,lambda _:True,lambda _:{'images':['a.png']})
        s['as_of']+=300000;c=s['tracks']['short']['candidates'][0]
        c['id']='new-lower-evidence';c['zone_id']='new-lower-evidence'
        c['sources'][-1]={**c['sources'][-1],'id':'new-15m-zone'}
        self.assertFalse(ledger.observe(s))

    def test_big_and_small_share_one_parent_lock_and_keep_separate_photos(self):
        ledger=PullbackAlertLedger(self.path,require_images=True,overlap_only=True,approach_only=True)
        s=self.sample();other=copy.deepcopy(s['tracks']['short']['candidates'][0]);other['lenses']=['broad']
        s['tracks']['short']['candidates'].append(other)
        self.assertEqual(len(ledger.observe(s)),1)
        result=ledger.dispatch_units(lambda _:True,lambda _:True,lambda _:{'images':['a.png']})
        self.assertEqual(result['units_sent'],2)
        self.assertEqual(len(ledger.state['locks']['AAPLUSDT|short']),1)
        s['as_of']+=300000;self.assertFalse(ledger.observe(s))

    def test_same_attempt_resumes_text_without_reposting_confirmed_photo(self):
        ledger=PullbackAlertLedger(self.path,require_images=True,overlap_only=True,approach_only=True)
        ledger.observe(self.sample());photos=[]
        sender=lambda items:photos.append(items) or True
        ledger.dispatch_units(sender,lambda _:False,lambda _:{'images':['a.png']})
        second=ledger.dispatch_units(sender,lambda _:True,lambda _:{'images':['a.png']})
        self.assertEqual(len(photos),1);self.assertEqual(second['texts_sent'],1)

    def test_preview_cannot_persist_or_delete_old_receipts(self):
        ledger=PullbackAlertLedger(self.path)
        ledger.observe(self.sample(),persist=False)
        self.assertFalse(self.path.exists())
        self.assertFalse(ledger.state['pending'])


if __name__=='__main__':unittest.main()
