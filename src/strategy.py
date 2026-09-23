import pandas as pd
import sys
import os
import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

def is_order_block(df: pd.DataFrame, index: int) -> bool:
    """
    15분봉 오더블록(강한 매수세/매도세 기준 캔들)을 판별하는 함수.
    (나중에 구체적인 캔들 패턴 로직이 들어갈 자리입니다.)
    """
    # 현재는 로직을 비워두고 무조건 통과(True)하도록 임시 처리 (주석 역할)
    return True

def check_signals(symbol, df_high, df_low, tf_high, tf_low):
    if len(df_high) < 2 or len(df_low) < 2:
        return {"signal": False, "message": ""}
    
    # 1. 현재 생성 중인 최신 캔들(미완성)
    current_low_candle = df_low.iloc[-1]
    
    # 2. 현재 시간(KST)과 최신 캔들의 생성(오픈) 시간 비교
    now_kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    time_diff = now_kst - current_low_candle['datetime']
    
    # 3. 최신 캔들이 열린 지 6분(360초)이 지났다면, 이미 검사한 과거의 캔들이므로 패스 (중복 알림 방지)
    # (cron-job 서버 지연 시간을 고려해 5분이 아닌 넉넉히 6분으로 잡았습니다)
    if time_diff.total_seconds() > 420:
        return {"signal": False, "message": ""}

    # 4. 방금 막 마감된 직전 캔들 기준으로 지표 세팅
    last_high = df_high.iloc[-2]
    last_low = df_low.iloc[-2]
    current_price = current_low_candle['close']

    high_k = last_high['stoch_rsi_k']
    high_d = last_high['stoch_rsi_d']
    low_k = last_low['stoch_rsi_k']
    low_d = last_low['stoch_rsi_d']

    # 조건 판별 (상위/하위 모두 과매도 또는 과매수)
    is_long = (high_k <= 30 and high_d <= 30) and (low_k <= 30 and low_d <= 30)
    is_short = (high_k >= 70 and high_d >= 70) and (low_k >= 70 and low_d >= 70)

    result = {"signal": False, "message": ""}

    if is_long:
        result["signal"] = True
        result["message"] = (
            f"🚨 🟢 LONG (매수) 포착! ({symbol})\n\n"
            f"📊 {tf_high} (상위)\n"
            f"- Stoch RSI: K({high_k:.1f}) / D({high_d:.1f})\n\n"
            f"⚡ {tf_low} (하위)\n"
            f"- Stoch RSI: K({low_k:.1f}) / D({low_d:.1f})\n"
            f"- 진입가: {current_price}"
        )
    elif is_short:
        result["signal"] = True
        result["message"] = (
            f"🚨 🔴 SHORT (매도) 포착! ({symbol})\n\n"
            f"📊 {tf_high} (상위)\n"
            f"- Stoch RSI: K({high_k:.1f}) / D({high_d:.1f})\n\n"
            f"⚡ {tf_low} (하위)\n"
            f"- Stoch RSI: K({low_k:.1f}) / D({low_d:.1f})\n"
            f"- 진입가: {current_price}"
        )

    return result
