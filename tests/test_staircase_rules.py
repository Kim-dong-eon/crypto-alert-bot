import copy
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from src.ict_engine import Bar,TF_MS
from src.ict_nested import PDZone
from src.ict_overlap import alert_eligible
from src.staircase_rules import staircases,ote_band,net_rr,risk_band,evaluate,RULESET,band_touch,opposing_upper_contact


def sample():
    """Explicitly synthetic, causal double-staircase for rules and chart QA."""
    start=1793318400000;u=TF_MS['4h'];l=TF_MS['15m'];now=start+60*u
    def series(count,step,offset,anchors):
        out=[]
        for i in range(count):
            a=max(k for k in anchors if k<=i);b=min((k for k in anchors if k>i),default=a)
            p=anchors[a] if a==b else anchors[a]+(anchors[b]-anchors[a])*(i-a)/(b-a)
            prev=out[-1].close if out else p
            out.append(Bar(offset+i*step,prev,max(prev,p)+.15,min(prev,p)-.15,p,100))
        return out
    upper=series(60,u,start,{0:95,15:80,20:110,26:100,35:120,59:105.5})
    lower=series(320,l,start+40*u,{0:106,260:103,270:107,280:104,295:109,319:105.5})
    upper[26]=Bar(upper[26].timestamp,108,108.15,99.85,104,100)
    lower[280]=Bar(lower[280].timestamp,107,107.15,103.85,105,100)
    def points(bs,tf,items):
        result=[]
        for kind,i,confirmed in items:
            result.append({'kind':kind,'index':i,'price':bs[i].low if kind=='low' else bs[i].high,
                           'confirmed_index':confirmed,'confirmed_at':bs[confirmed].timestamp+TF_MS[tf]})
        return result
    up=points(upper,'4h',[('low',15,17),('high',20,22),('low',26,28),('high',35,38)])
    lp=points(lower,'15m',[('low',260,263),('high',270,273),('low',280,283),('high',295,300)])
    sources=[asdict(PDZone('parent','4h','OB',1,104,108,start+30*u,start+26*u,99,104)),
             asdict(PDZone('middle','1h','FVG',1,105,107,start+31*u,start+26*u,105,105)),
             asdict(PDZone('last','15m','OB',1,105,107,lower[285].timestamp,lower[280].timestamp,103.5,105))]
    c={'direction':1,'pair':['4h','1h'],'refinement_tf':'15m','low':105,'high':107,
       'structure_area':{'pivot_origin':upper[26].timestamp,'origin':upper[26].timestamp,
                         'range_low':up[2]['price'],'range_high':up[3]['price'],'created':up[2]['confirmed_at']},
       'parent':sources[0],'sources':sources,'source_ids':[s['id'] for s in sources],
       'formed_at':lower[285].timestamp,'latest_source':lower[285].timestamp,'score':3}
    return c,upper,up,lower,lp,now


