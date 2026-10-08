import matplotlib
matplotlib.use('Agg')  # 팝업창 완전 차단 (백그라운드 저장 전용)

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import platform

if platform.system() == 'Windows':
    plt.rcParams['font.family'] = 'Malgun Gothic'
else:
    plt.rcParams['font.family'] = 'NanumGothic'  # ★ 깃허브 액션(Ubuntu) 한글 깨짐 방지
plt.rcParams['axes.unicode_minus'] = False

# 고정 파일명 2개 (계속 덮어쓰기 됨)
HTF_IMAGE_PATH = "channel_htf.png"
LTF_IMAGE_PATH = "channel_ltf.png"

SCALE_KOR_NAMES = {
    "SHORT": "단기",
    "MEDIUM": "중기",
    "LONG": "장기"
}


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
    prev_peaks = [p for p in wall_peaks if p < peak1_idx - 2]
    start_idx = max(prev_peaks[-1] + 1, peak1_idx - 16) if prev_peaks else max(0, peak1_idx - 10)
    sub_closes = df['close'].iloc[start_idx:peak1_idx]
    return int(sub_closes.idxmin())


def find_pre_high_close(df: pd.DataFrame, valley1_idx: int, wall_valleys: list):
    prev_valleys = [v for v in wall_valleys if v < valley1_idx - 2]
    start_idx = max(prev_valleys[-1] + 1, valley1_idx - 16) if prev_valleys else max(0, valley1_idx - 10)
    sub_closes = df['close'].iloc[start_idx:valley1_idx]
    return int(sub_closes.idxmax())


def build_channel_with_ltf(df_high: pd.DataFrame, df_low: pd.DataFrame, idx1: int, idx2: int, wall_points: list, mode: str, scale_type: str):
    """
    상위봉(df_high)에서 채널을 작도하고, 그 채널 선을 하위봉(df_low, 15분봉 등)의 실시간 시간에 투영하여
    하위봉이 실제로 그 채널 선에 닿았는지 판별합니다.
    """
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

    # 1) 상위봉 인덱스 기준 기울기 & 밀리초(timestamp) 기준 기울기 (하위봉 투영용)
    slope_idx = (c2 - c1) / (idx2 - idx1)
    slope_ms = (c2 - c1) / (t2 - t1)

    line0_at_third = c1 + slope_idx * (third_idx - idx1)
    if mode == "HIGH":
        channel_height = max(line0_at_third - c3, 1e-4)
    else:
        channel_height = max(c3 - line0_at_third, 1e-4)

    ratios = [round(r * 0.5, 1) for r in range(0, 11)]

    # 2) 현재 하위봉(15분봉 등) 시점에서의 정확한 채널 가격 계산
    ltf_curr = df_low.iloc[-1]
    ltf_prev = df_low.iloc[-2] if len(df_low) >= 2 else ltf_curr
    ltf_t = float(ltf_curr['timestamp'])
    ltf_close = float(ltf_curr['close'])
    # 직전 마감 봉과 현재 진행 봉의 고가/저가 범위 (15분봉 터치 판별용)
    ltf_low = min(float(ltf_curr['low']), float(ltf_prev['low']))
    ltf_high = max(float(ltf_curr['high']), float(ltf_prev['high']))

    line0_at_ltf = c1 + slope_ms * (ltf_t - t1)
    if mode == "HIGH":
        ltf_levels = {r: line0_at_ltf - (r * channel_height) for r in ratios}
    else:
        ltf_levels = {r: line0_at_ltf + (r * channel_height) for r in ratios}

    # 하위봉 터치 허용 오차 (채널 1칸의 5% 또는 현재가의 0.15% 이내)
    touch_tol = min(channel_height * 0.05, ltf_close * 0.0015)

    touched_ratios = []
    min_dist = float('inf')
    closest_ratio = 0.0

    for r, lvl_price in ltf_levels.items():
        dist = abs(ltf_close - lvl_price)
        if dist < min_dist:
            min_dist = dist
            closest_ratio = r
        # 15분봉 캔들(고가~저가)이 채널 선에 닿았거나 현재가가 오차 범위 이내일 때 터치 인정!
        if (ltf_low - touch_tol <= lvl_price <= ltf_high + touch_tol) or (dist <= touch_tol):
            touched_ratios.append((r, lvl_price))

    mode_kor = "고점기준(하향)" if mode == "HIGH" else "저점기준(상향)"
    scale_kor = SCALE_KOR_NAMES[scale_type]

    return {
        "scale_type": scale_type,
        "scale_kor": scale_kor,
        "mode": mode,
        "mode_kor": mode_kor,
        "title": f"{scale_kor} {mode_kor} 채널 (간격 {idx2 - idx1}봉)",
        "p1_idx": idx1, "p1_time": df_high.loc[idx1, 'datetime'], "p1_price": c1, "t1": t1,
        "p2_idx": idx2, "p2_time": df_high.loc[idx2, 'datetime'], "p2_price": c2, "t2": t2,
        "third_idx": third_idx, "third_time": df_high.loc[third_idx, 'datetime'], "third_price": c3, "t3": t3,
        "slope_idx": slope_idx,
        "slope_ms": slope_ms,
        "height": channel_height,
        "ratios": ratios,
        "ltf_levels": ltf_levels,
        "touched_ratios": touched_ratios,
        "closest_ratio": closest_ratio,
        "min_dist": min_dist
    }


