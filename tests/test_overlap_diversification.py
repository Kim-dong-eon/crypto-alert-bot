import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from src.ict_engine import Bar,TF_MS
from src.overlap_diversification import diversification_gate
from src.overlap_market import PublicMarket,select_instruments
from src.common_listings import select_common_instruments
from src.overlap_delivery import OverlapLedger
from tests.test_ict_overlap import snapshot


NOW=64*TF_MS['1d']


def series(tf,phase=0):
    price=100.;result=[];width=TF_MS[tf]
    for i in range(NOW//width):
        previous=price;price*=math.exp(.01*math.sin(2*math.pi*i/10+phase))
        result.append(Bar(i*width,previous,max(previous,price)+1,min(previous,price)-1,price,1000))
    return result


def inputs():
    frames={tf:series(tf,math.pi/2) for tf in ('4h','1d')}
    btc={tf:series(tf) for tf in ('4h','1d')}
    turnovers=[(i*TF_MS['1d'],10e6) for i in range(34,64)]
    ticker={'bid1Price':'100','ask1Price':'100.01','turnover24h':'20000000'}
    return frames,btc,turnovers,ticker


class DiversificationTests(unittest.TestCase):
    def test_independent_returns_and_persistent_quote_turnover_pass(self):
        proof=diversification_gate(*inputs(),NOW)
        self.assertTrue(proof['eligible'],proof['reasons'])
        self.assertEqual(proof['correlations']['4h_60d']['samples'],360)
        self.assertEqual(proof['days_above_5m_30d'],30)

    def test_positive_and_inverse_coupling_both_fail(self):
        for phase in (0,math.pi):
            frames,btc,vol,ticker=inputs()
            frames={tf:series(tf,phase) for tf in ('4h','1d')}
            proof=diversification_gate(frames,btc,vol,ticker,NOW)
            self.assertFalse(proof['eligible']);self.assertIn('4h_60d_correlation_high',proof['reasons'])

    def test_one_day_volume_spike_does_not_pass_persistence(self):
        frames,btc,vol,ticker=inputs();vol=[(t,1e6) for t,_ in vol];vol[-1]=(vol[-1][0],1e9)
        proof=diversification_gate(frames,btc,vol,ticker,NOW)
        self.assertFalse(proof['eligible']);self.assertIn('daily_turnover_not_persistent',proof['reasons'])

    def test_short_history_stale_reference_and_wide_spread_hold_alerts(self):
        frames,btc,vol,ticker=inputs();frames={tf:bars[-40:] for tf,bars in frames.items()}
        self.assertFalse(diversification_gate(frames,btc,vol,ticker,NOW)['eligible'])
        frames,btc,vol,ticker=inputs();btc['4h']=btc['4h'][:-1]
        self.assertIn('4h_history_insufficient_or_stale',diversification_gate(frames,btc,vol,ticker,NOW)['reasons'])
        frames,btc,vol,ticker=inputs();ticker['ask1Price']='101'
        self.assertIn('spread_wide_or_missing',diversification_gate(frames,btc,vol,ticker,NOW)['reasons'])

    def test_only_verified_exchange_commodity_can_join(self):
        item={'baseCoin':'CL','symbol':'CLUSDT','category':'USDT-FUTURES','quoteCoin':'USDT','status':'online','type':'perpetual','symbolType':'commodity','instrumentType':'energy','isRwa':'YES'}
        rows=select_instruments([item],('CL',));self.assertEqual(len(rows),1)
        common={'symbol':'CLUSDT','baseAsset':'CL','quoteAsset':'USDT','status':'TRADING','contractType':'TRADIFI_PERPETUAL','underlyingType':'COMMODITY'}
        self.assertEqual(len(select_common_instruments(rows,[common])),1)
        common['underlyingType']='COIN';self.assertFalse(select_common_instruments(rows,[common]))
        item['isRwa']='NO';self.assertFalse(select_instruments([item],('CL',)))

    def test_quote_turnover_cache_never_promotes_forming_day(self):
        with tempfile.TemporaryDirectory() as directory:
            market=PublicMarket(directory)
            rows=[[i*TF_MS['1d'],100,101,99,100,1,10000000] for i in range(34,65)]
            market.get=Mock(return_value={'data':rows})
            self.assertEqual(len(market.daily_turnovers('CLUSDT',NOW+1)),30)
            self.assertEqual(market.get.call_count,1)
            market.daily_turnovers('CLUSDT',NOW+TF_MS['1d']+1)
            self.assertEqual(market.get.call_count,2)

    def test_failed_supplement_gate_keeps_lock_and_blocks_forced_resend(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.json';s=snapshot();c=s['tracks']['short']['candidate']
            c['equilibrium']={'eligible':True,'midpoint':115}
            ledger=OverlapLedger(path,require_images=True,overlap_only=True,approach_only=True)
            ledger.observe(s);ledger.dispatch_symbols(lambda _:True,lambda _:Path('x'),{})
            c['monitor_eligible']=False
            ledger=OverlapLedger(path,require_images=True,overlap_only=True,approach_only=True,force_resend=True)
            ledger.observe(s);self.assertFalse(ledger.state['pending']);self.assertTrue(ledger.state['locks']['BTCUSDT|short'])
            c['monitor_eligible']=True
            ledger=OverlapLedger(path,require_images=True,overlap_only=True,approach_only=True)
            ledger.observe(s);self.assertFalse(ledger.state['pending'])


if __name__=='__main__':unittest.main()
