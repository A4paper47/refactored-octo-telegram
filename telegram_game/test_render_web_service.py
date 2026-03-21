from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace


class FakeUpdate:
    @staticmethod
    def de_json(payload, bot):
        return {"payload": payload, "bot": bot}


class FakeWebhookInfo:
    def __init__(self, url: str = "https://example.com/telegram/webhook/test"):
        self.url = url
        self.pending_update_count = 0
        self.max_connections = 40
        self.allowed_updates = ["message", "callback_query"]
        self.has_custom_certificate = False
        self.last_error_message = None
        self.last_error_date = None
        self.ip_address = None


class FakeBot:
    def __init__(self):
        self.set_calls = []
        self.deleted = []
        self.info = FakeWebhookInfo()

    async def set_webhook(self, **kwargs):
        self.set_calls.append(kwargs)
        self.info = FakeWebhookInfo(kwargs["url"])
        return True

    async def get_webhook_info(self):
        return self.info

    async def delete_webhook(self, drop_pending_updates: bool = False):
        self.deleted.append(drop_pending_updates)
        self.info = FakeWebhookInfo("")
        return True


class FakeApp:
    def __init__(self):
        self.bot = FakeBot()
        self.updates = []

    async def process_update(self, update):
        self.updates.append(update)



def _load_module(monkeypatch, *, token: str = ""):
    monkeypatch.setenv("BOT_AUTO_START", "0")
    monkeypatch.setenv("BOT_TOKEN", token)
    monkeypatch.setenv("WEBHOOK_SECRET", "abc123")
    monkeypatch.setenv("TELEGRAM_SECRET_TOKEN", "hdr-secret")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://demo.onrender.com")
    import render_game_web

    return importlib.reload(render_game_web)



def test_health_ok_without_bot(monkeypatch):
    mod = _load_module(monkeypatch, token="")
    client = mod.app.test_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["bot_enabled"] is False



def test_webhook_rejects_bad_secret(monkeypatch):
    mod = _load_module(monkeypatch, token="token-123")
    mod._game_app = FakeApp()
    mod._bot_started = True
    client = mod.app.test_client()

    resp = client.post(mod.webhook_path(), json={"update_id": 1})
    assert resp.status_code == 403
    data = resp.get_json()
    assert data["ok"] is False



def test_webhook_processes_update(monkeypatch):
    mod = _load_module(monkeypatch, token="token-123")
    fake_app = FakeApp()
    mod._game_app = fake_app
    mod._bot_started = True
    monkeypatch.setattr(mod, "Update", FakeUpdate)
    monkeypatch.setattr(mod, "run_bot_coro", lambda coro: asyncio.run(coro))
    client = mod.app.test_client()

    resp = client.post(
        mod.webhook_path(),
        json={"update_id": 9, "message": {"message_id": 1}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "hdr-secret"},
    )
    assert resp.status_code == 200
    assert fake_app.updates
    assert fake_app.updates[0]["payload"]["update_id"] == 9



def test_setup_webhook_route(monkeypatch):
    mod = _load_module(monkeypatch, token="token-123")
    fake_app = FakeApp()
    mod._game_app = fake_app
    mod._bot_started = True
    monkeypatch.setattr(mod, "run_bot_coro", lambda coro: asyncio.run(coro))
    client = mod.app.test_client()

    resp = client.get("/telegram/setup-webhook")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["url"].endswith(mod.webhook_path())
    assert fake_app.bot.set_calls
    assert fake_app.bot.set_calls[0]["secret_token"] == "hdr-secret"
