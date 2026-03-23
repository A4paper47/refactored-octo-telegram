from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

from telegram_game import telegram_studio_game_bot as bot
from telegram_game.game_engine import new_game


class FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.calls.append({"text": text, "reply_markup": reply_markup, "kwargs": kwargs})


class FakeCallbackQuery:
    def __init__(self, data: str):
        self.data = data
        self.answered = False

    async def answer(self):
        self.answered = True


class CommandUpdate:
    def __init__(self):
        self.effective_user = SimpleNamespace(id=120, first_name="Yee")
        self.effective_message = FakeMessage()


class CallbackUpdate:
    def __init__(self, data: str):
        self.callback_query = FakeCallbackQuery(data)
        self.effective_user = SimpleNamespace(id=120, first_name="Yee")
        self.effective_message = FakeMessage()


def test_presetapply_callback_applies_assignments(monkeypatch):
    state = new_game(120, "Preset Flow Studio")
    bot._ensure_bot_mission(state)
    monkeypatch.setattr(bot, "_load_or_create", lambda user_id: state)
    monkeypatch.setattr(bot, "_save", lambda state: None)

    update = CallbackUpdate("g|presetapply|workload")
    context = SimpleNamespace(args=None)
    asyncio.run(bot.on_callback(update, context))

    assert update.callback_query.answered is True
    body = update.effective_message.calls[-1]["text"]
    assert "Assign preset applied" in body
    assert "workload" in body
    assert state.current_mission.assigned_translator
    assert state.current_mission.assigned_roles


def test_accept_flow_surfaces_preset_buttons(monkeypatch):
    state = new_game(120, "Mission Flow Studio")
    bot._ensure_bot_mission(state)
    monkeypatch.setattr(bot, "_load_or_create", lambda user_id: state)
    monkeypatch.setattr(bot, "_save", lambda state: None)

    update = CommandUpdate()
    context = SimpleNamespace(args=[])
    asyncio.run(bot.cmd_accept(update, context))

    payload = update.effective_message.calls[-1]
    body = payload["text"]
    assert "Misi diterima" in body
    keyboard = payload["reply_markup"].inline_keyboard
    callback_data = [btn.callback_data for row in keyboard for btn in row]
    assert "g|presetapply|recommended" in callback_data
    assert "g|presetapply|trait" in callback_data


def test_dashboard_v20_contains_simulator_action_decks(monkeypatch):
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
    assert "Simulator action deck" in body
    assert "Preset action deck" in body
    assert "sim-operator-summary" in body


def test_mission_simulator_payload_includes_action_and_preset_decks(monkeypatch):
    monkeypatch.setenv("BOT_AUTO_START", "0")
    monkeypatch.setenv("BOT_TOKEN", "")
    monkeypatch.setenv("GAME_USE_DB", "0")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://demo.onrender.com")
    import render_game_web

    mod = importlib.reload(render_game_web)
    payload = mod._mission_simulator_payload({
        "code": "MS-920",
        "priority": "urgent",
        "lang": "bn",
        "client_tier": "premium",
        "modifiers": ["rush_rewrite", "glossary_lock"],
        "roles": [{"role": "man1", "gender": "male", "lines": 120, "assigned": "-"}],
        "active_tasks": 2,
    })

    assert payload["action_deck"][0]["command"].startswith("/assignpreset")
    assert any(item["command"] == "/assignpreset lang" for item in payload["preset_deck"])
    assert "Urgency" in payload["operator_summary"]
