from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

from telegram_game import telegram_studio_game_bot as bot
from telegram_game.game_engine import Mission, RoleSlot, Staff, new_game


class FakeMessage:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.calls.append({"text": text, "reply_markup": reply_markup, "kwargs": kwargs})


class CommandUpdate:
    def __init__(self):
        self.effective_user = SimpleNamespace(id=321, first_name="Yee")
        self.effective_message = FakeMessage()


def test_mission_card_text_is_compact_and_richer():
    state = new_game(321, "Compact Card Studio")
    mission = Mission(
        code="BN-321",
        title="Signal Harbor",
        year=2026,
        lang="bn",
        priority="urgent",
        reward=220,
        xp=48,
        deadline_day=2,
        translator_difficulty=11,
        qa_threshold=14,
        roles=[RoleSlot(role="man1", lines=120, gender="male"), RoleSlot(role="fem1", lines=90, gender="female")],
        assigned_translator="Alya",
        assigned_roles={"man1": "Ray"},
        source="database",
        client_name="Nova Stream",
        client_tier="premium",
        reputation_reward=2,
        modifiers=["premium_notes", "rush_rewrite"],
    )

    text = bot._mission_card_text(state, mission)

    assert "BN-321" in text
    assert "Cast 1/2" in text
    assert "Preset workload" in text or "Preset trait" in text or "Preset lang" in text or "Preset recommended" in text
    assert "Reward 220c" in text
    assert "Roles: man1:Ray, fem1:-" in text


def test_accept_flow_uses_compact_mission_card(monkeypatch):
    state = new_game(321, "Compact Accept Studio")
    bot._ensure_bot_mission(state)
    monkeypatch.setattr(bot, "_load_or_create", lambda user_id: state)
    monkeypatch.setattr(bot, "_save", lambda state: None)

    update = CommandUpdate()
    context = SimpleNamespace(args=[])
    asyncio.run(bot.cmd_accept(update, context))

    body = update.effective_message.calls[-1]["text"]
    assert "Misi diterima" in body
    assert "Cast " in body
    assert "Preset " in body


def test_dashboard_v21_contains_roster_backed_quick_actions(monkeypatch):
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
    assert "Roster-backed quick actions" in body
    assert "cmd-copy-quick-translator" in body
    assert "quick-role-options" in body


def test_quick_actions_payload_uses_synced_roster(monkeypatch):
    monkeypatch.setenv("BOT_AUTO_START", "0")
    monkeypatch.setenv("BOT_TOKEN", "")
    monkeypatch.setenv("GAME_USE_DB", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://demo")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://demo.onrender.com")
    import render_game_web

    mod = importlib.reload(render_game_web)

    def fake_sync(state):
        state.roster = [
            Staff(name="Alya", role_type="translator", skill=80, speed=74, energy=100, level=3, rarity="rare", traits=["polyglot"]),
            Staff(name="Rina", role_type="translator", skill=77, speed=70, energy=92, level=2, rarity="common", traits=["perfectionist"]),
            Staff(name="Ray", role_type="male", skill=82, speed=75, energy=98, level=3, rarity="rare", traits=["natural"]),
            Staff(name="Hakim", role_type="male", skill=75, speed=72, energy=90, level=2, rarity="common", traits=["resilient"]),
            Staff(name="Sara", role_type="female", skill=83, speed=74, energy=96, level=3, rarity="rare", traits=["natural"]),
        ]
        return {"total": len(state.roster)}

    def fake_build(state, code):
        return Mission(
            code=code,
            title="Quick Action Mission",
            year=2026,
            lang="bn",
            priority="urgent",
            reward=240,
            xp=55,
            deadline_day=2,
            translator_difficulty=12,
            qa_threshold=15,
            roles=[RoleSlot(role="man1", lines=150, gender="male"), RoleSlot(role="fem1", lines=110, gender="female")],
            source="database",
            client_name="Titan Global",
            client_tier="enterprise",
            reputation_reward=3,
            modifiers=["premium_notes", "rush_rewrite"],
        )

    monkeypatch.setattr(mod, "sync_state_with_db", fake_sync)
    monkeypatch.setattr(mod, "build_mission_from_movie_code", fake_build)

    payload = mod._recommend_roster_quick_actions({"code": "BN-777"})

    assert payload["available"] is True
    assert payload["translator"]["recommended"]["command"].startswith("/assigntr ")
    assert payload["roles"][0]["recommended"]["command"].startswith("/assign man1 ")
    assert payload["roles"][1]["recommended"]["command"].startswith("/assign fem1 ")
