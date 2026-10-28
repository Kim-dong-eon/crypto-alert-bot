"""Telegram presentation and acknowledged delivery for the new ICT monitor."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

from config.ict_settings import DEFAULTS
from src.ict_engine import RULE_VERSION, TERMINAL, TF_MS, estimated_rr
from src.ict_triple import TRACK_NAMES


STAGES = {"prepared": "눌림 준비", "touched": "관찰 구간 도달", "review": "진입 검토",
          "cancelled": "후보 취소", "target_reached": "목표 유동성 도달"}
REASONS = {
    "ob_invalidated": "OB 무효화 가격 침범", "liquidity_target": "목표 유동성 도달",
    "stop_and_target_same_bar": "한 봉에서 무효화 가격과 목표 모두 접촉 · 순서 불명",
    "expired": "대기 유효기간 종료", "support_not_confirmed": "재방문 후 지지·저항 마감 확인 실패",
    "gap_over_zone": "관찰 구간을 갭으로 통과", "higher_flow_changed": "4시간 흐름 종료·변경 또는 자료 지연",
    "replaced": "새로운 셋업으로 교체", "insufficient_rr": "가까운 유동성까지 3R 목표 공간 부족",
    "live_rr_insufficient": "전송 직전 현재가에서 3R 목표 공간 부족",
    "ote_price_left": "전송 직전 가격이 OTE 범위 밖으로 이동",
    "outside_confluence": "최다 중첩 구간 밖으로 이동",
    "confluence_invalidated": "선택한 중첩 근거의 만료·무효화",
    "equilibrium_changed": "상위 50% 범위 변경으로 기존 진입 구간 이탈",
    "swing_target_consumed": "준비한 15분 스윙 목표가 진입 전에 소모됨",
    "structure_range_invalidated": "기준 고저점을 종가로 이탈하여 이전 범위 종료",
    "missing_higher_target": "이미 확인된 반대쪽 4시간 목표 유동성 없음",
    "reaction_outside_bands": "반응 확인 후 가격이 상위 절반·15분 OTE 범위 밖",
}


def kst(stamp):
    return datetime.fromtimestamp(stamp / 1000, timezone.utc).astimezone(timezone(timedelta(hours=9))).strftime("%m/%d %H:%M KST")


def event_id(event):
    stamp = "once" if event.get("zone_only") else event["timestamp"]
    raw = f"{event.get('rule_version', RULE_VERSION)}|{event['symbol']}|{event['id']}|{event['stage']}|{stamp}|{event.get('reason')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def format_notice(event, cfg=DEFAULTS):
    if event.get("zone_only"):
        return format_zone_notice(event, cfg)
    d = event["direction"]
    side = "LONG" if d == 1 else "SHORT"
    pd = "디스카운트" if d == 1 else "프리미엄"
    reference = event.get("entry_reference")
    planned = event.get("trade_target")
    rr = estimated_rr(reference, event["stop"], planned, d, cfg) if reference is not None and planned is not None else None
    labels = {"1w": "1주", "1d": "1일", "4h": "4시간", "1h": "1시간", "15m": "15분"}
    chain = event.get("chain", ["4h", "15m"])
    first, last = labels[chain[0]], labels[chain[-1]]
    profile = TRACK_NAMES.get(event.get("track"), "관찰")
    lines = [f"[ICT {profile} · {STAGES[event['stage']]}] {event['symbol']} {side}",
             f"기준: {kst(event['timestamp'])} · 확정봉 기준",
             f"흐름: {' → '.join(labels[tf] for tf in chain)}",
             f"{first} 방향: {'강세' if d == 1 else '약세'} / 확보한 유동성: {event['higher_swept']:,.4f}",
             f"{last} 유동성 확보: {event['swept_level']:,.4f}",
             f"구조 변화 기준: {event['structure_level']:,.4f} · 종가 돌파 확인",
             f"관찰 구간: {event['zone_low']:,.4f} ~ {event['zone_high']:,.4f} ({pd})",
             f"OTE 범위: {event['ote_low']:,.4f} ~ {event['ote_high']:,.4f} / 중심: {event['ote_mid']:,.4f}",
             f"스윙 기준: 저점 {event['swing_low']:,.4f} = 0 / 고점 {event['swing_high']:,.4f} = 1",
             f"근거: OB{' + FVG 중첩' if event['fvg_overlap'] else ''} / {first} PD 중첩: {'있음' if event['higher_overlap'] else '없음'}",
             f"무효화 가격: {event['stop']:,.4f}",
             f"가까운 목표 유동성: {event['target']:,.4f} / {first} 목표: {event['higher_target']:,.4f}"]
    if "overlap_score" in event:
        lines.append(f"최다 중첩: {event['overlap_score']}/6 · {', '.join(event['overlap_groups'])}")
    if event["stage"] in TERMINAL:
        lines.append(f"종료 이유: {REASONS[event['reason']]}")
        lines.append("후보 관찰 종료입니다. 실제 보유·체결 여부나 손익을 판정한 알림은 아닙니다.")
    else:
        expiry = event["formation"] + cfg.setup_lifetime_bars * TF_MS[event.get("timeframe", cfg.low_tf)]
        lines.append(f"대기 만료: {kst(expiry)}")
        if event["stage"] == "review":
            lines += [f"확인봉 마감: {event['reference']:,.4f} / 진입 참고가: {reference:,.4f}",
                      f"1:{cfg.reward_risk_ratio:g} 계획 익절가: {planned:,.4f} (가격 기준)",
                      f"추정 비용 후 보상/위험: {rr:.2f}R" if rr is not None else "손익비 계산 불가",
                      "실제 체결가가 달라지면 3R 목표를 다시 계산하세요. OTE 밖에서는 추격하지 않습니다."]
        else:
            lines.append(f"OTE 재방문과 확인 마감을 기다립니다. 손절 1 : 목표 {cfg.reward_risk_ratio:g}, 가까운 유동성까지 목표 공간 필요.")
        if "current_price" in event:
            lines.append(f"전송 전 시세 참고가: {event['current_price']:,.4f}")
        lines.append("새 ICT 규칙의 관찰 신호 · 수익성 검증 전 · 자동 주문 없음")
    return "\n".join(lines)


def format_zone_notice(event, cfg=DEFAULTS):
    if event.get("lecture_profile"):
        return format_lecture_notice(event,cfg)
    if event.get("short_equilibrium"):
        return format_short_notice(event,cfg)
    labels = {"1w": "1주", "1d": "1일", "4h": "4시간", "1h": "1시간", "15m": "15분"}
    title = {"prepared": "중첩 구간 형성", "touched": "중첩 구간 도달", "cancelled": "관찰 구간 종료"}[event["stage"]]
    side = "LONG" if event["direction"] == 1 else "SHORT"
    lines = [f"[ICT {TRACK_NAMES[event['track']]} · {title}] {event['symbol']} {side}",
             f"감지: {kst(event['timestamp'])}",
             " → ".join(labels[tf] for tf in event["chain"]),
             f"첫 시간대 스윕: {event['swept_level']:,.4f} / MSS 종가 돌파: {event['structure_level']:,.4f}",
             f"알림 가격대: {event['zone_low']:,.4f} ~ {event['zone_high']:,.4f}",
             f"최다 중첩: {event['overlap_score']}/6 · {', '.join(event['overlap_groups'])}"]
    colors = ["🟥", "🟦", "🟨"]
    for tf, color in zip(event["chain"], colors):
        for zone in event["source_zones"]:
            if zone["timeframe"] == tf:
                lines.append(f"{color} {labels[tf]} {zone['kind']}: {zone['low']:,.4f} ~ {zone['high']:,.4f}")
    lines.append(f"최종 구간 손절 참고: {event['stop']:,.4f}")
    if event["ote_overlap"]:
        lines += [f"OTE와 겹치는 진입 관찰 범위: {event['entry_zone_low']:,.4f} ~ {event['entry_zone_high']:,.4f}",
                  f"계획 참고가: {event['entry_reference']:,.4f} / 1:{cfg.reward_risk_ratio:g} 목표: {event['trade_target']:,.4f}"]
        rr = estimated_rr(event["entry_reference"], event["stop"], event["trade_target"], event["direction"], cfg)
        lines.append(f"추정 비용 후 보상/위험: {rr:.2f}R" if rr is not None else "계획 손익비 계산 불가")
    else:
        lines.append("OTE 중첩 없음: 가격 구간만 관찰하며 OTE 진입 조건은 충족하지 않았습니다.")
    if event["stage"] == "cancelled":
        lines.append(f"종료: {REASONS[event['reason']]}")
    elif "current_price" in event:
        lines.append(f"전송 전 시세: {event['current_price']:,.4f}")
    lines.append("하위 시간대의 별도 스윕·MSS·반등 마감은 요구하지 않습니다. 실제 체결가로 3R을 다시 계산하세요.")
    return "\n".join(lines)


def format_lecture_notice(event,cfg=DEFAULTS):
    labels={"prepared":"영상 패턴 구간 형성","touched":"관찰 구간 도달","review":"반응 확인·진입 검토","cancelled":"관찰 종료"}
    side="LONG" if event["direction"]==1 else "SHORT"
    lines=[f"[ICT BTC 단기 · {labels[event['stage']]}] {side}",f"확인: {kst(event['timestamp'])}",
           "4H 방향·50% → 1H 50% → 15m OTE·영상 순서 패턴",
           f"4H 스윕 {event['swept_level']:,.2f} / MSS {event['structure_level']:,.2f}",
           f"유동성 레벨의 근접 확정 지점: {event.get('liquidity_strength',1)}개 (가격대 허용차 0.05%)",
           f"기존 OB 재방문 관찰: {event['zone_low']:,.2f} ~ {event['zone_high']:,.2f}",
           f"15m OTE: {event['ote_low']:,.2f} ~ {event['ote_high']:,.2f}",
           "확인 순서: 기존 OB·같은 방향 FVG → 유동성 확보 → 반대 FVG·기존 OB 이탈"]
    if event["stage"]=="review":
        p=event["exit_plan"]
        lines += [f"반응 후 진입 참고가: {event['entry_reference']:,.2f}",
                  f"확인 봉 바깥 손절: {event['stop']:,.2f} (여유 폭 {event['stop_buffer']:,.2f})",
                  f"실제 반대 유동성: {event['swing_target']:,.2f} / 가격 손익비 {event['liquidity_rr']:.2f}R",
                  f"1차 {int(p['first_fraction']*100)}%: {p['target1']:,.2f}"]
        if p["first_fraction"]<1:lines += [f"1차 후 잔량 가격 본절: {p['breakeven_stop']:,.2f}",f"잔량 50% 목표: {p['target2']:,.2f}"]
        lines.append("실제 체결가에서도 반대 유동성까지 3R 이상인지 재확인하세요. 본절에도 비용이 발생합니다.")
    elif event["stage"]=="cancelled":lines.append(f"종료: {REASONS.get(event['reason'],event['reason'])}")
    else:lines.append("관찰 알림입니다. 15분 반응 봉 확인 후 보호 손절과 실제 반대 유동성까지 최소 3R을 검사합니다.")
    lines.append("전체 영상 원문+사용자 시간대·OTE·분할 설정 / 실제 주문·체결 관리 없음")
    return "\n".join(lines)


def format_short_notice(event,cfg=DEFAULTS):
    title={"prepared":"15분 진입 구간 형성","touched":"15분 구간 도달","cancelled":"관찰 종료"}[event["stage"]]
    side="LONG" if event["direction"]==1 else "SHORT"
    half="아래 절반" if event["direction"]==1 else "위 절반"
    lines=[f"[ICT BTC 단기 · {title}] {side}",f"감지: {kst(event['timestamp'])}",
           "4시간 방향·50% → 1시간 50% → 15분 OTE·OB/FVG",
           f"4H 스윕: {event['swept_level']:,.4f} / MSS: {event['structure_level']:,.4f}"]
    for key,label in (("eq4h","4H"),("eq1h","1H")):
        r=event[key]
        lines.append(f"{label} 50%: {r['midpoint']:,.4f} ({r['low']:,.4f}~{r['high']:,.4f}의 {half})")
    lines += [f"15m OTE: {event['ote_low']:,.4f} ~ {event['ote_high']:,.4f}",
              f"실제 관찰 구간: {event['zone_low']:,.4f} ~ {event['zone_high']:,.4f}",
              f"근거 {event['overlap_score']}/4: {', '.join(event['overlap_groups'])}",
              f"15m 손절 참고: {event['stop']:,.4f}"]
    if event["trade_eligible"]:
        p=event["exit_plan"]
        percent=int(p["first_fraction"]*100)
        lines += [f"확정된 15m {'고점' if event['direction']==1 else '저점'}: {event['swing_target']:,.4f}",
                  f"목표 확정 시각: {kst(event['target_confirmed'])}",
                  f"계획 진입 참고가: {event['entry_reference']:,.4f}",
                  f"1차 {percent}%: {p['target1']:,.4f}"]
        if p["first_fraction"]<1:
            lines += [f"1차 후 잔량 손절: 진입가 {p['breakeven_stop']:,.4f}",f"최종 50%: {p['target2']:,.4f}"]
        lines.append("실제 체결가로 3R과 두 목표 순서를 재계산하세요. 가격 본절에도 수수료·슬리피지는 발생합니다.")
    else:
        lines.append("진짜 15m 스윙 목표 없음: 진입 제외, 구간만 관찰합니다.")
    if event["stage"]=="cancelled":lines.append(f"종료: {REASONS[event['reason']]}")
    if "current_price" in event:lines.append(f"전송 전 시세: {event['current_price']:,.4f}")
    return "\n".join(lines)


class NoticeLedger:
    def __init__(self, path):
        self.path = Path(path)
        self.state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"version": 1, "sent": {}, "dismissed": {}}
        if self.state.get("version") != 1 or not isinstance(self.state.get("sent"), dict) or not isinstance(self.state.get("dismissed", {}), dict):
            raise ValueError("Invalid ICT ledger; refusing to discard delivery history")
        self.state.setdefault("dismissed", {})

    @staticmethod
    def setup_key(event):
        return f"{event['symbol']}|{event['id']}"

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def dismiss(self, event):
        """Persist live-bar risk invalidations, even for unannounced candidates."""
        key = self.setup_key(event)
        if key not in self.state["dismissed"]:
            self.state["dismissed"][key] = event
            self._save()

    def candidates(self, events, now_ms, cfg=DEFAULTS):
        latest = {}
        for event in events:
            if event["timestamp"] <= now_ms:
                latest[(event["symbol"], event["id"])] = event
        for event in self.state["dismissed"].values():
            latest[(event["symbol"], event["id"])] = event
        sent = self.state["sent"]
        observed = {(v["symbol"], v["setup_id"]) for v in sent.values()}
        completed = {(v["symbol"], v["setup_id"]) for v in sent.values() if v["stage"] in TERMINAL}
        notices = []
        for key, event in latest.items():
            if event_id(event) in sent or key in completed:
                continue
            if event["stage"] in TERMINAL:
                # Close an already-notified candidate even after a restart/delay.
                eligible = key in observed
            else:
                eligible = 0 <= now_ms - event["timestamp"] <= cfg.max_notice_age_seconds * 1000
            if eligible:
                notices.append(event)
        return sorted(notices, key=lambda e: (e["timestamp"], e["symbol"], e["id"]))

    def acknowledge(self, event):
        self.state["sent"][event_id(event)] = {"symbol": event["symbol"], "setup_id": event["id"],
                                               "stage": event["stage"], "timestamp": event["timestamp"]}
        self._save()

    def dispatch(self, events, now_ms, sender, cfg=DEFAULTS):
        result = {"sent": 0, "failed": 0}
        for event in self.candidates(events, now_ms, cfg):
            try:
                success = sender(format_notice(event, cfg)) is True
            except Exception:
                success = False
            if success:
                self.acknowledge(event)
                result["sent"] += 1
            else:
                result["failed"] += 1
        return result


@contextmanager
def single_writer(path):
    """OS lock released on process exit, including crashes (Windows and Linux)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def telegram_sender(token, chat_id):
    if not token or not chat_id:
        raise ValueError("Telegram credentials are missing")

    def send(text):
        import requests
        try:
            response = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                     json={"chat_id": chat_id, "text": text}, timeout=15)
            return response.status_code == 200 and response.json().get("ok") is True
        except (requests.RequestException, ValueError):
            # Never print requests exceptions: their URLs can contain the token.
            return False
    return send
