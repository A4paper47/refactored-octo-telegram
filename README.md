# Studio Dub Tycoon — Render Web Service Edition (v15)

This is the cleaned deployment package for the Telegram + web dashboard version of Studio Dub Tycoon.

## What changed in v15

- refreshed the website dashboard into a more professional operations layout
- improved hierarchy, spacing, labels, and empty states across the dashboard
- reorganised the sidebar into operations, API, webhook, and Telegram workflow sections
- polished the mission board, action center, live status console, and mission detail panel
- refined Telegram text for:
  - `/menu`
  - `/help`
  - `/missions`
  - home panel wording
- updated build id to `20260323-v15-professional-dashboard-ui`

## What is inside

- Flask web service for Render webhook mode
- Telegram game bot with inline UI
- DB-backed mission board and write-back flow
- training / rest / achievement gameplay loop
- inventory / gear progression loop
- minimal runtime files only
- test suite for the current deployment path

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
- `/api/mission/<movie_code>`
- `/api/manifest`
- `/api/actions/setup-webhook`
- `/api/actions/webhook-info`
- `/api/actions/delete-webhook`
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
- `/roster`
- `/staff <name>`
- `/inventory`
- `/gearshop`
- `/gearui`
- `/buygear <item_key>`
- `/equip <staff> <item_key>`
- `/unequip <staff>`
- `/market`
- `/hire <name>`
- `/fire <name>`
- `/train <name> [balanced|skill|speed]`
- `/rest <name>`
- `/restall`
- `/goals`
- `/studio`
- `/clients`
- `/reputation`
- `/status`
- `/log`
- `/nextday`

## Local test

```bash
pytest -q
python -m py_compile telegram_game/*.py render_game_web.py
```


## v16 highlights
- Live mission search and quick filters on the Render dashboard
- Cleaner action feedback and selected-mission insight cards
- Telegram `/rosterui` paged staff browser for faster game navigation
