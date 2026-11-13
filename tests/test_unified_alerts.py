import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config.overlap_settings import MULTIBAR_OB_FRAMES
from run_ict_overlap import run_once
from src.ict_engine import Bar, TF_MS
from src.ict_overlap import Registry, PDZone
from src.review_history import formation_history_starts, history_frame
from src.star_alerts import formation_contacts, scan
from src.star_delivery import StarAlertLedger
from src.stock_departure import contract_reference, cash_close, stock_contacts
from src.pullback_alerts import prepare
from tests.test_star_alerts import replay


class UnifiedAlertTests(unittest.TestCase):
    def test_cash_and_exchange_frames_have_identical_triple_contact_decisions(self):
        for side, price in ((-1,64935.),(1,65065.)):
            snapshot, frames = list(replay(side))[2]
            now = snapshot['as_of']
            minute = Bar(now-60000,price,price,price,price,1)
            options = dict(tracks={'short':('4h','1h')},formation_tracks=('short',),
                           minute_bars=[minute],first_touch_cutoff=now)
            normal = scan('TEST',frames,now,snapshot['price'],**options)
            cash = scan('TEST',frames,now,snapshot['price'],
                        session_closes={'4h':{b.timestamp:b.timestamp+TF_MS['4h'] for b in frames['4h']}},
                        source_prefix={'4h':'cash:TEST:'},**options)
            expected = normal['tracks']['short']['candidates']
            actual = cash['tracks']['short']['candidates']
            self.assertTrue(expected)
            self.assertEqual([(c['level'],c['direction'],c['band']) for c in actual],
                             [(c['level'],c['direction'],c['band']) for c in expected])
            self.assertTrue(all(len(c['sources'])==3 for c in actual))
            self.assertTrue(all(len(c['upper']['points']) >= 2 for c in actual))

    def test_cash_route_uses_same_scanner_without_exchange_top_gate(self):
        # The cash route must not emit a hand-made single-OB alert or be gated
        # by the unavailable exchange weekly chart.
        snapshot = {'symbol':'TESTUSDT','as_of':100000,'price':100,
                    'valid_source_ids':[], 'star_contexts':{},
                    'tracks':{t:{'status':'data_unavailable','candidates':[]} for t in ('medium','long')}}
        empty = {'valid_source_ids':[], 'star_contexts':{},
                 'tracks':{t:{'status':'data_unavailable','candidates':[]} for t in ('medium','long')}}
        with patch('src.star_alerts.scan',return_value=empty) as scanner:
            stock_contacts(snapshot,'TEST',{},[],[],0,100000,exchange_frames={'4h':[]})
        args = scanner.call_args.kwargs
        self.assertEqual(set(args['origin_tracks']), {'medium','long'})
        self.assertEqual(set(args['formation_tracks']), {'medium','long'})
        self.assertTrue(args['middle_fvg'])
        self.assertTrue(args['require_ote'])
        self.assertEqual(args['departure_ob_frames'],MULTIBAR_OB_FRAMES)
        self.assertTrue(args['upper_atr'])

    def test_cash_registry_accepts_session_gaps_and_actual_close(self):
        day = TF_MS['1d']
        bars = [Bar(0,110,111,99,100,1), Bar(3*day,100,115,99,114,1)]
        closes = {0:16*3600000,3*day:3*day+16*3600000}
        snapshot, _, zones = prepare('TEST',{'1d':bars},4*day,105,
            tracks={'medium':('1d','4h')},build_overlaps=False,
            session_closes={'1d':closes},source_prefix={'1d':'cash:TEST:'})
        ob = next(z for z in zones if z.kind == 'OB')
        self.assertEqual(ob.created,closes[3*day])
        self.assertTrue(ob.id.startswith('cash:TEST:'))
        self.assertIn(ob.id,snapshot['valid_source_ids'])

    def test_history_bridges_stale_cache_before_checkpoint(self):
        step = TF_MS['5m']
        row = lambda i: [i*step,100,101,99,100,1]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'TEST_5m_review.json'
            path.write_text(json.dumps({'fetched_at':5*step,'data':[row(i) for i in range(5)]}))
            class Market:
                cache = Path(tmp)
                def get(self, endpoint, **kwargs):
                    return {'data':[row(i) for i in (range(10,15) if endpoint=='candles' else range(4,10))]}
            bars = history_frame(Market(),'TEST','5m',15*step,15)
            self.assertEqual([b.timestamp for b in bars], [i*step for i in range(15)])

    def test_multicandle_departures_on_every_timeframe_and_side(self):
        prices = [(302.41,313.21,298.29,300.91), (300.91,308,273.1,273.32),
                  (273.32,300.33,272,294.54), (294.54,322.22,294.17,313.21)]
        for tf in MULTIBAR_OB_FRAMES:
            for side in (1, -1):
                with self.subTest(tf=tf, side=side):
                    registry = Registry(MULTIBAR_OB_FRAMES)
                    for i, (o,h,l,c) in enumerate(prices):
                        values = (o,h,l,c) if side == 1 else (600-o,600-l,600-h,600-c)
                        registry.step(tf, Bar(i*TF_MS[tf], *values, 1))
                    zones = [z for z in registry.zones.values() if z.kind == 'OB']
                    self.assertEqual(len(zones), 1)
                    self.assertEqual((zones[0].origin, zones[0].direction), (0, side))

    def test_middle_fvg_allowed_but_final_ob_still_required(self):
        end = 2*TF_MS['4h']
        parent = PDZone('p','4h','OB',1,100,110,end,0,95,100)
        middle = PDZone('m','1h','FVG',1,102,108,2*TF_MS['1h'],0,95,102)
        child = PDZone('c','15m','OB',1,103,107,TF_MS['1h'],0,95,103)
        minute = Bar(end,105,105,105,105,1)
        args = (parent, [middle,child], ('4h','1h','15m'), 0, 111, end+60000, [minute], 0)
        contacts = formation_contacts(*args, middle_fvg=True)
        self.assertEqual(contacts[0]['band'], [103,107])
        self.assertFalse(formation_contacts(*args))

    def test_failed_frame_fetch_does_not_consume_touch_window(self):
        now = 1800000000000

        class Market:
            def tickers(self):
                return {'BTCUSDT': {}}, now

            def frame(self, *args):
                raise ValueError('temporary unavailable')

        minutes = [Bar(t,100,101,99,100,1) for t in range(now-300000,now,60000)]
        coverage = {'start':now-300000,'end':now,'bars':5,'truncated':False}
        with tempfile.TemporaryDirectory() as tmp:
            ledger = StarAlertLedger(Path(tmp)/'state.json')
            ledger.state['touch_cursors'] = {'BTCUSDT':now-300000}
            with patch('run_ict_overlap.time.time',return_value=now/1000), \
                 patch('run_ict_overlap.quote_price',return_value=100), \
                 patch('run_ict_overlap.recent_minutes',return_value=(minutes,coverage)), \
                 contextlib.redirect_stdout(io.StringIO()):
                report = run_once(Market(), [{'symbol':'BTCUSDT','baseCoin':'BTC','priceMultiplier':'.1'}],
                                  ledger, persist=True)
            self.assertEqual(ledger.state['touch_cursors']['BTCUSDT'],now-300000)
            self.assertTrue(report['failures'])
            self.assertTrue(all(t == now-300000 for t in ledger.state['track_touch_cursors']['BTCUSDT'].values()))

    def test_legacy_cash_lock_does_not_hide_three_frame_signal(self):
        snapshot = list(replay(-1))[1][0]
        normal = copy.deepcopy(snapshot['tracks']['short']['candidates'][0])
        normal.update(level=1,score=3)
        old = copy.deepcopy(normal)
        old.update(stock_reference='TEST',score=1)
        old['parent']['id'] = 'cash-parent'
        snapshot['valid_source_ids'].append('cash-parent')
        snapshot['tracks']['short']['candidates'] = [normal]
        with tempfile.TemporaryDirectory() as tmp:
            ledger = StarAlertLedger(Path(tmp)/'state.json')
            ledger._remember(old,1)
            self.assertEqual(len(ledger.observe(snapshot)),1)

    def test_backfill_reaches_actual_parent_origin(self):
        step = TF_MS['1h']
        bars = [Bar(0,110,111,99,100,1), Bar(step,100,115,99,114,1)]
        starts = formation_history_starts({'1h':bars}, {'fast':('1h','15m','5m')}, 105)
        self.assertLessEqual(starts['5m'],0)
        self.assertLessEqual(starts['15m'],0)

    def test_cash_levels_use_synchronous_contract_basis(self):
        daily = Bar(1700000000000,100,110,90,105,100)
        end = cash_close(daily.timestamp,'1d')
        with tempfile.TemporaryDirectory() as tmp:
            class Market:
                cache = Path(tmp)

                def get(self, *args, **kwargs):
                    return {'data':[[end-60000,210,212,209,210,5]]}

            frames, basis = contract_reference(Market(),'TESTUSDT',{'1d':[daily]})
            self.assertEqual(basis['ratio'],2)
            self.assertEqual((frames['1d'][0].low,frames['1d'][0].high),(180,220))
            self.assertEqual(frames['1d'][0].timestamp,daily.timestamp)


if __name__ == '__main__':
    unittest.main()
