from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from PIL import Image
from src.ict_engine import Bar,TF_MS
from src.overlap_charts import render_chart,TF_COLORS
from src.overlap_telegram import TelegramMedia
from src.overlap_delivery import OverlapLedger
from tests.test_ict_overlap import snapshot


def chart_example(direction=1):
    s=snapshot(price=120);c=s['tracks']['short']['candidate'];c.update(direction=direction,formed_at=30*TF_MS['4h'],sources=[{'timeframe':'4h','kind':'OB','origin':0,'low':100,'high':115},{'timeframe':'1h','kind':'FVG','origin':0,'low':102,'high':110}])
    frames={tf:[Bar(i*TF_MS[tf],110,125,95,120 if i%2 else 108,1) for i in range(30)] for tf in ('4h','1h')}
    return {'candidate':c,'timestamp':30*TF_MS['4h'],'stage':'prepared','new_formation':True},frames


class ChartTests(unittest.TestCase):
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
