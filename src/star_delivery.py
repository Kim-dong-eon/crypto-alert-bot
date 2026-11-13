"""One delivery per parent and grade, with persistent upgrades and receipts."""

import copy

from src.ict_engine import TF_MS
from src.ict_overlap import alert_eligible
from src.pullback_delivery import delivery_started, physical_id, POLICY
from src.star_alerts import RULESET, atr_label
from src.staircase_rules import MIN_RR
from src.structure_alerts import StructureLedger, candidates
from config.overlap_settings import TRACK_LABELS


def alert_bands(candidate):
    yield candidate['low'], candidate['high']
    # A ★★★ setup also owns the original confluence shown on its chart.
    if candidate['level'] == 3 and candidate.get('overlap'):
        yield tuple(candidate['overlap']['band'])


def bands_overlap(a, b):
    return any(
        max(a_low, b_low) < min(a_high, b_high)
        for a_low, a_high in alert_bands(a) for b_low, b_high in alert_bands(b)
    )


def intersects(a, b):
    if ({a.get('track'), b.get('track')} == {'fast', 'short'}
            and a.get('upper_atr') is None and b.get('upper_atr') is None):
        return False
    return a['direction'] == b['direction'] and bands_overlap(a, b)


def describe_atr_signal(candidate, tracks, contacts, locks):
    track = candidate['track']
    direction = candidate['direction']
    identity = physical_id(candidate)
    matched = {c['upper_atr'] for c in contacts
               if c.get('upper_atr') is not None and c['level'] == candidate['level']
               and physical_id(c) == identity and bands_overlap(c, candidate)}
    warnings = []
    for name, state in tracks.items():
        if name == track or name not in TRACK_LABELS:
            continue
        label = TRACK_LABELS[name]
        opposing_atrs = [atr_label(n) for n, side in state.get('atr_directions', {}).items() if side == -direction]
        if opposing_atrs:
            warnings.append(f"{label} {'롱' if direction == -1 else '숏'} 구조 ({', '.join(opposing_atrs)})")
        overlaps = {other['direction'] for other in (*contacts, *locks)
                    if other['track'] == name and bands_overlap(candidate, other)}
        for side in sorted(overlaps):
            warnings.append(f"{label} {'롱' if side == 1 else '숏'} 구간 겹침")
    return {**candidate, 'matched_atrs': sorted(matched),
            'atr_directions': tracks[track].get('atr_directions', {}), 'cross_track_warnings': warnings}


def shadowed_by_higher(candidate, blockers):
    return any(other['level'] > candidate['level'] and intersects(candidate, other) for other in blockers)


def eligible_contacts(tracks, entry_only=False, require_execution=False):
    return [c for row in tracks.values() for c in candidates(row)
            if alert_eligible(c) and (not entry_only or executable(c))
            and (not require_execution or c['level'] < 3 or executable(c))]


def executable(candidate):
    return candidate.get('level') == 3 and candidate.get('execution') and candidate['rr'] >= MIN_RR - 1e-9


def unique_contacts(tracks, entry_only=False, require_execution=False):
    ranked = sorted(eligible_contacts(tracks, entry_only, require_execution),
                    key=lambda c: (-c['level'], -TF_MS[c['pair'][0]], -c['score'], c['id']))
    accepted = []
    seen = set()
    for index, candidate in enumerate(ranked):
        identity = physical_id(candidate)
        if identity in seen or any(intersects(candidate, other) for other in accepted):
            continue
        if shadowed_by_higher(candidate, ranked[:index]):
            continue
        accepted.append(candidate)
        seen.add(identity)
    return accepted


