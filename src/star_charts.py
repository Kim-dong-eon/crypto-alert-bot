"""Side-by-side star alert charts using the approved historical preview style."""
from pathlib import Path
from bisect import bisect_left
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from src.ict_engine import Bar, TF_MS, frame_open
from src.ict_overlap import LABELS, kst
from src.overlap_charts import configure_font
from src.star_alerts import alert_title, atr_label, price_text as fmt, unit_text, is_entry, contact_status
from config.overlap_settings import TRACK_LABELS

WHITE = '#edf3fb'
MUTED = '#abb9ca'
GOLD = '#ffd270'
DENSITY = ('#3b68bc', '#274393', '#1c2e72')
REFERENCE = '#dfb9fb'


def alert_marker(event):
    """Both panels show the same triggering observation and the same price."""
    low, high = event['band']
    touch = event['touch']
    stamp = touch.get('bar_open', touch['at'] - 1)
    price = (touch['observed_price'] if 'observed_price' in touch
             else (max(low, touch['observed_low']) + min(high, touch['observed_high'])) / 2)
    if not low <= price <= high:
        raise ValueError('Alert marker outside its trigger band')
    label = '이번 알림 · OB 재접촉' if is_entry(event) else '이번 알림 · 3중 접촉'
    return stamp, price, label


def overlap_cells(boxes, edge):
    """Split chart price and time wherever the number of active boxes changes."""
    levels = sorted({price for _, low, high in boxes for price in (low, high)})
    for low, high in zip(levels, levels[1:]):
        starts = sorted(left for left, bottom, top in boxes if bottom <= low and high <= top)
        for count, (left, right) in enumerate(zip(starts[1:], [*starts[2:], edge]), 2):
            if left < right:
                yield left, right, low, high, count


def primary_overlap(event, zones):
    """Prefer the middle FVG when showing the actual three-frame intersection."""
    overlap = event['overlap'] if is_entry(event) else event
    if formation := overlap.get('formation'):
        middle = event['path'][1]
        kinds = sorted({z['kind'] for z in zones if z['timeframe'] == middle})
        return *overlap['band'], formation['end'], '/'.join(kinds) or 'OB'
    if event['level'] < 2:
        return None
    _, middle, final = event['path']
    parent = next((z for z in zones if z['id'] == event['parent']['id']), None)
    middle_sources = [z for z in zones if z['timeframe'] == middle]
    child = next((z for z in zones if z['timeframe'] == final and z['kind'] == 'OB'), None)
    if not parent or not middle_sources or not child:
        return None
    middle_source = next((z for z in middle_sources if z['kind'] == 'FVG'), middle_sources[0])
    sources = (parent, middle_source, child)
    low, high = max(z['low'] for z in sources), min(z['high'] for z in sources)
    if low < high:
        return low, high, max(z['created'] for z in sources), middle_source['kind']
    return None


def window(frames, tf, event, current_at):
    reaction = event.get('reaction')
    cutoff = reaction['at'] if reaction else event['now']
    show_live = reaction is None and cutoff == current_at
    bars = [b for b in frames[tf] if b.timestamp + TF_MS[tf] <= cutoff
            or (show_live and b.timestamp <= cutoff)]
    if tf == event['pair'][0]:
        start = min(event['upper']['chart_start'], event['parent']['origin']) - 4 * TF_MS[tf]
    elif 'lower' in event:
        start = event['lower']['chart_start'] - 5 * TF_MS[tf]
    elif wave := event.get('lower_wave'):
        pivot_at = (event.get('observation_risk') or {}).get('pivot_at', wave['chart_start'])
        start = max(bars[-72].timestamp if len(bars) >= 72 else bars[0].timestamp,
                    min(min(wave['chart_start'], pivot_at) - 3 * TF_MS[tf], bars[-min(36, len(bars))].timestamp))
    else:
        return bars[-36:]
    return [b for b in bars if b.timestamp >= start]


