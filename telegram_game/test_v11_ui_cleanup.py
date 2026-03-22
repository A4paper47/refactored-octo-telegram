from __future__ import annotations

import importlib
from types import SimpleNamespace

from telegram_game import telegram_studio_game_bot as bot


def test_help_text_mentions_menu_and_board():
    text = bot._help_text()
    assert "/menu" in text
    assert "/board" in text
    assert "/assignui" in text


def test_home_text_contains_mission_snapshot():
    state = bot.new_game(user_id=55, studio_name="Clean UI Studio")
    mission = bot.ensure_mission(state)
    text = bot._home_text(state)
    assert mission.code in text
    assert mission.title in text
    assert "Studio Dub Tycoon" in text


def test_dashboard_manifest_route(monkeypatch):
    monkeypatch.setenv("BOT_AUTO_START", "0")
    monkeypatch.setenv("BOT_TOKEN", "")
    monkeypatch.setenv("GAME_USE_DB", "0")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://demo.onrender.com")
    import render_game_web

    mod = importlib.reload(render_game_web)
    client = mod.app.test_client()
    resp = client.get("/api/manifest")

    assert resp.status_code == 200
    data = resp.get_json()
    assert "kept" in data
    assert "removed" in data
    assert "render_game_web.py" in data["kept"]["core"]
    assert "app.py" in data["removed"]["legacy_flask_tracker"]
