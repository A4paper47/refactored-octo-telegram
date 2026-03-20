from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from db import db
from models import (
    Movie,
    Assignment,
    TranslationTask,
    TranslationSubmission,
    VORoleSubmission,
    GroupOpenRequest,
    MovieEvent,
)
from movie_history import record_movie_event


def normalize_title(value: str | None) -> str:
    s = (value or '').strip().lower()
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def movie_signature(movie: Movie | None) -> tuple[str, str, str]:
    if not movie:
        return ('', '', '')
    return (
        normalize_title(getattr(movie, 'title', None)),
        str(getattr(movie, 'year', None) or '').strip(),
        str(getattr(movie, 'lang', None) or '').strip().lower(),
    )


def _sort_movies(rows: list[Movie]) -> list[Movie]:
    return sorted(
        rows,
        key=lambda m: (
            0 if not bool(getattr(m, 'is_archived', False)) else 1,
            -int(getattr(m, 'id', 0) or 0),
        ),
    )


def duplicate_groups(q: str = '', limit: int = 24, include_archived: bool = True) -> list[dict[str, Any]]:
    rows_q = Movie.query
    if not include_archived:
        rows_q = rows_q.filter((Movie.is_archived == False) | (Movie.is_archived.is_(None)))
    rows = rows_q.order_by(Movie.updated_at.desc().nullslast(), Movie.id.desc()).all()
    groups: dict[tuple[str, str, str], list[Movie]] = {}
    for m in rows:
        sig = movie_signature(m)
        if not sig[0]:
            continue
        groups.setdefault(sig, []).append(m)

    out: list[dict[str, Any]] = []
    q_norm = normalize_title(q)
    for sig, items in groups.items():
        if len(items) < 2:
            continue
        items = _sort_movies(items)
        codes = [str((m.code or '')).strip() for m in items if (m.code or '').strip()]
        titles = [str((m.title or '')).strip() for m in items if (m.title or '').strip()]
        text_blob = ' '.join(codes + titles + [sig[0], sig[1], sig[2]]).lower()
        if q_norm and q_norm not in normalize_title(text_blob):
            continue
        target = items[0]
        out.append({
            'signature': sig,
            'target': target,
            'items': items,
            'count': len(items),
            'active_count': len([m for m in items if not bool(getattr(m, 'is_archived', False))]),
            'archived_count': len([m for m in items if bool(getattr(m, 'is_archived', False))]),
            'title': target.title,
            'year': target.year,
            'lang': target.lang,
        })
    out.sort(key=lambda g: (-int(g['count']), 0 if not bool(getattr(g['target'], 'is_archived', False)) else 1, -int(getattr(g['target'], 'id', 0) or 0)))
    return out[:max(1, min(int(limit or 24), 100))]


def _assignment_rows(movie: Movie | None) -> list[Assignment]:
    if not movie:
        return []
    mid = getattr(movie, 'id', None)
    code = str(getattr(movie, 'code', None) or '').strip()
    return Assignment.query.filter((Assignment.movie_id == mid) | (Assignment.project == code)).all()


def _translation_task_rows(movie: Movie | None) -> list[TranslationTask]:
    if not movie:
        return []
    mid = getattr(movie, 'id', None)
    code = str(getattr(movie, 'code', None) or '').strip()
    return TranslationTask.query.filter((TranslationTask.movie_id == mid) | (TranslationTask.movie_code == code)).all()


def _translation_submission_rows(movie: Movie | None) -> list[TranslationSubmission]:
    if not movie:
        return []
    mid = getattr(movie, 'id', None)
    code = str(getattr(movie, 'code', None) or '').strip()
    return TranslationSubmission.query.filter((TranslationSubmission.movie_id == mid) | (TranslationSubmission.movie == code)).all()


def _vo_submission_rows(movie: Movie | None) -> list[VORoleSubmission]:
    if not movie:
        return []
    code = str(getattr(movie, 'code', None) or '').strip()
    return VORoleSubmission.query.filter_by(movie=code).all()


