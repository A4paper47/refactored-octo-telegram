from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from flask import Flask
from sqlalchemy import func, or_

from db import init_db, db
from models import (
    Assignment,
    Movie,
    MovieEvent,
    TranslationTask,
    Translator,
    VORoleSubmission,
    VOTeam,
)
from telegram_game.game_engine import GameState, Mission, RoleSlot, Staff


LEVEL_SKILL = {
    "expert_old": 84,
    "trained_new": 66,
    "new_limited": 50,
}

SPEED_SCORE = {
    "slow": 46,
    "normal": 62,
    "fast": 76,
}

PRIORITY_DEADLINES = {
    "superurgent": 1,
    "urgent": 2,
    "nonurgent": 3,
    "flexible": 4,
}

ACTIVE_MOVIE_EXPR = or_(Movie.is_archived.is_(False), Movie.is_archived.is_(None))


@contextmanager
def game_db_context(database_url: Optional[str] = None):
    db_url = (database_url or os.getenv("DATABASE_URL") or "").strip()
    if not db_url:
        raise RuntimeError("Missing DATABASE_URL for DB-backed game mode.")

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url

    app = Flask("telegram_game_db")
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
    init_db(app)

    try:
        with app.app_context():
            yield app
    finally:
        try:
            db.session.remove()
        except Exception:
            pass
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _safe_name(value: Optional[str], fallback: str) -> str:
    text = (value or "").strip()
    return text or fallback


