"""Confluence inside one exact upper candle, never another date or retest."""
from src.ict_engine import TF_MS
from src.ict_overlap import overlaps

ANCHOR_POLICY='same-upper-candle-v1'
# The storage buffer is wider than the upper analysis so the first upper
# candle still has all of its children at non-coincident frame boundaries.
TOP_BARS={'4h':1080,'1d':1102,'1w':156}


def anchor_start(zone):
    """OB base candle; FVG middle displacement candle (not the left wick)."""
    tf=zone.timeframe if hasattr(zone,'timeframe') else zone['timeframe']
    kind=zone.kind if hasattr(zone,'kind') else zone['kind']
    origin=zone.origin if hasattr(zone,'origin') else zone['origin']
    return origin+(TF_MS[tf] if kind=='FVG' else 0)


def anchored_overlaps(zones,pair,anchor_price,lower_bars,audit=None):
    top,lower=pair;duration=TF_MS[top];step=TF_MS[lower]
    parents={}
    for z in zones:
        if z.timeframe==top:parents.setdefault((anchor_start(z),z.direction),[]).append(z)
    timestamps={b.timestamp for b in lower_bars};result=[]
    counts={'parent_windows':len(parents),'missing_child_windows':0,'windows_with_children':0,'mixed_intersections':0}
    for (start,direction),upper in parents.items():
        end=start+duration
        # Price overlap cannot compensate for missing same-time children.
        if any(t not in timestamps for t in range(start,end,step)):
            counts['missing_child_windows']+=1;continue
        children=[z for z in zones if z.timeframe==lower and z.direction==direction
                  and start<=z.origin and z.created<=end]
        if not children:continue
        counts['windows_with_children']+=1
        for c in overlaps(upper+children,pair,anchor_price):
            kinds={s['kind'] for s in c['sources']}
            if not {'OB','FVG'}.issubset(kinds):continue
            c['parent_window']={'policy':ANCHOR_POLICY,'timeframe':top,'lower_timeframe':lower,
                                'start':start,'end':end,'child_bars':duration//step,
                                'parent_ids':[s['id'] for s in c['sources'] if s['timeframe']==top]}
            result.append(c)
    counts['mixed_intersections']=len(result)
    if audit is not None:audit.update(counts)
    return result


def valid_anchor(candidate):
    """Validate saved old/new locks against this policy before reusing them."""
    pair=candidate.get('pair',[])
    if len(pair)!=2:return False
    top,lower=pair;sources=candidate.get('sources',[])
    parents=[s for s in sources if s['timeframe']==top]
    children=[s for s in sources if s['timeframe']==lower]
    if not parents or not children:return False
    starts={anchor_start(s) for s in parents}
    if len(starts)!=1:return False
    start=next(iter(starts));end=start+TF_MS[top]
    window=candidate.get('parent_window')
    if window and (window['start']!=start or window['end']!=end):return False
    return all(start<=s['origin'] and s['created']<=end for s in children)


def analysis_frames(analysis,frames):
    starts=analysis.get('frame_starts',{})
    return {tf:[b for b in values if b.timestamp>=starts.get(tf,-1)] for tf,values in frames.items()}
