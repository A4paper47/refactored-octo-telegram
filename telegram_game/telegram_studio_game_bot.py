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
    EQUIPMENT_CATALOG,
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
        [InlineKeyboardButton("🏠 Home", callback_data="g|menu"), InlineKeyboardButton("🎬 Mission", callback_data="g|mission"), InlineKeyboardButton("🗃️ Mission UI", callback_data="g|missionsui|1")],
        [InlineKeyboardButton("📚 Missions", callback_data="g|missions"), InlineKeyboardButton("🗂️ Board", callback_data="g|board"), InlineKeyboardButton("🧠 Assign UI", callback_data="g|assignui")],
        [InlineKeyboardButton("✅ Accept", callback_data="g|accept"), InlineKeyboardButton("🤖 Auto Cast", callback_data="g|autocast"), InlineKeyboardButton("📤 Submit", callback_data="g|submit")],
        [InlineKeyboardButton("👥 Team", callback_data="g|team"), InlineKeyboardButton("👤 Roster UI", callback_data="g|rosterui"), InlineKeyboardButton("🪑 Bench", callback_data="g|bench")],
        [InlineKeyboardButton("🏢 Studio", callback_data="g|studio"), InlineKeyboardButton("🛒 Market", callback_data="g|market"), InlineKeyboardButton("🤝 Clients", callback_data="g|clients")],
        [InlineKeyboardButton("🎒 Inventory", callback_data="g|inventory"), InlineKeyboardButton("🧩 Gear UI", callback_data="g|gearui"), InlineKeyboardButton("🧰 Gear Shop", callback_data="g|gearshop")],
        [InlineKeyboardButton("🏆 Goals", callback_data="g|goals"), InlineKeyboardButton("⭐ Rep", callback_data="g|reputation"), InlineKeyboardButton("📜 Log", callback_data="g|log")],
        [InlineKeyboardButton("🛌 Rest All", callback_data="g|restall"), InlineKeyboardButton("🔄 Sync DB", callback_data="g|syncdb"), InlineKeyboardButton("🗄️ DB Mission", callback_data="g|dbmission")],
        [InlineKeyboardButton("⏭️ Next Day", callback_data="g|nextday"), InlineKeyboardButton("❓ Help", callback_data="g|help")],
    ])


def _help_text() -> str:
    return """❓ Studio Dub Tycoon — command guide

Recommended mission flow:
1. /mission or /missions
2. /accept
3. /assignui or /autocast
4. /team
5. /submit

Core controls:
/menu — main control panel
/mission — active mission summary
/missions [status=...] [translator=...] [priority=...] [lang=...] [page=...]
/missionsui [page] — paged mission browser with inline pick actions
/pick <code> — load a DB mission
/board — board snapshot
/assignui — assign with buttons
/assignpreset <recommended|lang|workload|trait> — smart auto-fill using a preset
Preset buttons are also available directly inside the mission flow and selected mission cards
/team /bench /roster — staff overview
/rosterui [page] — paged staff browser
/staff <name> — staff profile
/train <name> [balanced|skill|speed] — improve a staff member
/rest <name> /restall — recover energy and reduce burnout
/goals — achievements and milestones
/market /hire /fire — recruitment tools
/inventory /gearshop /gearui — inventory, shop, and gear actions
/buygear <item_key> — buy gear
/equip <staff> <item_key> /unequip <staff> — manage equipment
/studio /clients /reputation — studio overview
/syncdb /dbmission — database sync tools
/log /nextday — progression and daily cycle"""


def _home_text(state) -> str:
    mission = _ensure_bot_mission(state)
    translator_count = len([member for member in state.roster if member.role_type == "translator"])
    vo_count = len([member for member in state.roster if member.role_type in {"male", "female"}])
    assigned_roles = sum(1 for role in mission.roles if mission.assigned_roles.get(role.role))
    lines = [
        "🎮 Studio Dub Tycoon",
        f"Studio: {state.studio_name}",
        f"Mode: {_mode_label()}",
        f"Day {state.day} | Coins {state.coins} | XP {state.xp} | Level {state.level()} | Reputation {state.reputation}",
        f"Roster: {translator_count} translators · {vo_count} VO | Market {len(state.market)} | Goals {len(state.achievements)} | Inventory {sum(state.inventory.values())}",
        "",
        "Active mission",
        f"- {mission.code} | {mission.title}",
        f"- Client {mission.client_name} [{mission.client_tier}] | {mission.lang.upper()} | {mission.priority}",
        f"- Modifiers: {', '.join(mission.modifiers) if mission.modifiers else '-'}",
        f"- Translator: {mission.assigned_translator or '-'}",
        f"- Roles filled: {assigned_roles}/{len(mission.roles)}",
        "",
        "Use the buttons below for the fastest flow. Run /help for the full guide. Use /missionsui for paged mission picking, /rosterui for paged staff browsing, and the preset buttons on each mission card for one-tap smart casting.",
    ]
    return chr(10).join(lines)


def _recommended_preset_hint(mission) -> str:
    priority = (mission.priority or '').lower()
    lang = (mission.lang or '').lower()
    tier = (mission.client_tier or '').lower()
    modifiers = {str(item).lower() for item in (mission.modifiers or [])}
    if priority in {'urgent', 'superurgent'} or {'rush_rewrite', 'overnight_push', 'tight_deadline'} & modifiers:
        return 'workload'
    if lang not in {'en', 'ms', '-'} or {'glossary_lock', 'localized_terms', 'sub_style_lock'} & modifiers:
        return 'lang'
    if tier in {'broadcast', 'premium', 'enterprise'} or {'premium_notes', 'lip_sync_heavy'} & modifiers:
        return 'trait'
    return 'recommended'


def _mission_card_text(state, mission, heading: str = '🎬 Mission card') -> str:
    assigned_roles = sum(1 for role in mission.roles if mission.assigned_roles.get(role.role))
    total_roles = len(mission.roles)
    translator = mission.assigned_translator or '-'
    preset_hint = _recommended_preset_hint(mission)
    modifiers = ', '.join(str(item) for item in (mission.modifiers or [])[:3]) if mission.modifiers else '-'
    role_preview = []
    for role in mission.roles[:4]:
        role_preview.append(f"{role.role}:{mission.assigned_roles.get(role.role) or '-'}")
    if len(mission.roles) > 4:
        role_preview.append('…')
    role_line = ', '.join(role_preview) if role_preview else '-'
    return "\n".join([
        heading,
        f"{mission.code} · {mission.title} ({mission.year})",
        f"{mission.client_name} [{mission.client_tier}] · {mission.lang.upper()} · {mission.priority} · {mission.source}",
        f"Reward {mission.reward}c · XP {mission.xp} · Rep +{mission.reputation_reward} · Deadline day {mission.deadline_day}",
        f"Cast {assigned_roles}/{total_roles} · TR {translator} · Preset {preset_hint}",
        f"Mods: {modifiers}",
        f"Roles: {role_line}",
    ])


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


