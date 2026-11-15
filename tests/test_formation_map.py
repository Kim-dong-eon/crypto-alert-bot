import unittest
from dataclasses import replace
import tempfile
from pathlib import Path

import matplotlib.pyplot as plt

from src.ict_engine import Bar
from src.ict_nested import PDZone
from src.star_alerts import formation_map, formation_contacts, map_risk, upper_risk_reference, assess_reference, qualify_contact
from src.star_charts import draw_panel, map_rows, window
from tests.test_alert_presentation import example
from src.star_delivery import StarAlertLedger, qualified_first_contact


class FormationMapTests(unittest.TestCase):
    def test_delivery_rejects_old_observations_and_preserves_new_rr_proof(self):
        snapshot, _, c = example()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'ledger.json'
            ledger=StarAlertLedger(path,require_contact_rr=True)
            self.assertFalse(ledger.observe(snapshot))
            c.update(contact_rr_required=True,observation_risk={
                'stop_tf':c['path'][0], 'plan':{'band':c['band'],'rr':3.2}})
            self.assertTrue(qualified_first_contact(c))
            ledger._remember(c,1)
            ledger.save()
            refreshed=StarAlertLedger(path,require_contact_rr=True)
            self.assertEqual(len(refreshed.state['star_locks'][c['symbol']]),1)
            # Old observation-only locks are retired, without erasing receipts.
            c.pop('contact_rr_required')
            legacy=StarAlertLedger(path)
            legacy.state['star_locks'][c['symbol']].clear()
            legacy._remember(c,1)
            legacy.save()
            migrated=StarAlertLedger(path,require_contact_rr=True)
            self.assertFalse(migrated.state['star_locks'][c['symbol']])
            self.assertTrue(migrated.state['retired_star_locks'][c['symbol']])

    def test_rr_subband_must_be_touched_after_its_structure_was_known(self):
        reference = {'stop':90,'target':150,'stop_tf':'4h','known_at':0}
        quote = {'mid':109,'buy_vwap':109,'sell_vwap':109,'slippage_per_side':.0012}
        choice = {'level':1,'band':[100,110],'touch':{'valid_from':0}}
        high_touch = [Bar(60000,109,110,108,109,1)]
        low_touch = [Bar(60000,102,103,101,102,1)]
        args = choice, reference, 1, quote, '.01', 109, 120000
        self.assertIsNone(qualify_contact(*args,high_touch,0,confirmed_only=True))
        qualified = qualify_contact(*args,low_touch,0,confirmed_only=True)
        self.assertIsNotNone(qualified)
        self.assertLess(qualified['band'][1],110)
        self.assertEqual(qualified['structural_band'],[100,110])
        self.assertGreaterEqual(qualified['observation_risk']['plan']['rr'],3)
        self.assertIsNone(qualify_contact(choice,{**reference,'known_at':90000},1,quote,'.01',109,
                                          120000,low_touch,0,confirmed_only=True))
        self.assertIsNone(qualify_contact(choice,reference,1,None,'.01',109,
                                          120000,low_touch,0,confirmed_only=True))

    def test_star_one_uses_upper_departure_on_all_tracks_and_keeps_partial_values(self):
        for tf in ('1h', '4h', '1d', '1w'):
            for direction in (1, -1):
                with self.subTest(tf=tf, direction=direction):
                    bars = [Bar(0,100,105,90,101), Bar(100,101,115,100,112), Bar(200,112,120,108,110)]
                    points = [{'index':0,'kind':'low','price':90,'confirmed_at':150},
                              {'index':2,'kind':'high','price':120,'confirmed_at':300}]
                    if direction == -1:
                        bars = [Bar(b.timestamp,200-b.open,200-b.low,200-b.high,200-b.close) for b in bars]
                        points = [{**p,'kind':'high' if p['kind']=='low' else 'low','price':200-p['price']} for p in points]
                    parent = PDZone('p',tf,'OB',direction,95,105,100,0,89,89)
                    risk = upper_risk_reference(parent,bars,points,direction,400,tick_size='.01')
                    self.assertEqual(risk['stop'],89.99 if direction==1 else 110.01)
                    self.assertEqual(risk['target'],120 if direction==1 else 80)
                    self.assertEqual(risk['stop_tf'],tf)
                    partial = upper_risk_reference(parent,bars,points[1:],direction,400,tick_size='.01')
                    self.assertIsNone(partial['stop'])
                    self.assertEqual(partial['target'],risk['target'])
                    self.assertIsNotNone(partial['stop_reason'])
                    missing_target = upper_risk_reference(parent,bars,points[:1],direction,400,tick_size='.01')
                    self.assertEqual(missing_target['stop'],risk['stop'])
                    self.assertIsNone(missing_target['target'])

    def test_one_asof_reference_for_main_and_all_rows_and_broken_stop_reason(self):
        parent = PDZone('p','4h','OB',1,95,105,100,0,89,89)
        bars = [Bar(0,100,105,90,101), Bar(100,101,115,100,112)]
        points = [{'index':0,'kind':'low','price':90,'confirmed_at':150},
                  {'index':1,'kind':'high','price':120,'confirmed_at':250},
                  {'index':1,'kind':'high','price':130,'confirmed_at':390}]
        ref = upper_risk_reference(parent,bars,points,1,400,tick_size='.01')
        quote = {'mid':106,'buy_vwap':106,'sell_vwap':106,'slippage_per_side':.0012}
        main = assess_reference(ref,[100,101],1,quote,'.01')
        row = assess_reference(ref,[100,101],1,quote,'.01')
        self.assertEqual(main,row)
        self.assertEqual(main['target'],130)
        self.assertEqual(main['as_of'],400)
        broken = upper_risk_reference(parent,bars,points,1,400,tick_size='.01',
                                      minute_bars=[Bar(350,95,96,89,95)])
        broken = assess_reference(broken,[100,101],1,quote,'.01')
        self.assertEqual(broken['stop'],89.99)
        self.assertEqual(broken['target'],130)
        self.assertEqual(broken['stop_reason'],'상위 구조 기준점 이탈')
        self.assertIsNone(broken['plan'])

    def test_partially_overlapping_next_support_does_not_inherit_touch(self):
        parent=PDZone('p','4h','OB',1,90,120,100,0,89,89)
        middle=PDZone('m','1h','OB',1,90,120,60,10,89,89)
        hit=PDZone('a','15m','OB',1,104,110,80,20,89,89)
        below=replace(hit,id='b',low=100,high=105)
        groups=formation_map(parent,[middle,hit,below],('4h','1h','15m'),[104,110])
        self.assertEqual(len(groups),2)
        self.assertEqual(groups[0]['band'],[104,110])
        self.assertEqual(groups[0]['relation'],'trigger')
        self.assertEqual(groups[1]['band'],[100,105])
        self.assertEqual(groups[1]['relation'],'next')
        self.assertNotIn('접촉',groups[1]['label'])

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
