from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

from telegram_game import telegram_studio_game_bot as bot
from telegram_game.game_engine import new_game


class FakeCallbackQuery:
    def __init__(self, data: str):
        self.data = data
        self.answered = False

    async def answer(self):
        self.answered = True


class FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, reply_markup=None):
        self.calls.append({"text": text, "reply_markup": reply_markup})


class CallbackUpdate:
    def __init__(self, data: str):
        self.callback_query = FakeCallbackQuery(data)
        self.effective_user = SimpleNamespace(id=88, first_name="Yee")
        self.effective_message = FakeMessage()


class CommandUpdate:
    def __init__(self):
        self.effective_user = SimpleNamespace(id=88, first_name="Yee")
        self.effective_message = FakeMessage()


def test_assignnav_callback_supports_filters(monkeypatch):
    state = new_game(88, "Filtered Assign Studio")
    monkeypatch.setattr(bot, "_load_or_create", lambda user_id: state)
    monkeypatch.setattr(bot, "_save", lambda state: None)

    update = CallbackUpdate("g|assignnav|1|1|fresh|female")
    context = SimpleNamespace(args=None)
    asyncio.run(bot.on_callback(update, context))

    assert update.callback_query.answered is True
    body = update.effective_message.calls[-1]["text"]
    assert "filter fresh" in body
    assert "gender filter female" in body


def test_pickrole_callback_supports_energy_filter(monkeypatch):
    state = new_game(88, "Energy Filter Studio")
    bot._ensure_bot_mission(state)
    monkeypatch.setattr(bot, "_load_or_create", lambda user_id: state)
    monkeypatch.setattr(bot, "_save", lambda state: None)
    role_name = state.current_mission.roles[0].role

    update = CallbackUpdate(f"g|pickrole|{bot._name_token(role_name)}|1|fresh")
    context = SimpleNamespace(args=None)
    asyncio.run(bot.on_callback(update, context))

    assert update.callback_query.answered is True
    body = update.effective_message.calls[-1]["text"]
    assert f"Pilih VO untuk {role_name}" in body
    assert "Energy filter: fresh" in body


def test_dashboard_v18_contains_modal_and_templates(monkeypatch):
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
    assert "Open detail modal" in body
    assert "Quick assign templates" in body
    assert "mission-modal-backdrop" in body


def test_workflow_payload_includes_role_templates(monkeypatch):
    monkeypatch.setenv("BOT_AUTO_START", "0")
    monkeypatch.setenv("BOT_TOKEN", "")
    monkeypatch.setenv("GAME_USE_DB", "0")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://demo.onrender.com")
    import render_game_web

    mod = importlib.reload(render_game_web)
    detail = {
        "code": "MS-900",
        "translator": "Ryan",
        "roles": [
            {"role": "man1", "gender": "male", "lines": 90, "assigned": "Ray"},
            {"role": "fem1", "gender": "female", "lines": 70, "assigned": "-"},
        ],
    }
    payload = mod._mission_workflow_payload(detail)

    assert payload["translator_template"] == "/assigntr Ryan"
    assert payload["role_templates"][0]["template"] == "/assign man1 <male_staff_name>"
    assert payload["role_templates"][0]["assigned_template"] == "/assign man1 Ray"
