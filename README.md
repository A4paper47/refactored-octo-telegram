# v5.6.3
- Navbar cleaned up: main pages stay visible, secondary tools moved into a **More** dropdown so the header no longer becomes an oversized wall of tabs.
- Added defensive **group_role_import_request** table migration/column backfill so `/role_imports` is much less likely to 500 on older DBs.
- Added graceful fallback for Role Imports list/detail/actions: if schema is not ready or one row is broken, the page stays usable instead of crashing the whole route.

# Web VO Tracker v5.6.2

- Added **Role Import Detail** page (`/role_imports/<REQUEST_ID>`) for one pending request with full suggestions, parsed roles, and raw text.
- Added **Refresh suggestions** on web list + detail page, and Telegram command `/refresh_role_import <REQUEST_ID>`.
- Refresh now re-parses `roles_text`, recomputes `roles_json`, recomputes `suggested_json`, and updates the preview using the latest roster/workload state.

# Web VO Tracker v5.5.9

- Added **Movie Title Repair** page (`/title_repair`) to fix bad titles like `fem1 movie name` back to the clean movie title.
- Added Telegram admin commands: `/repair_titles [limit or keyword]` and `/repair_movie_title <MOVIE_CODE or title>`.
- Repair scans both active and archived movies, and blocks changes if a clean-title conflict already exists.

# Web VO Tracker v5.5.8

- Added web Role Imports queue (`/role_imports`) for pending auto-detected role helper requests.
- Admin can approve in 12h / 24h / 36h / 48h modes directly from web, or reject.
- Keeps the Telegram-first flow, but gives a web fallback when admin wants to review from dashboard.

# v5.5.7

- role*.txt helper files in VO groups now auto-detect movie title/year more aggressively using the helper filename itself plus recent group files.
- when role*.txt arrives, bot auto-opens the txt, parses roles, auto-assigns, updates the public card, and tries to delete the helper file.

# Web VO Tracker

**Version: v5.5.6**

## v5.5.6

- Role import approval now has **mode preview refresh** before commit.
- Admin can tap **12h / 24h / 36h / 48h**, compare that mode, then **Confirm** or go **Back**.
- Public VO group stays clean until the final confirm step.

# Web VO Tracker

**Version: v5.5.1**

## v5.5.1

- Added **private admin review card** for auto-detected group role imports.
- Added `/pending_roles [limit or keyword]` and `/review_roles <REQUEST_ID>`.
- DM panel now includes **Pending Roles** for admin quick access.
- Public group stays clean; only admin chat sees raw role preview + suggested assignments before approval.

# Web VO Tracker

**Version: v5.5.0**

## v5.4.9

- Added **Merge Simulator** web page and `/merge_simulate SOURCE | TARGET` bot command.
- Duplicates page now has a **Simulate** button so admin can compare source vs target before real merge.
- Merge simulator shows risk, row counts, overlap, translator compare, VO compare, and warning list.

# Web VO Tracker

Version: v5.4.7

## New in v5.4.7
- New web page: `/cleanup_presets` for stale / archived cleanup shortcuts.
- Bulk Ops now supports **saved filter slots** (3 slots saved in DB).
- New Telegram commands: `/cleanup_presets`, `/stale_movies [days] [limit]`, `/bulk_archive_stale [days] [limit]`.
- DM panel now includes **Cleanup** button for admin quick access.

## New in v5.4.5
- New web page: `/bulk_movies` for mass archive / clear / unarchive / hard delete.
- Topbar now includes **Bulk Ops**.
- New bot commands: `/bulk_archive <keyword> [limit]` and `/bulk_unarchive <keyword> [limit]`.
- Bulk actions now support preview + confirm before Telegram write actions.

## New in v5.4.3
- Dashboard now shows recent movie activity.
- New web page: `/activity` for full movie action feed.
- New bot command: `/activity [limit]`.
- DM panel now includes an Activity button.

# Web VO Tracker

> Version: **5.4.2**

## New in 5.4.2

