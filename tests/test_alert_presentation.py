import copy
import unittest
from dataclasses import asdict

import matplotlib.pyplot as plt

from src.ict_engine import Bar
from src.star_alerts import scan, unit_text, contact_status
from src.star_charts import alert_marker, draw_panel, window
from tests.test_star_alerts import replay


def example():
    snapshot, frames = list(replay(-1))[2]
    now = snapshot['as_of']
    scan_result = scan('BTCUSDT',frames,now,64935.,tracks={'short':('4h','1h')},
                       formation_tracks=('short',),
                       minute_bars=[Bar(now-60000,64935.,64935.,64935.,64935.,1)],
                       first_touch_cutoff=now)
    c = copy.deepcopy(scan_result['tracks']['short']['candidates'][0])
    return scan_result, frames, c


class PresentationTests(unittest.TestCase):
    def test_current_location_does_not_reuse_historic_touch_price(self):
        _, _, c = example()
        low, high = c['band']
        for price, expected in ((low,'검사 시 구간 안'),(high,'검사 시 구간 안'),
                                (low-1,'접촉 후 이탈'),(high+1,'접촉 후 이탈'),
                                (None,'현재 위치 미확인')):
            c['price'] = price
            self.assertEqual(contact_status(c),expected)
            self.assertIn(expected,unit_text(c))
            self.assertLessEqual(len(unit_text(c).splitlines()),5)
        c.update(level=2,phase='stair_retest',execution={'mid':low},price=high+1)
        self.assertEqual(contact_status(c),'검사 시 구간 안')

    def test_both_panels_use_same_trigger_price_and_parent_is_secondary(self):
        _, frames, c = example()
        c['arm'] = c['touch']['at'] - 4*3600000
        stamp, price, label = alert_marker(c)
        self.assertNotEqual(c['arm']-1,stamp)
        fig, axes = plt.subplots(1,2)
        try:
            for ax, tf in zip(axes,(c['path'][0],c['path'][-1])):
                draw_panel(ax,tf,window(frames,tf,c,c['now']),c)
                trigger, = [t for t in ax.texts if t.get_text()==label]
                self.assertEqual(trigger.xy[1],price)
            self.assertIn('상위 OB 접촉 · 참고',[t.get_text() for t in axes[0].texts])
            self.assertNotIn('상위 OB 접촉 · 참고',[t.get_text() for t in axes[1].texts])
        finally:
            plt.close(fig)

    def test_cash_panel_also_shows_exchange_trigger_without_fake_stock_candle(self):
        _, frames, c = example()
        top = c['path'][0]
        candles = window(frames,top,c,c['now'])
        c.update(stock_bars=[asdict(b) for b in candles],stock_reference='TEST')
        fig, ax = plt.subplots()
        try:
            draw_panel(ax,top,candles,c)
            trigger, = [t for t in ax.texts if t.get_text()=='거래소 알림 · 3중 접촉']
            self.assertEqual(trigger.xy[1],alert_marker(c)[1])
            self.assertGreater(trigger.xy[0],len(candles)-1)
        finally:
            plt.close(fig)
