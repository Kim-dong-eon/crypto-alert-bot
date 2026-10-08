import os
import time
from concurrent.futures import ThreadPoolExecutor
from config import settings
from src.fetcher import fetch_ohlcv
from src.indicators import add_indicators
from src.strategy import check_signals
from src.notifier import send_message, send_photo

def fetch_and_prepare(args):
    """단일 (코인, 타임프레임) 데이터를 가져와 지표를 계산하는 작업 함수"""
    symbol, tf = args
    time.sleep(0.15)
    df = fetch_ohlcv(symbol, tf, limit=120)
    if df is not None:
        df = add_indicators(df)
    return (symbol, tf), df

def main():
    start_time = time.time()
    print("🚀 고속 병렬 MTF 채널 & 스토캐스틱 알림 봇 실행 시작...")
    
    tasks = []
    seen = set()
    for symbol in settings.SYMBOLS:
        for tf_high, tf_low in settings.STRATEGY_PAIRS:
            for tf in (tf_high, tf_low):
                if (symbol, tf) not in seen:
                    seen.add((symbol, tf))
                    tasks.append((symbol, tf))
            
    data_map = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = executor.map(fetch_and_prepare, tasks)
        for key, df in results:
            data_map[key] = df

    alert_count = 0
    for symbol in settings.SYMBOLS:
        print(f"\n🔍 [{symbol}] 분석 결과:")
        for tf_high, tf_low in settings.STRATEGY_PAIRS:
            df_high = data_map.get((symbol, tf_high))
            df_low = data_map.get((symbol, tf_low))
            
            if df_high is None or df_low is None:
                continue
                
            result = check_signals(symbol, df_high, df_low, tf_high, tf_low)
            if result:
                msg, images = result
                alert_count += 1
                print(f"  -> 🎯 채널 터치 & 스토캐스틱 타점 포착! ({tf_high} & {tf_low})")
                
                # 타점 포착 즉시 텍스트와 해당 차트 2장(상위봉 + 하위봉) 발송
                send_message(msg)
                for img_path, caption in images:
                    send_photo(img_path, caption)
            else:
                print(f"  -> ⏳ 조건 미달, 0-0 잠금 또는 이미 발송됨 ({tf_high} & {tf_low})")
            
    elapsed = time.time() - start_time
    print(f"\n⚡ 전체 분석 완료! (소요 시간: {elapsed:.2f}초)")

    if alert_count > 0:
        print(f"✅ 총 {alert_count}건의 타점 텍스트 및 채널 이미지 발송 완료!")
    else:
        print("✅ 새로 포착된 타점이 없습니다.")

    # 기록 변경 시 깃허브 저장 및 3일 지난 auto 커밋 정리
    if os.path.exists("alert_history.json"):
        # ★ PNG 파일이 Git 작업을 방해하지 않도록 임시 이미지 정리
        for png_file in ["channel_htf.png", "channel_ltf.png"]:
            if os.path.exists(png_file):
                try:
                    os.remove(png_file)
                except:
                    pass

        os.system('git config --global user.name "github-actions[bot]"')
        os.system('git config --global user.email "github-actions[bot]@users.noreply.github.com"')
        os.system('git add alert_history.json')
        
        if os.system('git diff --staged --quiet') != 0:
            print("💾 기록 변경 감지! 깃허브 자동 저장 및 3일 지난 auto 커밋 정리 중...")
            os.system('git commit -m "auto: 알림 발송 기록 업데이트 (중복 방지)"')
            os.system('git pull --rebase origin HEAD')
            
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