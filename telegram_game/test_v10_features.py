from __future__ import annotations

import importlib

from telegram_game import game_engine
from telegram_game import telegram_studio_game_bot as bot


def _prepared_state():
    state = game_engine.new_game(user_id=99, studio_name="QA Studio")
    mission = game_engine.ensure_mission(state)
    game_engine.accept_mission(state)
    tr = next(member for member in state.roster if member.role_type == "translator")
    game_engine.assign_translator(state, tr.name)
    for role in mission.roles:
        vo = next(member for member in state.roster if member.role_type == role.gender)
        game_engine.assign_role(state, role.role, vo.name)
    return state, mission


def test_submission_risk_report_warns_on_burnout():
    state, mission = _prepared_state()
    tr = game_engine.find_staff(state, mission.assigned_translator)
    assert tr is not None
    tr.energy = 22
    tr.burnout = 68

    report = game_engine.submission_risk_report(state)

    assert report["can_submit"] is True
    assert report["has_warning"] is True
    assert report["risky_members"]
    assert any(item["name"] == tr.name for item in report["risky_members"])


def test_submission_risk_report_blocks_missing_assignment():
    state = game_engine.new_game(user_id=101, studio_name="Fresh Studio")
    mission = game_engine.ensure_mission(state)
    game_engine.accept_mission(state)

    report = game_engine.submission_risk_report(state)

    assert report["can_submit"] is False
    assert report["blockers"]
    assert "Translator belum assign." in report["blockers"]
    assert any("Role belum assign" in item for item in report["blockers"])


def test_name_token_roundtrip_handles_spaces():
    original = "Alya V 2"
    token = bot._name_token(original)
    assert bot._name_from_token(token) == original


def test_render_dashboard_route(monkeypatch):
    monkeypatch.setenv("BOT_AUTO_START", "0")
    monkeypatch.setenv("BOT_TOKEN", "")
    monkeypatch.setenv("GAME_USE_DB", "0")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://demo.onrender.com")
    import render_game_web

    mod = importlib.reload(render_game_web)
    client = mod.app.test_client()
    resp = client.get("/dashboard")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Studio Dub Tycoon Control Center" in body
    assert "/api/status" in body
