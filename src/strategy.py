import pandas as pd
import datetime
import json
import os
from src.channel import find_touched_or_closest_channel, generate_touched_pair_images

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
    """모든 상위 프레임(1d, 4h, 1h)에 적용되는 0,0 (숏은 100,100) 잠금 및 리셋 함수"""
    curr = df_high.iloc[-1]
    curr_k, curr_d = curr['stoch_rsi_k'], curr['stoch_rsi_d']

    if direction == "LONG":
        if curr_k > 20 and curr_d > 20:
            if history.get(lock_key, False):
                history[lock_key] = False
                save_history(history)
            return False

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
        if curr_k < 80 and curr_d < 80:
            if history.get(lock_key, False):
                history[lock_key] = False
                save_history(history)
            return False

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


def check_signals(symbol, df_high, df_low, tf_high, tf_low):
    """
    1. 상위봉(1d, 4h, 1h) K,D <= 20 (또는 >= 80) & 0-0(100-100) 잠금 통과
    2. 하위봉(2h, 15m, 5m) K,D <= 20 (또는 >= 80)
    3. 하위봉 캔들이 상위봉 채널 라인(0.0 ~ 5.0)에 실제로 닿았을 때!
    """
    if len(df_high) < 20 or len(df_low) < 10:
        return None

    history = load_history()
    current_high = df_high.iloc[-1]
    current_low = df_low.iloc[-1]
    current_price = current_low['close']
    low_candle_time = str(current_low['datetime'])

    high_k, high_d = current_high['stoch_rsi_k'], current_high['stoch_rsi_d']
    low_k, low_d = current_low['stoch_rsi_k'], current_low['stoch_rsi_d']

    now_kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    kst_str = now_kst.strftime('%Y-%m-%d %H:%M')

    # 🟢 1. LONG 판별
    lock_key_long = f"LOCK_LONG_{symbol}_{tf_high}"
    is_locked_long = is_htf_zero_locked(df_high, "LONG", history, lock_key_long)

    if (high_k <= 20 and high_d <= 20) and not is_locked_long and (low_k <= 20 and low_d <= 20):
        target_ch, touched_list = find_touched_or_closest_channel(df_high, df_low)

        if touched_list and target_ch is not None:
            history_key = f"{symbol}_{tf_high}_{tf_low}_LONG"
            if history.get(history_key) == low_candle_time:
                return None

            history[history_key] = low_candle_time
            save_history(history)

            touch_details = []
            for ch in touched_list:
                lvls = ", ".join([f"[{r:.1f}] ({p:,.1f})" for r, p in ch["touched_ratios"]])
                touch_details.append(f"  • {ch['scale_kor']}({ch['mode_kor']}): {lvls}")
            touch_summary = "\n".join(touch_details)

            msg = (
                f"🟢 [LONG - {tf_low} 채널 터치 & 과매도(20 이하)] {symbol}\n"
                f"⏰ {kst_str} (KST)\n"
                f"📊 상위({tf_high}) 20 이하: K({high_k:.1f}) / D({high_d:.1f})\n"
                f"⚡ 하위({tf_low}) 20 이하: K({low_k:.1f}) / D({low_d:.1f})\n"
                f"📐 {tf_low} 봉이 터치한 {tf_high} 채널 라인:\n{touch_summary}\n"
                f"💵 실시간 현재가: {current_price:,.2f}"
            )
            images = generate_touched_pair_images(symbol, tf_high, tf_low, df_high, df_low, target_ch)
            return msg, images

    # 🔴 2. SHORT 판별
    lock_key_short = f"LOCK_SHORT_{symbol}_{tf_high}"
    is_locked_short = is_htf_zero_locked(df_high, "SHORT", history, lock_key_short)

    if (high_k >= 80 and high_d >= 80) and not is_locked_short and (low_k >= 80 and low_d >= 80):
        target_ch, touched_list = find_touched_or_closest_channel(df_high, df_low)

        if touched_list and target_ch is not None:
            history_key = f"{symbol}_{tf_high}_{tf_low}_SHORT"
            if history.get(history_key) == low_candle_time:
                return None

            history[history_key] = low_candle_time
            save_history(history)

            touch_details = []
            for ch in touched_list:
                lvls = ", ".join([f"[{r:.1f}] ({p:,.1f})" for r, p in ch["touched_ratios"]])
                touch_details.append(f"  • {ch['scale_kor']}({ch['mode_kor']}): {lvls}")
            touch_summary = "\n".join(touch_details)

            msg = (
                f"🔴 [SHORT - {tf_low} 채널 터치 & 과매수(80 이상)] {symbol}\n"
                f"⏰ {kst_str} (KST)\n"
                f"📊 상위({tf_high}) 80 이상: K({high_k:.1f}) / D({high_d:.1f})\n"
                f"⚡ 하위({tf_low}) 80 이상: K({low_k:.1f}) / D({low_d:.1f})\n"
                f"📐 {tf_low} 봉이 터치한 {tf_high} 채널 라인:\n{touch_summary}\n"
                f"💵 실시간 현재가: {current_price:,.2f}"
            )
            images = generate_touched_pair_images(symbol, tf_high, tf_low, df_high, df_low, target_ch)
            return msg, images

    return None