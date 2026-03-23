from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote, unquote

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from telegram_game.db_integration import (
    auto_cast_db_mission,
    count_db_movie_candidates,
    get_db_board_snapshot,
    list_db_missions,
    load_db_mission_into_state,
    load_specific_db_mission_into_state,
    persist_mission_assignments,
    persist_submission_result,
    sync_state_with_db,
)
from telegram_game.game_engine import (
    accept_mission,
    assign_role,
    assign_translator,
    assigned_staff_members,
    auto_cast,
    bench_summary,
    client_summary,
    clear_assignments,
    current_team_summary,
    ensure_mission,
    equip_gear,
    fire_staff,
    hire_staff,
    latest_log,
    load_state,
    market_summary,
    mission_summary,
    new_game,
    next_day,
    reputation_summary,
    resolve_submission,
    rest_all_staff,
    rest_staff,
    roster_summary,
    save_state,
    staff_detail_summary,
    studio_summary,
    submission_risk_report,
    submission_risk_text,
    train_staff,
    upgrade_studio,
    goals_summary,
    gear_shop_summary,
    inventory_summary,
    buy_gear,
    unequip_gear,
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


def _persist_assignments_if_db(state, actor_name: str) -> Optional[dict]:
    mission = state.current_mission
    if not GAME_USE_DB or mission is None or mission.source != "database":
        return None
    try:
        return persist_mission_assignments(state, actor_name=actor_name)
    except Exception as exc:
        log.warning("DB assignment write-back failed: %s", exc)
        return None


def _persist_submission_if_db(mission, result: dict, actor_name: str) -> Optional[dict]:
    if not GAME_USE_DB or mission is None or mission.source != "database":
        return None
    try:
        return persist_submission_result(mission, result, actor_name=actor_name)
    except Exception as exc:
        log.warning("DB submission write-back failed: %s", exc)
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
        [InlineKeyboardButton("🏠 Home", callback_data="g|menu"), InlineKeyboardButton("🎬 Mission", callback_data="g|mission"), InlineKeyboardButton("📚 Missions", callback_data="g|missions")],
        [InlineKeyboardButton("🗂️ Board", callback_data="g|board"), InlineKeyboardButton("🧠 Assign UI", callback_data="g|assignui"), InlineKeyboardButton("👥 Team", callback_data="g|team")],
        [InlineKeyboardButton("✅ Accept", callback_data="g|accept"), InlineKeyboardButton("🤖 Auto Cast", callback_data="g|autocast"), InlineKeyboardButton("📤 Submit", callback_data="g|submit")],
        [InlineKeyboardButton("🏢 Studio", callback_data="g|studio"), InlineKeyboardButton("🛒 Market", callback_data="g|market"), InlineKeyboardButton("🤝 Clients", callback_data="g|clients")],
        [InlineKeyboardButton("👤 Roster", callback_data="g|roster"), InlineKeyboardButton("🪑 Bench", callback_data="g|bench"), InlineKeyboardButton("🏆 Goals", callback_data="g|goals")],
        [InlineKeyboardButton("🎒 Inventory", callback_data="g|inventory"), InlineKeyboardButton("🧰 Gear Shop", callback_data="g|gearshop"), InlineKeyboardButton("⭐ Rep", callback_data="g|reputation")],
        [InlineKeyboardButton("🛌 Rest All", callback_data="g|restall"), InlineKeyboardButton("📜 Log", callback_data="g|log"), InlineKeyboardButton("❓ Help", callback_data="g|help")],
        [InlineKeyboardButton("🔄 Sync DB", callback_data="g|syncdb"), InlineKeyboardButton("🗄️ DB Mission", callback_data="g|dbmission"), InlineKeyboardButton("⏭️ Next Day", callback_data="g|nextday")],
    ])


def _help_text() -> str:
    return """❓ Studio Dub Tycoon — command guide

Core flow:
1. /mission atau /missions
2. /accept
3. /assignui atau /autocast
4. /team
5. /submit

Main commands:
/menu — panel utama
/mission — mission semasa
/missions [status=...] [translator=...] [priority=...] [lang=...] [page=...]
/pick <code> — pilih mission DB
/board — ringkasan board
/assignui — assign dengan button
/team /bench /roster — lihat staff
/staff <nama> — kad staff detail
/train <nama> [balanced|skill|speed] — upgrade staff
/rest <nama> /restall — recover energy & burnout
/goals — achievement dan milestone
/market /hire /fire — recruitment
/inventory /gearshop — inventory dan shop
/buygear <item_key> — beli gear
/equip <staff> <item_key> /unequip <staff> — pasang gear staff
/studio /clients /reputation — studio panel
/syncdb /dbmission — DB sync tools
/log /nextday — progression"""