def extract_channels_by_mode(df_high: pd.DataFrame, df_low: pd.DataFrame, mode: str = "HIGH") -> list:
    if mode == "HIGH":
        short_pts = find_swing_high_closes(df_high, left=2, right=2)
        wall_pts = find_swing_high_closes(df_high, left=2, right=3)
        med_pts = find_swing_high_closes(df_high, left=4, right=4)
        long_pts = find_swing_high_closes(df_high, left=6, right=6)
        validator = is_valid_roof_line
    else:
        short_pts = find_swing_low_closes(df_high, left=2, right=2)
        wall_pts = find_swing_low_closes(df_high, left=2, right=3)
        med_pts = find_swing_low_closes(df_high, left=4, right=4)
        long_pts = find_swing_low_closes(df_high, left=6, right=6)
        validator = is_valid_floor_line

    channels = []

    # 1. 단기 (4 ~ 12봉)
    if len(short_pts) >= 2:
        for i in range(len(short_pts) - 1, 0, -1):
            for j in range(i - 1, -1, -1):
                pt2, pt1 = short_pts[i], short_pts[j]
                if 4 <= (pt2 - pt1) <= 12 and validator(df_high, pt1, pt2):
                    channels.append(build_channel_with_ltf(df_high, df_low, pt1, pt2, wall_pts, mode, "SHORT"))
                    break
            if any(c["scale_type"] == "SHORT" for c in channels):
                break

    # 2. 중기 (13 ~ 30봉)
    if len(med_pts) >= 2:
        for i in range(len(med_pts) - 1, 0, -1):
            for j in range(i - 1, -1, -1):
                pt2, pt1 = med_pts[i], med_pts[j]
                if 13 <= (pt2 - pt1) <= 30 and validator(df_high, pt1, pt2):
                    channels.append(build_channel_with_ltf(df_high, df_low, pt1, pt2, wall_pts, mode, "MEDIUM"))
                    break
            if any(c["scale_type"] == "MEDIUM" for c in channels):
                break

    # 3. 장기 (31봉 이상)
    if len(long_pts) >= 2:
        for i in range(len(long_pts) - 1, 0, -1):
            pt2, pt1 = long_pts[i], long_pts[i - 1]
            if (pt2 - pt1) >= 31 and validator(df_high, pt1, pt2):
                channels.append(build_channel_with_ltf(df_high, df_low, pt1, pt2, wall_pts, mode, "LONG"))
                break
        if not any(c["scale_type"] == "LONG" for c in channels):
            for i in range(len(long_pts) - 1, 0, -1):
                for j in range(i - 1, -1, -1):
                    pt2, pt1 = long_pts[i], long_pts[j]
                    if (pt2 - pt1) >= 31 and validator(df_high, pt1, pt2):
                        channels.append(build_channel_with_ltf(df_high, df_low, pt1, pt2, wall_pts, mode, "LONG"))
                        break
                if any(c["scale_type"] == "LONG" for c in channels):
                    break

    return channels


