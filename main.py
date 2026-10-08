import os
import time
from concurrent.futures import ThreadPoolExecutor
from config import settings
from src.fetcher import fetch_ohlcv
from src.indicators import add_indicators
from src.strategy import check_signals
from src.notifier import send_message, send_photo


def fetch_and_prepare(args):
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

                send_message(msg)
                for img_path, caption in images:
                    send_photo(img_path, caption)
                    if os.path.exists(img_path):
                        try:
                            os.remove(img_path)
                        except OSError:
                            pass
            else:
                print(f"  -> ⏳ 조건 미달, 상위봉 잠금 또는 이미 발송됨 ({tf_high} & {tf_low})")

    elapsed = time.time() - start_time
    print(f"\n⚡ 전체 분석 완료! (소요 시간: {elapsed:.2f}초)")

    if alert_count > 0:
        print(f"✅ 총 {alert_count}건의 타점 텍스트 및 채널 이미지 발송 완료! (alert_history.json 갱신됨)")
    else:
        print("✅ 새로 포착된 타점이 없습니다.")


if __name__ == "__main__":
    main()