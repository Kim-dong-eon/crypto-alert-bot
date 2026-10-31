import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from src.ict_engine import Bar,TF_MS,frame_open
from src.structure_review import turns,departure_areas,liquidity_events
from src.review_history import history_frame,weekly_from_daily


def bars(closes,tf='4h'):
    out=[];previous=closes[0]
    for i,c in enumerate(closes):
        out.append(Bar(i*TF_MS[tf],previous,max(previous,c)+.1,min(previous,c)-.1,c,1))
        previous=c
    return out


class ReviewStructureTests(unittest.TestCase):
    def test_wick_reversal_does_not_confirm_without_close(self):
        values=bars([100]*15+[98,95,90,90,90])
        b=values[-1];values[-1]=Bar(b.timestamp,90,99,89.9,90,1)
        self.assertEqual([p for p in turns(values,2,.05) if p['kind']=='low'],[])
        values.append(Bar(len(values)*TF_MS['4h'],90,101,90,100,1))
        found=turns(values,2,.05)
        self.assertEqual(found[-1]['kind'],'low')
        self.assertEqual(found[-1]['confirmed_at'],values[-1].timestamp+TF_MS['4h'])

    def test_confirmed_history_never_repaints_and_scales_differ(self):
        values=bars([100]*15+[98,95,90,94,97,103,99,96,100,105,112,108,100,95,98,104])
        full=turns(values,2,.04)
        for n in range(16,len(values)+1):
            self.assertEqual(turns(values[:n],2,.04),[p for p in full if p['confirmed_index']<n])
        self.assertGreater(len(full),len(turns(values,4,.10)))
        self.assertTrue(all(a['kind']!=b['kind'] for a,b in zip(full,full[1:])))

    def test_small_noise_does_not_create_turns(self):
        self.assertEqual(turns(bars([100]*15+[100.1,99.9]*30),2,.025),[])

    def test_one_way_decline_cannot_invent_an_initial_peak(self):
        self.assertEqual(turns(bars([100]*15+[99,97,95,93,91,89]),2,.04),[])

    def test_areas_keep_old_support_until_body_break_not_wick(self):
        values=bars([100]*15+[98,95,90,94,100,110,105,100,95])
        points=turns(values,2,.04)
        support=next(a for a in departure_areas(values,points,'broad') if a['direction']==1)
        self.assertIsNone(support['invalidated_at'])
        wick=Bar(len(values)*TF_MS['4h'],95,96,support['low']-10,95,1)
        unchanged=next(a for a in departure_areas(values+[wick],points,'broad') if a['id']==support['id'])
        self.assertIsNone(unchanged['invalidated_at'])
        close=Bar((len(values)+1)*TF_MS['4h'],95,96,support['low']-2,support['low']-1,1)
        area=next(a for a in departure_areas(values+[wick,close],points,'broad') if a['id']==support['id'])
        self.assertEqual(area['invalidated_at'],close.timestamp+TF_MS['4h'])

    def test_downside_is_supported_too(self):
        values=bars([100]*15+[105,110,106,100,90,95,100])
        areas=departure_areas(values,turns(values,2,.04),'recent')
        self.assertIn(-1,[a['direction'] for a in areas])
        self.assertTrue(all(a['low']<a['high'] for a in areas))


class ReviewHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.market=Mock();self.market.cache=Path(self.directory.name)

    def test_fresh_short_cache_still_backfills_and_excludes_forming(self):
        step=TF_MS['4h'];now=20*step+1000
        def row(i):return [i*step,100,102,98,101,2]
        self.market.get.side_effect=[{'data':[row(i) for i in range(17,21)]},
                                     {'data':[row(i) for i in range(10,18)]}]
        path=self.market.cache/'BTCUSDT_4h_review.json'
        path.write_text(json.dumps({'fetched_at':now,'data':[row(18),row(19)]}))
        result=history_frame(self.market,'BTCUSDT','4h',now,10)
        self.assertEqual([b.timestamp for b in result],[i*step for i in range(10,20)])
        self.assertEqual(self.market.get.call_args_list[1].args[0],'history-candles')
        saved=json.loads(path.read_text())
        self.assertEqual(saved['data'][-1][0],19*step)

    def test_gap_is_rejected(self):
        step=TF_MS['4h']
        self.market.get.return_value={'data':[[i*step,100,102,98,101,1] for i in (1,3)]}
        with self.assertRaisesRegex(ValueError,'Missing'):
            history_frame(self.market,'BTCUSDT','4h',4*step,3)

    def test_weekly_only_from_seven_complete_monday_days(self):
        day=TF_MS['1d'];monday=4*day
        values=[Bar(monday+i*day,100+i,103+i,99+i,101+i,2) for i in range(15)]
        week=weekly_from_daily(values,monday+14*day+day//2)
        self.assertEqual(len(week),2)
        self.assertEqual(week[0].timestamp,frame_open(monday,'1w'))
        self.assertEqual((week[0].open,week[0].close,week[0].volume),(100,107,14))
        self.assertEqual(weekly_from_daily(values[1:7],monday+7*day),[])


class ReviewSweepTests(unittest.TestCase):
    def samples(self,raid_close=109,mirror=False):
        rows=[(100,105,99,102),(102,110,101,108),(108,109,100,103),
              (103,106,95,99),(99,108,97,105),(105,111,100,raid_close),
              (105,108,96,100),(100,102,93,94)]
        if mirror:rows=[(220-o,220-l,220-h,220-c) for o,h,l,c in rows]
        values=[Bar(i*TF_MS['4h'],*r,1) for i,r in enumerate(rows)]
        points=[{'kind':'low' if mirror else 'high','price':110,'index':1,
                 'confirmed_index':2,'confirmed_at':3*TF_MS['4h']},
                {'kind':'high' if mirror else 'low','price':125 if mirror else 95,
                 'index':3,'confirmed_index':4,'confirmed_at':5*TF_MS['4h']}]
        return values,points

    def test_reclaim_and_later_body_mss_are_mirrored(self):
        for mirror in (False,True):
            values,points=self.samples(mirror=mirror)
            event=liquidity_events(values[:6],points)[0]
            self.assertIsNone(event['mss_at'])
            result=liquidity_events(values,points)[0]
            self.assertEqual(result['index'],5)
            self.assertEqual(result['direction'],1 if mirror else -1)
            self.assertEqual(result['mss_index'],7)
            self.assertEqual(result['mss_at'],8*TF_MS['4h'])

    def test_close_breakout_is_consumed_without_a_sweep(self):
        values,points=self.samples(raid_close=110.5)
        self.assertEqual(liquidity_events(values[:6],points),[])
        values.append(Bar(8*TF_MS['4h'],105,112,100,109,1))
        self.assertFalse(any(e['level']==110 for e in liquidity_events(values,points)))

    def test_future_confirmed_turn_cannot_be_swept_retroactively(self):
        values,points=self.samples()
        points[0].update(confirmed_index=6,confirmed_at=7*TF_MS['4h'])
        self.assertEqual(liquidity_events(values[:6],points),[])

    def test_both_sides_same_bar_are_not_labeled_directional_sweep(self):
        values,points=self.samples()
        values[5]=Bar(5*TF_MS['4h'],105,111,94,100,1)
        self.assertEqual(liquidity_events(values[:6],points),[])


if __name__=='__main__':unittest.main()
