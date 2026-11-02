import unittest
from dataclasses import asdict,replace
from pathlib import Path
import tempfile
from src.ict_engine import Bar,TF_MS
from src.ict_nested import PDZone
from src.anchored_overlap import anchored_overlaps,anchor_start,valid_anchor

H=TF_MS['1h']


def zone(key,tf,kind,low,high,origin,created,d=1):
    return PDZone(key,tf,kind,d,low,high,created,origin,low if d==1 else high,low if d==1 else high)


def children(start=0,count=4,tf='1h'):
    return [Bar(start+i*TF_MS[tf],105,110,100,106,1) for i in range(count)]


class AnchoredOverlapTests(unittest.TestCase):
    def test_ob_and_lower_pattern_are_inside_the_same_exact_upper_candle(self):
        parent=zone('p','4h','OB',100,110,0,8*H)
        fvg=zone('f','1h','FVG',102,108,0,3*H)
        ob=zone('b','1h','OB',103,107,2*H,4*H)
        c=anchored_overlaps([parent,fvg,ob],('4h','1h'),105,children())[0]
        self.assertEqual((c['low'],c['high'],c['score']),(103,107,3))
        self.assertEqual(c['parent_window']['start'],0);self.assertEqual(c['parent_window']['end'],4*H)
        self.assertEqual(c['parent_window']['child_bars'],4)
        self.assertTrue(valid_anchor({'pair':['4h','1h'],**c}))

    def test_another_date_same_price_does_not_count(self):
        parent=zone('p','4h','OB',100,110,0,8*H)
        old=zone('old','1h','FVG',103,108,-100*H,-97*H)
        later=zone('later','1h','FVG',103,108,5*H,8*H)
        self.assertEqual(anchored_overlaps([parent,old,later],('4h','1h'),105,children()),[])

    def test_boundary_straddling_patterns_are_excluded(self):
        parent=zone('p','4h','OB',100,110,0,8*H)
        left=zone('left','1h','FVG',103,108,-H,2*H)
        right=zone('right','1h','FVG',103,108,2*H,5*H)
        self.assertEqual(anchored_overlaps([parent,left,right],('4h','1h'),105,children()),[])

    def test_fvg_uses_its_middle_displacement_candle_not_the_left_candle(self):
        parent=zone('p','4h','FVG',102,108,0,12*H)
        inside=zone('b','1h','OB',103,107,6*H,8*H)
        wrong=zone('wrong','1h','OB',103,107,H,3*H)
        self.assertEqual(anchor_start(parent),4*H)
        result=anchored_overlaps([parent,inside,wrong],('4h','1h'),105,children(4*H))
        self.assertEqual(result[0]['source_ids'],['b','p'])
        self.assertEqual(result[0]['parent_window']['start'],4*H)

    def test_ob_and_fvg_parents_from_different_candles_do_not_inflate_count(self):
        parent=zone('p','4h','OB',100,110,0,8*H)
        other=zone('other','4h','FVG',102,108,0,12*H)
        child=zone('f','1h','FVG',103,107,0,3*H)
        result=anchored_overlaps([parent,other,child],('4h','1h'),105,children())
        self.assertEqual(result[0]['score'],2);self.assertNotIn('other',result[0]['source_ids'])

    def test_four_checks_are_possible_only_when_both_parents_share_the_anchor(self):
        ob=zone('p','4h','OB',100,110,4*H,12*H)
        fvg=zone('g','4h','FVG',102,108,0,12*H)
        lower_ob=zone('b','1h','OB',103,107,6*H,8*H)
        lower_fvg=zone('f','1h','FVG',104,106,4*H,7*H)
        result=anchored_overlaps([ob,fvg,lower_ob,lower_fvg],('4h','1h'),105,children(4*H))
        self.assertEqual(result[0]['score'],4)

    def test_incomplete_or_wrong_direction_children_cannot_confirm_overlap(self):
        parent=zone('p','4h','OB',100,110,0,8*H)
        child=zone('f','1h','FVG',103,107,0,3*H)
        self.assertEqual(anchored_overlaps([parent,child],('4h','1h'),105,children()[:3]),[])
        child=replace(child,direction=-1)
        self.assertEqual(anchored_overlaps([parent,child],('4h','1h'),105,children()),[])

    def test_fvg_plus_fvg_is_still_excluded(self):
        parent=zone('p','4h','FVG',102,108,0,12*H)
        child=zone('f','1h','FVG',103,107,4*H,7*H)
        self.assertEqual(anchored_overlaps([parent,child],('4h','1h'),105,children(4*H)),[])

    def test_daily_and_weekly_use_six_and_seven_internal_candles(self):
        for top,lower,count,start in [('1d','4h',6,0),('1w','1d',7,4*TF_MS['1d'])]:
            parent=zone('p',top,'OB',100,110,start,start+2*TF_MS[top])
            child=zone('f',lower,'FVG',103,107,start,start+3*TF_MS[lower])
            c=anchored_overlaps([parent,child],(top,lower),105,children(start,count,lower))[0]
            self.assertEqual(c['parent_window']['child_bars'],count)
            self.assertTrue(valid_anchor({'pair':[top,lower],**c}))

    def test_old_meta_style_mixed_dates_cannot_migrate_as_a_valid_anchor(self):
        parent=zone('p','4h','OB',100,110,100*H,108*H)
        old=zone('old','1h','FVG',103,107,0,3*H)
        candidate={'pair':['4h','1h'],'sources':[asdict(parent),asdict(old)]}
        self.assertFalse(valid_anchor(candidate))

    def test_right_chart_contains_only_the_four_internal_candles(self):
        import matplotlib.pyplot as plt
        from src.structure_review_charts import draw_notified_scope
        from src.overlap_charts import configure_font
        configure_font()
        parent=zone('p','4h','OB',100,110,4*H,12*H)
        child=zone('f','1h','FVG',103,107,4*H,7*H)
        c=anchored_overlaps([parent,child],('4h','1h'),105,children(4*H))[0]
        c.update(pair=['4h','1h'],price=999,direction=1,lenses=['recent'],
                 structure_area={'low':95,'high':115,'origin':0})
        frames={'4h':children(0,10,'4h'),'1h':children(0,40)}
        analysis={'views':{'recent':{'turns':[],'sweeps':[]}}}
        fig,ax=plt.subplots()
        try:
            draw_notified_scope(ax,[c],analysis,frames,False,'단기')
            candles=[p for p in ax.patches if abs(p.get_width()-.56)<1e-9]
            self.assertEqual(len(candles),4)
            self.assertLess(ax.get_xlim()[1],6)
            self.assertLess(ax.get_ylim()[1],200)  # Current price is not a historical candle.
            self.assertTrue(all(p.get_width()>=0 for p in ax.patches))
        finally:plt.close(fig)


