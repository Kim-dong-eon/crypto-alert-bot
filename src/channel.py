import matplotlib
matplotlib.use('Agg')

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import platform

if platform.system() == 'Windows':
    plt.rcParams['font.family'] = 'Malgun Gothic'
else:
    plt.rcParams['font.family'] = 'NanumGothic'
plt.rcParams['axes.unicode_minus'] = False

HTF_IMAGE_PATH = "channel_htf.png"
LTF_IMAGE_PATH = "channel_ltf.png"

SCALE_KOR_NAMES = {
    "SHORT": "단기",
    "MEDIUM": "중기",
    "LONG": "장기"
}

SCALE_WEIGHTS = {
    "LONG": 1.25,
    "MEDIUM": 1.00,
    "SHORT": 0.85
}

BASE_RATIO_WEIGHTS = {
    -1.5: 0.65,
    -1.0: 0.95,
    -0.5: 0.80,
     0.0: 1.00,
     0.5: 0.85,
     1.0: 1.00,
     1.5: 0.85,
     2.0: 0.80,
     2.5: 0.65,
     3.0: 0.35,
     3.5: 0.30,
     4.0: 0.25,
     4.5: 0.20,
     5.0: 0.15
}

REQUIRED_LTF_BARS = {
    3.0: 5,
    3.5: 10,
    4.0: 15,
    4.5: 20,
    5.0: 25
}

UNLOCKED_RATIO_WEIGHTS = {
    3.0: 0.60,
    3.5: 0.58,
    4.0: 0.55,
    4.5: 0.52,
    5.0: 0.50
}

MIN_ALERT_RATIO_WEIGHT = 0.50


def count_ltf_bars_beyond_2_5(df_low: pd.DataFrame, t1: float, t2: float, c1: float, slope_ms: float, channel_height: float, mode: str) -> int:
    post_p2_df = df_low[df_low['timestamp'] >= t2]
    if post_p2_df.empty:
        return 0

    first_break_pos = None
    for pos, (_, row) in enumerate(post_p2_df.iterrows()):
        row_t = float(row['timestamp'])
        line0_t = c1 + slope_ms * (row_t - t1)

        if mode == "HIGH":
            lvl_2_5 = line0_t - (2.5 * channel_height)
            if float(row['low']) <= lvl_2_5:
                first_break_pos = pos
                break
        else:
            lvl_2_5 = line0_t + (2.5 * channel_height)
            if float(row['high']) >= lvl_2_5:
                first_break_pos = pos
                break

    if first_break_pos is None:
        return 0

    return len(post_p2_df) - first_break_pos


def get_dynamic_ratio_weight(r: float, ltf_bars_count: int) -> float:
    r_key = round(r, 1)
    base_w = BASE_RATIO_WEIGHTS.get(r_key, 0.1)

    if r_key >= 3.0:
        req_bars = REQUIRED_LTF_BARS.get(r_key, 999)
        if ltf_bars_count >= req_bars:
            return UNLOCKED_RATIO_WEIGHTS.get(r_key, 0.50)
        return base_w

    return base_w


def find_swing_high_closes(df: pd.DataFrame, left: int = 2, right: int = 2):
    closes = df['close'].values
    peaks = []
    for i in range(left, len(df) - max(right, 2)):
        window = closes[i - left : i + right + 1]
        if closes[i] == np.max(window):
            if not peaks or (i - peaks[-1] >= left):
                peaks.append(i)
            elif closes[i] > closes[peaks[-1]]:
                peaks[-1] = i
    return peaks


def find_swing_low_closes(df: pd.DataFrame, left: int = 2, right: int = 2):
    closes = df['close'].values
    valleys = []
    for i in range(left, len(df) - max(right, 2)):
        window = closes[i - left : i + right + 1]
        if closes[i] == np.min(window):
            if not valleys or (i - valleys[-1] >= left):
                valleys.append(i)
            elif closes[i] < closes[valleys[-1]]:
                valleys[-1] = i
    return valleys


