import copy
import unittest
from unittest.mock import Mock
from src.ict_engine import Bar
from src.overlap_entry import entry_touch,mixed_evidence,recent_minutes,MINUTE,ENTRY_POLICY


def setup(direction=1):
    area={'direction':direction,'range_low':90,'range_high':110,'created':0,'range_known_at':0}
    choice={'low':94 if direction==1 else 102,'high':98 if direction==1 else 106,
            'latest_source':0,'groups':['4h:OB','1h:FVG']}
    return choice,area


class EntryTouchTests(unittest.TestCase):
    def test_approaching_after_half_range_is_not_an_entry(self):
        c,a=setup()
        self.assertFalse(entry_touch(c,a,99,180000)['hit'])
        self.assertTrue(entry_touch(c,a,96,180000)['hit'])

    def test_both_ob_and_fvg_are_required(self):
        c,a=setup()
        for kinds in (['4h:FVG','1h:FVG'],['4h:OB','1h:OB']):
            c['groups']=kinds
            self.assertFalse(mixed_evidence(c));self.assertFalse(entry_touch(c,a,96,180000)['hit'])
        c['groups']=['4h:OB','1h:FVG']
        self.assertTrue(mixed_evidence(c))

    def test_short_is_mirrored_and_half_range_is_required_at_touch(self):
        c,a=setup(-1)
        self.assertTrue(entry_touch(c,a,104,180000)['hit'])
        self.assertFalse(entry_touch(c,a,101,180000)['hit'])
        c.update(low=96,high=99)
        self.assertFalse(entry_touch(c,a,98,180000)['hit'])

    def test_between_checks_touch_is_detected_after_price_bounces_away(self):
        c,a=setup();minute=Bar(60000,99,99,95,98,10)
        result=entry_touch(c,a,105,180000,[minute],since=0)
        self.assertTrue(result['hit']);self.assertEqual(result['evidence'],'completed_1m_range')
        self.assertEqual(result['at'],120000)

    def test_future_formation_or_new_half_range_cannot_retroactively_qualify(self):
        c,a=setup();minute=Bar(60000,99,99,95,98,10)
        for field in ('created','range_known_at'):
            later=copy.deepcopy(a);later[field]=120000
            self.assertFalse(entry_touch(c,later,105,180000,[minute])['hit'])
        c['latest_source']=120000
        self.assertFalse(entry_touch(c,a,105,180000,[minute])['hit'])

    def test_forming_no_trade_and_already_checked_minutes_are_excluded(self):
        c,a=setup()
        for minute,since in ((Bar(180000,99,99,95,98,10),0),
                             (Bar(60000,99,99,95,98,0),0),
                             (Bar(60000,99,99,95,98,10),120000)):
            self.assertFalse(entry_touch(c,a,105,200000,[minute],since=since)['hit'])

    def test_minute_range_must_reach_the_discount_part_of_the_box(self):
        c,a=setup();c.update(low=99,high=103)
        premium=Bar(60000,102,103,101,102,10)
        discount=Bar(60000,100,102,99.5,101,10)
        self.assertFalse(entry_touch(c,a,105,180000,[premium])['hit'])
        self.assertTrue(entry_touch(c,a,105,180000,[discount])['hit'])


class MinuteCoverageTests(unittest.TestCase):
    def test_finished_minutes_only_and_coverage_cursor(self):
        m=Mock();m.get.return_value={'data':[[i*MINUTE,100,102,98,101,1] for i in (10,11,12)]}
        bars,coverage=recent_minutes(m,'BTCUSDT',10*MINUTE+20000,12*MINUTE+30000)
        self.assertEqual([b.timestamp for b in bars],[10*MINUTE,11*MINUTE])
        self.assertEqual(coverage['end'],12*MINUTE)
        self.assertEqual(m.get.call_args.kwargs['interval'],'1m')

    def test_missing_price_minutes_are_not_silently_considered_checked(self):
        m=Mock();m.get.return_value={'data':[[i*MINUTE,100,102,98,101,1] for i in (10,12)]}
        with self.assertRaisesRegex(ValueError,'coverage incomplete'):
            recent_minutes(m,'BTCUSDT',10*MINUTE,13*MINUTE)

    def test_outage_recovery_limit_is_reported(self):
        m=Mock();end=800*MINUTE
        m.get.return_value={'data':[[i*MINUTE,100,102,98,101,1] for i in range(80,800)]}
        bars,coverage=recent_minutes(m,'BTCUSDT',0,end)
        self.assertEqual(len(bars),720);self.assertTrue(coverage['truncated'])


if __name__=='__main__':unittest.main()