- Movie history timeline on web and Telegram
- `/movie_history <MOVIE_CODE or title>` command
- Movie card button: **History**
- Tracks key actions like create project, assign translator, reassign VO, clear active roles, archive, unarchive, hard delete, and status changes

## Hotfix 5.4.1-hotfix2
- Added web "Open movie by title or code" on Assignments page so movies with zero active roles can still be opened.
- Added Telegram movie card buttons for Archive and Hard Delete with confirm step.

Hotfix 5.3.9-hotfix2: module-level SQLAlchemy func import for movie search; panel workload callback remains guarded.

# Web VO Tracker (Bot + Web + Telegram)

A Flask web app + Telegram bot for movie dubbing / translation operations.

It covers:
- movie creation and assignment
- translator SRT intake + QA queue
- VO role assignment + submission tracking
- deadline / overdue / reminder control
- backups, restore, logs, and Telegram ops tools

## Current build

- App/Bot version: **5.3.9**
- Style: **title-first flow**
- Main idea: admin can work mostly by **movie title**, while the system auto-generates and keeps the internal movie code.

---

## What the system does

### Web (admin)

- **Dashboard**
  - Movies / assignments / QA counters
  - VO workload summary
  - Translator workload summary
  - Daily + monthly activity
- **Assignments**
  - Create project from movie name or code
  - Auto-generate movie code if you enter title only
  - Auto-assign VO roles
  - Project detail with status and line counts
- **VO Team roster**
  - add / edit / disable VO members
  - optional Telegram username / Telegram user id
- **Translator roster**
  - add / edit / disable translators
  - optional Telegram username / Telegram user id
- **Queue**
  - `READY_FOR_QA → IN_QA → DONE / REJECTED`
  - reset / unlock tools for stuck queue items
- **Backups**
  - Excel full export
  - JSON ZIP full backup
  - logs.txt export
  - send backup to Telegram
- **Restore**
  - dry run before write
  - Replace or Append mode
  - selected tables support
  - optional include admin tables / system logs
- **Telegram panel**
  - webhook setup
  - broadcast tools
  - owner/admin command runner
- **Tips**
  - short workflow cheat-sheet

### Telegram bot

- **Admin flow**
  - create movie / project from Telegram
  - button panel in DM
  - find movie, view card, preview + confirm assign translator, preview + confirm reassign VO
  - workload / who-has / overdue / priority / daily summary / admin digest
  - deadline setting + reminders
  - backups and backup destination
- **Translator flow**
  - DM `.srt` to bot
  - bot creates queue item
  - optional `/submit <movie>` mode
  - self-check with `/my_tasks`
- **VO flow**
  - upload media / ZIP in VO group with role caption
  - role auto-detection from ZIP filenames like `man1`, `fem2`
  - self-check with `/my_roles`

> Late / overdue only counts when a deadline exists. If deadline is empty, it is **not late**.

---


### New in 5.3.9

- Admin write actions now support **short-lived Undo** after success.
- Undo covers translator assign, VO reassign, clear movie, translator deadline change, and VO deadline change.
- New command: `/undo_last` to reverse your most recent still-valid undo action.
- Preview + confirm flow from 5.3.8 remains in place, now with a safer rollback layer.
- Added `UNDO_ACTION_TTL_MIN` env for undo expiry control (default 20 minutes).


## Telegram command guide

## General

- `/start`
- `/help`
- `/version`
- `/me`
- `/panel` or `/menu` — open DM button dashboard
- `/cancel` — cancel current DM flow

## Admin: movie + assignment flow

