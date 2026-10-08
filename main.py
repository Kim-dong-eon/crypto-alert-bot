import time
import os
from config import settings
from src.fetcher import fetch_ohlcv
from src.indicators import add_indicators
from src.strategy import check_signals
from src.notifier import send_message

def main():
    print("🚀 다중 MTF 알림 봇 실행 시작 (4h 0-0 잠금 & 하위 30이하 오더블록 모드)...")
    
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
                print(f"  -> 🎯 오더블록 타점 최초 포착! ({tf_high} & {tf_low})")
                alerts.append(msg)
            else:
                print(f"  -> ⏳ 조건 미달, 0-0 잠금 상태 또는 이미 알림 발송됨")
            
            time.sleep(1)
            
    if alerts:
        final_message = "🚨 통합 포지션 알림 🚨\n\n" + "\n\n---\n\n".join(alerts)
        send_message(final_message)
        print("\n✅ 텔레그램 통합 발송 완료!")
    else:
        print("\n✅ 새로 포착된 타점이 없습니다.")

    # ★ 알림 발송 기록 또는 4h 0-0 잠금 상태(alert_history.json)가 바뀌었는지 확인 후 저장
    if os.path.exists("alert_history.json"):
        os.system('git config --global user.name "github-actions[bot]"')
        os.system('git config --global user.email "github-actions[bot]@users.noreply.github.com"')
        os.system('git add alert_history.json')
        
        # alert_history.json 파일 내용이 실제로 변경되었을 때만 커밋 & 3일 지난 기록 삭제 실행
        if os.system('git diff --staged --quiet') != 0:
            print("💾 기록 변경 감지! 깃허브 자동 저장 및 3일 지난 auto 커밋 정리 중...")
            os.system('git commit -m "auto: 알림 발송 기록 업데이트 (중복 방지)"')
            os.system('git pull --rebase origin HEAD')
            
            # 최근 3일(259,200초)이 지난 'auto:' 커밋만 골라서 자동 삭제 (feat 등 일반 커밋은 보존)
            os.system(
                "git filter-branch -f --commit-filter '"
                "case $(git log -1 --format=%s $GIT_COMMIT) in "
                "auto:*) if [ $(git log -1 --format=%ct $GIT_COMMIT) -lt $(( $(date +%s) - 259200 )) ]; "
                "then skip_commit \"$@\"; else git commit-tree \"$@\"; fi ;; "
                "*) git commit-tree \"$@\" ;; esac' HEAD"
            )
            
            os.system('git push origin HEAD --force')
            print("✅ 알림 기록장 저장 및 오래된 커밋 청소 성공!")

if __name__ == "__main__":
    main()