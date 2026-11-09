"""Two readable panels with one complete wave before each selected leg."""
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle,Patch
from src.ict_engine import TF_MS
from src.ict_overlap import LABELS,kst
from src.overlap_charts import configure_font,box_color


def wave_window(bars,stair,source_origin=None,leading=5):
    start=min(stair['chart_start'],source_origin) if source_origin is not None else stair['chart_start']
    if not bars or bars[0].timestamp>start:raise ValueError('Previous wave is missing; do not crop it away')
    index=next((i for i,b in enumerate(bars) if b.timestamp>=start),None)
    if index is None:raise ValueError('Staircase lies outside chart history')
    return bars[max(0,index-leading):]


def render(snapshot,event,frames,prefix):
    from src.pullback_alerts import unit_text
    configure_font();c=event['candidate'];s=c['trade_setup'];now=snapshot['as_of'];d=c['direction']
    top,lower=c['pair'][0],c['refinement_tf']
    windows=[wave_window([b for b in frames[top] if b.timestamp<=now],s['upper'],c['parent']['origin']),
             wave_window([b for b in frames[lower] if b.timestamp<=now],s['lower'])]
    fig,axes=plt.subplots(1,2,figsize=(22,8.6),layout='constrained');fig.patch.set_facecolor('#101722')
    fmt=lambda v:f'{v:,.4f}'.rstrip('0').rstrip('.')
    try:
        for panel,(ax,tf,bars,stair) in enumerate(zip(axes,(top,lower),windows,(s['upper'],s['lower']))):
            first=bars[0].timestamp;step=TF_MS[tf];n=len(bars);right=n+max(4,n*.045)
            x=lambda t:(t-first)/step
            ax.set_facecolor('#18212e');ax.grid(color='white',alpha=.085);ax.set_axisbelow(True)
            ax.tick_params(colors='#b7c3d2');ax.yaxis.tick_right()
            for i,b in enumerate(bars):
                live=b.timestamp+step>now;color='#35c69d' if b.close>=b.open else '#ef7080'
                ax.vlines(i,b.low,b.high,color=color,linewidth=.75,zorder=4)
                ax.add_patch(Rectangle((i-.28,min(b.open,b.close)),.56,max(abs(b.open-b.close),c['price']*.000005),
                                      facecolor=color,edgecolor='#ffda70' if live else color,linewidth=1.3 if live else .2,zorder=5))
            legend=[]
            for z in c['sources']:
                color=box_color(z);left=max(-.5,x(z['origin']))
                if left>right:continue
                ax.add_patch(Rectangle((left,z['low']),right-left,z['high']-z['low'],facecolor=color,alpha=.12,zorder=1))
                ax.add_patch(Rectangle((left,z['low']),right-left,z['high']-z['low'],facecolor='none',edgecolor=color,linewidth=.65,zorder=2))
                name=f"{LABELS[z['timeframe']]} {z['kind']}"
                if name not in [p.get_label() for p in legend]:legend.append(Patch(facecolor=color,alpha=.5,label=name))
            # The previous complete leg remains visible, distinct from the active leg.
            ps=stair['points'];xs=[x(p['timestamp']) for p in ps];ys=[p['price'] for p in ps]
            ax.plot(xs,ys,color='#5ddce7',linewidth=2.2,marker='o',markersize=4,zorder=7)
            ax.axvspan(xs[0],xs[2],color='#7b91b9',alpha=.045,zorder=0)
            labels=(['이전 저점','이전 고점','높아진 저점','고점 갱신'] if d==1 else
                    ['이전 고점','이전 저점','낮아진 고점','저점 갱신'])
            for p,label in zip(ps,labels):
                high=p['kind']=='high'
                ax.annotate(label+'\n'+fmt(p['price']),(x(p['timestamp']),p['price']),xytext=(0,20 if high else -34),
                            textcoords='offset points',ha='center',color='#87eaf1',fontsize=10,
                            bbox={'facecolor':'#101722','edgecolor':'none','alpha':.85,'pad':2},zorder=10)
            if xs[-1]<n-1:
                ax.plot([xs[-1],n-1],[ys[-1],bars[-1].close],color='#f5cf63',linestyle='--',linewidth=1.3,zorder=6)
            broken=next((b for b in bars if b.timestamp+step==stair['break_at']),None)
            if broken:
                ax.scatter([x(broken.timestamp)],[broken.close],marker='s',s=30,color='#eb9fe2',zorder=8)
                ax.annotate('종가 돌파 확인',(x(broken.timestamp),broken.close),xytext=(-20,40) if d==1 else (30,-32),
                            textcoords='offset points',ha='right' if d==1 else 'left',color='#eb9fe2',fontsize=9,
                            arrowprops={'arrowstyle':'->','color':'#eb9fe2'},zorder=10)
            limits=[b.low for b in bars]+[b.high for b in bars]
            if panel==0:
                ax.axhspan(s['ote_low'],s['ote_high'],color='#cdb4fa',alpha=.12,zorder=0)
                ax.text(.02,s['ote_high'],'OTE 62~79%',transform=ax.get_yaxis_transform(),color='#d6bafa',va='bottom',fontsize=10)
                ax.axhline(s['target'],color='#81e1a8',linestyle='--',linewidth=.95)
                ax.text(.98,s['target'],'상위 목표 '+fmt(s['target']),transform=ax.get_yaxis_transform(),ha='right',va='bottom',color='#81e1a8',fontsize=11)
                parent=c['parent'];pb=next((b for b in bars if b.timestamp==parent['origin']),None)
                if pb:
                    ax.annotate('상위 OB 출발',(x(pb.timestamp),(pb.open+pb.close)/2),xytext=(22,-50) if d==1 else (-60,-15),textcoords='offset points',
                                color='#79baff',fontsize=10,arrowprops={'arrowstyle':'->','color':'#79baff'},zorder=10)
                limits += [s['target'],s['ote_low'],s['ote_high']]
                view=snapshot['tracks'][c['track']]['analysis']['views'][c['lenses'][0]]
                confirmed=[b for b in frames[top] if b.timestamp>=snapshot['tracks'][c['track']]['analysis']['frame_starts'][top] and b.timestamp+step<=now]
                raids=[e for e in view.get('sweeps',[]) if first<=e['origin'] and e['index']<len(confirmed)]
                for e in raids[-1:]:
                    b=confirmed[e['index']]
                    ax.scatter([x(b.timestamp)],[e['tip']],marker='D',s=30,color='#ffda70',zorder=8)
                    ax.annotate('과거 고점 스윕' if e['direction']==-1 else '과거 저점 스윕',(x(b.timestamp),e['tip']),
                                xytext=(8,16),textcoords='offset points',color='#ffda70',fontsize=9,zorder=10)
            else:
                touch=c['entry_touch'];stamp=touch.get('bar_open',touch['at'])
                contact=touch.get('observed_price',(c['low']+c['high'])/2)
                contact=min(c['high'],max(c['low'],contact));tx=min(right-1,x(stamp))
                ax.scatter([tx],[contact],marker='D',s=42,color='#f5cf63',zorder=9)
                ax.annotate('접촉 확인',(tx,contact),xytext=(-55,26),textcoords='offset points',ha='right',
                            color='#f5cf63',fontsize=10,arrowprops={'arrowstyle':'->','color':'#f5cf63'},zorder=11)
                for value,label,color in ((s['entry'],'진입 기준', '#ffffff'),(s['stop'],'손절 계산 기준','#ff9da6')):
                    ax.axhline(value,color=color,linestyle='--',linewidth=.9)
                    ax.text(.98,value,label+' '+fmt(value),transform=ax.get_yaxis_transform(),ha='right',va='bottom',color=color,fontsize=10)
                limits += [s['entry'],s['stop']]
                ax.text(.98,.98,'상위 목표 '+fmt(s['target'])+' (왼쪽 차트)\n비용 반영 예상 1:'+f"{s['rr']:.2f}",
                        transform=ax.transAxes,color='#e4eaf4',fontsize=11,ha='right',va='top',
                        bbox={'facecolor':'#101722','edgecolor':'#435168','alpha':.95,'pad':7},zorder=12)
            left=max(-.5,x(s['known_at']))
            ax.add_patch(Rectangle((min(left,right),c['low']),max(0,right-left),c['high']-c['low'],
                                  facecolor='white',edgecolor='white',linewidth=.7,alpha=.25,zorder=3))
            ax.axhline(c['price'],color='#efc68e',linestyle=':',linewidth=.7)
            lo,hi=min(limits),max(limits);pad=max((hi-lo)*.19,c['price']*.0003)
            ax.set_ylim(lo-pad,hi+pad);ax.set_xlim(-2,right)
            ticks=list(range(0,n,max(1,n//5)));ax.set_xticks(ticks,[kst(bars[i].timestamp).replace(' KST','') for i in ticks],fontsize=9)
            ax.legend(handles=legend,loc='lower left',bbox_to_anchor=(0,1.005),ncol=3,fontsize=9,facecolor='#101722',edgecolor='#344154',labelcolor='#dce5ef')
            ax.set_title(f"{LABELS[tf]} {'상위 계단 · OTE와 목표' if panel==0 else '하위 계단 · 진입과 기준 저점'} · 이전 한 파동 포함",loc='left',color='#e0e7f0',fontsize=13,pad=40)
        text=unit_text(event);lines=text.splitlines()
        heading='교육용 가상 차트 · 실제 시세/알림 아님' if snapshot.get('illustrative') else lines[0]
        extra=' · 함께 중첩: '+', '.join(LABELS[n['timeframe']] for n in c.get('also_overlaps',[])) if c.get('also_overlaps') else ''
        fig.suptitle(heading+' · '+lines[1]+'\n'+lines[2]+extra,color='#edf2f8',fontsize=15)
        demo='교육용 가상 예시 · 실제 알림 아님 | ' if snapshot.get('illustrative') else ''
        fig.supxlabel(demo+kst(now)+'\n청록: 확인된 계단 / 노란 점선: 진행 중 · 미확정 / 흰 박스: OTE·중첩·3R 공통 구간\n'
                      '비용 가정: 편도 수수료 0.06% + 슬리피지 0.02% · 손절 여유: 하위 ATR의 10% · 주문 실행 없음',color='#bdc9d9',fontsize=10)
        path=Path(prefix).with_name(Path(prefix).name+'_side_by_side.png');path.parent.mkdir(parents=True,exist_ok=True)
        fig.savefig(path,dpi=145,facecolor=fig.get_facecolor())
    finally:plt.close(fig)
    return {'images':[str(path.resolve())],'image_captions':[''],'text_after_images':text,
            'track':c['track'],'lens':c['lenses'][0],'source_event_id':c['id']}
