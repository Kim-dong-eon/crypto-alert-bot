import unittest

from src.ict_engine import Bar, TF_MS, frame_open
from src.star_alerts import scan


class LiveWeeklyTests(unittest.TestCase):
    def test_current_week_enters_analysis_with_stable_provisional_zone_ids(self):
        start = frame_open(1794701040000, '1w')
        now = start + 2 * TF_MS['1d'] + TF_MS['1h'] + 5 * 60000
        frames = {}
        for tf, count in (('1w', 30), ('1d', 220), ('4h', 1350)):
            end = frame_open(now, tf)
            frames[tf] = [Bar(t, 100, 102, 98, 100, 1)
                          for t in range(end-count*TF_MS[tf], end, TF_MS[tf])]
        frames['1w'][-1] = Bar(start-TF_MS['1w'], 100, 101, 89, 90, 1)
        minutes = [Bar(t, 100, 111, 99, 110, 1)
                   for t in range(frame_open(now, '4h'), now, 60000)]
        options = dict(tracks={'long': ('1w', '1d')}, upper_atr=True)
        closed = scan('TEST', frames, now, 110, minute_bars=minutes, **options)
        live = scan('TEST', frames, now, 110, minute_bars=minutes, live_weekly=True, **options)
        self.assertFalse(closed['forming_weekly'])
        self.assertTrue(live['forming_weekly'])
        self.assertTrue(live['tracks']['long']['forming_upper'])
        zones = [z for z in live['valid_zones'] if ':forming:' in z['id']]
        self.assertTrue(zones)
        self.assertTrue(all(z['created'] == now for z in zones))
        minutes.append(Bar(now, 110, 111, 109, 110, 1))
        later = scan('TEST', frames, now+60000, 110, minute_bars=minutes, live_weekly=True, **options)
        self.assertEqual({z['id'] for z in zones},
                         {z['id'] for z in later['valid_zones'] if ':forming:' in z['id']})

    def test_missing_intraday_coverage_does_not_fabricate_current_week(self):
        now = 1794701040000
        result = scan('TEST', {}, now, 100, live_weekly=True)
        self.assertFalse(result['forming_weekly'])
