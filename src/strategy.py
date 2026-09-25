import pandas as pd
import datetime

def is_order_block(df: pd.DataFrame, index: int) -> bool:
    return True

def check_signals(symbol, df_high, df_low, tf_high, tf_low):
    if len(df_high) < 1 or len(df_low) < 1:
        return None
    
    # ★ 상위, 하위 모두 현재 실시간으로 진행 중인 최신 캔들(-1) 기준
    current_high = df_high.iloc[-1]
    current_low = df_low.iloc[-1]
    
    current_price = current_low['close']

    high_k = current_high['stoch_rsi_k']
    high_d = current_high['stoch_rsi_d']
    low_k = current_low['stoch_rsi_k']
    low_d = current_low['stoch_rsi_d']

    is_long = (high_k <= 20 and high_d <= 20) and (low_k <= 20 and low_d <= 20)
    is_short = (high_k >= 80 and high_d >= 80) and (low_k >= 80 and low_d >= 80)

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