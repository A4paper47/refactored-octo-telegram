from __future__ import annotations

import asyncio
from types import SimpleNamespace

from telegram_game import telegram_studio_game_bot as bot


class FakeCallbackQuery:
    def __init__(self, data: str):
        self.data = data
        self.answered = False

    async def answer(self):
        self.answered = True


class GuardedUpdate:
    def __init__(self, data: str):
        object.__setattr__(self, 'callback_query', FakeCallbackQuery(data))

    def __setattr__(self, name, value):
        if name == 'effective_message':
            raise AssertionError('effective_message should not be assigned')
        object.__setattr__(self, name, value)


def test_callback_does_not_mutate_update_and_dispatches(monkeypatch):
    called = {}

    async def fake_cmd(update, context):
        called['ok'] = True

    monkeypatch.setattr(bot, 'cmd_mission', fake_cmd)
    update = GuardedUpdate('g|mission')
    context = SimpleNamespace(args=[])

    asyncio.run(bot.on_callback(update, context))

    assert update.callback_query.answered is True
    assert called['ok'] is True


def test_callback_missions_handles_missing_context_args(monkeypatch):
    captured = {}

    async def fake_cmd_missions(update, context):
        captured["args"] = getattr(context, "args", None)

    monkeypatch.setattr(bot, 'cmd_missions', fake_cmd_missions)
    update = GuardedUpdate('g|missions')
    context = SimpleNamespace(args=None)

    asyncio.run(bot.on_callback(update, context))

    assert update.callback_query.answered is True
    assert captured["args"] is None


def test_parse_mission_filters_accepts_none():
    status, translator, priority, lang, page = bot._parse_mission_filters(None)

    assert status is None
    assert translator is None
    assert priority is None
    assert lang is None
    assert page == 1