def _home_text(state) -> str:
    mission = _ensure_bot_mission(state)
    translator_count = len([member for member in state.roster if member.role_type == "translator"])
    vo_count = len([member for member in state.roster if member.role_type in {"male", "female"}])
    assigned_roles = sum(1 for role in mission.roles if mission.assigned_roles.get(role.role))
    lines = [
        "🎮 Studio Dub Tycoon",
        f"Studio: {state.studio_name}",
        f"Mode: {_mode_label()}",
        f"Day {state.day} | Coins {state.coins} | XP {state.xp} | Level {state.level()} | Rep {state.reputation}",
        f"Roster: {translator_count} translator · {vo_count} VO | Market {len(state.market)} | Goals {len(state.achievements)} | Inv {sum(state.inventory.values())}",
        "",
        "Current mission",
        f"- {mission.code} | {mission.title}",
        f"- Client {mission.client_name} [{mission.client_tier}] | {mission.lang.upper()} | {mission.priority}",
        f"- Modifiers: {', '.join(mission.modifiers) if mission.modifiers else '-'}",
        f"- Translator: {mission.assigned_translator or '-'}",
        f"- Roles filled: {assigned_roles}/{len(mission.roles)}",
        "",
        "Tap button bawah untuk cepat gerak. Guna /help kalau nak full guide.",
    ]
    return chr(10).join(lines)


def _parse_mission_filters(args: Optional[list[str]]) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str], int]:
    status: Optional[str] = None
    translator_parts: list[str] = []
    priority: Optional[str] = None
    lang: Optional[str] = None
    page: int = 1
    mode: Optional[str] = None

    for raw in (args or []):
        token = raw.strip()
        if not token:
            continue
        lower = token.lower()
        if lower.startswith("status="):
            status = token.split("=", 1)[1].strip() or None
            mode = None
            continue
        if lower.startswith("translator="):
            value = token.split("=", 1)[1].strip()
            translator_parts = [value] if value else []
            mode = "translator"
            continue
        if lower.startswith("priority="):
            priority = token.split("=", 1)[1].strip() or None
            mode = None
            continue
        if lower.startswith("lang="):
            lang = token.split("=", 1)[1].strip() or None
            mode = None
            continue
        if lower.startswith("page="):
            try:
                page = max(1, int(token.split("=", 1)[1].strip() or "1"))
            except ValueError:
                page = 1
            mode = None
            continue
        if lower == "translator":
            translator_parts = []
            mode = "translator"
            continue
        if lower == "status":
            mode = "status"
            continue
        if lower == "priority":
            mode = "priority"
            continue
        if lower == "lang":
            mode = "lang"
            continue
        if lower == "page":
            mode = "page"
            continue
        if mode == "translator":
            translator_parts.append(token)
        elif mode == "status" and status is None:
            status = token
        elif mode == "priority" and priority is None:
            priority = token
        elif mode == "lang" and lang is None:
            lang = token
        elif mode == "page":
            try:
                page = max(1, int(token))
            except ValueError:
                page = 1
        elif status is None and lower.replace("_", "").replace("-", "") in {"new", "pending", "inprogress", "completed", "ready", "active"}:
            status = token
        elif priority is None and lower in {"superurgent", "urgent", "nonurgent", "flexible", "su", "normal", "low"}:
            priority = token
        elif lang is None and len(lower) <= 5 and lower.replace("-", "").isalpha():
            lang = token
        else:
            translator_parts.append(token)

    translator = " ".join(part for part in translator_parts if part).strip() or None
    return status, translator, priority, lang, page


def _name_token(value: str) -> str:
    return quote((value or "").strip(), safe="")


def _name_from_token(value: str) -> str:
    return unquote(value or "").strip()


def _staff_rank_for_translator(member, mission) -> float:
    score = member.power() + member.energy * 0.22 - member.burnout * 0.95
    traits = set(member.traits or [])
    if mission.lang.lower() in {"bn", "ms", "en"} and "polyglot" in traits:
        score += 14
    if mission.priority in {"urgent", "superurgent"} and "sprinter" in traits:
        score += 10
    if "perfectionist" in traits:
        score += 8
    if "veteran" in traits:
        score += 6
    return round(score, 2)


def _staff_rank_for_role(member, mission, role) -> float:
    score = member.power() + member.energy * 0.18 - member.burnout * 0.9
    traits = set(member.traits or [])
    if mission.priority in {"urgent", "superurgent"} and "sprinter" in traits:
        score += 9
    if role.lines >= 90 and "workhorse" in traits:
        score += 10
    if "natural" in traits:
        score += 6
    if "charmer" in traits and role.gender == member.role_type:
        score += 4
    if "veteran" in traits:
        score += 5
    return round(score, 2)


def _top_translator_candidates(state, limit: int = 5):
    mission = _ensure_bot_mission(state)
    pool = [member for member in state.roster if member.role_type == "translator"]
    return sorted(pool, key=lambda member: (_staff_rank_for_translator(member, mission), member.level, member.name.lower()), reverse=True)[:limit]


