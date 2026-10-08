import pandas as pd
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
    if len(df_high) < 20 or len(df_low) < 10:
        return None

    history = load_history()
    current_high = df_high.iloc[-1]
    current_low = df_low.iloc[-1]
    high_candle_time = str(current_high['datetime'])
    low_candle_time = str(current_low['datetime'])

    high_k, high_d = current_high['stoch_rsi_k'], current_high['stoch_rsi_d']
    low_k, low_d = current_low['stoch_rsi_k'], current_low['stoch_rsi_d']

    coin_name = symbol.split('/')[0]
    tf_h_str = tf_high.upper()
    tf_l_str = tf_low.upper()

    # 🟢 1. LONG 판별
    lock_key_long = f"LOCK_LONG_{symbol}_{tf_high}"
    is_locked_long = is_htf_zero_locked(df_high, "LONG", history, lock_key_long)

    if (high_k <= 20 and high_d <= 20) and not is_locked_long and (low_k <= 20 and low_d <= 20):
        target_ch, touched_list = find_touched_or_closest_channel(df_high, df_low)

        if touched_list and target_ch is not None:
            r_hit = target_ch["touched_ratios"][0][0]
            candle_key = f"{symbol}_{tf_high}_{tf_low}_LONG_CANDLE"
            line_key = f"{symbol}_{tf_high}_{tf_low}_LONG_LINE"
            line_sig = f"{high_candle_time}_{target_ch['scale_type']}_{target_ch['mode']}_{r_hit}"

            # 같은 15분봉이거나, 이미 알림을 보낸 동일한 채널 라인이면 중복 발송 차단!
            if history.get(candle_key) == low_candle_time or history.get(line_key) == line_sig:
                return None

            history[candle_key] = low_candle_time
            history[line_key] = line_sig
            save_history(history)

            msg = (
                f"🟢 LONG | {coin_name} {tf_h_str} {target_ch['scale_kor']} [{r_hit:.1f}]\n"
                f"{tf_h_str} K/D : {high_k:.1f} / {high_d:.1f}\n"
                f"{tf_l_str} K/D : {low_k:.1f} / {low_d:.1f}"
            )
            images = generate_touched_pair_images(symbol, tf_high, tf_low, df_high, df_low, target_ch)
            return msg, images

    # 🔴 2. SHORT 판별
    lock_key_short = f"LOCK_SHORT_{symbol}_{tf_high}"
    is_locked_short = is_htf_zero_locked(df_high, "SHORT", history, lock_key_short)

    if (high_k >= 80 and high_d >= 80) and not is_locked_short and (low_k >= 80 and low_d >= 80):
        target_ch, touched_list = find_touched_or_closest_channel(df_high, df_low)

        if touched_list and target_ch is not None:
            r_hit = target_ch["touched_ratios"][0][0]
            candle_key = f"{symbol}_{tf_high}_{tf_low}_SHORT_CANDLE"
            line_key = f"{symbol}_{tf_high}_{tf_low}_SHORT_LINE"
            line_sig = f"{high_candle_time}_{target_ch['scale_type']}_{target_ch['mode']}_{r_hit}"

            if history.get(candle_key) == low_candle_time or history.get(line_key) == line_sig:
                return None

            history[candle_key] = low_candle_time
            history[line_key] = line_sig
            save_history(history)

            msg = (
                f"🔴 SHORT | {coin_name} {tf_h_str} {target_ch['scale_kor']} [{r_hit:.1f}]\n"
                f"{tf_h_str} K/D : {high_k:.1f} / {high_d:.1f}\n"
                f"{tf_l_str} K/D : {low_k:.1f} / {low_d:.1f}"
            )
            images = generate_touched_pair_images(symbol, tf_high, tf_low, df_high, df_low, target_ch)
            return msg, images

    return None