ASSIGN_PRESET_ALIASES = {
    "rec": "recommended",
    "recommended": "recommended",
    "smart": "recommended",
    "lang": "lang",
    "language": "lang",
    "load": "workload",
    "workload": "workload",
    "trait": "trait",
    "traits": "trait",
}


def _normalize_assign_preset(value: Optional[str]) -> str:
    raw = (value or "recommended").strip().lower()
    return ASSIGN_PRESET_ALIASES.get(raw, "recommended")


def _recommended_assign_preset(state) -> str:
    mission = _ensure_bot_mission(state)
    modifiers = {str(item).strip().lower() for item in (mission.modifiers or []) if str(item).strip()}
    priority = (mission.priority or "").strip().lower()
    if priority in {"urgent", "superurgent"} or {"rush_rewrite", "overnight_push", "tight_deadline"} & modifiers:
        return "workload"
    if (mission.lang or "").strip().lower() not in {"en", "ms"} or {"glossary_lock", "sub_style_lock", "localized_terms"} & modifiers:
        return "lang"
    if mission.client_tier in {"premium", "enterprise", "broadcast"} or {"premium_notes", "lip_sync_heavy"} & modifiers:
        return "trait"
    return "trait"


def _effective_assign_preset(state, preset: Optional[str]) -> str:
    normalized = _normalize_assign_preset(preset)
    return _recommended_assign_preset(state) if normalized == "recommended" else normalized


def _preset_caption(state, preset: Optional[str]) -> str:
    normalized = _normalize_assign_preset(preset)
    effective = _effective_assign_preset(state, normalized)
    if normalized == "recommended":
        return f"recommended → {effective}"
    return effective


def _translator_preset_bonus(member, mission, preset: str) -> float:
    preset = _normalize_assign_preset(preset)
    traits = set(member.traits or [])
    priority = (mission.priority or "").strip().lower()
    modifiers = {str(item).strip().lower() for item in (mission.modifiers or []) if str(item).strip()}
    lang = (mission.lang or "").strip().lower()
    bonus = 0.0
    if preset == "lang":
        if "polyglot" in traits:
            bonus += 24.0 if lang not in {"en", "ms"} else 14.0
        if "perfectionist" in traits:
            bonus += 6.0
        if "glossary_lock" in modifiers:
            bonus += 8.0
    elif preset == "workload":
        bonus += member.energy * 0.42
        bonus -= member.burnout * 1.35
        if "resilient" in traits:
            bonus += 9.0
        if priority in {"urgent", "superurgent"} and "sprinter" in traits:
            bonus += 8.0
    elif preset == "trait":
        if "perfectionist" in traits:
            bonus += 15.0
        if "veteran" in traits:
            bonus += 10.0
        if "polyglot" in traits:
            bonus += 8.0
        if "resilient" in traits:
            bonus += 6.0
    return round(bonus, 2)


def _role_preset_bonus(member, mission, role, preset: str) -> float:
    preset = _normalize_assign_preset(preset)
    traits = set(member.traits or [])
    priority = (mission.priority or "").strip().lower()
    modifiers = {str(item).strip().lower() for item in (mission.modifiers or []) if str(item).strip()}
    bonus = 0.0
    if preset == "lang":
        if "natural" in traits:
            bonus += 12.0
        if "charmer" in traits and role.gender == member.role_type:
            bonus += 8.0
        if "lip_sync_heavy" in modifiers:
            bonus += 8.0
    elif preset == "workload":
        bonus += member.energy * 0.34
        bonus -= member.burnout * 1.28
        if "resilient" in traits:
            bonus += 9.0
        if role.lines >= 90 and "workhorse" in traits:
            bonus += 10.0
        if priority in {"urgent", "superurgent"} and "sprinter" in traits:
            bonus += 8.0
    elif preset == "trait":
        if "natural" in traits:
            bonus += 12.0
        if "veteran" in traits:
            bonus += 10.0
        if "workhorse" in traits:
            bonus += 8.0
        if "charmer" in traits:
            bonus += 7.0
    return round(bonus, 2)


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