class AnchorLockTests(unittest.TestCase):
    def sample(self,start=0,identifier='nested'):
        from tests.test_structure_alerts import multi
        from src.anchored_overlap import ANCHOR_POLICY
        s=multi(identifier=identifier);s['anchor_policy']=ANCHOR_POLICY
        parent=zone(f'p{start}','4h','OB',100,110,start,start+8*H)
        child=zone(f'f{start}','1h','FVG',103,107,start,start+3*H)
        c=s['tracks']['short']['candidates'][0]
        c.update(sources=[asdict(parent),asdict(child)],source_ids=[parent.id,child.id],
                 parent_window={'start':start,'end':start+4*H})
        s['valid_source_ids']=c['source_ids']
        return s

    def test_invalid_old_cross_date_lock_is_retired_but_history_is_kept(self):
        from tests.test_structure_alerts import multi
        from src.structure_alerts import StructureLedger
        with tempfile.TemporaryDirectory() as directory:
            ledger=StructureLedger(Path(directory)/'s.json',overlap_only=True,approach_only=True)
            ledger.observe(multi(identifier='old-cross-date'))
            ledger.dispatch_symbols(lambda _:True,lambda _:[Path('a'),Path('b')],{})
            old=dict(ledger.state['delivered'])
            ledger.observe(self.sample())
            self.assertEqual(len(ledger.state['pending']),1)
            self.assertEqual(ledger.state['delivered'],old)
            self.assertEqual(ledger.state['dedup_audits']['BTCUSDT']['retired'],1)

    def test_two_distinct_parent_candles_at_the_same_price_do_not_merge(self):
        from src.structure_alerts import StructureLedger
        with tempfile.TemporaryDirectory() as directory:
            ledger=StructureLedger(Path(directory)/'s.json',overlap_only=True,approach_only=True)
            s=self.sample();ledger.observe(s)
            ledger.dispatch_symbols(lambda _:True,lambda _:[Path('a'),Path('b')],{})
            newer=self.sample(4*H,'another-parent')
            newer['valid_source_ids']+=s['valid_source_ids']
            ledger.observe(newer)
            self.assertEqual(len(ledger.state['pending']),1)


if __name__=='__main__':unittest.main()
