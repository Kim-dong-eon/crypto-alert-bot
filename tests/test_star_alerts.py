import copy
import json
from pathlib import Path
import tempfile
import unittest

from src.ict_engine import Bar, TF_MS
from src.ict_overlap import VERSION
from src.split_alert_preview import render_side_by_side_unit
from src.star_alerts import scan
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