def _top_role_candidates(state, role_name: str, limit: int = 5):
    mission = _ensure_bot_mission(state)
    role = next((item for item in mission.roles if item.role.lower() == role_name.lower()), None)
    if role is None:
        return []
    pool = [member for member in state.roster if member.role_type == role.gender]
    assigned = {member.name for member in assigned_staff_members(state, mission) if member.name != mission.assigned_roles.get(role.role)}
    ranked = sorted(
        pool,
        key=lambda member: (
            member.name not in assigned,
            _staff_rank_for_role(member, mission, role),
            member.level,
            member.name.lower(),
        ),
        reverse=True,
    )
    return ranked[:limit]


def _assign_ui_keyboard(state) -> InlineKeyboardMarkup:
    mission = _ensure_bot_mission(state)
    rows: list[list[InlineKeyboardButton]] = []
    tr_candidates = _top_translator_candidates(state, limit=3)
    if tr_candidates:
        rows.append([
            InlineKeyboardButton(f"TR {member.name}", callback_data=f"g|settr|{_name_token(member.name)}")
            for member in tr_candidates[:2]
        ])
        if len(tr_candidates) > 2:
            rows.append([
                InlineKeyboardButton(f"TR {tr_candidates[2].name}", callback_data=f"g|settr|{_name_token(tr_candidates[2].name)}")
            ])
    role_buttons = [
        InlineKeyboardButton(f"🎙 {role.role}", callback_data=f"g|pickrole|{_name_token(role.role)}")
        for role in mission.roles
    ]
    for idx in range(0, len(role_buttons), 2):
        rows.append(role_buttons[idx:idx+2])
    rows.append([
        InlineKeyboardButton("👥 Team", callback_data="g|team"),
        InlineKeyboardButton("📤 Submit", callback_data="g|submit"),
    ])
    return InlineKeyboardMarkup(rows)


def _role_picker_keyboard(state, role_name: str) -> InlineKeyboardMarkup:
    candidates = _top_role_candidates(state, role_name, limit=6)
    rows: list[list[InlineKeyboardButton]] = []
    for idx in range(0, len(candidates), 2):
        chunk = candidates[idx:idx+2]
        rows.append([
            InlineKeyboardButton(member.name, callback_data=f"g|setrole|{_name_token(role_name)}|{_name_token(member.name)}")
            for member in chunk
        ])
    rows.append([
        InlineKeyboardButton("⬅️ Assign UI", callback_data="g|assignui"),
        InlineKeyboardButton("👥 Team", callback_data="g|team"),
    ])
    return InlineKeyboardMarkup(rows)


def _submit_warning_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚠️ Proceed QA", callback_data="g|submitconfirm")],
        [InlineKeyboardButton("👥 Team", callback_data="g|team"), InlineKeyboardButton("⏭️ Next Day", callback_data="g|nextday")],
    ])


def _board_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("NEW", callback_data="g|missions|s=NEW|p=1"), InlineKeyboardButton("IN_PROGRESS", callback_data="g|missions|s=IN_PROGRESS|p=1")],
        [InlineKeyboardButton("READY", callback_data="g|missions|s=READY|p=1"), InlineKeyboardButton("COMPLETED", callback_data="g|missions|s=COMPLETED|p=1")],
        [InlineKeyboardButton("📚 All Missions", callback_data="g|missions")],
    ])


def _board_text(state) -> str:
    if not GAME_USE_DB:
        mission = _ensure_bot_mission(state)
        return "🗂️ Mission board (demo mode)" + chr(10) + chr(10) + mission_summary(mission)
    chunks = ["🗂️ Mission board snapshot", "Guna /missions untuk list penuh atau tap filter button bawah."]
    try:
        snapshot = get_db_board_snapshot(state, sample_limit=3)
    except Exception as exc:
        return f"❌ Tak dapat load board DB: {exc}"
    for status in ["NEW", "IN_PROGRESS", "READY", "COMPLETED"]:
        total = snapshot.get("counts", {}).get(status, 0)
        items = snapshot.get("items", {}).get(status, [])
        chunks.append("")
        chunks.append(f"{status} ({total})")
        if not items:
            chunks.append("- kosong")
            continue
        for item in items:
            chunks.append(
                f"- {item['code']} | {item['title']} | {item.get('lang', '-')} | {item.get('priority', '-')} | TR: {item.get('translator') or '-'}"
            )
    return chr(10).join(chunks)


def _assign_ui_text(state) -> str:
    mission = _ensure_bot_mission(state)
    tr = mission.assigned_translator or "-"
    lines = [
        f"🧠 Assign panel — {mission.code}",
        mission.title,
        f"Translator: {tr}",
        "Roles:",
    ]
    for role in mission.roles:
        lines.append(f"- {role.role}: {mission.assigned_roles.get(role.role, '-')} ({role.lines} lines)")
    lines.append("")
    lines.append("Tap translator shortcut atau pilih role untuk calon terbaik.")
    return chr(10).join(lines)


