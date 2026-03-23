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
    assert "Operations Dashboard v16" in body
