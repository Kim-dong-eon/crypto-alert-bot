import pandas as pd
import datetime

def is_order_block(df: pd.DataFrame, index: int) -> bool:
    return True

def check_signals(symbol, df_high, df_low, tf_high, tf_low):
    if len(df_high) < 2 or len(df_low) < 2:
        return None
    
    current_low_candle = df_low.iloc[-1]
    
    # 봇 내부 계산용: UTC 시간 유지 (찐빠 방지용)
    now_utc = datetime.datetime.utcnow()
    candle_open_time = current_low_candle['datetime']
    
    if getattr(candle_open_time, 'tzinfo', None) is not None:
        now_utc = now_utc.replace(tzinfo=datetime.timezone.utc)
        
    time_diff = (now_utc - candle_open_time).total_seconds()
    
    if time_diff > 270 or time_diff < 0:
        return None

    last_high = df_high.iloc[-2]
    last_low = df_low.iloc[-2]
    current_price = current_low_candle['close']

    high_k = last_high['stoch_rsi_k']
    high_d = last_high['stoch_rsi_d']
    low_k = last_low['stoch_rsi_k']
    low_d = last_low['stoch_rsi_d']

    is_long = (high_k <= 30 and high_d <= 30) and (low_k <= 30 and low_d <= 30)
    is_short = (high_k >= 70 and high_d >= 70) and (low_k >= 70 and low_d >= 70)

    # 텔레그램 발송용: 동언 님이 보기 편한 한국 시간(KST)으로 변환
    now_kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    kst_str = now_kst.strftime('%Y-%m-%d %H:%M')

    if is_long:
        return (
            f"🟢 [LONG] {symbol}\n"
            f"⏰ {kst_str} (KST)\n"
            f"📊 {tf_high}: K({high_k:.1f}) / D({high_d:.1f})\n"
            f"⚡ {tf_low}: K({low_k:.1f}) / D({low_d:.1f})\n"
            f"💵 진입가: {current_price}"
        )
    elif is_short:
        return (
            f"🔴 [SHORT] {symbol}\n"
            f"⏰ {kst_str} (KST)\n"
            f"📊 {tf_high}: K({high_k:.1f}) / D({high_d:.1f})\n"
            f"⚡ {tf_low}: K({low_k:.1f}) / D({low_d:.1f})\n"
            f"💵 진입가: {current_price}"
        )

    return None