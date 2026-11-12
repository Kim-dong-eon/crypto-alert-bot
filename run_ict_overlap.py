"""Graded OB reaction alerts; preview by default."""
import argparse
from contextlib import nullcontext
import json
import math
import os
from pathlib import Path
import time
import traceback
from functools import lru_cache

import requests

from config.overlap_settings import TRACK_LABELS, BASES, POLL_SECONDS,DIVERSIFIER_BASES
from src.ict_overlap import compact_notice,alert_eligible
from src.structure_alerts import StructureLedger,candidates
from src.pullback_alerts import scan,refinement_history_starts,POLICY,TRIPLE_TRACKS,TRACKS
from src.star_delivery import StarAlertLedger
from src.star_alerts import RULESET as STAR_RULESET, scan as scan_stars, unit_text as star_text
from src.entry_execution import depth_quote, entry_plan
from src.pullback_data import live_frames
from src.staircase_rules import RULESET
from src.ict_engine import TF_MS,frame_open
from src.review_history import HistoricalMarket
from src.overlap_entry import recent_minutes,MINUTE
from src.overlap_market import PublicMarket,select_instruments,quote_price
from src.overlap_delivery import OverlapLedger
from src.ict_notices import single_writer,telegram_sender
from src.common_listings import binance_listings,select_common_instruments
from src.overlap_diversification import diversification_gate
from src.overlap_status import overview_events

ROOT=Path(__file__).resolve().parent
ENTRY_NOTIONAL_USDT = 1000.
STAR_TRACKS = TRACKS
FORMATION_TRACKS = tuple(STAR_TRACKS)


