# Studio Dub Tycoon — Render Web Service Edition (v18)

This package continues the cleaned Render Web Service build and extends both the dashboard and Telegram gameplay UI.

## What changed in v18

- added **mission detail modal** on the dashboard for a faster full-screen quick view
- added **quick assign templates** on the website for translator and role assignment commands
- kept the mission workflow panel and copy deck from v17
- upgraded Telegram **assign UI filters** for:
  - translator freshness / calm burnout view
  - role list filtering by gender
  - role picker filtering by energy
- preserved webhook-first Render deployment structure
- updated build id to `20260323-v18-modal-filters-ui`

## Deploy

Use the existing Render Web Service setup:

```bash
sh -c 'gunicorn render_game_web:app --bind 0.0.0.0:${PORT:-10000} --workers ${WEB_CONCURRENCY:-1} --threads 4 --timeout 120'
```

Required environment variables:

- `BOT_TOKEN`
- `RENDER_EXTERNAL_URL`
- `WEBHOOK_SECRET`
- `TELEGRAM_SECRET_TOKEN`
- `GAME_USE_DB`
- `DATABASE_URL` (if DB-backed mission board is enabled)
