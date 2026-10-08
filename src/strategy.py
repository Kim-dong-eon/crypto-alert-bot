import pandas as pd
import datetime
import json
import os

HISTORY_FILE = "alert_history.json"

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def is_htf_zero_locked(df_high: pd.DataFrame, direction: str, history: dict, lock_key: str) -> bool:
    """
    상위 프레임(큰 타임) 0,0 (숏은 100,100) 잠금 판별 함수
    - LONG: 0,0 도달 시 잠금 -> 0~20 유지 중에는 계속 잠금 -> 20 초과(>20) 돌파 후 다시 20 이하로 내려올 때 해제
    - SHORT: 100,100 도달 시 잠금 -> 80~100 유지 중에는 계속 잠금 -> 80 미만(<80) 하락 후 다시 80 이상 올라올 때 해제
    """
    curr = df_high.iloc[-1]
    curr_k, curr_d = curr['stoch_rsi_k'], curr['stoch_rsi_d']

    if direction == "LONG":
        # 1) 현재 20 초과 상태라면 잠금 해제(Reset)
        if curr_k > 20 and curr_d > 20:
            if history.get(lock_key, False):
                history[lock_key] = False
                save_history(history)
            return False

        # 2) 현재 20 이하 구간에 있을 때, 가장 최근에 20 위에 있었던 시점 이후로 0,0을 찍은 적이 있는지 역추적
        touched_zero_in_cycle = False
        for i in range(len(df_high) - 1, -1, -1):
            row = df_high.iloc[i]
            k, d = row['stoch_rsi_k'], row['stoch_rsi_d']
            
            # 과거로 거슬러 올라가다 20 초과였던 캔들을 만나면 현재 침체 사이클 탐색 종료
            if k > 20 and d > 20:
                break
                
            # 이번 20 이하 사이클 도중 0, 0 (부동소수점 오차 감안 0.1 이하)을 찍었다면 잠금!
            if k <= 0.1 and d <= 0.1:
                touched_zero_in_cycle = True
                break

        # 장중 실시간으로 0,0을 찍었던 기록(history)까지 합산하여 잠금 상태 유지
        if touched_zero_in_cycle or history.get(lock_key, False):
            if not history.get(lock_key, False):
                history[lock_key] = True
                save_history(history)
            return True

        return False

    elif direction == "SHORT":
        # 1) 현재 80 미만 상태라면 잠금 해제(Reset)
        if curr_k < 80 and curr_d < 80:
            if history.get(lock_key, False):
                history[lock_key] = False
                save_history(history)
            return False

        # 2) 현재 80 이상 구간에 있을 때, 가장 최근에 80 아래에 있었던 시점 이후로 100,100을 찍은 적이 있는지 역추적
        touched_hundred_in_cycle = False
        for i in range(len(df_high) - 1, -1, -1):
            row = df_high.iloc[i]
            k, d = row['stoch_rsi_k'], row['stoch_rsi_d']
            
            if k < 80 and d < 80:
                break
                
            if k >= 99.9 and d >= 99.9:
                touched_hundred_in_cycle = True
                break

        if touched_hundred_in_cycle or history.get(lock_key, False):
            if not history.get(lock_key, False):
                history[lock_key] = True
                save_history(history)
            return True

        return False

    return False


def detect_order_block(df_low: pd.DataFrame, direction: str):
    """
    하위 프레임 오더블록(Order Block) 및 30 이하(숏 70 이상) 조건 판별 함수
    - 진행 중인 캔들(-1)이 직전 캔들(-2)을 장악했거나, 방금 마감된 캔들(-2)이 그 전 캔들(-3)을 장악한 경우를 포착
    """
    if len(df_low) < 4:
        return None

    # 검사할 후보: (기준 음봉/양봉 인덱스, 장악 돌파 캔들 인덱스)
    # 1순위: 직전 캔들(-2)을 현재 진행 캔들(-1)이 실시간 돌파할 때
    # 2순위: 그 전 캔들(-3)을 방금 마감된 캔들(-2)이 확정 돌파했을 때
    candidates = [(-2, -1), (-3, -2)]

    for ob_idx, eng_idx in candidates:
        ob_candle = df_low.iloc[ob_idx]    # 오더블록이 되는 기준 캔들
        eng_candle = df_low.iloc[eng_idx]  # 강하게 장악하며 오더블록을 완성시키는 캔들

        ob_k, ob_d = ob_candle['stoch_rsi_k'], ob_candle['stoch_rsi_d']
        eng_k, eng_d = eng_candle['stoch_rsi_k'], eng_candle['stoch_rsi_d']

        if direction == "LONG":
            # 1) 스토캐스틱 30 이하 조건 (장악 캔들 또는 오더블록 기준 캔들이 30 이하일 때)
            stoch_ok = (eng_k <= 30 and eng_d <= 30) or (ob_k <= 30 and ob_d <= 30 and eng_d <= 35)
            if not stoch_ok:
                continue

            # 2) 매수 오더블록(Bullish OB) 캔들 조건:
            # 직전 캔들은 음봉(close < open)이고, 다음 캔들이 강한 양봉(close > open)으로 직전 음봉의 시가(open)를 돌파(장악)
            is_prev_bearish = ob_candle['close'] < ob_candle['open']
            is_curr_bullish = eng_candle['close'] > eng_candle['open']
            is_engulfing = eng_candle['close'] > ob_candle['open']

            if is_prev_bearish and is_curr_bullish and is_engulfing:
                return {
                    "ob_time": str(ob_candle['datetime']),
                    "ob_low": ob_candle['low'],
                    "ob_high": ob_candle['open'],  # 매수 오더블록 상단(음봉 시가)
                    "k": eng_k,
                    "d": eng_d
                }

        elif direction == "SHORT":
            # 1) 스토캐스틱 70 이상 조건
            stoch_ok = (eng_k >= 70 and eng_d >= 70) or (ob_k >= 70 and ob_d >= 70 and eng_d >= 65)
            if not stoch_ok:
                continue

            # 2) 매도 오더블록(Bearish OB) 캔들 조건:
            # 직전 캔들은 양봉(close > open)이고, 다음 캔들이 강한 음봉(close < open)으로 직전 양봉의 시가(open)를 하향 돌파(장악)
            is_prev_bullish = ob_candle['close'] > ob_candle['open']
            is_curr_bearish = eng_candle['close'] < eng_candle['open']
            is_engulfing = eng_candle['close'] < ob_candle['open']

            if is_prev_bullish and is_curr_bearish and is_engulfing:
                return {
                    "ob_time": str(ob_candle['datetime']),
                    "ob_low": ob_candle['open'],   # 매도 오더블록 하단(양봉 시가)
                    "ob_high": ob_candle['high'],
                    "k": eng_k,
                    "d": eng_d
                }

    return None


