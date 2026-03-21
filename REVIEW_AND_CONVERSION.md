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


---

## V4 Update — Mission List + Pick Specific Project

Pre-live testing sekarang lebih praktikal sebab bot tak lagi bergantung pada auto-pick sahaja.

### Apa yang ditambah

- command baru:
  - `/missions`
  - `/pick <code>`
- button menu baru untuk lihat shortlist mission DB
- ranking candidate mission DB supaya project aktif lebih diutamakan
- helper untuk load mission tertentu berdasarkan `movie_code`
- test coverage baru untuk mission listing dan manual pick

### Kegunaan praktikal

Sebelum deploy live, kau dah boleh:
- tengok beberapa project dari DB yang sesuai dijadikan mission
- pilih sendiri project mana nak test
- elak bot auto-ambil project yang bukan kau nak
- test write-back pada mission tertentu dengan lebih terkawal

### Status ujian terkini

- `telegram_game/test_game_engine.py` ✅
- `telegram_game/test_db_integration.py` ✅
- `python -m py_compile telegram_game/*.py` ✅
- total: **10 passed**

### Limit semasa

- belum ada paging / filter by translator / filter by status
- button pick terus dari inline list belum dibuat
- belum integrate heuristic penuh dari `assign_logic.py`
- belum ada WebApp/game canvas


---

## V5 Update — Filtered Mission List + Inline Pick + Team/Bench View

Sekarang bot lebih sedap untuk pre-live test sebab browsing mission dan semak roster semasa dah jauh lebih jelas.

### Apa yang ditambah

- `/missions [status=...] [translator=...]`
  - boleh tapis ikut status movie
  - boleh tapis ikut translator assigned
- inline button terus pada senarai mission
  - klik terus untuk pick project
- command baru:
  - `/team`
  - `/bench`
- view baru:
  - team mission semasa
  - bench staff yang belum digunakan
- test coverage baru untuk:
  - filter mission ikut status
  - filter mission ikut translator
  - team summary
  - bench summary

### Kegunaan praktikal

Sebelum live, kau dah boleh:
- cari project yang betul-betul nak diuji, bukan scroll manual semua
- tapis project ikut translator tertentu
- tengok siapa dah masuk mission semasa
- tengok siapa masih available atas bench sebelum assign manual
- pick mission terus dengan button tanpa perlu copy-paste code setiap kali

### Status ujian terkini

- `telegram_game/test_game_engine.py` ✅
- `telegram_game/test_db_integration.py` ✅
- `python -m py_compile telegram_game/*.py` ✅
- total: **14 passed**

### Limit semasa

- belum ada paging untuk mission list panjang
- belum ada filter ikut priority / lang / deadline
- belum integrate heuristic penuh dari `assign_logic.py`
- belum ada WebApp/game canvas

---

## V6 Update — Paging + Priority/Lang Filter + DB Heuristic Auto-Cast

Sekarang mission browsing dan auto-cast dah lebih hampir kepada operasi sebenar projek asal.

### Apa yang ditambah

- `/missions` kini sokong filter tambahan:
  - `priority=...`
  - `lang=...`
  - `page=...`
- inline paging button:
  - `Prev`
  - `Next`
- metadata page count pada list mission
- `db_integration.py` kini ada **DB-aware auto-cast**
  - translator dipilih ikut language match + workload aktif
  - VO dipilih menggunakan heuristic dari `assign_logic.py` (`pick_vo`)
- test coverage baru untuk:
  - filter priority
  - filter lang
  - paging meta
  - auto-cast DB heuristic

### Kegunaan praktikal

Sebelum live, kau dah boleh:
- browse mission DB yang banyak tanpa semak satu-satu secara manual
- tapis project ikut bahasa dan priority sebenar
- klik next / prev terus dalam Telegram
- guna auto-cast yang lebih dekat dengan logic operasi asal, bukan random power sahaja
- elak VO tertentu di-overload bila heuristic DB detect workload tinggi

### Status ujian terkini

- `telegram_game/test_game_engine.py` ✅
- `telegram_game/test_db_integration.py` ✅
- `python -m py_compile telegram_game/*.py` ✅
- total: **17 passed**

### Limit semasa

- auto-cast translator masih heuristic custom ringan, belum 100% ambil semua business rule asal
- mission board masih text-based, belum ada UI panel yang lebih visual
- belum ada economy layer penuh (hire, fire, unlock, upgrade)
- belum ada WebApp/game canvas



## V7 Update — Recruitment Market + Studio Economy + Upgrades

Game layer sekarang dah masuk fasa progression sebenar. Bukan setakat pick mission dan submit, tapi dah ada **economy loop** untuk pertumbuhan studio.

### Apa yang ditambah

- recruitment market berasaskan hari + studio tier
- hire / fire staff
- payroll harian
- studio tier expansion
- upgrade berasingan untuk translator lab, VO booth, dan lounge
- save/load state sekarang simpan market, studio tier, dan upgrade level

### Kesan kepada gameplay

- player kena urus bajet, bukan cuma cast terbaik setiap kali
- roster besar beri fleksibiliti, tapi payroll naik
- expansion bagi market lebih besar dan recruit lebih kuat
- upgrade translator/VO/lounge beri growth jangka panjang

### Status ujian terkini

- `telegram_game/test_game_engine.py` ✅
- `telegram_game/test_db_integration.py` ✅
- `python -m py_compile telegram_game/*.py` ✅
- total: **22 passed**

### Limit semasa

- belum ada rarity/trait unik per staff
- belum ada contract expiry / burnout mendalam
- belum ada gacha/client reputation/season ladder
- belum ada visual WebApp board


---

## V8 Update — Web Service Fix for Render

Punca utama bot game tak respon di Render sebelum ini ialah architecture mismatch:
- game bot dijalankan dengan `run_polling()`
- Render service pula jenis **Web Service** yang perlukan HTTP listener aktif pada port awam
- `Dockerfile` lama masih launch `gunicorn app:app`, jadi game bot tak pernah start

### Pembetulan yang dibuat

- tambah `render_game_web.py` sebagai entrypoint webhook khusus untuk game bot
- PTB app game sekarang dihidupkan dalam asyncio loop latar, tapi update masuk melalui **Flask webhook route**
- tambah route:
  - `/`
  - `/health`
  - `/telegram/webhook/<WEBHOOK_SECRET>`
  - `/telegram/setup-webhook`
  - `/telegram/webhook-info`
  - `/telegram/delete-webhook`
- `Dockerfile` ditukar supaya bind ke `0.0.0.0:$PORT`
- tambah `render.yaml` contoh untuk Web Service
- tambah test baru untuk webhook Flask app

### Kesan praktikal

Sekarang repo ini boleh dideploy sebagai **Render Web Service** dengan cara yang betul untuk Telegram webhook.

Maknanya:
- Render nampak port HTTP yang valid
- Telegram boleh POST update ke webhook route
- `/start`, button callback, dan command game lain boleh diproses dalam Web Service mode
- tak perlu pakai Background Worker untuk versi ini

### Ujian terkini

- `telegram_game/test_game_engine.py` ✅
- `telegram_game/test_db_integration.py` ✅
- `telegram_game/test_render_web_service.py` ✅
- `python -m py_compile telegram_game/*.py render_game_web.py` ✅
