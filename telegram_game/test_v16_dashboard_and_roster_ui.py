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
        self.effective_user = SimpleNamespace(id=77, first_name="Yee")
        self.effective_message = FakeMessage()


class CommandUpdate:
    def __init__(self):
        self.effective_user = SimpleNamespace(id=77, first_name="Yee")
        self.effective_message = FakeMessage()


def test_rosterui_command_shows_paged_browser(monkeypatch):
    state = new_game(77, "Roster UI Studio")
    monkeypatch.setattr(bot, "_load_or_create", lambda user_id: state)
    monkeypatch.setattr(bot, "_save", lambda state: None)

    update = CommandUpdate()
    context = SimpleNamespace(args=["1"])
    asyncio.run(bot.cmd_rosterui(update, context))

    assert update.effective_message.calls
    assert "Roster browser" in update.effective_message.calls[-1]["text"]
    assert "Page 1/" in update.effective_message.calls[-1]["text"]


def test_rosterpage_callback_opens_next_page(monkeypatch):
    state = new_game(77, "Roster UI Studio")
    monkeypatch.setattr(bot, "_load_or_create", lambda user_id: state)
    monkeypatch.setattr(bot, "_save", lambda state: None)

    update = CallbackUpdate("g|rosterpage|2")
    context = SimpleNamespace(args=None)
    asyncio.run(bot.on_callback(update, context))

    assert update.callback_query.answered is True
    assert update.effective_message.calls
    assert "Roster browser" in update.effective_message.calls[-1]["text"]


def test_dashboard_v16_contains_quick_search_and_action_banner(monkeypatch):
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
    assert "Quick search" in body
    assert "action-banner" in body
    assert "Operations Dashboard v17" in body


def test_missionsui_command_shows_paged_browser(monkeypatch):
    state = new_game(77, "Mission UI Studio")
    monkeypatch.setattr(bot, "_load_or_create", lambda user_id: state)
    monkeypatch.setattr(bot, "_save", lambda state: None)
    monkeypatch.setattr(bot, "list_db_missions", lambda *args, **kwargs: {
        "items": [
            {"code": "MS-001", "title": "Mission One", "lang": "bn", "priority": "urgent", "status": "NEW", "translator": "-"},
            {"code": "MS-002", "title": "Mission Two", "lang": "ms", "priority": "flexible", "status": "READY", "translator": "Ryan"},
        ],
        "page": 2,
        "total_pages": 4,
        "total": 20,
    })

    update = CommandUpdate()
    context = SimpleNamespace(args=["2"])
    asyncio.run(bot.cmd_missionsui(update, context))

    assert update.effective_message.calls
    body = update.effective_message.calls[-1]["text"]
    assert "Mission browser" in body
    assert "Page 2/4" in body
    assert "MS-001" in body


def test_assignnav_callback_opens_paged_assign_panel(monkeypatch):
    state = new_game(77, "Assign UI Studio")
    monkeypatch.setattr(bot, "_load_or_create", lambda user_id: state)
    monkeypatch.setattr(bot, "_save", lambda state: None)
    monkeypatch.setattr(bot, "_all_translator_candidates", lambda _state: [SimpleNamespace(name=f"Translator {i}") for i in range(1, 10)])

    update = CallbackUpdate("g|assignnav|2|1")
    context = SimpleNamespace(args=None)
    asyncio.run(bot.on_callback(update, context))

    assert update.callback_query.answered is True
    assert update.effective_message.calls
    body = update.effective_message.calls[-1]["text"]
    assert "Assign panel" in body
    assert "Translator page 2/3" in body


def test_dashboard_v17_contains_workflow_panel_and_api(monkeypatch):
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
    assert "Telegram workflow panel" in body
    assert "Copy full flow" in body
    api_resp = client.get("/api/mission/BN-0001/workflow")
    payload = api_resp.get_json()
    assert api_resp.status_code == 200
    assert payload["ok"] is True
    assert "/pick BN-0001" in payload["workflow"]["workflow_text"]