class StaircaseTests(unittest.TestCase):
    def checked(self):
        c,u,up,l,lp,now=sample()
        return evaluate(c,u,up,l,now,105.5,lower_points=lp)

    def test_ote_matches_user_diagram(self):
        self.assertEqual(ote_band({'protected':100,'target':120,'direction':1}),(104.2,107.6))
        self.assertEqual(ote_band({'protected':120,'target':100,'direction':-1}),(112.4,115.8))

    def test_both_confirmed_stairs_and_ote_net_three_are_required(self):
        s=self.checked();self.assertTrue(s['eligible']);self.assertGreaterEqual(s['rr'],3)
        self.assertLess(s['stop'],s['lower']['protected']);self.assertEqual(s['target'],s['upper']['target'])
        self.assertEqual(s['entry'],s['high'])
        c,u,up,l,lp,now=sample()
        self.assertEqual(evaluate(c,u,up,l,now,105.5,lower_points=[])['reason'],'lower_staircase_unconfirmed')
        self.assertEqual(evaluate(c,u,[],l,now,105.5,lower_points=lp)['reason'],'upper_staircase_unconfirmed')

    def test_mirrored_short_passes(self):
        c,u,up,l,lp,now=sample();ceiling=250
        mirror=lambda b:Bar(b.timestamp,ceiling-b.open,ceiling-b.low,ceiling-b.high,ceiling-b.close,b.volume)
        for ps in (up,lp):
            for p in ps:p.update(kind='high' if p['kind']=='low' else 'low',price=ceiling-p['price'])
        c.update(direction=-1,low=ceiling-107,high=ceiling-105)
        s=evaluate(c,[mirror(b) for b in u],up,[mirror(b) for b in l],now,ceiling-105.5,lower_points=lp)
        self.assertTrue(s['eligible']);self.assertGreater(s['stop'],s['lower']['protected'])
        self.assertEqual(s['entry'],s['low'])

    def test_wick_only_break_is_not_a_stair(self):
        c,u,up,l,lp,now=sample();level=up[1]['price']
        u=[Bar(b.timestamp,min(b.open,level),b.high,b.low,min(b.close,level),b.volume) for b in u]
        self.assertFalse(staircases(u,up,'4h',1))

    def test_broken_upper_and_lower_and_live_breach_fail_closed(self):
        c,u,up,l,lp,now=sample()
        self.assertEqual(evaluate(c,u,up,l,now,90,lower_points=lp)['reason'],'upper_live_breach')
        b=u[-1];u[-1]=Bar(b.timestamp,105,106,90,95,100)
        self.assertEqual(evaluate(c,u,up,l,now,105.5,lower_points=lp)['reason'],'upper_staircase_broken')
        c,u,up,l,lp,now=sample();b=l[-1];l[-1]=Bar(b.timestamp,105,106,100,102,100)
        self.assertEqual(evaluate(c,u,up,l,now,105.5,lower_points=lp)['reason'],'lower_staircase_broken')

    def test_touch_before_confirmation_cannot_replay(self):
        c,u,up,l,lp,now=sample();known=lp[-1]['confirmed_at']
        minute=Bar(known-60000,105,106,104,105,1)
        s=evaluate(c,u,up,l,now,108,[minute],known-60000,lp)
        self.assertFalse(s['eligible'])
        minute=Bar(known,105,106,104,105,1)
        s=evaluate(c,u,up,l,now,108,[minute],known,lp)
        self.assertTrue(s['eligible']);self.assertEqual(s['touch']['evidence'],'completed_1m_range')

    def test_fee_adjustment_and_clipped_entry_band(self):
        self.assertLess(net_rr(100,98,106,1),3)
        lo,hi=risk_band(99,101,98,106,1)
        self.assertLess(hi,100);self.assertAlmostEqual(net_rr(hi,98,106,1),3)
        lo,hi=risk_band(99,101,102,94,-1)
        self.assertGreater(lo,100);self.assertAlmostEqual(net_rr(lo,102,94,-1),3)

    def test_latest_lower_source_must_belong_to_lower_stair(self):
        c,u,up,l,lp,now=sample();c['sources'][-1]['origin']=l[10].timestamp
        self.assertEqual(evaluate(c,u,up,l,now,105.5,lower_points=lp)['reason'],'lower_source_not_in_staircase')

    def test_midpoint_only_and_previously_reached_target_are_rejected(self):
        c,u,up,l,lp,now=sample();c.update(low=109,high=110)
        self.assertEqual(evaluate(c,u,up,l,now,109.5,lower_points=lp)['reason'],'outside_ote')
        c,u,up,l,lp,now=sample();b=u[-2];u[-2]=Bar(b.timestamp,110,121,109,110,100)
        self.assertEqual(evaluate(c,u,up,l,now,105.5,lower_points=lp)['reason'],'upper_target_already_revisited')

    def test_new_policy_retires_old_partial_but_preserves_sent_parent_locks(self):
        from src.pullback_delivery import PullbackAlertLedger
        from tests.test_pullback_alerts import observation,approved_candidate
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'state.json';ledger=PullbackAlertLedger(path,require_images=True,overlap_only=True,approach_only=True)
            snapshot=observation();c=approved_candidate(snapshot);snapshot['tracks']['short'].update(candidate=c,candidates=[c])
            ledger.observe(snapshot);ledger.dispatch_units(lambda _:True,lambda _:True,lambda _:{'images':['example.png']})
            count=len(ledger.state['locks']['AAPLUSDT|short'])
            ledger.state['presentations']['old-partial']={'complete':False,'candidate':c,'attempt':snapshot['as_of']}
            ledger.save();upgraded=PullbackAlertLedger(path)
            self.assertNotIn('old-partial',upgraded.state['presentations'])
            self.assertEqual(len(upgraded.state['locks']['AAPLUSDT|short']),count)
            self.assertTrue(upgraded.state['locks']['AAPLUSDT|short'][0]['sent'])

    def test_opposing_higher_contact_holds_but_not_before_zone_exists(self):
        c,u,up,l,lp,now=sample();c['trade_setup']=self.checked()
        z=PDZone('obstacle','1d','OB',-1,105,106,now-60000,now-TF_MS['1d'],107,106)
        self.assertEqual(opposing_upper_contact(c,{},[z],now,105.5),'obstacle')
        self.assertIsNone(opposing_upper_contact(c,{},[z],now-120000,105.5))

    def test_chart_has_previous_wave_and_new_risk_annotations(self):
        from src.staircase_charts import wave_window
        from src.pullback_charts import render_unit
        from src.pullback_alerts import POLICY
        from PIL import Image
        c,u,up,l,lp,now=sample();s=self.checked()
        shown=wave_window(l,s['lower']);self.assertLess(shown[0].timestamp,s['lower']['points'][0]['timestamp'])
        with self.assertRaises(ValueError):wave_window(l[270:],s['lower'])
        c.update(trade_setup=s,lenses=['recent'],id='example',symbol='EXAMPLE',track='short',price=105.5,
                 low=s['low'],high=s['high'],ruleset=RULESET,alert_policy=POLICY,entry_touch=s['touch'])
        snapshot={'as_of':now,'illustrative':True,'tracks':{'short':{'analysis':{'frame_starts':{'4h':u[0].timestamp},'views':{'recent':{'turns':up,'sweeps':[]}}}}}}
        with tempfile.TemporaryDirectory() as temp:
            out=render_unit(snapshot,{'candidate':c},{'4h':u,'15m':l},Path(temp)/'example')
            with Image.open(out['images'][0]) as im:self.assertGreater(im.width,2500)
            self.assertIn('예상 순손익비',out['text_after_images'])

    def test_production_scanner_applies_rules_not_legacy_touch(self):
        from src.pullback_alerts import scan
        c,u,up,l,lp,now=sample();area={**c['structure_area'],'overlaps':[c]}
        prepared={'as_of':now,'tracks':{'short':{'pair':['4h','1h'],'path':['4h','1h','15m'],
                  'analysis':{'views':{'recent':{'turns':up,'selected':[area],'structure':'up'}}}}}}
        with patch('src.pullback_alerts.prepare',return_value=(prepared,{'4h':u,'15m':l},[])),patch('src.pullback_alerts.refine_overlap',return_value=[c]),patch('src.pullback_alerts.turns',return_value=lp):
            result=scan('TEST',{},now,105.5)
            candidate=result['tracks']['short']['candidate']
            self.assertTrue(alert_eligible(candidate));self.assertEqual(candidate['ruleset'],RULESET)
        c,u,up,l,lp,now=sample();area={**c['structure_area'],'overlaps':[c]}
        prepared['tracks']['short']['analysis']['views']['recent']['selected']=[area]
        with patch('src.pullback_alerts.prepare',return_value=(prepared,{'4h':u,'15m':l},[])),patch('src.pullback_alerts.refine_overlap',return_value=[c]),patch('src.pullback_alerts.turns',return_value=[]):
            self.assertIsNone(scan('TEST',{},now,105.5)['tracks']['short']['candidate'])


if __name__=='__main__':unittest.main()