def _role_picker_text(state, role_name: str) -> str:
    mission = _ensure_bot_mission(state)
    role = next((item for item in mission.roles if item.role.lower() == role_name.lower()), None)
    if role is None:
        return f"❌ Role {role_name} tak jumpa."
    lines = [
        f"🎯 Pilih VO untuk {role.role}",
        f"Gender: {role.gender}",
        f"Lines: {role.lines}",
        f"Current: {mission.assigned_roles.get(role.role, '-')}",
        "",
        "Calon teratas:",
    ]
    for member in _top_role_candidates(state, role.role, limit=6):
        lines.append(f"- {member.name} | power {round(member.power(),1)} | energy {member.energy} | burnout {member.burnout}")
    return "\n".join(lines)


def _submit_result_text(state, result: dict, db_info: Optional[dict] = None) -> str:
    verdict = "🏆 QA LULUS" if result["passed"] else "💥 QA GAGAL"
    text = (
        f"{verdict}\n"
        f"Mission: {result['code']} — {result['title']}\n"
        f"Client: {result['client_name']} [{result['client_tier']}]\n"
        f"Score: {result['qa_score']} / {result['threshold']}\n"
        f"Reward: +{result['reward']} coins\n"
        f"XP: +{result['xp']}\n"
        f"Reputation: {result['rep_change']:+d} → {result['reputation']}\n"
        f"Studio coins sekarang: {state.coins}"
    )
    if db_info:
        text += f"\nDB write-back: {'COMPLETED' if db_info['passed'] else 'updated only'}, VO submissions +{db_info['vo_submissions_created']}"
    return text


def _pending_submit_warning_text(state) -> str:
    mission = _ensure_bot_mission(state)
    return submission_risk_text(state) + f"\n\nMission {mission.code} ada risiko. Proceed kalau kau memang nak terus QA sekarang."

def _mission_filter_tokens(
    status: Optional[str] = None,
    translator: Optional[str] = None,
    priority: Optional[str] = None,
    lang: Optional[str] = None,
    page: int = 1,
) -> list[str]:
    tokens: list[str] = []
    if status:
        tokens.append(f"status={status}")
    if translator:
        tokens.append(f"translator={translator}")
    if priority:
        tokens.append(f"priority={priority}")
    if lang:
        tokens.append(f"lang={lang}")
    if page > 1:
        tokens.append(f"page={page}")
    return tokens


def _missions_callback_payload(
    page: int,
    status: Optional[str] = None,
    translator: Optional[str] = None,
    priority: Optional[str] = None,
    lang: Optional[str] = None,
) -> str:
    parts = [f"p={max(1, page)}"]
    if status:
        parts.append(f"s={status}")
    if translator and len(translator) <= 18:
        parts.append(f"t={translator}")
    if priority:
        parts.append(f"r={priority}")
    if lang:
        parts.append(f"l={lang}")
    return "g|missions|" + ";".join(parts)


def _parse_missions_callback(payload: str) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str], int]:
    status = None
    translator = None
    priority = None
    lang = None
    page = 1
    for part in (payload or "").split(";"):
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        value = value.strip() or None
        if key == "p":
            try:
                page = max(1, int(value or "1"))
            except ValueError:
                page = 1
        elif key == "s":
            status = value
        elif key == "t":
            translator = value
        elif key == "r":
            priority = value
        elif key == "l":
            lang = value
    return status, translator, priority, lang, page


def _mission_pick_keyboard(
    items: list[dict],
    page: int = 1,
    total_pages: int = 1,
    status: Optional[str] = None,
    translator: Optional[str] = None,
    priority: Optional[str] = None,
    lang: Optional[str] = None,
) -> InlineKeyboardMarkup:
    rows = []
    for item in items[:8]:
        code = str(item["code"])
        title = str(item["title"])
        label = f"🎯 {code}"
        if len(title) <= 18:
            label += f" · {title}"
        rows.append([InlineKeyboardButton(label, callback_data=f"g|pick|{code}")])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=_missions_callback_payload(page - 1, status, translator, priority, lang)))
    if page < total_pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=_missions_callback_payload(page + 1, status, translator, priority, lang)))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Menu", callback_data="g|mission")])
    return InlineKeyboardMarkup(rows)


