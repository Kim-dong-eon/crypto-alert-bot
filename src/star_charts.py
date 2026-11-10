"""Side-by-side star alert charts using the approved historical preview style."""
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from src.ict_engine import TF_MS, frame_open
from src.ict_overlap import LABELS, kst
from src.overlap_charts import configure_font
from src.star_alerts import TITLES, unit_text
from config.overlap_settings import TRACK_LABELS

WHITE = '#edf3fb'
MUTED = '#abb9ca'
GOLD = '#ffd270'


def window(frames, tf, event, current_at):
    now = event['now']
    bars = [b for b in frames[tf] if b.timestamp + TF_MS[tf] <= now
            or (now == current_at and b.timestamp <= now)]
    if tf == event['pair'][0]:
        start = min(event['upper']['chart_start'], event['parent']['origin']) - 4 * TF_MS[tf]
    elif 'lower' in event:
        start = event['lower']['chart_start'] - 5 * TF_MS[tf]
    else:
        return bars[-36:]
    return [b for b in bars if b.timestamp >= start]


def fmt(value):
    return f'{value:,.2f}'.rstrip('0').rstrip('.')


def draw_panel(ax, tf, candles, event):
    from matplotlib.patches import Rectangle, Patch
    now, level = event['now'], event['level']
    top, middle, final = event['path']
    is_top = tf == top
    colors = {top: '#78aaff', middle: '#4cddd5', final: '#e7ae62'}
    start, step = candles[0].timestamp, TF_MS[tf]
    position = lambda stamp: (stamp - start) / step
    edge = len(candles) + 2
    ax.set_facecolor('#152130')
    ax.grid(color='#a0b7d1', alpha=.12)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.yaxis.tick_right()
    zones = event['overlap']['sources'] if level == 3 else event['sources']
    legend = {}
    prices = [value for b in candles for value in (b.low, b.high)]
    for z in zones:
        color = '#8bece6' if z['timeframe'] == middle and z['kind'] == 'FVG' else colors[z['timeframe']]
        left = max(-.6, position(z['created']))
        ax.add_patch(Rectangle((left, z['low']), edge - left, z['high'] - z['low'], color=color, alpha=.11, zorder=1))
        ax.add_patch(Rectangle((left, z['low']), edge - left, z['high'] - z['low'], fill=False, edgecolor=color, linewidth=.75, zorder=2))
        name = f"{LABELS[z['timeframe']]} {z['kind']}"
        legend[name] = Patch(facecolor=color, alpha=.62, label=name)
        prices.extend([z['low'], z['high']])

    if level >= 2:
        overlap = event['overlap'] if level == 3 else event
        lo, hi = overlap['band']
        left = max(-.6, position(overlap['touch']['valid_from']))
        ax.add_patch(Rectangle((left, lo), edge - left, hi - lo, facecolor=WHITE, alpha=.26, zorder=3))
        ax.hlines((lo + hi) / 2, left, edge, color=WHITE, linewidth=.9, zorder=3)
        legend['공통 구간'] = Patch(facecolor=WHITE, alpha=.55, label='3중 공통 구간')
    if level == 3 and not is_top:
        ob = event['sources'][-1]
        ob_left = max(-.6, position(ob['created']))
        ax.add_patch(Rectangle((ob_left, ob['low']), edge - ob_left, ob['high'] - ob['low'],
                               fill=False, edgecolor=GOLD, linestyle=':', linewidth=.95, zorder=2))
        prices.extend([ob['low'], ob['high']])
        lo, hi = event['band']
        left = max(-.6, position(event['touch']['valid_from']))
        ax.add_patch(Rectangle((left, lo), edge - left, hi - lo, facecolor=GOLD, alpha=.29, zorder=3))
        ax.hlines((lo + hi) / 2, left, edge, color=GOLD, linewidth=1.2, zorder=3)
        legend['재접촉'] = Patch(facecolor=GOLD, alpha=.65, label='1:3 충족 구간')

    for i, b in enumerate(candles):
        color = '#46d3aa' if b.close >= b.open else '#fa7b8a'
        ax.vlines(i, b.low, b.high, color=color, linewidth=.85, zorder=4)
        ax.add_patch(Rectangle((i - .31, min(b.open, b.close)), .62, max(abs(b.close - b.open), .3),
                               facecolor=color, edgecolor=GOLD if b.timestamp + step > now else color,
                               linewidth=1 if b.timestamp + step > now else .35, zorder=5))
    stair = event['upper'] if is_top else event.get('lower')
    if stair:
        points = stair['points']
        xs = [position(p['timestamp']) for p in points]
        ys = [p['price'] for p in points]
        ax.plot(xs, ys, color='#69e1e8', linewidth=1.65, marker='o', markersize=3, zorder=7)
        if is_top and stair['target'] is not None:
            ax.axhline(stair['target'], color='#88dfa4', linestyle='--', linewidth=1.1, zorder=3)
            ax.text(.98, stair['target'], f"{LABELS[top]} 목표 {fmt(stair['target'])}",
                    transform=ax.get_yaxis_transform(), ha='right', va='top', color='#9eecad', fontsize=10)
            prices.append(stair['target'])
        elif not is_top and level == 3:
            ax.axhline(event['stop'], color='#ff9da9', linestyle='--', linewidth=1, zorder=3)
            extreme = '저점' if event['direction'] == 1 else '고점'
            ax.text(.02, event['stop'], f"{LABELS[final]} 기준 {extreme} {fmt(event['stop'])}",
                    transform=ax.get_yaxis_transform(), ha='left', va='bottom', color='#ffabb4', fontsize=10,
                    bbox={'facecolor': '#152130', 'edgecolor': 'none', 'pad': 3})
            prices.append(event['stop'])

    lo, hi = (event['parent']['low'], event['parent']['high']) if is_top else event['band']
    if is_top:
        stamp = event['arm'] - 1
        price = event['arm_price']
    else:
        touch = event['touch']
        stamp = touch.get('bar_open', touch['at'] - 1)
        price = touch['observed_price'] if 'observed_price' in touch else (max(lo, touch['observed_low']) + min(hi, touch['observed_high'])) / 2
    assert lo <= price <= hi
    px = position(frame_open(stamp, tf))
    if -.6 <= px < edge:
        ax.scatter([px], [price], marker='D', color=GOLD, s=29, zorder=9)
        label = f'{LABELS[top]} OB 첫 접촉' if is_top else '접촉 확인' if level < 3 else f'{LABELS[final]} OB 재접촉'
        offset = (-17, -27) if level == 3 and not is_top else (-15, 22)
        ax.annotate(label, (px, price), xytext=offset, textcoords='offset points', ha='right',
                    color=GOLD, fontsize=10, zorder=10, arrowprops={'arrowstyle': '-', 'color': GOLD},
                    bbox={'facecolor': '#152130', 'edgecolor': 'none', 'pad': 3})
    lo, hi = min(prices), max(prices)
    pad = max((hi - lo) * .16, 25)
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(-.65, edge)
    ticks = list(range(0, len(candles), max(1, len(candles) // 5)))
    ax.set_xticks(ticks, [kst(candles[i].timestamp).replace(' KST', '') for i in ticks])
    ax.spines[:].set_color('#29394b')
    ax.legend(handles=list(legend.values()), loc='lower left', bbox_to_anchor=(0, 1.005), ncol=6,
              fontsize=8, facecolor='#152130', edgecolor='#29394b', labelcolor=WHITE, borderaxespad=0,
              columnspacing=1.1, handletextpad=.5)


def render(snapshot, alert, frames, prefix):
    configure_font()
    event = alert['candidate']
    level, now = event['level'], event['now']
    top, middle, final = event['path']
    side = 'LONG' if event['direction'] == 1 else 'SHORT'
    fig, axes = plt.subplots(1, 2, figsize=(16, 6.1))
    fig.patch.set_facecolor('#101a27')
    fig.subplots_adjust(left=.035, right=.94, top=.71, bottom=.19, wspace=.17)
    fig.text(.035, .938, '★' * level, color=GOLD, size=23, weight='bold', va='center')
    title = f'{LABELS[final]} OB 관찰' if level == 1 else TITLES[level]
    fig.text(.155, .938, title, color=WHITE, size=23, weight='bold', va='center')
    exchange = event.get('exchange', 'bitget').title()
    fig.text(.036, .868, f"{exchange} {event['symbol']}  ·  {TRACK_LABELS[event['track']]} {side}  ·  {kst(now)}",
             color=MUTED, size=12)
    if snapshot.get('illustrative'):
        fig.text(.94, .935, '과거 시세 재생 미리보기', color=MUTED, size=11, ha='right')
    try:
        for ax, tf in zip(axes, (top, final)):
            draw_panel(ax, tf, window(frames, tf, event, snapshot['as_of']), event)
            upper_label = '계단 · 상위 OB와 목표' if event['upper']['target'] is not None else '상위 구조 · OB 접촉'
            panel = upper_label if tf == top else '계단 · 진입 확인' if level == 3 else '확대 · 접촉 확인'
            ax.set_title(f'{LABELS[tf]} {panel}', color=WHITE, loc='left', fontsize=13, pad=29)
        if level == 1:
            message = f'{LABELS[top]} OB 반응 안에서 같은 방향의 {LABELS[final]} OB 접촉'
        elif level == 2:
            message = f'{LABELS[top]} OB · {LABELS[middle]} OB/FVG · {LABELS[final]} OB 공통 가격대 첫 접촉'
        else:
            message = f"{LABELS[top]} 계단 · 3중 겹침 · {LABELS[final]} 계단과 재접촉    |    비용 반영 1:{event['rr']:.2f}"
        fig.text(.035, .112, message, color=WHITE, size=13, weight='bold')
        lo, hi = event['band']
        detail = f'접촉 구간 {fmt(lo)} ~ {fmt(hi)}'
        if level == 3:
            extreme = '저점' if event['direction'] == 1 else '고점'
            detail += (f"     진입 계산 {fmt(event['entry'])}     {LABELS[final]} 기준 {extreme} {fmt(event['stop'])}"
                       f"     {LABELS[top]} 목표 {fmt(event['target'])}")
        fig.text(.035, .064, detail, color=MUTED, size=11)
        prefix = Path(prefix)
        path = prefix.with_name(f'{prefix.name}_star{level}.png')
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=145, facecolor=fig.get_facecolor())
    finally:
        plt.close(fig)
    return {'images': [str(path.resolve())], 'image_captions': [''], 'text_after_images': unit_text(event)}
