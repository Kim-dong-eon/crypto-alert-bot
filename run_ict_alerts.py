"""Current overlap CLI; immutable lecture-v8 helpers retained for research."""
import argparse
from dataclasses import replace
from contextlib import nullcontext
import json
import os
from pathlib import Path
import time

from config.ict_settings import DEFAULTS
from src.ict_engine import TERMINAL, TF_MS, closed_bars, frame_open, inside_ote, trade_target
from src.ict_triple import TRACK_NAMES
from src.ict_lecture import scan_lecture as scan_short,CHAIN
from src.ict_exit_plan import exit_plan
from src.ict_notices import NoticeLedger, format_notice, single_writer, telegram_sender

ROOT = Path(__file__).resolve().parent


def fetch_frames(exchange, symbol, now_ms, cfg=DEFAULTS):
    frames = {}
    frames["live_ranges"] = {}
    for tf in CHAIN:
        rows = exchange.fetch_ohlcv(symbol, tf, limit=cfg.history_bars)
        bars = closed_bars(rows, tf, now_ms)
        if len(bars) < 50:
            raise ValueError("Insufficient closed candle history")
        expected = frame_open(now_ms, tf)
        if bars[-1].timestamp + TF_MS[tf] != expected:
            raise ValueError("Latest closed candle is unavailable or delayed")
        frames[tf] = bars
        live = [row for row in rows if int(row[0]) == expected]
        if not live:
            raise ValueError("Live candle unavailable for pre-send price check")
        frames["live_ranges"][tf] = {"low": float(live[-1][3]), "high": float(live[-1][2]), "close": float(live[-1][4])}
    return frames


def guard_live_prices(events, live_range, now_ms, cfg=DEFAULTS):
    """Live prices can veto a signal, but never confirm a new structure."""
    latest = {}
    for event in events:
        latest[event["id"]] = event
    guarded, dismissed = list(events), []
    for event in latest.values():
        if event["stage"] in TERMINAL:
            continue
        d = event["direction"]
        if event.get("lecture_profile") and event["stage"]=="review":
            price=live_range["close"]
            stopped=live_range["low"]<=event["stop"] if d==1 else live_range["high"]>=event["stop"]
            used=live_range["high"]>=event["swing_target"] if d==1 else live_range["low"]<=event["swing_target"]
            risk=d*(price-event["stop"])
            rr=d*(event["swing_target"]-price)/risk if risk>0 else 0
            valid=event["ote_low"]<=price<=event["ote_high"]
            for key in ("eq4h","eq1h"):
                r=event[key]
                valid &= r["low"]<=price<=r["midpoint"] if d==1 else r["midpoint"]<=price<=r["high"]
            reason="ob_invalidated" if stopped else "swing_target_consumed" if used else "live_rr_insufficient" if rr<cfg.reward_risk_ratio else "reaction_outside_bands" if not valid else None
            current={**event,"current_price":price}
            if reason:
                current.update(stage="cancelled",timestamp=now_ms,reason=reason)
                dismissed.append(current)
            else:
                p=exit_plan(price,event["stop"],d,event["swing_target"],cfg.reward_risk_ratio)
                current.update(entry_reference=price,trade_target=p["three_r"],exit_plan=p,liquidity_rr=rr)
            guarded.append(current)
            continue
        if event.get("zone_only"):
            current = {**event, "current_price": live_range["close"]}
            target_used = event.get("short_equilibrium") and event["swing_target"] is not None and (live_range["high"]>event["swing_target"] if d==1 else live_range["low"]<event["swing_target"])
            if d * (live_range["close"] - event["stop"]) < 0 or target_used:
                cancelled = {**current, "stage": "cancelled", "reason": "swing_target_consumed" if target_used else "ob_invalidated", "timestamp": now_ms}
                guarded.append(cancelled)
                dismissed.append(cancelled)
            else:
                if event["stage"] == "prepared" and live_range["low"] <= event["zone_high"] and live_range["high"] >= event["zone_low"]:
                    current.update(stage="touched", timestamp=now_ms)
                if event["ote_overlap"] and event["entry_zone_low"] <= live_range["close"] <= event["entry_zone_high"]:
                    entry = live_range["close"]
                    current.update(entry_reference=entry, trade_target=entry + d * cfg.reward_risk_ratio * abs(entry - event["stop"]))
                    if event.get("short_equilibrium") and event["trade_eligible"]:
                        current["exit_plan"]=exit_plan(entry,event["stop"],d,event["swing_target"],cfg.reward_risk_ratio)
                guarded.append(current)
            continue
        stopped = live_range["low"] <= event["stop"] if d == 1 else live_range["high"] >= event["stop"]
        target_hit = live_range["high"] >= event["target"] if d == 1 else live_range["low"] <= event["target"]
        reason = None
        if stopped or target_hit:
            reason = "stop_and_target_same_bar" if stopped and target_hit else ("ob_invalidated" if stopped else "liquidity_target")
        elif event["stage"] == "review":
            planned = trade_target(live_range["close"], event["stop"], event["liquidity_target"], d, cfg)
            if not inside_ote(live_range["close"], event):
                reason = "ote_price_left"
            elif "overlap_score" in event and not event["zone_low"] <= live_range["close"] <= event["zone_high"]:
                reason = "outside_confluence"
            elif planned is None:
                reason = "live_rr_insufficient"
            else:
                event = {**event, "entry_reference": live_range["close"], "trade_target": planned}
        if reason:
            cancelled = {**event, "stage": "cancelled", "reason": reason, "timestamp": now_ms}
            guarded.append(cancelled)
            dismissed.append(cancelled)
        else:
            guarded.append({**event, "current_price": live_range["close"]})
    return guarded, dismissed


