"""Two review photos: independent broad/recent structures on the same 4H."""
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Patch
from src.ict_engine import TF_MS
from src.ict_overlap import kst,LABELS
from src.overlap_charts import configure_font, box_color

COLORS={'broad':'#bd9bf7','recent':'#5ac9db'}
REGIMES={'up':'상승 구조','down':'하락 구조','mixed':'혼합 구조'}


def render_review(result,frames,folder,label_prefix=''):
    configure_font();folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    tf,lower=result.get('pair',('4h','1h'))
    all_bars=frames[tf];paths=[]
    for scale,label in (('broad','큰 틀'),('recent','최근 틀')):
        view=result['views'][scale]
        # The recent lens zooms the same 4H data. It is not a lower timeframe.
        bars=all_bars if scale=='broad' else all_bars[-{'4h':270,'1d':180,'1w':52}[tf]:]
        origin=bars[0].timestamp;n=len(bars);end=n+max(12,n*.06)
        x=lambda stamp:(stamp-origin)/TF_MS[tf]
        fig=plt.figure(figsize=(15,8.5),layout='constrained')
        grid=fig.add_gridspec(2,1,height_ratios=[5.8,1.5])
        ax=fig.add_subplot(grid[0]);note=fig.add_subplot(grid[1]);note.axis('off')
        fig.patch.set_facecolor('#111722');ax.set_facecolor('#18202d')
        try:
            color=COLORS[scale];handles=[];known=[];summaries=[]
            shown=[]
            for d in (1,-1):
                choices=[a for a in view['selected'] if a['direction']==d]
                if choices:shown.append(choices[0])
            for index,a in enumerate(shown,1):
                left=max(-.5,x(a['origin']));side='지지' if a['direction']==1 else '저항'
                known.extend([a['low'],a['high']])
                ax.add_patch(Rectangle((left,a['low']),end-left,a['high']-a['low'],
                                      facecolor=color,edgecolor='none',alpha=.10,zorder=1))
                ax.add_patch(Rectangle((left,a['low']),end-left,a['high']-a['low'],
                                      facecolor='none',edgecolor=color,linewidth=.8,zorder=2))
                ax.text(end,a['high'],f" {index}. {side} 후보",ha='right',va='bottom',
                        color=color,fontsize=9)
                best=a['overlaps'][0] if a['overlaps'] else None
                count=best['score'] if best else 0
                mid_status='확정 파동' if a['range_complete'] else '진행 파동'
                line=(f"{index}. {side} 후보 {a['low']:,.1f} ~ {a['high']:,.1f}  |  "
                      f"실제 OB/FVG 중첩 {count}/4  |  해당 파동 0.5 {a['midpoint']:,.1f} ({mid_status})")
                summaries.append(line)
                if best:
                    for z in best['sources']:
                        zl=max(z['low'],a['low']);zh=min(z['high'],a['high'])
                        if zl>=zh:continue
                        zleft=max(-.5,x(z['origin']));zc=box_color(z)
                        ax.add_patch(Rectangle((zleft,zl),end-zleft,zh-zl,
                                              facecolor=zc,edgecolor=zc,linewidth=.45,alpha=.25,zorder=2))
                        tag=f"{LABELS[z['timeframe']]} {z['kind']}"
                        if tag not in [h.get_label() for h in handles]:
                            handles.append(Patch(facecolor=zc,edgecolor=zc,label=tag,alpha=.7))
                    left=max(-.5,x(best['latest_source']))
                    ax.add_patch(Rectangle((left,best['low']),end-left,best['high']-best['low'],
                                          facecolor='none',edgecolor='white',linewidth=.75,zorder=4))
                    gate=best['equilibrium'];phase='0.5 접근 조건 충족' if gate['eligible'] else '0.5 접근 조건 미충족'
                    summaries.append(f"   공통 {best['low']:,.1f} ~ {best['high']:,.1f}  ·  {phase}")
            handles.insert(0,Patch(facecolor=color,edgecolor=color,alpha=.4,label=f'{label} 구조 후보 · OB 판정과 별도'))
            if any(a['overlaps'] for a in shown):handles.append(Patch(facecolor='none',edgecolor='white',label=f'실제 {LABELS[tf]}·{LABELS[lower]} 공통 중첩'))
            span=max(b.high for b in bars)-min(b.low for b in bars)
            for i,b in enumerate(bars):
                c='#30c59a' if b.close>=b.open else '#ee6677'
                ax.vlines(i,b.low,b.high,color=c,linewidth=.5,zorder=3)
                ax.add_patch(Rectangle((i-.28,min(b.open,b.close)),.56,max(abs(b.open-b.close),span*.00002),
                                       facecolor=c,edgecolor='none',zorder=3))
            points=[p for p in view['turns'] if all_bars[p['index']].timestamp>=origin]
            if points:
                ax.plot([x(all_bars[p['index']].timestamp) for p in points],[p['price'] for p in points],
                        color=color,linewidth=1.2,alpha=.85,zorder=5)
                ax.scatter([x(all_bars[p['index']].timestamp) for p in points],[p['price'] for p in points],
                           color=color,s=9,zorder=5)
            # Three most recent visible raids per lens; all detected events
            # remain in JSON. Target lines start at the actual older turn.
            visible=[s for s in view.get('sweeps',[]) if all_bars[s['index']].timestamp>=origin]
            sweep_color='#f8cf57';mss_color='#fb9ac5'
            for number,s in enumerate(visible[-3:],1):
                sx=x(all_bars[s['index']].timestamp)
                left=max(-.5,x(s['origin']));low_side=s['direction']==1
                ax.hlines(s['level'],left,sx,color=sweep_color,linewidth=.9,linestyle='--',zorder=6)
                ax.scatter([sx],[s['tip']],s=60,marker='D',color=sweep_color,edgecolor='#111722',linewidth=.6,zorder=8)
                offset=(12,-34-number*4) if low_side else (-12,30+number*4)
                sweep_label=f"{'저점' if low_side else '고점'} 스윕 {s['level']:,.0f}"
                if s.get('status')=='raid_tip_close_broken':sweep_label+='\n이후 종가 재이탈'
                ax.annotate(sweep_label,
                            xy=(sx,s['tip']),xytext=offset,textcoords='offset points',
                            ha='left' if low_side else 'right',fontsize=9,color=sweep_color,
                            arrowprops={'arrowstyle':'->','color':sweep_color,'linewidth':.8},zorder=9,
                            bbox={'facecolor':'#18202d','edgecolor':'none','alpha':.8,'pad':2})
                if s.get('mss_index') is not None:
                    mb=all_bars[s['mss_index']];mx=x(mb.timestamp)
                    ax.hlines(s['mss_level'],max(-.5,x(all_bars[s['mss_origin_index']].timestamp)),mx,
                              color=mss_color,linestyle=':',linewidth=.7,zorder=5)
                    ax.scatter([mx],[mb.close],s=40,marker='s',color=mss_color,zorder=8)
                    ax.annotate('종가 MSS',xy=(mx,mb.close),xytext=(8,20 if low_side else -24),
                                textcoords='offset points',fontsize=8,color=mss_color,zorder=9,
                                arrowprops={'arrowstyle':'->','color':mss_color,'linewidth':.7})
            if visible:
                from matplotlib.lines import Line2D
                handles.append(Line2D([],[],marker='D',color=sweep_color,linestyle='--',linewidth=.7,
                                      label='확정 고저점 스윕 · 종가 복귀'))
                if any(s.get('mss_index') is not None for s in visible[-3:]):
                    handles.append(Line2D([],[],marker='s',color=mss_color,linestyle=':',linewidth=.7,
                                          label='스윕 이후 종가 MSS'))
            ax.axhline(result['price'],color='#f0d296',linestyle=':',linewidth=.8)
            ax.text(end,result['price'],f"  현재가 {result['price']:,.1f}",ha='right',va='bottom',color='#f0d296',fontsize=9)
            limits=[b.low for b in bars]+[b.high for b in bars]+known+[result['price']]
            lo,hi=min(limits),max(limits);pad=(hi-lo)*.08
            ax.set_ylim(lo-pad,hi+pad);ax.set_xlim(-1,end+1)
            ax.grid(alpha=.10,color='white');ax.set_axisbelow(True);ax.tick_params(colors='#bac6d4')
            ax.yaxis.tick_right()
            ticks=list(range(0,n,max(1,n//7)))
            ax.set_xticks(ticks,[kst(bars[i].timestamp).split(' ')[0] for i in ticks],fontsize=9)
            ax.legend(handles=handles,loc='upper left',fontsize=8,ncol=min(3,len(handles)),
                      facecolor='#111722',edgecolor='#344154',labelcolor='#dce2eb',framealpha=.9)
            both=' · '.join(f"{'큰 틀' if k=='broad' else '최근 틀'} {REGIMES[v['structure']]}" for k,v in result['views'].items())
            fig.suptitle(f"Bitget · {result['symbol']} · {label_prefix}{LABELS[tf]} {label}\n{both}\n{kst(result['as_of'])} · 완성 봉만 분석",
                         color='#e3e8f0',fontsize=14)
            note.text(.005,.95,'\n'.join(summaries) if summaries else '유효한 구조 후보 없음',
                      color='#d8e2ef',fontsize=10,va='top',linespacing=1.6)
            sweep_note=(f"이 화면의 {label} 스윕 {len(visible)}건 · 최근 최대 3건 표시 · 노랑: 기준선 돌파 후 종가 복귀 / 분홍: 종가 MSS"
                        if visible else f"이 화면의 {label} 확정 고저점 기준 스윕 없음")
            note.text(.005,.05,sweep_note,
                      color='#99a8bc',fontsize=9,va='bottom')
            path=folder/f"{result['symbol']}_{tf}_{scale}.png";fig.savefig(path,dpi=140,facecolor=fig.get_facecolor());paths.append(path)
        finally:plt.close(fig)
    return paths


def render_structure_pair(snapshot,events,frames,prefix):
    """Exactly two photos per symbol, only scopes with undelivered alerts."""
    from config.overlap_settings import TRACKS,TRACK_LABELS
    configure_font();prefix=Path(prefix);prefix.parent.mkdir(parents=True,exist_ok=True)
    tracks=[t for t in TRACKS if any(e['candidate']['track']==t for e in events)]
    if not tracks:raise ValueError('No eligible structure scope')
    paths=[]
    for overview,suffix,heading in ((True,'overview','알림 구간 전체'),(False,'detail','알림 구간 확대')):
        fig,axes=plt.subplots(len(tracks),1,figsize=(14,5.8*len(tracks)),squeeze=False,layout='constrained')
        fig.patch.set_facecolor('#111722')
        try:
            for track,row in zip(tracks,axes):
                own=[e['candidate'] for e in events if e['candidate']['track']==track]
                draw_notified_scope(row[0],own,snapshot['tracks'][track]['analysis'],frames,overview,TRACK_LABELS[track])
            fig.suptitle(f"Bitget · {snapshot['symbol']} · {heading}\n{kst(snapshot['as_of'])} · 흰 테두리 = 이번 알림 구간",
                         color='#e3e8f0',fontsize=13)
            path=prefix.with_name(prefix.name+'_'+suffix+'.png');fig.savefig(path,dpi=140,facecolor=fig.get_facecolor());paths.append(path)
        finally:plt.close(fig)
    return paths


def draw_notified_scope(ax,own,analysis,frames,overview,track_label,show_zone_text=True,sweep_limit=2,detail_bars=180):
    """Only delivered candidates and their evidence; no other watch boxes."""
    top,lower=own[0]['pair'];tf=top if overview else lower
    from src.anchored_overlap import analysis_frames
    frames=analysis_frames(analysis,frames)
    window=own[0].get('parent_window')
    internal=bool(window and not overview)
    all_bars=frames[tf]
    bars=([b for b in all_bars if window['start']<=b.timestamp and b.timestamp+TF_MS[tf]<=window['end']]
          if internal else all_bars if overview else all_bars[-detail_bars:])
    if not bars:raise ValueError('Parent candle child data missing')
    if internal and len(bars)!=window['child_bars']:raise ValueError('Incomplete parent candle children')
    origin=bars[0].timestamp;n=len(bars);end=n+.65 if internal else n+max(8,n*.05)
    x=lambda stamp:(stamp-origin)/TF_MS[tf]
    ax.set_facecolor('#18202d');ax.tick_params(colors='#bac6d4');ax.yaxis.tick_right()
    ax.grid(alpha=.10,color='white');ax.set_axisbelow(True)
    span=max(b.high for b in bars)-min(b.low for b in bars)
    for i,b in enumerate(bars):
        color='#30c59a' if b.close>=b.open else '#ee6677'
        ax.vlines(i,b.low,b.high,color=color,linewidth=.55,zorder=3)
        ax.add_patch(Rectangle((i-.28,min(b.open,b.close)),.56,max(abs(b.open-b.close),span*.00002),
                               facecolor=color,edgecolor='none',zorder=3))
    handles=[];known=[] if internal else [own[0]['price']];titles=[];seen_sources=set();seen_sweeps=set()
    for c in own:
        lens='·'.join('큰 틀' if s=='broad' else '작은 틀' for s in c['lenses'])
        side='지지 LONG' if c['direction']==1 else '저항 SHORT'
        titles.append(f"{track_label} · {lens} · {side} · 중첩 {c['score']}/4")
        for z in c['sources']:
            if z['id'] in seen_sources:continue
            seen_sources.add(z['id']);color=box_color(z)
            # Evidence remains within this notified structural candidate.
            if c.get('parent_window'):low,high=z['low'],z['high']
            else:low,high=max(z['low'],c['structure_area']['low']),min(z['high'],c['structure_area']['high'])
            left=max(-.5,x(z['origin']));known.extend((low,high))
            ax.add_patch(Rectangle((left,low),end-left,high-low,facecolor=color,edgecolor='none',alpha=.12,zorder=1))
            ax.add_patch(Rectangle((left,low),end-left,high-low,facecolor='none',edgecolor=color,linewidth=.6,zorder=2))
            name=f"{LABELS[z['timeframe']]} {z['kind']}"
            if name not in [h.get_label() for h in handles]:handles.append(Patch(facecolor=color,edgecolor=color,alpha=.5,label=name))
        common_origin=(max(z['origin'] for z in c['sources'] if z['timeframe']==lower)
                       if internal else c['latest_source'])
        left=max(-.5,x(common_origin));known.extend((c['low'],c['high']))
        ax.add_patch(Rectangle((left,c['low']),end-left,c['high']-c['low'],facecolor='#ffffff',edgecolor='none',alpha=.10,zorder=2))
        ax.add_patch(Rectangle((left,c['low']),end-left,c['high']-c['low'],facecolor='none',edgecolor='white',linewidth=.9,zorder=5))
        if show_zone_text:
            ax.annotate(f"알림 {c['low']:,.4f} ~ {c['high']:,.4f}",xy=(end,(c['low']+c['high'])/2),
                        xytext=(-4,10),textcoords='offset points',ha='right',fontsize=9,color='white',zorder=8,
                        bbox={'facecolor':'#18202d','edgecolor':'none','alpha':.85,'pad':2})
        chosen='broad' if overview and 'broad' in c['lenses'] else 'recent' if 'recent' in c['lenses'] else c['lenses'][0]
        view=analysis['views'][chosen];top_bars=frames[top]
        points=[p for p in view['turns'] if origin<=top_bars[p['index']].timestamp<=bars[-1].timestamp]
        if points:
            ax.plot([x(top_bars[p['index']].timestamp) for p in points],[p['price'] for p in points],
                    color=COLORS[chosen],linewidth=.85,alpha=.65,zorder=4)
        area=c['structure_area']
        related=[s for s in view['sweeps'] if s['direction']==c['direction'] and
                 s['timestamp']>=area['origin'] and
                 area['low']<=s['tip']<=area['high'] and origin<=top_bars[s['index']].timestamp<=bars[-1].timestamp]
        for s in related[-sweep_limit:]:
            sid=(s['timestamp'],s['level'])
            if sid in seen_sweeps:continue
            seen_sweeps.add(sid);sx=x(top_bars[s['index']].timestamp)
            ax.hlines(s['level'],max(-.5,x(s['origin'])),sx,color='#f8cf57',linestyle='--',linewidth=.7)
            ax.scatter([sx],[s['tip']],marker='D',s=40,color='#f8cf57',zorder=7)
            ax.annotate('스윕',xy=(sx,s['tip']),xytext=(5,-20),textcoords='offset points',color='#f8cf57',fontsize=8)
            if s['mss_index'] is not None:
                b=top_bars[s['mss_index']];mx=x(b.timestamp)
                ax.scatter([mx],[b.close],marker='s',s=30,color='#fb9ac5',zorder=7)
                ax.annotate('종가 MSS',xy=(mx,b.close),xytext=(5,12),textcoords='offset points',color='#fb9ac5',fontsize=8)
    price=own[0]['price']
    if not internal:
        ax.axhline(price,color='#f0d296',linestyle=':',linewidth=.8)
        ax.text(end,price,f"현재가 {price:,.2f}",ha='right',va='bottom',color='#f0d296',fontsize=9)
    if window and overview:
        left=x(window['start']);right=x(window['end'])
        ax.axvspan(left,right,color='#f8cf57',alpha=.15,zorder=1)
        parent_bar=next((b for b in bars if b.timestamp==window['start']),None)
        if parent_bar:
            ax.annotate(f"기준 {LABELS[top]}봉",xy=(left,(parent_bar.high+parent_bar.low)/2),
                        xytext=(-35,35),textcoords='offset points',color='#f8cf57',fontsize=9,
                        arrowprops={'arrowstyle':'->','color':'#f8cf57','linewidth':.8},zorder=9)
    limits=[b.low for b in bars]+[b.high for b in bars]+known
    lo,hi=min(limits),max(limits);pad=max((hi-lo)*.09,price*.0005)
    ax.set_ylim(lo-pad,hi+pad);ax.set_xlim(-.7 if internal else -1,end+.2 if internal else end+1)
    ticks=list(range(0,n,max(1,n//6)))
    ax.set_xticks(ticks,[kst(bars[i].timestamp).replace(' KST','') for i in ticks],fontsize=8)
    ax.set_title('\n'.join(dict.fromkeys(titles))+f" · {LABELS[tf]}",loc='left',fontsize=11,color='#e3e8f0')
    if handles:ax.legend(handles=handles,loc='upper left',ncol=min(4,len(handles)),fontsize=8,
                         facecolor='#111722',edgecolor='#344154',labelcolor='#dce2eb',framealpha=.9)
