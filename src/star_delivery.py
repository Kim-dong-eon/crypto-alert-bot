"""One delivery per parent and grade, with persistent upgrades and receipts."""

import copy

from src.alert_confluence import confluence_error

from src.ict_engine import TF_MS
from src.ict_overlap import alert_eligible
from src.pullback_delivery import delivery_started, physical_id, POLICY
from src.star_alerts import RULESET, atr_label, is_entry
from src.staircase_rules import MIN_RR
from src.structure_alerts import StructureLedger, candidates
from config.overlap_settings import TRACK_LABELS


def alert_bands(candidate):
    yield candidate['low'], candidate['high']
    # A ★★★ setup also owns the original confluence shown on its chart.
    if is_entry(candidate) and candidate.get('overlap'):
        yield tuple(candidate['overlap']['band'])


def bands_overlap(a, b):
    return any(
        max(a_low, b_low) < min(a_high, b_high)
        for a_low, a_high in alert_bands(a) for b_low, b_high in alert_bands(b)
    )


def intersects(a, b):
    if b.get('stock_reference') and b.get('score', 1) < 3 and a.get('score', 3) >= 3:
        return False
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
    comparisons = []
    for name, state in tracks.items():
        if name == track or name not in TRACK_LABELS:
            continue
        label = TRACK_LABELS[name]
        opposing_atrs = [n for n, side in state.get('atr_directions', {}).items() if side == -direction]
        if opposing_atrs:
            warnings.append(f"{label} {'롱' if direction == -1 else '숏'} 구조 ({', '.join(map(atr_label, opposing_atrs))})")
        overlapping = [other for other in (*contacts, *locks)
                       if other['track'] == name and bands_overlap(candidate, other)]
        for side in sorted({other['direction'] for other in overlapping}):
            warnings.append(f"{label} {'롱' if side == 1 else '숏'} 구간 겹침")
        if opposing_atrs or overlapping:
            waves = state.get('atr_waves', {})
            wave_atr = next((n for n in sorted(opposing_atrs) if waves.get(n)), None)
            if wave_atr is None:
                wave_atr = next((n for n in sorted(waves) if waves[n]), None)
            comparisons.append({'track': name, 'tf': state.get('pair', [None])[0],
                                'opposing_atrs': opposing_atrs, 'wave_atr': wave_atr,
                                'wave': waves.get(wave_atr, []),
                                'bands': sorted({(lo, hi, other['direction']) for other in overlapping
                                                 for lo, hi in alert_bands(other)})})
    return {**candidate, 'matched_atrs': sorted(matched),
            'atr_directions': candidate.get('atr_directions', tracks[track].get('atr_directions', {})),
            'cross_track_warnings': warnings, 'cross_track_charts': comparisons}


def shadowed_by_higher(candidate, blockers):
    return any(other['level'] > candidate['level'] and intersects(candidate, other) for other in blockers)


def eligible_contacts(tracks, entry_only=False, require_execution=False, require_confluence=True):
    return [c for row in tracks.values() for c in candidates(row)
            if alert_eligible(c) and (not require_confluence or confluence_error(c) is None)
            and (not entry_only or executable(c))
            and (not require_execution or not is_entry(c) or executable(c))]


def executable(candidate):
    return is_entry(candidate) and candidate.get('execution') and candidate['rr'] >= MIN_RR - 1e-9


