# Telegram Game Conversion — Studio Dub Tycoon

Ini ialah **hybrid prototype** penukaran **Web VO Tracker** kepada **chat-based Telegram management game**.

Prototype sekarang ada 2 mode:
- **Game-only demo** → generate mission/raw roster sendiri
- **DB + Game hybrid** → tarik translator, VO, movie, assignment daripada DB projek asal

## Idea utama

Sistem asal:
- movie / project creation
- translator assign
- VO role assign
- deadlines
- QA queue
- reminders / workload

Versi game:
- **Movie / Project** → `Mission`
- **Translator** → hero/unit translation
- **VO roles** → cast slots yang perlu diisi
- **Deadline** → kiraan hari (`deadline_day`)
- **QA** → scoring gate
- **Workload / energy** → stamina staff
- **Reward** → coins + XP

## Command game

- `/start`
- `/newgame [studio name]`
- `/mission`
- `/dbmission` → force load mission daripada DB sebenar
- `/missions [status=...] [translator=...]` → senarai mission aktif dari DB + filter
- `/pick <code>` → pilih mission tertentu dari DB
- `/syncdb` → sync translator + VO roster daripada DB sebenar
- `/accept`
- `/autocast`
- `/assigntr <nama>`
- `/assign <role> <nama>`
- `/clearcast`
- `/team` → tengok team mission semasa
- `/bench` → tengok staff available / belum assign
- `/submit`
- `/roster`
- `/market`
- `/hire <nama>`
- `/fire <nama>`
- `/studio`
- `/upgrade <studio|translator|vo|lounge>`
- `/status`
- `/log`
- `/nextday`

## Cara run

### Demo mode

```bash
export BOT_TOKEN="<telegram-bot-token>"
export GAME_USE_DB=0
python -m telegram_game.telegram_studio_game_bot
```

### Hybrid mode (guna DB projek asal)

```bash
export BOT_TOKEN="<telegram-bot-token>"
export DATABASE_URL="<database-url-projek-asal>"
export GAME_USE_DB=1
python -m telegram_game.telegram_studio_game_bot
```

## Hybrid mode buat apa

Bila `GAME_USE_DB=1` dan `DATABASE_URL` tersedia:
- `/syncdb` akan tarik `translator` + `vo_team` jadi roster game
- `/dbmission` akan bina mission daripada `movie` + `assignment` + `translation_task`
- `/missions` akan paparkan shortlist mission DB yang boleh diuji
- `/missions status=NEW`, `/missions translator=Ryan`, `/missions priority=urgent`, atau `/missions lang=ms` boleh tapis mission
- `/missions page=2` boleh browse page seterusnya bila mission banyak
- `/pick <code>` akan load project tertentu terus ke state game
- assignment sedia ada dalam DB akan terus dipaparkan sebagai cast awal
- translator assigned sedia ada akan dibaca sebagai assigned translator untuk mission
- `/assigntr`, `/assign`, `/clearcast`, dan `/autocast` akan sync balik assignment ke DB bila mission datang dari DB
- `/submit` akan write-back ke `movie`, `translation_task`, `assignment`, `movie_event`, dan `vo_role_submission`

## UI Telegram semasa

- main menu inline button
- inline mission pick button dari senarai `/missions`
- inline paging button Prev / Next untuk mission list panjang
- team view dan bench view untuk tengok siapa sedang bermain dan siapa masih available
- recruitment market untuk hire staff baru
- studio view untuk economy, payroll, dan kos upgrade

## Kenapa format ini dipilih

Telegram ada 2 pendekatan:
1. **Official Telegram Games / HTML5** — perlu setup game di BotFather + web canvas.
2. **Chat-based game** — lebih cepat, murah, dan boleh terus reuse struktur bot sedia ada.

Prototype ini guna **chat-based game** sebab paling dekat dengan repo asal yang memang sudah berasaskan command + inline button.

## Fail baru penting

- `telegram_game/game_engine.py`
- `telegram_game/db_integration.py`
- `telegram_game/telegram_studio_game_bot.py`
- `telegram_game/test_game_engine.py`
- `telegram_game/test_db_integration.py`

## Status semasa