def is_valid_roof_line(df: pd.DataFrame, p1_idx: int, p2_idx: int, tolerance: float = 0.0005) -> bool:
    closes = df['close'].values
    p1_c, p2_c = closes[p1_idx], closes[p2_idx]
    slope = (p2_c - p1_c) / (p2_idx - p1_idx)
    for idx in range(p1_idx + 1, p2_idx):
        line_val = p1_c + slope * (idx - p1_idx)
        if closes[idx] > line_val * (1 + tolerance):
            return False
    return True


def is_valid_floor_line(df: pd.DataFrame, v1_idx: int, v2_idx: int, tolerance: float = 0.0005) -> bool:
    closes = df['close'].values
    v1_c, v2_c = closes[v1_idx], closes[v2_idx]
    slope = (v2_c - v1_c) / (v2_idx - v1_idx)
    for idx in range(v1_idx + 1, v2_idx):
        line_val = v1_c + slope * (idx - v1_idx)
        if closes[idx] < line_val * (1 - tolerance):
            return False
    return True


def find_pre_low_close(df: pd.DataFrame, peak1_idx: int, wall_peaks: list):
    closes = df['close'].values
    p1_close = closes[peak1_idx]

    valid_walls = [
        p for p in wall_peaks
        if p < peak1_idx - 2 and (closes[p] >= p1_close * 0.998 or p <= peak1_idx - 10)
    ]

    if valid_walls:
        start_idx = max(valid_walls[-1] + 1, peak1_idx - 16)
    else:
        start_idx = max(0, peak1_idx - 12)

    valley_candidates = []
    for i in range(start_idx + 1, peak1_idx):
        if closes[i] <= closes[i - 1] and closes[i] <= closes[i + 1]:
            valley_candidates.append(i)

    if valley_candidates:
        return int(min(valley_candidates, key=lambda idx: closes[idx]))

    sub_closes = df['close'].iloc[start_idx:peak1_idx]
    return int(sub_closes.idxmin())


def find_pre_high_close(df: pd.DataFrame, valley1_idx: int, wall_valleys: list):
    closes = df['close'].values
    v1_close = closes[valley1_idx]

    valid_walls = [
        v for v in wall_valleys
        if v < valley1_idx - 2 and (closes[v] <= v1_close * 1.002 or v <= valley1_idx - 10)
    ]

    if valid_walls:
        start_idx = max(valid_walls[-1] + 1, valley1_idx - 16)
    else:
        start_idx = max(0, valley1_idx - 12)

    peak_candidates = []
    for i in range(start_idx + 1, valley1_idx):
        if closes[i] >= closes[i - 1] and closes[i] >= closes[i + 1]:
            peak_candidates.append(i)

    if peak_candidates:
        return int(max(peak_candidates, key=lambda idx: closes[idx]))

    sub_closes = df['close'].iloc[start_idx:valley1_idx]
    return int(sub_closes.idxmax())


def is_channel_height_valid(df_high: pd.DataFrame, idx1: int, third_idx: int, channel_height: float) -> bool:
    """
    ★ 사진처럼 채널 폭(channel_height)이 극단적으로 좁아져 선들이 다닥다닥 뭉치는 현상을 차단합니다.
    - 조건 1: 상위봉 최근 구간 전체 고저 변동폭의 최소 8% 이상이어야 함
    - 조건 2: 상위봉 평균 캔들 길이(high - low)의 최소 1.8배 이상이어야 함
    """
    start_lookback = max(0, min(idx1, third_idx) - 5)
    sub = df_high.iloc[start_lookback:]
    total_range = float(sub['high'].max() - sub['low'].min())
    avg_candle_size = float((sub['high'] - sub['low']).mean())

    min_required_height = max(total_range * 0.08, avg_candle_size * 1.8)
    return channel_height >= min_required_height