def _missions_text(
    items: list[dict],
    status: Optional[str] = None,
    translator: Optional[str] = None,
    priority: Optional[str] = None,
    lang: Optional[str] = None,
    page: int = 1,
    total_pages: int = 1,
    total: Optional[int] = None,
) -> str:
    filters = []
    if status:
        filters.append(f"status={status}")
    if translator:
        filters.append(f"translator={translator}")
    if priority:
        filters.append(f"priority={priority}")
    if lang:
        filters.append(f"lang={lang}")
    header = ["📚 DB mission list"]
    if filters:
        header.append(f"Filter: {', '.join(filters)}")
    suffix = f" — total {total}" if total is not None else ""
    header.append(f"Page {page}/{max(1, total_pages)}{suffix}")
    if not items:
        return chr(10).join(header + ["", "- Tiada mission jumpa."])
    lines = header + [""]
    for item in items:
        lines.append(
            f"- {item['code']} | {item['title']}" + chr(10) + f"  {item.get('lang', '-')} | {item.get('priority', '-')} | {item.get('status', '-')} | TR: {item.get('translator') or '-'}"
        )
    return chr(10).join(lines)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _save(state)
    await update.effective_message.reply_text(_home_text(state), reply_markup=_menu())


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _save(state)
    await update.effective_message.reply_text(_home_text(state), reply_markup=_menu())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _save(state)
    await update.effective_message.reply_text(_help_text(), reply_markup=_menu())


async def cmd_newgame(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    studio_name = " ".join(context.args).strip() or "Studio Baru"
    state = new_game(update.effective_user.id, studio_name)
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


async def cmd_missions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    status, translator, priority, lang, page = _parse_mission_filters(getattr(context, "args", None))
    try:
        payload = list_db_missions(
            state,
            limit=8,
            status=status,
            translator=translator,
            priority=priority,
            lang=lang,
            page=page,
            include_meta=True,
        )
        items = payload["items"]
        text = _missions_text(
            items,
            status=status,
            translator=translator,
            priority=priority,
            lang=lang,
            page=payload["page"],
            total_pages=payload["total_pages"],
            total=payload["total"],
        )
        markup = _mission_pick_keyboard(
            items,
            page=payload["page"],
            total_pages=payload["total_pages"],
            status=status,
            translator=translator,
            priority=priority,
            lang=lang,
        ) if items or payload["has_prev"] or payload["has_next"] else _menu()
    except Exception as exc:
        text = f"❌ Tak dapat ambil list mission DB: {exc}"
        markup = _menu()
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=markup)


async def cmd_board(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _ensure_bot_mission(state)
    _save(state)
    await update.effective_message.reply_text(_board_text(state), reply_markup=_board_keyboard())


async def cmd_assignui(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _ensure_bot_mission(state)
    _save(state)
    await update.effective_message.reply_text(_assign_ui_text(state), reply_markup=_assign_ui_keyboard(state))


async def cmd_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    code = " ".join(context.args).strip()
    if not code:
        text = "Usage: /pick <movie_code>"
    else:
        try:
            mission = load_specific_db_mission_into_state(state, code)
            if mission is None:
                text = f"❌ Mission {code} tak jumpa dalam DB."
            else:
                text = f"🎯 Mission dipilih dari DB\n\n{mission_summary(mission)}"
        except Exception as exc:
            text = f"❌ Pick mission gagal: {exc}"
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
    try:
        if GAME_USE_DB and mission.source == "database":
            picks = auto_cast_db_mission(state)
        else:
            picks = auto_cast(state)
        db_info = _persist_assignments_if_db(state, actor_name=update.effective_user.first_name or "player")
        pretty = "\n".join(f"- {k}: {v}" for k, v in picks.items()) if picks else "- Tiada cast tersedia"
        extra = ""
        if db_info:
            extra = f"\n\nDB synced: task #{db_info['translation_task_id']}, assignments +{db_info['assignment_created']} created"
        text = f"🤖 Auto cast siap untuk {mission.code}:\n{pretty}{extra}"
    except Exception as exc:
        text = f"❌ Auto cast gagal: {exc}"
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_menu())


async def cmd_assigntr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _ensure_bot_mission(state)
    name = " ".join(context.args).strip()
    if not name:
        text = "Usage: /assigntr <nama translator>"
    else:
        try:
            assigned = assign_translator(state, name)
            db_info = _persist_assignments_if_db(state, actor_name=update.effective_user.first_name or "player")
            text = f"📝 Translator assigned: {assigned}"
            if db_info:
                text += f"\nDB task synced: #{db_info['translation_task_id']}"
        except Exception as exc:
            text = f"❌ {exc}"
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_menu())


async def cmd_assign(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _ensure_bot_mission(state)
    if len(context.args) < 2:
        text = "Usage: /assign <role> <nama staff>"
    else:
        role_name = context.args[0].strip()
        staff_name = " ".join(context.args[1:]).strip()
        try:
            assigned = assign_role(state, role_name, staff_name)
            db_info = _persist_assignments_if_db(state, actor_name=update.effective_user.first_name or "player")
            text = f"🎙️ Role {role_name} → {assigned}"
            if db_info:
                text += "\nDB assignments synced."
        except Exception as exc:
            text = f"❌ {exc}"
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_menu())


