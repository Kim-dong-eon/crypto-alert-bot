import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
import requests
from src.common_listings import SOURCE,binance_listings,select_common_instruments


def bitget(base,kind='stock'):
    return {'symbol':base+'USDT','baseCoin':base,'instrumentType':kind}


def binance(base,kind='EQUITY',**extra):
    return dict(symbol=base+'USDT',baseAsset=base,quoteAsset='USDT',status='TRADING',
                contractType='TRADIFI_PERPETUAL',underlyingType=kind,**extra)


class CommonListingTests(unittest.TestCase):
    def test_exact_active_usdt_perpetual_and_asset_kind_required(self):
        rows=[binance('BTC','COIN'),binance('AAPL'),binance('SKHYNIX','KR_EQUITY')]
        chosen=select_common_instruments([bitget('BTC','crypto'),bitget('AAPL'),bitget('SKHYNIX'),bitget('NDX100','index')],rows)
        self.assertEqual([r['baseCoin'] for r in chosen],['BTC','AAPL','SKHYNIX'])
        for field,value in [('quoteAsset','USDC'),('contractType','CURRENT_QUARTER'),('status','SETTLING'),('baseAsset','1000AAPL'),('underlyingType','COIN')]:
            row=binance('AAPL');row[field]=value
            self.assertEqual(select_common_instruments([bitget('AAPL')],[row]),[])

    def test_official_451_uses_dated_snapshot_without_claiming_live_check(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'saved.json';stamp=int(time.time()*1000)-1000
            path.write_text(json.dumps({'source':SOURCE,'verified_at':stamp,'symbols':[binance('AAPL')]}))
            response=Mock(status_code=451);response.raise_for_status.side_effect=requests.HTTPError(response=response)
            with patch('src.common_listings.requests.Session') as session:
                session.return_value.get.return_value=response
                rows,proof=binance_listings(path,Path(directory)/'cache.json')
            self.assertEqual(len(rows),1);self.assertEqual(proof['mode'],'saved_official_snapshot')
            self.assertEqual(proof['verified_at'],stamp);self.assertEqual(proof['live_check_unavailable'],'HTTP 451')

    def test_invalid_saved_source_cannot_expand_universe(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'saved.json';path.write_text(json.dumps({'source':'https://example.com','verified_at':1,'symbols':[binance('NDX100','INDEX')]}))
            with patch('src.common_listings.requests.Session') as session:
                session.return_value.get.side_effect=requests.Timeout()
                with self.assertRaises(ValueError):binance_listings(path,Path(directory)/'cache.json')

    def test_live_delisting_overrides_old_saved_listing(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'saved.json';path.write_text(json.dumps({'source':SOURCE,'verified_at':1,'symbols':[binance('AAPL')]}))
            response=Mock();row=binance('AAPL');row['status']='SETTLING';response.json.return_value={'symbols':[row]}
            with patch('src.common_listings.requests.Session') as session:
                session.return_value.get.return_value=response
                rows,proof=binance_listings(path,Path(directory)/'cache.json')
            self.assertEqual(proof['mode'],'live_official');self.assertEqual(select_common_instruments([bitget('AAPL')],rows),[])


if __name__=='__main__':unittest.main()
