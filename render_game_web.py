from __future__ import annotations

import asyncio
import atexit
import logging
import os
import threading
from typing import Any, Optional

from flask import Flask, jsonify, redirect, render_template, request, url_for
from telegram import Update

from telegram_game.db_integration import count_db_movie_candidates, list_db_missions
from telegram_game.game_engine import new_game
from telegram_game.telegram_studio_game_bot import build_game_application
from version import APP_VERSION, BUILD_ID

log = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
PORT = int(os.getenv("PORT", "10000"))
HOST = os.getenv("HOST", "0.0.0.0")
RENDER_EXTERNAL_URL = (os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
WEBHOOK_SECRET = (os.getenv("WEBHOOK_SECRET") or "studio-game-webhook").strip()
TELEGRAM_SECRET_TOKEN = (os.getenv("TELEGRAM_SECRET_TOKEN") or WEBHOOK_SECRET).strip()
BOT_AUTO_START = os.getenv("BOT_AUTO_START", "1").strip() not in ("0", "false", "False", "")
BOT_ENABLED = bool(BOT_TOKEN)
GAME_USE_DB = os.getenv("GAME_USE_DB", "1").strip() not in ("0", "false", "False", "")

RUNTIME_FILE_GROUPS = {
    "core": [
        "render_game_web.py",
        "Dockerfile",
        "render.yaml",
        "requirements.txt",
        "db.py",
        "models.py",
        "assign_logic.py",
        "version.py",
    ],
    "telegram": [
        "telegram_game/game_engine.py",
        "telegram_game/db_integration.py",
        "telegram_game/telegram_studio_game_bot.py",
        "telegram_game/__init__.py",
    ],
    "ui": [
        "templates/render_dashboard.html",
        "static/render_dashboard.css",
        "static/render_dashboard.js",
    ],
    "tests": [
        "telegram_game/test_game_engine.py",
        "telegram_game/test_db_integration.py",
        "telegram_game/test_render_web_service.py",
        "telegram_game/test_bot_callback.py",
        "telegram_game/test_v11_ui_cleanup.py",
        "conftest.py",
    ],
}

REMOVED_FILE_GROUPS = {
    "legacy_flask_tracker": [
        "app.py",
        "bot_ptb.py",
        "export_dynamic.py",
        "export_excel.py",
        "restore_dynamic.py",
        "movie_history.py",
        "movie_merge.py",
        "ops_log.py",
        "sec_logging.py",
    ],
    "temporary_or_local": [
        "_ins_admin.py",
        "_test_translator_srt.py",
        "_test_web_smoke.py",
        "admin_snip.txt",
        "Web Vo tracker excel.xlsx",
        "test_screenshot.db",
        "test_smoke.db",
    ],
    "old_ui_assets": [
        "templates/*.html from old tracker app",
        "static/style.css",
    ],
}

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

_game_app = None
_bot_loop: Optional[asyncio.AbstractEventLoop] = None
_bot_thread: Optional[threading.Thread] = None
_bot_started = False
_bot_start_error: Optional[str] = None
_start_lock = threading.Lock()


def webhook_path() -> str:
    return f"/telegram/webhook/{WEBHOOK_SECRET}"


def webhook_url(base_url: Optional[str] = None) -> str:
    root = (base_url or RENDER_EXTERNAL_URL or "").strip().rstrip("/")
    if not root:
        return ""
    return f"{root}{webhook_path()}"


def _default_allowed_updates() -> list[str]:
    return ["message", "callback_query"]


def run_bot_coro(coro):
    if not _bot_loop:
        raise RuntimeError("Telegram bot loop not started")
    future = asyncio.run_coroutine_threadsafe(coro, _bot_loop)
    return future.result(timeout=45)


async def _process_update(update: Update) -> None:
    if not _game_app:
        return
    await _game_app.process_update(update)


async def _set_webhook_if_possible(drop_pending_updates: bool = False, explicit_base_url: Optional[str] = None) -> dict[str, Any]:
    if not _game_app:
        return {"ok": False, "message": "bot disabled"}
    url = webhook_url(explicit_base_url)
    if not url:
        return {"ok": False, "message": "Missing RENDER_EXTERNAL_URL"}
    await _game_app.bot.set_webhook(
        url=url,
        allowed_updates=_default_allowed_updates(),
        drop_pending_updates=drop_pending_updates,
        secret_token=TELEGRAM_SECRET_TOKEN or None,
    )
    info = await _game_app.bot.get_webhook_info()
    return {
        "ok": True,
        "url": info.url,
        "pending_update_count": info.pending_update_count,
        "max_connections": info.max_connections,
        "allowed_updates": info.allowed_updates,
        "has_custom_certificate": info.has_custom_certificate,
        "last_error_message": info.last_error_message,
    }


async def _delete_webhook(drop_pending_updates: bool = False) -> dict[str, Any]:
    if not _game_app:
        return {"ok": False, "message": "bot disabled"}
    await _game_app.bot.delete_webhook(drop_pending_updates=drop_pending_updates)
    info = await _game_app.bot.get_webhook_info()
    return {
        "ok": True,
        "url": info.url,
        "pending_update_count": info.pending_update_count,
        "last_error_message": info.last_error_message,
    }


async def _get_webhook_info() -> dict[str, Any]:
    if not _game_app:
        return {"ok": False, "message": "bot disabled"}
    info = await _game_app.bot.get_webhook_info()
    return {
        "ok": True,
        "url": info.url,
        "pending_update_count": info.pending_update_count,
        "last_error_message": info.last_error_message,
        "last_error_date": info.last_error_date,
        "max_connections": info.max_connections,
        "allowed_updates": info.allowed_updates,
        "ip_address": info.ip_address,
    }


async def _startup_game_bot() -> None:
    assert _game_app is not None
    await _game_app.initialize()
    await _game_app.start()
    log.info("Studio game PTB application initialized")


async def _shutdown_game_bot() -> None:
    if not _game_app:
        return
    try:
        await _game_app.stop()
    finally:
        await _game_app.shutdown()



def _boot_loop_forever() -> None:
    assert _bot_loop is not None
    asyncio.set_event_loop(_bot_loop)
    _bot_loop.run_forever()



def _ensure_bot_started() -> bool:
    global _game_app, _bot_loop, _bot_thread, _bot_started, _bot_start_error

    if _bot_started:
        return True
    if not BOT_ENABLED:
        _bot_start_error = "Missing BOT_TOKEN"
        return False

    with _start_lock:
        if _bot_started:
            return True
        try:
            _game_app = build_game_application(token=BOT_TOKEN)
            _bot_loop = asyncio.new_event_loop()
            _bot_thread = threading.Thread(target=_boot_loop_forever, daemon=True, name="telegram-game-loop")
            _bot_thread.start()
            run_bot_coro(_startup_game_bot())
            _bot_started = True
            _bot_start_error = None
            log.info("Studio game bot loop started")
            if RENDER_EXTERNAL_URL:
                try:
                    info = run_bot_coro(_set_webhook_if_possible())
                    log.info("Webhook ready: %s", info.get("url"))
                except Exception as exc:  # pragma: no cover
                    log.warning("Auto set webhook failed: %s", exc)
            else:
                log.info("RENDER_EXTERNAL_URL not set; skipping auto webhook setup")
        except Exception as exc:
            _bot_start_error = str(exc)
            log.exception("Failed to start Studio game bot: %s", exc)
            _bot_started = False
            return False
    return _bot_started



def _cleanup() -> None:
    global _bot_loop
    if _bot_loop and _bot_started:
        try:
            run_bot_coro(_shutdown_game_bot())
        except Exception:
            pass
        try:
            _bot_loop.call_soon_threadsafe(_bot_loop.stop)
        except Exception:
            pass


atexit.register(_cleanup)

if BOT_AUTO_START:
    _ensure_bot_started()


def _service_snapshot() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "service": "studio-dub-tycoon-webhook",
        "status": "ok" if _bot_started else "starting" if BOT_ENABLED else "disabled",
        "bot_enabled": BOT_ENABLED,
        "bot_started": _bot_started,
        "mode": "webhook",
        "game_use_db": GAME_USE_DB,
        "webhook_path": webhook_path(),
        "render_external_url": RENDER_EXTERNAL_URL or None,
        "webhook_url": webhook_url() or None,
        "start_error": _bot_start_error,
        "app_version": APP_VERSION,
        "build_id": BUILD_ID,
    }
    if BOT_ENABLED and _bot_started:
        try:
            info = run_bot_coro(_get_webhook_info())
            payload["webhook_info"] = info
        except Exception as exc:
            payload["webhook_info_error"] = str(exc)
    return payload


