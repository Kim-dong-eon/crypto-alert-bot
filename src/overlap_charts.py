"""Thin, translucent TradingView-style boxes on the lower-timeframe chart."""
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle,Patch
from matplotlib import font_manager
from src.ict_engine import TF_MS
from src.ict_overlap import LABELS,kst
from config.overlap_settings import TRACK_LABELS

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


def render_chart(event,frames,path):
    configure_font();c=event['candidate'];asof=event['timestamp'];side='LONG' if c['direction']==1 else 'SHORT';tf=c['pair'][-1]
    bars=[b for b in frames[tf] if b.timestamp+TF_MS[tf]<=asof][-90:]
    if len(bars)<2:raise ValueError('Chart needs closed candles')
    fig,ax=plt.subplots(figsize=(13,7),layout='constrained');fig.patch.set_facecolor('#111722');ax.set_facecolor('#18202d')
    try:
        fig.suptitle(f"{c['symbol']} · {TRACK_LABELS[c['track']]} · {side} 관찰\n{' + '.join(LABELS[t] for t in c['pair'])} 중첩 {c['low']:,.4f} ~ {c['high']:,.4f} USDT · {kst(asof)}",color='#e3e8f0',fontsize=13)
        ax.tick_params(colors='#b4bfce');ax.grid(alpha=.10,color='white');ax.set_axisbelow(True)
        lows=[b.low for b in bars];highs=[b.high for b in bars];n=len(bars);end=n+7
        def start_at(stamp):return min(max(-.5,(stamp-bars[0].timestamp)/TF_MS[tf]),n-.5)
        # Gray opposition is bounded just like the user's ordinary boxes.
        for z in c.get('resistance_support',[]):
            left=start_at(z['origin'])
            ax.add_patch(Rectangle((left,z['low']),end-left,z['high']-z['low'],facecolor='#98a1af',edgecolor='#b7bfca',alpha=.09,linewidth=.55,zorder=1))
        handles=[]
        for z in c['sources']:
            left=start_at(z['origin']);color=box_color(z)
            ax.add_patch(Rectangle((left,z['low']),end-left,z['high']-z['low'],facecolor=color,edgecolor='none',alpha=.12,zorder=1))
            ax.add_patch(Rectangle((left,z['low']),end-left,z['high']-z['low'],facecolor='none',edgecolor=color,linewidth=.65,alpha=.85,zorder=2))
            handles.append(Patch(facecolor=color,edgecolor=color,alpha=.55,label=f"{LABELS[z['timeframe']]} {z['kind']}"))
        common_start=start_at(max(z.get('created',z['origin']) for z in c['sources']))
        ax.add_patch(Rectangle((common_start,c['low']),end-common_start,c['high']-c['low'],facecolor='none',edgecolor='#e5e7eb',linewidth=.7,zorder=2))
        handles.append(Patch(facecolor='none',edgecolor='#e5e7eb',linewidth=.7,label='공통 중첩'))
        for i,b in enumerate(bars):
            color='#30c59a' if b.close>=b.open else '#ee6677';ax.vlines(i,b.low,b.high,color=color,linewidth=.75,zorder=3)
            height=max(abs(b.close-b.open),(max(highs)-min(lows))*1e-5)
            ax.add_patch(Rectangle((i-.28,min(b.open,b.close)),.56,height,facecolor=color,edgecolor=color,linewidth=.45,zorder=3))
        ax.axhline(c['price'],color='#c7cdd7',linestyle=':',linewidth=.6,zorder=2)
        ax.annotate(f"현재가 {c['price']:,.2f}",xy=(.985,c['price']),xycoords=('axes fraction','data'),ha='right',va='bottom',fontsize=8,color='#e1e5eb',bbox={'facecolor':'#18202d','edgecolor':'none','alpha':.8,'pad':2})
        known=[c['low'],c['high'],c['price']]+[v for z in c['sources'] for v in (z['low'],z['high'])]
        bottom=min(lows+known);top=max(highs+known);pad=max((top-bottom)*.09,top*.0005)
        ax.set_ylim(bottom-pad,top+pad);ax.set_xlim(-1,end+.5);ax.yaxis.tick_right()
        indices=list(range(0,n,max(1,n//6)));ax.set_xticks(indices,[kst(bars[i].timestamp).replace(' KST','') for i in indices],fontsize=8)
        ax.legend(handles=handles,loc='upper left',ncol=min(5,len(handles)),fontsize=8,facecolor='#111722',edgecolor='#344154',labelcolor='#dce2eb',framealpha=.85)
        ax.set_title(f"{LABELS[tf]} 차트 · 시간대별 색상 / OB·FVG별 명암",color='#aeb9c9',loc='left',fontsize=9)
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);fig.savefig(path,dpi=125,facecolor=fig.get_facecolor());return path
    finally:plt.close(fig)
