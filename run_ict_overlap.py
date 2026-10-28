"""BTC+exchange stock/ETF:4h/1h,1d/4h,1w/1d; preview by default."""
import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import time

from config.overlap_settings import TRACKS, TRACK_LABELS, BASES, POLL_SECONDS
from src.ict_overlap import scan,compact_notice
from src.overlap_market import PublicMarket,select_instruments,quote_price
from src.overlap_delivery import OverlapLedger
from src.ict_notices import single_writer,telegram_sender

ROOT=Path(__file__).resolve().parent


def run_once(market,instruments,ledger,sender=None,persist=False,snapshot=None,image_sender=None,render_previews=False):
    results=[];failures=[];chart_frames={}
    tickers,received=market.tickers()
    for instrument in instruments:
        symbol=instrument["symbol"];frames={};errors={};now=int(time.time()*1000)
        try:price=quote_price(tickers[symbol],now)
        except (KeyError,ValueError,TypeError):
            failures.append({"symbol":symbol,"reason":"ticker_unavailable_or_stale"});continue
        for tf in {tf for pair in TRACKS.values() for tf in pair}:
            try:frames[tf]=market.frame(symbol,tf,now)
            except Exception as error:errors[tf]=type(error).__name__
        # Recheck at the end, so crossing a candle boundary pauses affected
        # tracks until the newly closed candle has actually been retrieved.
        observed=int(time.time()*1000)
        if observed-now>120000:
            failures.append({"symbol":symbol,"reason":"scan_took_too_long"});continue
        try:price=quote_price(tickers[symbol],observed)
        except (KeyError,ValueError,TypeError):
            failures.append({"symbol":symbol,"reason":"ticker_became_stale_during_scan"});continue
        result=scan(symbol,frames,observed,price,equilibrium_filter=True)
        asset_label={'stock':'주식','etf':'ETF','index':'지수'}.get(instrument.get('instrumentType'),'상품')
        result.update(display_name=f"{instrument.get('fullName',symbol)} ({symbol}) · "+('BTC 무기한' if instrument['baseCoin']=='BTC' else f'거래소 {asset_label} 무기한'),data_errors=errors)
        pending=ledger.observe(result,result["display_name"],persist=persist or sender is not None)
        results.append(result)
        chart_frames[symbol]=frames
        if sender is None:
            own=[e for e in pending.values() if e['candidate']['symbol']==symbol]
            if own:print(compact_notice(own))
        for name,state in result["tracks"].items():
            c=state["candidate"]
            print(f"{symbol} {TRACK_LABELS[name]}: "+(f"{c['low']:.4f}~{c['high']:.4f} ({c['score']}/4)" if c else state["status"]))
    # Dispatch only events refreshed by a successful scan in this run. A full
    # symbol fetch failure cannot leak an older pending alert into delivery.
    delivery={"sent":0,"failed":0}
    if render_previews and sender is None:
        from src.overlap_charts import render_symbol_chart
        for result in results:
            symbol_events=[]
            for name,state in result['tracks'].items():
                if name=='auxiliary' or state['candidate'] is None:continue
                c=state['candidate'];event={'candidate':c,'timestamp':result['as_of'],'stage':'prepared','new_formation':False}
                if not c['context_price_breach'] and c.get('equilibrium',{}).get('eligible'):symbol_events.append(event)
            if symbol_events:render_symbol_chart(symbol_events,chart_frames[result['symbol']],ROOT/'runtime/ict_overlap/charts'/f"{result['symbol']}_overlap_preview.png")
    if sender:
        successful={r['symbol'] for r in results}
        held={k:e for k,e in ledger.state['pending'].items() if e['candidate']['symbol'] not in successful}
        for key in held:del ledger.state['pending'][key]
        if image_sender:
            from src.overlap_charts import render_symbol_chart
            contexts={}
            for result in results:
                events=[]
                for state in result['tracks'].values():
                    c=state['candidate']
                    if c and not c['context_price_breach'] and c.get('equilibrium',{}).get('eligible'):events.append({'candidate':c,'timestamp':result['as_of'],'stage':'approach','new_formation':False})
                contexts[result['symbol']]=events
            def renderer(events):
                c=events[0]['candidate'];return render_symbol_chart(events,chart_frames[c['symbol']],ROOT/'runtime/ict_overlap/charts'/f"{c['symbol']}_overlap.png")
            delivery=ledger.dispatch_symbols(image_sender,renderer,contexts)
        else:delivery=ledger.dispatch(sender,batch=True)
        ledger.state['pending'].update(held);ledger.save()
    report={"as_of":int(time.time()*1000),"mode":"send" if sender else "preview","exchange":"bitget","category":"USDT-FUTURES","instruments":instruments,"symbols":results,"failures":failures,"delivery":delivery}
    if snapshot:
        path=Path(snapshot);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"중첩 알림: 전송 {delivery['sent']}건 / 실패 {delivery['failed']}건")
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description="BTC+거래소 주식/ETF: 단기4H/1H·중기1D/4H·장기1W/1D 중첩 관찰")
    parser.add_argument('--send',action='store_true');parser.add_argument('--watch',action='store_true')
    parser.add_argument('--render-preview',action='store_true',help='미리보기에서도 현재 중첩 차트 이미지 생성')
    parser.add_argument('--bases',nargs='+',default=list(BASES),help='거래소 기초 심볼. 코인은BTC만 허용')
    parser.add_argument('--state',type=Path,default=ROOT/'runtime/ict_overlap/state.json')
    parser.add_argument('--snapshot',type=Path,default=ROOT/'runtime/ict_overlap/preview.json')
    parser.add_argument('--unlock',nargs=2,metavar=('SYMBOL','TRACK'),help='특정 종목·단기/중기/장기 잠금만 수동 해제')
    args=parser.parse_args(argv)
    if args.unlock:
        symbol,track=args.unlock
        if track not in ('short','medium','long'):parser.error('TRACK must be short, medium or long')
        with single_writer(args.state.with_suffix('.lock')):
            OverlapLedger(args.state).unlock(symbol,track)
        print(f'{symbol} {TRACK_LABELS[track]} 잠금 해제');return 0
    market=PublicMarket(ROOT/'runtime/ict_overlap/cache')
    catalog=market.instruments();instruments=select_instruments(catalog,tuple(dict.fromkeys(args.bases)))
    missing=set(args.bases)-{r['baseCoin'] for r in instruments}
    if missing:print('지원 대상에서 제외된 요청 심볼: '+', '.join(sorted(missing)))
    if not instruments:raise ValueError('No supported instruments')
    sender=None
    if args.send:
        from dotenv import load_dotenv
        load_dotenv(ROOT/'.env');sender=telegram_sender(os.getenv('TELEGRAM_BOT_TOKEN'),os.getenv('TELEGRAM_CHAT_ID'))
    with single_writer(args.state.with_suffix('.lock')) if args.send or args.watch else nullcontext():
        ledger=OverlapLedger(args.state,require_images=args.send,overlap_only=True,approach_only=True)
        image_sender=None
        if args.send:
            from src.overlap_telegram import TelegramMedia
            image_sender=TelegramMedia(os.getenv('TELEGRAM_BOT_TOKEN'),os.getenv('TELEGRAM_CHAT_ID')).send_images
        catalog_checked=time.monotonic()
        while True:
            try:
                if time.monotonic()-catalog_checked>=3600:
                    instruments=select_instruments(market.instruments(),tuple(dict.fromkeys(args.bases)))
                    catalog_checked=time.monotonic()
                report=run_once(market,instruments,ledger,sender,persist=args.watch,snapshot=args.snapshot,image_sender=image_sender,render_previews=args.render_preview)
            except Exception as error:
                if not args.watch:raise
                print(f'시세 확인 보류 ({type(error).__name__}); 다음 주기에 재시도',flush=True)
                time.sleep(POLL_SECONDS);continue
            if not args.watch:return int(bool(report['failures'] or report['delivery']['failed']))
            time.sleep(POLL_SECONDS)


if __name__=='__main__':
    try:raise SystemExit(main())
    except KeyboardInterrupt:print('중첩 감시 종료')
    except Exception as error:
        print(f'중첩 감시 중단 ({type(error).__name__})');raise SystemExit(1)
