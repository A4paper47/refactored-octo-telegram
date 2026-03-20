from __future__ import annotations

import os
from pathlib import Path
import tempfile

from flask import Flask

from db import init_db, db
from models import Assignment, Movie, MovieEvent, TranslationTask, Translator, VORoleSubmission, VOTeam
from telegram_game.db_integration import (
    build_mission_from_db,
    load_db_roster,
    persist_mission_assignments,
    persist_submission_result,
    sync_state_with_db,
)
from telegram_game.game_engine import accept_mission, assign_role, assign_translator, new_game, resolve_submission


def _seed_sqlite(db_url: str) -> None:
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url
    app = Flask("test_seed")
    init_db(app)
    with app.app_context():
        db.drop_all()
        db.create_all()

        db.session.add_all([
            Translator(name="Ryan", active=True, languages="bn,ms,en"),
            Translator(name="Sumi", active=True, languages="bn,ms"),
            VOTeam(name="Ray", gender="male", level="expert_old", speed="normal", urgent_ok=True, active=True),
            VOTeam(name="Sara", gender="female", level="trained_new", speed="slow", urgent_ok=True, active=True),
        ])
        movie = Movie(
            code="BN-260320-01",
            title="Shadow Harbor",
            year="2025",
            lang="bn",
            status="IN_PROGRESS",
            translator_assigned="Ryan",
        )
        db.session.add(movie)
        db.session.flush()
        db.session.add_all([
            Assignment(project="BN-260320-01", movie_id=movie.id, vo="Ray", role="man1", lines=120, priority_mode="urgent"),
            Assignment(project="BN-260320-01", movie_id=movie.id, vo="Sara", role="fem1", lines=90, priority_mode="urgent"),
            TranslationTask(movie_id=movie.id, movie_code="BN-260320-01", title="Shadow Harbor", translator_name="Ryan", status="SENT", priority_mode="urgent"),
        ])
        db.session.commit()
    if previous is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous


def _readback(db_url: str):
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url
    app = Flask("test_readback")
    init_db(app)
    try:
        with app.app_context():
            movie = Movie.query.filter(Movie.code == "BN-260320-01").first()
            assignments = Assignment.query.filter(Assignment.project == "BN-260320-01").order_by(Assignment.role.asc()).all()
            tasks = TranslationTask.query.filter(TranslationTask.movie_code == "BN-260320-01").order_by(TranslationTask.id.asc()).all()
            events = MovieEvent.query.filter(MovieEvent.movie_code == "BN-260320-01").order_by(MovieEvent.id.asc()).all()
            submissions = VORoleSubmission.query.filter(VORoleSubmission.movie == "BN-260320-01").order_by(VORoleSubmission.role.asc()).all()
            return movie, assignments, tasks, events, submissions
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def test_load_db_roster_and_sync_state():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "game.sqlite"
        db_url = f"sqlite:///{db_path}"
        _seed_sqlite(db_url)

        roster = load_db_roster(db_url)
        names = {member.name for member in roster}
        assert {"Ryan", "Sumi", "Ray", "Sara"}.issubset(names)

        state = new_game(321, "Hybrid Studio")
        stats = sync_state_with_db(state, db_url)
        assert stats["translator"] >= 2
        assert stats["male"] >= 1
        assert stats["female"] >= 1
        assert any(member.name == "Ryan" and member.role_type == "translator" for member in state.roster)


def test_build_mission_from_db_uses_real_movie_and_assignments():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "game.sqlite"
        db_url = f"sqlite:///{db_path}"
        _seed_sqlite(db_url)

        state = new_game(654, "Hybrid Studio")
        mission = build_mission_from_db(state, db_url)
        assert mission is not None
        assert mission.source == "database"
        assert mission.code == "BN-260320-01"
        assert mission.title == "Shadow Harbor"
        assert mission.assigned_translator == "Ryan"
        assert mission.assigned_roles["man1"] == "Ray"
        assert mission.assigned_roles["fem1"] == "Sara"
        assert len(mission.roles) == 2


def test_persist_mission_assignments_writes_back_to_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "game.sqlite"
        db_url = f"sqlite:///{db_path}"
        _seed_sqlite(db_url)

        state = new_game(999, "Hybrid Studio")
        sync_state_with_db(state, db_url)
        state.current_mission = build_mission_from_db(state, db_url)
        accept_mission(state)
        assign_translator(state, "Sumi")
        assign_role(state, "man1", "Ray")
        assign_role(state, "fem1", "Sara")

        info = persist_mission_assignments(state, db_url, actor_name="pytest")
        assert info["movie_code"] == "BN-260320-01"

        movie, assignments, tasks, events, submissions = _readback(db_url)
        assert movie is not None
        assert movie.translator_assigned == "Sumi"
        assert any(t.translator_name == "Sumi" and t.status == "SENT" for t in tasks)
        assert any(a.role == "man1" and a.vo == "Ray" for a in assignments)
        assert any(a.role == "fem1" and a.vo == "Sara" for a in assignments)
        assert any(e.event_type == "GAME_ASSIGN" for e in events)
        assert submissions == []


def test_persist_submission_result_marks_completed_and_creates_vo_submissions():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "game.sqlite"
        db_url = f"sqlite:///{db_path}"
        _seed_sqlite(db_url)

        state = new_game(1001, "Hybrid Studio")
        sync_state_with_db(state, db_url)
        state.current_mission = build_mission_from_db(state, db_url)
        accept_mission(state)
        assign_translator(state, "Ryan")
        assign_role(state, "man1", "Ray")
        assign_role(state, "fem1", "Sara")
        persist_mission_assignments(state, db_url, actor_name="pytest")

        mission_before = state.current_mission
        result = resolve_submission(state)
        assert mission_before is not None
        info = persist_submission_result(mission_before, result, db_url, actor_name="pytest")
        assert info["movie_code"] == "BN-260320-01"
        assert info["passed"] is True
        assert info["vo_submissions_created"] == 2

        movie, assignments, tasks, events, submissions = _readback(db_url)
        assert movie is not None
        assert movie.status == "COMPLETED"
        assert movie.completed_at is not None
        assert any(t.status == "COMPLETED" and t.completed_at is not None for t in tasks)
        assert len(submissions) == 2
        assert {s.role for s in submissions} == {"man1", "fem1"}
        assert any(e.event_type == "GAME_SUBMIT_OK" for e in events)