- Paste in chat: `Movie Title (2025) - bn`
- `/create_movie Title | 2025 | bn`
- `/create_project Title | 2025 | bn | superurgent/urgent/nonurgent/flexible | man-1 120; fem-1 80`
- `/project` — alias of `/create_project`
- `/project_wizard` — guided button flow in DM
- `/new_project` — alias of `/project_wizard`
- `/project_cancel`
- `/find_movie <keyword>`
- `/movie <MOVIE_CODE or title>`
- `/rename_movie <MOVIE_CODE or title> | <new title> | <year?> | <lang?>`
- `/set_movie ...` — alias of `/rename_movie`
- `/assign_translator <MOVIE_CODE or title> | <name/@username>  (preview + confirm + undo)`
- `/suggest_translator <MOVIE_CODE or title>`
- `/reassign_vo <MOVIE_CODE or title> | <role> | <VO name>  (preview + confirm + undo)`
- `/suggest_vo <MOVIE_CODE or title> | <role?>`
- `/bulk_assign <MOVIE_CODE or title>`
- `/clear_movie <MOVIE_CODE or title>  (undo available)`
- `/vo_stats <MOVIE_CODE or title>`
- `/progress <MOVIE_CODE or title>`
- `/who_has <MOVIE_CODE or title>`
- `/movie_workload <MOVIE_CODE or title>`
- `/workload [translator|vo|all]`

## Admin: deadlines + reminders

- `/deadlines <MOVIE_CODE or title>`
- `/deadline_tr <MOVIE_CODE or title> | <YYYY-MM-DD HH:MM MYT>  (undo available)`
- `/deadline_tr <MOVIE_CODE or title> | clear`
- `/deadline_vo <MOVIE_CODE or title> | <role/open/all> | <YYYY-MM-DD HH:MM MYT>  (undo available)`
- `/deadline_vo <MOVIE_CODE or title> | <role/open/all> | clear`
- `/remind_tr <MOVIE_CODE or title>`
- `/remind_vo <MOVIE_CODE or title> | <role/open/all>`
- `/overdue [translator|vo|all]`
- `/late` — alias of `/overdue`
- `/remind_overdue [translator|vo|all] [limit]`
- `/priority [limit]`
- `/priority_movies [limit]` — alias of `/priority`
- `/summary_today`
- `/daily_summary` — alias of `/summary_today`
- `/digest_here` — save current chat as admin digest destination
- `/digest_status` — show admin digest destination + enabled status
- `/digest_now [dest]` — send admin digest now
- `/digest_on` / `/digest_off` — enable or disable cron digest
- `/undo_last` — reverse your latest still-valid undo action

### Practical examples

- `/deadline_tr Inside Out 2 | 2026-03-10 22:00`
- `/deadline_vo Inside Out 2 | open | 2026-03-10 22:00`
- `/remind_overdue all 10`
- `/priority 8`
- `/summary_today`

## Admin: VO group / binding

- `/request_group <MOVIE_CODE or title>`
- `/bind <MOVIE_CODE or title>`
- `/group_reject <REQUEST_ID> <note>`

## Admin: backups

- `/backup_here` — save current chat as backup destination
- `/backup_status`
- `/backup_now [all|json|excel|logs] [dest]`

## Admin: owner-only

- `/admin_add <tg_id> [display_name]`
- `/admin_remove <tg_id>`

## Translator self-service

- send `.srt` in DM
- `/submit <MOVIE_CODE or title>`
- `/my_tasks`
- `/cancel`

## VO self-service

- send media / ZIP in VO group
- `/my_roles`

---

## DM panel buttons

When admin opens `/panel`, the DM dashboard includes:

- **Create Project**
- **Find Movie**
- **Workload**
- **Who Has**
- **Assign Translator**
- **Reassign VO**
- **Movie Workload**
- **Overdue**
- **Priority**
- **Remind Overdue**
- **Daily Summary**
- **Digest Now**
- **Backup Now**
- **Backup Status**
- **My Tasks**
- **My Roles**

This is meant to reduce long command typing for daily ops.

The digest flow is useful when you want one compact admin update pushed to a chosen chat.

---

## Movie card actions

Movie cards now support button-based admin actions such as:

> After successful assign/reassign/clear/deadline changes, the bot also sends a short-lived **Undo** button.


- Received
- QA Ready
- Wait Embed
- Completed
- Progress
- Who Has
- Translator Picks
- Assign Translator
- Reassign VO
- Workload
- Deadlines
- Remind
- Clear Movie
- Send Card

---

## Title-first assignment flow

This project now supports working by **movie title first**.

Example web/admin idea:
- enter `Inside Out 2`
- choose year `2024`
- choose lang `bn`
- paste roles
- system auto-creates movie row and generates internal code