def _safe_page(raw: Optional[str]) -> int:
    try:
        return max(1, int((raw or "1").strip() or "1"))
    except ValueError:
        return 1


def _dashboard_filters() -> dict[str, Any]:
    return {
        "status": (request.args.get("status") or "").strip() or None,
        "translator": (request.args.get("translator") or "").strip() or None,
        "priority": (request.args.get("priority") or "").strip() or None,
        "lang": (request.args.get("lang") or "").strip() or None,
        "page": _safe_page(request.args.get("page")),
    }


def _manifest_payload() -> dict[str, Any]:
    total_kept = sum(len(items) for items in RUNTIME_FILE_GROUPS.values())
    total_removed = sum(len(items) for items in REMOVED_FILE_GROUPS.values())
    return {
        "kept": RUNTIME_FILE_GROUPS,
        "removed": REMOVED_FILE_GROUPS,
        "kept_total": total_kept,
        "removed_total": total_removed,
    }


def _dashboard_board() -> dict[str, Any]:
    filters = _dashboard_filters()
    if not GAME_USE_DB:
        return {
            "items": [],
            "page": 1,
            "total_pages": 1,
            "total": 0,
            "counts": {},
            "error": "GAME_USE_DB=0, mission board DB dimatikan.",
        }
    try:
        state = new_game(user_id=0, studio_name="Render Dashboard")
        payload = list_db_missions(
            state,
            limit=12,
            status=filters["status"],
            translator=filters["translator"],
            priority=filters["priority"],
            lang=filters["lang"],
            page=filters["page"],
            include_meta=True,
        )
        payload["counts"] = {
            "NEW": count_db_movie_candidates(status="NEW"),
            "IN_PROGRESS": count_db_movie_candidates(status="IN_PROGRESS"),
            "READY": count_db_movie_candidates(status="READY"),
            "COMPLETED": count_db_movie_candidates(status="COMPLETED"),
        }
        payload["error"] = None
        return payload
    except Exception as exc:
        return {
            "items": [],
            "page": filters["page"],
            "total_pages": 1,
            "total": 0,
            "counts": {},
            "error": str(exc),
        }