def build_channel_with_ltf(df_high: pd.DataFrame, df_low: pd.DataFrame, idx1: int, idx2: int, wall_points: list, mode: str, scale_type: str):
    if mode == "HIGH":
        third_idx = find_pre_low_close(df_high, idx1, wall_points)
    else:
        third_idx = find_pre_high_close(df_high, idx1, wall_points)

    c1 = float(df_high.loc[idx1, 'close'])
    c2 = float(df_high.loc[idx2, 'close'])
    c3 = float(df_high.loc[third_idx, 'close'])

    t1 = float(df_high.loc[idx1, 'timestamp'])
    t2 = float(df_high.loc[idx2, 'timestamp'])
    t3 = float(df_high.loc[third_idx, 'timestamp'])

    slope_idx = (c2 - c1) / (idx2 - idx1)
    slope_ms = (c2 - c1) / (t2 - t1)

    line0_at_third = c1 + slope_idx * (third_idx - idx1)
    if mode == "HIGH":
        channel_height = line0_at_third - c3
    else:
        channel_height = c3 - line0_at_third

    # ★ 찌그러진 좁은 채널(국수가락 채널)이면 즉시 무효(None) 반환!
    if not is_channel_height_valid(df_high, idx1, third_idx, channel_height):
        return None

    ratios = [round(r * 0.5, 1) for r in range(-3, 11)]

    ltf_curr = df_low.iloc[-1]
    ltf_t = float(ltf_curr['timestamp'])
    ltf_close = float(ltf_curr['close'])
    ltf_low = float(ltf_curr['low'])
    ltf_high = float(ltf_curr['high'])

    # ★ 하위봉 캔들 1개 길이가 채널 1칸(1.0) 폭보다 크면(한 캔들에 선이 3개 이상 뭉치면) 제외
    if (ltf_high - ltf_low) > channel_height * 0.95:
        return None

    line0_at_ltf = c1 + slope_ms * (ltf_t - t1)
    if mode == "HIGH":
        ltf_levels = {r: line0_at_ltf - (r * channel_height) for r in ratios}
    else:
        ltf_levels = {r: line0_at_ltf + (r * channel_height) for r in ratios}

    ltf_bars_count = count_ltf_bars_beyond_2_5(
        df_low, t1, t2, c1, slope_ms, channel_height, mode
    )

    ratio_weights = {
        r: get_dynamic_ratio_weight(r, ltf_bars_count)
        for r in ratios
    }

    touched_ratios = []
    min_dist = float('inf')
    closest_ratio = 0.0

    for r, lvl_price in ltf_levels.items():
        r_weight = ratio_weights[r]
        dist = abs(ltf_close - lvl_price)

        if r_weight >= MIN_ALERT_RATIO_WEIGHT and dist < min_dist:
            min_dist = dist
            closest_ratio = r

        if r_weight >= MIN_ALERT_RATIO_WEIGHT and (ltf_low <= lvl_price <= ltf_high):
            touched_ratios.append((r, lvl_price, r_weight))

    touched_ratios.sort(key=lambda x: (-x[2], abs(ltf_close - x[1])))
    # 가장 우선순위가 높은 대표 터치 라인 1개만 남겨 초록색 중복 도배 방지
    clean_touched = [(touched_ratios[0][0], touched_ratios[0][1])] if touched_ratios else []

    scale_weight = SCALE_WEIGHTS.get(scale_type, 1.0)
    best_r_weight = touched_ratios[0][2] if touched_ratios else ratio_weights.get(closest_ratio, 0.1)
    priority_score = scale_weight * best_r_weight

    mode_kor = "고점기준" if mode == "HIGH" else "저점기준"
    scale_kor = SCALE_KOR_NAMES[scale_type]

    return {
        "scale_type": scale_type,
        "scale_kor": scale_kor,
        "mode": mode,
        "mode_kor": mode_kor,
        "title": f"{scale_kor} ({mode_kor})",
        "p1_idx": idx1, "p1_time": df_high.loc[idx1, 'datetime'], "p1_price": c1, "t1": t1,
        "p2_idx": idx2, "p2_time": df_high.loc[idx2, 'datetime'], "p2_price": c2, "t2": t2,
        "third_idx": third_idx, "third_time": df_high.loc[third_idx, 'datetime'], "third_price": c3, "t3": t3,
        "slope_idx": slope_idx,
        "slope_ms": slope_ms,
        "height": channel_height,
        "ratios": ratios,
        "ratio_weights": ratio_weights,
        "ltf_bars_beyond_2_5": ltf_bars_count,
        "ltf_levels": ltf_levels,
        "touched_ratios": clean_touched,
        "closest_ratio": closest_ratio,
        "min_dist": min_dist,
        "priority_score": priority_score
    }


