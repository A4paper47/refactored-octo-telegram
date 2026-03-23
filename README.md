# Studio Dub Tycoon — Render Web Service Edition (v20)

This package continues the cleaned Render Web Service build and extends both the dashboard and Telegram gameplay UI.

## What changed in v20

- added **live simulator action decks** on the website dashboard
  - copy-ready recommended preset action
  - full recommended workflow copy
  - assign/team/submit quick actions
  - preset action deck for recommended, language-safe, workload-safe, and trait-polish flows
  - operator summary card tied to the selected mission
- upgraded Telegram mission flow with **one-tap preset apply buttons**
  - `recommended`
  - `lang`
  - `workload`
  - `trait`
- `/accept`, `/mission`, and selected mission cards now surface preset buttons directly
- refreshed dashboard copy and build id to `20260323-v20-live-sim-actions`

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