def _paginate_items(items, page: int = 1, per_page: int = 4):
    total = len(items)
    total_pages = max(1, (total + per_page - 1) // per_page)
    safe_page = max(1, min(page, total_pages))
    start = (safe_page - 1) * per_page
    return items[start:start + per_page], safe_page, total_pages, total


def _all_translator_candidates(state, preset: str = "recommended"):
    mission = _ensure_bot_mission(state)
    effective = _effective_assign_preset(state, preset)
    pool = [member for member in state.roster if member.role_type == "translator"]
    return sorted(
        pool,
        key=lambda member: (
            _staff_rank_for_translator(member, mission) + _translator_preset_bonus(member, mission, effective),
            member.level,
            member.name.lower(),
        ),
        reverse=True,
    )


def _normalize_tr_filter(value: Optional[str]) -> str:
    raw = (value or "all").strip().lower()
    return raw if raw in {"all", "fresh", "calm"} else "all"


def _normalize_role_gender_filter(value: Optional[str]) -> str:
    raw = (value or "all").strip().lower()
    return raw if raw in {"all", "male", "female"} else "all"


def _normalize_energy_filter(value: Optional[str]) -> str:
    raw = (value or "all").strip().lower()
    return raw if raw in {"all", "fresh", "tired"} else "all"


def _apply_translator_filter(candidates, filter_name: str):
    filter_name = _normalize_tr_filter(filter_name)
    if filter_name == "fresh":
        filtered = [member for member in candidates if member.energy >= 70]
        return filtered or candidates
    if filter_name == "calm":
        filtered = [member for member in candidates if member.burnout <= 35]
        return filtered or candidates
    return candidates


def _apply_role_list_filter(roles, gender_filter: str):
    gender_filter = _normalize_role_gender_filter(gender_filter)
    if gender_filter == "all":
        return roles
    filtered = [role for role in roles if (role.gender or "").lower() == gender_filter]
    return filtered or roles


def _apply_role_energy_filter(candidates, energy_filter: str):
    energy_filter = _normalize_energy_filter(energy_filter)
    if energy_filter == "fresh":
        filtered = [member for member in candidates if member.energy >= 70]
        return filtered or candidates
    if energy_filter == "tired":
        filtered = [member for member in candidates if member.energy < 70]
        return filtered or candidates
    return candidates


def _safe_all_translator_candidates(state, preset: str = "recommended"):
    try:
        return _all_translator_candidates(state, preset=preset)
    except TypeError:
        return _all_translator_candidates(state)


def _safe_all_role_candidates(state, role_name: str, energy_filter: str = "all", preset: str = "recommended"):
    try:
        return _all_role_candidates(state, role_name, energy_filter=energy_filter, preset=preset)
    except TypeError:
        return _all_role_candidates(state, role_name, energy_filter=energy_filter)


def _all_role_candidates(state, role_name: str, energy_filter: str = "all", preset: str = "recommended"):
    mission = _ensure_bot_mission(state)
    role = next((item for item in mission.roles if item.role.lower() == role_name.lower()), None)
    if role is None:
        return []
    effective = _effective_assign_preset(state, preset)
    pool = [member for member in state.roster if member.role_type == role.gender]
    assigned = {member.name for member in assigned_staff_members(state, mission) if member.name != mission.assigned_roles.get(role.role)}
    ranked = sorted(
        pool,
        key=lambda member: (
            member.name not in assigned,
            _staff_rank_for_role(member, mission, role) + _role_preset_bonus(member, mission, role, effective),
            member.level,
            member.name.lower(),
        ),
        reverse=True,
    )
    return _apply_role_energy_filter(ranked, energy_filter)

def _selected_mission_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Accept", callback_data="g|accept"), InlineKeyboardButton("🧠 Assign UI", callback_data="g|assignui"), InlineKeyboardButton("🤖 Auto Cast", callback_data="g|autocast")],
        [InlineKeyboardButton("🪄 Rec", callback_data="g|presetapply|recommended"), InlineKeyboardButton("🌐 Lang", callback_data="g|presetapply|lang")],
        [InlineKeyboardButton("⚙️ Load", callback_data="g|presetapply|workload"), InlineKeyboardButton("✨ Trait", callback_data="g|presetapply|trait")],
        [InlineKeyboardButton("👥 Team", callback_data="g|team"), InlineKeyboardButton("📤 Submit", callback_data="g|submit")],
        [InlineKeyboardButton("🗃️ Mission UI", callback_data="g|missionsui|1"), InlineKeyboardButton("🏠 Menu", callback_data="g|menu")],
    ])


def _assign_ui_keyboard(state, tr_page: int = 1, role_page: int = 1, tr_filter: str = "all", role_gender: str = "all", preset: str = "recommended") -> InlineKeyboardMarkup:
    mission = _ensure_bot_mission(state)
    tr_filter = _normalize_tr_filter(tr_filter)
    role_gender = _normalize_role_gender_filter(role_gender)
    preset = _normalize_assign_preset(preset)
    rows: list[list[InlineKeyboardButton]] = []

    filtered_translators = _apply_translator_filter(_safe_all_translator_candidates(state, preset=preset), tr_filter)
    tr_candidates, tr_page, tr_pages, _ = _paginate_items(filtered_translators, tr_page, per_page=4)
    if tr_candidates:
        rows.append([InlineKeyboardButton("📝 Translator picks", callback_data="g|noop")])
        rows.append([
            InlineKeyboardButton(f"TR {'●' if tr_filter == 'all' else ''}All", callback_data=f"g|assignnav|1|{role_page}|all|{role_gender}|{preset}"),
            InlineKeyboardButton(f"TR {'●' if tr_filter == 'fresh' else ''}Fresh", callback_data=f"g|assignnav|1|{role_page}|fresh|{role_gender}|{preset}"),
            InlineKeyboardButton(f"TR {'●' if tr_filter == 'calm' else ''}Calm", callback_data=f"g|assignnav|1|{role_page}|calm|{role_gender}|{preset}"),
        ])
        for idx in range(0, len(tr_candidates), 2):
            chunk = tr_candidates[idx:idx+2]
            rows.append([
                InlineKeyboardButton(f"TR {member.name}", callback_data=f"g|settr|{_name_token(member.name)}")
                for member in chunk
            ])
        nav: list[InlineKeyboardButton] = []
        if tr_page > 1:
            nav.append(InlineKeyboardButton("⬅️ TR", callback_data=f"g|assignnav|{tr_page-1}|{role_page}|{tr_filter}|{role_gender}|{preset}"))
        if tr_page < tr_pages:
            nav.append(InlineKeyboardButton("TR ➡️", callback_data=f"g|assignnav|{tr_page+1}|{role_page}|{tr_filter}|{role_gender}|{preset}"))
        if nav:
            rows.append(nav)

    filtered_roles = _apply_role_list_filter(list(mission.roles), role_gender)
    role_items, role_page, role_pages, _ = _paginate_items(filtered_roles, role_page, per_page=4)
    if role_items:
        rows.append([InlineKeyboardButton("🎙 Role picks", callback_data="g|noop")])
        rows.append([
            InlineKeyboardButton(f"{'●' if role_gender == 'all' else ''}All Roles", callback_data=f"g|assignnav|{tr_page}|1|{tr_filter}|all|{preset}"),
            InlineKeyboardButton(f"{'●' if role_gender == 'male' else ''}Male", callback_data=f"g|assignnav|{tr_page}|1|{tr_filter}|male|{preset}"),
            InlineKeyboardButton(f"{'●' if role_gender == 'female' else ''}Female", callback_data=f"g|assignnav|{tr_page}|1|{tr_filter}|female|{preset}"),
        ])
        role_buttons = [
            InlineKeyboardButton(f"🎙 {role.role}", callback_data=f"g|pickrole|{_name_token(role.role)}|1|all|{preset}")
            for role in role_items
        ]
        for idx in range(0, len(role_buttons), 2):
            rows.append(role_buttons[idx:idx+2])
        nav = []
        if role_page > 1:
            nav.append(InlineKeyboardButton("⬅️ Roles", callback_data=f"g|assignnav|{tr_page}|{role_page-1}|{tr_filter}|{role_gender}|{preset}"))
        if role_page < role_pages:
            nav.append(InlineKeyboardButton("Roles ➡️", callback_data=f"g|assignnav|{tr_page}|{role_page+1}|{tr_filter}|{role_gender}|{preset}"))
        if nav:
            rows.append(nav)

    rows.append([
        InlineKeyboardButton("👥 Team", callback_data="g|team"),
        InlineKeyboardButton("📤 Submit", callback_data="g|submit"),
    ])
    rows.append([
        InlineKeyboardButton(f"Preset {'●' if preset == 'recommended' else ''}Rec", callback_data=f"g|assignnav|1|1|{tr_filter}|{role_gender}|recommended"),
        InlineKeyboardButton(f"Lang {'●' if _effective_assign_preset(state, preset) == 'lang' else ''}", callback_data=f"g|assignnav|1|1|{tr_filter}|{role_gender}|lang"),
        InlineKeyboardButton(f"Load {'●' if _effective_assign_preset(state, preset) == 'workload' else ''}", callback_data=f"g|assignnav|1|1|{tr_filter}|{role_gender}|workload"),
    ])
    rows.append([
        InlineKeyboardButton(f"Trait {'●' if _effective_assign_preset(state, preset) == 'trait' else ''}", callback_data=f"g|assignnav|1|1|{tr_filter}|{role_gender}|trait"),
        InlineKeyboardButton("🗃️ Mission UI", callback_data="g|missionsui|1"),
        InlineKeyboardButton("🏠 Menu", callback_data="g|menu"),
    ])
    return InlineKeyboardMarkup(rows)


