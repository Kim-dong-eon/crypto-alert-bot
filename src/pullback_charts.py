"""Approved image: upper structure / lower detail; no trade risk prices."""
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle,Patch
from src.ict_engine import TF_MS
from src.ict_overlap import LABELS,kst
from src.overlap_charts import configure_font,box_color
from src.pullback_alerts import unit_text


def origin_window(bars,candidate,leading=3):
    """Retain the root and real OB source, regardless of their age."""
    area=candidate['structure_area'];parent=candidate['parent']
    start=min(parent['origin'],area.get('origin',parent['origin']),
              area.get('pivot_origin',parent['origin']))
    index=next((i for i,b in enumerate(bars) if b.timestamp>=start),None)
    if index is None or not any(b.timestamp==parent['origin'] for b in bars):
        raise ValueError('Original OB candle missing from chart history')
    return bars[max(0,index-leading):]


def render_unit(snapshot,event,frames,prefix):
    if event['candidate'].get('trade_setup'):
        from src.staircase_charts import render
        return render(snapshot,event,frames,prefix)
    configure_font();c=event['candidate'];lens=c['lenses'][0]
    if len(c['lenses'])!=1:raise ValueError('One lens per notification image')
    top=c['pair'][0];detail=c.get('refinement_tf',c['pair'][1]);now=snapshot['as_of'];price=c['price']
    analysis=snapshot['tracks'][c['track']]['analysis'];view=analysis['views'][lens]
    origin=analysis['frame_starts'][top]
    top_all=[b for b in frames[top] if b.timestamp>=origin and b.timestamp<=now]
    confirmed=[b for b in top_all if b.timestamp+TF_MS[top]<=now]
    latest_top=origin_window(top_all,c)
    latest_detail=[b for b in frames[detail] if b.timestamp<=now][-(48 if detail!='1d' else 60):]
    if not latest_top or not latest_detail:raise ValueError('Chart candles unavailable')
    left_width=min(16,max(9.3,len(latest_top)/25))
    fig,axes=plt.subplots(1,2,figsize=(left_width+9.3,8),gridspec_kw={'width_ratios':[left_width,9.3]},layout='constrained');fig.patch.set_facecolor('#101722')
    for ax,tf,bars in zip(axes,(top,detail),(latest_top,latest_detail)):
        first=bars[0].timestamp;step=TF_MS[tf];n=len(bars);right=n+max(4,n*.05);x=lambda t:(t-first)/step
        ax.set_facecolor('#18212e');ax.grid(color='white',alpha=.085);ax.set_axisbelow(True);ax.tick_params(colors='#b7c3d2');ax.yaxis.tick_right()
        handles=[]
        for z in sorted(c['sources'],key=lambda z:(-TF_MS[z['timeframe']],z['kind'])):
            color=box_color(z);left=max(-.5,x(z['origin']))
            if left>right:continue
            ax.add_patch(Rectangle((left,z['low']),right-left,z['high']-z['low'],facecolor=color,edgecolor='none',alpha=.12,zorder=1))
            ax.add_patch(Rectangle((left,z['low']),right-left,z['high']-z['low'],facecolor='none',edgecolor=color,linewidth=.65,zorder=2))
            label=f"{LABELS[z['timeframe']]} {z['kind']}"
            if label not in [h.get_label() for h in handles]:handles.append(Patch(facecolor=color,edgecolor=color,alpha=.6,label=label))
        low,high=c['low'],c['high'];known=max(c['formed_at'],c['latest_source']);left=max(-.5,x(known))
        if left<=right:
            ax.add_patch(Rectangle((left,low),right-left,high-low,facecolor='white',edgecolor='white',linewidth=.7,alpha=.3,zorder=3))
            ax.hlines((low+high)/2,left,right,color='white',linewidth=.65,zorder=3)
        ax.text(right,high,'이번 알림 중첩',ha='right',va='bottom',color='white',fontsize=10,
                bbox={'facecolor':'#101722','edgecolor':'none','alpha':.9,'pad':3},zorder=10)
        for i,b in enumerate(bars):
            live=b.timestamp+step>now;color='#35c69d' if b.close>=b.open else '#ef7080'
            ax.vlines(i,b.low,b.high,color=color,linewidth=.85,zorder=4)
            ax.add_patch(Rectangle((i-.28,min(b.open,b.close)),.56,max(abs(b.open-b.close),price*.000003),facecolor=color,edgecolor='#f5cf63' if live else color,linewidth=1.3 if live else .2,zorder=5))
        if tf==top:
            area=c['structure_area'];parent=c['parent']
            parent_bar=next(b for b in bars if b.timestamp==parent['origin'])
            px=x(parent_bar.timestamp);color=box_color(parent)
            ax.axvline(px,color=color,linestyle=':',linewidth=.7,alpha=.55,zorder=2)
            ax.add_patch(Rectangle((px-.36,min(parent_bar.open,parent_bar.close)),.72,
                                   max(abs(parent_bar.open-parent_bar.close),price*.000003),
                                   facecolor='none',edgecolor=color,linewidth=1.5,zorder=8))
            offset=32 if px<n*.55 else -32
            ax.annotate(f"OB 기준봉 · {kst(parent_bar.timestamp).replace(' KST','')}",
                        xy=(px,(parent_bar.open+parent_bar.close)/2),xytext=(offset,65),textcoords='offset points',
                        ha='left' if offset>0 else 'right',color=color,fontsize=10,
                        arrowprops={'arrowstyle':'->','color':color,'linewidth':.9},
                        bbox={'facecolor':'#101722','edgecolor':'none','alpha':.9,'pad':3},zorder=10)
            pivot=next((b for b in bars if b.timestamp==area.get('pivot_origin')),None)
            if pivot:
                point=pivot.low if c['direction']==1 else pivot.high
                sx=x(pivot.timestamp)
                ax.scatter([sx],[point],marker='o',s=42,facecolor='none',edgecolor='#59d5df',linewidth=1.3,zorder=8)
                ax.annotate('출발 파동 시작',xy=(sx,point),xytext=(22,30 if c['direction']==1 else -38),textcoords='offset points',
                            ha='left',color='#59d5df',fontsize=10,arrowprops={'arrowstyle':'->','color':'#59d5df','linewidth':.8},
                            bbox={'facecolor':'#101722','edgecolor':'none','alpha':.9,'pad':3},zorder=10)
            points=view['turns']
            visible=[p for p in points if first<=confirmed[p['index']].timestamp<=bars[-1].timestamp]
            ax.plot([x(confirmed[p['index']].timestamp) for p in visible],[p['price'] for p in visible],color='#59d5df',linewidth=1.1,zorder=6)
            if points:
                last=points[-1];start=confirmed[last['index']].timestamp;leg=[b for b in top_all if b.timestamp>=start]
                extreme=max(leg,key=lambda b:b.high) if last['kind']=='low' else min(leg,key=lambda b:b.low)
                ax.plot([x(start),x(extreme.timestamp),x(bars[-1].timestamp)],
                        [last['price'],extreme.high if last['kind']=='low' else extreme.low,bars[-1].close],
                        color='#f5cf63',linestyle='--',linewidth=1,zorder=6)
            area=c['structure_area'];bos=area.get('bos_at');level=area.get('bos_level')
            if bos and level is not None:
                match=next((b for b in confirmed if b.timestamp+step==bos),None)
                if match and first<=match.timestamp:
                    bx=x(match.timestamp);ax.hlines(level,max(-.5,bx-22),bx,color='#59d5df',linestyle='--',linewidth=.75)
                    ax.scatter([bx],[match.close],s=34,color='#59d5df',zorder=8)
                    near_left=bx<n*.3
                    ax.annotate('이전 고점 위 종가 갱신' if c['direction']==1 else '이전 저점 아래 종가 갱신',xy=(bx,match.close),xytext=(25 if near_left else -22,35),textcoords='offset points',ha='left' if near_left else 'right',color='#59d5df',fontsize=10,
                                arrowprops={'arrowstyle':'->','color':'#59d5df','linewidth':.8},bbox={'facecolor':'#101722','edgecolor':'none','alpha':.9,'pad':3},zorder=10)
            sweeps=[e for e in view['sweeps'] if first<=confirmed[e['index']].timestamp<=bars[-1].timestamp]
            for e in sweeps[-1:]:
                sx=x(confirmed[e['index']].timestamp)
                ax.hlines(e['level'],max(-.5,x(e['origin'])),sx,color='#f5cf63',linestyle='--',linewidth=.7)
                ax.scatter([sx],[e['tip']],marker='D',s=32,color='#f5cf63',zorder=8)
                ax.annotate('확인된 고점 스윕' if e['direction']==-1 else '확인된 저점 스윕',xy=(sx,e['tip']),xytext=(-5,16 if e['direction']==-1 else -23),textcoords='offset points',ha='right',color='#f5cf63',fontsize=9,
                            bbox={'facecolor':'#101722','edgecolor':'none','alpha':.85,'pad':2},zorder=10)
                if e.get('mss_index') is not None:
                    b=confirmed[e['mss_index']]
                    if b.timestamp>=first:
                        ax.scatter([x(b.timestamp)],[b.close],marker='s',s=28,color='#ed93c7',zorder=8)
                        ax.annotate('종가 MSS',xy=(x(b.timestamp),b.close),xytext=(6,17),textcoords='offset points',fontsize=9,color='#ed93c7',zorder=9)
        else:
            touch=c['entry_touch'];stamp=touch.get('bar_open',touch['at'])
            sx=min(n-.4,max(0,x(stamp)))
            ax.scatter([sx],[(low+high)/2],marker='D',s=45,color='#f5cf63',zorder=9)
            ax.annotate('구간 접촉 확인',xy=(sx,(low+high)/2),xytext=(-35,54),textcoords='offset points',ha='right',color='#f5cf63',fontsize=11,
                        arrowprops={'arrowstyle':'->','color':'#f5cf63'},bbox={'facecolor':'#101722','edgecolor':'none','alpha':.9,'pad':3},zorder=10)
        mid=c['equilibrium']['midpoint']
        ax.axhline(mid,color='#a5b4c7',linestyle='--',linewidth=.65,zorder=2)
        ax.text(.01,mid,'구역 기준 0.5',transform=ax.get_yaxis_transform(),va='bottom',color='#a5b4c7',fontsize=9,clip_on=True)
        ax.axhline(price,color='#f0d19c',linestyle=':',linewidth=.8,zorder=6)
        ax.text(right,price,f'확인 시세 {price:,.4f}'.rstrip('0').rstrip('.'),ha='right',va='top',color='#f0d19c',fontsize=10)
        parent=c['parent'];limits=[b.low for b in bars]+[b.high for b in bars]+[parent['low'],parent['high'],price]
        if tf==top:limits.append(mid)
        lo,hi=min(limits),max(limits);pad=max((hi-lo)*.13,price*.0003)
        ax.set_ylim(lo-pad,hi+pad);ax.set_xlim(-1,right+.5)
        ticks=list(range(0,n,max(1,n//5)));ax.set_xticks(ticks,[kst(bars[i].timestamp).replace(' KST','') for i in ticks],fontsize=9)
        if handles:ax.legend(handles=handles,loc='upper left',ncol=2,fontsize=9,facecolor='#101722',edgecolor='#344154',labelcolor='#dce5ef')
        title=f'{LABELS[top]} 출발 구역부터 현재 · {n}봉' if tf==top else f'{LABELS[detail]} 확대 · 하위 중첩과 접촉'
        ax.set_title(title,loc='left',color='#e2e9f3',fontsize=12,pad=12)
    text=unit_text(event);lines=text.splitlines()
    fig.suptitle(lines[0]+' '+lines[1]+'\n'+lines[2],color='#edf2f8',fontsize=14)
    inside=c['low']<=price<=c['high'];location='현재 중첩 안' if inside else '접촉 후 현재 중첩 밖'
    fig.supxlabel(f"{kst(now)} · {location} · 0.5 {'이하' if c['direction']==1 else '이상'}\nOB 원본 {kst(c['parent']['origin'])} · 출발부터 현재까지 표시 / 흰 테두리: 이번 중첩 / 노란 점선: 진행 파동",color='#c5d1df',fontsize=10)
    visible=[t.get_text() for t in fig.texts]+[t.get_text() for ax in axes for t in ax.texts]
    if any(word in item for item in visible+[text] for word in ('손절','익절','3R')):raise ValueError('Unapproved trade risk annotation')
    prefix=Path(prefix);prefix.parent.mkdir(parents=True,exist_ok=True);path=prefix.with_name(prefix.name+'_side_by_side.png')
    try:fig.savefig(path,dpi=145,facecolor=fig.get_facecolor())
    finally:plt.close(fig)
    return {'images':[str(path.resolve())],'image_captions':[''],'text_after_images':text,
            'track':c['track'],'lens':lens,'source_event_id':c['id']}
