from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from telegram_game.db_integration import load_db_mission_into_state, sync_state_with_db
from telegram_game.game_engine import (
    accept_mission,
    auto_cast,
    ensure_mission,
    latest_log,
    load_state,
    mission_summary,
    new_game,
    next_day,
    resolve_submission,
    roster_summary,
    save_state,
)

log = logging.getLogger(__name__)
BOT_TOKEN = os.getenv("BOT_TOKEN")
GAME_DATA_DIR = Path(os.getenv("GAME_DATA_DIR", "./game_data"))
GAME_USE_DB = os.getenv("GAME_USE_DB", "1").strip() not in ("0", "false", "False", "")


def _state_path(user_id: int) -> Path:
    return GAME_DATA_DIR / f"{user_id}.json"


def _load_or_create(user_id: int):
    state = load_state(_state_path(user_id))
    if state is None:
        state = new_game(user_id=user_id)
        save_state(state, _state_path(user_id))
    return state


def _save(state) -> None:
    save_state(state, _state_path(state.user_id))


def _sync_if_possible(state) -> Optional[dict]:
    if not GAME_USE_DB:
        return None
    try:
        return sync_state_with_db(state)
    except Exception as exc:
        log.warning("DB sync failed: %s", exc)
        return None


def _ensure_bot_mission(state):
    if state.current_mission is not None:
        return state.current_mission
    if GAME_USE_DB:
        try:
            mission = load_db_mission_into_state(state)
            if mission is not None:
                return mission
        except Exception as exc:
            log.warning("DB mission load failed: %s", exc)
    return ensure_mission(state)


def _mode_label() -> str:
    return "DB + Game hybrid" if GAME_USE_DB else "Game-only demo"


def _menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Mission", callback_data="g|mission"), InlineKeyboardButton("✅ Accept", callback_data="g|accept")],
        [InlineKeyboardButton("🤖 Auto Cast", callback_data="g|autocast"), InlineKeyboardButton("📤 Submit", callback_data="g|submit")],
        [InlineKeyboardButton("🗄️ DB Mission", callback_data="g|dbmission"), InlineKeyboardButton("🔄 Sync DB", callback_data="g|syncdb")],
        [InlineKeyboardButton("👥 Roster", callback_data="g|roster"), InlineKeyboardButton("📜 Log", callback_data="g|log")],
        [InlineKeyboardButton("⏭️ Next Day", callback_data="g|nextday")],
    ])


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    state = _load_or_create(user.id)
    stats = _sync_if_possible(state)
    text = (
        f"🎮 Selamat datang ke *Studio Dub Tycoon*, {user.first_name or 'Player'}!\n\n"
        f"Mode: *{_mode_label()}*\n"
        "Game ni tukar workflow asal Web VO Tracker jadi management sim dalam Telegram:\n"
        "project → translator → VO cast → QA → reward.\n\n"
        "Command utama:\n"
        "/newgame — reset studio\n"
        "/mission — tengok misi\n"
        "/dbmission — paksa load mission dari DB\n"
        "/syncdb — sync translator/VO dari DB\n"
        "/accept — terima misi\n"
        "/autocast — auto assign team\n"
        "/submit — hantar ke QA\n"
        "/roster — tengok staff\n"
        "/nextday — maju hari\n"
        "/status — ringkasan studio"
    )
    if stats:
        text += f"\n\nSync DB awal: {stats['translator']} translator, {stats['male']} male VO, {stats['female']} female VO"
    await update.effective_message.reply_text(text, reply_markup=_menu(), parse_mode="Markdown")
    _save(state)


async def cmd_newgame(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    studio_name = " ".join(context.args).strip() or f"{user.first_name or 'Player'} Studio"
    state = new_game(user_id=user.id, studio_name=studio_name)
    stats = _sync_if_possible(state)
    _save(state)
    extra = ""
    if stats:
        extra = f"\nDB sync: {stats['total']} staff imported."
    await update.effective_message.reply_text(
        f"🆕 Game baru dibuka: *{studio_name}*\nMode: *{_mode_label()}*{extra}",
        parse_mode="Markdown",
        reply_markup=_menu(),
    )


async def cmd_mission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    mission = _ensure_bot_mission(state)
    _save(state)
    await update.effective_message.reply_text(mission_summary(mission), reply_markup=_menu())


async def cmd_dbmission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    try:
        mission = load_db_mission_into_state(state)
        if mission is None:
            text = "❌ Tiada mission sesuai dalam DB. Fallback guna /mission biasa."
        else:
            text = f"🗄️ DB mission loaded\n\n{mission_summary(mission)}"
    except Exception as exc:
        text = f"❌ DB mission gagal load: {exc}"
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_menu())