def _role_picker_keyboard(state, role_name: str, page: int = 1, energy_filter: str = "all", preset: str = "recommended") -> InlineKeyboardMarkup:
    energy_filter = _normalize_energy_filter(energy_filter)
    preset = _normalize_assign_preset(preset)
    candidates, page, total_pages, _ = _paginate_items(_safe_all_role_candidates(state, role_name, energy_filter=energy_filter, preset=preset), page, per_page=6)
    rows: list[list[InlineKeyboardButton]] = []
    rows.append([
        InlineKeyboardButton(f"{'●' if energy_filter == 'all' else ''}All", callback_data=f"g|pickrole|{_name_token(role_name)}|1|all|{preset}"),
        InlineKeyboardButton(f"{'●' if energy_filter == 'fresh' else ''}Fresh", callback_data=f"g|pickrole|{_name_token(role_name)}|1|fresh|{preset}"),
        InlineKeyboardButton(f"{'●' if energy_filter == 'tired' else ''}Tired", callback_data=f"g|pickrole|{_name_token(role_name)}|1|tired|{preset}"),
    ])
    for idx in range(0, len(candidates), 2):
        chunk = candidates[idx:idx+2]
        rows.append([
            InlineKeyboardButton(member.name, callback_data=f"g|setrole|{_name_token(role_name)}|{_name_token(member.name)}")
            for member in chunk
        ])
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"g|pickrole|{_name_token(role_name)}|{page-1}|{energy_filter}|{preset}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"g|pickrole|{_name_token(role_name)}|{page+1}|{energy_filter}|{preset}"))
    if nav:
        rows.append(nav)
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


def _gear_inventory_count(state) -> int:
    return sum(int(qty or 0) for qty in state.inventory.values() if int(qty or 0) > 0)


def _gear_staff_candidates(state, limit: int = 10):
    order = {"translator": 0, "male": 1, "female": 2}
    return sorted(
        state.roster,
        key=lambda member: (order.get(member.role_type, 9), -member.level, -member.power(), member.name.lower()),
    )[:limit]


def _gear_shop_keyboard(state) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    items = sorted(EQUIPMENT_CATALOG.items(), key=lambda item: (item[1].get("cost", 0), item[1].get("label", item[0])))
    for idx in range(0, len(items), 2):
        chunk = items[idx:idx+2]
        rows.append([
            InlineKeyboardButton(f"+ {meta['label']}", callback_data=f"g|buygearui|{key}")
            for key, meta in chunk
        ])
    rows.append([
        InlineKeyboardButton("🎒 Inventory", callback_data="g|inventory"),
        InlineKeyboardButton("🧩 Gear UI", callback_data="g|gearui"),
    ])
    return InlineKeyboardMarkup(rows)


def _gear_ui_keyboard(state) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("🧰 Shop", callback_data="g|gearshopui"), InlineKeyboardButton("🎒 Inventory", callback_data="g|inventory")],
    ]
    staff_buttons = [
        InlineKeyboardButton(f"🧾 {member.name}", callback_data=f"g|staffcard|{_name_token(member.name)}")
        for member in _gear_staff_candidates(state)
    ]
    for idx in range(0, len(staff_buttons), 2):
        rows.append(staff_buttons[idx:idx+2])
    rows.append([InlineKeyboardButton("⬅️ Menu", callback_data="g|menu")])
    return InlineKeyboardMarkup(rows)


def _roster_page_items(state, page: int = 1, per_page: int = 6):
    ordered = sorted(state.roster, key=lambda member: (member.role_type, -member.level, member.name.lower()))
    total = len(ordered)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    items = ordered[start:start + per_page]
    return items, page, total_pages, total


def _roster_ui_text(state, page: int = 1) -> str:
    items, page, total_pages, total = _roster_page_items(state, page)
    lines = [
        "👥 Roster browser",
        f"Page {page}/{total_pages} · Total staff {total}",
        "Tap a staff button below to open the full card.",
        "",
    ]
    if not items:
        lines.append("- Roster kosong")
    for member in items:
        gear = EQUIPMENT_CATALOG.get(member.equipped, {}).get("label", "-") if member.equipped else "-"
        traits = ", ".join(member.traits[:2]) if member.traits else "-"
        lines.append(
            f"- {member.name} | {member.role_type} | lvl {member.level} | rarity {member.rarity} | energy {member.energy} | gear {gear} | traits {traits}"
        )
    return "\n".join(lines)


def _roster_ui_keyboard(state, page: int = 1) -> InlineKeyboardMarkup:
    items, page, total_pages, _ = _roster_page_items(state, page)
    rows: list[list[InlineKeyboardButton]] = []
    for member in items:
        rows.append([InlineKeyboardButton(f"🧾 {member.name} · {member.role_type}", callback_data=f"g|staffcard|{_name_token(member.name)}")])
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"g|rosterpage|{page-1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"g|rosterpage|{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton("📜 Text roster", callback_data="g|roster"),
        InlineKeyboardButton("🧩 Gear UI", callback_data="g|gearui"),
    ])
    rows.append([InlineKeyboardButton("⬅️ Menu", callback_data="g|menu")])
    return InlineKeyboardMarkup(rows)


