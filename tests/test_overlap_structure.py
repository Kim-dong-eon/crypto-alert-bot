"""Closed-bar examples exercise the entire turn -> raid -> MSS sequence."""
import unittest
from copy import deepcopy
from src.ict_engine import Bar, TF_MS
from src.overlap_structure import ExternalSweepMSS
from src.ict_overlap import scan


ROWS = [(103,110,100,105), (105,120,95,96), (96,102,90,94),
        (94,106,93,104), (104,112,100,110), (110,115,105,112),
        (112,113,102,104), (104,110,100,103), (103,105,89,95),
        (95,114,94,110), (110,117,108,116), (116,116,108,109),
        (109,115,105,110), (110,119,109,118), (118,120,104,106),
        (106,108,102,104)]


def candle(index, row, direction=1):
    o,h,l,c = row
    if direction == -1:
        o,h,l,c = 220-o,220-l,220-h,220-c
    return Bar(index*TF_MS['4h'],o,h,l,c)


def replay(rows=ROWS, direction=1):
    state = ExternalSweepMSS('4h')
    for index,row in enumerate(rows):
        state.step(candle(index,row,direction))
    return state


class ExternalStructureTests(unittest.TestCase):
    def test_external_raid_needs_close_mss_and_is_mirrored(self):
        for direction in (1,-1):
            state = replay(ROWS[:9],direction)
            self.assertIsNone(state.flow)
            self.assertEqual(state.pending['direction'],direction)
            # A wick over the MSS level is insufficient.
            state.step(candle(9,(95,116,94,114),direction))
            self.assertIsNone(state.flow)
            state.step(candle(10,ROWS[10],direction))
            self.assertEqual(state.flow.direction,direction)
            self.assertEqual(state.flow_proof['external_range']['end'],8*TF_MS['4h'])
            self.assertEqual(state.flow_proof['swept_origin'],2*TF_MS['4h'])

    def test_internal_turn_cannot_start_or_replace_external_pending(self):
        state = replay(ROWS[:8])
        # Real internal high 115, while the external high is 120.
        state.step(candle(8,(104,116,101,110)))
        self.assertIsNone(state.pending)
        self.assertIsNone(state.flow)
        state = replay(ROWS[:9])
        original = deepcopy(state.pending)
        # Minor high 115 is raided; it must not overwrite the lower raid.
        state.step(candle(9,(100,116,94,110)))
        self.assertEqual(state.pending['sweep'],original['sweep'])
        self.assertEqual(state.pending['direction'],1)

    def test_body_breakout_is_not_reclaimed_sweep(self):
        for direction in (1,-1):
            state = replay(ROWS[:8],direction)
            state.step(candle(8,(103,105,88,89),direction))
            self.assertIsNone(state.pending)
            self.assertIsNone(state.flow)

    def test_pending_cannot_wait_through_close_beyond_raid_tip(self):
        for direction in (1,-1):
            state = replay(ROWS[:9],direction)
            state.step(candle(9,(95,100,87,88),direction))
            self.assertIsNone(state.pending)
            self.assertIsNone(state.flow)
            self.assertEqual(state.last_transition['reason'],'pending_raid_extreme_close_broken')

    def test_protected_pullback_advances_only_after_body_break_then_close_invalidates(self):
        for direction in (1,-1):
            state = replay(ROWS[:13],direction)
            self.assertEqual(state.flow.invalidation,89 if direction==1 else 131)
            state.step(candle(13,ROWS[13],direction))
            self.assertEqual(state.flow.invalidation,105 if direction==1 else 115)
            state.step(candle(14,ROWS[14],direction))
            self.assertIsNotNone(state.flow)  # Only a wick violates protection.
            state.step(candle(15,ROWS[15],direction))
            self.assertIsNone(state.flow)
            self.assertEqual(state.last_transition['reason'],'protected_structure_close_broken')

    def test_both_external_sides_in_one_ohlc_bar_are_ambiguous(self):
        state = replay(ROWS[:8])
        state.step(candle(8,(103,121,89,104)))
        self.assertIsNone(state.flow)
        self.assertIsNone(state.pending)
        self.assertEqual(state.last_transition['reason'],'both_external_sides_taken_order_unknown')

    def test_rolling_range_ages_out_distant_extreme(self):
        rows=[(103,1000,100,105)]+[(103,110,100,105)]*20+ROWS
        state = replay(rows[:30])
        self.assertIsNotNone(state.pending)
        self.assertEqual(state.pending['level'],90)
        self.assertEqual(state.pending['external_range']['bars'],20)

    def test_scan_retires_broken_flow_before_original_raid_tip_is_broken(self):
        def snapshot(count):
            now=count*TF_MS['4h']
            frames={'4h':[candle(i,row) for i,row in enumerate(ROWS[:count])],
                    '1h':[Bar(i*TF_MS['1h'],110,111,109,110) for i in range(count*4)]}
            return scan('BTCUSDT',frames,now,104,{'short':('4h','1h')},equilibrium_filter=True)['tracks']['short']
        self.assertEqual(snapshot(14)['direction'],1)
        latest=snapshot(16)
        self.assertEqual(latest['direction'],0)
        self.assertIsNone(latest['context'])
        self.assertIsNone(latest['candidate'])
        self.assertEqual(latest['liquidity']['last_transition']['reason'],'protected_structure_close_broken')


if __name__ == '__main__':
    unittest.main()
