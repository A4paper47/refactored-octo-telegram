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
- automated tests untuk engine + DB integration

Belum siap:
- shop / upgrade / rarity system
- WebApp / mission board yang lebih visual
- leaderboard / progression layer yang penuh
- Telegram Web App / HTML5 UI

## Cadangan fasa seterusnya

1. Tambah mission board yang ada sorting / grouping ikut status, priority, lang, deadline.
2. Tambah progression layer: hire, unlock, upgrade, burnout, premium clients.
3. Tambah leaderboard / season progression.
4. Tambah mode co-op / guild untuk satu project dimainkan ramai user.
5. Bila loop dah solid, baru naikkan ke Telegram Web App / HTML5.