def _gear_ui_text(state) -> str:
    return (
        f"🧩 Gear control center\n"
        f"Coins {state.coins} | Inventory {_gear_inventory_count(state)} item | Roster {len(state.roster)} staff\n\n"
        f"Tap staff card untuk train/rest/equip. Tap shop untuk beli gear baru."
    )


def _staff_action_keyboard(state, staff_name: str) -> InlineKeyboardMarkup:
    name_token = _name_token(staff_name)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚖️ Train", callback_data=f"g|trainstaff|{name_token}|balanced"),
            InlineKeyboardButton("💪 Skill", callback_data=f"g|trainstaff|{name_token}|skill"),
        ],
        [
            InlineKeyboardButton("⚡ Speed", callback_data=f"g|trainstaff|{name_token}|speed"),
            InlineKeyboardButton("🛌 Rest", callback_data=f"g|reststaff|{name_token}"),
        ],
        [
            InlineKeyboardButton("🎯 Equip", callback_data=f"g|equippick|{name_token}"),
            InlineKeyboardButton("🎒 Unequip", callback_data=f"g|unequipstaff|{name_token}"),
        ],
        [
            InlineKeyboardButton("🧩 Gear UI", callback_data="g|gearui"),
            InlineKeyboardButton("🎒 Inventory", callback_data="g|inventory"),
        ],
    ])


def _compatible_gear_items(state, staff_name: str) -> list[tuple[str, dict]]:
    member = next((item for item in state.roster if item.name.lower() == staff_name.lower()), None)
    if member is None:
        return []
    items: list[tuple[str, dict]] = []
    for key, qty in sorted(state.inventory.items()):
        if int(qty or 0) <= 0:
            continue
        meta = EQUIPMENT_CATALOG.get(key) or {}
        roles = set(meta.get("roles", []))
        if member.role_type in roles:
            items.append((key, meta))
    items.sort(key=lambda item: (item[1].get("cost", 0), item[1].get("label", item[0])), reverse=True)
    return items


def _equip_picker_keyboard(state, staff_name: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx, (key, meta) in enumerate(_compatible_gear_items(state, staff_name)):
        label = str(meta.get("label", key))
        qty = int(state.inventory.get(key, 0))
        rows.append([InlineKeyboardButton(f"{label} x{qty}", callback_data=f"g|equipdo|{_name_token(staff_name)}|{key}")])
        if idx >= 7:
            break
    rows.append([
        InlineKeyboardButton("⬅️ Staff", callback_data=f"g|staffcard|{_name_token(staff_name)}"),
        InlineKeyboardButton("🧩 Gear UI", callback_data="g|gearui"),
    ])
    return InlineKeyboardMarkup(rows)


def _equip_picker_text(state, staff_name: str) -> str:
    items = _compatible_gear_items(state, staff_name)
    if not items:
        return f"🎯 Equip picker — {staff_name}\nTak ada gear sesuai dalam inventory sekarang."
    lines = [f"🎯 Equip picker — {staff_name}", "Pilih gear yang sesuai:"]
    for key, meta in items[:8]:
        lines.append(f"- {meta.get('label', key)} x{state.inventory.get(key, 0)} | {meta.get('desc', '-')}")
    return "\n".join(lines)


def _board_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("NEW", callback_data="g|missions|s=NEW|p=1"), InlineKeyboardButton("IN_PROGRESS", callback_data="g|missions|s=IN_PROGRESS|p=1")],
        [InlineKeyboardButton("READY", callback_data="g|missions|s=READY|p=1"), InlineKeyboardButton("COMPLETED", callback_data="g|missions|s=COMPLETED|p=1")],
        [InlineKeyboardButton("📚 All Missions", callback_data="g|missions")],
    ])


def _board_text(state) -> str:
    if not GAME_USE_DB:
        mission = _ensure_bot_mission(state)
        return "🗂️ Mission board (demo mode)" + chr(10) + chr(10) + _mission_card_text(state, mission)
    chunks = ["🗂️ Mission board snapshot", "Use /missions for the full list or the filter buttons below."]
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


def _assign_ui_text(state, tr_page: int = 1, role_page: int = 1, tr_filter: str = "all", role_gender: str = "all", preset: str = "recommended") -> str:
    mission = _ensure_bot_mission(state)
    tr = mission.assigned_translator or "-"
    tr_filter = _normalize_tr_filter(tr_filter)
    role_gender = _normalize_role_gender_filter(role_gender)
    preset = _normalize_assign_preset(preset)
    filtered_translators = _apply_translator_filter(_safe_all_translator_candidates(state, preset=preset), tr_filter)
    filtered_roles = _apply_role_list_filter(list(mission.roles), role_gender)
    _, safe_tr_page, tr_pages, tr_total = _paginate_items(filtered_translators, tr_page, per_page=4)
    _, safe_role_page, role_pages, role_total = _paginate_items(filtered_roles, role_page, per_page=4)
    lines = [
        f"🧠 Assign panel — {mission.code}",
        mission.title,
        f"Translator: {tr}",
        f"Translator page {safe_tr_page}/{tr_pages} · candidates {tr_total} · filter {tr_filter}",
        f"Role page {safe_role_page}/{role_pages} · roles {role_total} · gender filter {role_gender}",
        f"Preset: {_preset_caption(state, preset)}",
        "Roles:",
    ]
    for role in mission.roles:
        lines.append(f"- {role.role}: {mission.assigned_roles.get(role.role, '-')} ({role.gender} · {role.lines} lines)")
    lines.append("")
    lines.append("Use translator filters for fresh/calm candidates, role filters for male/female slots, or switch preset to bias language, workload, or trait-fit picks.")
    return chr(10).join(lines)


