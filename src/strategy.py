import pandas as pd
import datetime

def is_order_block(df: pd.DataFrame, index: int) -> bool:
    return True

def check_signals(symbol, df_high, df_low, tf_high, tf_low):
    if len(df_high) < 2 or len(df_low) < 2:
        return None
    
    current_low_candle = df_low.iloc[-1]
    
    # 시간 오차(찐빠)를 막기 위해 KST 변환을 없애고 무조건 UTC 기준으로 일괄 비교
    now_utc = datetime.datetime.utcnow()
    candle_open_time = current_low_candle['datetime']
    
    if getattr(candle_open_time, 'tzinfo', None) is not None:
        now_utc = now_utc.replace(tzinfo=datetime.timezone.utc)
        
    time_diff = (now_utc - candle_open_time).total_seconds()
    
    # [핵심] 봇 실행 주기인 5분(300초)보다 짧게 270초(4분 30초)로 설정.
    # 이렇게 해야 다음 5분 뒤 실행 시 겹치지 않고 깔끔하게 한 번만 신호를 잡습니다.
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

    # main.py에서 하나로 합치기 쉽도록 문자열(알림 내용)만 바로 반환
    if is_long:
        return (
            f"🟢 [LONG] {symbol}\n"
            f"📊 {tf_high}: K({high_k:.1f}) / D({high_d:.1f})\n"
            f"⚡ {tf_low}: K({low_k:.1f}) / D({low_d:.1f})\n"
            f"💵 진입가: {current_price}"
        )
    elif is_short:
        return (
            f"🔴 [SHORT] {symbol}\n"
            f"📊 {tf_high}: K({high_k:.1f}) / D({high_d:.1f})\n"
            f"⚡ {tf_low}: K({low_k:.1f}) / D({low_d:.1f})\n"
            f"💵 진입가: {current_price}"
        )

    return None