def _group_request_rows(movie: Movie | None) -> list[GroupOpenRequest]:
    if not movie:
        return []
    mid = getattr(movie, 'id', None)
    code = str(getattr(movie, 'code', None) or '').strip()
    return GroupOpenRequest.query.filter((GroupOpenRequest.movie_id == mid) | (GroupOpenRequest.movie_code == code)).all()


def _event_rows(movie: Movie | None) -> list[MovieEvent]:
    if not movie:
        return []
    mid = getattr(movie, 'id', None)
    code = str(getattr(movie, 'code', None) or '').strip()
    return MovieEvent.query.filter((MovieEvent.movie_id == mid) | (MovieEvent.movie_code == code)).all()


def _sorted_unique(values: list[str]) -> list[str]:
    return sorted({str(v).strip() for v in values if str(v or '').strip()})


def merge_preview(source: Movie, target: Movie) -> dict[str, Any]:
    src_assignments = _assignment_rows(source)
    tgt_assignments = _assignment_rows(target)
    src_vo_subs = _vo_submission_rows(source)
    tgt_vo_subs = _vo_submission_rows(target)
    src_tasks = _translation_task_rows(source)
    tgt_tasks = _translation_task_rows(target)
    src_ts = _translation_submission_rows(source)
    src_group = _group_request_rows(source)
    src_events = _event_rows(source)

    counts = {
        'assignments': len(src_assignments),
        'vo_submissions': len(src_vo_subs),
        'translation_tasks': len(src_tasks),
        'translation_submissions': len(src_ts),
        'group_requests': len(src_group),
        'events': len(src_events),
    }
    counts['total_rows'] = sum(v for v in counts.values())

    assignment_role_overlap = _sorted_unique([a.role for a in src_assignments if a.role] + [])
    target_assignment_roles = {str(a.role or '').strip() for a in tgt_assignments if str(a.role or '').strip()}
    assignment_role_overlap = sorted(r for r in assignment_role_overlap if r in target_assignment_roles)

    vo_role_overlap = _sorted_unique([r.role for r in src_vo_subs if r.role])
    target_vo_roles = {str(r.role or '').strip() for r in tgt_vo_subs if str(r.role or '').strip()}
    vo_role_overlap = sorted(r for r in vo_role_overlap if r in target_vo_roles)

    src_task_people = _sorted_unique([t.translator_name for t in src_tasks if t.translator_name])
    tgt_task_people = _sorted_unique([t.translator_name for t in tgt_tasks if t.translator_name])
    src_task_statuses = _sorted_unique([t.status for t in src_tasks if t.status])
    tgt_task_statuses = _sorted_unique([t.status for t in tgt_tasks if t.status])

    warnings: list[str] = []
    severity = 'low'

    src_translator = str(getattr(source, 'translator_assigned', None) or '').strip()
    tgt_translator = str(getattr(target, 'translator_assigned', None) or '').strip()
    translator_conflict = bool(src_translator and tgt_translator and src_translator.lower() != tgt_translator.lower())
    if translator_conflict:
        warnings.append(f'Translator differs: source={src_translator} vs target={tgt_translator}')
        severity = 'high'

    if src_tasks and tgt_tasks:
        warnings.append(
            'Both source and target already have translation task rows'
            + (f' ({", ".join(src_task_people or ["-"])} → {", ".join(tgt_task_people or ["-"])})' if (src_task_people or tgt_task_people) else '')
        )
        severity = 'high'

    if assignment_role_overlap:
        preview_roles = ', '.join(assignment_role_overlap[:6])
        extra = '' if len(assignment_role_overlap) <= 6 else f' +{len(assignment_role_overlap) - 6} more'
        warnings.append(f'Assignment role overlap on target: {preview_roles}{extra}')
        severity = 'high'

    if vo_role_overlap:
        preview_roles = ', '.join(vo_role_overlap[:6])
        extra = '' if len(vo_role_overlap) <= 6 else f' +{len(vo_role_overlap) - 6} more'
        warnings.append(f'VO submission overlap on target: {preview_roles}{extra}')
        severity = 'high'

    src_year = str(getattr(source, 'year', None) or '').strip()
    tgt_year = str(getattr(target, 'year', None) or '').strip()
    if src_year and tgt_year and src_year != tgt_year:
        warnings.append(f'Year differs: source={src_year} vs target={tgt_year}')
        severity = 'medium' if severity == 'low' else severity

    src_lang = str(getattr(source, 'lang', None) or '').strip().lower()
    tgt_lang = str(getattr(target, 'lang', None) or '').strip().lower()
    if src_lang and tgt_lang and src_lang != tgt_lang:
        warnings.append(f'Language differs: source={src_lang.upper()} vs target={tgt_lang.upper()}')
        severity = 'high'

    src_norm = normalize_title(getattr(source, 'title', None))
    tgt_norm = normalize_title(getattr(target, 'title', None))
    if src_norm and tgt_norm and src_norm != tgt_norm:
        warnings.append('Normalized title is different — merge may not be a true duplicate')
        severity = 'medium' if severity == 'low' else severity

    movie_card_conflict = bool(
        getattr(source, 'movie_card_chat_id', None)
        and getattr(target, 'movie_card_chat_id', None)
        and int(getattr(source, 'movie_card_chat_id')) != int(getattr(target, 'movie_card_chat_id'))
    )
    if movie_card_conflict:
        warnings.append('Both source and target already have different Telegram movie cards')
        severity = 'medium' if severity == 'low' else severity

    vo_group_conflict = bool(
        getattr(source, 'vo_group_chat_id', None)
        and getattr(target, 'vo_group_chat_id', None)
        and int(getattr(source, 'vo_group_chat_id')) != int(getattr(target, 'vo_group_chat_id'))
    )
    if vo_group_conflict:
        warnings.append('Both source and target already have different bound VO groups')
        severity = 'high'

    pending_group_conflict = bool(src_group and _group_request_rows(target))
    if pending_group_conflict:
        warnings.append('Both source and target already have group request rows')
        severity = 'medium' if severity == 'low' else severity

    counts.update({
        'assignment_role_overlap_count': len(assignment_role_overlap),
        'vo_submission_overlap_count': len(vo_role_overlap),
        'warnings_count': len(warnings),
    })
    counts['warnings'] = warnings
    counts['severity'] = severity
    counts['assignment_role_overlap'] = assignment_role_overlap
    counts['vo_submission_role_overlap'] = vo_role_overlap
    counts['translator_conflict'] = translator_conflict
    counts['translation_task_conflict'] = bool(src_tasks and tgt_tasks)
    counts['translation_task_people_source'] = src_task_people
    counts['translation_task_people_target'] = tgt_task_people
    counts['translation_task_statuses_source'] = src_task_statuses
    counts['translation_task_statuses_target'] = tgt_task_statuses
    counts['movie_card_conflict'] = movie_card_conflict
    counts['vo_group_conflict'] = vo_group_conflict
    counts['group_request_conflict'] = pending_group_conflict
    return counts