This means your team can think in **movie names**, while the system still keeps a consistent code internally.

---

## Translator DM workflow

Recommended:
- translator DM the bot with `.srt` only
- no caption needed
- best filename: `MOVIECODE.srt`

Supported behavior:
- bot detects movie code or title match
- queue row is created automatically
- `TranslationTask` can auto-complete when matched
- optional forward to `SRT_OUTBOX_CHAT_ID`

---

## VO group workflow

Recommended caption format:
- `BN-260303-01 man-1 120`
- `BN-260303-01 fem-2 80`

ZIP support:
- role names inside filenames like `man1`, `man-1`, `fem2`, `fem-2` can be auto-detected

When all roles are completed:
- bot can post `WAIT_EMBED` notice

---

## Backups and restore

## Backup routine

Recommended every 2 weeks:
1. download **JSON ZIP full** from `/backups`
2. download **Excel full**
3. download **logs.txt**

Optional Telegram flow:
- run `/backup_here` in your target chat once
- then run `/backup_now dest json`

Cron endpoint:
- `/cron/backup?key=CRON_SECRET&mode=json`
- `/cron/admin_digest?key=CRON_SECRET`
- optional: `?chat_id=<tg_chat_id>&limit=5`

## Restore flow

Use web page `/restore`.

Recommended:
1. upload JSON ZIP backup
2. click **Dry run** first
3. review detected tables / columns
4. upload same ZIP again
5. choose **Replace** or **Append**
6. type `RESTORE`
7. click **Restore now**

Safety defaults:
- admin tables are excluded unless you tick them
- system logs are excluded unless you tick them

---

## Environment variables

## Required web

- `DATABASE_URL`
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`
- `SECRET_KEY`

## Required for bot

- `BOT_TOKEN`
- `WEBHOOK_SECRET`
- `RENDER_EXTERNAL_URL`

## Backup / export

- `BACKUP_TELEGRAM_CHAT_ID`
- `CRON_SECRET`
- `ADMIN_DIGEST_CHAT_ID` (optional; overrides DB digest destination)
- `ADMIN_DIGEST_ENABLED` (default `1`)
- `EXPORT_MAX_LOGS`

## Optional routing / ops

- `ADMIN_TELEGRAM_CHAT_ID`
- `DROP_CHAT_ID`
- `SRT_OUTBOX_CHAT_ID`
- `ARCHIVE_CHAT_ID`
- `SRT_FORWARD_ANON=1`
- `ADMIN_USER_IDS`
- `OWNER_TG_ID`

## Admin panel options

- `DISABLE_LOGIN=1` — testing only
- `ADMIN_PANEL_KEY` — unlock key when login is disabled

> Production recommendation: do not leave weak default secrets, and do not rely on open admin panel mode.

---

## Local run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export DATABASE_URL=sqlite:///local.db
export ADMIN_EMAIL=admin@local
export ADMIN_PASSWORD=admin123
export SECRET_KEY=change-me
# optional
export BOT_TOKEN=123:fake

python app.py
```

Open:
- `http://127.0.0.1:5000/`

---

## Render deploy notes

- deploy the **whole repo**, not only `app.py`
- make sure `templates/` and `static/` are included
- set `RENDER_EXTERNAL_URL`
- if bot is enabled, webhook path is:
  - `{RENDER_EXTERNAL_URL}/webhook/{WEBHOOK_SECRET}`

Recommended post-deploy checks:
- web topbar/version shows expected version
- `/help` shows latest commands
- `/panel` buttons work in DM
- `/restore` loads
- `/tips` loads
- backup/export works

---

## Troubleshooting

- open Logs modal in web, or download `logs.txt`
- use `/backup_status` to verify Telegram backup destination
- if reminders fail, check whether translator / VO has linked Telegram ID or username
- if a bot token leaks, revoke it in BotFather and update ENV immediately


## Hotfixes

- **5.3.9-hotfix1**
  - Fixed `cmd_workload()` crash when opened from Telegram panel callback where `context.args` can be `None`.
  - Added missing `from sqlalchemy import func` import for movie search helpers and workload queries.


