import copy
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from src.ict_overlap import compact_notice,overlap_count
from src.overlap_status import overview_events
from src.overlap_delivery import OverlapLedger
from src.overlap_charts import render_symbol_chart
from src.ict_engine import Bar,TF_MS
from tests.test_ict_overlap import snapshot,zone
from PIL import Image


def unconfirmed_snapshot():
    result=snapshot(price=108)
    result['valid_zones']=[asdict(z) for z in [zone('a','4h','OB',100,110),zone('b','1h','FVG',105,115),zone('c','1d','OB',200,210),zone('d','4h','FVG',212,220)]]
    result['tracks']['short']={'status':'sweep_mss_unconfirmed','context':None,'candidate':None,
                              'liquidity':{'last_transition':{'reason':'pending_raid_extreme_close_broken'}}}
    result['tracks']['medium']={'status':'sweep_confirmed_mss_waiting','context':None,'candidate':None,
                               'pending_sweep':{'direction':1,'sweep':900,'level':90,'structure':250}}
    result['tracks']['long']={'status':'data_unavailable','context':None,'candidate':None}
    return result


class StatusTests(unittest.TestCase):
    def test_overview_detail_pair_shares_one_caption_and_acknowledgement(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'s.json',require_images=True,overlap_only=True)
            ledger.observe(snapshot());sender=Mock(side_effect=[False,True])
            renderer=lambda _:[Path('full.png'),Path('detail.png')]
            self.assertEqual(ledger.dispatch_symbols(sender,renderer,{})['failed'],1)
            self.assertEqual(len(ledger.state['pending']),1)
            report=ledger.dispatch_symbols(sender,renderer,{})
            self.assertEqual(report,{'sent':1,'failed':0,'images_sent':2})
            items=sender.call_args.args[0];self.assertEqual(len(items),2)
            self.assertIn('2/4',items[0][1]);self.assertEqual(items[1][1],'')
            self.assertFalse(ledger.state['pending'])

    def test_waiting_scopes_are_visible_but_never_become_candidates(self):
        result=unconfirmed_snapshot();before=copy.deepcopy(result);events=overview_events(result)
        self.assertEqual(result,before)
        self.assertEqual([e['view']['track'] for e in events],['short','medium','long'])
        self.assertTrue(all(e['candidate'] is None for e in events))
        short,medium,long=[e['view'] for e in events]
        self.assertEqual((short['direction'],short['plot_direction'],short['count']),(0,1,2))
        self.assertTrue(short['reference']);self.assertIn('취소',short['reason'])
        self.assertEqual(medium['count'],0);self.assertIn('250.00 상회 마감 대기',medium['reason'])
        self.assertIsNone(long['count'])
        text=compact_notice(events)
        self.assertIn('단기 · 방향 미확정 · 참고 중첩 2/4',text)
        self.assertIn('중기 · 상승 전환 대기 · 중첩 0/4',text)
        self.assertIn('장기 · 자료 대기 · 중첩 ?/4',text)
        self.assertNotIn('대기 취소',text);self.assertNotIn('상회 마감 대기',text)
        self.assertLess(len(text),1024)

    def test_duplicate_zones_do_not_inflate_four_checks(self):
        c=snapshot()['tracks']['short']['candidate']
        c['sources']=[{'timeframe':'4h','kind':'OB'}]*20+[{'timeframe':'1h','kind':'FVG'}]
        self.assertEqual(overlap_count(c),2)

    def test_three_full_scopes_fit_one_photo_caption(self):
        result=snapshot(price=105);c=result['tracks']['short']['candidate'];c['equilibrium']={'eligible':True,'midpoint':115}
        for name,pair in [('medium',['1d','4h']),('long',['1w','1d'])]:
            state=copy.deepcopy(result['tracks']['short']);state['candidate'].update(track=name,pair=pair,groups=[pair[0]+':OB',pair[1]+':FVG']);result['tracks'][name]=state
        events=overview_events(result);text=compact_notice(events)
        self.assertEqual(text.count('중첩 2/4'),3);self.assertEqual(text.count('접근 알림'),3)
        self.assertLess(len(text),1024)

    def test_reference_and_unavailable_scopes_render_three_rows(self):
        result=unconfirmed_snapshot();result['as_of']=30*TF_MS['1d']
        frames={tf:[Bar(i*TF_MS[tf],105,120,95,110,1) for i in range(12)] for tf in ('1d','4h','1h')}
        with tempfile.TemporaryDirectory() as directory:
            path=render_symbol_chart(overview_events(result),frames,Path(directory)/'all.png')
            with Image.open(path) as image:self.assertGreaterEqual(image.height,2000)

    def test_symbols_send_separate_photos_partial_failure_retries_only_failed_symbol(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'s.json',require_images=True,overlap_only=True)
            for symbol in ('BTCUSDT','AAPLUSDT','BZUSDT'):
                result=snapshot();result['symbol']=symbol;result['tracks']['short']['candidate']['symbol']=symbol
                ledger.observe(result)
            sender=Mock(side_effect=[True,False,True])
            report=ledger.dispatch_symbols(sender,lambda _:Path('image.png'),{})
            self.assertEqual(report,{'sent':2,'failed':1,'images_sent':2})
            self.assertEqual(sender.call_count,3)
            self.assertTrue(all(len(call.args[0])==1 for call in sender.call_args_list))
            self.assertEqual(len(ledger.state['pending']),1);self.assertEqual(len(ledger.state['images_delivered']),2)
            retry=Mock(return_value=True);report=ledger.dispatch_symbols(retry,lambda _:Path('image.png'),{})
            self.assertEqual(report['sent'],1);self.assertEqual(retry.call_count,1)
            self.assertIn('AAPLUSDT',retry.call_args.args[0][0][1]);self.assertFalse(ledger.state['pending'])

    def test_requested_unconfirmed_status_is_not_recorded_as_signal_delivery(self):
        from run_ict_overlap import run_once
        market=Mock();market.tickers.return_value=({'BTCUSDT':{'lastPrice':'108','ts':'1000000'}},1000000)
        market.frame.return_value=[]
        result=unconfirmed_snapshot();result['as_of']=1000000
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'state.json',require_images=True,overlap_only=True,approach_only=True)
            photos=Mock(return_value=True)
            with patch('run_ict_overlap.time.time',return_value=1000),patch('run_ict_overlap.scan',return_value=result),patch('src.overlap_charts.render_symbol_chart',return_value=Path('manual.png')):
                report=run_once(market,[{'symbol':'BTCUSDT','baseCoin':'BTC'}],ledger,sender=Mock(),image_sender=photos,status_symbols=('BTCUSDT',))
            self.assertEqual(report['delivery']['sent'],0);self.assertEqual(report['status_delivery']['sent'],1)
            self.assertFalse(ledger.state['delivered']);self.assertFalse(ledger.state['pending'])
            self.assertEqual(photos.call_count,1);self.assertIn('요청한 현재 상태',photos.call_args.args[0][0][1])


if __name__=='__main__':unittest.main()
