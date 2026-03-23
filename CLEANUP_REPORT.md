# Cleanup Report — v14

This package keeps only the files used by the current Render Web Service deployment for the dashboard + Telegram game flow.

## Still used

- `render_game_web.py`
- `db.py`
- `models.py`
- `assign_logic.py`
- `version.py`
- `templates/render_dashboard.html`
- `static/render_dashboard.css`
- `static/render_dashboard.js`
- `telegram_game/game_engine.py`
- `telegram_game/db_integration.py`
- `telegram_game/telegram_studio_game_bot.py`
- `telegram_game/__init__.py`
- current test files

## Main v14 focus

- live website action center for webhook tasks
- cleaner dashboard mission command deck
- richer Telegram inline gear UI
- inline train / rest / equip / unequip flow