def _translator_staff(row: Translator, completed_count: int) -> Staff:
    langs = [p for p in (row.languages or "").split(",") if p.strip()]
    skill = min(92, 56 + min(6, len(langs)) * 3 + min(20, completed_count * 2))
    speed = min(88, 54 + min(16, completed_count * 2))
    level = 1 + min(5, completed_count // 2)
    return Staff(
        name=_safe_name(row.name, f"Translator-{row.id}"),
        role_type="translator",
        skill=skill,
        speed=speed,
        energy=100,
        level=level,
    )


def _vo_staff(row: VOTeam, submission_count: int) -> Staff:
    role_type = "male" if (row.gender or "").strip().lower().startswith("m") else "female"
    base_skill = LEVEL_SKILL.get((row.level or "trained_new").strip().lower(), 60)
    speed = SPEED_SCORE.get((row.speed or "normal").strip().lower(), 60)
    if row.urgent_ok:
        speed += 4
    skill = min(95, base_skill + min(10, submission_count))
    speed = min(92, speed + min(8, submission_count // 2))
    level = {
        "expert_old": 4,
        "trained_new": 2,
        "new_limited": 1,
    }.get((row.level or "trained_new").strip().lower(), 1) + min(3, submission_count // 5)
    return Staff(
        name=_safe_name(row.name, f"VO-{row.id}"),
        role_type=role_type,
        skill=skill,
        speed=speed,
        energy=100,
        level=level,
    )


def load_db_roster(database_url: Optional[str] = None) -> List[Staff]:
    with game_db_context(database_url):
        translator_done: Dict[str, int] = {
            (name or "").strip().lower(): int(count or 0)
            for name, count in (
                db.session.query(TranslationTask.translator_name, func.count(TranslationTask.id))
                .filter(TranslationTask.status == "COMPLETED")
                .group_by(TranslationTask.translator_name)
                .all()
            )
        }
        vo_done: Dict[str, int] = {
            (name or "").strip().lower(): int(count or 0)
            for name, count in (
                db.session.query(VORoleSubmission.vo, func.count(VORoleSubmission.id))
                .group_by(VORoleSubmission.vo)
                .all()
            )
        }

        roster: List[Staff] = []
        for row in Translator.query.filter_by(active=True).order_by(Translator.name.asc()).all():
            roster.append(_translator_staff(row, translator_done.get((row.name or "").strip().lower(), 0)))
        for row in VOTeam.query.filter_by(active=True).order_by(VOTeam.name.asc()).all():
            roster.append(_vo_staff(row, vo_done.get((row.name or "").strip().lower(), 0)))
        return roster


def _merge_roster(old: Iterable[Staff], fresh: Iterable[Staff]) -> List[Staff]:
    old_map = {member.name.lower(): member for member in old}
    merged: List[Staff] = []
    for member in fresh:
        previous = old_map.get(member.name.lower())
        if previous and previous.role_type == member.role_type:
            member.energy = previous.energy
            member.level = max(previous.level, member.level)
        merged.append(member)
    return merged


def sync_state_with_db(state: GameState, database_url: Optional[str] = None) -> Dict[str, int]:
    fresh = load_db_roster(database_url)
    if not fresh:
        raise RuntimeError("DB roster kosong. Pastikan translator / vo_team ada data.")
    state.roster = _merge_roster(state.roster, fresh)
    translator_count = len([s for s in state.roster if s.role_type == "translator"])
    male_count = len([s for s in state.roster if s.role_type == "male"])
    female_count = len([s for s in state.roster if s.role_type == "female"])
    state.log.append(
        f"Sync DB roster siap: {translator_count} translator, {male_count} VO male, {female_count} VO female."
    )
    return {
        "translator": translator_count,
        "male": male_count,
        "female": female_count,
        "total": len(state.roster),
    }


def _priority_from_text(value: Optional[str]) -> str:
    raw = (value or "").strip().lower()
    if raw in PRIORITY_DEADLINES:
        return raw
    mapping = {
        "su": "superurgent",
        "urgent_only": "urgent",
        "normal": "nonurgent",
        "low": "flexible",
    }
    return mapping.get(raw, "nonurgent")


def _deadline_day_from_datetimes(now: datetime, state_day: int, deadlines: List[datetime], priority: str) -> int:
    real_deadlines = [d for d in deadlines if d is not None]
    if not real_deadlines:
        return state_day + PRIORITY_DEADLINES[priority]
    soonest = min(real_deadlines)
    delta_days = (soonest - now).total_seconds() / 86400.0
    if delta_days <= 0:
        return state_day
    return state_day + max(1, min(5, int(round(delta_days))))


def _build_roles_from_assignments(assignments: List[Assignment]) -> List[RoleSlot]:
    roles: List[RoleSlot] = []
    for idx, row in enumerate(assignments, start=1):
        role_name = (row.role or f"role{idx}").strip() or f"role{idx}"
        role_lower = role_name.lower()
        gender = "male" if role_lower.startswith("man") else "female"
        if role_lower.startswith("fem"):
            gender = "female"
        roles.append(RoleSlot(role=role_name, lines=int(row.lines or 0), gender=gender))
    return roles


def _load_assignments_for_movie(movie: Movie) -> List[Assignment]:
    code = (movie.code or "").strip()
    q = Assignment.query
    if movie.id is not None and code:
        q = q.filter(or_(Assignment.movie_id == movie.id, Assignment.project == code))
    elif movie.id is not None:
        q = q.filter(Assignment.movie_id == movie.id)
    else:
        q = q.filter(Assignment.project == code)
    return q.order_by(Assignment.role.asc(), Assignment.id.asc()).all()


def _movie_score(movie: Movie) -> Tuple[int, int, int, int, int]:
    assignments = _load_assignments_for_movie(movie)
    tasks = (
        TranslationTask.query.filter(
            or_(TranslationTask.movie_id == movie.id, TranslationTask.movie_code == movie.code)
        )
        .order_by(TranslationTask.created_at.desc(), TranslationTask.id.desc())
        .all()
    )
    active_tasks = sum(1 for task in tasks if (task.status or "").strip().upper() != "COMPLETED")
    task_status = ((tasks[0].status or "") if tasks else "").strip().upper()
    task_weight = {"SENT": 3, "READY": 2, "NEW": 1}.get(task_status, 0)
    movie_status = (movie.status or "").strip().upper()
    movie_weight = {"IN_PROGRESS": 3, "PENDING": 2, "NEW": 1}.get(movie_status, 0)
    has_cast = 1 if assignments else 0
    recency_dt = movie.updated_at or movie.created_at or _utcnow_naive()
    recency = int(recency_dt.timestamp())
    return (movie_weight, task_weight, active_tasks, has_cast, recency)


def list_db_movie_candidates(limit: int = 8) -> List[Movie]:
    candidates = (
        Movie.query.filter(ACTIVE_MOVIE_EXPR)
        .filter(Movie.status != "ARCHIVED")
        .order_by(Movie.updated_at.desc(), Movie.created_at.desc(), Movie.id.desc())
        .all()
    )
    if not candidates:
        return []
    ranked = sorted(candidates, key=_movie_score, reverse=True)
    return ranked[: max(1, limit)]


def _pick_movie_candidate() -> Optional[Movie]:
    candidates = list_db_movie_candidates(limit=1)
    return candidates[0] if candidates else None


def _get_movie_by_code(movie_code: str) -> Optional[Movie]:
    code = (movie_code or "").strip()
    if not code:
        return None
    return Movie.query.filter(Movie.code == code).first()


def _build_candidate_info(movie: Movie, state: GameState) -> Dict[str, object]:
    assignments = _load_assignments_for_movie(movie)
    tasks = (
        TranslationTask.query.filter(
            or_(TranslationTask.movie_id == movie.id, TranslationTask.movie_code == movie.code)
        )
        .order_by(TranslationTask.created_at.desc(), TranslationTask.id.desc())
        .all()
    )
    task = tasks[0] if tasks else None
    priority = _priority_from_text(
        (task.priority_mode if task else None)
        or next((a.priority_mode for a in assignments if a.priority_mode), None)
    )
    deadlines = [a.deadline_at for a in assignments if a.deadline_at] + [t.deadline_at for t in tasks if t.deadline_at]
    mission = _build_mission_from_movie(state, movie, assignments, tasks, priority, deadlines)
    active_tasks = sum(1 for t in tasks if (t.status or "").strip().upper() != "COMPLETED")
    return {
        "code": mission.code,
        "title": mission.title,
        "status": movie.status,
        "priority": mission.priority,
        "translator": mission.assigned_translator or "-",
        "role_count": len(mission.roles),
        "total_lines": sum(role.lines for role in mission.roles),
        "active_tasks": active_tasks,
        "source": mission.source,
    }


def _build_mission_from_movie(
    state: GameState,
    movie: Movie,
    assignments: List[Assignment],
    tasks: List[TranslationTask],
    priority: str,
    deadlines: List[datetime],
) -> Mission:
    deadline_day = _deadline_day_from_datetimes(_utcnow_naive(), state.day, deadlines, priority)
    roles = _build_roles_from_assignments(assignments)
    if not roles:
        roles = [
            RoleSlot(role="man1", lines=80, gender="male"),
            RoleSlot(role="fem1", lines=70, gender="female"),
        ]

    total_lines = sum(r.lines for r in roles)
    reward = 80 + total_lines // 4 + len(roles) * 12
    xp = 30 + len(roles) * 10 + min(40, total_lines // 25)
    translator_difficulty = 48 + len(roles) * 7 + min(30, total_lines // 30)
    qa_threshold = 56 + len(roles) * 5 + (10 if priority == "superurgent" else 0)

    assigned_roles = {
        (a.role or "").strip(): (a.vo or "").strip()
        for a in assignments
        if (a.role or "").strip() and (a.vo or "").strip()
    }

    task = tasks[0] if tasks else None
    translator_name = None
    if task and (task.translator_name or "").strip():
        translator_name = task.translator_name.strip()
    elif (movie.translator_assigned or "").strip():
        translator_name = movie.translator_assigned.strip()

    return Mission(
        code=(movie.code or f"DB-{movie.id:06d}"),
        title=(movie.title or "Untitled Project"),
        year=int(movie.year) if str(movie.year or "").isdigit() else datetime.now(timezone.utc).year,
        lang=((movie.lang or "bn").strip() or "bn"),
        priority=priority,
        reward=reward,
        xp=xp,
        deadline_day=deadline_day,
        translator_difficulty=translator_difficulty,
        qa_threshold=qa_threshold,
        roles=roles,
        assigned_translator=translator_name,
        assigned_roles=assigned_roles,
        accepted=False,
        source="database",
    )


def build_mission_from_db(state: GameState, database_url: Optional[str] = None) -> Optional[Mission]:
    with game_db_context(database_url):
        movie = _pick_movie_candidate()
        if not movie:
            return None

        assignments = _load_assignments_for_movie(movie)
        tasks = (
            TranslationTask.query.filter(
                or_(TranslationTask.movie_id == movie.id, TranslationTask.movie_code == movie.code)
            )
            .order_by(TranslationTask.created_at.desc(), TranslationTask.id.desc())
            .all()
        )
        priority = _priority_from_text(
            (tasks[0].priority_mode if tasks else None)
            or next((a.priority_mode for a in assignments if a.priority_mode), None)
        )
        deadlines = [a.deadline_at for a in assignments if a.deadline_at] + [t.deadline_at for t in tasks if t.deadline_at]
        return _build_mission_from_movie(state, movie, assignments, tasks, priority, deadlines)


def list_db_missions(state: GameState, limit: int = 8, database_url: Optional[str] = None) -> List[Dict[str, object]]:
    with game_db_context(database_url):
        return [_build_candidate_info(movie, state) for movie in list_db_movie_candidates(limit=limit)]


def build_mission_from_movie_code(
    state: GameState,
    movie_code: str,
    database_url: Optional[str] = None,
) -> Optional[Mission]:
    with game_db_context(database_url):
        movie = _get_movie_by_code(movie_code)
        if movie is None:
            return None
        assignments = _load_assignments_for_movie(movie)
        tasks = (
            TranslationTask.query.filter(
                or_(TranslationTask.movie_id == movie.id, TranslationTask.movie_code == movie.code)
            )
            .order_by(TranslationTask.created_at.desc(), TranslationTask.id.desc())
            .all()
        )
        priority = _priority_from_text(
            (tasks[0].priority_mode if tasks else None)
            or next((a.priority_mode for a in assignments if a.priority_mode), None)
        )
        deadlines = [a.deadline_at for a in assignments if a.deadline_at] + [t.deadline_at for t in tasks if t.deadline_at]
        return _build_mission_from_movie(state, movie, assignments, tasks, priority, deadlines)


def load_db_mission_into_state(state: GameState, database_url: Optional[str] = None) -> Optional[Mission]:
    mission = build_mission_from_db(state, database_url)
    if mission is None:
        return None
    state.current_mission = mission
    state.log.append(f"DB mission loaded: {mission.code} — {mission.title}")
    return mission


def load_specific_db_mission_into_state(
    state: GameState,
    movie_code: str,
    database_url: Optional[str] = None,
) -> Optional[Mission]:
    mission = build_mission_from_movie_code(state, movie_code, database_url)
    if mission is None:
        return None
    state.current_mission = mission
    state.log.append(f"DB mission picked: {mission.code} — {mission.title}")
    return mission


def _get_movie_by_mission(mission: Mission) -> Movie:
    code = (mission.code or "").strip()
    movie = None
    if code:
        movie = Movie.query.filter(Movie.code == code).first()
    if movie is None:
        raise RuntimeError(f"Movie untuk mission {mission.code} tak jumpa dalam DB.")
    return movie


def _latest_task_for_movie(movie: Movie) -> Optional[TranslationTask]:
    return (
        TranslationTask.query.filter(
            or_(TranslationTask.movie_id == movie.id, TranslationTask.movie_code == movie.code)
        )
        .order_by(TranslationTask.created_at.desc(), TranslationTask.id.desc())
        .first()
    )


def _upsert_translation_task(movie: Movie, mission: Mission, now: datetime) -> TranslationTask:
    task = _latest_task_for_movie(movie)
    if task is None:
        task = TranslationTask(
            movie_id=movie.id,
            movie_code=movie.code,
            title=movie.title,
            year=movie.year,
            lang=movie.lang,
            created_at=now,
        )
        db.session.add(task)

    task.movie_id = movie.id
    task.movie_code = movie.code
    task.title = movie.title
    task.year = movie.year
    task.lang = movie.lang
    task.translator_name = mission.assigned_translator
    task.priority_mode = mission.priority
    if task.status != "COMPLETED":
        task.status = "SENT"
    if not task.sent_at:
        task.sent_at = now
    task.updated_at = now
    return task


def _sync_assignment_rows(movie: Movie, mission: Mission, now: datetime) -> Tuple[int, int]:
    existing = {row.role.strip().lower(): row for row in _load_assignments_for_movie(movie) if (row.role or "").strip()}
    created = 0
    updated = 0
    for role in mission.roles:
        key = role.role.strip().lower()
        row = existing.get(key)
        if row is None:
            row = Assignment(
                project=movie.code or mission.code,
                movie_id=movie.id,
                vo=mission.assigned_roles.get(role.role, "") or "UNASSIGNED",
                role=role.role,
                lines=int(role.lines or 0),
                urgent=mission.priority in {"superurgent", "urgent"},
                priority_mode=mission.priority,
                created_at=now,
            )
            db.session.add(row)
            created += 1
        else:
            updated += 1
        row.project = movie.code or mission.code
        row.movie_id = movie.id
        row.role = role.role
        row.lines = int(role.lines or 0)
        row.vo = mission.assigned_roles.get(role.role, row.vo) or row.vo or "UNASSIGNED"
        row.priority_mode = mission.priority
        row.urgent = mission.priority in {"superurgent", "urgent"}
    return created, updated


def _add_movie_event(
    movie: Movie,
    event_type: str,
    summary: str,
    detail: Optional[str] = None,
    actor_name: str = "telegram_game",
    now: Optional[datetime] = None,
) -> MovieEvent:
    evt = MovieEvent(
        movie_id=movie.id,
        movie_code=movie.code,
        movie_title=movie.title,
        event_type=event_type,
        summary=summary,
        detail=detail,
        actor_source="telegram_game",
        actor_name=actor_name,
        created_at=now or _utcnow_naive(),
    )
    db.session.add(evt)
    return evt


def persist_mission_assignments(
    state: GameState,
    database_url: Optional[str] = None,
    actor_name: str = "telegram_game",
) -> Dict[str, object]:
    mission = state.current_mission
    if mission is None:
        raise RuntimeError("Tiada mission aktif untuk disimpan ke DB.")
    if mission.source != "database":
        raise RuntimeError("Write-back DB hanya untuk mission yang datang dari database.")

    with game_db_context(database_url):
        now = _utcnow_naive()
        movie = _get_movie_by_mission(mission)
        movie.translator_assigned = mission.assigned_translator
        movie.status = movie.status or "IN_PROGRESS"
        if movie.status == "NEW":
            movie.status = "IN_PROGRESS"
        movie.updated_at = now

        task = _upsert_translation_task(movie, mission, now)
        created, updated = _sync_assignment_rows(movie, mission, now)

        detail_lines = [f"translator={mission.assigned_translator or '-'}"]
        for role in mission.roles:
            detail_lines.append(f"{role.role}={mission.assigned_roles.get(role.role, '-')}")
        _add_movie_event(
            movie,
            event_type="GAME_ASSIGN",
            summary=f"Game mission sync untuk {mission.code}",
            detail="\n".join(detail_lines),
            actor_name=actor_name,
            now=now,
        )
        db.session.commit()
        return {
            "movie_id": movie.id,
            "movie_code": movie.code,
            "translation_task_id": task.id,
            "assignment_created": created,
            "assignment_updated": updated,
        }


def _create_submission_rows(movie: Movie, mission: Mission, now: datetime) -> int:
    created = 0
    for role in mission.roles:
        vo_name = mission.assigned_roles.get(role.role)
        if not vo_name:
            continue
        row = VORoleSubmission(
            movie=movie.code or mission.code,
            vo=vo_name,
            role=role.role,
            lines=int(role.lines or 0),
            submitted_at=now,
        )
        db.session.add(row)
        created += 1
    return created


def persist_submission_result(
    mission: Mission,
    result: Dict[str, object],
    database_url: Optional[str] = None,
    actor_name: str = "telegram_game",
) -> Dict[str, object]:
    if mission.source != "database":
        raise RuntimeError("Submission write-back DB hanya untuk mission database.")

    with game_db_context(database_url):
        now = _utcnow_naive()
        movie = _get_movie_by_mission(mission)
        task = _upsert_translation_task(movie, mission, now)
        submissions_created = 0
        passed = bool(result.get("passed"))

        if passed:
            movie.translator_assigned = mission.assigned_translator
            movie.status = "COMPLETED"
            movie.submitted_at = now
            movie.completed_at = now
            movie.updated_at = now
            task.status = "COMPLETED"
            task.completed_at = now
            task.updated_at = now
            submissions_created = _create_submission_rows(movie, mission, now)
            summary = f"Mission {mission.code} lulus QA dengan score {result.get('qa_score')}"
            detail = f"reward={result.get('reward')} xp={result.get('xp')} threshold={result.get('threshold')}"
            event_type = "GAME_SUBMIT_OK"
        else:
            movie.status = movie.status or "IN_PROGRESS"
            movie.updated_at = now
            task.status = "SENT"
            task.updated_at = now
            summary = f"Mission {mission.code} gagal QA dengan score {result.get('qa_score')}"
            detail = f"threshold={result.get('threshold')}"
            event_type = "GAME_SUBMIT_FAIL"

        _add_movie_event(
            movie,
            event_type=event_type,
            summary=summary,
            detail=detail,
            actor_name=actor_name,
            now=now,
        )
        db.session.commit()
        return {
            "movie_id": movie.id,
            "movie_code": movie.code,
            "passed": passed,
            "vo_submissions_created": submissions_created,
            "translation_task_id": task.id,
        }