def unique_contacts(tracks, entry_only=False, require_execution=False, require_confluence=True):
    ranked = sorted(eligible_contacts(tracks, entry_only, require_execution, require_confluence),
                    key=lambda c: (-c['level'], bool(c.get('stock_reference')), -TF_MS[c['pair'][0]],
                                   -bool(c.get('in_initial_overlap')), -c['score'], c['id']))
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
    def __init__(self, path, *, entry_only=False, require_execution=False, require_confluence=True, **kwargs):
        super().__init__(path, **kwargs)
        # Disabling this is only for reproducing obsolete historical strategies.
        self.require_confluence = require_confluence
        self.entry_only = entry_only
        self.require_execution = require_execution or entry_only
        self.state.setdefault('star_contexts', {})
        self.state.setdefault('star_locks', {})
        self.state.setdefault('presentations', {})
        if self.require_confluence:
            # Retain receipts for audit, but never retry a single-OB presentation
            # or let its old lock suppress a newly verified three-frame contact.
            for key, p in list(self.state.get('presentations', {}).items()):
                if reason := confluence_error(p.get('candidate') or {}):
                    self.state.setdefault('retired_presentations', {})[f"{key}:{p.get('attempt', 0)}"] = {
                        **p, 'retired_reason': reason}
                    del self.state['presentations'][key]
            for symbol, locks in self.state['star_locks'].items():
                for key, lock in list(locks.items()):
                    if lock.get('score', 0) < 3:
                        self.state.setdefault('retired_star_locks', {}).setdefault(symbol, {})[key] = lock
                        del locks[key]
            for key, event in list(self.state.get('pending', {}).items()):
                if confluence_error(event.get('candidate') or {}):
                    del self.state['pending'][key]
        if self.state.get('delivery_mode') == RULESET:
            return
        old_reaction_mode = self.state.get('delivery_mode') == 'star-ob-reaction-v1'
        if old_reaction_mode:
            # A previously sent close-break ★★ is not the new entry ★★. Keep
            # its first-contact lock and receipt, but allow a genuine upgrade.
            for locks in self.state['star_locks'].values():
                for lock in locks.values():
                    if lock['level'] == 2:
                        lock['legacy_level'] = 2
                        lock['level'] = 1
        for old_locks in self.state.get('locks', {}).values():
            for lock in old_locks:
                c = lock.get('latest') or {}
                if lock.get('sent') and c.get('alert_policy') == POLICY and c.get('parent'):
                    self._remember(c, 3)
        for key, p in list(self.state.get('presentations', {}).items()):
            c = p.get('candidate') or {}
            if c.get('alert_policy') == POLICY and c.get('parent') and (p.get('complete') or delivery_started(p)):
                level = c.get('level', 3)
                self._remember(c, 1 if old_reaction_mode and level == 2 else level)
            if old_reaction_mode or not p.get('complete'):
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
                          'direction': c['direction'], 'low': c['low'], 'high': c['high'],
                          'score': c.get('score', len(c.get('sources', ())))}
            if c.get('stock_reference'):
                locks[key]['stock_reference'] = c['stock_reference']
            if c.get('phase'):
                locks[key]['phase'] = c['phase']
        if locks[key]['level'] == level and is_entry(c) and c.get('overlap'):
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
                    'tracks', 'delivered', 'images_delivered', 'touch_cursors', 'track_touch_cursors', 'dedup_audits'):
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
            if (c.get('symbol') == symbol and c.get('ruleset') == RULESET
                    and (not self.require_confluence or confluence_error(c) is None)
                    and (p.get('complete') or delivery_started(p))):
                self._remember(c, c['level'])
        for key, lock in list(locks.items()):
            parent = lock['parent']
            source = 'cash' if lock.get('stock_reference') else 'bitget'
            source_status = snapshot.get('source_status')
            if source_status is not None and source_status.get(source, {}).get(lock['track'], 'data_unavailable') == 'data_unavailable':
                continue
            start = snapshot['history_starts'].get(parent['timeframe'])
            if start is not None and parent['origin'] >= start and parent['id'] not in active_ids:
                del locks[key]

        contacts = eligible_contacts(snapshot['tracks'], self.entry_only, self.require_execution, self.require_confluence)
        selected = unique_contacts(snapshot['tracks'], self.entry_only, self.require_execution, self.require_confluence)
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
            duplicate_band = any(lock['level'] >= c['level'] and intersects(c, lock) for lock in active_locks)
            formation = c.get('formation') or c.get('overlap', {}).get('formation')
            if unfinished and formation and p['candidate']['context_id'] != c['context_id']:
                if delivery_started(p):
                    continue
                del presentations[key]
                unfinished = False
            if not self.force_resend and (same_parent or duplicate_band) and not unfinished:
                suppressed += 1
                continue
            if self.require_execution and is_entry(c) and unfinished and not executable(p['candidate']):
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
            if self.require_confluence and confluence_error(c):
                continue
            if self.require_execution and is_entry(c):
                continue
            source = 'cash' if c.get('stock_reference') else 'bitget'
            if snapshot.get('source_status') is not None and snapshot['source_status'].get(source, {}).get(c['track'], 'data_unavailable') == 'data_unavailable':
                continue
            if (not c.get('stock_reference') and c['context_id'] not in episodes
                    or not set(c['source_ids']).issubset(active_ids)):
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
            if records and all((r.get('image_sent') or (c.get('first_touch_text') and r.get('text_sent')))
                               and not r.get('in_flight') for r in records):
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
            p = self.state['presentations'].get(key)
            if self.require_confluence and (confluence_error(c) or
                    (p and confluence_error(p['candidate']))):
                del self.state['pending'][key]
                continue
            if (self.entry_only or (self.require_execution and is_entry(c))) and not executable(c):
                del self.state['pending'][key]
                continue
            if refresh is not None and is_entry(c):
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