async def cmd_clearcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    mission = clear_assignments(state)
    db_info = _persist_assignments_if_db(state, actor_name=update.effective_user.first_name or "player")
    _save(state)
    text = f"🧹 Assignment dibersihkan untuk {mission.code}"
    if db_info:
        text += "\nDB assignment state disync semula."
    await update.effective_message.reply_text(text, reply_markup=_menu())


async def _finalize_submit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    mission_before = state.current_mission
    try:
        result = resolve_submission(state)
        db_info = _persist_submission_if_db(mission_before, result, actor_name=update.effective_user.first_name or "player")
        text = _submit_result_text(state, result, db_info=db_info)
    except Exception as exc:
        text = f"❌ {exc}"
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_menu())


async def cmd_submit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _ensure_bot_mission(state)
    report = submission_risk_report(state)
    if report["blockers"]:
        _save(state)
        await update.effective_message.reply_text("❌ " + submission_risk_text(state), reply_markup=_menu())
        return
    if report["has_warning"]:
        _save(state)
        await update.effective_message.reply_text(_pending_submit_warning_text(state), reply_markup=_submit_warning_keyboard())
        return
    await _finalize_submit(update, context)


async def cmd_roster(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    await update.effective_message.reply_text(roster_summary(state), reply_markup=_menu())


async def cmd_team(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _ensure_bot_mission(state)
    _save(state)
    await update.effective_message.reply_text(current_team_summary(state), reply_markup=_menu())


async def cmd_bench(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _ensure_bot_mission(state)
    _save(state)
    await update.effective_message.reply_text(bench_summary(state), reply_markup=_menu())


async def cmd_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _save(state)
    await update.effective_message.reply_text(market_summary(state), reply_markup=_menu())


async def cmd_hire(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    name = " ".join(context.args).strip()
    if not name:
        text = "Usage: /hire <nama staff dalam market>"
    else:
        try:
            member = hire_staff(state, name)
            text = (
                f"✅ Hire berjaya: {member.name}\n"
                f"Role: {member.role_type}\n"
                f"Rarity: {member.rarity}\n"
                f"Traits: {', '.join(member.traits) if member.traits else '-'}\n"
                f"Hire cost: {member.hire_cost}\n"
                f"Salary/day: {member.salary}\n"
                f"Coins sekarang: {state.coins}"
            )
        except Exception as exc:
            text = f"❌ {exc}"
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_menu())


async def cmd_fire(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    name = " ".join(context.args).strip()
    if not name:
        text = "Usage: /fire <nama staff>"
    else:
        try:
            member = fire_staff(state, name)
            text = f"🗑️ Staff dibuang: {member.name} [{member.role_type}]"
        except Exception as exc:
            text = f"❌ {exc}"
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_menu())


async def cmd_studio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _save(state)
    await update.effective_message.reply_text(studio_summary(state), reply_markup=_menu())


async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    target = " ".join(context.args).strip().lower()
    if not target:
        text = "Usage: /upgrade <studio|translator|vo|lounge>"
    else:
        try:
            info = upgrade_studio(state, target)
            text = f"⬆️ Upgrade berjaya: {info['target']} → lvl {info['level']} (cost {info['cost']})"
        except Exception as exc:
            text = f"❌ {exc}"
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_menu())


async def cmd_staff(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    name = " ".join(context.args).strip()
    if not name:
        text = "Usage: /staff <nama staff>"
    else:
        try:
            text = staff_detail_summary(state, name)
        except Exception as exc:
            text = f"❌ {exc}"
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_menu())


async def cmd_train(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    args = list(context.args or [])
    focus = "balanced"
    if args and args[-1].lower() in {"balanced", "skill", "speed"}:
        focus = args.pop(-1).lower()
    name = " ".join(args).strip()
    if not name:
        text = "Usage: /train <nama staff> [balanced|skill|speed]"
    else:
        try:
            info = train_staff(state, name, focus=focus)
            member = info["member"]
            unlocked = info.get("unlocked") or []
            text = (
                f"🏋️ Training siap untuk {member.name}\n"
                f"Focus: {info['focus']} | Cost: {info['cost']}\n"
                f"Skill {member.skill} | Speed {member.speed} | Level {member.level}\n"
                f"Energy {member.energy} | Burnout {member.burnout}"
            )
            if info.get("level_up"):
                text += "\n✨ Level up!"
            if unlocked:
                text += "\n🏆 Unlock: " + ", ".join(unlocked)
        except Exception as exc:
            text = f"❌ {exc}"
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_menu())


async def cmd_rest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    name = " ".join(context.args).strip()
    if not name:
        text = "Usage: /rest <nama staff>"
    else:
        try:
            info = rest_staff(state, name)
            member = info["member"]
            unlocked = info.get("unlocked") or []
            text = (
                f"🛌 Rest siap untuk {member.name}\n"
                f"Cost: {info['cost']}\n"
                f"Energy +{info['energy_recovered']} | Burnout -{info['burnout_reduced']}\n"
                f"Energy now {member.energy} | Burnout now {member.burnout}"
            )
            if unlocked:
                text += "\n🏆 Unlock: " + ", ".join(unlocked)
        except Exception as exc:
            text = f"❌ {exc}"
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_menu())


async def cmd_restall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    try:
        info = rest_all_staff(state)
        unlocked = info.get("unlocked") or []
        text = (
            f"🛌 Company rest day selesai\n"
            f"Cost: {info['cost']}\n"
            f"Total energy +{info['energy_recovered']} | Burnout -{info['burnout_reduced']}"
        )
        if unlocked:
            text += "\n🏆 Unlock: " + ", ".join(unlocked)
    except Exception as exc:
        text = f"❌ {exc}"
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_menu())


