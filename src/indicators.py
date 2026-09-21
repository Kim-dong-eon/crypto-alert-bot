import pandas as pd
import pandas_ta as ta
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings
from src.fetcher import fetch_ohlcv

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """캔들 데이터프레임에 RSI와 Stoch RSI 지표를 추가합니다."""
    if df is None or df.empty:
        return df

    # 1. 일반 RSI 계산
    df['rsi'] = ta.rsi(df['close'], length=settings.RSI_PERIOD)

    # 2. 스토캐스틱 RSI 계산 (TradingView 방식)
    # Stoch RSI는 내부적으로 RSI(14)를 구한 뒤, 그 값으로 Stoch(14,3,3)을 계산함
    stochrsi = ta.stochrsi(
        df['close'],
        length=settings.STOCH_LENGTH,    # 스토캐스틱 길이 (14)
        rsi_length=settings.RSI_PERIOD,  # RSI 길이 (14)
        k=settings.STOCH_RSI_K,          # %K (3)
        d=settings.STOCH_RSI_D           # %D (3)
    )

    # 생성된 컬럼명 동적 매핑 (보통 STOCHRSIk_14_14_3_3, STOCHRSId_14_14_3_3 형태)
    if stochrsi is not None and not stochrsi.empty:
        k_col = [c for c in stochrsi.columns if 'k' in c.lower()][0]
        d_col = [c for c in stochrsi.columns if 'd' in c.lower()][0]

        df['stoch_rsi_k'] = stochrsi[k_col]
        df['stoch_rsi_d'] = stochrsi[d_col]

    return df

if __name__ == "__main__":
    print("📈 스토캐스틱 RSI 지표 계산 테스트 중...\n")
    # 트레이딩뷰와 일치시키기 위해 캔들 1000개 로드
    df_test = fetch_ohlcv(settings.SYMBOL, settings.TIMEFRAME_HIGH, limit=500)
    
    if df_test is not None:
        df_calculated = add_indicators(df_test)
        # 최신 5개 캔들의 지표 값 확인
        recent = df_calculated[['datetime', 'close', 'rsi', 'stoch_rsi_k', 'stoch_rsi_d']].tail(5)
        print("✅ 최신 지표 계산 결과:")
        print(recent)