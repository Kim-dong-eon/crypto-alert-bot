import unittest
from dataclasses import replace

import matplotlib.pyplot as plt

from src.ict_engine import Bar
from src.ict_nested import PDZone
from src.star_alerts import formation_map, formation_contacts, map_risk
from src.star_charts import draw_panel, map_rows, window
from tests.test_alert_presentation import example


class FormationMapTests(unittest.TestCase):
    def test_all_paths_keep_untouched_bands_without_creating_contacts(self):
        for path in [('1h', '15m', '5m'), ('4h', '1h', '15m'),
                     ('1d', '4h', '1h'), ('1w', '1d', '4h')]:
            for direction in (1, -1):
                with self.subTest(path=path, direction=direction):
                    parent = PDZone('p', path[0], 'OB', direction, 90, 110, 100, 0, 89, 89)
                    mid = PDZone('m', path[1], 'FVG', direction, 91, 109, 60, 10, 90, 90)
                    zones = [mid]
                    for i, band in enumerate([(100, 102), (94, 96), (94, 95), (104, 106)]):
                        zones.append(PDZone(str(i), path[2], 'OB', direction, *band, 80, 30, 89, 89))
                    zones.extend([replace(zones[1], id='wrong-kind', kind='FVG', low=97, high=99),
                                  replace(zones[1], id='later', origin=101, created=120, low=97, high=99),
                                  replace(zones[1], id='opposite', direction=-direction, low=97, high=99)])
                    args = parent, zones, path, 100, 101, 200, (), 100
                    before = formation_contacts(*args, middle_fvg=True)
                    groups = formation_map(parent, zones, path, [100, 102], middle_fvg=True)
                    self.assertEqual(formation_contacts(*args, middle_fvg=True), before)
                    self.assertEqual(len(before), 1)
                    self.assertEqual(len(groups), 3)
                    self.assertEqual(sum(len(g['areas']) for g in groups), 4)
                    next_group, = [g for g in groups if g['relation'] == 'next']
                    self.assertEqual(next_group['band'], [94, 96] if direction == 1 else [104, 106])
                    self.assertIn('지지' if direction == 1 else '저항', next_group['label'])
                    self.assertEqual(formation_map(parent, zones, path, [100, 102]), [])
                    ids = {z['id'] for g in groups for a in g['areas']
                           for v in a['alternatives'] for z in v['sources']}
                    self.assertFalse(ids & {'wrong-kind', 'later', 'opposite'})

    def test_risk_uses_each_childs_own_structure_and_known_target(self):
        bars = [Bar(0, 100, 104, 98, 101), Bar(100, 101, 105, 100, 104),
                Bar(200, 104, 107, 102, 106)]
        points = [{'kind': 'high', 'price': 120, 'confirmed_at': 250},
                  {'kind': 'high', 'price': 999, 'confirmed_at': 500}]
        lower = [{'index': 0, 'kind': 'low', 'price': 98, 'confirmed_at': 100},
                 {'index': 2, 'kind': 'low', 'price': 102, 'confirmed_at': 300}]
        quote = {'mid': 106, 'buy_vwap': 106, 'sell_vwap': 106, 'slippage_per_side': .0012}
        args = dict(lower_points=lower, tick_size='.01', price=106, quote=quote)
        first = map_risk([{'origin': 100}], [100, 101], bars, points, 1, 400, **args)
        second = map_risk([{'origin': 200}], [103, 104], bars, points, 1, 400, **args)
        self.assertEqual((first['stop'], second['stop']), (97.99, 101.99))
        self.assertEqual(first['target'], 120)
        self.assertGreaterEqual(first['plan']['rr'], 3)
        self.assertGreaterEqual(second['plan']['rr'], 3)
        unknown = map_risk([{'origin': 100}], [100, 101], bars, points, 1, 400,
                           **{**args, 'quote': None})
        self.assertIsNone(unknown['plan'])
        self.assertFalse(unknown['costs_checked'])
        breached = Bar(350, 100, 101, 97, 100)
        self.assertIsNone(map_risk([{'origin': 100}], [100, 101], bars, points, 1, 400,
                                  minute_bars=[breached], **args))

    def test_chart_marks_alternatives_without_moving_trigger(self):
        _, frames, c = example()
        parent = PDZone(**c['parent'])
        lo, hi = c['band']
        width = hi - lo
        # A separate valid synthetic confluence is display context only.
        extra = [replace(PDZone(**z), id=z['id']+'extra', low=lo-2*width, high=lo-width)
                 for z in c['sources'][1:]]
        parent = replace(parent, low=min(parent.low, lo-3*width))
        groups = formation_map(parent, [PDZone(**z) for z in c['sources'][1:]] + extra,
                               c['path'], c['band'], middle_fvg=True)
        c['formation_map'] = groups
        self.assertTrue(map_rows(c))
        fig, ax = plt.subplots()
        try:
            tf = c['path'][-1]
            draw_panel(ax, tf, window(frames, tf, c, c['now']), c)
            patches = [p for p in ax.patches if p.get_gid() == 'alternative-confluence']
            self.assertTrue(patches)
            core, = [p for p in ax.patches if p.get_hatch() and p.get_gid() != 'alternative-confluence']
            self.assertAlmostEqual(core.get_y(), lo)
            self.assertAlmostEqual(core.get_y()+core.get_height(), hi)
            self.assertEqual(sum('이번 알림' in t.get_text() for t in ax.texts), 1)
        finally:
            plt.close(fig)