def draw_panel(ax, tf, candles, event):
    from matplotlib.patches import Rectangle, Patch
    now, level = event['now'], event['level']
    top, middle, final = event['path']
    is_top = tf == top
    colors = {top: '#659dff', middle: '#43d9bc', final: '#f2a75b'}
    start, step = candles[0].timestamp, TF_MS[tf]
    position = lambda stamp: (stamp - start) / step
    cash_panel = is_top and event.get('stock_bars')
    if cash_panel:
        timestamps = [bar.timestamp for bar in candles]
        position = lambda stamp: bisect_left(timestamps, stamp)
    edge = len(candles) + 2
    ax.set_facecolor('#152130')
    ax.grid(color='#a0b7d1', alpha=.12)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.yaxis.tick_right()
    zones = event['overlap']['sources'] if is_entry(event) else event['sources']
    core = primary_overlap(event, zones)
    legend = {}
    overlap = event.get('overlap', event)
    formation = ({'start':event['parent']['origin'], 'end':event['parent']['created']}
                 if cash_panel else overlap.get('formation'))
    if is_top and formation:
        left = position(formation['start']) - .5
        right = position(formation['end']) - .5
        ax.axvspan(left, right, facecolor=GOLD, alpha=.12, zorder=1)
        legend['형성'] = Patch(facecolor=GOLD, alpha=.3, label=f'{LABELS[top]} OB 형성 봉')
    prices = [value for b in candles for value in (b.low, b.high)]
    prices.extend(event['band'])
    boxes = []
    for z in zones:
        color = '#8bece6' if z['timeframe'] == middle and z['kind'] == 'FVG' else colors[z['timeframe']]
        left = max(-.6, position(z['created']))
        ax.add_patch(Rectangle((left, z['low']), edge - left, z['high'] - z['low'], color=color, alpha=.12, zorder=1))
        ax.add_patch(Rectangle((left, z['low']), edge - left, z['high'] - z['low'], fill=False, edgecolor=color, linewidth=1, zorder=2))
        boxes.append((left, z['low'], z['high']))
        source = (event.get('source_labels') or {}).get(z['timeframe'], '')
        name = f"{source + ' ' if source else ''}{LABELS[z['timeframe']]} {z['kind']}"
        legend[name] = Patch(facecolor=color, alpha=.62, label=name)
        if is_top:
            prices.extend([z['low'], z['high']])

    if core:
        lo, hi, known, _ = core
        start_core = max(-.6, position(known))
        clipped = [(max(left, start_core), max(low, lo), min(high, hi))
                   for left, low, high in boxes if max(low, lo) < min(high, hi)]
        for left, right, low, high, count in overlap_cells(clipped, edge):
            if count >= 3:
                color = DENSITY[min(count - 3, len(DENSITY) - 1)]
                ax.add_patch(Rectangle((left, low), right - left, high - low, facecolor=color,
                                       alpha=.94, edgecolor='none', zorder=1.5))
        ax.add_patch(Rectangle((start_core, lo), edge - start_core, hi - lo, fill=False,
                               edgecolor=WHITE, linewidth=1.5, zorder=3))
        prices.extend((lo, hi))
        legend['핵심'] = Patch(facecolor='none', edgecolor=WHITE,
                              label='최초 3중 겹침' if is_entry(event) else '핵심 3중 겹침')

    if level >= 2:
        overlap = event['overlap'] if is_entry(event) else event
        lo, hi = overlap['band']
        prices.extend((lo, hi))
    if is_entry(event) and not is_top:
        ob = event['sources'][-1]
        ob_left = max(-.6, position(ob['created']))
        ax.add_patch(Rectangle((ob_left, ob['low']), edge - ob_left, ob['high'] - ob['low'],
                               fill=False, edgecolor=GOLD, linestyle=':', linewidth=.95, zorder=2))
        legend['후속'] = Patch(facecolor='none', edgecolor=GOLD, linestyle=':', label=f'{LABELS[final]} 후속 OB')
        prices.extend([ob['low'], ob['high']])
        lo, hi = event['band']
        left = max(-.6, position(event['touch']['valid_from']))
        ax.add_patch(Rectangle((left, lo), edge - left, hi - lo, facecolor=GOLD, alpha=.29, zorder=3))
        ax.hlines((lo + hi) / 2, left, edge, color=GOLD, linewidth=1.2, zorder=3)
        legend['재접촉'] = Patch(facecolor=GOLD, alpha=.65, label='1:3 충족 구간')
    risk = event.get('observation_risk') or {}
    if not is_top and (plan := risk.get('plan')):
        lo, hi = plan['band']
        left = position(frame_open(event['touch'].get('bar_open', event['touch']['at'] - 1), tf))
        ax.hlines(sorted({lo, hi}), max(-.6, left), edge, color=REFERENCE, linestyle='--', linewidth=2, zorder=6)
        legend['참고'] = Patch(facecolor='none', edgecolor=REFERENCE, linestyle='--', label='참고 1:3')

    candle_span = max(b.high for b in candles) - min(b.low for b in candles)
    min_body = max(candle_span * .0015, abs(candles[-1].close) * .000005)
    for i, b in enumerate(candles):
        color = '#46d3aa' if b.close >= b.open else '#fa7b8a'
        unclosed = not cash_panel and b.timestamp + step > now
        ax.vlines(i, b.low, b.high, color=color, linewidth=.85, zorder=4)
        height = max(abs(b.close - b.open), min_body)
        ax.add_patch(Rectangle((i - .31, (b.open + b.close - height) / 2), .62, height,
                               facecolor=color, edgecolor=GOLD if unclosed else color,
                               linewidth=1 if unclosed else .35, zorder=5))
    structure = event['upper'] if is_top else event.get('lower') or event.get('lower_wave')
    if structure:
        points = [p for p in structure['points'] if start <= p['timestamp'] <= candles[-1].timestamp]
        xs = [position(p['timestamp']) for p in points]
        ys = [p['price'] for p in points]
        if points:
            ax.plot(xs, ys, color='#78ebff', linewidth=2.05, marker='o', markersize=4, zorder=7)
        if (level == 2 or is_entry(event) or event.get('formation') or cash_panel) and points and points[-1]['timestamp'] < candles[-1].timestamp:
            ax.plot([xs[-1], len(candles) - 1], [ys[-1], candles[-1].close],
                    color=GOLD, linestyle='--', linewidth=1.5, zorder=7)
        goal = event['target'] if is_entry(event) and is_top else risk.get('target', structure['target'])
        if is_top and goal is not None:
            ax.axhline(goal, color='#88dfa4', linestyle='--', linewidth=1.1, zorder=3)
            extreme = '고점' if event['direction'] == 1 else '저점'
            ax.text(.98, goal, f"{LABELS[top]} 직전 {extreme} · 목표 {fmt(goal)}",
                    transform=ax.get_yaxis_transform(), ha='right', va='top', color='#9eecad', fontsize=10)
            prices.append(goal)
        elif not is_top and is_entry(event):
            ax.axhline(event['stop'], color='#ff9da9', linestyle='--', linewidth=1, zorder=3)
            pivot_x = position(event['lower']['origin'])
            ax.scatter(pivot_x, event['stop'], s=65, facecolors='none', edgecolors='#ff9da9', linewidths=1.5, zorder=9)
            extreme = '저점' if event['direction'] == 1 else '고점'
            ax.text(.02, event['stop'], f"{LABELS[final]} 기준 {extreme} {fmt(event['stop'])}",
                    transform=ax.get_yaxis_transform(), ha='left', va='bottom', color='#ffabb4', fontsize=10,
                    bbox={'facecolor': '#152130', 'edgecolor': 'none', 'pad': 3})
            prices.append(event['stop'])
            ax.hlines(event['entry'], max(-.6, position(event['touch']['valid_from'])), edge,
                      color=GOLD, linestyle='--', linewidth=1.1, zorder=6)
            bound = '상한' if event['direction'] == 1 else '하한'
            ax.text(.98, event['entry'], f"진입 {bound} {fmt(event['entry'])}", transform=ax.get_yaxis_transform(),
                    ha='right', va='bottom', color=GOLD, fontsize=10, zorder=8,
                    bbox={'facecolor': '#152130', 'edgecolor': 'none', 'pad': 2})
    if level == 2 and not is_top and risk:
        stop = risk['stop']
        ax.axhline(stop, color='#ff9da9', linestyle=':', linewidth=1.3, zorder=4)
        stop_kind = '구조' if 'pivot_at' in risk else 'OB'
        ax.text(.02, stop, f'{LABELS[final]} {stop_kind} 손절 {fmt(stop)}', transform=ax.get_yaxis_transform(),
                ha='left', va='bottom', color='#ffabb4', fontsize=10, zorder=8)
        if (pivot_at := risk.get('pivot_at')) is not None and start <= pivot_at <= candles[-1].timestamp:
            ax.scatter(position(pivot_at), risk['pivot_price'], s=66, facecolors='none', edgecolors='#ff9da9',
                       linewidths=1.5, zorder=9)
        prices.append(stop)
    if not is_top and (reaction := event.get('reaction')):
        x = position(reaction['bar_open'])
        ax.axhline(reaction['level'], color='#88dfa4', linestyle='--', linewidth=1, zorder=3)
        ax.scatter(x, reaction['price'], marker='D', color='#88dfa4', s=38, zorder=9)
        ax.annotate('종가 돌파', (x, reaction['price']), xytext=(-12, 18), textcoords='offset points',
                    ha='right', color='#9eecad', fontsize=10, zorder=10)
        prices.extend((reaction['level'], reaction['price']))

    if is_top and (pivot := event.get('departure_pivot')) and start <= pivot['timestamp'] <= candles[-1].timestamp:
        kind = '고점' if pivot['kind'] == 'high' else '저점'
        pivot_x = position(pivot['timestamp'])
        pivot_color = '#ff8b9e' if pivot['kind'] == 'high' else '#79ebef'
        ax.hlines(pivot['price'], pivot_x, edge, color=pivot_color, linestyle='--', linewidth=1.3, zorder=5)
        ax.annotate(f"OB 출발 {kind} {fmt(pivot['price'])}", (pivot_x, pivot['price']),
                    xytext=(10, 16 if pivot['kind'] == 'high' else -18), textcoords='offset points',
                    color=pivot_color, fontsize=10, zorder=10, arrowprops={'arrowstyle': '-', 'color': pivot_color},
                    bbox={'facecolor': '#152130', 'edgecolor': 'none', 'pad': 2})

    stamp, price, label = alert_marker(event)
    # The cash panel contains only closed stock candles. A later Bitget touch
    # is placed in the right margin and explicitly identified as exchange data.
    px = (len(candles) + .5 if stamp > candles[-1].timestamp else position(stamp)) if cash_panel else position(frame_open(stamp, tf))
    if cash_panel:
        label = label.replace('이번 알림', '거래소 알림')
    if -.6 <= px < edge:
        ax.scatter([px], [price], marker='D', color=GOLD, s=29, zorder=9)
        offset = (-17, -27) if is_entry(event) and not is_top else (-15, 22)
        ax.annotate(label, (px, price), xytext=offset, textcoords='offset points', ha='right',
                    color=GOLD, fontsize=10, zorder=10, arrowprops={'arrowstyle': '-', 'color': GOLD},
                    bbox={'facecolor': '#152130', 'edgecolor': 'none', 'pad': 3})
        prices.append(price)
    # Parent-only contact is supporting context, not the alert trigger.
    if is_top and not cash_panel and event['arm'] != event['touch']['at']:
        arm_x = position(frame_open(event['arm'] - 1, tf))
        if -.6 <= arm_x < edge:
            ax.scatter([arm_x], [event['arm_price']], marker='o', facecolors='none',
                       edgecolors=MUTED, s=24, linewidths=.8, zorder=8)
            ax.annotate('상위 OB 접촉 · 참고', (arm_x, event['arm_price']),
                        xytext=(8, -22), textcoords='offset points', color=MUTED, fontsize=8,
                        arrowprops={'arrowstyle': '-', 'color': MUTED}, zorder=8)
    lo, hi = min(prices), max(prices)
    pad = max((hi - lo) * .16, abs(candles[-1].close) * .0005)
    ax.set_ylim(max(0, lo - pad), hi + pad)
    ax.ticklabel_format(axis='y', style='plain', useOffset=False)
    ax.set_xlim(-.65, edge)
    ticks = list(range(0, len(candles), max(1, len(candles) // 5)))
    ax.set_xticks(ticks, [kst(candles[i].timestamp).replace(' KST', '') for i in ticks])
    ax.spines[:].set_color('#29394b')
    ax.legend(handles=list(legend.values()), loc='lower left', bbox_to_anchor=(0, 1.005), ncol=6,
              fontsize=8, facecolor='#152130', edgecolor='#29394b', labelcolor=WHITE, borderaxespad=0,
              columnspacing=1.1, handletextpad=.5)


def render_cross_track(event, comparison, frames, prefix):
    from matplotlib.patches import Rectangle

    tf = comparison['tf']
    if tf not in frames:
        return None
    bars = [b for b in frames[tf] if b.timestamp + TF_MS[tf] <= event['now']]
    if not bars:
        return None
    wave = comparison['wave']
    oldest = wave[0]['timestamp'] - 4 * TF_MS[tf] if wave else bars[-min(48, len(bars))].timestamp
    bars = [b for b in bars if b.timestamp >= oldest]
    positions = {b.timestamp: i for i, b in enumerate(bars)}
    points = [p for p in wave if p['timestamp'] in positions]
    fig, ax = plt.subplots(figsize=(12, 5.2), layout='constrained')
    fig.patch.set_facecolor('#101a27')
    ax.set_facecolor('#152130')
    ax.grid(alpha=.14)
    ax.tick_params(colors=MUTED)
    ax.yaxis.tick_right()
    track = TRACK_LABELS[comparison['track']]
    try:
        for i, bar in enumerate(bars):
            color = '#46d3aa' if bar.close >= bar.open else '#fa7b8a'
            ax.vlines(i, bar.low, bar.high, color=color, linewidth=.8)
            ax.add_patch(Rectangle((i - .31, min(bar.open, bar.close)), .62,
                                   max(abs(bar.close - bar.open), abs(bar.close) * .00001), color=color))
        lo, hi = event['band']
        ax.axhspan(lo, hi, color=GOLD, alpha=.24, label='본 알림 구간')
        main_bands = [(lo, hi)]
        if event['level'] == 3:
            first_low, first_high = event['overlap']['band']
            main_bands.append((first_low, first_high))
            ax.axhspan(first_low, first_high, color=GOLD, alpha=.15, label='본 신호 첫 접촉 구간')
        common = set()
        for i, (low, high, _) in enumerate(comparison['bands']):
            ax.axhspan(low, high, color=REFERENCE, alpha=.25, label='비교 구간' if not i else None)
            common.update((max(a, low), min(b, high)) for a, b in main_bands if max(a, low) < min(b, high))
        for i, (low, high) in enumerate(sorted(common)):
            ax.axhspan(low, high, facecolor='none', edgecolor=WHITE,
                       hatch='////', label='실제 공통 가격대' if not i else None)
        if points:
            wave_atr = comparison.get('wave_atr') or next(iter(comparison['opposing_atrs']), None)
            ax.plot([positions[p['timestamp']] for p in points], [p['price'] for p in points],
                    color='#78ebff', linewidth=2, marker='o', markersize=4,
                    label=f'{atr_label(wave_atr)} 확정 파동' if wave_atr is not None else '확정 파동')
        ticks = sorted({round(i * (len(bars) - 1) / 5) for i in range(6)})
        ax.set_xticks(ticks, [kst(bars[i].timestamp).replace(' KST', '') for i in ticks])
        ax.set_xlim(-1, len(bars))
        atrs = comparison['opposing_atrs']
        side = '숏' if event['direction'] == 1 else '롱'
        reasons = []
        if atrs:
            reasons.append(f"{side} 구조 ({', '.join(map(atr_label, atrs))})")
        reasons.append('가격대 겹침' if common else '방향 경고')
        ax.set_title(f"{event['symbol']} · {LABELS[tf]} {track} · {' / '.join(reasons)}",
                     color=WHITE, loc='left', fontsize=14, pad=12)
        ax.legend(loc='upper left', facecolor='#152130', labelcolor=WHITE)
        path = prefix.with_name(f"{prefix.name}_star{event['level']}_{comparison['track']}.png")
        fig.savefig(path, dpi=135, facecolor=fig.get_facecolor())
        return str(path.resolve())
    finally:
        plt.close(fig)


def chart_atr(event):
    values = event.get('matched_atrs') or [event.get('stock_atr', event.get('upper_atr'))]
    return '/'.join(f'{float(n):g}' for n in values if n is not None) + ' ATR'


def render(snapshot, alert, frames, prefix):
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    configure_font()
    event = alert['candidate']
    level, now = event['level'], event['now']
    reaction = event.get('reaction')
    top, middle, final = event['path']
    side = 'LONG' if event['direction'] == 1 else 'SHORT'
    fig, axes = plt.subplots(1, 2, figsize=(16, 6.1))
    fig.patch.set_facecolor('#101a27')
    fig.subplots_adjust(left=.035, right=.94, top=.71, bottom=.19, wspace=.17)
    fig.text(.035, .938, '★' * level, color=GOLD, size=23, weight='bold', va='center')
    title = alert_title(event)
    fig.text(.155, .938, title, color=WHITE, size=23, weight='bold', va='center')
    exchange = event.get('exchange', 'bitget').title()
    fig.text(.036, .868, f"{exchange} {event['symbol']}  ·  {TRACK_LABELS[event['track']]} {side}  ·  {kst(now)}",
             color=MUTED, size=12)
    fig.text(.94, .813, contact_status(event), color=GOLD, size=11, ha='right')
    keys = ([Line2D([], [], color='#78ebff', linewidth=2, label='확정 파동'),
             Line2D([], [], color=GOLD, linestyle='--', label='미확정 파동' if reaction else '진행 파동')]
            if level == 2 or is_entry(event) or event.get('formation') or event.get('stock_bars') else [Patch(facecolor=color, label=label) for color, label in zip(DENSITY, ('3개 겹침', '4개 겹침', '5개 이상'))])
    fig.legend(handles=keys,
               loc='center right', bbox_to_anchor=(.94, .876), ncol=3, fontsize=10,
               frameon=False, labelcolor=WHITE, handlelength=1.3, columnspacing=1.2)
    if snapshot.get('illustrative'):
        priced = event.get('execution') or (event.get('observation_risk') or {}).get('plan')
        preview = snapshot.get('preview_label') or ('과거 캔들 · 비용 예시' if priced else '과거 시세 재생 미리보기')
        fig.text(.94, .935, preview, color=MUTED, size=11, ha='right')
    try:
        for ax, tf in zip(axes, (top, final)):
            cash_panel = tf == top and event.get('stock_bars')
            candles = [Bar(**b) for b in cash_panel] if cash_panel else window(frames, tf, event, snapshot['as_of'])
            draw_panel(ax, tf, candles, event)
            upper_label = '계단 · 상위 OB와 목표' if event['upper']['target'] is not None else '상위 구조 · OB 접촉'
            if reaction:
                close = kst(candles[-1].timestamp + TF_MS[tf]).split()[1]
                panel = f"{'상위 구조' if tf == top else '종가 돌파'} · {close} 마감까지"
            else:
                panel = upper_label if tf == top else '계단 · OB 재접촉' if is_entry(event) else '구조 확대 · 접촉 확인'
            if cash_panel:
                panel = f"원주식 {event['stock_reference']} 전체 구조 · {chart_atr(event)}"
            elif event.get('stock_reference') and tf == final:
                panel = 'Bitget 접촉 확대'
            elif tf == top and event.get('upper_atr') is not None:
                panel += ' · ' + chart_atr(event)
            ax.set_title(f'{LABELS[tf]} {panel}', color=WHITE, loc='left', fontsize=13, pad=29)
        if reaction:
            message = (f"{LABELS[final]}봉 {kst(reaction['at'])} 마감 · 종가 {fmt(reaction['price'])}"
                       f" · 돌파 기준 {fmt(reaction['level'])}")
        elif event.get('phase') == 'stair_retest':
            location = '최초 겹침 안' if event['in_initial_overlap'] else '최초 겹침 밖의 후속 OB'
            message = f"{LABELS[final]} 계단 OB 재접촉 · {location} · 비용 반영 1:{event['rr']:.2f}"
        elif level == 1 and not event.get('formation'):
            message = f'{LABELS[top]} OB 반응 안에서 같은 방향의 {LABELS[final]} OB 접촉'
        elif level <= 2:
            core = primary_overlap(event, event['sources'])
            kind = core[3] if core else 'OB/FVG'
            message = f'{LABELS[top]} OB × {LABELS[middle]} {kind} × {LABELS[final]} OB · 핵심 3중 겹침'
            if event.get('formation'):
                message += ' · 계단은 이후 확인'
        else:
            message = f"{LABELS[top]} 계단 · 3중 겹침 · {LABELS[final]} 계단과 재접촉    |    비용 반영 1:{event['rr']:.2f}"
        fig.text(.035, .112, message, color=WHITE, size=13, weight='bold')
        lo, hi = event['band']
        detail = f'접촉 구간 {fmt(lo)} ~ {fmt(hi)}'
        if is_entry(event):
            detail = f"진입 {fmt(lo)} ~ {fmt(hi)}     손절 {fmt(event['stop'])}     목표 {fmt(event['target'])}"
            if x := event.get('execution'):
                detail += f"     편도 슬리피지 {x['slippage_per_side']:.3%} · {x['notional']:,.0f} USDT 기준"
        elif risk := event.get('observation_risk'):
            if plan := risk['plan']:
                lo, hi = plan['band']
                stop_kind = '구조' if 'pivot_at' in risk else 'OB'
                detail = (f"참고 진입 {fmt(lo)} ~ {fmt(hi)}     {stop_kind} 손절 {fmt(risk['stop'])}"
                          f"     상위 직전 목표 {fmt(risk['target'])}     비용 반영 1:{plan['rr']:.2f}")
            elif level == 2:
                detail += '     ·     ' + ('비용 반영 1:3 미충족' if risk.get('costs_checked') else '비용 확인 대기 · 1:3 미확정')
        fig.text(.035, .064, detail, color=MUTED, size=11)
        if event.get('stock_reference'):
            age = (event.get('price_basis') or {}).get('quote_age_ms', 0) // 60000
            source = '상위 Nasdaq · 하위/접촉 Bitget · USDT 환산'
            if age:
                source += f' · 환산 기준 {age}분 전 체결'
            fig.text(.035, .025, source, color=MUTED, size=9)
        prefix = Path(prefix)
        path = prefix.with_name(f'{prefix.name}_star{level}.png')
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=145, facecolor=fig.get_facecolor())
    finally:
        plt.close(fig)
    images = [str(path.resolve())]
    return {'images': images, 'image_captions': [''] * len(images), 'text_after_images': unit_text(event)}