@app.route("/")
def index():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
@app.route("/dashboard/")
def dashboard():
    return render_template(
        "render_dashboard.html",
        service=_service_snapshot(),
        board=_dashboard_board(),
        filters=_dashboard_filters(),
        manifest=_manifest_payload(),
        setup_path=url_for("setup_webhook_route"),
        webhook_info_path=url_for("webhook_info_route"),
        delete_webhook_path=url_for("delete_webhook_route"),
        api_status_path=url_for("api_status"),
        api_missions_path=url_for("api_missions"),
        api_manifest_path=url_for("api_manifest"),
        health_path=url_for("health"),
    )


@app.route("/api/status")
def api_status():
    return jsonify(_service_snapshot())


@app.route("/api/missions")
def api_missions():
    payload = _dashboard_board()
    payload["filters"] = _dashboard_filters()
    return jsonify(payload)


@app.route("/api/manifest")
def api_manifest():
    return jsonify(_manifest_payload())


@app.route("/health")
def health():
    payload = {
        "ok": True,
        "service": "studio-dub-tycoon-webhook",
        "bot_enabled": BOT_ENABLED,
        "bot_started": _bot_started,
        "start_error": _bot_start_error,
        "app_version": APP_VERSION,
        "build_id": BUILD_ID,
    }
    status = 200 if (not BOT_ENABLED or _bot_started) else 503
    return jsonify(payload), status


