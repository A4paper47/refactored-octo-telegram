from __future__ import annotations

import asyncio
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
        self.effective_user = SimpleNamespace(id=55, first_name="Yee")
        self.effective_message = FakeMessage()


def test_staffcard_callback_shows_staff_panel(monkeypatch):
    state = new_game(55, "Gear Test")
    monkeypatch.setattr(bot, "_load_or_create", lambda user_id: state)
    monkeypatch.setattr(bot, "_save", lambda state: None)

    update = CallbackUpdate(f"g|staffcard|{bot._name_token('Alya')}")
    context = SimpleNamespace(args=None)

    asyncio.run(bot.on_callback(update, context))

    assert update.callback_query.answered is True
    assert update.effective_message.calls
    assert "Staff card" in update.effective_message.calls[-1]["text"]


def test_buygearui_callback_buys_item(monkeypatch):
    state = new_game(55, "Gear Test")
    state.coins = 999
    monkeypatch.setattr(bot, "_load_or_create", lambda user_id: state)
    monkeypatch.setattr(bot, "_save", lambda state: None)

    update = CallbackUpdate("g|buygearui|focus_notes")
    context = SimpleNamespace(args=None)

    asyncio.run(bot.on_callback(update, context))

    assert update.callback_query.answered is True
    assert state.inventory.get("focus_notes", 0) >= 1
    assert "Gear dibeli" in update.effective_message.calls[-1]["text"]


def test_equippick_and_equipdo_callbacks(monkeypatch):
    state = new_game(55, "Gear Test")
    state.inventory["focus_notes"] = 1
    monkeypatch.setattr(bot, "_load_or_create", lambda user_id: state)
    monkeypatch.setattr(bot, "_save", lambda state: None)

    pick_update = CallbackUpdate(f"g|equippick|{bot._name_token('Alya')}")
    context = SimpleNamespace(args=None)
    asyncio.run(bot.on_callback(pick_update, context))

    assert "Equip picker" in pick_update.effective_message.calls[-1]["text"]

    equip_update = CallbackUpdate(f"g|equipdo|{bot._name_token('Alya')}|focus_notes")
    asyncio.run(bot.on_callback(equip_update, context))

    member = next(item for item in state.roster if item.name == "Alya")
    assert member.equipped == "focus_notes"
    assert "equip" in equip_update.effective_message.calls[-1]["text"].lower()
