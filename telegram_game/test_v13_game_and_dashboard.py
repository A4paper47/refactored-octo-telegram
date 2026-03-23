from __future__ import annotations

import importlib
from pathlib import Path
import tempfile

from telegram_game.game_engine import (
    accept_mission,
    auto_cast,
    buy_gear,
    ensure_mission,
    equip_gear,
    inventory_summary,
    mission_summary,
    new_game,
    resolve_submission,
    save_state,
    load_state,
    staff_detail_summary,
    unequip_gear,
)
from telegram_game.test_db_integration import _seed_sqlite


def test_v13_inventory_equip_and_modifiers_roundtrip():
    state = new_game(1301, "Gear Studio")
    mission = ensure_mission(state)
    assert mission.modifiers
    assert "Modifiers:" in mission_summary(mission)

    state.coins = 999
    info = buy_gear(state, "focus_notes")
    assert info["qty"] >= 1

    translator = next(member for member in state.roster if member.role_type == "translator")
    equip_info = equip_gear(state, translator.name, "focus_notes")
    assert equip_info["member"].equipped == "focus_notes"
    assert "Equipped:" in staff_detail_summary(state, translator.name)
    assert "Inventory" in inventory_summary(state)

    accept_mission(state)
    auto_cast(state)
    result = resolve_submission(state)
    assert "modifiers" in result

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "v13_save.json"
        save_state(state, path)
        loaded = load_state(path)
    assert loaded is not None
    reloaded = next(member for member in loaded.roster if member.name == translator.name)
    assert reloaded.equipped == "focus_notes"


def test_v13_unequip_returns_item_to_inventory():
    state = new_game(1302, "Unequip Studio")
    translator = next(member for member in state.roster if member.role_type == "translator")
    equip_gear(state, translator.name, "focus_notes")
    info = unequip_gear(state, translator.name)
    assert info["label"]
    assert translator.equipped is None
    assert state.inventory.get("focus_notes", 0) >= 1


def test_render_dashboard_v13_db_board_and_detail(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "dashboard.sqlite"
        db_url = f"sqlite:///{db_path}"
        _seed_sqlite(db_url)

        monkeypatch.setenv("BOT_AUTO_START", "0")
        monkeypatch.setenv("BOT_TOKEN", "")
        monkeypatch.setenv("GAME_USE_DB", "1")
        monkeypatch.setenv("DATABASE_URL", db_url)
        monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://demo.onrender.com")

        import render_game_web

        mod = importlib.reload(render_game_web)
        client = mod.app.test_client()

        resp = client.get("/dashboard")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Mission board" in body
        assert "BN-260320-01" in body
        assert "The current Flask app is not registered" not in body

        detail_resp = client.get("/api/mission/BN-260320-01")
        assert detail_resp.status_code == 200
        payload = detail_resp.get_json()
        assert payload["ok"] is True
        assert payload["detail"]["code"] == "BN-260320-01"
        assert "roles" in payload["detail"]
