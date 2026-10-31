"""One alert/lens per image, with side-by-side context and close-up."""
import copy
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from config.overlap_settings import TRACK_LABELS
from src.ict_overlap import LABELS,kst,overlap_checks
from src.overlap_charts import configure_font
from src.structure_review_charts import draw_notified_scope


def split_units(events):
    """A single photo cannot overlay broad and recent turning-point lines."""
    result=[]
    for event in events:
        for lens in event['candidate']['lenses']:
            unit=copy.deepcopy(event)
            unit['candidate']['lenses']=[lens]
            result.append(unit)
    rank={'short':0,'medium':1,'long':2}
    return sorted(result,key=lambda e:(rank[e['candidate']['track']],
                                      0 if e['candidate']['lenses'][0]=='recent' else 1,
                                      e['candidate']['low']))


def unit_text(event):
    c=event['candidate'];lens=c['lenses'][0]
    if len(c['lenses'])!=1:raise ValueError('One lens per photo pair')
    name='큰 틀' if lens=='broad' else '작은 틀'
    side='지지 LONG' if c['direction']==1 else '저항 SHORT'
    checks=overlap_checks(c)
    proof=' + '.join(f'{LABELS[tf]} {kind}' for tf,kind,hit in checks if hit)
    count=sum(hit for _,_,hit in checks)
    role='지지' if c['direction']==1 else '저항'
    return (f"[Bitget · {c['symbol']}]\n"
            f"{LABELS[c['pair'][0]]} {TRACK_LABELS[c['track']]} {name} · {side} · {count}/4\n"
            f"{role} 근거: {proof}")


def render_unit(snapshot,event,frames,prefix):
    configure_font();c=event['candidate'];text=unit_text(event)
    prefix=Path(prefix);prefix.parent.mkdir(parents=True,exist_ok=True)
    paths=[]
    for overview,suffix in ((True,'overview'),(False,'detail')):
        fig,ax=plt.subplots(figsize=(13,6.6),layout='constrained')
        fig.patch.set_facecolor('#111722')
        try:
            draw_notified_scope(ax,[c],snapshot['tracks'][c['track']]['analysis'],frames,
                                overview,TRACK_LABELS[c['track']],show_zone_text=False,sweep_limit=1,detail_bars=36)
            tf=c['pair'][0 if overview else -1]
            ax.set_title(f"{LABELS[tf]} {'전체 흐름' if overview else '확대'}",loc='left',
                         fontsize=11,color='#e3e8f0')
            fig.suptitle(f"{text.splitlines()[0]} · {text.splitlines()[1]}\n{kst(snapshot['as_of'])}",
                         color='#e3e8f0',fontsize=13)
            path=prefix.with_name(prefix.name+'_'+suffix+'.png')
            fig.savefig(path,dpi=135,facecolor=fig.get_facecolor());paths.append(str(path.resolve()))
        finally:plt.close(fig)
    return {'images':paths,'image_captions':['',''],'text_after_images':text,
            'track':c['track'],'lens':c['lenses'][0],'source_event_id':c['id']}


def message_example(units):
    """Keep each alert's two photos and following text as a separate unit."""
    return {'delivery_order':'per_alert_overview_detail_then_text',
            'groups':[{'images':u['images'],'image_captions':['',''],
                       'text_after_images':u['text_after_images']} for u in units]}


def render_side_by_side_unit(snapshot,event,frames,prefix):
    """One alert/lens in one image: left context, right close-up."""
    configure_font();c=event['candidate'];text=unit_text(event)
    prefix=Path(prefix);prefix.parent.mkdir(parents=True,exist_ok=True)
    fig,axes=plt.subplots(1,2,figsize=(19,7.2),layout='constrained')
    fig.patch.set_facecolor('#111722')
    try:
        for ax,overview in zip(axes,(True,False)):
            draw_notified_scope(ax,[c],snapshot['tracks'][c['track']]['analysis'],frames,
                                overview,TRACK_LABELS[c['track']],show_zone_text=False,
                                sweep_limit=1,detail_bars=36)
            tf=c['pair'][0 if overview else -1]
            ax.set_title(f"{LABELS[tf]} {'전체 흐름' if overview else '구간 확대'}",loc='left',
                         fontsize=12,color='#e3e8f0')
        fig.suptitle(f"{text.splitlines()[0]} · {text.splitlines()[1]}\n{kst(snapshot['as_of'])}",
                     color='#e3e8f0',fontsize=14)
        path=prefix.with_name(prefix.name+'_side_by_side.png')
        fig.savefig(path,dpi=140,facecolor=fig.get_facecolor())
    finally:plt.close(fig)
    return {'images':[str(path.resolve())],'image_captions':[''],
            'text_after_images':text,'track':c['track'],'lens':c['lenses'][0],
            'source_event_id':c['id']}