async def cmd_inventory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _save(state)
    await update.effective_message.reply_text(inventory_summary(state), reply_markup=_menu())


async def cmd_gearshop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _save(state)
    await update.effective_message.reply_text(gear_shop_summary(state), reply_markup=_menu())


async def cmd_buygear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    item_key = " ".join(context.args).strip()
    if not item_key:
        text = "Usage: /buygear <item_key>"
    else:
        try:
            info = buy_gear(state, item_key)
            text = f"🧰 Gear dibeli: {info['label']}\nCost: {info['cost']}\nQty sekarang: {info['qty']}"
        except Exception as exc:
            text = f"❌ {exc}"
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_menu())


async def cmd_equip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    if len(context.args) < 2:
        text = "Usage: /equip <nama staff> <item_key>"
    else:
        staff_name = " ".join(context.args[:-1]).strip()
        item_key = context.args[-1].strip()
        try:
            info = equip_gear(state, staff_name, item_key)
            text = f"🎯 {info['member'].name} equip {info['label']}"
            if info.get('previous'):
                text += f"\nPrevious returned to inventory: {info['previous']}"
        except Exception as exc:
            text = f"❌ {exc}"
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_menu())


async def cmd_unequip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    staff_name = " ".join(context.args).strip()
    if not staff_name:
        text = "Usage: /unequip <nama staff>"
    else:
        try:
            info = unequip_gear(state, staff_name)
            text = f"🎒 {info['member'].name} unequip {info['label']}"
        except Exception as exc:
            text = f"❌ {exc}"
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_menu())


async def cmd_goals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _save(state)
    await update.effective_message.reply_text(goals_summary(state), reply_markup=_menu())


