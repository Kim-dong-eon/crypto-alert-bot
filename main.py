import time
import os
from config import settings
from src.fetcher import fetch_ohlcv
from src.indicators import add_indicators
from src.strategy import check_signals
from src.notifier import send_message

def main():
    print("🚀 다중 MTF 알림 봇 실행 시작 (실시간 1회 타격 모드)...")
    
    alerts = []
    
    for symbol in settings.SYMBOLS:
        print(f"\n🔍 [{symbol}] 분석 중...")
        
        for tf_high, tf_low in settings.STRATEGY_PAIRS:
            df_high = fetch_ohlcv(symbol, tf_high, limit=100)
            df_low = fetch_ohlcv(symbol, tf_low, limit=100)
            
            if df_high is None or df_low is None:
                continue
                
            df_high = add_indicators(df_high)
            df_low = add_indicators(df_low)
            
            msg = check_signals(symbol, df_high, df_low, tf_high, tf_low)
            
            if msg:
                print(f"  -> 🎯 실시간 타점 최초 포착! ({tf_high} & {tf_low})")
                alerts.append(msg)
            else:
                print(f"  -> ⏳ 조건 미달 또는 이미 알림 발송됨")
            
            time.sleep(1)
            
    if alerts:
        final_message = "🚨 통합 포지션 알림 🚨\n\n" + "\n\n---\n\n".join(alerts)
        send_message(final_message)
        print("\n✅ 텔레그램 통합 발송 완료!")
        
        # ★ 봇이 스스로 기억장(history.json)을 깃허브에 덮어쓰기 합니다.
        print("💾 알림 기록 중복 방지를 위해 깃허브에 자동 저장 중...")
        os.system('git config --global user.name "github-actions[bot]"')
        os.system('git config --global user.email "github-actions[bot]@users.noreply.github.com"')
        os.system('git add alert_history.json')
        os.system('git commit -m "auto: 알림 발송 기록 업데이트 (중복 방지)"')
        # 혹시 모를 충돌을 막기 위해 현재 가지(branch)에 밀어넣습니다.
        os.system('git push origin HEAD')
        print("✅ 알림 기록장 저장 성공!")
        
    else:
        print("\n✅ 새로 포착된 타점이 없습니다.")

if __name__ == "__main__":
    main()