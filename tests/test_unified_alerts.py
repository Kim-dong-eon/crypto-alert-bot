import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config.overlap_settings import MULTIBAR_OB_FRAMES
from run_ict_overlap import STAR_TRACKS, main, run_once, wait_for_next_scan
from src.ict_engine import Bar, TF_MS, frame_open
from src.ict_overlap import Registry, PDZone
from src.review_history import formation_history_starts, history_frame
from src.star_alerts import RULESET, formation_contacts, scan
from src.star_delivery import StarAlertLedger
from src.stock_departure import contract_reference, cash_close, stock_contacts
from src.pullback_alerts import prepare
from tests.test_star_alerts import replay


class UnifiedAlertTests(unittest.TestCase):
    def test_watch_waits_only_for_remainder_and_logs_overrun(self):
        output = io.StringIO()
        with patch('run_ict_overlap.time.monotonic', side_effect=[142., 175.]), \
                patch('run_ict_overlap.time.sleep') as sleep, contextlib.redirect_stdout(output):
            wait_for_next_scan(100.)
            wait_for_next_scan(100.)
        sleep.assert_called_once_with(18.)
        self.assertIn('75.0초', output.getvalue())

    def test_bounded_watch_starts_five_scans_and_exits_without_extra_sleep(self):
        now = [100.]
        started = []
        instruments = [{'symbol': 'BTCUSDT', 'baseCoin': 'BTC'}]

        def sleep(seconds):
            now[0] += seconds

        def scan_once(*args, **kwargs):
            started.append(now[0])
            now[0] += 42.
            if len(started) == 1:
                raise ValueError('temporary market error')
            return {'failures': [], 'delivery': {'failed': 0}, 'status_delivery': {'failed': 0}}

        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()), \
                patch('run_ict_overlap.HistoricalMarket'), \
                patch('run_ict_overlap.binance_listings', return_value=([], {'mode': 'test', 'verified_at': 0})), \
                patch('run_ict_overlap.select_common_instruments', return_value=instruments), \
                patch('run_ict_overlap.run_once', side_effect=scan_once), \
                patch('run_ict_overlap.time.monotonic', side_effect=lambda: now[0]), \
                patch('run_ict_overlap.time.sleep', side_effect=sleep):
            status = main(['--watch', '--watch-seconds', '300', '--bases', 'BTC',
                           '--state', str(Path(temp) / 'state.json')])

        self.assertEqual(status, 0)
        self.assertEqual(started, [100., 160., 220., 280., 340.])
        self.assertEqual(now[0], 382.)

    def test_one_off_resend_covers_every_symbol_and_resets_after_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = StarAlertLedger(Path(temp) / 'state.json', force_resend=True)
            checked = []

            def batch(market, instruments, ledger, *args, **kwargs):
                checked.append((instruments[0]['symbol'], ledger.force_resend))
                ledger.force_resend = False
                return dict(instruments=instruments, symbols=[], failures=[],
                            delivery=dict(sent=0, failed=0), status_delivery=dict(sent=0, failed=0, symbols=[]))

            instruments = [{'symbol': 'BTCUSDT'}, {'symbol': 'ETHUSDT'}]
            with patch('run_ict_overlap._run_batch', side_effect=batch):
                run_once(None, instruments, ledger)
                run_once(None, instruments, ledger)
        self.assertEqual(checked, [('BTCUSDT', True), ('ETHUSDT', True),
                                   ('BTCUSDT', False), ('ETHUSDT', False)])

    def test_scan_shares_only_fresh_bulk_ticker_snapshot(self):
        now = [100.]
        fetched = []
        received = []

        class Market:
            def tickers(self):
                stamp = int(now[0] * 1000)
                fetched.append(stamp)
                return {}, stamp

        def batch(market, instruments, ledger, *args, read_tickers):
            received.append(read_tickers()[1])
            now[0] += 3.
            return dict(instruments=instruments, symbols=[], failures=[],
                        delivery=dict(sent=0, failed=0), status_delivery=dict(sent=0, failed=0, symbols=[]))

        with tempfile.TemporaryDirectory() as temp:
            ledger = StarAlertLedger(Path(temp) / 'state.json')
            instruments = [{'symbol': symbol} for symbol in ('BTCUSDT', 'ETHUSDT', 'AAPLUSDT')]
            with patch('run_ict_overlap.time.time', side_effect=lambda: now[0]), \
                    patch('run_ict_overlap._run_batch', side_effect=batch):
                run_once(Market(), instruments, ledger)
                run_once(Market(), instruments[:1], ledger)
        self.assertEqual(received, [100000, 100000, 106000, 109000])
        self.assertEqual(fetched, [100000, 106000, 109000])

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

    def test_scan_checks_latest_closed_minute_inside_open_five_minute_candle(self):
        five_minute_open = 1800000000000
        last_close = five_minute_open + 120000
        now = last_close + 10000
        start = five_minute_open - 60000
        minutes = [Bar(t, 100, 101, 99, 100, 1) for t in range(start, last_close, 60000)]
        coverage = {'start': start, 'end': last_close, 'bars': len(minutes), 'truncated': False}

        class Market:
            def tickers(self):
                return {'BTCUSDT': {}}, now

            def frame(self, symbol, tf, as_of):
                stamp = frame_open(as_of, tf) - TF_MS[tf]
                return [Bar(stamp, 100, 101, 99, 100, 1)]

        def scan(symbol, frames, observed, price, **options):
            self.assertEqual(options['first_touch_cutoff'], last_close)
            self.assertEqual(options['minute_bars'][-1].timestamp, last_close - 60000)
            self.assertEqual(frames['5m'][-1].timestamp, five_minute_open - 300000)
            return {'symbol': symbol, 'as_of': observed, 'ruleset': RULESET,
                    'valid_source_ids': [], 'star_contexts': {}, 'history_starts': {},
                    'tracks': {name: {'status': 'watching', 'candidate': None} for name in STAR_TRACKS}}

        with tempfile.TemporaryDirectory() as temp:
            ledger = StarAlertLedger(Path(temp) / 'state.json')
            with patch('run_ict_overlap.time.time', return_value=now / 1000), \
                    patch('run_ict_overlap.quote_price', return_value=100), \
                    patch('run_ict_overlap.recent_minutes', return_value=(minutes, coverage)), \
                    patch('run_ict_overlap.formation_history_starts', return_value={}), \
                    patch('run_ict_overlap.scan_stars', side_effect=scan) as scanner, \
                    contextlib.redirect_stdout(io.StringIO()):
                report = run_once(Market(), [{'symbol': 'BTCUSDT', 'baseCoin': 'BTC'}], ledger, persist=True)
            scanner.assert_called_once()
            self.assertFalse(report['failures'])
            self.assertEqual(ledger.state['touch_cursors']['BTCUSDT'], last_close)
            self.assertEqual(ledger.save_count, 1)
            self.assertEqual(StarAlertLedger(ledger.path).state['touch_cursors']['BTCUSDT'], last_close)

    def test_legacy_cash_lock_does_not_hide_three_frame_signal(self):
        old_snapshot, frames = list(replay(-1))[2]
        now = old_snapshot['as_of']
        snapshot = scan('BTCUSDT', frames, now, 64935., tracks={'short': ('4h', '1h')},
                        formation_tracks=('short',), minute_bars=[Bar(now-60000, 64935., 64935., 64935., 64935., 1)],
                        first_touch_cutoff=now)
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

    def test_cash_basis_uses_bounded_past_trade_never_future_or_zero_volume(self):
        daily = Bar(1700000000000,100,110,90,105,100)
        end = cash_close(daily.timestamp,'1d')
        with tempfile.TemporaryDirectory() as tmp:
            class Market:
                cache = Path(tmp)
                def get(self, *args, **kwargs):
                    return {'data':[[end,300,300,300,300,10],
                                    [end-60000,220,220,220,220,0],
                                    [end-180000,210,210,210,210,5]]}
            _, basis = contract_reference(Market(),'TEST',{'1d':[daily]})
            self.assertEqual(basis['ratio'],2)
            self.assertEqual(basis['quote_age_ms'],120000)
            with patch.object(Market,'get',return_value={'data':[[end-17*60000,210,210,210,210,5]]}):
                with self.assertRaisesRegex(ValueError,'Missing traded'):
                    contract_reference(Market(),'STALE',{'1d':[daily]})


if __name__ == '__main__':
    unittest.main()
