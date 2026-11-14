import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.entry_execution import depth_quote
from src.ict_engine import Bar, TF_MS
from src.ict_overlap import PDZone
from src.star_alerts import RULESET, scan, unit_text
from src.star_delivery import StarAlertLedger, unique_contacts, alert_bands
from src.star_retests import retest_entries, target_at_contact, target_spent_at
from src.staircase_rules import staircases
from tests.test_star_alerts import replay, FIXTURE


class RetestTests(unittest.TestCase):
    def setup_case(self, direction=1, band=(100, 105)):
        m = lambda x: x if direction == 1 else 240 - x
        bounds = lambda a, b: sorted((m(a), m(b)))
        parent = PDZone('p', '4h', 'OB', direction, *bounds(95, 108), 100, 0, m(90), m(95))
        ob = PDZone('o', '15m', 'OB', direction, *bounds(101, 104), 2700000, 900000, m(100), m(101))
        overlap = {'touch': {'at': 960000, 'bar_open': 900000}, 'band': bounds(*band),
                   'score': 3, 'sources': [vars(parent)]}
        step = {'timeframe': '15m', 'direction': direction, 'origin': 1800000, 'end': 3600000,
                'known_at': 4500000, 'protected': m(100), 'target': m(112), 'invalidated_at': None}
        target = {'price': m(120), 'confirmed_at': 800000}
        mid = m(102)
        quote = depth_quote({'ts': 4800000, 'a': [[mid + .01, 100]], 'b': [[mid - .01, 100]]}, 4800000, 1000)
        return dict(parent=parent, overlap=overlap, step=step, target_point=target, zones=[ob],
                    lower_bars=[], minutes=[], now=4800000, price=mid,
                    execution_quote=lambda: quote, tick_size='.01')

    def test_lower_only_two_stars_upper_aligned_three_stars_both_sides(self):
        for d in (1, -1):
            args = self.setup_case(d)
            two, = retest_entries(**args)
            three, = retest_entries(**args, upper_ready=True)
            self.assertEqual((two['level'], three['level']), (2, 3))
            self.assertEqual(two['phase'], 'stair_retest')
            self.assertTrue(two['in_initial_overlap'])
            self.assertEqual(two['stop'], args['step']['protected'])
            self.assertEqual(two['target'], args['target_point']['price'])
            self.assertGreaterEqual(two['rr'], 3)
            self.assertEqual(two['band'], three['band'])

    def test_original_overlap_preferred_but_outside_ob_is_allowed(self):
        inside = self.setup_case(band=(102, 103))
        c, = retest_entries(**inside)
        self.assertGreaterEqual(c['band'][0], 102)
        self.assertLessEqual(c['band'][1], 103)
        outside, = retest_entries(**self.setup_case(band=(95, 99)))
        self.assertFalse(outside['in_initial_overlap'])

    def test_reject_breakout_no_stairs_old_stairs_unrelated_ob_and_missing_costs(self):
        for d in (1, -1):
            base = self.setup_case(d)
            changes = [dict(step=None), dict(step={**base['step'], 'origin': 0}),
                       dict(step={**base['step'], 'invalidated_at': 4700000}),
                       dict(step={**base['step'], 'known_at': 4900000}),
                       dict(execution_quote=None), dict(execution_quote=lambda: None),
                       dict(zones=[PDZone(**{**vars(base['zones'][0]), 'origin': 2700000})])]
            mid = 110 if d == 1 else 130
            q = depth_quote({'ts': base['now'], 'a': [[mid + .01, 100]], 'b': [[mid - .01, 100]]}, base['now'], 1000)
            changes.append(dict(price=mid, execution_quote=lambda: q))
            for change in changes:
                with self.subTest(direction=d, change=change):
                    self.assertFalse(retest_entries(**{**base, **change}))

    def test_spent_target_stop_wick_breach_costs_and_ote(self):
        for d in (1, -1):
            args = self.setup_case(d)
            target = args['target_point']['price']; stop = args['step']['protected']
            target_bar = Bar(3000000, target, target, target, target, 1)
            stop_bar = Bar(4600000, stop - d, stop - d, stop - d, stop - d, 1)
            for change in (dict(lower_bars=[target_bar]), dict(minutes=[stop_bar]),
                           dict(lower_bars=[stop_bar])):
                self.assertFalse(retest_entries(**{**args, **change}))
            quote = args['execution_quote']()
            self.assertFalse(retest_entries(**{**args, 'execution_quote': lambda: {**quote, 'slippage_per_side': .05}}))
            self.assertFalse(retest_entries(**args, require_ote=True))

    def test_target_is_known_at_first_contact(self):
        args = self.setup_case()
        points = [{'kind': 'high', 'price': 120, 'confirmed_at': 800000},
                  {'kind': 'high', 'price': 150, 'confirmed_at': 950000}]
        self.assertEqual(target_at_contact(points, args['overlap'])['price'], 120)

    def test_same_entry_rule_for_all_four_final_timeframes(self):
        for tf in ('5m', '15m', '1h', '4h'):
            args = self.setup_case()
            args['step'] = {**args['step'], 'timeframe': tf}
            args['zones'] = [PDZone(**{**vars(args['zones'][0]), 'timeframe': tf})]
            c, = retest_entries(**args)
            self.assertEqual(c['level'], 2)
            self.assertEqual(c['lower']['timeframe'], tf)

    def test_target_hit_in_contact_bucket_is_recorded_from_minute_evidence(self):
        args = self.setup_case()
        at = 1020000
        minute = Bar(at, 119, 120, 118, 119, 1)
        self.assertEqual(target_spent_at(args['overlap'], args['target_point'], 1, [], [minute], args['now']), at)

    def test_scan_delivery_upgrade_and_charts_with_synthetic_unbroken_retest(self):
        from src.star_charts import render
        raw = json.loads(FIXTURE.read_text(encoding='utf-8'))
        for d, contact_price in ((-1, 64935.), (1, 65065.)):
            timeline = list(replay(d)); snap, frames = timeline[2]; at = snap['as_of']
            opts = dict(tracks={'short': ('4h', '1h')}, formation_tracks=('short',))
            first = scan('BTCUSDT', frames, at, snap['price'],
                         minute_bars=[Bar(at - 60000, contact_price, contact_price, contact_price, contact_price, 1)], **opts)
            snap, frames = timeline[-1]; at = snap['as_of']; price = 64529 if d == -1 else 65471
            quote = depth_quote({'ts': at, 'a': [[price + 1, 10]], 'b': [[price - 1, 10]]}, at, 1000)
            # Controlled current retest, intentionally separate from the original
            # historic fixture whose final one-minute wick invalidates the setup.
            minute = Bar(at - 60000, price, price + 1, price - 1, price, 1)
            scan_args = dict(contexts=first['star_contexts'], execution_quote=lambda: quote, tick_size='.1',
                             minute_bars=[minute], touch_since=at - 300000, **opts)
            native = staircases
            with patch('src.star_alerts.staircases', side_effect=lambda bs, ps, tf, direction, *extra:
                       [] if tf == '4h' else native(bs, ps, tf, direction, *extra)):
                second = scan('BTCUSDT', frames, at, price, **scan_args)
            third = scan('BTCUSDT', frames, at, price, **scan_args)
            with tempfile.TemporaryDirectory() as tmp:
                ledger = StarAlertLedger(Path(tmp) / 'state.json', require_execution=True)
                for expected, result in enumerate((first, second, third), 1):
                    event, = ledger.observe(copy.deepcopy(result)).values()
                    c = event['candidate']; self.assertEqual(c['level'], expected)
                    if expected > 1:
                        self.assertIn('OB 재접촉', unit_text(c))
                        self.assertNotIn('종가 돌파', unit_text(c))
                        self.assertGreaterEqual(c['rr'], 3)
                        c['cross_track_warnings'] = ['중기 롱 구조 (2 ATR)', '장기 롱 구조 (2 ATR, 3 ATR)']
                        c['cross_track_charts'] = [{'track': 'medium', 'tf': '1d', 'wave': []}]
                        self.assertLessEqual(len(unit_text(c).splitlines()), 5)
                        self.assertIn('⚠ 중기·장기 반대 방향', unit_text(c))
                        self.assertNotIn('ATR', unit_text(c))
                        rendered = render({**result, 'illustrative': True}, event, frames, Path(tmp) / f'g{expected}')
                        self.assertEqual(len(rendered['images']), 1)
                        self.assertTrue(Path(rendered['images'][0]).exists())
                    ledger._remember(c, expected)
                    if expected == 2:
                        lock, = ledger.state['star_locks']['BTCUSDT'].values()
                        self.assertIn(tuple(c['overlap']['band']), list(alert_bands(lock)))
                    self.assertFalse(ledger.observe(copy.deepcopy(result)))
                # Grade 2 now has the same fresh-execution requirement as grade 3.
                c = copy.deepcopy(next(iter(second['tracks']['short']['candidates'])))
                c.pop('execution')
                self.assertFalse(unique_contacts({'short': {'candidates': [c]}}, require_execution=True))
                # An entry whose live price has moved away must not be sent.
                fresh = StarAlertLedger(Path(tmp) / 'fresh.json', require_execution=True)
                self.assertTrue(fresh.observe(copy.deepcopy(second)))
                fail = lambda _: self.fail('stale entry must not be delivered')
                self.assertEqual(fresh.dispatch_units(fail, fail, fail, refresh=lambda _: None)['sent'], 0)

    def test_old_reaction_lock_does_not_block_new_entry_or_resend_first_contact(self):
        snap, _ = list(replay(-1))[2]
        c = snap['tracks']['short']['candidates'][0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            ledger = StarAlertLedger(path); ledger._remember(c, 2)
            ledger.state['delivery_mode'] = 'star-ob-reaction-v1'
            ledger.state['star_contexts'] = {'BTCUSDT': {'existing': {'overlap': 'preserved'}}}
            ledger.save()
            migrated = StarAlertLedger(path)
            lock, = migrated.state['star_locks']['BTCUSDT'].values()
            self.assertEqual((lock['level'], lock['legacy_level']), (1, 2))
            self.assertEqual(migrated.state['delivery_mode'], RULESET)
            self.assertEqual(migrated.state['star_contexts']['BTCUSDT']['existing']['overlap'], 'preserved')


if __name__ == '__main__':
    unittest.main()