def run_once(market,instruments,ledger,sender=None,persist=False,snapshot=None,image_sender=None,render_previews=False,listing_check=None,status_symbols=(),unit_text_sender=None,entry_notional=ENTRY_NOTIONAL_USDT):
    results=[];failures=[];chart_frames={}
    graded = isinstance(ledger, StarAlertLedger)
    require_execution = graded and ledger.require_execution
    paths = {name: TRIPLE_TRACKS[name] for name in STAR_TRACKS} if graded else TRACKS
    tick_sizes = {item['symbol']:item.get('priceMultiplier') for item in instruments}
    execution_checks = {}

    def get_execution_quote(symbol):
        try:
            book = market.get('orderbook', category='USDT-FUTURES', symbol=symbol, limit='50')['data']
            quote = depth_quote(book, int(time.time()*1000), entry_notional)
        except (requests.RequestException, ValueError, KeyError, TypeError, OverflowError) as error:
            execution_checks[symbol] = {'available': False, 'reason': str(error) if type(error) is ValueError else type(error).__name__}
            return None
        execution_checks[symbol] = {'available': True, **quote}
        return quote

    def refresh_entry(c):
        quote = get_execution_quote(c['symbol'])
        plan = entry_plan(*c['structural_band'], c['stop'], c['target'], c['direction'], quote,
                          tick_sizes[c['symbol']], prior_band=c['band'])
        if plan is None:
            return None
        lo, hi = plan['band']
        return {**c, **plan, 'low': lo, 'high': hi,
                'touch': {**c['touch'], 'observed_price': quote['mid']}}
    tickers,received=market.tickers()
    for instrument in instruments:
        symbol=instrument["symbol"];frames={};errors={};now=int(time.time()*1000)
        if now-received>60000:tickers,received=market.tickers()
        try:price=quote_price(tickers[symbol],now)
        except (KeyError,ValueError,TypeError):
            failures.append({"symbol":symbol,"reason":"ticker_unavailable_or_stale"});continue
        for tf in sorted({tf for path in paths.values() for tf in path}, key=TF_MS.get):
            try:frames[tf]=market.frame(symbol,tf,now)
            except Exception as error:errors[tf]=type(error).__name__
        touch_since=ledger.state.get('touch_cursors',{}).get(symbol,(now//MINUTE-5)*MINUTE)
        if reset_at := ledger.state.get('alert_reset_at'):
            touch_since = max(touch_since, ((reset_at + MINUTE - 1) // MINUTE) * MINUTE)
        minute_bars=[];minute_coverage=None;minute_error=None
        # The same completed minutes both detect between-scan contacts and
        # reconstruct the still-open 4H candle for the approved image format.
        try:minute_bars,minute_coverage=recent_minutes(market,symbol,min(touch_since,frame_open(now,'4h')),int(time.time()*1000))
        except Exception as error:minute_error=type(error).__name__
        refinement_starts={}
        if not graded:
            try:
                refinement_starts=refinement_history_starts(symbol,frames,now,price,minute_bars,
                                                            max(touch_since,minute_coverage['start']) if minute_coverage else touch_since)
            except Exception as error:errors['refinement_context']=type(error).__name__
        for tf,start in refinement_starts.items():
            if frames.get(tf) and frames[tf][0].timestamp<=start:continue
            try:frames[tf]=market.refinement_frame(symbol,tf,int(time.time()*1000),start)
            except Exception as error:errors[tf]=type(error).__name__
        diversity_data=None;diversity_error=None
        if instrument['baseCoin'] in DIVERSIFIER_BASES:
            try:
                reference={tf:market.frame('BTCUSDT',tf,now) for tf in ('4h','1d')}
                diversity_data=(reference,market.daily_turnovers(symbol,now))
            except Exception as error:diversity_error=type(error).__name__
        # Recheck at the end, so crossing a candle boundary pauses affected
        # tracks until the newly closed candle has actually been retrieved.
        observed=int(time.time()*1000)
        if observed-now>600000:
            failures.append({"symbol":symbol,"reason":"scan_took_too_long"});continue
        # A first 15m backfill can cross a boundary. Retrieve the actual newly
        # completed bar; never promote an old partial cache snapshot.
        for tf in list(frames):
            if frames[tf] and frames[tf][-1].timestamp+TF_MS[tf]!=frame_open(observed,tf):
                try:frames[tf]=(market.refinement_frame(symbol,tf,observed,refinement_starts[tf]) if tf in refinement_starts else market.frame(symbol,tf,observed))
                except Exception as error:errors[tf]=type(error).__name__;frames.pop(tf,None)
        if minute_coverage and minute_coverage['end']!=observed//MINUTE*MINUTE:
            try:minute_bars,minute_coverage=recent_minutes(market,symbol,min(touch_since,frame_open(observed,'4h')),observed)
            except Exception as error:minute_bars=[];minute_coverage=None;minute_error=type(error).__name__
        if observed-received>60000:tickers,received=market.tickers()
        try:price=quote_price(tickers[symbol],observed)
        except (KeyError,ValueError,TypeError):
            failures.append({"symbol":symbol,"reason":"ticker_became_stale_during_scan"});continue
        since = max(touch_since, minute_coverage['start']) if minute_coverage else touch_since
        if graded:
            result = scan_stars(symbol, frames, observed, price, minute_bars=minute_bars, touch_since=since,
                                contexts=ledger.state['star_contexts'].get(symbol), tracks=STAR_TRACKS,
                                formation_tracks=FORMATION_TRACKS,
                                origin_tracks=FORMATION_TRACKS,
                                execution_quote=lru_cache(maxsize=1)(lambda: get_execution_quote(symbol)) if require_execution else None,
                                tick_size=tick_sizes[symbol])
        else:
            result=scan(symbol,frames,observed,price,equilibrium_filter=True,minute_bars=minute_bars,touch_since=since)
        result.update(minute_coverage=minute_coverage,minute_error=minute_error)
        if instrument['baseCoin'] in DIVERSIFIER_BASES:
            try:
                eligibility=(diversification_gate(frames,*diversity_data,tickers[symbol],observed) if diversity_data
                             else {'eligible':False,'reasons':['screening_data_unavailable'],'error':diversity_error})
            except (ValueError,TypeError,KeyError,OverflowError):eligibility={'eligible':False,'reasons':['screening_data_invalid']}
            result['diversification']=eligibility
            for state in result['tracks'].values():
                for candidate in candidates(state):
                    candidate['monitor_eligible']=candidate.get('monitor_eligible',True) and eligibility['eligible']
                    candidate['diversification']=eligibility
            print(f"{symbol} BTC 상관·유동성: "+('통과' if eligibility['eligible'] else '대기'))
        asset_label={'stock':'주식','etf':'ETF','index':'지수','energy':'원유','metal':'금속'}.get(instrument.get('instrumentType'),'상품')
        instrument_label = '코인 무기한' if instrument.get('symbolType') == 'crypto' else f'거래소 {asset_label} 무기한'
        result.update(display_name=f"Bitget · {instrument.get('fullName',symbol)} ({symbol}) · {instrument_label}",data_errors=errors)
        pending=ledger.observe(result,result["display_name"],persist=persist or sender is not None)
        if persist or sender is not None:
            cursors=ledger.state.setdefault('touch_cursors',{})
            cursors.setdefault(symbol,touch_since)
            if minute_coverage:cursors[symbol]=minute_coverage['end']
            ledger.save()
        results.append(result)
        chart_frames[symbol]=live_frames(frames,minute_bars,observed,price)
        if sender is None:
            own=[e for e in pending.values() if e['candidate']['symbol']==symbol]
            if own:print('\n\n'.join(star_text(e['candidate']) for e in own) if graded else compact_notice(own))
        for name,state in result["tracks"].items():
            c=state["candidate"]
            label = '★' * c['level'] if graded and c else f"{c['score']}/4" if c else ''
            print(f"{symbol} {TRACK_LABELS[name]}: "+(f"{c['low']:.4f}~{c['high']:.4f} ({label})" if c else state["status"]))
    # Dispatch only events refreshed by a successful scan in this run. A full
    # symbol fetch failure cannot leak an older pending alert into delivery.
    delivery={"sent":0,"failed":0}
    status_delivery={'sent':0,'failed':0,'symbols':[]}
    if render_previews and sender is None:
        from src.split_alert_preview import split_units,render_side_by_side_unit
        for result in results:
            symbol_events=[{'candidate':c,'timestamp':result['as_of'],'stage':'approach'} for state in result['tracks'].values() for c in candidates(state) if alert_eligible(c)]
            for unit in split_units(symbol_events):
                c=unit['candidate']
                render_side_by_side_unit(result,unit,chart_frames[result['symbol']],ROOT/'runtime/ict_overlap/charts'/f"{result['symbol']}_{c['track']}_{c['lenses'][0]}_{c['id']}_preview")
    if sender:
        successful={r['symbol'] for r in results}
        held={k:e for k,e in ledger.state['pending'].items() if e['candidate']['symbol'] not in successful}
        for key in held:del ledger.state['pending'][key]
        if image_sender:
            from src.overlap_charts import render_symbol_chart,render_alert_pair
            contexts={}
            for result in results:
                contexts[result['symbol']]=[e for e in ledger.state['pending'].values() if e['candidate']['symbol']==result['symbol']]
            def renderer(events):
                symbol=events[0]['candidate']['symbol']
                if events[0]['candidate'].get('structure_area'):
                    from src.structure_review_charts import render_structure_pair
                    item=next(r for r in results if r['symbol']==symbol)
                    return render_structure_pair(item,events,chart_frames[symbol],ROOT/'runtime/ict_overlap/charts'/f"{symbol}_overlap")
                return render_alert_pair(events,chart_frames[symbol],ROOT/'runtime/ict_overlap/charts'/f"{symbol}_overlap")
            before={symbol:{key for key,event in ledger.state['pending'].items() if event['candidate']['symbol']==symbol} for symbol in successful}
            if isinstance(ledger,StructureLedger):
                from src.split_alert_preview import render_side_by_side_unit
                def unit_renderer(unit):
                    c=unit['candidate'];item=next(r for r in results if r['symbol']==c['symbol'])
                    prefix=ROOT/'runtime/ict_overlap/charts'/f"{c['symbol']}_{c['track']}_{c['lenses'][0]}_{c['id']}"
                    return render_side_by_side_unit(item,unit,chart_frames[c['symbol']],prefix)
                if require_execution:
                    delivery=ledger.dispatch_units(image_sender,unit_text_sender or sender,unit_renderer,refresh=refresh_entry)
                else:
                    delivery=ledger.dispatch_units(image_sender,unit_text_sender or sender,unit_renderer)
            else:delivery=ledger.dispatch_symbols(image_sender,renderer,contexts)
            completed={symbol for symbol,keys in before.items() if keys and keys.isdisjoint(ledger.state['pending'])}
            for symbol in status_symbols:
                if symbol in completed:continue
                item=next((item for item in results if item['symbol']==symbol),None)
                events=overview_events(item) if item else None
                if not events:status_delivery['failed']+=1;continue
                try:
                    caption='요청한 현재 상태\n'+compact_notice(events)
                    path=render_symbol_chart(events,chart_frames[symbol],ROOT/'runtime/ict_overlap/charts'/f'{symbol}_status.png')
                    ok=image_sender([(path,caption)]) is True
                except Exception:ok=False
                if ok:status_delivery['sent']+=1;status_delivery['symbols'].append(symbol)
                else:status_delivery['failed']+=1
        else:delivery=ledger.dispatch(sender,batch=True)
        ledger.state['pending'].update(held);ledger.save()
    report={"as_of":int(time.time()*1000),"mode":"send" if sender else "preview","exchange":"bitget","category":"USDT-FUTURES","strategy":POLICY,'paths':paths,'deduplication_enabled':True,"binance_listing_check":listing_check,"instruments":instruments,"symbols":results,"failures":failures,"delivery":delivery,'status_delivery':status_delivery}
    report['ruleset']=STAR_RULESET if graded else RULESET
    if graded:
        report['formation_tracks'] = FORMATION_TRACKS
        report['origin_tracks'] = FORMATION_TRACKS
        report['alert_reset_at'] = ledger.state.get('alert_reset_at')
    if require_execution:
        report.update(entry_only=ledger.entry_only, execution_required_for_grade=3,
                      entry_notional_usdt=entry_notional, execution_checks=execution_checks)
    if snapshot:
        path=Path(snapshot);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"중첩 알림: 전송 {delivery['sent']}건 / 실패 {delivery['failed']}건")
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description="별점 알림: 초단기1H/15m/5m·단기4H/1H/15m·중기1D/4H/1H·장기1W/1D/4H")
    parser.add_argument('--send',action='store_true');parser.add_argument('--watch',action='store_true')
    parser.add_argument('--resend-current',action='store_true',help='이번 검사만 기존 발송 잠금을 무시하고 현재 조건 충족 구간 재발송')
    parser.add_argument('--reset-alerts', action='store_true', help='모든 알림 잠금과 접촉 이력을 초기화하고 새 검사 시작')
    parser.add_argument('--inspect-symbol',help='이번 실행만 이 공통 상장 종목의 전체 상태 발송. 여러 종목은 쉼표로 구분')
    parser.add_argument('--render-preview',action='store_true',help='미리보기에서도 현재 중첩 차트 이미지 생성')
    parser.add_argument('--entry-notional',type=float,default=ENTRY_NOTIONAL_USDT,help='호가 슬리피지 계산에 사용할 주문금액(USDT)')
    parser.add_argument('--bases',nargs='+',default=list(BASES),help='공통 상장 선물의 기초 심볼')
    parser.add_argument('--state',type=Path,default=ROOT/'runtime/ict_overlap/state.json')
    parser.add_argument('--snapshot',type=Path,default=ROOT/'runtime/ict_overlap/preview.json')
    parser.add_argument('--unlock',nargs=2,metavar=('SYMBOL','TRACK'),help='특정 종목·단기/중기/장기 잠금만 수동 해제')
    args=parser.parse_args(argv)
    if not math.isfinite(args.entry_notional) or args.entry_notional <= 0:parser.error('--entry-notional must be positive')
    if args.resend_current and args.watch:parser.error('--resend-current is one-shot only; omit --watch')
    if args.reset_alerts and (not args.send or args.resend_current or args.unlock):
        parser.error('--reset-alerts requires --send and cannot be combined with --resend-current or --unlock')
    if args.inspect_symbol and args.watch:parser.error('--inspect-symbol is one-shot only; omit --watch')
    if args.unlock:
        symbol,track=args.unlock
        if track not in TRACKS:parser.error('TRACK must be fast, short, medium or long')
        with single_writer(args.state.with_suffix('.lock')):
            StarAlertLedger(args.state).unlock(symbol,track)
        print(f'{symbol} {TRACK_LABELS[track]} 잠금 해제');return 0
    market=HistoricalMarket(ROOT/'runtime/ict_overlap/cache/structure')
    catalog=market.instruments()
    binance_rows,listing_check=binance_listings(ROOT/'config/binance_contracts.json',market.cache/'binance_contracts.json')
    bases = tuple(dict.fromkeys(args.bases))
    instruments=select_common_instruments(select_instruments(catalog,bases),binance_rows,exclude_korean_equities=True)
    print('공통 상장 확인: '+listing_check['mode']+' / '+str(listing_check['verified_at']))
    missing=set(args.bases)-{r['baseCoin'] for r in instruments}
    if missing:print('지원 대상에서 제외된 요청 심볼: '+', '.join(sorted(missing)))
    if not instruments:raise ValueError('No supported instruments')
    inspect_symbols=tuple(dict.fromkeys(x.strip() for x in (args.inspect_symbol or '').split(',') if x.strip()))
    if any(symbol not in {r['symbol'] for r in instruments} for symbol in inspect_symbols):parser.error('inspect-symbol must be selected and verified on both exchanges')
    sender=None
    if args.send:
        from dotenv import load_dotenv
        load_dotenv(ROOT/'.env');sender=telegram_sender(os.getenv('TELEGRAM_BOT_TOKEN'),os.getenv('TELEGRAM_CHAT_ID'))
    with single_writer(args.state.with_suffix('.lock')) if args.send or args.watch else nullcontext():
        ledger=StarAlertLedger(args.state,require_images=args.send,force_resend=args.resend_current,require_execution=True)
        if args.reset_alerts:
            ledger.reset_alerts(int(time.time()*1000))
        image_sender=None
        unit_text_sender=None
        if args.send:
            from src.overlap_telegram import TelegramMedia
            media=TelegramMedia(os.getenv('TELEGRAM_BOT_TOKEN'),os.getenv('TELEGRAM_CHAT_ID'),min_interval=3.1)
            image_sender=media.send_images;unit_text_sender=media.send_text
        catalog_checked=time.monotonic()
        while True:
            try:
                if time.monotonic()-catalog_checked>=3600:
                    binance_rows,listing_check=binance_listings(ROOT/'config/binance_contracts.json',market.cache/'binance_contracts.json')
                    instruments=select_common_instruments(select_instruments(market.instruments(),bases),binance_rows,exclude_korean_equities=True)
                    catalog_checked=time.monotonic()
                report=run_once(market,instruments,ledger,sender,persist=args.watch,snapshot=args.snapshot,image_sender=image_sender,render_previews=args.render_preview,listing_check=listing_check,status_symbols=inspect_symbols,unit_text_sender=unit_text_sender,entry_notional=args.entry_notional)
            except Exception as error:
                if not args.watch:raise
                print(f'시세 확인 보류 ({type(error).__name__}); 다음 주기에 재시도',flush=True)
                time.sleep(POLL_SECONDS);continue
            if not args.watch:return int(bool(report['failures'] or report['delivery']['failed'] or report['status_delivery']['failed']))
            time.sleep(POLL_SECONDS)


if __name__=='__main__':
    try:raise SystemExit(main())
    except KeyboardInterrupt:print('중첩 감시 종료')
    except Exception as error:
        traceback.print_tb(error.__traceback__)
        print(f'중첩 감시 중단 ({type(error).__name__})');raise SystemExit(1)
