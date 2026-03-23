from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

from telegram_game import telegram_studio_game_bot as bot
from telegram_game.game_engine import new_game


class FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, reply_markup=None):
        self.calls.append({"text": text, "reply_markup": reply_markup})


class CommandUpdate:
    def __init__(self):
        self.effective_user = SimpleNamespace(id=91, first_name="Yee")
        self.effective_message = FakeMessage()


class FakeCallbackQuery:
    def __init__(self, data: str):
        self.data = data
        self.answered = False

    async def answer(self):
        self.answered = True


class CallbackUpdate:
    def __init__(self, data: str):
        self.callback_query = FakeCallbackQuery(data)
        self.effective_user = SimpleNamespace(id=91, first_name="Yee")
        self.effective_message = FakeMessage()


def test_assignpreset_command_applies_smart_cast(monkeypatch):
    state = new_game(91, "Preset Studio")
    bot._ensure_bot_mission(state)
    monkeypatch.setattr(bot, "_load_or_create", lambda user_id: state)
    monkeypatch.setattr(bot, "_save", lambda state: None)

    update = CommandUpdate()
    context = SimpleNamespace(args=["trait"])
    asyncio.run(bot.cmd_assignpreset(update, context))

    body = update.effective_message.calls[-1]["text"]
    assert "Assign preset applied" in body
    assert "trait" in body
    assert state.current_mission.assigned_translator
    assert state.current_mission.assigned_roles


def test_assignnav_callback_supports_preset(monkeypatch):
    state = new_game(91, "Preset Filter Studio")
    monkeypatch.setattr(bot, "_load_or_create", lambda user_id: state)
    monkeypatch.setattr(bot, "_save", lambda state: None)

    update = CallbackUpdate("g|assignnav|1|1|fresh|female|workload")
    context = SimpleNamespace(args=None)
    asyncio.run(bot.on_callback(update, context))

    body = update.effective_message.calls[-1]["text"]
    assert "filter fresh" in body
    assert "gender filter female" in body
    assert "Preset: workload" in body


def test_pickrole_callback_supports_preset(monkeypatch):
    state = new_game(91, "Preset Role Studio")
    bot._ensure_bot_mission(state)
    monkeypatch.setattr(bot, "_load_or_create", lambda user_id: state)
    monkeypatch.setattr(bot, "_save", lambda state: None)
    role_name = state.current_mission.roles[0].role

    update = CallbackUpdate(f"g|pickrole|{bot._name_token(role_name)}|1|fresh|trait")
    context = SimpleNamespace(args=None)
    asyncio.run(bot.on_callback(update, context))

    body = update.effective_message.calls[-1]["text"]
    assert f"Pilih VO untuk {role_name}" in body
    assert "Energy filter: fresh" in body
    assert "Preset: trait" in body


def test_dashboard_v19_contains_mission_simulator(monkeypatch):
    monkeypatch.setenv("BOT_AUTO_START", "0")
    monkeypatch.setenv("BOT_TOKEN", "")
    monkeypatch.setenv("GAME_USE_DB", "0")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://demo.onrender.com")
    import render_game_web

    mod = importlib.reload(render_game_web)
    client = mod.app.test_client()
    resp = client.get("/dashboard")
    body = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "Mission simulator" in body
    assert "Copy preset flow" in body
    assert "sim-warning-list" in body


def test_mission_simulator_payload_prefers_workload_for_urgent_modifier(monkeypatch):
    monkeypatch.setenv("BOT_AUTO_START", "0")
    monkeypatch.setenv("BOT_TOKEN", "")
    monkeypatch.setenv("GAME_USE_DB", "0")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://demo.onrender.com")
    import render_game_web

    mod = importlib.reload(render_game_web)
    payload = mod._mission_simulator_payload({
        "code": "MS-999",
        "priority": "superurgent",
        "lang": "bn",
        "client_tier": "enterprise",
        "modifiers": ["rush_rewrite", "premium_notes"],
        "roles": [{"role": "man1", "gender": "male", "lines": 120, "assigned": "-"}],
        "active_tasks": 2,
    })

    assert payload["preset"] == "workload"
    assert "/assignpreset workload" in payload["workflow_text"]
    assert payload["urgency_score"] >= 60
