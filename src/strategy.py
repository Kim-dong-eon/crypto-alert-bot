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
    모든 상위 프레임(1d, 4h, 1h)에 적용되는 0,0 (숏은 100,100) 잠금 및 리셋 함수
    - LONG: 0,0 도달 시 잠금 -> 0~20 구간 유지 중에는 계속 잠금 -> 20 초과(>20) 돌파 후 다시 20 이하로 떨어질 때 해제
    - SHORT: 100,100 도달 시 잠금 -> 80~100 구간 유지 중에는 계속 잠금 -> 80 미만(<80) 하락 후 다시 80 이상으로 올라올 때 해제
    """
    curr = df_high.iloc[-1]
    curr_k, curr_d = curr['stoch_rsi_k'], curr['stoch_rsi_d']

    if direction == "LONG":
        # 1) 현재 20 위로 올라가면 잠금 해제(Reset) -> 이후 다시 20 이하로 내려오면 정상 포착!
        if curr_k > 20 and curr_d > 20:
            if history.get(lock_key, False):
                history[lock_key] = False
                save_history(history)
            return False

        # 2) 현재 20 이하 구간일 때, 가장 최근 20 초과 시점 이후로 0,0을 찍은 적이 있는지 역추적
        touched_zero_in_cycle = False
        for i in range(len(df_high) - 1, -1, -1):
            row = df_high.iloc[i]
            k, d = row['stoch_rsi_k'], row['stoch_rsi_d']
            
            if k > 20 and d > 20:
                break
                
            if k <= 0.1 and d <= 0.1:
                touched_zero_in_cycle = True
                break

        if touched_zero_in_cycle or history.get(lock_key, False):
            if not history.get(lock_key, False):
                history[lock_key] = True
                save_history(history)
            return True

        return False

    elif direction == "SHORT":
        # 1) 현재 80 아래로 내려가면 잠금 해제(Reset)
        if curr_k < 80 and curr_d < 80:
            if history.get(lock_key, False):
                history[lock_key] = False
                save_history(history)
            return False

        # 2) 현재 80 이상 구간일 때, 가장 최근 80 미만 시점 이후로 100,100을 찍은 적이 있는지 역추적
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
    하위 프레임 스토캐스틱 30 이하(롱) / 70 이상(숏) 구역에서 오더블록 발생 여부 감지
    """
    if len(df_low) < 4:
        return None

    # (-2 캔들을 -1 실시간 캔들이 장악) 또는 (-3 캔들을 -2 마감 캔들이 장악)
    candidates = [(-2, -1), (-3, -2)]

    for ob_idx, eng_idx in candidates:
        ob_candle = df_low.iloc[ob_idx]
        eng_candle = df_low.iloc[eng_idx]

        ob_k, ob_d = ob_candle['stoch_rsi_k'], ob_candle['stoch_rsi_d']
        eng_k, eng_d = eng_candle['stoch_rsi_k'], eng_candle['stoch_rsi_d']

        if direction == "LONG":
            # 하위 프레임 30 이하 조건
            stoch_ok = (eng_k <= 30 and eng_d <= 30) or (ob_k <= 30 and ob_d <= 30 and eng_d <= 35)
            if not stoch_ok:
                continue

            # 매수 오더블록: 직전 음봉을 현재 양봉이 몸통(시가) 위로 돌파하며 장악
            is_prev_bearish = ob_candle['close'] < ob_candle['open']
            is_curr_bullish = eng_candle['close'] > eng_candle['open']
            is_engulfing = eng_candle['close'] > ob_candle['open']

            if is_prev_bearish and is_curr_bullish and is_engulfing:
                return {
                    "ob_time": str(ob_candle['datetime']),
                    "ob_low": ob_candle['low'],
                    "ob_high": ob_candle['open'],
                    "k": eng_k,
                    "d": eng_d
                }

        elif direction == "SHORT":
            # 하위 프레임 70 이상 조건
            stoch_ok = (eng_k >= 70 and eng_d >= 70) or (ob_k >= 70 and ob_d >= 70 and eng_d >= 65)
            if not stoch_ok:
                continue

            # 매도 오더블록: 직전 양봉을 현재 음봉이 몸통(시가) 아래로 돌파하며 장악
            is_prev_bullish = ob_candle['close'] > ob_candle['open']
            is_curr_bearish = eng_candle['close'] < eng_candle['open']
            is_engulfing = eng_candle['close'] < ob_candle['open']

            if is_prev_bullish and is_curr_bearish and is_engulfing:
                return {
                    "ob_time": str(ob_candle['datetime']),
                    "ob_low": ob_candle['open'],
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

    # 🟢 1. LONG 판별 (모든 상위 프레임 20 이하 + 0-0 잠금 아닐 때 + 하위 30 이하 매수 오더블록)
    lock_key_long = f"LOCK_LONG_{symbol}_{tf_high}"
    is_locked_long = is_htf_zero_locked(df_high, "LONG", history, lock_key_long)

    if (high_k <= 20 and high_d <= 20) and not is_locked_long:
        ob_info = detect_order_block(df_low, "LONG")
        
        if ob_info is not None:
            history_key = f"{symbol}_{tf_high}_{tf_low}_LONG"
            if history.get(history_key) == ob_info["ob_time"]:
                return None

            history[history_key] = ob_info["ob_time"]
            save_history(history)

            return (
                f"🟢 [LONG - 매수 오더블록] {symbol}\n"
                f"⏰ {kst_str} (KST)\n"
                f"📊 {tf_high} (20 이하): K({high_k:.1f}) / D({high_d:.1f})\n"
                f"⚡ {tf_low} (30 이하 + OB): K({ob_info['k']:.1f}) / D({ob_info['d']:.1f})\n"
                f"🧱 매수 오더블록 구간: {ob_info['ob_low']} ~ {ob_info['ob_high']}\n"
                f"💵 실시간 현재가: {current_price}"
            )

    # 🔴 2. SHORT 판별 (모든 상위 프레임 80 이상 + 100-100 잠금 아닐 때 + 하위 70 이상 매도 오더블록)
    lock_key_short = f"LOCK_SHORT_{symbol}_{tf_high}"
    is_locked_short = is_htf_zero_locked(df_high, "SHORT", history, lock_key_short)

    if (high_k >= 80 and high_d >= 80) and not is_locked_short:
        ob_info = detect_order_block(df_low, "SHORT")
        
        if ob_info is not None:
            history_key = f"{symbol}_{tf_high}_{tf_low}_SHORT"
            if history.get(history_key) == ob_info["ob_time"]:
                return None

            history[history_key] = ob_info["ob_time"]
            save_history(history)

            return (
                f"🔴 [SHORT - 매도 오더블록] {symbol}\n"
                f"⏰ {kst_str} (KST)\n"
                f"📊 {tf_high} (80 이상): K({high_k:.1f}) / D({high_d:.1f})\n"
                f"⚡ {tf_low} (70 이상 + OB): K({ob_info['k']:.1f}) / D({ob_info['d']:.1f})\n"
                f"🧱 매도 오더블록 구간: {ob_info['ob_low']} ~ {ob_info['ob_high']}\n"
                f"💵 실시간 현재가: {current_price}"
            )

    return None