import time
from config import settings
from src.fetcher import fetch_ohlcv
from src.indicators import add_indicators
from src.strategy import check_signals
from src.notifier import send_message

def main():
    print("🚀 가상화폐 MTF 알림 봇 실행 시작...")
    
    # 4개의 코인을 순서대로 반복하면서 검사
    for symbol in settings.SYMBOLS:
        print(f"\n🔍 [{symbol}] 데이터 분석 중...")
        
        # 1. 캔들 데이터 수집 (4시간봉, 15분봉)
        df_high = fetch_ohlcv(symbol, settings.TIMEFRAME_HIGH, limit=1000)
        df_low = fetch_ohlcv(symbol, settings.TIMEFRAME_LOW, limit=1000)
        
        if df_high is None or df_low is None:
            print(f"  -> ⚠️ {symbol} 데이터 로드 실패. 다음 코인으로 넘어갑니다.")
            continue
            
        # 2. 보조지표(RSI, 스토캐스틱 RSI) 계산
        df_high = add_indicators(df_high)
        df_low = add_indicators(df_low)
        
        # 3. 전략 조건 판별
        result = check_signals(symbol, df_high, df_low)
        
        # 4. 결과에 따른 텔레그램 알림 발송
        if result["signal"]:
            print(f"  -> 🎯 타점 발견! 텔레그램 알림 발송")
            send_message(result["message"])
        else:
            print(f"  -> ⏳ 조건 미달 (대기)")
            
        # 거래소 API 호출 제한(Rate Limit) 방지를 위해 1초 대기
        time.sleep(1)
        
    print("\n✅ 모든 코인 분석 완료!")

if __name__ == "__main__":
    main()