async def cmd_clients(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _ensure_bot_mission(state)
    _save(state)
    await update.effective_message.reply_text(client_summary(state), reply_markup=_menu())


async def cmd_reputation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _save(state)
    await update.effective_message.reply_text(reputation_summary(state), reply_markup=_menu())


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _ensure_bot_mission(state)
    _save(state)
    await update.effective_message.reply_text(_home_text(state), reply_markup=_menu())


async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    await update.effective_message.reply_text(f"📜 Log studio\n{latest_log(state)}", reply_markup=_menu())


async def cmd_nextday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    next_day(state)
    _save(state)
    await update.effective_message.reply_text(f"⏭️ Masuk hari {state.day}. Market dan payroll dah update. Reputation: {state.reputation}", reply_markup=_menu())


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    raw = query.data or ""
    parts = raw.split("|")

    if len(parts) >= 3 and parts[1] == "pick":
        context.args = ["|".join(parts[2:])]  # type: ignore[attr-defined]
        await cmd_pick(update, context)
        return

    if len(parts) >= 3 and parts[1] == "missions":
        status, translator, priority, lang, page = _parse_missions_callback("|".join(parts[2:]))
        context.args = _mission_filter_tokens(status, translator, priority, lang, page)  # type: ignore[attr-defined]
        await cmd_missions(update, context)
        return

    if len(parts) >= 3 and parts[1] == "settr":
        state = _load_or_create(update.effective_user.id)
        _ensure_bot_mission(state)
        name = _name_from_token(parts[2])
        try:
            assigned = assign_translator(state, name)
            db_info = _persist_assignments_if_db(state, actor_name=update.effective_user.first_name or "player")
            text = f"📝 Translator assigned: {assigned}"
            if db_info:
                text += f"\nDB task synced: #{db_info['translation_task_id']}"
        except Exception as exc:
            text = f"❌ {exc}"
        _save(state)
        await update.effective_message.reply_text(text, reply_markup=_assign_ui_keyboard(state))
        return

    if len(parts) >= 3 and parts[1] == "pickrole":
        state = _load_or_create(update.effective_user.id)
        _ensure_bot_mission(state)
        role_name = _name_from_token(parts[2])
        _save(state)
        await update.effective_message.reply_text(_role_picker_text(state, role_name), reply_markup=_role_picker_keyboard(state, role_name))
        return

    if len(parts) >= 4 and parts[1] == "setrole":
        state = _load_or_create(update.effective_user.id)
        _ensure_bot_mission(state)
        role_name = _name_from_token(parts[2])
        staff_name = _name_from_token(parts[3])
        try:
            assigned = assign_role(state, role_name, staff_name)
            db_info = _persist_assignments_if_db(state, actor_name=update.effective_user.first_name or "player")
            text = f"🎙️ Role {role_name} → {assigned}"
            if db_info:
                text += "\nDB assignments synced."
        except Exception as exc:
            text = f"❌ {exc}"
        _save(state)
        await update.effective_message.reply_text(text, reply_markup=_assign_ui_keyboard(state))
        return

    if len(parts) >= 2 and parts[1] == "submitconfirm":
        await _finalize_submit(update, context)
        return

    action = parts[1] if len(parts) > 1 else ""
    mapping = {
        "menu": cmd_menu,
        "help": cmd_help,
        "mission": cmd_mission,
        "dbmission": cmd_dbmission,
        "missions": cmd_missions,
        "board": cmd_board,
        "assignui": cmd_assignui,
        "syncdb": cmd_syncdb,
        "accept": cmd_accept,
        "autocast": cmd_autocast,
        "submit": cmd_submit,
        "roster": cmd_roster,
        "team": cmd_team,
        "bench": cmd_bench,
        "market": cmd_market,
        "studio": cmd_studio,
        "clients": cmd_clients,
        "reputation": cmd_reputation,
        "goals": cmd_goals,
        "inventory": cmd_inventory,
        "gearshop": cmd_gearshop,
        "restall": cmd_restall,
        "log": cmd_log,
        "nextday": cmd_nextday,
    }
    handler = mapping.get(action)
    if handler:
        await handler(update, context)

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Telegram handler error", exc_info=context.error)


def build_game_app(token: Optional[str] = None) -> Application:
    bot_token = (token or BOT_TOKEN or "").strip()
    if not bot_token:
        raise RuntimeError("Missing BOT_TOKEN")
    app = Application.builder().token(bot_token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("newgame", cmd_newgame))
    app.add_handler(CommandHandler("mission", cmd_mission))
    app.add_handler(CommandHandler("dbmission", cmd_dbmission))
    app.add_handler(CommandHandler("missions", cmd_missions))
    app.add_handler(CommandHandler("board", cmd_board))
    app.add_handler(CommandHandler("assignui", cmd_assignui))
    app.add_handler(CommandHandler("pick", cmd_pick))
    app.add_handler(CommandHandler("syncdb", cmd_syncdb))
    app.add_handler(CommandHandler("accept", cmd_accept))
    app.add_handler(CommandHandler("autocast", cmd_autocast))
    app.add_handler(CommandHandler("assigntr", cmd_assigntr))
    app.add_handler(CommandHandler("assign", cmd_assign))
    app.add_handler(CommandHandler("clearcast", cmd_clearcast))
    app.add_handler(CommandHandler("submit", cmd_submit))
    app.add_handler(CommandHandler("roster", cmd_roster))
    app.add_handler(CommandHandler("staff", cmd_staff))
    app.add_handler(CommandHandler("team", cmd_team))
    app.add_handler(CommandHandler("bench", cmd_bench))
    app.add_handler(CommandHandler("market", cmd_market))
    app.add_handler(CommandHandler("hire", cmd_hire))
    app.add_handler(CommandHandler("fire", cmd_fire))
    app.add_handler(CommandHandler("train", cmd_train))
    app.add_handler(CommandHandler("rest", cmd_rest))
    app.add_handler(CommandHandler("restall", cmd_restall))
    app.add_handler(CommandHandler("inventory", cmd_inventory))
    app.add_handler(CommandHandler("gearshop", cmd_gearshop))
    app.add_handler(CommandHandler("buygear", cmd_buygear))
    app.add_handler(CommandHandler("equip", cmd_equip))
    app.add_handler(CommandHandler("unequip", cmd_unequip))
    app.add_handler(CommandHandler("studio", cmd_studio))
    app.add_handler(CommandHandler("clients", cmd_clients))
    app.add_handler(CommandHandler("reputation", cmd_reputation))
    app.add_handler(CommandHandler("goals", cmd_goals))
    app.add_handler(CommandHandler("upgrade", cmd_upgrade))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("log", cmd_log))
    app.add_handler(CommandHandler("nextday", cmd_nextday))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^g\|"))
    app.add_error_handler(on_error)
    return app


def build_game_application(token: Optional[str] = None) -> Application:
    return build_game_app(token=token)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    app = build_game_app()
    log.info("Starting Studio Dub Tycoon bot")
    app.run_polling()


if __name__ == "__main__":
    main()