def _role_picker_text(state, role_name: str, page: int = 1, energy_filter: str = "all", preset: str = "recommended") -> str:
    mission = _ensure_bot_mission(state)
    role = next((item for item in mission.roles if item.role.lower() == role_name.lower()), None)
    if role is None:
        return f"❌ Role {role_name} tak jumpa."
    energy_filter = _normalize_energy_filter(energy_filter)
    preset = _normalize_assign_preset(preset)
    candidates, page, total_pages, total = _paginate_items(_safe_all_role_candidates(state, role.role, energy_filter=energy_filter, preset=preset), page, per_page=6)
    lines = [
        f"🎯 Pilih VO untuk {role.role}",
        f"Gender: {role.gender}",
        f"Lines: {role.lines}",
        f"Current: {mission.assigned_roles.get(role.role, '-')}",
        f"Energy filter: {energy_filter}",
        f"Candidate page {page}/{total_pages} · total {total}",
        f"Preset: {_preset_caption(state, preset)}",
        "",
        "Calon pada page ini:",
    ]
    for member in candidates:
        lines.append(f"- {member.name} | power {round(member.power(),1)} | energy {member.energy} | burnout {member.burnout}")
    if not candidates:
        lines.append("- Tiada calon yang sesuai")
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


def _missions_ui_text(payload: dict[str, object]) -> str:
    items = list(payload.get("items", []))
    page = int(payload.get("page", 1) or 1)
    total_pages = int(payload.get("total_pages", 1) or 1)
    total = int(payload.get("total", len(items)) or 0)
    lines = [
        "🗃️ Mission browser",
        f"Page {page}/{max(1, total_pages)} · Total {total}",
        "Tap a mission button to load it instantly into the game state.",
        "",
    ]
    if not items:
        lines.append("- No missions available for this page.")
    for item in items:
        lines.append(
            f"- {item['code']} | {item['title']}" + chr(10) +
            f"  {item.get('lang', '-')} | {item.get('priority', '-')} | {item.get('status', '-')} | TR: {item.get('translator') or '-'}"
        )
    return chr(10).join(lines)


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
    rows.append([InlineKeyboardButton("🗃️ Mission UI", callback_data=f"g|missionsui|{page}"), InlineKeyboardButton("⬅️ Menu", callback_data="g|mission")])
    return InlineKeyboardMarkup(rows)



def _missions_ui_keyboard(payload: dict[str, object]) -> InlineKeyboardMarkup:
    items = list(payload.get("items", []))
    page = int(payload.get("page", 1) or 1)
    total_pages = int(payload.get("total_pages", 1) or 1)
    rows: list[list[InlineKeyboardButton]] = []
    for item in items[:6]:
        code = str(item["code"])
        title = str(item["title"])
        label = f"🎯 {code}"
        if len(title) <= 20:
            label += f" · {title}"
        rows.append([InlineKeyboardButton(label, callback_data=f"g|pick|{code}")])
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"g|missionsui|{page-1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"g|missionsui|{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("📚 Text list", callback_data=f"g|missions|p={page}"), InlineKeyboardButton("🏠 Menu", callback_data="g|menu")])
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
        header.append(f"Filters: {', '.join(filters)}")
    suffix = f" — total {total}" if total is not None else ""
    header.append(f"Page {page}/{max(1, total_pages)}{suffix}")
    if not items:
        return chr(10).join(header + ["", "- No missions found for the current filter."])
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
    await update.effective_message.reply_text(_mission_card_text(state, mission), reply_markup=_selected_mission_keyboard())


async def cmd_dbmission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    try:
        mission = load_db_mission_into_state(state)
        if mission is None:
            text = "❌ No suitable DB mission was found. Falling back to the standard /mission flow."
        else:
            text = f"🗄️ DB mission loaded\n\n{_mission_card_text(state, mission)}"
    except Exception as exc:
        text = f"❌ DB mission gagal load: {exc}"
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_selected_mission_keyboard() if not text.startswith("❌") and code else _menu())


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


