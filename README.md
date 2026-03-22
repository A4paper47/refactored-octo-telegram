# Studio Dub Tycoon — Render Web Service Edition (Clean v11)

This is the cleaned deployment package for the Telegram + web dashboard version of Studio Dub Tycoon.

## What is inside

- Flask web service for Render webhook mode
- Telegram game bot with inline UI
- DB-backed mission board and write-back flow
- Minimal runtime files only
- Small test suite kept for the current deployment path

## Render env vars

Required:

- `BOT_TOKEN`
- `RENDER_EXTERNAL_URL`
- `WEBHOOK_SECRET`
- `TELEGRAM_SECRET_TOKEN`

Optional:

- `DATABASE_URL`
- `GAME_USE_DB=1`
- `BOT_AUTO_START=1`
- `WEB_CONCURRENCY=1`
- `LOG_LEVEL=INFO`

## Start command

The included Dockerfile already starts the correct entrypoint:

```bash
sh -c 'gunicorn render_game_web:app --bind 0.0.0.0:${PORT:-10000} --workers ${WEB_CONCURRENCY:-1} --threads 4 --timeout 120'
```

## Main routes

- `/` → redirects to dashboard
- `/dashboard`
- `/health`
- `/api/status`
- `/api/missions`
- `/api/manifest`
- `/telegram/setup-webhook`
- `/telegram/webhook-info`
- `/telegram/delete-webhook`

## Telegram commands

- `/start`
- `/menu`
- `/help`
- `/mission`
- `/missions`
- `/board`
- `/assignui`
- `/accept`
- `/autocast`
- `/submit`
- `/team`
- `/bench`
- `/studio`
- `/market`
- `/clients`
- `/reputation`
- `/status`
- `/log`
- `/nextday`

## Local test

```bash
pytest -q telegram_game/test_game_engine.py telegram_game/test_db_integration.py telegram_game/test_render_web_service.py telegram_game/test_bot_callback.py telegram_game/test_v11_ui_cleanup.py
```
