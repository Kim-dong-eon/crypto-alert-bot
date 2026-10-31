r"""BTC two-scale review only. Does not load Telegram secrets or alert state.

Usage: .\venv\Scripts\python.exe .\run_structure_review.py
"""
import json
from pathlib import Path
import time
from src.overlap_market import PublicMarket,quote_price
from src.review_history import history_frame,weekly_from_daily
from src.structure_review import analyze
from src.structure_review_charts import render_review
from src.ict_overlap import kst,scan
from src.ict_engine import TF_MS

ROOT=Path(__file__).resolve().parent


def main():
    out=ROOT/'runtime/structure_review';out.mkdir(parents=True,exist_ok=True)
    market=PublicMarket(out/'cache');frames={}
    for tf,count in (('4h',1080),('1h',1440),('1d',1102)):
        now=int(time.time()*1000)
        frames[tf]=history_frame(market,'BTCUSDT',tf,now,count)
        print(tf,len(frames[tf]),kst(frames[tf][0].timestamp),flush=True)
    now=int(time.time()*1000);frames['1w']=weekly_from_daily(frames['1d'],now)
    tickers,_=market.tickers();now=int(time.time()*1000)
    result=analyze(frames,now,quote_price(tickers['BTCUSDT'],now))
    # Keep the original sweep/MSS interpretation visible as a separate
    # diagnostic. It neither hides a review area nor certifies every box.
    confirmed=scan('BTCUSDT',frames,now,result['price'],equilibrium_filter=True)
    result['confirmation']={name:{'status':v['status'],'direction':v['direction'],
                                  'context':v['context'],'liquidity':v.get('liquidity')}
                            for name,v in confirmed['tracks'].items()}
    result['coverage']={tf:{'bars':len(values),'first_open':values[0].timestamp,
                            'last_close':values[-1].timestamp+TF_MS[tf]}
                        for tf,values in frames.items() if values}
    paths=render_review(result,frames,out/'charts');result['charts']=[str(p) for p in paths]
    (out/'BTC_review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(kst(now),'price',result['price'],flush=True)
    for scale,v in result['views'].items():
        print(scale,v['structure'],'turns',len(v['turns']),flush=True)
        for a in v['selected']:
            print(a['direction'],a['low'],a['high'],'overlaps',len(a['overlaps']),flush=True)
    for p in paths:print(p,flush=True)


if __name__=='__main__':main()
