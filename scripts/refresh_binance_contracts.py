"""Refresh the dated official fallback catalog from a host with normal access."""
import json
from pathlib import Path
import sys
import time
import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from config.overlap_settings import BASES
from src.common_listings import SOURCE,FIELDS


def main():
    session=requests.Session();session.trust_env=False
    response=session.get(SOURCE,timeout=20)
    response.raise_for_status()
    payload=response.json()
    rows=[{key:row.get(key) for key in FIELDS} for row in payload['symbols'] if row.get('baseAsset') in BASES]
    if not rows:raise ValueError('Empty official catalog: saved evidence was not changed')
    data={'source':SOURCE,'verified_at':int(time.time()*1000),'server_time':payload.get('serverTime'),'symbols':rows}
    path=ROOT/'config/binance_contracts.json'
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Official listing evidence saved: {len(rows)} contracts, {path}')


if __name__=='__main__':main()
