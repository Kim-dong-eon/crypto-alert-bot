import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from run_ict_overlap import FORMATION_TRACKS, STAR_TRACKS
from src.ict_engine import Bar, TF_MS
from src.entry_execution import depth_quote, entry_plan
from src.ict_overlap import PDZone, VERSION
from src.split_alert_preview import render_side_by_side_unit
from src.star_alerts import formation_contacts, formation_pivot, formation_reaction, observation_reference, scan, unit_text
from src.star_charts import overlap_cells, primary_overlap, window
from src.star_delivery import StarAlertLedger, unique_contacts


FIXTURE = Path(__file__).parent / 'fixtures/star_alert_btc.json'


def replay(direction):
    raw = json.loads(FIXTURE.read_text(encoding='utf-8'))

    def bar(row):
        t, o, h, lo, c, v = row
        return Bar(int(t), o, h, lo, c, v) if direction == -1 else Bar(int(t), 130000-o, 130000-lo, 130000-h, 130000-c, v)

    contexts = {}
    for tick in raw['ticks']:
        now = tick['now']
        frames = {tf: [bar(r) for r in rows if r[0]+TF_MS[tf] <= now] for tf, rows in raw['frames'].items()}
        minutes = [bar(r) for r in tick['minutes']]
        snapshot = scan('BTCUSDT', frames, now, minutes[-1].close, minute_bars=minutes,
                        touch_since=now-300000, contexts=contexts, tracks={'short': ('4h','1h')})
        contexts = snapshot['star_contexts']
        yield snapshot, frames


