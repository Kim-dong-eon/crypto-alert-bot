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

def check_signals(symbol: str, df_high: pd.DataFrame, df_low: pd.DataFrame) -> dict:
    """
    4시간/15분 스토캐스틱 RSI 수치를 비교하여 롱/숏 타점을 판별합니다.
    """
    if df_high is None or df_high.empty or df_low is None or df_low.empty:
        return {"signal": False, "message": ""}

    # 최신 캔들(현재 진행 중인 캔들) 데이터 추출
    latest_high = df_high.iloc[-1]
    latest_low = df_low.iloc[-1]

    # 4시간봉 스토캐스틱 K, D
    h_k, h_d = latest_high['stoch_rsi_k'], latest_high['stoch_rsi_d']
    # 15분봉 스토캐스틱 K, D
    l_k, l_d = latest_low['stoch_rsi_k'], latest_low['stoch_rsi_d']

    # --- [조건 1: 🟢 LONG (매수) 타점] ---
    # 4h, 15m 모두 K와 D가 30 이하인가?
    is_long = (h_k <= settings.OVERSOLD and h_d <= settings.OVERSOLD) and \
              (l_k <= settings.OVERSOLD and l_d <= settings.OVERSOLD)
    
    # 롱 타점일 때 오더블록 조건 추가 (현재는 is_order_block이 무조건 True라 영향 없음)
    if is_long:
        is_long = is_long and is_order_block(df_low, len(df_low)-1)

    # --- [조건 2: 🔴 SHORT (매도) 타점] ---
    # 4h, 15m 모두 K와 D가 70 이상인가?
    is_short = (h_k >= settings.OVERBOUGHT and h_d >= settings.OVERBOUGHT) and \
               (l_k >= settings.OVERBOUGHT and l_d >= settings.OVERBOUGHT)

    # 숏 타점일 때 오더블록 조건 추가
    if is_short:
        is_short = is_short and is_order_block(df_low, len(df_low)-1)

    # 최종 결과 세팅
    signal = False
    position = ""

    if is_long:
        signal = True
        position = "🟢 LONG (매수)"
    elif is_short:
        signal = True
        position = "🔴 SHORT (매도)"

    message = ""
    if signal:
        message = (
            f"🚨 *{position} 타점 포착!* ({symbol})\n\n"
            f"📊 *{settings.TIMEFRAME_HIGH} (상위)*\n"
            f"- Stoch RSI: K({h_k:.1f}) / D({h_d:.1f})\n\n"
            f"⚡ *{settings.TIMEFRAME_LOW} (하위)*\n"
            f"- Stoch RSI: K({l_k:.1f}) / D({l_d:.1f})\n"
            f"- 현재가: {latest_low['close']}"
        )
    
    return {"signal": signal, "message": message}