def extract_channels_by_mode(df_high: pd.DataFrame, df_low: pd.DataFrame, mode: str = "HIGH") -> list:
    if mode == "HIGH":
        short_pts = find_swing_high_closes(df_high, left=2, right=2)
        wall_pts = find_swing_high_closes(df_high, left=3, right=3)
        med_pts = find_swing_high_closes(df_high, left=4, right=4)
        long_pts = find_swing_high_closes(df_high, left=6, right=6)
        validator = is_valid_roof_line
    else:
        short_pts = find_swing_low_closes(df_high, left=2, right=2)
        wall_pts = find_swing_low_closes(df_high, left=3, right=3)
        med_pts = find_swing_low_closes(df_high, left=4, right=4)
        long_pts = find_swing_low_closes(df_high, left=6, right=6)
        validator = is_valid_floor_line

    channels = []

    if len(short_pts) >= 2:
        for i in range(len(short_pts) - 1, 0, -1):
            for j in range(i - 1, -1, -1):
                pt2, pt1 = short_pts[i], short_pts[j]
                if 4 <= (pt2 - pt1) <= 12 and validator(df_high, pt1, pt2):
                    ch = build_channel_with_ltf(df_high, df_low, pt1, pt2, wall_pts, mode, "SHORT")
                    if ch is not None:
                        channels.append(ch)
                        break
            if any(c["scale_type"] == "SHORT" for c in channels):
                break

    if len(med_pts) >= 2:
        for i in range(len(med_pts) - 1, 0, -1):
            for j in range(i - 1, -1, -1):
                pt2, pt1 = med_pts[i], med_pts[j]
                if 13 <= (pt2 - pt1) <= 30 and validator(df_high, pt1, pt2):
                    ch = build_channel_with_ltf(df_high, df_low, pt1, pt2, wall_pts, mode, "MEDIUM")
                    if ch is not None:
                        channels.append(ch)
                        break
            if any(c["scale_type"] == "MEDIUM" for c in channels):
                break

    if len(long_pts) >= 2:
        for i in range(len(long_pts) - 1, 0, -1):
            pt2, pt1 = long_pts[i], long_pts[i - 1]
            if (pt2 - pt1) >= 31 and validator(df_high, pt1, pt2):
                ch = build_channel_with_ltf(df_high, df_low, pt1, pt2, wall_pts, mode, "LONG")
                if ch is not None:
                    channels.append(ch)
                    break
        if not any(c["scale_type"] == "LONG" for c in channels):
            for i in range(len(long_pts) - 1, 0, -1):
                for j in range(i - 1, -1, -1):
                    pt2, pt1 = long_pts[i], long_pts[j]
                    if (pt2 - pt1) >= 31 and validator(df_high, pt1, pt2):
                        ch = build_channel_with_ltf(df_high, df_low, pt1, pt2, wall_pts, mode, "LONG")
                        if ch is not None:
                            channels.append(ch)
                            break
                if any(c["scale_type"] == "LONG" for c in channels):
                    break

    return channels


def find_touched_or_closest_channel(df_high: pd.DataFrame, df_low: pd.DataFrame):
    df_high = df_high.reset_index(drop=True)
    df_low = df_low.reset_index(drop=True)

    all_channels = extract_channels_by_mode(df_high, df_low, "HIGH") + extract_channels_by_mode(df_high, df_low, "LOW")
    touched_list = [ch for ch in all_channels if ch["touched_ratios"]]

    if touched_list:
        best_target = min(touched_list, key=lambda x: (-x["priority_score"], x["min_dist"]))
    elif all_channels:
        best_target = min(all_channels, key=lambda x: x["min_dist"])
    else:
        best_target = None

    return best_target, touched_list