def _pick_status(target: Movie, source: Movie) -> str:
    t = str(getattr(target, 'status', None) or '').strip().upper()
    s = str(getattr(source, 'status', None) or '').strip().upper()
    if t and t not in {'NEW', 'RECEIVED', 'ARCHIVED'}:
        return getattr(target, 'status', None) or t
    if s and s not in {'NEW', 'ARCHIVED'}:
        return getattr(source, 'status', None) or s
    return getattr(target, 'status', None) or getattr(source, 'status', None) or 'RECEIVED'


def _min_dt(a, b):
    if a and b:
        return a if a <= b else b
    return a or b


def _max_dt(a, b):
    if a and b:
        return a if a >= b else b
    return a or b


def merge_movies(source: Movie, target: Movie, actor_source: str = 'web', actor_name: str = 'movie_merge', delete_source: bool = False) -> dict[str, Any]:
    if not source or not target:
        raise ValueError('Source and target movie are required')
    if int(source.id) == int(target.id):
        raise ValueError('Source and target must be different movies')

    preview = merge_preview(source, target)
    src_id = source.id
    src_code = str(source.code or '').strip()
    tgt_id = target.id
    tgt_code = str(target.code or '').strip()

    for a in Assignment.query.filter((Assignment.movie_id == src_id) | (Assignment.project == src_code)).all():
        a.movie_id = tgt_id
        a.project = tgt_code
    for row in VORoleSubmission.query.filter_by(movie=src_code).all():
        row.movie = tgt_code
    for row in TranslationTask.query.filter((TranslationTask.movie_id == src_id) | (TranslationTask.movie_code == src_code)).all():
        row.movie_id = tgt_id
        row.movie_code = tgt_code
        row.title = target.title
        row.year = target.year
        row.lang = target.lang
    for row in TranslationSubmission.query.filter((TranslationSubmission.movie_id == src_id) | (TranslationSubmission.movie == src_code)).all():
        row.movie_id = tgt_id
        row.movie = tgt_code
    for row in GroupOpenRequest.query.filter((GroupOpenRequest.movie_id == src_id) | (GroupOpenRequest.movie_code == src_code)).all():
        row.movie_id = tgt_id
        row.movie_code = tgt_code
    for row in MovieEvent.query.filter((MovieEvent.movie_id == src_id) | (MovieEvent.movie_code == src_code)).all():
        row.movie_id = tgt_id
        row.movie_code = tgt_code
        row.movie_title = target.title

    # Best-effort metadata carry-over.
    target.title = target.title or source.title
    target.year = target.year or source.year
    target.lang = target.lang or source.lang
    target.translator_assigned = target.translator_assigned or source.translator_assigned
    target.movie_card_chat_id = target.movie_card_chat_id or source.movie_card_chat_id
    target.movie_card_message_id = target.movie_card_message_id or source.movie_card_message_id
    target.vo_group_chat_id = target.vo_group_chat_id or source.vo_group_chat_id
    target.vo_group_invite_link = target.vo_group_invite_link or source.vo_group_invite_link
    target.received_at = _min_dt(target.received_at, source.received_at)
    target.submitted_at = _min_dt(target.submitted_at, source.submitted_at)
    target.completed_at = _max_dt(target.completed_at, source.completed_at)
    target.status = _pick_status(target, source)
    target.is_archived = False
    target.archived_at = None
    target.updated_at = datetime.utcnow()

    detail = ' • '.join([
        f'merged_from={src_code or source.id}',
        f'moved_rows={preview.get("total_rows", 0)}',
        f'warnings={preview.get("warnings_count", 0)}',
        f'severity={preview.get("severity", "low")}',
    ])
    record_movie_event(target, 'MERGE_IN', f'Merged duplicate movie {source.title or src_code}', detail=detail, actor_source=actor_source, actor_name=actor_name)

    source.translator_assigned = None
    source.movie_card_chat_id = None
    source.movie_card_message_id = None
    source.vo_group_chat_id = None
    source.vo_group_invite_link = None
    source.updated_at = datetime.utcnow()

    if delete_source:
        db.session.delete(source)
        source_state = 'hard_deleted'
    else:
        source.is_archived = True
        source.archived_at = datetime.utcnow()
        source.status = 'MERGED'
        record_movie_event(source, 'MERGE_OUT', f'Merged into {target.title or tgt_code}', detail=f'target={tgt_code}', actor_source=actor_source, actor_name=actor_name)
        source_state = 'archived'

    return {
        'source_code': src_code,
        'target_code': tgt_code,
        'source_state': source_state,
        'moved': preview,
    }