class StarAlertsTests(unittest.TestCase):
    def test_live_text_is_sent_before_chart_and_is_not_resent_on_retry(self):
        # Replay the obsolete policy explicitly; live defaults reject these legacy fixtures.
        snapshot = list(replay(-1))[1][0]
        candidate = snapshot['tracks']['short']['candidates'][0]
        candidate['first_touch_text'] = True
        with tempfile.TemporaryDirectory() as tmp:
            ledger = StarAlertLedger(Path(tmp) / 'state.json', require_confluence=False)
            self.assertTrue(ledger.observe(copy.deepcopy(snapshot)))
            sent = []

            def text(_):
                sent.append('text')
                return True

            def renderer(_):
                sent.append('render')
                return {'images': ['chart.png']}

            first = ledger.dispatch_units(lambda _: False, text, renderer)
            self.assertEqual(sent, ['text', 'render'])
            self.assertEqual(first['failed'], 1)
            self.assertTrue(ledger.observe(copy.deepcopy(snapshot)))
            second = ledger.dispatch_units(lambda _: True, text, renderer)
            self.assertEqual(second['sent'], 1)
            self.assertEqual(sent, ['text', 'render', 'render'])
            self.assertFalse(ledger.observe(copy.deepcopy(snapshot)))

    def test_upper_atr_scan_uses_three_scales_for_each_available_track(self):
        snapshot, frames = list(replay(-1))[-1]
        result = scan('BTCUSDT', frames, snapshot['as_of'], snapshot['price'], upper_atr=True)
        available = [row for row in result['tracks'].values() if row['status'] != 'data_unavailable']
        self.assertTrue(available)
        for row in available:
            self.assertEqual(set(row['atr_directions']), {1.5, 2., 3.})

    def test_atr_same_direction_overlap_keeps_both_timeframes(self):
        # Replay the obsolete policy explicitly; live defaults reject these legacy fixtures.
        snapshot = list(replay(-1))[1][0]
        short = snapshot['tracks']['short']['candidates'][0]
        short.update(upper_atr=1.5)
        same_zone = {**short, 'id': 'same-zone', 'upper_atr': 2.}
        fast = {**short, 'id': 'fast', 'track': 'fast', 'pair': ['1h', '15m']}
        snapshot['tracks']['short'].update(candidates=[short, same_zone], atr_directions={1.5: -1, 2.: -1, 3.: 0})
        snapshot['tracks']['fast'] = {'candidates': [fast], 'atr_directions': {1.5: -1, 2.: -1, 3.: 1}}
        with tempfile.TemporaryDirectory() as tmp:
            ledger = StarAlertLedger(Path(tmp) / 'state.json', require_confluence=False)
            pending = ledger.observe(copy.deepcopy(snapshot))
            self.assertEqual({e['candidate']['track'] for e in pending.values()}, {'short', 'fast'})
            event = next(e for e in pending.values() if e['candidate']['track'] == 'short')
            self.assertEqual(event['candidate']['track'], 'short')
            self.assertEqual(event['candidate']['matched_atrs'], [1.5, 2.])
            self.assertIn('⚠ 초단기 반대 방향 / 초단기 숏 구간 겹침', unit_text(event['candidate']))
            ledger._remember(short, short['level'])
            snapshot['tracks']['short']['candidates'] = []
            remaining, = ledger.observe(copy.deepcopy(snapshot)).values()
            self.assertEqual(remaining['candidate']['track'], 'fast')

            fast['direction'] = 1
            snapshot['tracks']['fast']['atr_directions'] = {1.5: 1, 2.: 1, 3.: 0}
            event, = ledger.observe(copy.deepcopy(snapshot)).values()
            self.assertEqual(event['candidate']['track'], 'fast')
            self.assertIn('⚠ 단기 반대 방향 / 단기 숏 구간 겹침', unit_text(event['candidate']))

    def test_live_paths_all_require_the_parent_ob_formation_window(self):
        self.assertEqual(set(FORMATION_TRACKS), set(STAR_TRACKS))
        self.assertIn('fast', FORMATION_TRACKS)

    def test_parent_formation_must_contain_its_confirmed_departure_pivot(self):
        hour = TF_MS['1h']
        bars = [Bar(i * hour, 650, 652.7, 638.77, 640.51) for i in range(5)]
        points = [{'index': 1, 'kind': 'high', 'price': 652.7, 'confirmed_at': 3 * hour},
                  {'index': 2, 'kind': 'low', 'price': 638.77, 'confirmed_at': 3 * hour},
                  {'index': 4, 'kind': 'high', 'price': 645., 'confirmed_at': 6 * hour}]
        parent = PDZone('origin', '1h', 'OB', -1, 649.89, 651.62, 2 * hour, 0, 652.7, 652.7)
        continuation = PDZone('later', '1h', 'OB', -1, 629.57, 630., 4 * hour, 2 * hour, 632., 632.)
        self.assertIsNone(formation_pivot(parent, bars, points, 2 * hour))
        self.assertEqual(formation_pivot(parent, bars, points, 3 * hour)['timestamp'], hour)
        self.assertIsNone(formation_pivot(continuation, bars, points, 6 * hour))

    def test_formation_contact_requires_three_obs_from_the_original_parent_window(self):
        start, end = 0, 2 * TF_MS['4h']
        parent = PDZone('parent', '4h', 'OB', 1, 100, 110, end, start, 99, 100)
        middle = PDZone('middle', '1h', 'OB', 1, 98, 106, 3 * TF_MS['1h'], TF_MS['1h'], 97, 98)
        final = PDZone('final', '15m', 'OB', 1, 104, 108, end, end - 2 * TF_MS['15m'], 103, 104)
        path = ('4h', '1h', '15m')
        during_formation = Bar(end - 60000, 105, 105, 105, 105, 1)
        touch = Bar(end, 107, 107, 105, 107, 1)
        now = end + 120000
        self.assertFalse(formation_contacts(parent, [middle, final], path, 0, 107, now,
                                            [during_formation], 0))
        for changes in ({'kind': 'FVG'}, {'direction': -1}, {'origin': -TF_MS['15m']},
                        {'created': end + TF_MS['15m']}, {'low': 106}):
            other = PDZone(**{**vars(final), **changes})
            self.assertFalse(formation_contacts(parent, [middle, other], path, 0, 107, now, [touch], 0))
        choice, = formation_contacts(parent, [middle, final], path, 0, 107, now, [touch], 0)
        self.assertEqual(choice['band'], [104, 106])
        self.assertEqual(choice['formation'], {'start': start, 'end': end})
        self.assertEqual(choice['touch']['bar_open'], end)
        self.assertFalse(formation_contacts(parent, [middle, final], path, 0, 105, now,
                                            [], end, confirmed_only=True))
        confirmed, = formation_contacts(parent, [middle, final], path, 0, 107, now,
                                        [touch], end, confirmed_only=True)
        self.assertEqual(confirmed['touch']['evidence'], 'completed_1m_range')

    def test_formation_first_touch_and_later_staircase_on_both_sides(self):
        raw = json.loads(FIXTURE.read_text(encoding='utf-8'))
        tracks = {'short': ('4h', '1h')}
        for direction, touch_price in ((-1, 64935.), (1, 65065.)):
            with self.subTest(direction=direction), tempfile.TemporaryDirectory() as tmp:
                timeline = list(replay(direction))
                snapshot, frames = timeline[2]
                now = snapshot['as_of']
                options = {'tracks': tracks, 'formation_tracks': ('short',)}
                outside = scan('BTCUSDT', frames, now, snapshot['price'], **options)
                self.assertFalse(outside['tracks']['short']['candidates'])
                minute = Bar(now - 60000, touch_price, touch_price, touch_price, touch_price, 1)
                next_bucket = scan('BTCUSDT', frames, now, snapshot['price'], minute_bars=[minute],
                                   first_touch_cutoff=minute.timestamp, **options)
                self.assertFalse(next_bucket['tracks']['short']['candidates'])
                closed_bucket = scan('BTCUSDT', frames, now, snapshot['price'], minute_bars=[minute],
                                     first_touch_cutoff=now, **options)
                confirmed, = unique_contacts(closed_bucket['tracks'])
                self.assertEqual((confirmed['level'], confirmed['touch']['at']), (1, now))
                origin_only = scan('BTCUSDT', frames, now, snapshot['price'], minute_bars=[minute],
                                   origin_tracks=('short',), **options)
                self.assertFalse(origin_only['tracks']['short']['candidates'])
                quote = depth_quote({'ts': now, 'a': [[touch_price + 1, 1]], 'b': [[touch_price - 1, 1]]},
                                    now, 1000)
                confirmation = {'at': now, 'bar_open': now - TF_MS['15m'], 'price': snapshot['price'],
                                'level': snapshot['price'] - direction}
                with patch('src.star_alerts.formation_reaction', return_value=confirmation):
                    first = scan('BTCUSDT', frames, now, snapshot['price'], minute_bars=[minute],
                                 execution_quote=lambda: quote, tick_size='0.1', **options)
                ledger = StarAlertLedger(Path(tmp) / 'state.json')
                event, = ledger.observe(first).values()
                c = event['candidate']
                self.assertEqual((c['level'], c['direction']), (1, direction))
                self.assertEqual([s['kind'] for s in c['sources']], ['OB'] * 3)
                self.assertIn('3중 겹침 첫 접촉', unit_text(c))
                self.assertEqual(primary_overlap(c, c['sources'])[:2], tuple(c['band']))
                self.assertGreaterEqual(len(c['upper']['points']), 2)
                self.assertGreaterEqual(len(c['lower_wave']['points']), 2)
                self.assertNotIn('observation_risk', c)
                result = ledger.dispatch_units(
                    lambda _: True, lambda _: True,
                    lambda unit: render_side_by_side_unit(first, unit, frames, Path(tmp) / 'formation'))
                self.assertEqual(result['sent'], 1)
                self.assertFalse(ledger.observe(first))

                reaction = scan('BTCUSDT', frames, now + 60000, snapshot['price'], touch_since=now,
                                contexts=first['star_contexts'], execution_quote=lambda: quote,
                                tick_size='0.1', **options)
                # A close break alone no longer upgrades a formation alert.
                self.assertFalse(ledger.observe(reaction))
                self.assertFalse(any(c['level'] >= 2 for c in reaction['tracks']['short']['candidates']))

                snapshot, frames = timeline[-1]
                now = snapshot['as_of']
                minutes = [Bar(*r) if direction == -1 else Bar(r[0], 130000-r[1], 130000-r[3],
                           130000-r[2], 130000-r[4], r[5]) for r in raw['ticks'][-1]['minutes']]
                # This historic fixture actually wicks through the lower stop.
                followup = scan('BTCUSDT', frames, now, snapshot['price'], minute_bars=minutes,
                                touch_since=now - 300000, contexts=reaction['star_contexts'], **options)
                self.assertFalse(any(c['level'] >= 2 for c in followup['tracks']['short']['candidates']))

    def test_reaction_requires_a_closed_cross_of_a_pivot_known_at_touch(self):
        width = TF_MS['5m']
        points = [{'kind': 'high', 'price': 105, 'confirmed_at': width},
                  {'kind': 'high', 'price': 112, 'confirmed_at': 3 * width}]
        bars = [Bar(0, 101, 105, 99, 102), Bar(width, 102, 104, 99, 103),
                Bar(2 * width, 103, 109, 102, 105), Bar(3 * width, 105, 110, 103, 106)]
        overlap = {'touch': {'at': 2 * width, 'bar_open': 2 * width - 60000}}
        self.assertIsNone(formation_reaction(overlap, bars[:-1], points, '5m', 1))
        self.assertEqual(formation_reaction(overlap, bars, points, '5m', 1),
                         {'at': 4 * width, 'bar_open': 3 * width, 'price': 106, 'level': 105})

    def test_reaction_chart_ends_at_signal_close_on_both_timeframes(self):
        hour = TF_MS['1h']
        five = TF_MS['5m']
        close = hour + 2 * five
        sent = close + 6 * 60000
        frames = {tf: [Bar(t, 100, 101, 99, 100) for t in range(0, sent + 1, step)]
                  for tf, step in (('1h', hour), ('5m', five))}
        event = {'now': sent, 'reaction': {'at': close}, 'pair': ['1h', '15m'],
                 'upper': {'chart_start': 0}, 'parent': {'origin': 0}}
        self.assertEqual(window(frames, '1h', event, sent)[-1].timestamp, 0)
        self.assertEqual(window(frames, '5m', event, sent)[-1].timestamp, close - five)

    def test_both_sides_upgrade_once_and_render_the_highest_grade(self):
        # Replay the obsolete policy explicitly; live defaults reject these legacy fixtures.
        for direction in (-1, 1):
            with self.subTest(direction=direction), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'state.json'
                sent = []
                for expected, (snapshot, frames) in enumerate(replay(direction)):
                    ledger = StarAlertLedger(path, require_confluence=False)
                    pending = ledger.observe(snapshot)
                    if expected == 0:
                        self.assertEqual(len(pending), 0)
                        continue
                    self.assertEqual(len(pending), 1)
                    event = next(iter(pending.values()))
                    c = event['candidate']
                    self.assertEqual((c['level'], c['direction']), (expected, direction))
                    if expected == 2:
                        self.assertEqual({s['timeframe'] for s in c['sources']}, {'4h','1h','15m'})
                        self.assertTrue(any(s['timeframe']=='15m' and s['kind']=='OB' for s in c['sources']))
                    if expected == 3:
                        self.assertGreaterEqual(c['rr'], 3)
                        self.assertEqual(c['stop'], c['lower']['protected'])
                        self.assertEqual(len(c['upper']['points']), 4)
                        self.assertLess(c['overlap']['touch']['at'], c['touch']['at'])
                    def renderer(unit):
                        if expected != 3:
                            return {'images': ['mock.png']}
                        return render_side_by_side_unit(snapshot, unit, frames, Path(tmp) / 'chart')
                    result = ledger.dispatch_units(lambda _: True, lambda text: sent.append(text) or True, renderer)
                    self.assertEqual((result['sent'], result['images_sent'], result['failed']), (1, 1, 0))
                    self.assertFalse(StarAlertLedger(path, require_confluence=False).observe(snapshot))
                self.assertEqual(len(sent), 3)
                self.assertIn('★★★', sent[-1])
                self.assertIn('롱' if direction == 1 else '숏', sent[-1])
                self.assertEqual(len(list(Path(tmp).glob('chart_star3.png'))), 1)

    def test_same_price_deduplicates_only_within_each_timeframe(self):
        # Replay the obsolete policy explicitly; live defaults reject these legacy fixtures.
        base = list(replay(-1))[1][0]['tracks']['short']['candidates'][0]
        higher = {**base, 'id': 'higher', 'track': 'medium', 'pair': ['1d', '4h']}
        same_scope = {**base, 'id': 'other-lens', 'lenses': ['broad']}
        tracks = {'short': {'candidates': [base, same_scope]}, 'medium': {'candidates': [higher]}}
        self.assertEqual(unique_contacts(tracks, require_confluence=False), [higher, base])
        base['level'] = 3
        self.assertEqual(unique_contacts(tracks, require_confluence=False), [base, higher])

    def test_one_hour_and_four_hour_overlap_deliver_independently(self):
        # Replay the obsolete policy explicitly; live defaults reject these legacy fixtures.
        snapshot = list(replay(-1))[2][0]
        short = snapshot['tracks']['short']['candidates'][0]
        fast = {**short, 'id': 'fast', 'track': 'fast', 'pair': ['1h', '15m'], 'level': 1,
                'refinement_tf': '5m'}
        snapshot['tracks']['fast'] = {'candidates': [fast]}
        self.assertEqual([c['track'] for c in unique_contacts(snapshot['tracks'], require_confluence=False)], ['short', 'fast'])
        with tempfile.TemporaryDirectory() as tmp:
            ledger = StarAlertLedger(Path(tmp) / 'state.json', require_confluence=False)
            ledger._remember(short, 2)
            pending = ledger.observe(copy.deepcopy(snapshot))
            self.assertEqual([e['candidate']['track'] for e in pending.values()], ['fast'])
            ledger.dispatch_units(lambda _: True, lambda _: True, lambda _: {'images': ['mock.png']})
            self.assertFalse(ledger.observe(copy.deepcopy(snapshot)))
            ledger.reset_alerts(snapshot['as_of'])
            self.assertFalse(ledger.state['star_locks'])
            self.assertFalse(ledger.state['presentations'])
            self.assertTrue(ledger.state['retired_presentations'])
            self.assertEqual(ledger.state['alert_reset_at'], snapshot['as_of'])
            self.assertEqual(len(ledger.observe(copy.deepcopy(snapshot))), 2)

    def test_entry_delivery_requires_a_current_cost_checked_plan(self):
        # Replay the obsolete policy explicitly; live defaults reject these legacy fixtures.
        timeline = list(replay(-1))
        for snapshot, _ in timeline[1:3]:
            self.assertFalse(unique_contacts(snapshot['tracks'], entry_only=True, require_confluence=False))
        snapshot, frames = timeline[-1]
        old = snapshot['tracks']['short']['candidates'][0]
        self.assertFalse(unique_contacts(snapshot['tracks'], entry_only=True, require_confluence=False))
        raw = json.loads(FIXTURE.read_text(encoding='utf-8'))
        minutes = [Bar(*row) for row in raw['ticks'][-1]['minutes']]
        mid = sum(old['band']) / 2
        book = {'ts': snapshot['as_of'], 'a': [[mid + 1, 1]], 'b': [[mid - 1, 1]]}
        quote = depth_quote(book, snapshot['as_of'], 1000)
        options = dict(minute_bars=minutes, touch_since=snapshot['as_of']-300000,
                       contexts=timeline[-2][0]['star_contexts'], tracks={'short': ('4h', '1h')}, tick_size='0.1')
        for value in (None, {**quote, 'mid': mid-1000}, {**quote, 'slippage_per_side': .025}):
            rejected = scan('BTCUSDT', frames, snapshot['as_of'], minutes[-1].close,
                            execution_quote=lambda: value, **options)
            self.assertFalse(unique_contacts(rejected['tracks'], entry_only=True, require_confluence=False))
        checked = scan('BTCUSDT', frames, snapshot['as_of'], minutes[-1].close,
                       execution_quote=lambda: quote, **options)
        c, = unique_contacts(checked['tracks'], entry_only=True, require_confluence=False)
        self.assertEqual(c['touch']['evidence'], 'live_orderbook_mid')
        self.assertIn('순손익비', unit_text(c))
        with tempfile.TemporaryDirectory() as tmp:
            ledger = StarAlertLedger(Path(tmp) / 'state.json', entry_only=True, require_confluence=False)
            self.assertEqual(len(ledger.observe(checked)), 1)
            sender = lambda _: self.fail('expired plan must not be sent')
            result = ledger.dispatch_units(sender, sender, sender, refresh=lambda _: None)
            self.assertEqual(result['sent'], 0)

    def test_depth_changes_both_long_and_short_entry_boundaries(self):
        book = {'ts': 1000, 'a': [[100.1, 2], [100.3, 20]], 'b': [[99.9, 2], [99.7, 20]]}
        quote = depth_quote(book, 1000, 1000)
        self.assertAlmostEqual(quote['buy_vwap'], 100.26)
        self.assertAlmostEqual(quote['sell_vwap'], 99.74)
        self.assertAlmostEqual(quote['slippage_per_side'], .0028)
        with self.assertRaisesRegex(ValueError, 'insufficient_orderbook_depth'):
            depth_quote(book, 1000, 10000)
        with self.assertRaisesRegex(ValueError, 'stale_orderbook'):
            depth_quote(book, 40000, 1000)
        for direction, stop, target in ((1, 98., 110.), (-1, 102., 90.)):
            plan = entry_plan(99., 101., stop, target, direction, quote, '.01')
            self.assertGreaterEqual(plan['rr'], 3.)
            self.assertEqual(plan['entry'], plan['band'][1 if direction == 1 else 0])
            self.assertIsNone(entry_plan(99., 101., stop, target, direction, quote, None))

    def test_one_star_observation_uses_ob_wick_and_only_prior_confirmed_target(self):
        bar = Bar(100, 102, 103, 98, 101, 1)
        choice = {'sources': [{'origin': 100}], 'touch': {'at': 500}}
        points = [{'kind': 'high', 'price': 112, 'confirmed_at': 400},
                  {'kind': 'low', 'price': 88, 'confirmed_at': 490},
                  {'kind': 'high', 'price': 120, 'confirmed_at': 501}]
        self.assertEqual(observation_reference(choice, [bar], points, 1), {'stop': 98, 'target': 112})
        self.assertEqual(observation_reference(choice, [bar], points, -1), {'stop': 103, 'target': 88})
        book = {'ts': 1000, 'a': [[105.1, 20]], 'b': [[104.9, 20]]}
        quote = depth_quote(book, 1000, 1000)
        self.assertIsNone(entry_plan(99, 101, 98, 112, 1, quote, '.01'))
        self.assertGreaterEqual(entry_plan(99, 101, 98, 112, 1, quote, '.01', require_touch=False)['rr'], 3.)
        above_target = {**quote, 'mid': 115., 'buy_vwap': 115.1}
        self.assertIsNone(entry_plan(99, 101, 98, 112, 1, above_target, '.01'))
        self.assertIsNotNone(entry_plan(99, 101, 98, 112, 1, above_target, '.01', require_touch=False))

    def test_two_star_stop_uses_the_ob_wave_and_rejects_a_broken_pivot(self):
        bars = [Bar(0, 104, 105, 98.02, 102), Bar(100, 102, 104, 101, 103),
                Bar(200, 103, 107, 102, 106), Bar(300, 106, 107, 104, 105)]
        choice = {'level': 2, 'sources': [{'origin': 100}], 'touch': {'at': 500, 'bar_open': 400}}
        points = [{'kind': 'high', 'price': 120, 'confirmed_at': 300},
                  {'kind': 'high', 'price': 125, 'confirmed_at': 450}]
        low = {'index': 0, 'kind': 'low', 'price': 98.02, 'confirmed_at': 200}
        later_low = {'index': 2, 'kind': 'low', 'price': 102, 'confirmed_at': 400}
        kwargs = {'lower_points': [low, later_low], 'tick_size': '0.01', 'price': 103}
        self.assertEqual(observation_reference(choice, bars, points, 1, **kwargs),
                         {'stop': 98.01, 'target': 120, 'pivot_at': 0, 'pivot_price': 98.02})
        broken = Bar(400, 103, 104, 97.99, 103)
        self.assertIsNone(observation_reference(choice, bars, points, 1, minute_bars=[broken], **kwargs))
        self.assertIsNone(observation_reference(choice, bars, points, 1,
                                                **{**kwargs, 'lower_points': [{**low, 'confirmed_at': 401}]}))
        short_bars = [Bar(b.timestamp, 200-b.open, 200-b.low, 200-b.high, 200-b.close) for b in bars]
        high = {**low, 'kind': 'high', 'price': 101.98}
        short_target = [{'kind': 'low', 'price': 80, 'confirmed_at': 300}]
        self.assertEqual(observation_reference(choice, short_bars, short_target, -1,
                                               lower_points=[high], tick_size='0.01', price=97),
                         {'stop': 101.99, 'target': 80, 'pivot_at': 0, 'pivot_price': 101.98})

    def test_mixed_delivery_sends_observations_without_entry_recheck(self):
        # Replay the obsolete policy explicitly; live defaults reject these legacy fixtures.
        timeline = list(replay(-1))
        with tempfile.TemporaryDirectory() as tmp:
            ledger = StarAlertLedger(Path(tmp) / 'state.json', require_execution=True, require_confluence=False)
            for snapshot, _ in timeline[1:3]:
                self.assertEqual(len(ledger.observe(snapshot)), 1)
                result = ledger.dispatch_units(lambda _: True, lambda _: True, lambda _: {'images': ['mock.png']},
                                               refresh=lambda _: self.fail('observation must not require an entry recheck'))
                self.assertEqual(result['sent'], 1)
            self.assertFalse(ledger.observe(timeline[-1][0]))

    def test_overlap_color_counts_only_boxes_sharing_time_and_price(self):
        boxes = [(0, 90, 110), (2, 95, 105), (4, 100, 103), (5, 103, 104)]
        cells = list(overlap_cells(boxes, 8))
        self.assertIn((2, 4, 100, 103, 2), cells)
        self.assertIn((4, 8, 100, 103, 3), cells)
        self.assertIn((5, 8, 103, 104, 3), cells)
        self.assertTrue(all(c[4] < 4 for c in cells))

    def test_primary_chart_band_prefers_middle_fvg_over_wider_ob(self):
        zones = [dict(id='parent', timeframe='4h', kind='OB', low=90, high=110, created=1),
                 dict(id='middle-ob', timeframe='1h', kind='OB', low=91, high=109, created=2),
                 dict(id='middle-fvg', timeframe='1h', kind='FVG', low=100, high=104, created=3),
                 dict(id='lower-ob', timeframe='15m', kind='OB', low=102, high=107, created=4)]
        event = {'level': 2, 'path': ['4h', '1h', '15m'], 'parent': {'id': 'parent'}}
        self.assertEqual(primary_overlap(event, zones), (102, 104, 4, 'FVG'))
        self.assertEqual(primary_overlap(event, [z for z in zones if z['kind'] != 'FVG']), (102, 107, 4, 'OB'))

    def test_higher_grade_shadows_its_original_overlap_in_later_scans(self):
        # Replay the obsolete policy explicitly; live defaults reject these legacy fixtures.
        timeline = list(replay(-1))
        high_snapshot = timeline[-1][0]
        high = high_snapshot['tracks']['short']['candidates'][0]
        low, top = high['overlap']['band']
        # A later retest can have a band entirely separate from its confluence.
        high = {**high, 'low': top + 10, 'high': top + 20}
        high_snapshot['tracks']['short']['candidates'] = [high]
        weaker = {**high, 'track': 'short', 'pair': ['4h', '1h'], 'level': 2,
                  'low': low, 'high': top}
        other_side = {**weaker, 'direction': -weaker['direction']}
        self.assertEqual(unique_contacts({'short': {'candidates': [high]},
                                         'short-extra': {'candidates': [weaker, other_side]}}, require_confluence=False), [high, other_side])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            ledger = StarAlertLedger(path, require_confluence=False)
            self.assertEqual(len(ledger.observe(high_snapshot)), 1)
            ledger.dispatch_units(lambda _: True, lambda _: True, lambda _: {'images': ['mock.png']})
            later = {**high_snapshot, 'as_of': high_snapshot['as_of'] + 300000,
                     'tracks': {'short': {'candidates': [weaker], 'status': 'watching'}}}
            ledger = StarAlertLedger(path, force_resend=True, require_confluence=False)
            self.assertFalse(ledger.observe(later))
            self.assertEqual(ledger.state['dedup_audits']['BTCUSDT']['suppressed'], 1)

    def test_migration_merges_duplicate_old_locks_without_replaying_receipt(self):
        snapshot = list(replay(-1))[-1][0]
        c = copy.deepcopy(snapshot['tracks']['short']['candidates'][0])
        c.pop('level')
        receipt = {'timestamp': 1, 'message_ids': [123]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            path.write_text(json.dumps({'version': VERSION, 'tracks': {}, 'delivered': {'old': receipt}, 'pending': {},
                                        'locks': {'BTCUSDT|short': [{'latest': c, 'sent': True}]*2}}), encoding='utf-8')
            ledger = StarAlertLedger(path)
            self.assertFalse(ledger.state['star_locks'].get('BTCUSDT'))
            self.assertEqual(ledger.state['delivered']['old'], receipt)
            self.assertFalse(ledger.observe(snapshot))

    def test_text_failure_retries_only_the_text(self):
        # Replay the obsolete policy explicitly; live defaults reject these legacy fixtures.
        snapshot = list(replay(-1))[1][0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            ledger = StarAlertLedger(path, require_confluence=False)
            ledger.observe(snapshot)
            ledger.dispatch_units(lambda _: True, lambda _: False, lambda _: {'images': ['mock.png']})
            ledger = StarAlertLedger(path, require_confluence=False)
            snapshot['tracks']['short']['candidates'] = []
            self.assertEqual(len(ledger.observe(snapshot)), 1)
            result = ledger.dispatch_units(lambda _: self.fail('duplicate image'), lambda _: True, lambda _: self.fail('duplicate render'))
            self.assertEqual((result['images_sent'], result['texts_sent']), (0, 1))