@app.route(webhook_path(), methods=["POST"])
def telegram_webhook():
    if not BOT_ENABLED:
        return jsonify({"ok": False, "message": "BOT_TOKEN missing"}), 503
    if not _bot_started and not _ensure_bot_started():
        return jsonify({"ok": False, "message": _bot_start_error or "bot startup failed"}), 503

    if TELEGRAM_SECRET_TOKEN:
        header = (request.headers.get("X-Telegram-Bot-Api-Secret-Token") or "").strip()
        if header != TELEGRAM_SECRET_TOKEN:
            return jsonify({"ok": False, "message": "invalid secret token header"}), 403

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "message": "invalid json payload"}), 400

    try:
        assert _game_app is not None
        update = Update.de_json(payload, _game_app.bot)
        run_bot_coro(_process_update(update))
    except Exception as exc:
        log.exception("Webhook processing failed: %s", exc)
        return jsonify({"ok": False, "message": str(exc)}), 500
    return jsonify({"ok": True})


@app.route("/telegram/setup-webhook", methods=["GET", "POST"])
def setup_webhook_route():
    if not BOT_ENABLED:
        return jsonify({"ok": False, "message": "BOT_TOKEN missing"}), 503
    if not _bot_started and not _ensure_bot_started():
        return jsonify({"ok": False, "message": _bot_start_error or "bot startup failed"}), 503

    explicit_base_url = (request.values.get("base_url") or "").strip() or None
    drop_pending = (request.values.get("drop_pending") or "").strip() in {"1", "true", "True", "yes", "on"}
    try:
        info = run_bot_coro(_set_webhook_if_possible(drop_pending_updates=drop_pending, explicit_base_url=explicit_base_url))
    except Exception as exc:
        log.exception("Manual setup webhook failed: %s", exc)
        return jsonify({"ok": False, "message": str(exc)}), 500
    status = 200 if info.get("ok") else 400
    return jsonify(info), status


@app.route("/telegram/delete-webhook", methods=["GET", "POST"])
def delete_webhook_route():
    if not BOT_ENABLED:
        return jsonify({"ok": False, "message": "BOT_TOKEN missing"}), 503
    if not _bot_started and not _ensure_bot_started():
        return jsonify({"ok": False, "message": _bot_start_error or "bot startup failed"}), 503
    drop_pending = (request.values.get("drop_pending") or "").strip() in {"1", "true", "True", "yes", "on"}
    try:
        info = run_bot_coro(_delete_webhook(drop_pending_updates=drop_pending))
    except Exception as exc:
        log.exception("Delete webhook failed: %s", exc)
        return jsonify({"ok": False, "message": str(exc)}), 500
    return jsonify(info)


@app.route("/telegram/webhook-info")
def webhook_info_route():
    if not BOT_ENABLED:
        return jsonify({"ok": False, "message": "BOT_TOKEN missing"}), 503
    if not _bot_started and not _ensure_bot_started():
        return jsonify({"ok": False, "message": _bot_start_error or "bot startup failed"}), 503
    try:
        info = run_bot_coro(_get_webhook_info())
    except Exception as exc:
        log.exception("Webhook info failed: %s", exc)
        return jsonify({"ok": False, "message": str(exc)}), 500
    return jsonify(info)


if __name__ == "__main__":
    app.run(host=HOST, port=PORT)