def merge_simulation(source: Movie, target: Movie) -> dict[str, Any]:
    preview = merge_preview(source, target)

    src_assignments = _assignment_rows(source)
    tgt_assignments = _assignment_rows(target)
    src_vo_subs = _vo_submission_rows(source)
    tgt_vo_subs = _vo_submission_rows(target)
    src_tasks = _translation_task_rows(source)
    tgt_tasks = _translation_task_rows(target)
    src_ts = _translation_submission_rows(source)
    tgt_ts = _translation_submission_rows(target)
    src_group = _group_request_rows(source)
    tgt_group = _group_request_rows(target)
    src_events = _event_rows(source)
    tgt_events = _event_rows(target)

    def _role_set(rows):
        return _sorted_unique([getattr(r, 'role', None) for r in rows if getattr(r, 'role', None)])

    def _vo_name_set(rows):
        names = []
        for r in rows:
            name = getattr(r, 'vo_name', None) or getattr(r, 'voice_actor', None) or getattr(r, 'submitted_by', None)
            if name:
                names.append(name)
        return _sorted_unique(names)

    src_roles = _role_set(src_assignments)
    tgt_roles = _role_set(tgt_assignments)
    src_vo_roles = _role_set(src_vo_subs)
    tgt_vo_roles = _role_set(tgt_vo_subs)

    src_people = _sorted_unique([getattr(t, 'translator_name', None) for t in src_tasks if getattr(t, 'translator_name', None)])
    tgt_people = _sorted_unique([getattr(t, 'translator_name', None) for t in tgt_tasks if getattr(t, 'translator_name', None)])

    comparison = {
        'source': {
            'code': source.code,
            'title': source.title,
            'year': source.year,
            'lang': source.lang,
            'status': source.status,
            'translator_assigned': getattr(source, 'translator_assigned', None),
            'movie_card_chat_id': getattr(source, 'movie_card_chat_id', None),
            'vo_group_chat_id': getattr(source, 'vo_group_chat_id', None),
            'is_archived': bool(getattr(source, 'is_archived', False)),
            'assignment_roles': src_roles,
            'vo_submission_roles': src_vo_roles,
            'translation_task_people': src_people,
            'vo_submitters': _vo_name_set(src_vo_subs),
            'counts': {
                'assignments': len(src_assignments),
                'vo_submissions': len(src_vo_subs),
                'translation_tasks': len(src_tasks),
                'translation_submissions': len(src_ts),
                'group_requests': len(src_group),
                'events': len(src_events),
            },
        },
        'target': {
            'code': target.code,
            'title': target.title,
            'year': target.year,
            'lang': target.lang,
            'status': target.status,
            'translator_assigned': getattr(target, 'translator_assigned', None),
            'movie_card_chat_id': getattr(target, 'movie_card_chat_id', None),
            'vo_group_chat_id': getattr(target, 'vo_group_chat_id', None),
            'is_archived': bool(getattr(target, 'is_archived', False)),
            'assignment_roles': tgt_roles,
            'vo_submission_roles': tgt_vo_roles,
            'translation_task_people': tgt_people,
            'vo_submitters': _vo_name_set(tgt_vo_subs),
            'counts': {
                'assignments': len(tgt_assignments),
                'vo_submissions': len(tgt_vo_subs),
                'translation_tasks': len(tgt_tasks),
                'translation_submissions': len(tgt_ts),
                'group_requests': len(tgt_group),
                'events': len(tgt_events),
            },
        },
        'diff': {
            'assignment_overlap': sorted(set(src_roles) & set(tgt_roles)),
            'assignment_source_only': sorted(set(src_roles) - set(tgt_roles)),
            'assignment_target_only': sorted(set(tgt_roles) - set(src_roles)),
            'vo_overlap': sorted(set(src_vo_roles) & set(tgt_vo_roles)),
            'vo_source_only': sorted(set(src_vo_roles) - set(tgt_vo_roles)),
            'vo_target_only': sorted(set(tgt_vo_roles) - set(src_vo_roles)),
            'translator_people_overlap': sorted(set(src_people) & set(tgt_people)),
            'translator_people_source_only': sorted(set(src_people) - set(tgt_people)),
            'translator_people_target_only': sorted(set(tgt_people) - set(src_people)),
        },
        'preview': preview,
    }
    return comparison
