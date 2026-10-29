"""One image per symbol, containing only matching overlap scopes."""
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle,Patch
from matplotlib import font_manager
from src.ict_engine import TF_MS
from src.ict_overlap import LABELS,kst,overlap_count
from config.overlap_settings import TRACK_LABELS
from src.ict_swings import ConfirmedSwings

TF_COLORS={'1w':'#b59aff','1d':'#fa81b0','4h':'#4d9dff','1h':'#22d3d3','15m':'#f7ce56'}
FVG_COLORS={'1w':'#d6c5ff','1d':'#ffc0d9','4h':'#94c5ff','1h':'#93e9e5','15m':'#f6a96c'}


def configure_font():
    available={f.name for f in font_manager.fontManager.ttflist}
    for name in ('Malgun Gothic','NanumGothic','Noto Sans CJK KR'):
        if name in available:
            plt.rcParams['font.family']=name;break
    plt.rcParams['axes.unicode_minus']=False


def box_color(zone):
    return (TF_COLORS if zone['kind']=='OB' else FVG_COLORS)[zone['timeframe']]


def draw_scope(ax,event,frames,overview=False):
    c=event.get('view') or event['candidate'];asof=event['timestamp'];tf=c['pair'][0 if overview else -1]
    direction=c.get('plot_direction',c['direction'])
    plot_context=c.get('context') or c.get('pending_context') or {}
    all_bars=[b for b in frames.get(tf,[]) if b.timestamp+TF_MS[tf]<=asof]
    if len(all_bars)<2:
        if 'view' not in event:raise ValueError('Chart needs closed candles')
        ax.set_facecolor('#18202d');ax.set_xticks([]);ax.set_yticks([])
        ax.text(.5,.5,'완성 봉 자료 부족·지연',transform=ax.transAxes,ha='center',color='#d1dbe8')
        ax.set_title(f"{TRACK_LABELS[c['track']]} · {LABELS[tf]} · 자료 대기 (?/4)",color='#e0e5ee',loc='left',fontsize=11)
        return
    start=max(0,len(all_bars)-(160 if overview else 90))
    if overview and plot_context.get('sweep_time'):
        raid=plot_context['sweep_time']-TF_MS[tf]
        raid_index=next((i for i,b in enumerate(all_bars) if b.timestamp>=raid),start)
        start=max(0,len(all_bars)-400,min(start,max(0,raid_index-20)))
    bars=all_bars[start:]
    if len(bars)<2:raise ValueError('Chart needs closed candles')
    ax.set_facecolor('#18202d');ax.tick_params(colors='#b4bfce');ax.grid(alpha=.10,color='white');ax.set_axisbelow(True)
    lows=[b.low for b in bars];highs=[b.high for b in bars];n=len(bars);end=n+7
    def start_at(stamp):return min(max(-.5,(stamp-bars[0].timestamp)/TF_MS[tf]),n-.5)
    handles=[]
    for z in c['sources']:
        left=start_at(z['origin']);color=box_color(z)
        ax.add_patch(Rectangle((left,z['low']),end-left,z['high']-z['low'],facecolor=color,edgecolor='none',alpha=.12,zorder=1))
        ax.add_patch(Rectangle((left,z['low']),end-left,z['high']-z['low'],facecolor='none',edgecolor=color,linewidth=.65,alpha=.85,zorder=2))
        side=(' 지지' if z.get('direction')==1 else ' 저항' if z.get('direction')==-1 else '') if c.get('reference') else ''
        handles.append(Patch(facecolor=color,edgecolor=color,alpha=.55,label=f"{LABELS[z['timeframe']]} {z['kind']}{side}"))
    if c.get('low') is not None and c.get('high') is not None and c['sources']:
        common_start=start_at(max(z.get('created',z['origin']) for z in c['sources']))
        ax.add_patch(Rectangle((common_start,c['low']),end-common_start,c['high']-c['low'],facecolor='none',edgecolor='#e5e7eb',linewidth=.7,linestyle='--' if c.get('reference') else '-',zorder=2))
        handles.append(Patch(facecolor='none',edgecolor='#e5e7eb',linewidth=.7,label='참고 공통 중첩' if c.get('reference') else '공통 중첩'))
    for i,b in enumerate(bars):
        color='#30c59a' if b.close>=b.open else '#ee6677';ax.vlines(i,b.low,b.high,color=color,linewidth=.75,zorder=3)
        height=max(abs(b.close-b.open),(max(highs)-min(lows))*1e-5)
        ax.add_patch(Rectangle((i-.28,min(b.open,b.close)),.56,height,facecolor=color,edgecolor=color,linewidth=.45,zorder=3))
    ax.axhline(c['price'],color='#c7cdd7',linestyle=':',linewidth=.6,zorder=2)
    ax.annotate(f"현재가 {c['price']:,.2f}",xy=(.985,c['price']),xycoords=('axes fraction','data'),ha='right',va='bottom',fontsize=8,color='#e1e5eb',bbox={'facecolor':'#18202d','edgecolor':'none','alpha':.8,'pad':2})
    known=[value for value in (c.get('low'),c.get('high'),c['price']) if value is not None]+[v for z in c['sources'] for v in (z['low'],z['high'])]
    midpoint=c.get('equilibrium',{}).get('midpoint')
    if midpoint is not None:
        ax.axhline(midpoint,color='#b5bdca',linestyle='--',linewidth=.6,zorder=2)
        ax.annotate(f'0.5 {midpoint:,.2f}',xy=(.01,midpoint),xycoords=('axes fraction','data'),va='bottom',color='#b5bdca',fontsize=8)
        known.append(midpoint)
    if overview:
        context=plot_context
        proof=c.get('structure_proof') or {}
        swept=context.get('swept_level')
        if swept is not None and proof.get('swept_origin') is not None:
            left=start_at(proof['swept_origin'])
            right=start_at(context['sweep_time']-TF_MS[tf])
            ax.hlines(swept,left,right,color='#f4c75e',linestyle='--',linewidth=.8,zorder=4)
            ax.annotate('외곽 유동성 기준',xy=(left,swept),xytext=(0,9),textcoords='offset points',color='#f4c75e',fontsize=7)
            known.append(swept)
        if context.get('invalidation') is not None and proof:
            protected=context['invalidation']
            ax.hlines(protected,start_at(proof.get('protected_origin',context['sweep_time'])),end,color='#b3a3df',linestyle=':',linewidth=.65,zorder=2)
            ax.annotate('보호 저점' if direction==1 else '보호 고점',xy=(.985,protected),xycoords=('axes fraction','data'),ha='right',va='top',fontsize=7,color='#b3a3df')
            known.append(protected)
        if context.get('sweep_time',asof)-TF_MS[tf]<bars[0].timestamp:
            ax.text(.99,.02,'기준 스윕은 표시 범위 이전',transform=ax.transAxes,ha='right',color='#aebbd0',fontsize=7)
        for name,key in [('기준 저점','range_low'),('기준 고점','range_high')]:
            value=c.get('equilibrium',{}).get(key)
            if value is not None:
                ax.axhline(value,color='#7e8ca2',linestyle=':',linewidth=.5,zorder=1)
                ax.annotate(name,xy=(.01,value),xycoords=('axes fraction','data'),color='#aebbd0',fontsize=8,va='bottom');known.append(value)
        detector=ConfirmedSwings(tf)
        for bar in all_bars:detector.step(bar)
        by_origin={}
        for point in detector.all_confirmed:by_origin.setdefault(point.origin,{})[point.kind]=point
        chain=[]
        for origin,points in sorted(by_origin.items()):
            if len(points)!=1:continue
            point=next(iter(points.values()))
            if chain and chain[-1].kind==point.kind:
                if (point.price>chain[-1].price if point.kind=='high' else point.price<chain[-1].price):chain[-1]=point
            else:chain.append(point)
        visible=[p for p in chain if bars[0].timestamp<=p.origin<=bars[-1].timestamp][-8:]
        if visible:
            xs=[(p.origin-bars[0].timestamp)/TF_MS[tf] for p in visible]
            ax.plot(xs,[p.price for p in visible],color='#a6b7cc',linewidth=.8,linestyle='--',alpha=.7,zorder=4)
            previous={}
            for p,x in zip(visible,xs):
                old=previous.get(p.kind);label='고점' if p.kind=='high' else '저점'
                if old is not None:label+='↑' if p.price>old else '↓' if p.price<old else '='
                previous[p.kind]=p.price
                ax.annotate(label,xy=(x,p.price),xytext=(0,7 if p.kind=='high' else -12),textcoords='offset points',ha='center',color='#d1dbe8',fontsize=7,zorder=5)
        for field,label,color in [('sweep_time','외곽 스윕' if proof else '유동성 스윕','#f4c75e'),('formed','종가 MSS','#d9b3ff')]:
            stamp=context.get(field)
            point=next((b for b in bars if b.timestamp+TF_MS[tf]==stamp),None)
            if point:
                x=(point.timestamp-bars[0].timestamp)/TF_MS[tf]
                y=(point.low if direction==1 else point.high) if field=='sweep_time' else point.close
                ax.scatter([x],[y],s=25,color=color,zorder=6)
                ax.annotate(label,xy=(x,y),xytext=(8,25 if field=='sweep_time' else -30),textcoords='offset points',color=color,fontsize=8,arrowprops={'arrowstyle':'->','color':color,'linewidth':.7},zorder=6)
    pending=c.get('pending_sweep')
    if pending:
        value=pending['structure'];known.append(value)
        ax.axhline(value,color='#d9b3ff',linestyle='--',linewidth=.65)
        ax.annotate(f"MSS 대기 {value:,.2f}",xy=(.985,value),xycoords=('axes fraction','data'),ha='right',va='bottom',color='#d9b3ff',fontsize=8)
    if c.get('reason'):
        ax.text(.01,.025,c['reason'],transform=ax.transAxes,fontsize=8,color='#e1e5eb',bbox={'facecolor':'#111722','edgecolor':'none','alpha':.85,'pad':3},zorder=8)
    bottom=min(lows+known);top=max(highs+known);pad=max((top-bottom)*.09,top*.0005)
    ax.set_ylim(bottom-pad,top+pad);ax.set_xlim(-1,end+.5);ax.yaxis.tick_right()
    indices=list(range(0,n,max(1,n//6)));ax.set_xticks(indices,[kst(bars[i].timestamp).replace(' KST','') for i in indices],fontsize=8)
    if handles:ax.legend(handles=handles,loc='upper left',ncol=min(3,len(handles)),fontsize=8,facecolor='#111722',edgecolor='#344154',labelcolor='#dce2eb',framealpha=.85)
    regime='강세 구조' if c['direction']==1 else '약세 구조'
    view=f'{LABELS[tf]} 전체 구조' if overview else f'{LABELS[tf]} 중첩 확대'
    count=c.get('count',overlap_count(c));score='?/4' if count is None else f'{count}/4'
    if c.get('headline'):regime=c['headline']
    ax.set_title(f"{TRACK_LABELS[c['track']]} {regime} · {'참고 ' if c.get('reference') and count else ''}중첩 {score} · {view}",color='#e0e5ee',loc='left',fontsize=11)


def render_symbol_chart(events,frames,path):
    value=lambda e:e.get('view') or e['candidate']
    configure_font();ordered=sorted(events,key=lambda e:{'short':0,'medium':1,'long':2}.get(value(e)['track'],9))
    if not 1<=len(ordered)<=3 or len({value(e)['symbol'] for e in ordered})!=1:raise ValueError('One symbol and1-3overlap scopes required')
    count=len(ordered);fig,axes=plt.subplots(count,2,figsize=(18,7+5*(count-1)),squeeze=False,layout='constrained');fig.patch.set_facecolor('#111722')
    try:
        regimes='단기 · 중기 · 장기 전체 상태' if 'view' in ordered[0] else ' / '.join(f"{TRACK_LABELS[value(e)['track']]} {'강세' if value(e)['direction']==1 else '약세'} ({overlap_count(value(e))}/4)" for e in ordered)
        venue=value(ordered[0]).get('exchange')
        prefix=(venue.title()+' · ') if venue else ''
        fig.suptitle(f"{prefix}{value(ordered[0])['symbol']} · {regimes}\n{kst(max(e['timestamp'] for e in ordered))}",color='#e3e8f0',fontsize=14)
        for row,event in zip(axes,ordered):
            draw_scope(row[0],event,frames,overview=True);draw_scope(row[1],event,frames)
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);fig.savefig(path,dpi=125,facecolor=fig.get_facecolor());return path
    finally:plt.close(fig)


def render_chart(event,frames,path):
    return render_symbol_chart([event],frames,path)