def run_once(exchange, ledger, sender=None, cfg=DEFAULTS, snapshot=None):
    now_ms = int(time.time() * 1000)
    events, summaries, failures = [], [], []
    for symbol in cfg.symbols:
        try:
            frames = fetch_frames(exchange, symbol, now_ms, cfg)
            result = scan_short(symbol, {tf: frames[tf] for tf in CHAIN}, cfg)
            for name, chain in {"short":CHAIN}.items():
                track_events = [event for event in result["events"] if event["track"] == name]
                guarded, dismissed = guard_live_prices(track_events, frames["live_ranges"][chain[-1]], int(time.time() * 1000), cfg)
                if sender is not None:
                    for event in dismissed:
                        ledger.dismiss(event)
                events.extend(guarded)
                state = result["tracks"][name]
                summaries.append({"symbol": symbol, "track": name, **state})
                direction = {1: "강세", -1: "약세", 0: "확인된 흐름 없음"}[state["direction"]]
                print(f"{symbol}: {TRACK_NAMES[name]} {direction}")
        except Exception as error:
            # Exchange messages may contain request internals. Show only type.
            failures.append({"symbol": symbol, "error_type": type(error).__name__})
            print(f"{symbol}: 자료 확인 실패 ({type(error).__name__}); 해당 종목 알림 보류")
    now_ms = int(time.time() * 1000)
    if sender is None:
        candidates = ledger.candidates(events, now_ms, cfg)
        for event in candidates:
            print(format_notice(event, cfg))
        print(f"미리보기: 새 알림 후보 {len(candidates)}건 / 전송 0건")
        delivery = {"sent": 0, "failed": 0}
    else:
        delivery = ledger.dispatch(events, now_ms, sender, cfg)
        print(f"ICT 알림: 성공 {delivery['sent']}건 / 실패 {delivery['failed']}건")
    if snapshot:
        snapshot = Path(snapshot)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(json.dumps({"as_of": now_ms, "mode": "send" if sender else "preview",
                                        "symbols": summaries, "data_failures": failures,
                                        "delivery": delivery}, ensure_ascii=False, indent=2), encoding="utf-8")
    return bool(failures or delivery["failed"])


def main(argv=None):
    # The approved current observation profile. Old helpers stay importable
    # for reproducing the immutable v8 research and its tests.
    from run_ict_overlap import main as overlap_main
    return overlap_main(argv)


def legacy_lecture_main(argv=None):
    parser = argparse.ArgumentParser(description="BTC 단기: 영상 순서 패턴·반응 확인·반대 유동성까지 최소3R")
    parser.add_argument("--send", action="store_true", help="명시적으로 Telegram 전송 사용")
    parser.add_argument("--watch", action="store_true", help="60초마다 계속 확인")
    parser.add_argument("--state", type=Path, default=ROOT / "runtime" / "ict_lecture_v8_alerts_state.json")
    parser.add_argument("--snapshot", type=Path, help="현재 흐름과 최근 신호를 JSON으로 저장")
    args = parser.parse_args(argv)
    sender = None
    if args.send:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        sender = telegram_sender(os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID"))
    import ccxt
    exchange = getattr(ccxt, DEFAULTS.exchange)({"enableRateLimit": True, "timeout": 15_000,
                                               "options": {"defaultType": "swap"}})
    context = single_writer(args.state.with_suffix(".lock")) if args.send else nullcontext()
    with context:
        ledger = NoticeLedger(args.state)
        cfg=replace(DEFAULTS,symbols=("BTC/USDT:USDT",))
        while True:
            failed = run_once(exchange, ledger, sender,cfg,snapshot=args.snapshot)
            if not args.watch:
                return 1 if failed else 0
            time.sleep(DEFAULTS.poll_seconds)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("ICT 감시 종료")
    except Exception as error:
        print(f"ICT 실행 중단 ({type(error).__name__}). 설정·상태 파일·중복 실행을 확인하세요.")
        raise SystemExit(1)
