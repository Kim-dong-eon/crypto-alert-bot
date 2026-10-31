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
    from PIL import Image
    from config.overlap_settings import TRACKS,TRACK_LABELS
    prefix=Path(prefix);rows=[]
    wanted={e['candidate']['track'] for e in events}
    for track in TRACKS:
        if track not in wanted:continue
        review=snapshot['tracks'][track]['analysis']
        rows.append(render_review(review,frames,prefix.parent/track,TRACK_LABELS[track]+' · '))
    if not rows:raise ValueError('No eligible structure scope')
    paths=[]
    for column,label in enumerate(('overview','detail')):
        images=[Image.open(row[column]).convert('RGB') for row in rows]
        try:
            canvas=Image.new('RGB',(max(i.width for i in images),sum(i.height for i in images)),(17,23,34))
            offset=0
            for i in images:canvas.paste(i,(0,offset));offset+=i.height
            path=prefix.with_name(prefix.name+'_'+label+'.png');canvas.save(path);paths.append(path)
        finally:
            for i in images:i.close()
    return paths
