import pandas as pd
import sys
import os

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
    last_high = df_high.iloc[-1]
    last_low = df_low.iloc[-1]
    
    high_k = last_high['stoch_rsi_k']
    high_d = last_high['stoch_rsi_d']
    low_k = last_low['stoch_rsi_k']
    low_d = last_low['stoch_rsi_d']
    current_price = last_low['close']

    # 상위 프레임과 하위 프레임 모두 과매도/과매수 조건 만족 시
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
            f"- 현재가: {current_price}"
        )
    elif is_short:
        result["signal"] = True
        result["message"] = (
            f"🚨 🔴 SHORT (매도) 포착! ({symbol})\n\n"
            f"📊 {tf_high} (상위)\n"
            f"- Stoch RSI: K({high_k:.1f}) / D({high_d:.1f})\n\n"
            f"⚡ {tf_low} (하위)\n"
            f"- Stoch RSI: K({low_k:.1f}) / D({low_d:.1f})\n"
            f"- 현재가: {current_price}"
        )
    
    return result
