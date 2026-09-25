import pandas as pd
import datetime
import json
import os

# 봇의 기억을 담당할 메모장 파일 이름
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
        json.dump(history, f)

def is_order_block(df: pd.DataFrame, index: int) -> bool:
    return True

def check_signals(symbol, df_high, df_low, tf_high, tf_low):
    if len(df_high) < 1 or len(df_low) < 1:
        return None
    
    # 상위, 하위 모두 현재 실시간으로 진행 중인 최신 캔들(-1) 기준
    current_high = df_high.iloc[-1]
    current_low = df_low.iloc[-1]
    
    high_k = current_high['stoch_rsi_k']
    high_d = current_high['stoch_rsi_d']
    low_k = current_low['stoch_rsi_k']
    low_d = current_low['stoch_rsi_d']

    is_long = (high_k <= 20 and high_d <= 20) and (low_k <= 20 and low_d <= 20)
    is_short = (high_k >= 80 and high_d >= 80) and (low_k >= 80 and low_d >= 80)

    # 조건에 맞지 않으면 조용히 패스
    if not is_long and not is_short:
        return None

    # ====================================================
    # 🧠 봇 기억력 시스템 (중복 알림 원천 차단)
    # ====================================================
    candle_time_str = str(current_low['datetime']) # 현재 캔들이 열린 시간 (예: 10:15)
    history_key = f"{symbol}_{tf_high}_{tf_low}"   # 예: BTC/USDT_4h_15m
    
    history = load_history()
    
    # 이미 이번 캔들(10:15)에서 알림을 보낸 기록이 있다면? -> 5분 뒤에 또 걸려도 무시!
    if history.get(history_key) == candle_time_str:
        return None
        
    # 처음 조건에 닿았다면, 현재 캔들 시간을 기억장에 저장!
    history[history_key] = candle_time_str
    save_history(history)
    # ====================================================

    current_price = current_low['close']
    now_kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    kst_str = now_kst.strftime('%Y-%m-%d %H:%M')

    if is_long:
        return (
            f"🟢 [LONG] {symbol}\n"
            f"⏰ {kst_str} (KST)\n"
            f"📊 {tf_high} (진행중): K({high_k:.1f}) / D({high_d:.1f})\n"
            f"⚡ {tf_low} (진행중): K({low_k:.1f}) / D({low_d:.1f})\n"
            f"💵 실시간 현재가: {current_price}"
        )
    elif is_short:
        return (
            f"🔴 [SHORT] {symbol}\n"
            f"⏰ {kst_str} (KST)\n"
            f"📊 {tf_high} (진행중): K({high_k:.1f}) / D({high_d:.1f})\n"
            f"⚡ {tf_low} (진행중): K({low_k:.1f}) / D({low_d:.1f})\n"
            f"💵 실시간 현재가: {current_price}"
        )

    return None