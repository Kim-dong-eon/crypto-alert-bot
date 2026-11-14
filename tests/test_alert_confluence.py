import copy
import tempfile
import unittest
from pathlib import Path

from src.alert_confluence import PATHS, confluence_error
from src.star_delivery import StarAlertLedger, unique_contacts
from src.pullback_delivery import physical_id


def contact(track='long', direction=1, cash=True):
    path = PATHS[track]
    zones = [dict(id=f'z{i}', timeframe=tf, kind='OB', direction=direction,
                  origin=10+i, created=100-i, low=100+i, high=110-i)
             for i, tf in enumerate(path)]
    c = dict(symbol='TESTUSDT', track=track, path=list(path), pair=list(path[:2]),
             level=1, direction=direction, parent=zones[0], sources=zones, score=3,
             band=[102,108], low=102, high=108, formation=dict(start=10,end=100),
             touch=dict(hit=True, at=200, bar_open=150, evidence='completed_1m_range',
                        observed_low=107, observed_high=111),
             id='test', source_ids=[z['id'] for z in zones],
             context_price_breach=False, equilibrium=dict(eligible=True))
    if cash:
        c['stock_reference'] = 'TEST'
    return c


class ConfluenceTests(unittest.TestCase):
    def test_all_tracks_and_both_sources_require_three_distinct_frames(self):
        for track in PATHS:
            for direction in (-1,1):
                for cash in (True,False):
                    c = contact(track,direction,cash)
                    self.assertIsNone(confluence_error(c))
                    self.assertEqual(unique_contacts({track:{'candidates':[c]}}),[c])
                    c['sources'] = [c['parent']]
                    self.assertIsNotNone(confluence_error(c))
                    self.assertFalse(unique_contacts({track:{'candidates':[c]}}))

    def test_parent_touch_does_not_substitute_for_common_band_touch(self):
        c = contact()
        c['touch'].update(observed_low=109,observed_high=111)
        self.assertEqual(confluence_error(c),'common_band_not_touched')

    def test_price_overlap_and_formation_provenance_are_required(self):
        for change in ({'low':109}, {'origin':101,'created':102},
                       {'direction':-1}, {'timeframe':'1d'}, {'kind':'FVG'}):
            c = contact()
            c['sources'][-1].update(change)
            self.assertIsNotNone(confluence_error(c))
        c = contact()
        c['sources'][1]['kind'] = 'FVG'
        self.assertIsNone(confluence_error(c))

    def test_followup_uses_original_triple_proof_not_just_score(self):
        base = contact()
        c = copy.deepcopy(base)
        c.update(level=2, phase='stair_retest', overlap=copy.deepcopy(base),
                 sources=[c['parent']], band=[120,121],low=120,high=121,
                 touch=dict(hit=True,at=300,evidence='live_orderbook_mid',observed_price=120.5))
        self.assertIsNone(confluence_error(c))
        c['overlap']['sources'] = [c['parent']]
        self.assertIsNotNone(confluence_error(c))

    def test_old_single_ob_cannot_be_dispatched_even_with_force_resend(self):
        c = contact()
        c.pop('formation')
        c['sources'] = [c['parent']]
        with tempfile.TemporaryDirectory() as tmp:
            ledger = StarAlertLedger(Path(tmp)/'state.json', force_resend=True)
            ledger.state['pending']['old'] = {'candidate':c}
            fail = lambda *a: self.fail('legacy single-OB payload reached delivery')
            self.assertEqual(ledger.dispatch_units(fail,fail,fail)['sent'],0)
            self.assertFalse(ledger.state['pending'])

    def test_saved_presentation_cannot_replace_valid_candidate_with_legacy_image(self):
        c = contact()
        legacy = copy.deepcopy(c)
        legacy['sources'] = [legacy['parent']]
        with tempfile.TemporaryDirectory() as tmp:
            ledger = StarAlertLedger(Path(tmp)/'state.json')
            ledger.state['pending']['old'] = {'candidate':c}
            ledger.state['presentations']['old'] = {'candidate':legacy}
            fail = lambda *a: self.fail('stored legacy image reached delivery')
            self.assertEqual(ledger.dispatch_units(fail,fail,fail)['sent'],0)

    def test_missing_or_nonfinite_proof_is_rejected(self):
        self.assertIsNotNone(confluence_error({}))
        c = contact()
        c['band'][0] = float('nan')
        self.assertIsNotNone(confluence_error(c))

    def test_restart_retires_legacy_single_ob_but_preserves_receipts(self):
        c = contact()
        c.update(score=1, sources=[c['parent']])
        c.pop('formation')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'state.json'
            ledger = StarAlertLedger(path)
            ledger.state['presentations']['old'] = {'candidate':c,'attempt':123,'complete':True,
                                                   'receipt':{'message_ids':[42]}}
            ledger._remember(contact(), 1)
            ledger.state['star_locks']['TESTUSDT'].update(old={'score':1},unproven={'score':3})
            ledger.save()
            restarted = StarAlertLedger(path)
            self.assertNotIn('old',restarted.state['presentations'])
            self.assertEqual(restarted.state['retired_presentations']['old:123']['receipt']['message_ids'],[42])
            self.assertEqual(list(restarted.state['star_locks']['TESTUSDT']),[physical_id(contact())])

    def test_lock_recovers_evidence_from_matching_receipt_without_resetting_dedup(self):
        c = contact()
        key = physical_id(c)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'state.json'
            ledger = StarAlertLedger(path)
            ledger._remember(c, 1)
            del ledger.state['star_locks']['TESTUSDT'][key]['confluence_proof']
            ledger.state['presentations']['sent'] = {'candidate':c, 'attempt':200,'complete':True}
            ledger.save()
            resumed = StarAlertLedger(path)
            self.assertIsNone(confluence_error(resumed.state['star_locks']['TESTUSDT'][key]['confluence_proof']))
            self.assertIn('sent', resumed.state['presentations'])

    def test_forged_score_or_mismatched_lock_cannot_suppress_new_contact(self):
        c = contact()
        key = physical_id(c)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'state.json'
            ledger = StarAlertLedger(path)
            ledger._remember(c,1)
            ledger.state['star_locks']['TESTUSDT'][key]['low'] = 1
            ledger.save()
            resumed = StarAlertLedger(path)
            self.assertFalse(resumed.state['star_locks']['TESTUSDT'])
            self.assertEqual(resumed.state['retired_star_locks']['TESTUSDT'][key]['retired_reason'],
                             'unverified_confluence_lock')

    def test_all_tracks_survive_same_direction_same_price_with_atr(self):
        tracks = {}
        for track in PATHS:
            c = contact(track)
            c.update(upper_atr=1.5)
            tracks[track] = {'candidates':[c,{**c,'upper_atr':2.},{**c,'upper_atr':3.}]}
        selected = unique_contacts(tracks)
        self.assertEqual({c['track'] for c in selected},set(PATHS))
        self.assertEqual(len(selected),4)
