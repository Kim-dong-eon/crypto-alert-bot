from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from PIL import Image
from src.ict_engine import Bar,TF_MS
from src.overlap_charts import render_chart,render_symbol_chart,TF_COLORS
from src.ict_overlap import compact_notice,equilibrium_gate
from src.overlap_telegram import TelegramMedia
from src.overlap_delivery import OverlapLedger
from tests.test_ict_overlap import snapshot


def chart_example(direction=1):
    s=snapshot(price=120);c=s['tracks']['short']['candidate'];c.update(direction=direction,formed_at=30*TF_MS['4h'],sources=[{'timeframe':'4h','kind':'OB','origin':0,'low':100,'high':115},{'timeframe':'1h','kind':'FVG','origin':0,'low':102,'high':110}])
    frames={tf:[Bar(i*TF_MS[tf],110,125,95,120 if i%2 else 108,1) for i in range(30)] for tf in ('4h','1h')}
    return {'candidate':c,'timestamp':30*TF_MS['4h'],'stage':'prepared','new_formation':True},frames


class ChartTests(unittest.TestCase):
    def test_eq50_approach_long_and_short_are_mirrored(self):
        self.assertFalse(equilibrium_gate(110,115,125,1,100,140)['eligible'])
        self.assertTrue(equilibrium_gate(110,115,119,1,100,140)['eligible'])
        self.assertTrue(equilibrium_gate(110,115,112,1,100,140)['eligible'])
        self.assertFalse(equilibrium_gate(110,115,109,1,100,140)['eligible'])
        self.assertFalse(equilibrium_gate(125,130,115,-1,100,140)['eligible'])
        self.assertTrue(equilibrium_gate(125,130,121,-1,100,140)['eligible'])
        self.assertTrue(equilibrium_gate(125,130,128,-1,100,140)['eligible'])
        self.assertFalse(equilibrium_gate(125,130,131,-1,100,140)['eligible'])

    def test_zone_on_wrong_half_or_invalid_range_cannot_alert(self):
        self.assertFalse(equilibrium_gate(125,130,127,1,100,140)['eligible'])
        self.assertFalse(equilibrium_gate(110,115,112,-1,100,140)['eligible'])
        self.assertFalse(equilibrium_gate(110,115,112,1,140,100)['eligible'])

    def test_eq50_wait_then_approach_once_and_keep_lock_after_retreat(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'s.json',require_images=True,overlap_only=True,approach_only=True)
            s=snapshot();s['tracks']['short']['candidate']['equilibrium']={'midpoint':115,'eligible':False};ledger.observe(s);self.assertFalse(ledger.state['pending'])
            s['tracks']['short']['candidate']['equilibrium']['eligible']=True;ledger.observe(s);self.assertEqual(ledger.dispatch_symbols(lambda _:True,lambda _:Path('x'),{})['images_sent'],1)
            s['tracks']['short']['candidate']['equilibrium']['eligible']=False;ledger.observe(s);s['tracks']['short']['candidate']['equilibrium']['eligible']=True;ledger.observe(s);self.assertFalse(ledger.state['pending'])
            ledger.unlock('BTCUSDT','short');ledger.observe(s);self.assertTrue(ledger.state['pending'])

    def test_failed_approach_does_not_deliver_after_price_moves_away(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'s.json',require_images=True,overlap_only=True,approach_only=True);s=snapshot();s['tracks']['short']['candidate']['equilibrium']={'midpoint':115,'eligible':True};ledger.observe(s)
            self.assertEqual(ledger.dispatch_symbols(lambda _:False,lambda _:Path('x'),{})['failed'],1)
            s['tracks']['short']['candidate']['equilibrium']['eligible']=False;ledger.observe(s);self.assertFalse(ledger.state['pending'])

    def test_compact_caption_lists_only_overlaps_in_scope_order(self):
        import copy
        short=snapshot()['tracks']['short']['candidate'];medium=copy.deepcopy(short);medium.update(track='medium',pair=['1d','4h'],groups=['1d:OB','4h:FVG'])
        events=[{'candidate':medium,'timestamp':1000},{'candidate':short,'timestamp':1000}]
        text=compact_notice(events);self.assertLess(text.index('단기 겹침'),text.index('중기 겹침'));self.assertNotIn('장기',text);self.assertNotIn('MSS',text);self.assertNotIn('무효화',text);self.assertLess(len(text),250)

    def test_two_scopes_of_one_symbol_produce_one_image_and_two_locks(self):
        import copy
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'s.json',require_images=True,overlap_only=True);s=snapshot();medium=copy.deepcopy(s['tracks']['short']);medium['candidate'].update(id='medium',track='medium');s['tracks']['medium']=medium
            ledger.observe(s);images=Mock(return_value=True);renderer=Mock(return_value=Path('symbol.png'))
            r=ledger.dispatch_symbols(images,renderer,{})
            self.assertEqual(r,{'sent':2,'failed':0,'images_sent':1});self.assertEqual(len(images.call_args.args[0]),1);self.assertEqual(len(ledger.state['images_delivered']),2)
            ledger.observe(s);self.assertEqual(ledger.dispatch_symbols(images,renderer,{})['images_sent'],0)

    def test_overlap_only_does_not_repeat_on_touch_or_send_termination(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'s.json',require_images=True,overlap_only=True);ledger.observe(snapshot());ledger.dispatch_symbols(lambda _:True,lambda _:Path('x'),{})
            ledger.observe(snapshot(price=105,stamp=2000));self.assertFalse(ledger.state['pending'])
            s=snapshot();s['tracks']['short']['candidate']=None;s['tracks']['short']['context']=None;s['tracks']['short']['status']='sweep_mss_unconfirmed';ledger.observe(s);self.assertFalse(ledger.state['pending'])

    def test_failed_combined_photo_keeps_all_scopes_for_retry(self):
        import copy
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'s.json',require_images=True,overlap_only=True);s=snapshot();medium=copy.deepcopy(s['tracks']['short']);medium['candidate'].update(id='medium',track='medium');s['tracks']['medium']=medium;ledger.observe(s)
            self.assertEqual(ledger.dispatch_symbols(lambda _:False,lambda _:Path('x'),{})['failed'],2);self.assertEqual(len(ledger.state['pending']),2)
            self.assertEqual(ledger.dispatch_symbols(lambda _:True,lambda _:Path('x'),{})['images_sent'],1);self.assertFalse(ledger.state['pending'])

    def test_symbol_image_can_hold_only_the_matching_scopes(self):
        import copy
        with tempfile.TemporaryDirectory() as directory:
            e,frames=chart_example();medium=copy.deepcopy(e);medium['candidate'].update(track='medium',pair=['1d','4h']);frames['1d']=frames['4h']
            p=render_symbol_chart([medium,e],frames,Path(directory)/'one_symbol.png')
            with Image.open(p) as im:self.assertGreater(im.height,1000)

    def test_long_and_short_render_readable_single_panel_box_images(self):
        with tempfile.TemporaryDirectory() as directory:
            for d in (1,-1):
                event,frames=chart_example(d);p=render_chart(event,frames,Path(directory)/f'{d}.png')
                with Image.open(p) as im:self.assertGreaterEqual(im.width,1400);self.assertGreaterEqual(im.height,750)
        self.assertEqual(len(set(TF_COLORS.values())),5)

    def test_unclosed_bar_cannot_extend_chart_price_range(self):
        with tempfile.TemporaryDirectory() as directory:
            event,frames=chart_example();tf=event['candidate']['pair'][-1];frames[tf].append(Bar(event['timestamp'],1,1000000,1,500000))
            self.assertTrue(render_chart(event,frames,Path(directory)/'closed.png').exists())

    def test_failed_photo_is_not_acknowledged_then_retry_locks(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=OverlapLedger(Path(directory)/'s.json',require_images=True);ledger.observe(snapshot());renderer=Mock(return_value=Path(directory)/'x.png')
            self.assertEqual(ledger.dispatch_media(lambda _:True,lambda _:False,renderer)['failed'],1);self.assertFalse(ledger.state['images_delivered'])
            self.assertEqual(ledger.dispatch_media(lambda _:True,lambda _:True,renderer)['images_sent'],1)
            ledger=OverlapLedger(Path(directory)/'s.json',require_images=True);ledger.observe(snapshot());self.assertFalse(ledger.state['pending'])

    def test_prior_text_lock_gets_one_photo_without_resending_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'s.json';ledger=OverlapLedger(path);ledger.observe(snapshot());ledger.dispatch(lambda _:True)
            ledger=OverlapLedger(path,require_images=True);ledger.observe(snapshot());text=Mock(return_value=True)
            self.assertEqual(ledger.dispatch_media(text,lambda _:True,lambda _:Path('x'))['images_sent'],1);text.assert_not_called()
            ledger.observe(snapshot());self.assertFalse(ledger.state['pending'])

    def test_telegram_single_and_album_methods_no_external_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'x.png';p.write_bytes(b'image')
            with patch('src.overlap_telegram.requests.post') as post:
                post.return_value.status_code=200;post.return_value.json.return_value={'ok':True,'result':{}}
                sender=TelegramMedia('test-token','test-chat');self.assertTrue(sender.send_images([(p,'caption')]))
                self.assertTrue(post.call_args.args[0].endswith('/sendPhoto'))
                post.return_value.json.return_value={'ok':True,'result':[{},{}]};self.assertTrue(sender.send_images([(p,'one'),(p,'two')]))
                self.assertTrue(post.call_args.args[0].endswith('/sendMediaGroup'))


if __name__=='__main__':unittest.main()
