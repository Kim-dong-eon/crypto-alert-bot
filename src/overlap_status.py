"""All three scope views. Reference boxes never become entry candidates."""
from dataclasses import asdict
from src.ict_nested import PDZone
from src.ict_engine import TF_MS
from src.ict_overlap import overlaps,overlap_checks,overlap_count,LABELS,kst,alert_eligible
from config.overlap_settings import TRACKS,TRACK_LABELS


def nearest_sources(zones,price):
    groups={}
    for zone in sorted(zones,key=lambda z:(max(z.low-price,price-z.high,0),-z.created)):
        groups.setdefault((zone.timeframe,zone.kind),asdict(zone))
    return list(groups.values())


def overview_events(snapshot):
    events=[];price=snapshot['price']
    all_zones=[PDZone(**row) for row in snapshot.get('valid_zones',[])]
    for track,pair in TRACKS.items():
        state=snapshot['tracks'].get(track,{'status':'data_unavailable','context':None,'candidate':None})
        actual=state.get('candidate');context=state.get('context');pending=state.get('pending_sweep')
        unavailable=state['status']=='data_unavailable'
        direction=context.get('direction',actual.get('direction',0) if actual else 0) if context else 0
        preferred=direction or (pending['direction'] if pending else 0)
        zones=[z for z in all_zones if z.timeframe in pair and (not preferred or z.direction==preferred)]
        since=(context.get('sweep_time') if context else pending['sweep'] if pending else None)
        if since is not None:zones=[z for z in zones if z.created>=since-TF_MS[pair[0]]]
        view={'symbol':snapshot['symbol'],'exchange':snapshot.get('exchange','bitget'),'track':track,
              'pair':list(pair),'price':price,'direction':direction,'plot_direction':preferred,
              'context':context,'sources':[],'groups':[],'low':None,'high':None,
              'reference':not bool(context),'unavailable':unavailable,
              'equilibrium':{},'pending_sweep':pending,'liquidity':state.get('liquidity',{}),
              'state_status':state['status'],'structure_proof':state.get('liquidity',{}).get('flow_proof')}
        if actual:
            view.update(actual,reference=False,plot_direction=actual['direction'],unavailable=False)
        elif not unavailable:
            choices=[]
            if not context:
                for d in ((preferred,) if preferred else (1,-1)):
                    relevant=[z for z in zones if z.direction==d and (z.low<=price if d==1 else z.high>=price)]
                    choices.extend({**choice,'reference_direction':d} for choice in overlaps(relevant,pair,price))
                choices.sort(key=lambda c:(max(c['low']-price,price-c['high'],0),-c['score'],-c['latest_source']))
            if choices:
                choice=choices[0];view.update(choice,plot_direction=choice['reference_direction'])
            else:
                view['sources']=nearest_sources(zones,price)
                # Separate existing boxes are not common-price evidence.
                view['no_common_overlap']=True
            if context:
                view['equilibrium']={'midpoint':(context['swing_low']+context['swing_high'])/2,
                                     'range_low':context['swing_low'],'range_high':context['swing_high']}
            if pending:
                view['pending_context']={'sweep_time':pending['sweep'],'swept_level':pending['level']}
        if unavailable:
            headline='자료 대기';reason='완성 봉 자료 부족·지연'
        elif actual:
            headline='강세(LONG)' if direction==1 else '약세(SHORT)'
            if alert_eligible(actual):
                side='지지' if direction==1 else '저항';where='안' if actual['low']<=price<=actual['high'] else '접근'
                reason=f"{'하단 스윕·상승' if direction==1 else '상단 스윕·하락'} MSS 확인 · {side} 박스 {where}"
            elif actual.get('monitor_eligible') is False:reason='BTC 상관·거래대금 조건 대기'
            elif actual.get('context_price_breach'):reason='보호 가격 침범 · 확정 종가 대기'
            else:reason='방향 확인 · 0.5 접근 대기'
        elif context:
            headline='강세(LONG)' if direction==1 else '약세(SHORT)';reason='방향 확인 · 현재 국면의 공통 중첩 없음'
        elif pending:
            headline='상승 전환 대기' if preferred==1 else '하락 전환 대기'
            reason=f"{'하단' if preferred==1 else '상단'} 스윕 확인 · {LABELS[pair[0]]} {pending['structure']:,.2f} {'상회' if preferred==1 else '하회'} 마감 대기"
        else:
            headline='방향 미확정'
            audit=state.get('liquidity',{}).get('last_transition') or {}
            last=audit.get('reason')
            reason={'pending_raid_extreme_close_broken':'이전 스윕 꼬리 끝 종가 이탈 · 대기 취소',
                    'protected_structure_close_broken':'보호 고저점 종가 이탈 · 이전 방향 종료',
                    'both_external_sides_taken_order_unknown':'한 봉이 양쪽 외곽을 침범 · 방향 확인 대기'}.get(last,'외곽 스윕·종가 MSS 조합 미확정')
            if last=='pending_raid_extreme_close_broken' and audit.get('direction') in (-1,1):
                reason=f"이전 {'하락' if audit['direction']==-1 else '상승'} 대기 취소 · 새 외곽 스윕·MSS 대기"
        sample=(state.get('liquidity',{}).get('external_range') or {}).get('bars')
        if sample and sample<state.get('liquidity',{}).get('lookback_bars',20):reason+=f' · 최근 {sample}봉 기준'
        view.update(headline=headline,reason=reason,notify_eligible=alert_eligible(actual))
        view['checks']=[(tf,kind,False) for tf in pair for kind in ('OB','FVG')] if view.get('no_common_overlap') or unavailable else overlap_checks(view)
        view['count']=None if unavailable else sum(hit for _,_,hit in view['checks'])
        events.append({'view':view,'candidate':actual,'timestamp':snapshot['as_of'],'stage':'status'})
    return events


def overview_notice(events):
    ordered=sorted(events,key=lambda e:list(TRACKS).index(e['view']['track']))
    first=ordered[0]['view'];number=lambda value:f'{value:,.2f}'.rstrip('0').rstrip('.')
    lines=[f"[{first['exchange'].title()} · {first['symbol']}] 현재가 {number(first['price'])}",'']
    for event in ordered:
        v=event['view'];count='?/4' if v['count'] is None else f"{v['count']}/4"
        lines += [f"{TRACK_LABELS[v['track']]} · {v['headline']} · {'참고 중첩' if v['reference'] and v['count'] else '중첩'} {count}"+(' · 접근 알림' if v['notify_eligible'] else '')]
        if v['notify_eligible']:
            side='지지' if v['direction']==1 else '저항'
            lines.append(f"{side} 박스 {'안' if v['low']<=v['price']<=v['high'] else '접근'}")
        if not v['unavailable']:
            for tf in v['pair']:
                checks={kind:hit for frame,kind,hit in v['checks'] if frame==tf}
                lines.append(f"{LABELS[tf]} OB {'✓' if checks['OB'] else '—'} · FVG {'✓' if checks['FVG'] else '—'}")
            if v['low'] is not None:lines.append(f"{'참고 구간' if v['reference'] else '겹침'} {number(v['low'])} ~ {number(v['high'])}")
            midpoint=v.get('equilibrium',{}).get('midpoint')
            if midpoint is not None:lines.append(f"0.5 기준 {number(midpoint)}")
        lines.append('')
    lines += ['✓=같은 가격에서 겹침 · 참고 박스는 방향 확정 전',kst(max(e['timestamp'] for e in ordered))]
    return '\n'.join(lines)