class StarAlertLedger(StructureLedger):
    def __init__(self, path, *, entry_only=False, require_execution=False, **kwargs):
        super().__init__(path, **kwargs)
        self.entry_only = entry_only
        self.require_execution = require_execution or entry_only
        self.state.setdefault('star_contexts', {})
        self.state.setdefault('star_locks', {})
        if self.state.get('delivery_mode') == RULESET:
            return
        for old_locks in self.state.get('locks', {}).values():
            for lock in old_locks:
                c = lock.get('latest') or {}
                if lock.get('sent') and c.get('alert_policy') == POLICY and c.get('parent'):
                    self._remember(c, 3)
        for key, p in list(self.state.get('presentations', {}).items()):
            c = p.get('candidate') or {}
            if c.get('alert_policy') == POLICY and c.get('parent') and (p.get('complete') or delivery_started(p)):
                self._remember(c, c.get('level', 3))
            if not p.get('complete'):
                self.state.setdefault('retired_presentations', {})[f"{key}:{p.get('attempt', 0)}"] = p
                del self.state['presentations'][key]
        self.state['pending'] = {}
        self.state['locks'] = {}
        self.state['delivery_mode'] = RULESET
        self.state['notification_policy'] = RULESET
        self.state['strategy'] = POLICY

    def _remember(self, c, level):
        locks = self.state['star_locks'].setdefault(c['symbol'], {})
        key = physical_id(c)
        old = locks.get(key)
        if old is None or level > old['level']:
            locks[key] = {'track': c['track'], 'level': level, 'parent': c['parent'],
                          'direction': c['direction'], 'low': c['low'], 'high': c['high']}
        if locks[key]['level'] == level == 3 and c.get('overlap'):
            locks[key].setdefault('overlap', {'band': c['overlap']['band']})

    def unlock(self, symbol, track):
        locks = self.state['star_locks'].get(symbol, {})
        self.state['star_locks'][symbol] = {key: lock for key, lock in locks.items() if lock['track'] != track}
        for key, event in list(self.state['pending'].items()):
            candidate = event['candidate']
            if (candidate['symbol'], candidate['track']) == (symbol, track):
                del self.state['pending'][key]
        super().unlock(symbol, track)

    def reset_alerts(self, now):
        retired = self.state.setdefault('retired_presentations', {})
        for key, presentation in self.state.get('presentations', {}).items():
            retired[f"{key}:{presentation['attempt']}"] = presentation
        for key in ('presentations', 'pending', 'in_flight', 'locks', 'star_locks', 'star_contexts',
                    'tracks', 'delivered', 'images_delivered', 'touch_cursors', 'dedup_audits'):
            self.state[key] = {}
        self.state['alert_reset_at'] = now
        self.save()

    def observe(self, snapshot, display_name=None, persist=True):
        if snapshot.get('ruleset') != RULESET:
            raise ValueError('Unexpected star policy')
        before = copy.deepcopy(self.state) if not persist else None
        symbol = snapshot['symbol']
        now = snapshot['as_of']
        active_ids = set(snapshot['valid_source_ids'])
        episodes = snapshot['star_contexts']
        self.state['star_contexts'][symbol] = copy.deepcopy(episodes)
        locks = self.state['star_locks'].setdefault(symbol, {})
        presentations = self.state.setdefault('presentations', {})
        pending = self.state['pending']
        for key, event in list(pending.items()):
            if event['candidate']['symbol'] == symbol:
                del pending[key]
        for p in presentations.values():
            c = p.get('candidate') or {}
            if c.get('symbol') == symbol and c.get('ruleset') == RULESET and (p.get('complete') or delivery_started(p)):
                self._remember(c, c['level'])
        for key, lock in list(locks.items()):
            parent = lock['parent']
            start = snapshot['history_starts'].get(parent['timeframe'])
            if start is not None and parent['origin'] >= start and parent['id'] not in active_ids:
                del locks[key]

        contacts = eligible_contacts(snapshot['tracks'], self.entry_only, self.require_execution)
        selected = unique_contacts(snapshot['tracks'], self.entry_only, self.require_execution)
        disabled = set(snapshot.get('disabled_tracks', ()))
        active_locks = [lock for lock in locks.values()
                        if lock['track'] not in disabled and lock['parent']['id'] in active_ids]
        selected = [describe_atr_signal(c, snapshot['tracks'], contacts, active_locks)
                    if c.get('upper_atr') is not None else c for c in selected]
        # Keep the scan, image previews and delivery on the same selections.
        for track, row in snapshot['tracks'].items():
            row['candidates'] = [c for c in selected if c['track'] == track]
            row['candidate'] = next(iter(row['candidates']), None)
        suppressed = 0
        for c in selected:
            c = {**c, 'id': physical_id(c)}
            stage = f"star{c['level']}"
            key = self.key(c, stage)
            p = presentations.get(key)
            unfinished = bool(p and not p['complete'])
            if shadowed_by_higher(c, active_locks):
                suppressed += 1
                continue
            same_parent = locks.get(c['id'], {}).get('level', 0) >= c['level']
            duplicate_band = any(lock['level'] >= c['level'] and intersects(lock, c) for lock in active_locks)
            formation = c.get('formation') or c.get('overlap', {}).get('formation')
            if unfinished and formation and p['candidate']['context_id'] != c['context_id']:
                if delivery_started(p):
                    continue
                del presentations[key]
                unfinished = False
            if not self.force_resend and (same_parent or duplicate_band) and not unfinished:
                suppressed += 1
                continue
            if self.require_execution and c['level'] == 3 and unfinished and not executable(p['candidate']):
                if delivery_started(p):
                    continue
                del presentations[key]
                unfinished = False
            pending[key] = {'candidate': p['candidate'] if unfinished else c, 'stage': stage,
                            'timestamp': p['attempt'] if unfinished else now, 'display_name': display_name or symbol}

        # A confirmed photo can receive its text after the touch minute passes.
        selected_grades = {physical_id(c): c['level'] for c in selected}
        for key, p in (() if self.entry_only else presentations.items()):
            c = p.get('candidate') or {}
            if key in pending or p.get('complete') or c.get('symbol') != symbol or c.get('ruleset') != RULESET:
                continue
            if self.require_execution and c['level'] == 3:
                continue
            if c['context_id'] not in episodes or not set(c['source_ids']).issubset(active_ids):
                continue
            if snapshot['tracks'].get(c['track'], {}).get('status', 'data_unavailable') == 'data_unavailable':
                continue
            if max(locks.get(c['id'], {}).get('level', 0), selected_grades.get(c['id'], 0)) > c['level']:
                continue
            if shadowed_by_higher(c, active_locks) or shadowed_by_higher(c, contacts):
                continue
            if any(physical_id(other) != c['id'] and other['level'] >= c['level'] and intersects(other, c)
                   for other in selected):
                continue
            records = list(p['units'].values())
            if records and all(r.get('image_sent') and not r.get('in_flight') for r in records):
                pending[key] = {'candidate': c, 'stage': p['stage'], 'timestamp': p['attempt'], 'display_name': display_name or symbol}

        self.state.setdefault('dedup_audits', {})[symbol] = {'at': now, 'suppressed': suppressed,
                                                           'one_off_resend': self.force_resend, 'locking_enabled': True}
        result = copy.deepcopy(pending)
        if persist:
            self.save()
        else:
            self.state = before
        return result

    def dispatch_units(self, image_sender, text_sender, renderer, *, refresh=None):
        for key, event in list(self.state['pending'].items()):
            c = event['candidate']
            if (self.entry_only or (self.require_execution and c['level'] == 3)) and not executable(c):
                del self.state['pending'][key]
                continue
            if refresh is not None and c['level'] == 3:
                checked = refresh(c)
                if checked is None:
                    del self.state['pending'][key]
                    continue
                p = self.state['presentations'].get(key)
                if p and not p['complete']:
                    if delivery_started(p):
                        # A partial delivery must retain the same chart and text.
                        if checked['band'] != c['band']:
                            del self.state['pending'][key]
                        continue
                    p['candidate'] = checked
                event['candidate'] = checked
        attempted = tuple(self.state['pending'])
        result = super().dispatch_units(image_sender, text_sender, renderer)
        for key in attempted:
            p = self.state.get('presentations', {}).get(key, {})
            c = p.get('candidate') or {}
            if c.get('ruleset') == RULESET and (p.get('complete') or delivery_started(p)):
                self._remember(c, c['level'])
        self.force_resend = False
        self.save()
        return result