## Archive vs hard delete

Assignment View on the web now has 3 separate actions:

- **Clear active roles only**: removes current VO assignment rows only.
- **Archive movie**: marks the movie archived, clears active ops rows, and hides it from Telegram movie search / find-movie flows.
- **Hard delete permanently**: removes the movie row and related assignment / translation / submission records permanently. You must type `DELETE` to confirm.


## Archived movies

- Web now has an **Archived** page to review archived movies.
- Use **Unarchive** to make a movie visible again in Telegram search.
- Telegram commands:
  - `/archived [limit or keyword]`
  - `/unarchive_movie <MOVIE_CODE or title>`
- Archive hides a movie from normal search. Hard delete permanently removes it.


## 5.4.1-hotfix3
- Telegram movie-card confirm/edit flow now falls back to plain text if Markdown entity parsing fails.


## v5.4.4

- Activity page now supports filters by keyword, event type, source (web/tg), archived state, and limit.
- Added **Export CSV** for filtered activity.
- Bot `/activity` now accepts basic filters like `web`, `tg`, event type, and keyword.


## v5.4.5

- Added **Bulk Ops** web page with search, scope, limit, checkbox selection, and batch actions.
- Active scope supports **Clear active roles** and **Archive selected**.
- Archived scope supports **Unarchive selected** and **Hard delete selected** with `DELETE` confirmation.
- Added Telegram preview + confirm commands:
  - `/bulk_archive <keyword> [limit]`
  - `/bulk_unarchive <keyword> [limit]`
- Bulk archive hides movies from Telegram search; bulk unarchive makes them visible again.


## v5.4.7

- Duplicate movie groups / merge cleanup from web and Telegram
- Web page: `/duplicates`
- Bot commands: `/duplicates`, `/merge_movie <SOURCE> | <TARGET> [| delete]`
- Merge preview shows related rows before confirm
- Source movie can be archived as `MERGED` or hard-deleted after merge


## v5.4.8 merge conflict preview

Duplicate merge preview is now smarter. Before confirm, web and Telegram preview now warn about translator mismatch, translation task overlap, assignment role overlap, VO submission overlap, and bound Telegram movie-card / VO-group conflicts.


## Public VO card + admin-only detail

- Group movie detection details are admin-only.
- Raw role-list messages can be deleted on approval when the bot has delete permission.
- Public VO group messages use a clean assignment card with `Due in ...` / `Late by ...` countdown instead of exposing internal movie code details.


## v5.5.2
- Group role-import approval now cleans helper noise better: raw role text, waiting-approval notice, and tracked `role*.txt` helper files are deleted after approval when the bot has permission.
- VO public card now shows submitted roles with ✅ and pending roles with ⏳, then refreshes after new VO uploads.
- VO submission detection now accepts filename-only uploads in a bound group, for example `fem 1 movie.rar`.


## Deadline modes

- Super Urgent = 12h
- Urgent = 24h
- Non-Urgent = 36h
- Flexible = 48h
- Public VO card shows `Due in ...` / `Late by ...`.


## v5.5.5
- Role import admin approval now has quick buttons for 12h / 24h / 36h / 48h.
- The selected mode is applied directly to created VO assignments and public card deadlines.


## Movie aliases (v5.6.1)
- Bot/web can now store alias titles for a movie.
- Aliases help search resolve noisy helper filenames and previously repaired bad titles.
- Telegram: `/aliases`, `/add_alias`, `/delete_alias`.
- Web: `/movie_aliases`.


## New in v5.6.1

- Web **Resolve Tools** page (`/resolve_tools`) to analyze noisy filenames, helper files, aliases, and group context before importing.
- Telegram commands:
  - `/resolve_movie <filename or title>`
  - `/group_context [chat_id]`
  - `/clear_group_context [chat_id]`
- Useful for checking why a `role*.txt` or weird filename resolved to the wrong movie.


## 5.6.5
- Fix missing `detect_lang_from_filename()` helper used by role-list reply fallback in Telegram bot.
- Keeps navbar cleanup + role-import guard + stale-movies fix from 5.6.3 / 5.6.4.