def find_touched_or_closest_channel(df_high: pd.DataFrame, df_low: pd.DataFrame):
    """
    상위봉(4h 등)에서 만들어진 모든 채널 중:
    1) 현재 하위봉(15m 등)이 실제로 터치하고 있는 채널들을 찾고,
    2) 가장 정확히 닿아 있는 대표 채널 1개(target_ch)와 터치된 전체 목록(touched_list)을 반환합니다.
    """
    df_high = df_high.reset_index(drop=True)
    df_low = df_low.reset_index(drop=True)

    all_channels = extract_channels_by_mode(df_high, df_low, "HIGH") + extract_channels_by_mode(df_high, df_low, "LOW")
    touched_list = [ch for ch in all_channels if ch["touched_ratios"]]

    if touched_list:
        # 터치된 채널 중 현재가와 라인의 거리가 가장 밀착된 채널을 대표 이미지용으로 선택
        best_target = min(touched_list, key=lambda x: x["min_dist"])
    elif all_channels:
        best_target = min(all_channels, key=lambda x: x["min_dist"])
    else:
        best_target = None

    return best_target, touched_list


def plot_htf_channel(symbol: str, tf_high: str, df_high: pd.DataFrame, ch: dict) -> str:
    """
    [1번 이미지] 상위 프레임(예: 4시간봉) 채널 차트 저장 (오른쪽 30%는 캔들이 없는 완전한 빈 칸)
    """
    df = df_high.reset_index(drop=True)
    last_idx = len(df) - 1
    scale_type = ch["scale_type"]

    first_anchor = min(ch['p1_idx'], ch['third_idx'])
    if scale_type == "SHORT":
        x_start = max(0, first_anchor - 5)
    elif scale_type == "MEDIUM":
        x_start = max(0, first_anchor - 7)
    else:
        x_start = max(0, first_anchor - 9)

    sub_df = df.iloc[x_start:]
    visible_count = len(sub_df)

    # ★ 핵심: 오른쪽에 캔들이 전혀 없는 빈 칸을 visible_count의 32%(최소 8칸) 확보!
    empty_right_bars = max(8, int(visible_count * 0.32))
    x_right_edge = last_idx + empty_right_bars

    y_min = sub_df['low'].min() * 0.992
    y_max = sub_df['high'].max() * 1.006

    fig, ax = plt.subplots(1, 1, figsize=(14, 7), facecolor='#131722')
    # 우측 테두리 바깥에 가격표를 적을 여백(13%) 확보
    fig.subplots_adjust(left=0.07, right=0.87, top=0.91, bottom=0.10)
    ax.set_facecolor('#131722')

    curr_close = float(df.iloc[-1]['close'])
    ax.set_title(
        f"[{symbol} - {tf_high} 상위봉] {ch['title']} | 현재가: {curr_close:,.2f}",
        color='white', fontsize=13, pad=12, fontweight='bold'
    )

    # 1. 캔들 그리기 (last_idx 까지만 그림 -> 그 오른쪽은 완전 빈 칸!)
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

    # 마지막 캔들 위치에 옅은 세로 점선 표시 (오른쪽이 빈 칸임을 한눈에 구분)
    ax.axvline(last_idx, color='#363a45', linestyle='--', linewidth=0.8, alpha=0.7, zorder=1)
    ax.axhline(curr_close, color='#00bcd4', linestyle=':', linewidth=1.0, alpha=0.8, zorder=3)

    # 2. 채널 평행선 그리기 (빈 칸 끝인 x_right_edge까지 쭉 연장)
    line_start_x = max(x_start, first_anchor - 2)
    x_vals = np.array([line_start_x, x_right_edge])
    line0_y = ch['p1_price'] + ch['slope_idx'] * (x_vals - ch['p1_idx'])

    touched_r_set = {r for r, _ in ch['touched_ratios']}

    for r in ch['ratios']:
        if ch['mode'] == "HIGH":
            y_vals = line0_y - (r * ch['height'])
        else:
            y_vals = line0_y + (r * ch['height'])

        is_hit = (r in touched_r_set) or (r == ch['closest_ratio'] and not touched_r_set)
        if r in touched_r_set:
            line_color = '#00e676'
            lw = 2.1
            alpha = 1.0
        else:
            line_color = '#ffffff' if r.is_integer() else '#f5d142'
            lw = 1.3 if r.is_integer() else 1.0
            alpha = 0.85 if r.is_integer() else 0.75

        ax.plot(x_vals, y_vals, color=line_color, alpha=alpha, linewidth=lw, zorder=4, clip_on=True)

        # ★ 가격 라벨을 차트 안쪽이 아니라 오른쪽 테두리 바깥(Y축 위)에 배치하여 빈 칸을 가리지 않음!
        end_y = y_vals[1]
        lvl_price = ch['ltf_levels'][r]
        if y_min <= end_y <= y_max:
            tag = " 👈TOUCH" if r in touched_r_set else ""
            ax.annotate(
                f"[{r:.1f}] {lvl_price:,.1f}{tag}",
                xy=(1.006, end_y), xycoords=('axes fraction', 'data'),
                color=line_color, fontsize=9, va='center', fontweight='bold', clip_on=False
            )

    # 3. 3개의 기준 파란 점 표시
    anchor_x = [ch['p1_idx'], ch['p2_idx'], ch['third_idx']]
    anchor_y = [ch['p1_price'], ch['p2_price'], ch['third_price']]
    ax.scatter(anchor_x, anchor_y, s=110, facecolor='#1e53e5', edgecolor='#64b5f6', linewidth=2, zorder=6)

    if ch['mode'] == "HIGH":
        lbl1, lbl2, lbl3 = "고점1(종가)", "고점2(종가)", "직전저점(종가)"
        offset1, offset2, offset3 = (0, 12), (0, 12), (0, -24)
    else:
        lbl1, lbl2, lbl3 = "저점1(종가)", "저점2(종가)", "직전고점(종가)"
        offset1, offset2, offset3 = (0, -24), (0, -24), (0, 12)

    ax.annotate(f"{lbl1}\n{ch['p1_price']:,.1f}", (ch['p1_idx'], ch['p1_price']),
                textcoords="offset points", xytext=offset1, ha='center', color='#64b5f6', fontsize=8.5, fontweight='bold')
    ax.annotate(f"{lbl2}\n{ch['p2_price']:,.1f}", (ch['p2_idx'], ch['p2_price']),
                textcoords="offset points", xytext=offset2, ha='center', color='#64b5f6', fontsize=8.5, fontweight='bold')
    ax.annotate(f"{lbl3}\n{ch['third_price']:,.1f}", (ch['third_idx'], ch['third_price']),
                textcoords="offset points", xytext=offset3, ha='center', color='#64b5f6', fontsize=8.5, fontweight='bold')

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
    """
    [2번 이미지] 하위 프레임(예: 15분봉) 차트 위에 상위 프레임(4시간봉) 채널 선을 그대로 그어서
    15분봉이 채널 선에 어떻게 닿아 있는지 확대해 보여주는 차트 (오른쪽 30% 빈 칸 포함)
    """
    # 최근 55개의 15분봉만 확대해서 표시
    sub_df = df_low.tail(55).reset_index(drop=True)
    visible_count = len(sub_df)
    last_idx = visible_count - 1

    # 오른쪽에 봉이 없는 빈 칸 18개(약 32%) 확보
    empty_right_bars = 18
    x_right_edge = last_idx + empty_right_bars

    # 15분봉 간격(ms) 계산
    dt_ms = float(sub_df.loc[1, 'timestamp'] - sub_df.loc[0, 'timestamp']) if len(sub_df) > 1 else 900000.0
    t_start = float(sub_df.loc[0, 'timestamp'])
    t_right_edge = float(sub_df.loc[last_idx, 'timestamp']) + (empty_right_bars * dt_ms)

    # Y축 범위: 15분봉 고가/저가 + 가장 가까운 채널 라인이 반드시 화면 안에 보이도록 설정
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

    curr_close = float(sub_df.iloc[-1]['close'])
    ax.set_title(
        f"[{symbol} - {tf_low} 하위봉] ({tf_high} {ch['scale_kor']} 채널 투영) | 현재가: {curr_close:,.2f}",
        color='white', fontsize=13, pad=12, fontweight='bold'
    )

    # 1. 15분봉 캔들 그리기
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

    # 마지막 15분봉 위치 세로선 & 현재가 가로선
    ax.axvline(last_idx, color='#363a45', linestyle='--', linewidth=0.8, alpha=0.7, zorder=1)
    ax.axhline(curr_close, color='#00bcd4', linestyle=':', linewidth=1.0, alpha=0.8, zorder=3)

    # 2. 상위(4h) 채널 선들을 15분봉 시간축 위에 정확히 투영하여 그리기
    x_vals = np.array([0, x_right_edge])
    line0_start = ch['p1_price'] + ch['slope_ms'] * (t_start - ch['t1'])
    line0_end = ch['p1_price'] + ch['slope_ms'] * (t_right_edge - ch['t1'])
    line0_y = np.array([line0_start, line0_end])

    touched_r_set = {r for r, _ in ch['touched_ratios']}

    for r in ch['ratios']:
        if ch['mode'] == "HIGH":
            y_vals = line0_y - (r * ch['height'])
        else:
            y_vals = line0_y + (r * ch['height'])

        if r in touched_r_set:
            line_color = '#00e676'  # 15분봉이 닿은 채널 선은 밝은 형광 초록색으로 강조!
            lw = 2.4
            alpha = 1.0
        elif r == target_r:
            line_color = '#00bcd4'
            lw = 1.8
            alpha = 0.95
        else:
            line_color = '#ffffff' if r.is_integer() else '#f5d142'
            lw = 1.3 if r.is_integer() else 1.0
            alpha = 0.80 if r.is_integer() else 0.70

        ax.plot(x_vals, y_vals, color=line_color, alpha=alpha, linewidth=lw, zorder=4, clip_on=True)

        end_y = y_vals[1]
        lvl_price = ch['ltf_levels'][r]
        if y_min <= end_y <= y_max:
            tag = " 👈15m 터치!" if r in touched_r_set else (" 👈근접" if r == target_r else "")
            ax.annotate(
                f"[{r:.1f}] {lvl_price:,.1f}{tag}",
                xy=(1.006, end_y), xycoords=('axes fraction', 'data'),
                color=line_color, fontsize=9, va='center', fontweight='bold', clip_on=False
            )

    # 마지막 15분봉 터치 지점에 눈에 띄는 노란색 원 포인트 표시
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
    """
    닿은 채널에 대해 딱 2장의 이미지(1. 상위 4h 채널 차트, 2. 하위 15m 터치 차트)만 덮어쓰기 생성하여 반환합니다.
    """
    if target_ch is None:
        return []

    htf_path = plot_htf_channel(symbol, tf_high, df_high, target_ch)
    ltf_path = plot_ltf_channel_touch(symbol, tf_high, tf_low, df_low, target_ch)

    if target_ch["touched_ratios"]:
        r_hit, p_hit = target_ch["touched_ratios"][0]
        status_str = f"🔥 [{r_hit:.1f}] 라인 ({p_hit:,.1f}) 실시간 터치!"
    else:
        r_hit = target_ch["closest_ratio"]
        p_hit = target_ch["ltf_levels"][r_hit]
        status_str = f"📍 [{r_hit:.1f}] 라인 ({p_hit:,.1f}) 근접"

    htf_caption = (
        f"📐 [1/2] {symbol} {tf_high}(상위봉) 채널 작도 이미지입니다.\n"
        f"• 적용 채널: {target_ch['title']}\n"
        f"• {status_str}"
    )
    ltf_caption = (
        f"⚡ [2/2] {symbol} {tf_low}(하위봉) 실시간 채널 터치 이미지입니다.\n"
        f"• {tf_high} {target_ch['scale_kor']} 채널을 {tf_low} 차트에 그대로 투영한 화면\n"
        f"• {status_str}"
    )

    return [(htf_path, htf_caption), (ltf_path, ltf_caption)]