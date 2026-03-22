# Cleanup Report — v11

This package was reduced to the files that are still used by the current Render Web Service deployment.

## Files still used at runtime

### Core service
- `render_game_web.py`
- `Dockerfile`
- `render.yaml`
- `requirements.txt`
- `.dockerignore`

### DB/runtime support
- `db.py`
- `models.py`
- `assign_logic.py`
- `version.py`

### Telegram game package
- `telegram_game/game_engine.py`
- `telegram_game/db_integration.py`
- `telegram_game/telegram_studio_game_bot.py`
- `telegram_game/__init__.py`

### Website UI
- `templates/render_dashboard.html`
- `static/render_dashboard.css`
- `static/render_dashboard.js`

### Tests kept
- `telegram_game/test_game_engine.py`
- `telegram_game/test_db_integration.py`
- `telegram_game/test_render_web_service.py`
- `telegram_game/test_bot_callback.py`
- `telegram_game/test_v11_ui_cleanup.py`
- `conftest.py`

## Files removed from deployment package

These were not needed for the current webhook dashboard + Telegram game path:

- `app.py`
- `bot_ptb.py`
- `export_dynamic.py`
- `export_excel.py`
- `restore_dynamic.py`
- `movie_history.py`
- `movie_merge.py`
- `ops_log.py`
- `sec_logging.py`
- `_ins_admin.py`
- `_test_translator_srt.py`
- `_test_web_smoke.py`
- `admin_snip.txt`
- `Web Vo tracker excel.xlsx`
- `test_screenshot.db`
- `test_smoke.db`
- old `templates/*.html` not used by `render_game_web.py`
- old `static/style.css`

## Why they were removed

- not imported by the current Render entrypoint
- not required by the current Telegram game flow
- legacy files from the earlier Flask tracker app
- local helper files or temporary artifacts
- duplicate UI assets no longer referenced

## Result

The deployment package is smaller, easier to inspect, and less confusing when editing or deploying.
