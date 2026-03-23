# Studio Dub Tycoon — Render Web Service Edition (v19)

This package continues the cleaned Render Web Service build and extends both the dashboard and Telegram gameplay UI.

## What changed in v19

- added **mission simulator** panel on the dashboard
  - recommended assign preset
  - operator playbook notes
  - quick copy sequence
  - API route for simulation payloads
- upgraded Telegram assignment flow with **smart presets**
  - `lang`
  - `workload`
  - `trait`
  - `recommended`
- added `/assignpreset <recommended|lang|workload|trait>` command
- assign UI and role picker now preserve the active preset across pagination and filters
- refreshed dashboard copy and build id to `20260323-v19-simulator-presets-ui`

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