Sudah siap:
- basic gameplay loop
- roster persistence (JSON save per user)
- DB roster sync
- DB-backed mission import
- DB write-back untuk assignment dan submission
- manual cast commands
- DB mission listing + explicit mission pick by code
- mission filtering by status / translator / priority / lang
- paging untuk mission list panjang
- inline pick buttons + Prev / Next paging
- team / bench view
- DB-aware auto-cast guna heuristic dari `assign_logic.py` untuk pilih VO lebih real
- recruitment market / hire / fire
- studio tier, payroll economy, dan upgrade system
- automated tests untuk engine + DB integration

Belum siap:
- rarity / trait system yang lebih dalam
- WebApp / mission board yang lebih visual
- leaderboard / progression layer yang penuh
- Telegram Web App / HTML5 UI

## Cadangan fasa seterusnya

1. Tambah mission board yang ada sorting / grouping ikut status, priority, lang, deadline.
2. Tambah progression layer: hire, unlock, upgrade, burnout, premium clients.
3. Tambah leaderboard / season progression.
4. Tambah mode co-op / guild untuk satu project dimainkan ramai user.
5. Bila loop dah solid, baru naikkan ke Telegram Web App / HTML5.


## V7 Update — Recruitment Market + Studio Economy + Upgrades

Sekarang game dah mula rasa macam **management tycoon** betul, bukan sekadar mission runner.

### Apa yang ditambah

- `/market` untuk tengok calon recruit baru
- `/hire <nama>` untuk ambil staff dari market
- `/fire <nama>` untuk buang staff yang tak diperlukan
- `/studio` untuk tengok studio tier, payroll, dan kos upgrade
- `/upgrade <studio|translator|vo|lounge>` untuk kuatkan studio
- payroll harian bila `/nextday`
- market refresh automatik setiap hari
- studio tier yang mempengaruhi saiz dan kualiti market

### Kegunaan praktikal

- sebelum live, kau dah boleh test loop ekonomi sebenar
- boleh rasa beza antara roster kecil vs roster besar
- boleh hire staff khusus ikut keperluan mission
- boleh simpan coins untuk expansion atau upgrade fokus
- boleh simulate growth studio, bukan sekadar assign dan submit

### Status ujian terkini

- `telegram_game/test_game_engine.py` ✅
- `telegram_game/test_db_integration.py` ✅
- `python -m py_compile telegram_game/*.py` ✅
- total: **22 passed**


## V8 Update — Render Web Service / Webhook Mode

Sekarang game bot dah ada mode khas untuk **Render Web Service**. Ini bermaksud bot tak perlu lagi jalan sebagai polling worker.

### Apa yang ditambah

- fail baru `render_game_web.py`
- webhook endpoint:
  - `/health`
  - `/telegram/webhook/<WEBHOOK_SECRET>`
  - `/telegram/setup-webhook`
  - `/telegram/webhook-info`
  - `/telegram/delete-webhook`
- `Dockerfile` baru untuk launch:
  - `gunicorn render_game_web:app --bind 0.0.0.0:$PORT`
- `render.yaml` blueprint contoh
- test baru untuk Flask webhook service

### Env yang penting untuk Render

- `BOT_TOKEN`
- `RENDER_EXTERNAL_URL`
- `WEBHOOK_SECRET`
- `TELEGRAM_SECRET_TOKEN`
- `GAME_USE_DB`
- `PORT` (default Render biasanya 10000)
- `WEB_CONCURRENCY=1`

### Cara deploy sebagai Web Service

Bila guna Dockerfile dalam repo ini, start command dah siap dalam image.

Kalau kau set manual start command dalam dashboard, guna ini:

```bash
sh -c 'gunicorn render_game_web:app --bind 0.0.0.0:${PORT:-10000} --workers ${WEB_CONCURRENCY:-1} --threads 4 --timeout 120'
```

### Flow lepas deploy

1. pastikan service `Live`
2. buka `/health`
3. buka `/telegram/setup-webhook`
4. lepas webhook set, test `/start` dalam Telegram

### Nota penting

- local JSON save masih sesuai untuk test, tapi pada free web service Render filesystem ialah sementara
- jadi untuk progression game jangka panjang, lebih elok guna DB/persistent disk bila masuk fasa live sebenar