async def cmd_syncdb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    try:
        stats = sync_state_with_db(state)
        text = (
            "🔄 Sync DB siap\n"
            f"Translator: {stats['translator']}\n"
            f"VO male: {stats['male']}\n"
            f"VO female: {stats['female']}\n"
            f"Total: {stats['total']}"
        )
    except Exception as exc:
        text = f"❌ Sync DB gagal: {exc}"
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_menu())


async def cmd_accept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _ensure_bot_mission(state)
    mission = accept_mission(state)
    _save(state)
    await update.effective_message.reply_text(f"✅ Misi diterima!\n\n{mission_summary(mission)}", reply_markup=_menu())


async def cmd_autocast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    mission = _ensure_bot_mission(state)
    picks = auto_cast(state)
    _save(state)
    pretty = "\n".join(f"- {k}: {v}" for k, v in picks.items()) if picks else "- Tiada cast tersedia"
    await update.effective_message.reply_text(f"🤖 Auto cast siap untuk {mission.code}:\n{pretty}", reply_markup=_menu())


async def cmd_submit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    try:
        result = resolve_submission(state)
        verdict = "🏆 QA LULUS" if result["passed"] else "💥 QA GAGAL"
        text = (
            f"{verdict}\n"
            f"Mission: {result['code']} — {result['title']}\n"
            f"Score: {result['qa_score']} / {result['threshold']}\n"
            f"Reward: +{result['reward']} coins\n"
            f"XP: +{result['xp']}\n"
            f"Studio coins sekarang: {state.coins}"
        )
    except Exception as exc:
        text = f"❌ {exc}"
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_menu())


async def cmd_roster(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    await update.effective_message.reply_text(roster_summary(state), reply_markup=_menu())


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    mission = _ensure_bot_mission(state)
    text = (
        f"🏢 {state.studio_name}\n"
        f"Mode: {_mode_label()}\n"
        f"Day {state.day} | Coins {state.coins} | XP {state.xp} | Level {state.level()}\n"
        f"Wins {state.wins} | Losses {state.losses}\n\n"
        f"Current mission:\n{mission.title} ({mission.code})\n"
        f"Mission source: {mission.source}"
    )
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_menu())


async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    await update.effective_message.reply_text(f"📜 Log studio\n{latest_log(state)}", reply_markup=_menu())


async def cmd_nextday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    next_day(state)
    _save(state)
    await update.effective_message.reply_text(f"⏭️ Masuk hari {state.day}.", reply_markup=_menu())


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action = (query.data or "").split("|", 1)[-1]

    class _Msg:
        async def reply_text(self2, *args, **kwargs):
            return await query.message.reply_text(*args, **kwargs)

    update.effective_message = _Msg()  # type: ignore[attr-defined]

    mapping = {
        "mission": cmd_mission,
        "dbmission": cmd_dbmission,
        "syncdb": cmd_syncdb,
        "accept": cmd_accept,
        "autocast": cmd_autocast,
        "submit": cmd_submit,
        "roster": cmd_roster,
        "log": cmd_log,
        "nextday": cmd_nextday,
    }
    handler = mapping.get(action)
    if handler:
        await handler(update, context)


def build_game_app() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError("Missing BOT_TOKEN")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("newgame", cmd_newgame))
    app.add_handler(CommandHandler("mission", cmd_mission))
    app.add_handler(CommandHandler("dbmission", cmd_dbmission))
    app.add_handler(CommandHandler("syncdb", cmd_syncdb))
    app.add_handler(CommandHandler("accept", cmd_accept))
    app.add_handler(CommandHandler("autocast", cmd_autocast))
    app.add_handler(CommandHandler("submit", cmd_submit))
    app.add_handler(CommandHandler("roster", cmd_roster))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("log", cmd_log))
    app.add_handler(CommandHandler("nextday", cmd_nextday))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^g\|"))
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    app = build_game_app()
    log.info("Starting Studio Dub Tycoon bot")
    app.run_polling()


if __name__ == "__main__":
    main()
