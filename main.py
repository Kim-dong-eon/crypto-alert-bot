import time
from config import settings
from src.fetcher import fetch_ohlcv
from src.indicators import add_indicators
from src.strategy import check_signals
from src.notifier import send_message

def main():
    print("🚀 다중 MTF 알림 봇 실행 시작...")
    
    alerts = [] # 여러 알림을 모아둘 빈 바구니 생성
    
    for symbol in settings.SYMBOLS:
        print(f"\n🔍 [{symbol}] 분석 중...")
        
        for tf_high, tf_low in settings.STRATEGY_PAIRS:
            df_high = fetch_ohlcv(symbol, tf_high, limit=100)
            df_low = fetch_ohlcv(symbol, tf_low, limit=100)
            
            if df_high is None or df_low is None:
                continue
                
            df_high = add_indicators(df_high)
            df_low = add_indicators(df_low)
            
            # strategy.py에서 반환된 알림 메시지 받기
            msg = check_signals(symbol, df_high, df_low, tf_high, tf_low)
            
            if msg:
                print(f"  -> 🎯 타점 발견! ({tf_high} & {tf_low})")
                alerts.append(msg) # 바로 안 보내고 바구니에 담기
            else:
                print(f"  -> ⏳ {tf_high} & {tf_low} 조건 미달")
            
            time.sleep(1)
            
    # 모든 코인 검사가 끝나고, 바구니에 알림이 1개라도 있다면 한 번에 묶어서 발송
    if alerts:
        final_message = "🚨 통합 포지션 알림 🚨\n\n" + "\n\n---\n\n".join(alerts)
        send_message(final_message)
        print("\n✅ 텔레그램 통합 발송 완료!")
    else:
        print("\n✅ 포착된 타점이 없습니다.")

if __name__ == "__main__":
    main()