def _get_line_style(r: float, r_weight: float, touched_r_set: set, target_r: float = None, is_ltf: bool = False):
    if r in touched_r_set:
        return '#00e676', (2.4 if is_ltf else 2.1), 1.0
    if is_ltf and target_r is not None and r == target_r:
        return '#00bcd4', 1.8, 0.95
    if r_weight < MIN_ALERT_RATIO_WEIGHT:
        return '#787b86', 0.8, 0.35
    if r < 0:
        return ('#ba68c8' if r.is_integer() else '#ce93d8'), (1.3 if r.is_integer() else 1.0), 0.80
    return ('#ffffff' if r.is_integer() else '#f5d142'), (1.3 if r.is_integer() else 1.0), (0.85 if r.is_integer() else 0.75)


def plot_htf_channel(symbol: str, tf_high: str, df_high: pd.DataFrame, ch: dict) -> str:
    df = df_high.reset_index(drop=True)
    last_idx = len(df) - 1
    scale_type = ch["scale_type"]
    coin_name = symbol.split('/')[0]

    first_anchor = min(ch['p1_idx'], ch['third_idx'])
    if scale_type == "SHORT":
        x_start = max(0, first_anchor - 5)
    elif scale_type == "MEDIUM":
        x_start = max(0, first_anchor - 7)
    else:
        x_start = max(0, first_anchor - 9)

    sub_df = df.iloc[x_start:]
    visible_count = len(sub_df)

    empty_right_bars = max(8, int(visible_count * 0.32))
    x_right_edge = last_idx + empty_right_bars

    y_min = sub_df['low'].min() * 0.992
    y_max = sub_df['high'].max() * 1.006

    fig, ax = plt.subplots(1, 1, figsize=(14, 7), facecolor='#131722')
    fig.subplots_adjust(left=0.07, right=0.87, top=0.91, bottom=0.10)
    ax.set_facecolor('#131722')

    ax.set_title(
        f"{coin_name} {tf_high.upper()} {ch['title']}",
        color='white', fontsize=14, pad=12, fontweight='bold'
    )

    candle_width = 0.62
    for idx, row in sub_df.iterrows():
        is_bull = row['close'] >= row['open']
        color = '#089981' if is_bull else '#f23645'
        ax.plot([idx, idx], [row['low'], row['high']], color=color, linewidth=1.1, zorder=2)
        body_low = min(row['open'], row['close'])
        body_height = max(abs(row['close'] - row['open']), 1e-4)
        rect = patches.Rectangle((idx - candle_width / 2, body_low), candle_width, body_height,
                                 facecolor=color, edgecolor=color, zorder=3)
        ax.add_patch(rect)

    curr_close = float(df.iloc[-1]['close'])
    ax.axvline(last_idx, color='#363a45', linestyle='--', linewidth=0.8, alpha=0.7, zorder=1)
    ax.axhline(curr_close, color='#00bcd4', linestyle=':', linewidth=1.0, alpha=0.8, zorder=3)

    line_start_x = max(x_start, first_anchor - 2)
    x_vals = np.array([line_start_x, x_right_edge])
    line0_y = ch['p1_price'] + ch['slope_idx'] * (x_vals - ch['p1_idx'])

    touched_r_set = {r for r, _ in ch['touched_ratios']}
    ratio_weights = ch.get('ratio_weights', {})

    for r in ch['ratios']:
        if ch['mode'] == "HIGH":
            y_vals = line0_y - (r * ch['height'])
        else:
            y_vals = line0_y + (r * ch['height'])

        r_w = ratio_weights.get(r, 0.5)
        line_color, lw, alpha = _get_line_style(r, r_w, touched_r_set, is_ltf=False)
        ax.plot(x_vals, y_vals, color=line_color, alpha=alpha, linewidth=lw, zorder=4, clip_on=True)

        end_y = y_vals[1]
        lvl_price = ch['ltf_levels'][r]
        if y_min <= end_y <= y_max:
            tag = " ◀ TOUCH" if r in touched_r_set else ""
            ax.annotate(
                f"[{r:.1f}] {lvl_price:,.1f}{tag}",
                xy=(1.006, end_y), xycoords=('axes fraction', 'data'),
                color=line_color, fontsize=9, va='center', fontweight='bold', clip_on=False
            )

    anchor_x = [ch['p1_idx'], ch['p2_idx'], ch['third_idx']]
    anchor_y = [ch['p1_price'], ch['p2_price'], ch['third_price']]
    ax.scatter(anchor_x, anchor_y, s=110, facecolor='#1e53e5', edgecolor='#64b5f6', linewidth=2, zorder=6)

    if ch['mode'] == "HIGH":
        lbl1, lbl2, lbl3 = "고점1", "고점2", "직전저점"
        offset1, offset2, offset3 = (0, 12), (0, 12), (0, -22)
    else:
        lbl1, lbl2, lbl3 = "저점1", "저점2", "직전고점"
        offset1, offset2, offset3 = (0, -22), (0, -22), (0, 12)

    ax.annotate(lbl1, (ch['p1_idx'], ch['p1_price']), textcoords="offset points", xytext=offset1, ha='center', color='#64b5f6', fontsize=9, fontweight='bold')
    ax.annotate(lbl2, (ch['p2_idx'], ch['p2_price']), textcoords="offset points", xytext=offset2, ha='center', color='#64b5f6', fontsize=9, fontweight='bold')
    ax.annotate(lbl3, (ch['third_idx'], ch['third_price']), textcoords="offset points", xytext=offset3, ha='center', color='#64b5f6', fontsize=9, fontweight='bold')

    step = max(1, visible_count // 6)
    tick_indices = list(range(x_start, len(df), step))
    ax.set_xticks(tick_indices)
    ax.set_xticklabels([df.loc[i, 'datetime'].strftime('%m-%d %H:%M') for i in tick_indices], color='#b2b5be', fontsize=8.5)
    ax.tick_params(axis='y', colors='#b2b5be')
    ax.grid(color='#2a2e39', linestyle='--', linewidth=0.5, alpha=0.5)

    ax.set_ylim(y_min, y_max)
    ax.set_xlim(x_start - 1.5, x_right_edge)

    plt.savefig(HTF_IMAGE_PATH, dpi=140, facecolor='#131722')
    plt.close(fig)
    return HTF_IMAGE_PATH


def plot_ltf_channel_touch(symbol: str, tf_high: str, tf_low: str, df_low: pd.DataFrame, ch: dict) -> str:
    sub_df = df_low.tail(55).reset_index(drop=True)
    visible_count = len(sub_df)
    last_idx = visible_count - 1
    coin_name = symbol.split('/')[0]

    empty_right_bars = 18
    x_right_edge = last_idx + empty_right_bars

    dt_ms = float(sub_df.loc[1, 'timestamp'] - sub_df.loc[0, 'timestamp']) if len(sub_df) > 1 else 900000.0
    t_start = float(sub_df.loc[0, 'timestamp'])
    t_right_edge = float(sub_df.loc[last_idx, 'timestamp']) + (empty_right_bars * dt_ms)

    target_r = ch['touched_ratios'][0][0] if ch['touched_ratios'] else ch['closest_ratio']
    target_lvl_price = ch['ltf_levels'][target_r]

    raw_min = min(sub_df['low'].min(), target_lvl_price)
    raw_max = max(sub_df['high'].max(), target_lvl_price)
    pad = max((raw_max - raw_min) * 0.18, raw_max * 0.004)
    y_min = raw_min - pad
    y_max = raw_max + pad

    fig, ax = plt.subplots(1, 1, figsize=(14, 7), facecolor='#131722')
    fig.subplots_adjust(left=0.07, right=0.87, top=0.91, bottom=0.10)
    ax.set_facecolor('#131722')

    ax.set_title(
        f"{coin_name} {tf_low.upper()} ({tf_high.upper()} {ch['scale_kor']} 투영)",
        color='white', fontsize=14, pad=12, fontweight='bold'
    )

    candle_width = 0.62
    for idx, row in sub_df.iterrows():
        is_bull = row['close'] >= row['open']
        color = '#089981' if is_bull else '#f23645'
        ax.plot([idx, idx], [row['low'], row['high']], color=color, linewidth=1.1, zorder=2)
        body_low = min(row['open'], row['close'])
        body_height = max(abs(row['close'] - row['open']), 1e-4)
        rect = patches.Rectangle((idx - candle_width / 2, body_low), candle_width, body_height,
                                 facecolor=color, edgecolor=color, zorder=3)
        ax.add_patch(rect)

    curr_close = float(sub_df.iloc[-1]['close'])
    ax.axvline(last_idx, color='#363a45', linestyle='--', linewidth=0.8, alpha=0.7, zorder=1)
    ax.axhline(curr_close, color='#00bcd4', linestyle=':', linewidth=1.0, alpha=0.8, zorder=3)

    x_vals = np.array([0, x_right_edge])
    line0_start = ch['p1_price'] + ch['slope_ms'] * (t_start - ch['t1'])
    line0_end = ch['p1_price'] + ch['slope_ms'] * (t_right_edge - ch['t1'])
    line0_y = np.array([line0_start, line0_end])

    touched_r_set = {r for r, _ in ch['touched_ratios']}
    ratio_weights = ch.get('ratio_weights', {})

    for r in ch['ratios']:
        if ch['mode'] == "HIGH":
            y_vals = line0_y - (r * ch['height'])
        else:
            y_vals = line0_y + (r * ch['height'])

        r_w = ratio_weights.get(r, 0.5)
        line_color, lw, alpha = _get_line_style(r, r_w, touched_r_set, target_r=target_r, is_ltf=True)
        ax.plot(x_vals, y_vals, color=line_color, alpha=alpha, linewidth=lw, zorder=4, clip_on=True)

        end_y = y_vals[1]
        lvl_price = ch['ltf_levels'][r]
        if y_min <= end_y <= y_max:
            tag = " ◀ TOUCH" if r in touched_r_set else ""
            ax.annotate(
                f"[{r:.1f}] {lvl_price:,.1f}{tag}",
                xy=(1.006, end_y), xycoords=('axes fraction', 'data'),
                color=line_color, fontsize=9, va='center', fontweight='bold', clip_on=False
            )

    if touched_r_set:
        ax.scatter([last_idx], [target_lvl_price], s=130, facecolor='none', edgecolor='#00e676', linewidth=2.2, zorder=7)

    step = max(1, visible_count // 6)
    tick_indices = list(range(0, visible_count, step))
    ax.set_xticks(tick_indices)
    ax.set_xticklabels([sub_df.loc[i, 'datetime'].strftime('%m-%d %H:%M') for i in tick_indices], color='#b2b5be', fontsize=8.5)
    ax.tick_params(axis='y', colors='#b2b5be')
    ax.grid(color='#2a2e39', linestyle='--', linewidth=0.5, alpha=0.5)

    ax.set_ylim(y_min, y_max)
    ax.set_xlim(-1.5, x_right_edge)

    plt.savefig(LTF_IMAGE_PATH, dpi=140, facecolor='#131722')
    plt.close(fig)
    return LTF_IMAGE_PATH


def generate_touched_pair_images(symbol: str, tf_high: str, tf_low: str, df_high: pd.DataFrame, df_low: pd.DataFrame, target_ch: dict) -> list:
    if target_ch is None:
        return []

    coin_name = symbol.split('/')[0]
    htf_path = plot_htf_channel(symbol, tf_high, df_high, target_ch)
    ltf_path = plot_ltf_channel_touch(symbol, tf_high, tf_low, df_low, target_ch)

    r_hit = target_ch["touched_ratios"][0][0] if target_ch["touched_ratios"] else target_ch["closest_ratio"]

    htf_caption = f"{coin_name} {tf_high.upper()} {target_ch['scale_kor']} [{r_hit:.1f}]"
    ltf_caption = f"{coin_name} {tf_low.upper()} ({tf_high.upper()} {target_ch['scale_kor']} [{r_hit:.1f}])"

    return [(htf_path, htf_caption), (ltf_path, ltf_caption)]