async def cmd_missionsui(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    try:
        page = max(1, int((context.args or ["1"])[0]))
    except (ValueError, TypeError):
        page = 1
    try:
        if GAME_USE_DB:
            payload = list_db_missions(state, limit=6, page=page, include_meta=True)
        else:
            mission = _ensure_bot_mission(state)
            payload = {
                "items": [{
                    "code": mission.code,
                    "title": mission.title,
                    "lang": mission.lang,
                    "priority": mission.priority,
                    "status": "ACTIVE",
                    "translator": mission.assigned_translator or "-",
                }],
                "page": 1,
                "total_pages": 1,
                "total": 1,
            }
        text = _missions_ui_text(payload)
        markup = _missions_ui_keyboard(payload)
    except Exception as exc:
        text = f"❌ Tak dapat buka mission browser: {exc}"
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
    tr_filter = _normalize_tr_filter(context.args[0]) if getattr(context, "args", None) else "all"
    role_gender = _normalize_role_gender_filter(context.args[1]) if getattr(context, "args", None) and len(context.args) > 1 else "all"
    preset = _normalize_assign_preset(context.args[2]) if getattr(context, "args", None) and len(context.args) > 2 else "recommended"
    _save(state)
    await update.effective_message.reply_text(
        _assign_ui_text(state, tr_page=1, role_page=1, tr_filter=tr_filter, role_gender=role_gender, preset=preset),
        reply_markup=_assign_ui_keyboard(state, tr_page=1, role_page=1, tr_filter=tr_filter, role_gender=role_gender, preset=preset),
    )


def _apply_assign_preset(state, requested: str, actor_name: str) -> tuple[str, str]:
    mission = _ensure_bot_mission(state)
    preset = _effective_assign_preset(state, requested)
    picks: dict[str, str] = {}
    translators = _safe_all_translator_candidates(state, preset=preset)
    if translators:
        picks["translator"] = assign_translator(state, translators[0].name)
    for role in mission.roles:
        candidates = _safe_all_role_candidates(state, role.role, energy_filter="all", preset=preset)
        if candidates:
            picks[role.role] = assign_role(state, role.role, candidates[0].name)
    db_info = _persist_assignments_if_db(state, actor_name=actor_name)
    lines = [f"🧠 Assign preset applied: {_preset_caption(state, requested)}"]
    for key, value in picks.items():
        lines.append(f"- {key}: {value}")
    if not picks:
        lines.append("- No suitable staff available")
    if db_info:
        lines.append(f"DB synced: task #{db_info['translation_task_id']} · assignments +{db_info['assignment_created']}")
    lines.append("")
    lines.append("Use Team or Submit next, or tap another preset to re-balance the roster.")
    return "\n".join(lines), preset


async def cmd_assignpreset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    requested = (context.args[0] if getattr(context, "args", None) else "recommended")
    try:
        text, preset = _apply_assign_preset(state, requested, actor_name=update.effective_user.first_name or "player")
    except Exception as exc:
        preset = requested
        text = f"❌ Assign preset gagal: {exc}"
    _save(state)
    await update.effective_message.reply_text(text, reply_markup=_assign_ui_keyboard(state, preset=preset))


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
                text = f"🎯 Mission dipilih dari DB\n\n{_mission_card_text(state, mission)}"
        except Exception as exc:
            text = f"❌ Pick mission gagal: {exc}"
    _save(state)
    picked_ok = bool(code) and not text.startswith("❌") and not text.startswith("Usage:")
    await update.effective_message.reply_text(text, reply_markup=_selected_mission_keyboard() if picked_ok else _menu())


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
    await update.effective_message.reply_text(f"✅ Misi diterima!\n\n{_mission_card_text(state, mission)}", reply_markup=_selected_mission_keyboard())


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


async def cmd_rosterui(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    page = 1
    args = list(getattr(context, "args", None) or [])
    if args:
        try:
            page = max(1, int(args[0]))
        except ValueError:
            page = 1
    _save(state)
    await update.effective_message.reply_text(_roster_ui_text(state, page), reply_markup=_roster_ui_keyboard(state, page))


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
    markup = _staff_action_keyboard(state, name) if name and not text.startswith('❌') else _menu()
    await update.effective_message.reply_text(text, reply_markup=markup)


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
    markup = _staff_action_keyboard(state, name) if name and not text.startswith('❌') else _menu()
    await update.effective_message.reply_text(text, reply_markup=markup)


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
    markup = _staff_action_keyboard(state, name) if name and not text.startswith('❌') else _menu()
    await update.effective_message.reply_text(text, reply_markup=markup)


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
    await update.effective_message.reply_text(inventory_summary(state), reply_markup=_gear_ui_keyboard(state))


async def cmd_gearshop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _save(state)
    await update.effective_message.reply_text(gear_shop_summary(state), reply_markup=_gear_shop_keyboard(state))


async def cmd_gearui(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _load_or_create(update.effective_user.id)
    _save(state)
    await update.effective_message.reply_text(_gear_ui_text(state), reply_markup=_gear_ui_keyboard(state))


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
    await update.effective_message.reply_text(text, reply_markup=_gear_shop_keyboard(state))


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
    markup = _staff_action_keyboard(state, staff_name) if 'staff_name' in locals() and staff_name and not text.startswith('❌') else _gear_ui_keyboard(state)
    await update.effective_message.reply_text(text, reply_markup=markup)


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
    markup = _staff_action_keyboard(state, staff_name) if staff_name and not text.startswith('❌') else _gear_ui_keyboard(state)
    await update.effective_message.reply_text(text, reply_markup=markup)


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
        try:
            page = max(1, int(parts[3])) if len(parts) >= 4 else 1
        except ValueError:
            page = 1
        energy_filter = _normalize_energy_filter(parts[4]) if len(parts) >= 5 else "all"
        preset = _normalize_assign_preset(parts[5]) if len(parts) >= 6 else "recommended"
        _save(state)
        await update.effective_message.reply_text(
            _role_picker_text(state, role_name, page=page, energy_filter=energy_filter, preset=preset),
            reply_markup=_role_picker_keyboard(state, role_name, page=page, energy_filter=energy_filter, preset=preset),
        )
        return

    if len(parts) >= 4 and parts[1] == "assignnav":
        state = _load_or_create(update.effective_user.id)
        _ensure_bot_mission(state)
        try:
            tr_page = max(1, int(parts[2]))
        except ValueError:
            tr_page = 1
        try:
            role_page = max(1, int(parts[3]))
        except ValueError:
            role_page = 1
        tr_filter = _normalize_tr_filter(parts[4]) if len(parts) >= 5 else "all"
        role_gender = _normalize_role_gender_filter(parts[5]) if len(parts) >= 6 else "all"
        preset = _normalize_assign_preset(parts[6]) if len(parts) >= 7 else "recommended"
        _save(state)
        await update.effective_message.reply_text(
            _assign_ui_text(state, tr_page=tr_page, role_page=role_page, tr_filter=tr_filter, role_gender=role_gender, preset=preset),
            reply_markup=_assign_ui_keyboard(state, tr_page=tr_page, role_page=role_page, tr_filter=tr_filter, role_gender=role_gender, preset=preset),
        )
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

    if len(parts) >= 3 and parts[1] == "presetapply":
        state = _load_or_create(update.effective_user.id)
        requested = parts[2].strip() or "recommended"
        try:
            text, preset = _apply_assign_preset(state, requested, actor_name=update.effective_user.first_name or "player")
        except Exception as exc:
            preset = requested
            text = f"❌ Assign preset gagal: {exc}"
        _save(state)
        await update.effective_message.reply_text(text, reply_markup=_assign_ui_keyboard(state, preset=preset))
        return

    if len(parts) >= 3 and parts[1] == "assignpreset":
        state = _load_or_create(update.effective_user.id)
        _ensure_bot_mission(state)
        preset = parts[2].strip() or "recommended"
        _save(state)
        await update.effective_message.reply_text(
            _assign_ui_text(state, tr_page=1, role_page=1, tr_filter="all", role_gender="all", preset=preset),
            reply_markup=_assign_ui_keyboard(state, tr_page=1, role_page=1, tr_filter="all", role_gender="all", preset=preset),
        )
        return

    if len(parts) >= 2 and parts[1] == "gearshopui":
        state = _load_or_create(update.effective_user.id)
        _save(state)
        await update.effective_message.reply_text(gear_shop_summary(state), reply_markup=_gear_shop_keyboard(state))
        return

    if len(parts) >= 3 and parts[1] == "buygearui":
        state = _load_or_create(update.effective_user.id)
        item_key = parts[2].strip()
        try:
            info = buy_gear(state, item_key)
            text = f"🧰 Gear dibeli: {info['label']}\nCost: {info['cost']}\nQty sekarang: {info['qty']}"
        except Exception as exc:
            text = f"❌ {exc}"
        _save(state)
        await update.effective_message.reply_text(text, reply_markup=_gear_shop_keyboard(state))
        return

    if len(parts) >= 3 and parts[1] == "staffcard":
        state = _load_or_create(update.effective_user.id)
        staff_name = _name_from_token(parts[2])
        try:
            text = staff_detail_summary(state, staff_name)
        except Exception as exc:
            text = f"❌ {exc}"
        _save(state)
        markup = _staff_action_keyboard(state, staff_name) if not text.startswith('❌') else _gear_ui_keyboard(state)
        await update.effective_message.reply_text(text, reply_markup=markup)
        return

    if len(parts) >= 3 and parts[1] == "missionsui":
        state = _load_or_create(update.effective_user.id)
        try:
            page = max(1, int(parts[2]))
        except ValueError:
            page = 1
        try:
            if GAME_USE_DB:
                payload = list_db_missions(state, limit=6, page=page, include_meta=True)
            else:
                mission = _ensure_bot_mission(state)
                payload = {
                    "items": [{
                        "code": mission.code,
                        "title": mission.title,
                        "lang": mission.lang,
                        "priority": mission.priority,
                        "status": "ACTIVE",
                        "translator": mission.assigned_translator or "-",
                    }],
                    "page": 1,
                    "total_pages": 1,
                    "total": 1,
                }
            text = _missions_ui_text(payload)
            markup = _missions_ui_keyboard(payload)
        except Exception as exc:
            text = f"❌ Tak dapat buka mission browser: {exc}"
            markup = _menu()
        _save(state)
        await update.effective_message.reply_text(text, reply_markup=markup)
        return

    if len(parts) >= 3 and parts[1] == "rosterpage":
        state = _load_or_create(update.effective_user.id)
        try:
            page = max(1, int(parts[2]))
        except ValueError:
            page = 1
        _save(state)
        await update.effective_message.reply_text(_roster_ui_text(state, page), reply_markup=_roster_ui_keyboard(state, page))
        return

    if len(parts) >= 4 and parts[1] == "trainstaff":
        state = _load_or_create(update.effective_user.id)
        staff_name = _name_from_token(parts[2])
        focus = parts[3].strip().lower() or "balanced"
        try:
            info = train_staff(state, staff_name, focus=focus)
            member = info["member"]
            text = (
                f"🏋️ Training siap untuk {member.name}\n"
                f"Focus: {info['focus']} | Cost: {info['cost']}\n"
                f"Skill {member.skill} | Speed {member.speed} | Level {member.level}\n"
                f"Energy {member.energy} | Burnout {member.burnout}"
            )
            if info.get("level_up"):
                text += "\n✨ Level up!"
        except Exception as exc:
            text = f"❌ {exc}"
        _save(state)
        markup = _staff_action_keyboard(state, staff_name) if not text.startswith('❌') else _gear_ui_keyboard(state)
        await update.effective_message.reply_text(text, reply_markup=markup)
        return

    if len(parts) >= 3 and parts[1] == "reststaff":
        state = _load_or_create(update.effective_user.id)
        staff_name = _name_from_token(parts[2])
        try:
            info = rest_staff(state, staff_name)
            member = info["member"]
            text = (
                f"🛌 Rest siap untuk {member.name}\n"
                f"Cost: {info['cost']}\n"
                f"Energy +{info['energy_recovered']} | Burnout -{info['burnout_reduced']}\n"
                f"Energy now {member.energy} | Burnout now {member.burnout}"
            )
        except Exception as exc:
            text = f"❌ {exc}"
        _save(state)
        markup = _staff_action_keyboard(state, staff_name) if not text.startswith('❌') else _gear_ui_keyboard(state)
        await update.effective_message.reply_text(text, reply_markup=markup)
        return

    if len(parts) >= 3 and parts[1] == "equippick":
        state = _load_or_create(update.effective_user.id)
        staff_name = _name_from_token(parts[2])
        _save(state)
        await update.effective_message.reply_text(_equip_picker_text(state, staff_name), reply_markup=_equip_picker_keyboard(state, staff_name))
        return

    if len(parts) >= 4 and parts[1] == "equipdo":
        state = _load_or_create(update.effective_user.id)
        staff_name = _name_from_token(parts[2])
        item_key = parts[3].strip()
        try:
            info = equip_gear(state, staff_name, item_key)
            text = f"🎯 {info['member'].name} equip {info['label']}"
            if info.get('previous'):
                text += f"\nPrevious returned to inventory: {info['previous']}"
        except Exception as exc:
            text = f"❌ {exc}"
        _save(state)
        markup = _staff_action_keyboard(state, staff_name) if not text.startswith('❌') else _gear_ui_keyboard(state)
        await update.effective_message.reply_text(text, reply_markup=markup)
        return

    if len(parts) >= 3 and parts[1] == "unequipstaff":
        state = _load_or_create(update.effective_user.id)
        staff_name = _name_from_token(parts[2])
        try:
            info = unequip_gear(state, staff_name)
            text = f"🎒 {info['member'].name} unequip {info['label']}"
        except Exception as exc:
            text = f"❌ {exc}"
        _save(state)
        markup = _staff_action_keyboard(state, staff_name) if not text.startswith('❌') else _gear_ui_keyboard(state)
        await update.effective_message.reply_text(text, reply_markup=markup)
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
        "missionsui": cmd_missionsui,
        "board": cmd_board,
        "assignui": cmd_assignui,
        "assignpreset": cmd_assignpreset,
        "syncdb": cmd_syncdb,
        "accept": cmd_accept,
        "autocast": cmd_autocast,
        "submit": cmd_submit,
        "roster": cmd_roster,
        "rosterui": cmd_rosterui,
        "team": cmd_team,
        "bench": cmd_bench,
        "market": cmd_market,
        "studio": cmd_studio,
        "clients": cmd_clients,
        "reputation": cmd_reputation,
        "goals": cmd_goals,
        "inventory": cmd_inventory,
        "gearui": cmd_gearui,
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
    app.add_handler(CommandHandler("missionsui", cmd_missionsui))
    app.add_handler(CommandHandler("board", cmd_board))
    app.add_handler(CommandHandler("assignui", cmd_assignui))
    app.add_handler(CommandHandler("assignpreset", cmd_assignpreset))
    app.add_handler(CommandHandler("pick", cmd_pick))
    app.add_handler(CommandHandler("syncdb", cmd_syncdb))
    app.add_handler(CommandHandler("accept", cmd_accept))
    app.add_handler(CommandHandler("autocast", cmd_autocast))
    app.add_handler(CommandHandler("assigntr", cmd_assigntr))
    app.add_handler(CommandHandler("assign", cmd_assign))
    app.add_handler(CommandHandler("clearcast", cmd_clearcast))
    app.add_handler(CommandHandler("submit", cmd_submit))
    app.add_handler(CommandHandler("roster", cmd_roster))
    app.add_handler(CommandHandler("rosterui", cmd_rosterui))
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
    app.add_handler(CommandHandler("gearui", cmd_gearui))
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