def check_signals(symbol, df_high, df_low, tf_high, tf_low):
    if len(df_high) < 5 or len(df_low) < 5:
        return None

    history = load_history()
    current_high = df_high.iloc[-1]
    current_low = df_low.iloc[-1]
    current_price = current_low['close']

    high_k = current_high['stoch_rsi_k']
    high_d = current_high['stoch_rsi_d']

    now_kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    kst_str = now_kst.strftime('%Y-%m-%d %H:%M')

    # ====================================================
    # 🟢 1. LONG (매수) 신호 판별
    # ====================================================
    lock_key_long = f"LOCK_LONG_{symbol}_{tf_high}"
    is_locked_long = is_htf_zero_locked(df_high, "LONG", history, lock_key_long)

    # 상위 프레임이 20 이하이고, 0-0 잠금 상태가 아닐 때만 하위 오더블록 탐색
    if (high_k <= 20 and high_d <= 20) and not is_locked_long:
        ob_info = detect_order_block(df_low, "LONG")
        
        if ob_info is not None:
            history_key = f"{symbol}_{tf_high}_{tf_low}_LONG"
            # 동일한 오더블록 캔들에서 중복 알림이 나가지 않도록 차단
            if history.get(history_key) == ob_info["ob_time"]:
                return None

            history[history_key] = ob_info["ob_time"]
            save_history(history)

            return (
                f"🟢 [LONG - 매수 오더블록 발생] {symbol}\n"
                f"⏰ {kst_str} (KST)\n"
                f"📊 {tf_high} (20 이하 통과): K({high_k:.1f}) / D({high_d:.1f})\n"
                f"⚡ {tf_low} (30 이하 + OB): K({ob_info['k']:.1f}) / D({ob_info['d']:.1f})\n"
                f"🧱 매수 오더블록 구간: {ob_info['ob_low']} ~ {ob_info['ob_high']}\n"
                f"💵 실시간 현재가: {current_price}"
            )

    # ====================================================
    # 🔴 2. SHORT (매도) 신호 판별
    # ====================================================
    lock_key_short = f"LOCK_SHORT_{symbol}_{tf_high}"
    is_locked_short = is_htf_zero_locked(df_high, "SHORT", history, lock_key_short)

    # 상위 프레임이 80 이상이고, 100-100 잠금 상태가 아닐 때만 하위 오더블록 탐색
    if (high_k >= 80 and high_d >= 80) and not is_locked_short:
        ob_info = detect_order_block(df_low, "SHORT")
        
        if ob_info is not None:
            history_key = f"{symbol}_{tf_high}_{tf_low}_SHORT"
            if history.get(history_key) == ob_info["ob_time"]:
                return None

            history[history_key] = ob_info["ob_time"]
            save_history(history)

            return (
                f"🔴 [SHORT - 매도 오더블록 발생] {symbol}\n"
                f"⏰ {kst_str} (KST)\n"
                f"📊 {tf_high} (80 이상 통과): K({high_k:.1f}) / D({high_d:.1f})\n"
                f"⚡ {tf_low} (70 이상 + OB): K({ob_info['k']:.1f}) / D({ob_info['d']:.1f})\n"
                f"🧱 매도 오더블록 구간: {ob_info['ob_low']} ~ {ob_info['ob_high']}\n"
                f"💵 실시간 현재가: {current_price}"
            )

    return None