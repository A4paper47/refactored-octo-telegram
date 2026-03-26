# Studio Dub Tycoon — Render Web Service Edition (v21)

This package continues the cleaned Render Web Service build and extends both the dashboard and Telegram gameplay UI.

## What changed in v21

- added **roster-backed quick actions** on the website dashboard
  - recommended translator from the synced roster
  - recommended role picks per mission role
  - copy-ready assign commands built from the selected mission
  - new API route: `/api/mission/<movie_code>/quick-actions`
- refined Telegram mission cards to be **more compact and more informative**
  - faster scan for client, reward, cast progress, and preset hint
  - cleaner selected mission flow after `/mission`, `/pick`, `/accept`, and `/dbmission`
- refreshed dashboard copy and build id to `20260326-v21-roster-quick-actions`

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
