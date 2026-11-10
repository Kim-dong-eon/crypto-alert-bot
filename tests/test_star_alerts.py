import copy
import json
from pathlib import Path
import tempfile
import unittest

from src.ict_engine import Bar, TF_MS
from src.entry_execution import depth_quote, entry_plan
from src.ict_overlap import PDZone, VERSION
from src.split_alert_preview import render_side_by_side_unit
from src.star_alerts import formation_contacts, observation_reference, scan, unit_text
from src.star_charts import overlap_cells, primary_overlap
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
                first = scan('BTCUSDT', frames, now, snapshot['price'], minute_bars=[minute], **options)
                ledger = StarAlertLedger(Path(tmp) / 'state.json')
                event, = ledger.observe(first).values()
                c = event['candidate']
                self.assertEqual((c['level'], c['direction']), (2, direction))
                self.assertEqual([s['kind'] for s in c['sources']], ['OB'] * 3)
                self.assertIn('계단은 이후 확인', unit_text(c))
                self.assertEqual(primary_overlap(c, c['sources'])[:2], tuple(c['band']))
                result = ledger.dispatch_units(
                    lambda _: True, lambda _: True,
                    lambda unit: render_side_by_side_unit(first, unit, frames, Path(tmp) / 'formation'))
                self.assertEqual(result['sent'], 1)
                self.assertFalse(ledger.observe(first))

                snapshot, frames = timeline[-1]
                now = snapshot['as_of']
                minutes = [Bar(*r) if direction == -1 else Bar(r[0], 130000-r[1], 130000-r[3],
                           130000-r[2], 130000-r[4], r[5]) for r in raw['ticks'][-1]['minutes']]
                followup = scan('BTCUSDT', frames, now, snapshot['price'], minute_bars=minutes,
                                touch_since=now - 300000, contexts=first['star_contexts'], **options)
                entry, = ledger.observe(followup).values()
                c = entry['candidate']
                self.assertEqual(c['level'], 3)
                self.assertGreaterEqual(c['rr'], 3)
                self.assertLess(c['overlap']['touch']['at'], c['lower']['known_at'])

    def test_both_sides_upgrade_once_and_render_the_highest_grade(self):
        for direction in (-1, 1):
            with self.subTest(direction=direction), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'state.json'
                sent = []
                for expected, (snapshot, frames) in enumerate(replay(direction)):
                    ledger = StarAlertLedger(path)
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
                    self.assertFalse(StarAlertLedger(path).observe(snapshot))
                self.assertEqual(len(sent), 3)
                self.assertIn('★★★', sent[-1])
                self.assertIn('LONG' if direction == 1 else 'SHORT', sent[-1])
                self.assertEqual(len(list(Path(tmp).glob('chart_star3.png'))), 1)

    def test_same_price_highest_grade_then_higher_timeframe_wins(self):
        base = list(replay(-1))[1][0]['tracks']['short']['candidates'][0]
        higher = {**base, 'id': 'higher', 'track': 'medium', 'pair': ['1d', '4h']}
        same_scope = {**base, 'id': 'other-lens', 'lenses': ['broad']}
        tracks = {'short': {'candidates': [base, same_scope]}, 'medium': {'candidates': [higher]}}
        self.assertEqual(unique_contacts(tracks), [higher])
        base['level'] = 3
        self.assertEqual(unique_contacts(tracks), [base])

    def test_entry_delivery_requires_a_current_cost_checked_plan(self):
        timeline = list(replay(-1))
        for snapshot, _ in timeline[1:3]:
            self.assertFalse(unique_contacts(snapshot['tracks'], entry_only=True))
        snapshot, frames = timeline[-1]
        old = snapshot['tracks']['short']['candidates'][0]
        self.assertFalse(unique_contacts(snapshot['tracks'], entry_only=True))
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
            self.assertFalse(unique_contacts(rejected['tracks'], entry_only=True))
        checked = scan('BTCUSDT', frames, snapshot['as_of'], minutes[-1].close,
                       execution_quote=lambda: quote, **options)
        c, = unique_contacts(checked['tracks'], entry_only=True)
        self.assertEqual(c['touch']['evidence'], 'live_orderbook_mid')
        self.assertIn('예상 슬리피지', unit_text(c))
        with tempfile.TemporaryDirectory() as tmp:
            ledger = StarAlertLedger(Path(tmp) / 'state.json', entry_only=True)
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

    def test_observation_uses_ob_wick_and_only_prior_confirmed_target(self):
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

    def test_mixed_delivery_sends_observations_without_entry_recheck(self):
        timeline = list(replay(-1))
        with tempfile.TemporaryDirectory() as tmp:
            ledger = StarAlertLedger(Path(tmp) / 'state.json', require_execution=True)
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
        timeline = list(replay(-1))
        high_snapshot = timeline[-1][0]
        high = high_snapshot['tracks']['short']['candidates'][0]
        low, top = high['overlap']['band']
        # A later retest can have a band entirely separate from its confluence.
        high = {**high, 'low': top + 10, 'high': top + 20}
        high_snapshot['tracks']['short']['candidates'] = [high]
        weaker = {**high, 'track': 'medium', 'pair': ['1d', '4h'], 'level': 2,
                  'low': low, 'high': top}
        other_side = {**weaker, 'direction': -weaker['direction']}
        self.assertEqual(unique_contacts({'short': {'candidates': [high]},
                                         'medium': {'candidates': [weaker, other_side]}}), [high, other_side])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            ledger = StarAlertLedger(path)
            self.assertEqual(len(ledger.observe(high_snapshot)), 1)
            ledger.dispatch_units(lambda _: True, lambda _: True, lambda _: {'images': ['mock.png']})
            later = {**high_snapshot, 'as_of': high_snapshot['as_of'] + 300000,
                     'tracks': {'medium': {'candidates': [weaker], 'status': 'watching'}}}
            ledger = StarAlertLedger(path, force_resend=True)
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
            self.assertEqual(len(ledger.state['star_locks']['BTCUSDT']), 1)
            self.assertEqual(ledger.state['delivered']['old'], receipt)
            self.assertFalse(ledger.observe(snapshot))

    def test_text_failure_retries_only_the_text(self):
        snapshot = list(replay(-1))[1][0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            ledger = StarAlertLedger(path)
            ledger.observe(snapshot)
            ledger.dispatch_units(lambda _: True, lambda _: False, lambda _: {'images': ['mock.png']})
            ledger = StarAlertLedger(path)
            snapshot['tracks']['short']['candidates'] = []
            self.assertEqual(len(ledger.observe(snapshot)), 1)
            result = ledger.dispatch_units(lambda _: self.fail('duplicate image'), lambda _: True, lambda _: self.fail('duplicate render'))
            self.assertEqual((result['images_sent'], result['texts_sent']), (0, 1))
