# Review + Conversion Notes

## Apa projek asal ini

Ini ialah sistem operasi **Web VO Tracker** berasaskan:
- **Flask web admin**
- **python-telegram-bot** untuk bot Telegram
- **SQLAlchemy + SQLite/Postgres** untuk data

Domain utama projek:
- buat movie/project
- assign translator
- assign VO by role (`man1`, `fem2`, dll.)
- queue submission translator
- deadline / reminder / overdue
- backup / restore / merge / history / cleanup

## Dapatan review

### Kuat
- Struktur domain jelas dan matang.
- Banyak flow penting sudah wujud dalam bot Telegram.
- Ada smoke test dan SRT-flow test.
- Assignment/domain logic boleh digamify dengan natural.

### Risiko / hutang teknikal
- `requirements.txt` pin lama untuk environment moden.
- `psycopg2-binary==2.9.9` gagal build pada Python 3.13 dalam environment test ini.
- `SQLAlchemy==2.0.25` trigger issue pada Python 3.13; runtime lebih selamat dengan versi 2.0.x lebih baru.
- Test path hardcoded ke `/mnt/data/gitgud-5.1.2/...`, jadi environment baru boleh gagal walaupun app ok.
- `DISABLE_LOGIN=1` default pada `app.py` ialah risiko keselamatan jika terdeploy tanpa `ADMIN_PANEL_KEY`.

## Ujian yang dijalankan

### Asal repo
- `_test_web_smoke.py` ✅ lulus selepas sediakan path DB test.
- `_test_translator_srt.py` ✅ lulus selepas guna dependency yang serasi untuk env ini.

### Prototype game baru
- `telegram_game/test_game_engine.py` ✅ `4 passed`

## Cara penukaran ke game Telegram

Saya pilih **chat-based Telegram game**, bukan terus Telegram HTML5 Game API.

Kenapa:
- repo asal memang command-driven + inline button
- lebih cepat untuk reuse konsep sedia ada
- kurang kos integrasi
- boleh diuji terus tanpa frontend web game baru

## Mapping sistem asal → game

- Movie / Project → Mission
- Translator → playable staff unit
- VO Role Assignment → cast puzzle
- Deadline → turn/day pressure
- QA Queue → mission resolution gate
- Workload → energy/stamina
- Success → coins + XP
- History/log → progression log

## Fail baru ditambah

- `telegram_game/game_engine.py`
- `telegram_game/telegram_studio_game_bot.py`
- `telegram_game/test_game_engine.py`
- `telegram_game/README_GAME.md`

## Apa yang prototype baru buat

- buka game baru
- generate mission
- accept mission
- auto-cast translator + VO
- submit ke QA
- kira score, reward, XP
- simpan progress ke fail JSON per user
- inline button menu untuk flow asas

## Cadangan next step paling bernilai

1. sambung game ini ke roster sebenar dari DB asal
2. tukar scoring guna heuristic `assign_logic.py`
3. tambah manual cast UI per role
4. tambah upgrade shop + rarity staff
5. tambah leaderboard / guild / co-op
6. bila loop dah sedap, baru pertimbang Telegram Web App / HTML5 game

---

## V2 Update — Hybrid DB/Game Integration

Selepas prototype pertama, game Telegram kini ada **hybrid integration** dengan struktur DB asal.

### Apa yang ditambah

- `telegram_game/db_integration.py`
- sync roster sebenar daripada table `translator` + `vo_team`
- build mission terus daripada `movie` + `assignment` + `translation_task`
- button dan command baru:
  - `/syncdb`
  - `/dbmission`
- test DB integration menggunakan sqlite temp DB

### Maksud praktikal

Game sekarang bukan sekadar “theme based on project asal”, tapi sudah mula:
- baca translator sebenar
- baca VO team sebenar
- baca movie sebenar
- baca role assignment sebenar
- baca translator assignment sebenar

### Limit semasa

- game masih **read-first**, belum tulis progress/score balik ke DB produksi
- belum guna semua heuristic asal daripada `assign_logic.py`
- belum ada manual cast command dalam bot
- belum ada WebApp / canvas game UI

### Cadangan sambungan paling bernilai

1. Tulis balik hasil mission ke `movie_event`
2. Tambah `/assign` untuk manual casting
3. Reuse `assign_logic.py` sebagai scoring/autocast sebenar
4. Tambah economy: hire / fire / upgrade / burnout
5. Buat mode group co-op untuk satu project dimainkan ramai user


---

## V3 Update — DB Write-Back + Manual Casting

Hybrid mode sekarang bukan setakat baca dari DB, tapi juga boleh **tulis semula** hasil gameplay ke struktur asal bila mission datang dari database.

### Apa yang ditambah

- command baru:
  - `/assigntr <nama>`
  - `/assign <role> <nama>`
  - `/clearcast`
- write-back assignment ke DB:
  - update `movie.translator_assigned`
  - sync / create `translation_task`
  - sync `assignment` ikut cast semasa
  - create audit event dalam `movie_event`
- write-back submit result ke DB:
  - bila lulus QA, mark `movie.status = COMPLETED`
  - mark `translation_task.status = COMPLETED`
  - create `vo_role_submission` untuk setiap role

### Kegunaan praktikal

Sebelum deploy live, kau dah boleh test flow hampir sebenar:
- load mission dari DB
- assign translator manual
- assign cast manual
- submit mission
- tengok perubahan terus masuk balik ke DB

### Limit semasa

- generated mission mode masih tak tulis ke DB
- belum ada command pilih mission tertentu dari senarai
- belum integrate heuristic penuh dari `assign_logic.py`
- belum ada UI WebApp/game canvas
