import os
import json
import re
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, Any, List
from collections import deque
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.error import BadRequest
from db import db
from models import (
    Movie,
    GroupOpenRequest,
    Assignment,
    VOTeam,
    TranslationSubmission,
    VORoleSubmission,
    AdminTelegramUser,
    GroupMovieContext,
    GroupRoleImportRequest,
    Translator,
    TranslationTask,
    AppKV,
    MovieAlias,
)
from assign_logic import parse_lines, role_gender, pick_vo, movie_load, norm_role
from ops_log import log_event, fetch_logs
from export_dynamic import export_excel_dynamic, backup_json_zip_dynamic
from movie_history import record_movie_event, fetch_movie_history, fetch_recent_movie_events
from movie_merge import duplicate_groups, merge_preview, merge_movies, merge_simulation
log = logging.getLogger(__name__)
# Security: redact bot tokens from logs + silence noisy HTTP client logs
from sec_logging import install_security_logging
install_security_logging()
BOT_NAME = "Web VO Tracker"
# NOTE: keep version hard-coded so /help always matches deployed code.
# Version is shared between web and bot.
from version import APP_VERSION
from sqlalchemy import func
# Optional hard owner (still supported) — but you can manage admins via /admin_add
OWNER_TG_ID = os.getenv("OWNER_TG_ID")
# Where to notify ops when something happens (queue submit, WAIT_EMBED)
ADMIN_TELEGRAM_CHAT_ID = os.getenv("ADMIN_TELEGRAM_CHAT_ID")
# Optional anonymous drop channel/group where translator submissions get forwarded
DROP_CHAT_ID = os.getenv("DROP_CHAT_ID")
# ✅ Translator DM → bot forwards translated SRT to this group/channel
# Example: -1001234567890
SRT_OUTBOX_CHAT_ID = os.getenv("SRT_OUTBOX_CHAT_ID")
# Optional archive group/channel.
# When a movie is marked COMPLETED, bot can post a summary + latest SRT there.
ARCHIVE_CHAT_ID = os.getenv("ARCHIVE_CHAT_ID")
# If 1, hide uploader name when forwarding to group
SRT_FORWARD_ANON = os.getenv("SRT_FORWARD_ANON", "0") == "1"
# Default group naming (manual create flow)
GROUP_TITLE_TEMPLATE = os.getenv("GROUP_TITLE_TEMPLATE", "VO — {code} — {title} ({year}) [{lang}]")
# Default language if filename doesn't include [BN]/[ID]/[MS]
DEFAULT_LANG = os.getenv("DEFAULT_LANG", "bn")
# Optional extra admin user ids (comma-separated). These users can see internal IDs/codes.
ADMIN_USER_IDS = {int(x) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip().isdigit()}
# Malaysia Time (UTC+8) for deadline commands shown to admins/users.
MYT_OFFSET_HOURS = 8
PRIORITY_MODE_SPECS = {
    "superurgent": {"label": "SUPER URGENT", "hours": 12, "urgent_only": True},
    "urgent": {"label": "URGENT", "hours": 24, "urgent_only": True},
    "nonurgent": {"label": "NON-URGENT", "hours": 36, "urgent_only": False},
    "flexible": {"label": "FLEXIBLE", "hours": 48, "urgent_only": False},
}

def _normalize_priority_mode(raw: str | None) -> str:
    s = (raw or "").strip().lower().replace("_", "").replace("-", "")
    aliases = {
        "super": "superurgent",
        "superurgent": "superurgent",
        "urgent": "urgent",
        "normal": "urgent",
        "nonurgent": "nonurgent",
        "non": "nonurgent",
        "relaxed": "flexible",
        "flexible": "flexible",
        "lowpriority": "flexible",
        "48h": "flexible",
        "36h": "nonurgent",
    }
    return aliases.get(s, "urgent")

def _priority_mode_hours(mode: str | None) -> int:
    return int(PRIORITY_MODE_SPECS[_normalize_priority_mode(mode)]["hours"])

def _priority_mode_label(mode: str | None) -> str:
    return str(PRIORITY_MODE_SPECS[_normalize_priority_mode(mode)]["label"])

def _priority_mode_urgent_only(mode: str | None) -> bool:
    return bool(PRIORITY_MODE_SPECS[_normalize_priority_mode(mode)]["urgent_only"])

def _priority_mode_deadline(mode: str | None, now_utc: datetime | None = None) -> datetime:
    return (now_utc or _now_utc()) + timedelta(hours=_priority_mode_hours(mode))

def _movie_priority_mode(movie: Movie | None, assigns: list[Assignment] | None = None) -> str:
    rows = assigns if assigns is not None else ([] if not movie else Assignment.query.filter((Assignment.movie_id == movie.id) | (Assignment.project == movie.code)).all())
    scores = {"superurgent": 4, "urgent": 3, "nonurgent": 2, "flexible": 1}
    best = None
    best_score = -1
    for a in rows:
        mode = _normalize_priority_mode(getattr(a, "priority_mode", None) or ("urgent" if bool(getattr(a, "urgent", False)) else "nonurgent"))
        sc = scores.get(mode, 0)
        if sc > best_score:
            best = mode
            best_score = sc
    return best or "urgent"
def utc_to_myt(dt: datetime | None) -> datetime | None:
    if not dt:
        return None
    return dt + timedelta(hours=MYT_OFFSET_HOURS)
def fmt_myt(dt: datetime | None) -> str:
    if not dt:
        return "-"
    return utc_to_myt(dt).strftime("%Y-%m-%d %H:%M") + " MYT"
def parse_myt_datetime_local(val: str | None) -> datetime | None:
    s = (val or "").strip()
    if not s:
        return None
    if s.lower() in {"clear", "none", "null", "-"}:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M"):
        try:
            dt_local = datetime.strptime(s[:16] if fmt == "%Y-%m-%dT%H:%M" else s, fmt)
            return dt_local - timedelta(hours=MYT_OFFSET_HOURS)
        except Exception:
            continue
    return None
def lang_label(lang: str) -> str:
    """Human-friendly language label."""
    mapping = {
        "bn": "Bengali",
        "en": "English",
        "ms": "Malay",
    }
    if not lang:
        return ""
    return mapping.get(lang.lower(), lang.upper())
def fmt_title_year(title: str | None, year: str | int | None) -> str:
    """Format movie display safely.
    If year is missing/blank, returns just title (no empty parentheses).
    """
    t = (title or "").strip()
    y = ""
    if year is not None:
        y = str(year).strip()
    if t and y and y.lower() != "none":
        return f"{t} ({y})"
    return t or y or ""
def _is_admin_id(uid: int | None) -> bool:
    """Admin/owner check by Telegram user id (safe for handlers without Update)."""
    if uid is None:
        return False
    # OWNER always allowed
    try:
        if OWNER_TG_ID and str(uid) == str(int(OWNER_TG_ID)):
            return True
    except Exception:
        pass
    # extra allowlist
    if uid in ADMIN_USER_IDS:
        return True
    # DB-based admin list
    try:
        row = AdminTelegramUser.query.filter_by(tg_user_id=int(uid), active=True).first()
        return bool(row)
    except Exception:
        return False
def is_owner_or_admin(update: Update) -> bool:
    """Whether the current Telegram user can see internal-only details."""
    uid = getattr(getattr(update, "effective_user", None), "id", None)
    return _is_admin_id(uid)
# Option A: group auto-detect context TTL (hours)
GROUP_CTX_TTL_HOURS = int(os.getenv("GROUP_CTX_TTL_HOURS", "72"))
# In-memory cache of recent movie detections per chat. This lets the bot
# associate a later role list with the nearest latest media context, even if
# the role list is posted separately (not as a reply).
MOVIE_CANDIDATE_CACHE_MAX = int(os.getenv("MOVIE_CANDIDATE_CACHE_MAX", "50"))
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler so the bot doesn't crash silently on Render."""
    log.exception("Telegram handler error", exc_info=context.error)
    try:
        import traceback as tb
        log_event("ERROR", "tg.error", f"{context.error}", tb.format_exc())
    except Exception:
        pass
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("⚠️ Bot error. Admin please check logs.")
    except Exception:
        # never raise inside error handler
        pass
# -----------------------------
# In-memory sessions (short-lived)
# -----------------------------
# DM submit state: user_id -> token (movie code or title string)
SUBMIT_MODE: Dict[int, str] = {}
# Bulk assign state: chat_id -> {movie_code, movie_id, text}
BULK_ASSIGN: Dict[int, Dict[str, Any]] = {}
# Interactive project wizard (admin). user_id -> state
PROJECT_WIZARD: Dict[int, Dict[str, Any]] = {}
# Private inline panel prompt state. user_id -> workflow state
# Modes: find_movie | who_has | assign_tr_movie | assign_tr_name | reassign_vo_movie | reassign_vo | movie_load | remind_overdue
PANEL_PROMPT: Dict[int, Dict[str, Any]] = {}
# Pending admin actions that require confirm/cancel before writing to DB.
PENDING_ACTIONS: Dict[str, Dict[str, Any]] = {}
PENDING_ACTION_TTL_MIN = int(os.getenv("PENDING_ACTION_TTL_MIN", "30"))
UNDO_ACTIONS: Dict[str, Dict[str, Any]] = {}
UNDO_LAST_BY_USER: Dict[int, str] = {}
UNDO_ACTION_TTL_MIN = int(os.getenv("UNDO_ACTION_TTL_MIN", "20"))
BULK_MOVIE_ACTIONS: Dict[str, Dict[str, Any]] = {}
BULK_MOVIE_ACTION_TTL_MIN = int(os.getenv("BULK_MOVIE_ACTION_TTL_MIN", "20"))
MOVIE_CODE_RE = re.compile(r"\b[A-Za-z]{2,5}-\d{6}-\d{2}\b")
# For parsing translated SRT filename
YEAR_RE = re.compile(r"\((19\d{2}|20\d{2})\)")
LANG_RE = re.compile(r"\[([A-Za-z]{2,8})\]")
PAREN_RE = re.compile(r"\(([^()]+)\)")
# Fast heuristic: a role-list message usually has many lines starting with man/fem.
ROLELIST_HINT_RE = re.compile(r"(?im)^(man|male|m|fem|female|f)[- ]?\d{1,2}\b")
ROLE_PREFIX_TITLE_RE = re.compile(r"(?i)^(?:man|male|m|fem|female|f)[-_ ]?\d{1,2}(?:\b|(?=\s))[-_ ]*")
def _norm_title(s: str) -> str:
    s = (s or "").strip()
    # keep non-latin characters, only normalize spacing/separators
    s = re.sub(r"[._]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" -_\t")
def _strip_role_prefix_title(title: str) -> str:
    t = (title or '').strip()
    prev = None
    while t and t != prev:
        prev = t
        t = ROLE_PREFIX_TITLE_RE.sub('', t).strip(' -_')
    return t.strip()

def _looks_role_prefixed_title(title: str) -> bool:
    return bool(ROLE_PREFIX_TITLE_RE.match((title or '').strip()))

def detect_lang_from_filename(filename: str) -> str:
    """Best-effort language guess from filename/caption text.
    Returns a normalized slug and never raises.
    """
    parsed = parse_movie_from_filename(filename or "")
    if parsed and parsed.get("lang"):
        return _slug_lang(parsed.get("lang") or DEFAULT_LANG)
    raw = (filename or "").strip().lower()
    if re.search(r"\b(bengal|bengali|bangla|bn)\b", raw):
        return "bn"
    if re.search(r"\b(english|eng|en)\b", raw):
        return "en"
    if re.search(r"\b(malay|melayu|bahasa\s*melayu|bm|ms)\b", raw):
        return "ms"
    return DEFAULT_LANG


def _context_args_list(context) -> list[str]:
    args = getattr(context, "args", None)
    if not args:
        return []
    try:
        return [str(x) for x in args]
    except TypeError:
        return []

def _context_args_text(context) -> str:
    return " ".join(_context_args_list(context)).strip()

def parse_movie_from_filename(filename: str) -> Optional[Dict[str, str]]:
    """Infer title/year/lang from a filename like:
    - The Big Whoop 2025.mp4.bengal_sub.mp4
    - The Big Whoop (2025) [bn].srt
    Year is REQUIRED for Option A.
    """
    base = (filename or "").strip()
    if not base:
        return None
    # Strip extension(s)
    name = re.sub(r"\.(mp4|mkv|avi|mov|srt|ass|vtt|txt|zip|rar|7z|wav|mp3|m4a)$", "", base, flags=re.I)
    # detect lang by keywords anywhere in name
    lname = name.lower()
    lang = None
    # explicit [xx]
    mtag = LANG_RE.search(name)
    if mtag:
        lang = _slug_lang(mtag.group(1))
    else:
        if re.search(r"\b(bengal|bengali|bangla|bn)\b", lname):
            lang = "bn"
        elif re.search(r"\b(english|eng|en)\b", lname):
            lang = "en"
        elif re.search(r"\b(malay|melayu|bahasa\s*melayu|bm|ms)\b", lname):
            lang = "ms"
    # find a 4-digit year
    my = re.search(r"\b(19\d{2}|20\d{2})\b", name)
    if not my:
        # also allow (2025)
        my = YEAR_RE.search(name)
    if not my:
        return None
    year = my.group(1)
    # Title: everything before year token
    pre = name[: my.start()].strip() if hasattr(my, "start") else name
    if not pre:
        # fallback: remove year group
        pre = re.sub(r"\(?\b(19\d{2}|20\d{2})\b\)?", " ", name)
    # remove common non-title tokens
    pre = re.sub(r"\b(role|roles|censor|sub|subs|subtitle|bengal_sub|bn_sub|dub|vo)\b", " ", pre, flags=re.I)
    pre = _strip_role_prefix_title(pre)
    title = _norm_title(pre)
    if len(title) < 2:
        return None
    return {"title": title, "year": str(year), "lang": _slug_lang(lang or DEFAULT_LANG)}
def _ctx_upsert(chat_id: int, title: str, year: str, lang: str, file_name: Optional[str], msg_id: Optional[int]) -> None:
    expires_at = _now_utc() + timedelta(hours=GROUP_CTX_TTL_HOURS)
    row = GroupMovieContext.query.filter_by(tg_chat_id=chat_id).first()
    if not row:
        row = GroupMovieContext(
            tg_chat_id=chat_id,
            title=title,
            year=year,
            lang=_slug_lang(lang),
            source_file_name=file_name,
            source_message_id=msg_id,
            detected_at=_now_utc(),
            expires_at=expires_at,
        )
        db.session.add(row)
    else:
        row.title = title
        row.year = year
        row.lang = _slug_lang(lang)
        row.source_file_name = file_name
        row.source_message_id = msg_id
        row.detected_at = _now_utc()
        row.expires_at = expires_at
    db.session.commit()
def _ctx_get(chat_id: int) -> Optional[GroupMovieContext]:
    row = GroupMovieContext.query.filter_by(tg_chat_id=chat_id).first()
    if not row:
        return None
    if row.expires_at and row.expires_at < _now_utc():
        try:
            db.session.delete(row)
            db.session.commit()
        except Exception:
            db.session.rollback()
        return None
    return row
def _cache_movie_candidate(context: ContextTypes.DEFAULT_TYPE, chat_id: int, payload: Dict[str, Any]) -> None:
    """Cache latest movie detection for a chat in bot_data.
    This is used as a fallback when a later role list isn't posted as a reply.
    """
    store = context.bot_data.setdefault("movie_candidates", {})
    dq = store.get(chat_id)
    if dq is None:
        dq = deque(maxlen=MOVIE_CANDIDATE_CACHE_MAX)
        store[chat_id] = dq
    dq.append(payload)

def _cache_recent_group_file(context: ContextTypes.DEFAULT_TYPE, chat_id: int, file_name: str | None, msg_id: int | None = None) -> None:
    """Remember recent file names posted in a group.
    Used as a stronger fallback when role*.txt arrives before the movie is formally bound.
    """
    name = (file_name or '').strip()
    if not chat_id or not name:
        return
    store = context.bot_data.setdefault("recent_group_files", {})
    dq = store.get(chat_id)
    if dq is None:
        dq = deque(maxlen=24)
        store[chat_id] = dq
    dq.append({
        'file_name': name,
        'msg_id': int(msg_id or 0),
        'detected_at': datetime.utcnow(),
    })

def _recent_group_file_candidates(context: ContextTypes.DEFAULT_TYPE, chat_id: int, *, now: datetime, lookback_hours: int = 24) -> List[Dict[str, Any]]:
    store = context.bot_data.get("recent_group_files", {})
    dq = store.get(chat_id)
    if not dq:
        return []
    ttl = timedelta(hours=lookback_hours)
    out: List[Dict[str, Any]] = []
    for item in reversed(dq):
        ts = item.get('detected_at')
        if not isinstance(ts, datetime):
            continue
        if now - ts > ttl:
            continue
        out.append(item)
    return out

def _parse_movie_from_role_helper_filename(name: str | None) -> Optional[Dict[str, str]]:
    """Try harder on names like 'The Big Whoop 2025_role_mari.txt'."""
    base = (name or '').strip()
    if not base:
        return None
    parsed = parse_movie_from_filename(base)
    if parsed:
        return parsed
    stem = re.sub(r'\.(txt|srt|ass|vtt)$', '', base, flags=re.I)
    stem = re.sub(r'([_\- ]+)?(role|roles)([_\- ][^.]*)?$', '', stem, flags=re.I)
    stem = re.sub(r'([_\- ]+)?(censor)([_\- ][^.]*)?$', '', stem, flags=re.I)
    stem = re.sub(r'[_]+', ' ', stem).strip(' -_')
    if stem and stem != base:
        parsed = parse_movie_from_filename(stem)
        if parsed:
            return parsed
    return None


_TITLE_REPAIR_SUFFIX_RE = re.compile(r"(?i)(?:^|[\s_-])(role|roles|censor)\b.*$")

def _clean_movie_title_candidate(raw: str | None) -> str:
    t = _norm_title(raw or '')
    if not t:
        return ''
    t = _strip_role_prefix_title(t)
    t = _TITLE_REPAIR_SUFFIX_RE.sub('', t).strip(' -_')
    t = _norm_title(t)
    return t

def _latest_title_hint_for_movie(movie: Movie) -> str:
    hints: List[str] = []
    try:
        chat_id = int(getattr(movie, 'vo_group_chat_id', 0) or 0)
    except Exception:
        chat_id = 0
    year = str(getattr(movie, 'year', '') or '').strip()
    lang = _slug_lang(getattr(movie, 'lang', '') or DEFAULT_LANG)
    if chat_id:
        try:
            reqs = (
                GroupRoleImportRequest.query
                .filter_by(tg_chat_id=chat_id)
                .order_by(GroupRoleImportRequest.created_at.desc())
                .limit(10)
                .all()
            )
            for req in reqs:
                if year and str(req.year or '').strip() and str(req.year).strip() != year:
                    continue
                req_lang = _slug_lang(req.lang or DEFAULT_LANG)
                if req_lang != lang:
                    continue
                cand = _clean_movie_title_candidate(req.title)
                if cand and not _looks_role_prefixed_title(cand):
                    hints.append(cand)
        except Exception:
            pass
        try:
            ctx = GroupMovieContext.query.filter_by(tg_chat_id=chat_id).first()
            if ctx:
                cand = _clean_movie_title_candidate(ctx.title)
                if cand and not _looks_role_prefixed_title(cand):
                    hints.append(cand)
        except Exception:
            pass
    for cand in hints:
        if cand:
            return cand
    return ''

def _title_repair_issue(movie: Movie) -> Optional[Dict[str, Any]]:
    old = (getattr(movie, 'title', '') or '').strip()
    if not old:
        return None
    candidate = _latest_title_hint_for_movie(movie) or _clean_movie_title_candidate(old)
    candidate = (candidate or '').strip()
    if not candidate or candidate.lower() == old.lower():
        return None
    conflict = None
    try:
        conflict = (
            Movie.query
            .filter(Movie.id != movie.id)
            .filter(func.lower(Movie.title) == candidate.lower())
            .filter(Movie.year == movie.year)
            .filter(Movie.lang == movie.lang)
            .first()
        )
    except Exception:
        conflict = None
    return {
        'movie': movie,
        'old_title': old,
        'new_title': candidate,
        'conflict': conflict,
        'source': 'group-hint' if _latest_title_hint_for_movie(movie) else 'cleanup',
    }

def find_repairable_movie_titles(q: str = '', limit: int = 20, include_archived: bool = True) -> List[Dict[str, Any]]:
    raw = (q or '').strip()
    rows_q = Movie.query if include_archived else _active_movie_query()
    if raw:
        like = f"%{raw}%"
        rows_q = rows_q.filter((Movie.title.ilike(like)) | (Movie.code.ilike(like)))
    rows = rows_q.order_by(Movie.updated_at.desc().nullslast(), Movie.id.desc()).limit(max(1, min(int(limit or 20), 200))).all()
    out: List[Dict[str, Any]] = []
    for movie in rows:
        issue = _title_repair_issue(movie)
        if issue:
            out.append(issue)
    return out

def repair_movie_title_db(movie: Movie, *, actor_source: str = 'tg', actor_name: str = 'repair_movie_title') -> Dict[str, Any]:
    issue = _title_repair_issue(movie)
    if not issue:
        return {'changed': False, 'reason': 'no_change', 'movie': movie}
    if issue.get('conflict'):
        return {'changed': False, 'reason': 'conflict', 'movie': movie, 'issue': issue}
    old = issue['old_title']
    new = issue['new_title']
    movie.title = new
    movie.updated_at = _now_utc()
    _learn_movie_alias(movie, old, source='title_repair')
    db.session.commit()
    try:
        record_movie_event(movie, 'TITLE_REPAIR', f'Title repaired: {old} → {new}', detail=f"source={issue.get('source')}", actor_source=actor_source, actor_name=actor_name)
    except Exception:
        pass
    return {'changed': True, 'reason': 'ok', 'movie': movie, 'old_title': old, 'new_title': new, 'issue': issue}


def _alias_norm(raw: str | None) -> str:
    return (_clean_movie_title_candidate(raw) or '').strip().lower()


def _learn_movie_alias(movie: Movie | None, raw_alias: str | None, *, source: str = 'auto') -> Optional[MovieAlias]:
    if not movie:
        return None
    alias_clean = (_clean_movie_title_candidate(raw_alias) or '').strip()
    if not alias_clean:
        return None
    if alias_clean.lower() == ((movie.title or '').strip().lower()):
        return None
    year = str(getattr(movie, 'year', '') or '').strip() or None
    lang = _slug_lang(getattr(movie, 'lang', '') or DEFAULT_LANG)
    norm = alias_clean.lower()
    row = (
        MovieAlias.query
        .filter(MovieAlias.alias_norm == norm)
        .filter(MovieAlias.year == year)
        .filter(MovieAlias.lang == lang)
        .first()
    )
    if row:
        if row.movie_id == movie.id:
            if source and row.source != source:
                row.source = source
            return row
        return row
    row = MovieAlias(movie_id=movie.id, alias=alias_clean, alias_norm=norm, year=year, lang=lang, source=source)
    db.session.add(row)
    db.session.flush()
    return row


def find_movie_aliases(movie: Movie, limit: int = 50) -> List[MovieAlias]:
    if not movie:
        return []
    return (
        MovieAlias.query
        .filter_by(movie_id=movie.id)
        .order_by(MovieAlias.created_at.desc(), MovieAlias.id.desc())
        .limit(max(1, min(int(limit or 50), 200)))
        .all()
    )


def add_movie_alias_db(movie: Movie, alias: str, *, source: str = 'manual') -> Dict[str, Any]:
    clean = (_clean_movie_title_candidate(alias) or '').strip()
    if not movie or not clean:
        return {'changed': False, 'reason': 'empty'}
    if clean.lower() == ((movie.title or '').strip().lower()):
        return {'changed': False, 'reason': 'same_title'}
    year = str(getattr(movie, 'year', '') or '').strip() or None
    lang = _slug_lang(getattr(movie, 'lang', '') or DEFAULT_LANG)
    existing = (
        MovieAlias.query
        .filter(MovieAlias.alias_norm == clean.lower())
        .filter(MovieAlias.year == year)
        .filter(MovieAlias.lang == lang)
        .first()
    )
    if existing and existing.movie_id != movie.id:
        other = Movie.query.filter_by(id=existing.movie_id).first()
        return {'changed': False, 'reason': 'conflict', 'existing': existing, 'movie': other}
    row = _learn_movie_alias(movie, clean, source=source)
    db.session.commit()
    try:
        record_movie_event(movie, 'ALIAS_ADD', f'Alias added: {clean}', actor_source='web' if source.startswith('web') else 'tg', actor_name=source)
    except Exception:
        pass
    return {'changed': True, 'reason': 'ok', 'alias': row}


def delete_movie_alias_db(alias_id: int) -> Dict[str, Any]:
    row = MovieAlias.query.filter_by(id=int(alias_id)).first()
    if not row:
        return {'changed': False, 'reason': 'not_found'}
    movie = Movie.query.filter_by(id=row.movie_id).first()
    alias_txt = row.alias
    db.session.delete(row)
    db.session.commit()
    try:
        if movie:
            record_movie_event(movie, 'ALIAS_DELETE', f'Alias deleted: {alias_txt}', actor_source='web', actor_name='movie_aliases_delete')
    except Exception:
        pass
    return {'changed': True, 'reason': 'ok', 'alias': alias_txt, 'movie': movie}


def _search_movie_alias_rows(query: str, year: str | None = None, *, include_archived: bool = False, limit: int = 8, exact_only: bool = False) -> List[MovieAlias]:
    title = (_clean_movie_title_candidate(query) or '').strip()
    if not title:
        return []
    rows_q = MovieAlias.query.join(Movie, Movie.id == MovieAlias.movie_id)
    if not include_archived:
        rows_q = rows_q.filter((Movie.is_archived.is_(False)) | (Movie.is_archived.is_(None)))
    if exact_only:
        rows_q = rows_q.filter(MovieAlias.alias_norm == title.lower())
    else:
        rows_q = rows_q.filter(MovieAlias.alias.ilike(f"%{title}%"))
    if year:
        rows_q = rows_q.filter(MovieAlias.year == str(year))
    return rows_q.order_by(MovieAlias.created_at.desc(), MovieAlias.id.desc()).limit(max(1, min(int(limit or 8), 100))).all()


def _movies_from_alias_rows(rows: List[MovieAlias]) -> List[Movie]:
    seen = set()
    out = []
    for row in rows:
        try:
            mid = int(row.movie_id)
        except Exception:
            continue
        if mid in seen:
            continue
        movie = Movie.query.filter_by(id=mid).first()
        if movie:
            seen.add(mid)
            out.append(movie)
    return out

def _nearest_latest_candidate(context: ContextTypes.DEFAULT_TYPE, chat_id: int, now: datetime) -> Optional[Dict[str, Any]]:
    store = context.bot_data.get("movie_candidates", {})
    dq = store.get(chat_id)
    if not dq:
        return None
    ttl = timedelta(hours=GROUP_CTX_TTL_HOURS)
    # iterate from newest to oldest
    for item in reversed(dq):
        ts = item.get("detected_at")
        if not isinstance(ts, datetime):
            continue
        if now - ts <= ttl:
            return item
    return None
def _find_cached_candidate(context: ContextTypes.DEFAULT_TYPE, chat_id: int, now: datetime) -> Optional[Dict[str, Any]]:
    """DB-backed fallback for group movie context.
    The bot normally keeps recent detections in `chat_data` (in-memory). On deploy/restart,
    that cache is gone, but the group may still send the role list referencing the earlier
    forwarded video/file. This helper finds the latest non-expired `GroupMovieContext` row
    for the group and returns it in the same dict-shape used by the in-memory cache.
    """
    try:
        row = (
            GroupMovieContext.query
            .filter(GroupMovieContext.tg_chat_id == chat_id)
            .filter((GroupMovieContext.expires_at.is_(None)) | (GroupMovieContext.expires_at >= now))
            .order_by(GroupMovieContext.detected_at.desc())
            .first()
        )
        if not row:
            return None
        return {
            "title": row.title,
            "year": row.year,
            "lang": row.lang,
            "file_name": row.source_file_name,
            "msg_id": row.source_message_id,
            "detected_at": row.detected_at,
        }
    except Exception as e:
        log_event("ERROR", "tg.ctx_lookup", f"ctx_lookup_db_error chat_id={chat_id} err={str(e)}")
        return None
def _suggest_assignments(project_key: str, roles: List[Tuple[str, int]]) -> List[Dict[str, Any]]:
    """Return suggestion list: [{role, lines, vo}]"""
    suggestions: List[Dict[str, Any]] = []
    # One role -> one VO: aggregate duplicates like man-1 repeated per character.
    agg: dict[str, int] = {}
    for role, lines in roles:
        key = norm_role(role)
        agg[key] = agg.get(key, 0) + int(lines or 0)
    roles = sorted(agg.items(), key=lambda x: x[0])
    used = set()
    load = movie_load(project_key)
    # Workload across projects (used by pick_vo preference rules).
    from sqlalchemy import func
    rows = (
        db.session.query(Assignment.vo, func.count(func.distinct(Assignment.project)))
        .group_by(Assignment.vo)
        .all()
    )
    project_counts = {vo: int(cnt or 0) for vo, cnt in rows}
    for role, lines in roles:
        gender = role_gender(role)
        candidates = VOTeam.query.filter_by(active=True, gender=gender).all()
        v = pick_vo(candidates, used, load, project_counts) if candidates else None
        vo_name = v.name if v else "(unassigned)"
        if v:
            used.add(v.name)
        suggestions.append({"role": role, "lines": int(lines or 0), "vo": vo_name})
    return suggestions
def _format_suggestion_preview(title: str, year: str, lang: str, suggestions: List[Dict[str, Any]]) -> str:
    lines = [
        "🧠 *Auto-detect (Option A)*",
        f"🎬 *{title} ({year})* — *{lang_display(lang)}*",
        "",
        "*Preview assignments (approve to apply):*",
    ]
    for s in suggestions[:20]:
        lines.append(f"• `{s['role']}` → *{s['vo']}* ({s['lines']} lines)")
    if len(suggestions) > 20:
        lines.append(f"… +{len(suggestions)-20} more")
    lines.append("")
    lines.append(f"⏳ Expires in *{GROUP_CTX_TTL_HOURS}h* (needs admin approval)")
    return "\n".join(lines)
# Role list quick detector (text blocks without code)
def _load_import_req_roles(req: GroupRoleImportRequest) -> List[Tuple[str, int]]:
    try:
        rows = json.loads(req.roles_json or '[]')
        out = []
        for row in rows or []:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                out.append((str(row[0]), int(row[1] or 0)))
        if out:
            return out
    except Exception:
        pass
    return parse_lines(req.roles_text or '')
def _load_import_req_suggestions(req: GroupRoleImportRequest) -> List[Dict[str, Any]]:
    try:
        rows = json.loads(req.suggested_json or '[]')
        out = []
        for row in rows or []:
            if isinstance(row, dict):
                out.append({
                    'role': str(row.get('role') or ''),
                    'lines': int(row.get('lines') or 0),
                    'vo': str(row.get('vo') or '').strip() or '(unassigned)',
                })
        if out:
            return out
    except Exception:
        pass
    return []
def _refresh_role_import_request(req: GroupRoleImportRequest, *, commit: bool = True) -> List[Dict[str, Any]]:
    roles = parse_lines(req.roles_text or '')
    if not roles:
        raise ValueError('Parsed 0 roles from roles_text')
    lang = _slug_lang((req.lang or DEFAULT_LANG).strip() or DEFAULT_LANG)
    project_key = f"{req.title} ({req.year}) [{lang}]"
    suggestions = _suggest_assignments(project_key, roles)
    req.roles_json = json.dumps(roles)
    req.suggested_json = json.dumps(suggestions)
    if commit:
        db.session.add(req)
        db.session.commit()
    return suggestions
def _import_review_keyboard(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton('⚡ 12h', callback_data=f'imp|preview|{req_id}|superurgent'),
            InlineKeyboardButton('✅ 24h', callback_data=f'imp|preview|{req_id}|urgent'),
        ],
        [
            InlineKeyboardButton('🕒 36h', callback_data=f'imp|preview|{req_id}|nonurgent'),
            InlineKeyboardButton('🌿 48h', callback_data=f'imp|preview|{req_id}|flexible'),
        ],
        [
            InlineKeyboardButton('❌ Reject', callback_data=f'imp|reject|{req_id}'),
            InlineKeyboardButton('🔄 Refresh', callback_data=f'imp|refresh|{req_id}'),
        ],
    ])

def _import_mode_preview_keyboard(req_id: int, mode: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f'✅ Confirm {_priority_mode_hours(mode)}h', callback_data=f'imp|approve|{req_id}|{mode}'),
        ],
        [
            InlineKeyboardButton('↩️ Back', callback_data=f'imp|show|{req_id}'),
            InlineKeyboardButton('❌ Reject', callback_data=f'imp|reject|{req_id}'),
        ],
    ])
def _admin_import_review_text(req: GroupRoleImportRequest) -> str:
    roles = _load_import_req_roles(req)
    suggestions = _load_import_req_suggestions(req)
    total_lines = sum(int(lines or 0) for _, lines in roles)
    raw_lines = [ln.strip() for ln in (req.roles_text or '').splitlines() if ln.strip()]
    existing = (
        Movie.query
        .filter(func.lower(Movie.title) == (req.title or '').strip().lower())
        .filter(Movie.year == (req.year or ''))
        .filter(func.lower(func.coalesce(Movie.lang, '')) == ((req.lang or DEFAULT_LANG).strip().lower()))
        .first()
    )
    status = (req.status or 'PENDING').upper()
    lines = [
        '🛡️ Admin review — auto-detected roles',
        f'Request ID: {req.id}',
        f'Status: {status}',
        f'Movie: {fmt_title_year(req.title, req.year)} — {lang_display(req.lang or DEFAULT_LANG)}',
        f'Group chat: {req.tg_chat_id}',
        f'Requested by: {req.requested_by_name or req.requested_by_tg_id or "-"}',
        f'Created: {req.created_at.strftime("%Y-%m-%d %H:%M UTC") if req.created_at else "-"}',
        f'Expires: {req.expires_at.strftime("%Y-%m-%d %H:%M UTC") if req.expires_at else "-"}',
        f'Existing movie: {existing.code if existing else "(new movie will be created/bound)"}',
        '',
        f'Roles detected: {len(roles)}',
        f'Total lines: {total_lines}',
        '',
        'Suggested assignments',
    ]
    if suggestions:
        for row in suggestions[:12]:
            role = norm_role(row.get('role') or '') or (row.get('role') or '-')
            who = (row.get('vo') or '-').strip() or '-'
            line_count = int(row.get('lines') or 0)
            lines.append(f'• {role} → {who} ({line_count} lines)')
        if len(suggestions) > 12:
            lines.append(f'… +{len(suggestions) - 12} more')
    else:
        lines.append('• No suggestions available')
    lines.extend(['', 'Raw roles preview'])
    for ln in raw_lines[:10]:
        lines.append(f'• {ln}')
    if len(raw_lines) > 10:
        lines.append(f'… +{len(raw_lines) - 10} more raw lines')
    lines.extend([
        '',
        'Tap 12h / 24h / 36h / 48h to preview that mode first.',
        'This review card is admin-only.',
        'Public VO group stays clean until you approve.',
    ])
    return "\n".join(lines)

def _admin_import_mode_preview_text(req: GroupRoleImportRequest, mode: str) -> str:
    roles = _load_import_req_roles(req)
    suggestions = _load_import_req_suggestions(req)
    total_lines = sum(int(lines or 0) for _, lines in roles)
    submitted = 0
    pending = 0
    for row in suggestions:
        if str(row.get('vo') or '').strip() and str(row.get('vo') or '').strip() != '(unassigned)':
            pending += 1
    due_label = f'{_priority_mode_label(mode)} • {_priority_mode_hours(mode)}h'
    lines = [
        '🔎 Preview role import',
        f'Request ID: {req.id}',
        f'Movie: {fmt_title_year(req.title, req.year)} — {lang_display(req.lang or DEFAULT_LANG)}',
        f'Group chat: {req.tg_chat_id}',
        f'Mode: {due_label}',
        '',
        f'Roles detected: {len(roles)}',
        f'Total lines: {total_lines}',
        f'Assignments to create/update: {len(suggestions)}',
        f'Assigned suggestions: {pending}',
        f'Unassigned suggestions: {max(0, len(suggestions) - pending)}',
        '',
        'Preview assignments',
    ]
    if suggestions:
        for row in suggestions[:12]:
            role = norm_role(row.get('role') or '') or (row.get('role') or '-')
            who = (row.get('vo') or '-').strip() or '-'
            line_count = int(row.get('lines') or 0)
            lines.append(f'• {role} → {who} ({line_count} lines)')
        if len(suggestions) > 12:
            lines.append(f'… +{len(suggestions) - 12} more')
    else:
        lines.append('• No suggestions available')
    lines.extend([
        '',
        'This is still a preview only.',
        'Confirm to apply assignments, deadline mode, and public card update.',
    ])
    return "\n".join(lines)
ROLE_LINE_RE = re.compile(r"\b(?:man|male|m|fem|female|f)\s*[-]?\s*\d{1,2}\b", re.I)
def _now_utc() -> datetime:
    return datetime.utcnow()
def _upsert_translator_seen(update: Update) -> None:
    """Track translators/submitters that DM the bot.
    This lets the web roster auto-fill Telegram ID + last seen even if admin only keyed in the name.
    """
    try:
        u = update.effective_user
        if not u or not getattr(u, 'id', None):
            return
        uid = int(u.id)
        uname = (u.username or '').strip().lstrip('@') or None
        display = (u.full_name or u.first_name or uname or '').strip() or (uname or f"tg_{uid}")
        tr = Translator.query.filter_by(tg_user_id=uid).first()
        if not tr and uname:
            tr = Translator.query.filter(Translator.tg_username.ilike(uname)).first()
        if not tr:
            tr = Translator.query.filter(Translator.name.ilike(display)).first()
        if not tr:
            tr = Translator(name=display, tg_user_id=uid, tg_username=uname, active=True, last_seen_at=_now_utc())
            db.session.add(tr)
        else:
            if uname:
                tr.tg_username = uname
            if not tr.tg_user_id:
                tr.tg_user_id = uid
            tr.last_seen_at = _now_utc()
            if tr.active is None:
                tr.active = True
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception as e:
            detail["forward"] = {"enabled": True, "outbox_chat_id": int(SRT_OUTBOX_CHAT_ID), "forwarded": False, "error": str(e)}
def _upsert_vo_seen(update: Update) -> None:
    """Best-effort: if a VO submits media in a group, link their tg_user_id to the VO roster.
    Matching priority:
      1) vo_team.tg_user_id == uid
      2) vo_team.tg_username == uploader username
      3) vo_team.name == uploader display/full name (case-insensitive)
    """
    try:
        u = update.effective_user
        if not u or not getattr(u, 'id', None):
            return
        uid = int(u.id)
        uname = (u.username or '').strip().lstrip('@') or None
        display = (u.full_name or u.first_name or uname or '').strip() or (uname or f"tg_{uid}")
        vo = VOTeam.query.filter_by(tg_user_id=uid).first()
        if not vo and uname:
            vo = VOTeam.query.filter(VOTeam.tg_username.ilike(uname)).first()
        if not vo and display:
            vo = VOTeam.query.filter(VOTeam.name.ilike(display)).first()
        if not vo:
            return
        if uname:
            vo.tg_username = uname
        if not vo.tg_user_id:
            vo.tg_user_id = uid
        vo.last_seen_at = _now_utc()
        if vo.active is None:
            vo.active = True
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception as e:
            detail["forward"] = {"enabled": True, "outbox_chat_id": int(SRT_OUTBOX_CHAT_ID), "forwarded": False, "error": str(e)}
def _is_dm(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.type == ChatType.PRIVATE)
def _is_group(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP))
def _extract_movie_code(text: str) -> Optional[str]:
    if not text:
        return None
    m = MOVIE_CODE_RE.search(text)
    return m.group(0).upper() if m else None
def _slug_lang(s: str) -> str:
    """Normalize language tags to stable 2-letter codes used in DB."""
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "", s).replace("_", "").replace("-", "")
    # Bengali
    if s in ("bn", "bengali", "bangla"):
        return "bn"
    # English
    if s in ("en", "eng", "english"):
        return "en"
    # Malay
    if s in ("ms", "malay", "bahasamelayu", "bmmelayu", "melayu", "bahasamalaysia"):
        return "ms"
    # Indonesian (kept for compatibility)
    if s in ("id", "indo", "indonesian", "bahasaindonesia"):
        return "id"
    return s[:12] if s else "bn"
LANG_DISPLAY = {
    "bn": "Bengali",
    "en": "English",
    "ms": "Malay",
    "id": "Indonesian",
}
def lang_display(code: str) -> str:
    c = _slug_lang(code or "")
    return LANG_DISPLAY.get(c, c.upper() if c else "Bengali")
def _make_movie_code(lang: str) -> str:
    lang2 = _slug_lang(lang).upper()
    day = _now_utc().strftime("%y%m%d")
    prefix = f"{lang2}-{day}-"
    n = Movie.query.filter(Movie.code.like(f"{prefix}%")).count() + 1
    return f"{prefix}{n:02d}"
def _parse_movie_text(text: str) -> Optional[Tuple[str, Optional[int], str]]:
    """Accept:
      "Title (2025) - bn"
      "Title (2025) | bn"
      "Title 2025 - bn"
      "Title - bn"
      "Title (2025)"
      Or code-only: "BN-260129-01"
    """
    t = (text or "").strip()
    if not t:
        return None
    # code-only
    if re.fullmatch(r"[A-Za-z]{2,5}-\d{6}-\d{2}", t):
        return ("__CODE__", None, t.upper())
    t = re.sub(r"\s+", " ", t)
    # lang at end
    m = re.search(r"(?:\s*[-|—]\s*)([A-Za-z]{2,12})\s*$", t)
    lang = "bn"
    if m:
        lang = m.group(1)
        t = t[: m.start()].strip()
    # year in parentheses
    y = None
    m2 = re.search(r"\((\d{4})\)", t)
    if m2:
        y = int(m2.group(1))
        title = re.sub(r"\s*\(\d{4}\)\s*", " ", t).strip()
        return (title, y, _slug_lang(lang))
    # bare year at end
    m3 = re.search(r"(?:^|\s)(\d{4})\s*$", t)
    if m3:
        y = int(m3.group(1))
        title = re.sub(r"(?:^|\s)\d{4}\s*$", "", t).strip()
        return (title, y, _slug_lang(lang))
    title = t.strip()
    if len(title) < 3:
        return None
    return (title, None, _slug_lang(lang))
def parse_srt_filename(filename: str) -> Dict[str, Optional[str]]:
    """Parse translated SRT metadata from filename.
    Supports:
      - Example 3: "Inside Out (2015).srt" (no submitter)
      - "Dune (2021) (Shazia).srt"
      - "Avatar (2009) [BN] (Rezaul).srt"
    Returns: title, year, lang, submitter
    """
    base = (filename or "").strip()
    # remove extension
    name = re.sub(r"\.(srt|ass|vtt)$", "", base, flags=re.I).strip()
    # language like [BN]
    lang = None
    mlang = LANG_RE.search(name)
    if mlang:
        lang = mlang.group(1).upper()
        name = LANG_RE.sub("", name).strip()
    # year like (2015)
    year = None
    my = YEAR_RE.search(name)
    if my:
        year = my.group(1)
    # submitter: last (...) that is NOT the year
    submitter = None
    parens = PAREN_RE.findall(name)
    for p in reversed(parens):
        p_clean = p.strip()
        if year and p_clean == year:
            continue
        # ignore common non-name tags
        if re.fullmatch(r"\d+p", p_clean.lower()):
            continue
        submitter = p_clean
        break
    # title: part before the year group
    title = name
    if year and f"({year})" in name:
        title = name.split(f"({year})", 1)[0].strip()
    title = re.sub(r"\s+", " ", title).strip(" -_")
    return {"title": title or None, "year": year, "lang": lang, "submitter": submitter}
def _human_translator_srt_log(detail: Dict[str, Any]) -> str:
    """Human-readable log for translator SRT submissions.
    The full JSON is stored separately (in the `traceback` field) to keep the UI readable.
    """
    try:
        t = detail.get("telegram") or {}
        d = detail.get("document") or {}
        p = detail.get("parse") or {}
        mv = detail.get("movie") or {}
        tt = detail.get("translation_task") or {}
        fw = detail.get("forward") or {}
        res = detail.get("result") or {}
        errs = detail.get("errors") or []
        lines: List[str] = []
        lines.append("📥 Translator SRT submission (DM)")
        lines.append(f"UTC: {detail.get('ts_utc')}")
        lines.append(f"App: {detail.get('app_version')}")
        lines.append("")
        lines.append("Telegram")
        lines.append(
            f"- user_id: {t.get('user_id')}  username: {t.get('username') or '-'}  name: {t.get('full_name') or '-'}  admin: {t.get('is_admin')}"
        )
        lines.append(f"- chat_id: {t.get('chat_id')}  message_id: {t.get('message_id')}  date: {t.get('date')}")
        lines.append("")
        lines.append("Document")
        lines.append(f"- file: {d.get('file_name')}  size: {d.get('file_size')}  mime: {d.get('mime_type')}")
        lines.append(f"- file_id: {d.get('file_id')}  unique: {d.get('file_unique_id')}")
        cap = detail.get("caption")
        if cap:
            lines.append("")
            lines.append("Caption (truncated 2048 chars)")
            lines.append(str(cap))
        lines.append("")
        lines.append("Parsed")
        lines.append(f"- code_from_any: {p.get('code_from_any')}")
        lines.append(
            f"- title: {p.get('title')}  year: {p.get('year')}  lang_tag: {p.get('lang_tag')}  resolved_lang: {p.get('resolved_lang')}"
        )
        lines.append(f"- submitter: {p.get('resolved_submitter')}")
        lines.append("")
        lines.append("Movie")
        lines.append(
            f"- id: {mv.get('movie_id')}  code: {mv.get('code')}  title: {mv.get('title')}  year: {mv.get('year')}  lang: {mv.get('lang')}  placeholder: {mv.get('created_placeholder')}"
        )
        lines.append("")
        lines.append("TranslationTask auto-complete")
        lines.append(f"- translator_id: {tt.get('translator_id')}  name: {tt.get('translator_name')}")
        lines.append(f"- candidate_count: {tt.get('candidate_count')}")
        if tt.get("matched_task_id"):
            lines.append(f"- matched_task_id: {tt.get('matched_task_id')}  match_method: {tt.get('match_method')}")
            b = tt.get("before") or {}
            a = tt.get("after") or {}
            lines.append(f"- before: status={b.get('status')} completed_at={b.get('completed_at')}")
            lines.append(f"- after:  status={a.get('status')} completed_at={a.get('completed_at')}")
        else:
            note = tt.get("note") or ""
            lines.append(f"- matched_task_id: None  note: {note}")
        lines.append("")
        lines.append("Forwarding")
        lines.append(
            f"- enabled: {fw.get('enabled')}  outbox_chat_id: {fw.get('outbox_chat_id')}  forwarded: {fw.get('forwarded')}  outbox_message_id: {fw.get('outbox_message_id')}"
        )
        if fw.get("error"):
            lines.append(f"- error: {fw.get('error')}")
        lines.append("")
        lines.append("Result")
        lines.append(
            f"- submission_id: {res.get('submission_id')}  queue_status: {res.get('queue_status')}  movie_status: {res.get('movie_status')}  task_completed: {res.get('translation_task_completed')}  forwarded: {res.get('forwarded')}"
        )
        if errs:
            lines.append("")
            lines.append("Errors")
            for e in errs:
                lines.append(f"- {e}")
        return "\n".join(lines)
    except Exception:
        # Never crash for logging.
        return "📥 Translator SRT submission (DM) — (log format error)"
def _human_vo_submission_log(detail: Dict[str, Any]) -> str:
    """Human-readable log for VO submissions (group media)."""
    try:
        t = detail.get("telegram") or {}
        media = detail.get("media") or {}
        mv = detail.get("movie") or {}
        res = detail.get("result") or {}
        errs = detail.get("errors") or []
        lines: List[str] = []
        lines.append("🎙️ VO submission (group)")
        lines.append(f"UTC: {detail.get('ts_utc')}")
        lines.append(f"App: {detail.get('app_version')}")
        lines.append("")
        lines.append("Telegram")
        lines.append(f"- user_id: {t.get('user_id')}  username: {t.get('username') or '-'}  name: {t.get('full_name') or '-'}")
        lines.append(f"- chat_id: {t.get('chat_id')}  message_id: {t.get('message_id')}  date: {t.get('date')}")
        lines.append("")
        lines.append("Movie")
        lines.append(f"- code: {mv.get('code')}  title: {mv.get('title')}  year: {mv.get('year')}  lang: {mv.get('lang')}")
        lines.append("")
        lines.append("Media")
        lines.append(f"- type: {media.get('media_type')}  file_name: {media.get('file_name') or '-'}  file_id: {media.get('file_id') or '-'}")
        lines.append("")
        lines.append("Detected roles")
        lines.append(f"- method: {res.get('detect_method')}  roles_count: {res.get('roles_count')}  saved_count: {res.get('saved_count')}  skipped_dupe: {res.get('skipped_dupe_count')}")
        if res.get("saved_roles"):
            for r in res.get("saved_roles")[:30]:
                lines.append(f"  - {r.get('role')} lines={r.get('lines')} id={r.get('id')}")
            if len(res.get("saved_roles")) > 30:
                lines.append(f"  ... ({len(res.get('saved_roles'))-30} more)")
        lines.append("")
        lines.append("Post-submit")
        lines.append(f"- wait_embed_triggered: {res.get('wait_embed_triggered')}  movie_status: {res.get('movie_status')}")
        if errs:
            lines.append("")
            lines.append("Errors")
            for e in errs:
                lines.append(f"- {e}")
        return "\n".join(lines)
    except Exception:
        return "🎙️ VO submission (group) — (log format error)"
def _human_group_srt_log(detail: Dict[str, Any]) -> str:
    """Human-readable log for SRT posted inside a group (auto queue)."""
    try:
        t = detail.get("telegram") or {}
        d = detail.get("document") or {}
        mv = detail.get("movie") or {}
        res = detail.get("result") or {}
        errs = detail.get("errors") or []
        lines: List[str] = []
        lines.append("📥 Group SRT → Queue")
        lines.append(f"UTC: {detail.get('ts_utc')}  App: {detail.get('app_version')}")
        lines.append("")
        lines.append("Telegram")
        lines.append(f"- user_id: {t.get('user_id')}  username: {t.get('username') or '-'}  name: {t.get('full_name') or '-'}")
        lines.append(f"- chat_id: {t.get('chat_id')}  message_id: {t.get('message_id')}  date: {t.get('date')}  chat_type: {t.get('chat_type')}")
        lines.append("")
        lines.append("Document")
        lines.append(f"- file: {d.get('file_name')}  size: {d.get('file_size')}  mime: {d.get('mime_type')}")
        lines.append(f"- file_id: {d.get('file_id')}  unique: {d.get('file_unique_id')}")
        lines.append("")
        lines.append("Movie")
        lines.append(f"- code: {mv.get('code')}  title: {mv.get('title')}  year: {mv.get('year')}  lang: {mv.get('lang')}")
        lines.append("")
        lines.append("Result")
        lines.append(f"- submission_id: {res.get('submission_id')}  deduped: {res.get('deduped')}  movie_status: {res.get('movie_status')}")
        if errs:
            lines.append("")
            lines.append("Errors")
            for e in errs:
                lines.append(f"- {e}")
        return "\n".join(lines)
    except Exception:
        return "📥 Group SRT → Queue — (log format error)"
def _human_submit_mode_log(detail: Dict[str, Any]) -> str:
    """Human-readable log for /submit mode submissions (DM)."""
    try:
        t = detail.get("telegram") or {}
        mv = detail.get("movie") or {}
        res = detail.get("result") or {}
        errs = detail.get("errors") or []
        lines: List[str] = []
        lines.append("📝 /submit mode submission (DM)")
        lines.append(f"UTC: {detail.get('ts_utc')}  App: {detail.get('app_version')}")
        lines.append("")
        lines.append("Telegram")
        lines.append(f"- user_id: {t.get('user_id')}  username: {t.get('username') or '-'}  name: {t.get('full_name') or '-'}")
        lines.append(f"- chat_id: {t.get('chat_id')}  message_id: {t.get('message_id')}")
        lines.append("")
        lines.append("Movie")
        lines.append(f"- token: {detail.get('token')}")
        lines.append(f"- resolved_code: {mv.get('code')}  title: {mv.get('title')}  year: {mv.get('year')}  lang: {mv.get('lang')}")
        lines.append("")
        lines.append("Submission")
        lines.append(f"- type: {res.get('content_type')}  file_name: {res.get('file_name') or '-'}  text_len: {res.get('text_len')}")
        lines.append(f"- submission_id: {res.get('submission_id')}  forwarded: {res.get('forwarded')}")
        if errs:
            lines.append("")
            lines.append("Errors")
            for e in errs:
                lines.append(f"- {e}")
        return "\n".join(lines)
    except Exception:
        return "📝 /submit mode submission (DM) — (log format error)"
def tg_submitter_display(update: Update) -> str:
    """Fallback submitter for Example 3 (no submitter in filename)."""
    u = update.effective_user
    if not u:
        return "unknown"
    if u.username:
        return f"@{u.username}"
    return (u.full_name or str(u.id)).strip()
def _is_owner(update: Update) -> bool:
    if not OWNER_TG_ID:
        return False
    try:
        return str(update.effective_user.id) == str(int(OWNER_TG_ID))
    except Exception:
        return False
def _is_admin(update: Update) -> bool:
    """Admin whitelist:
    - OWNER_TG_ID always allowed
    - AdminTelegramUser table
    """
    if _is_owner(update):
        return True
    uid = getattr(update.effective_user, "id", None)
    if not uid:
        return False
    row = AdminTelegramUser.query.filter_by(tg_user_id=uid, active=True).first()
    return bool(row)
def _require_admin(update: Update) -> bool:
    if _is_admin(update):
        return True
    # DM only — don't spam group
    try:
        if update.effective_message:
            # short, no markdown
            update.effective_message.reply_text("❌ Not allowed")
    except Exception:
        pass
    return False
def _find_translator_for_user(update: Update) -> Optional[Translator]:
    """Best-effort resolve current Telegram user to Translator roster row."""
    try:
        u = update.effective_user
        if not u or not getattr(u, 'id', None):
            return None
        uid = int(u.id)
        uname = (u.username or '').strip().lstrip('@') or None
        display = (u.full_name or u.first_name or uname or '').strip()
        tr = Translator.query.filter_by(tg_user_id=uid).first()
        if not tr and uname:
            tr = Translator.query.filter(Translator.tg_username.ilike(uname)).first()
        if not tr and display:
            tr = Translator.query.filter(Translator.name.ilike(display)).first()
        return tr
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return None
def _find_vo_for_user(update: Update) -> Optional[VOTeam]:
    """Best-effort resolve current Telegram user to VO roster row."""
    try:
        u = update.effective_user
        if not u or not getattr(u, 'id', None):
            return None
        uid = int(u.id)
        uname = (u.username or '').strip().lstrip('@') or None
        display = (u.full_name or u.first_name or uname or '').strip()
        vo = VOTeam.query.filter_by(tg_user_id=uid).first()
        if not vo and uname:
            vo = VOTeam.query.filter(VOTeam.tg_username.ilike(uname)).first()
        if not vo and display:
            vo = VOTeam.query.filter(VOTeam.name.ilike(display)).first()
        return vo
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return None
def _assignment_project_counts() -> Dict[str, int]:
    from sqlalchemy import func
    rows = (
        db.session.query(Assignment.vo, func.count(func.distinct(Assignment.project)))
        .group_by(Assignment.vo)
        .all()
    )
    return {vo: int(cnt or 0) for vo, cnt in rows}
def _normalize_roles_blob(text: str) -> str:
    """Accept newline/comma/semicolon separated role buckets from Telegram commands."""
    work = (text or '').replace(';', '\n').replace(',', '\n')
    work = re.sub(r"\r\n?", "\n", work)
    return work
def _auto_assign_movie_roles(movie: Movie, roles: List[Tuple[str, int]], urgent: bool = True, replace_existing: bool = True, priority_mode: str | None = None) -> List[Dict[str, Any]]:
    """Create assignments for one movie using the same picker rules as web quick-start."""
    project = (movie.code or '').strip()
    if not project:
        raise ValueError('Movie code missing')
    agg: dict[str, int] = {}
    for role, lines in roles:
        nr = norm_role(role) or role
        agg[nr] = agg.get(nr, 0) + int(lines or 0)
    parsed2 = sorted(agg.items(), key=lambda x: x[0])
    mode = _normalize_priority_mode(priority_mode or ("urgent" if urgent else "nonurgent"))
    default_deadline = _priority_mode_deadline(mode)
    urgent = _priority_mode_urgent_only(mode)
    if replace_existing:
        Assignment.query.filter((Assignment.movie_id == movie.id) | (Assignment.project == project)).delete(synchronize_session=False)
        db.session.flush()
    load = movie_load(project)
    used: set[str] = set()
    project_counts = _assignment_project_counts()
    results: List[Dict[str, Any]] = []
    for role, lines in parsed2:
        gender = role_gender(role)
        q = VOTeam.query.filter_by(active=True, gender=gender)
        if urgent:
            q = q.filter_by(urgent_ok=True)
        picked = pick_vo(q.all(), used, load, project_counts)
        if not picked:
            results.append({"role": role, "lines": int(lines or 0), "vo": None})
            continue
        used.add(picked.name)
        row = Assignment(
            project=project,
            movie_id=movie.id,
            vo=picked.name,
            role=role,
            lines=int(lines or 0),
            urgent=bool(urgent),
            priority_mode=mode,
            deadline_at=default_deadline,
        )
        db.session.add(row)
        results.append({"role": role, "lines": int(lines or 0), "vo": picked.name})
    db.session.commit()
    return results
def upsert_movie(title: str, year: Optional[int], lang: str) -> Movie:
    lang = _slug_lang(lang or "bn")
    year_str = str(year) if year else None
    existing = Movie.query.filter_by(title=title, year=year_str, lang=lang).first()
    if existing:
        if getattr(existing, "is_archived", False):
            existing.is_archived = False
            existing.archived_at = None
            if (existing.status or "").upper() == "ARCHIVED":
                existing.status = "RECEIVED"
            existing.updated_at = _now_utc()
            db.session.commit()
        return existing
    code = _make_movie_code(lang)
    m = Movie(
        code=code,
        title=title,
        year=year_str,
        lang=lang,
        status="RECEIVED",
        received_at=_now_utc(),
        updated_at=_now_utc(),
    )
    db.session.add(m)
    db.session.commit()
    return m
def get_or_create_movie(title: str, year: Optional[int], lang: str) -> tuple[Movie, bool]:
    """Return (movie, created). Uses title+year+lang as the natural key."""
    lang = _slug_lang(lang or "bn")
    year_str = str(year) if year else None
    existing = Movie.query.filter_by(title=title, year=year_str, lang=lang).first()
    if existing:
        if getattr(existing, "is_archived", False):
            existing.is_archived = False
            existing.archived_at = None
            if (existing.status or "").upper() == "ARCHIVED":
                existing.status = "RECEIVED"
            existing.updated_at = _now_utc()
            db.session.commit()
        return existing, False
    return upsert_movie(title, year, lang), True
def _active_movie_query():
    return Movie.query.filter((Movie.is_archived.is_(False)) | (Movie.is_archived.is_(None)))
def _archived_movie_query():
    return Movie.query.filter(Movie.is_archived.is_(True))
def _reactivate_movie_if_archived(movie: Optional[Movie]) -> Optional[Movie]:
    if movie and getattr(movie, "is_archived", False):
        movie.is_archived = False
        movie.archived_at = None
        if (movie.status or "").upper() == "ARCHIVED":
            movie.status = "RECEIVED"
        movie.updated_at = _now_utc()
        db.session.commit()
    return movie
def movie_by_code(code: str, include_archived: bool = False) -> Optional[Movie]:
    q = Movie.query if include_archived else _active_movie_query()
    return q.filter_by(code=(code or "").strip().upper()).first()
def _search_movies(query: str, limit: int = 8) -> List[Movie]:
    q = (query or "").strip()
    if not q:
        return []
    exact_code = _extract_movie_code(q)
    if exact_code:
        m = movie_by_code(exact_code)
        return [m] if m else []
    work = re.sub(r"\s+", " ", q).strip()
    year = None
    m_year = re.search(r"\((\d{4})\)", work)
    if m_year:
        year = m_year.group(1)
        work = re.sub(r"\s*\(\d{4}\)\s*", " ", work).strip()
    else:
        m_year2 = re.search(r"(?:^|\s)(\d{4})\s*$", work)
        if m_year2:
            year = m_year2.group(1)
            work = re.sub(r"(?:^|\s)\d{4}\s*$", "", work).strip()
    title = work.strip()
    if not title:
        return []
    exact_q = _active_movie_query().filter(func.lower(Movie.title) == title.lower())
    if year:
        exact_q = exact_q.filter(Movie.year == str(year))
    exact = exact_q.order_by(Movie.id.desc()).all()
    if exact:
        return exact[:limit]
    alias_exact = _movies_from_alias_rows(_search_movie_alias_rows(title, year, include_archived=False, limit=limit, exact_only=True))
    if alias_exact:
        return alias_exact[:limit]
    contains_q = _active_movie_query().filter(Movie.title.ilike(f"%{title}%"))
    if year:
        contains_q = contains_q.filter(Movie.year == str(year))
    contains = contains_q.order_by(Movie.id.desc()).limit(limit).all()
    if contains:
        return contains
    alias_contains = _movies_from_alias_rows(_search_movie_alias_rows(title, year, include_archived=False, limit=limit, exact_only=False))
    return alias_contains[:limit]
def _search_archived_movies(query: str, limit: int = 8) -> List[Movie]:
    q = (query or "").strip()
    rows_q = _archived_movie_query()
    if not q:
        return rows_q.order_by(Movie.archived_at.desc(), Movie.id.desc()).limit(limit).all()
    exact_code = _extract_movie_code(q)
    if exact_code:
        m = rows_q.filter_by(code=exact_code).first()
        return [m] if m else []
    work = re.sub(r"\s+", " ", q).strip()
    year = None
    m_year = re.search(r"\((\d{4})\)", work)
    if m_year:
        year = m_year.group(1)
        work = re.sub(r"\s*\(\d{4}\)\s*", " ", work).strip()
    title = work.strip()
    if not title:
        return rows_q.order_by(Movie.archived_at.desc(), Movie.id.desc()).limit(limit).all()
    exact_q = rows_q.filter(func.lower(Movie.title) == title.lower())
    if year:
        exact_q = exact_q.filter(Movie.year == str(year))
    exact = exact_q.order_by(Movie.archived_at.desc(), Movie.id.desc()).all()
    if exact:
        return exact[:limit]
    alias_exact = _movies_from_alias_rows(_search_movie_alias_rows(title, year, include_archived=True, limit=limit, exact_only=True))
    alias_exact = [m for m in alias_exact if bool(getattr(m, 'is_archived', False))]
    if alias_exact:
        return alias_exact[:limit]
    contains_q = rows_q.filter(Movie.title.ilike(f"%{title}%"))
    if year:
        contains_q = contains_q.filter(Movie.year == str(year))
    contains = contains_q.order_by(Movie.archived_at.desc(), Movie.id.desc()).limit(limit).all()
    if contains:
        return contains
    alias_contains = _movies_from_alias_rows(_search_movie_alias_rows(title, year, include_archived=True, limit=limit, exact_only=False))
    alias_contains = [m for m in alias_contains if bool(getattr(m, 'is_archived', False))]
    return alias_contains[:limit]
def _resolve_archived_movie_query(query: str) -> tuple[Optional[Movie], List[Movie]]:
    matches = _search_archived_movies(query, limit=8)
    if not matches:
        return None, []
    if len(matches) == 1:
        return matches[0], matches
    exact_code = _extract_movie_code(query)
    if exact_code:
        return matches[0], matches
    normalized = (query or "").strip().lower()
    exact_title = [m for m in matches if (m.title or "").strip().lower() == normalized]
    if len(exact_title) == 1:
        return exact_title[0], matches
    return None, matches
def _archived_lookup_help(query: str, matches: List[Movie]) -> str:
    if not matches:
        return f"❌ Archived movie not found: {query}"
    lines = [f"Found multiple archived movies for: {query}", ""]
    for m in matches[:8]:
        lines.append(f"• {fmt_title_year(m.title, m.year)} [{(m.lang or '').upper() or '-'}] — {m.code}")
    lines.append("")
    lines.append("Use the movie code or type a more specific title with year.")
    return "\n".join(lines)
def _resolve_movie_query(query: str) -> tuple[Optional[Movie], List[Movie]]:
    matches = _search_movies(query, limit=8)
    if not matches:
        return None, []
    if len(matches) == 1:
        return matches[0], matches
    first = matches[0]
    exact_code = _extract_movie_code(query)
    if exact_code:
        return first, matches
    normalized = (query or "").strip().lower()
    exact_title = [m for m in matches if (m.title or "").strip().lower() == normalized]
    if len(exact_title) == 1:
        return exact_title[0], matches
    return None, matches
def _search_any_movies(query: str, limit: int = 8) -> List[Movie]:
    q = (query or '').strip()
    if not q:
        return []
    code = _extract_movie_code(q)
    if code:
        m = movie_by_code(code, include_archived=True)
        return [m] if m else []
    rows = Movie.query.order_by(Movie.updated_at.desc().nullslast(), Movie.id.desc()).all()
    normalized = (q or '').strip().lower()
    exact = [m for m in rows if (m.title or '').strip().lower() == normalized]
    if exact:
        return exact[:limit]
    return [m for m in rows if normalized in ((m.title or '').strip().lower())][:limit]
def _resolve_any_movie_query(query: str) -> tuple[Optional[Movie], List[Movie]]:
    matches = _search_any_movies(query, limit=8)
    if not matches:
        return None, []
    if len(matches) == 1:
        return matches[0], matches
    code = _extract_movie_code(query)
    if code:
        return matches[0], matches
    normalized = (query or '').strip().lower()
    exact_title = [m for m in matches if (m.title or '').strip().lower() == normalized]
    if len(exact_title) == 1:
        return exact_title[0], matches
    return None, matches
def _pending_merge_movie_text(source: Movie, target: Movie, delete_source: bool = False) -> str:
    moved = merge_preview(source, target)
    sev = str(moved.get('severity', 'low') or 'low').upper()
    lines = [
        f"🧬 Confirm movie merge — {source.code} → {target.code}",
        f"Risk: {sev}",
        f"Source: {fmt_title_year(source.title, source.year)} [{source.code}]",
        f"Target: {fmt_title_year(target.title, target.year)} [{target.code}]",
        '',
        'Rows that will move:',
        f"• Assignments: {moved.get('assignments', 0)}",
        f"• VO submissions: {moved.get('vo_submissions', 0)}",
        f"• Translation tasks: {moved.get('translation_tasks', 0)}",
        f"• Translation submissions: {moved.get('translation_submissions', 0)}",
        f"• Group requests: {moved.get('group_requests', 0)}",
        f"• History events: {moved.get('events', 0)}",
    ]
    warnings = list(moved.get('warnings') or [])
    if warnings:
        lines += ['', 'Warnings:']
        for item in warnings[:6]:
            lines.append(f"• {item}")
        extra = len(warnings) - 6
        if extra > 0:
            lines.append(f"• +{extra} more warning(s)")
    else:
        lines += ['', 'Warnings:', '• No major conflicts detected in preview.']
    lines += [
        '',
        f"After merge: source will be {'hard deleted' if delete_source else 'archived as MERGED'}.",
        'Press Confirm to write changes, or Cancel to abort.',
    ]
    return "\n".join(lines)
def _merge_simulation_text(source: Movie, target: Movie) -> str:
    sim = merge_simulation(source, target)
    preview = sim.get('preview') or {}
    lines = [
        f"🧪 Merge simulator — {source.code} → {target.code}",
        f"Risk: {str(preview.get('severity', 'low') or 'low').upper()}",
        f"Source: {fmt_title_year(source.title, source.year)} [{source.code}]",
        f"Target: {fmt_title_year(target.title, target.year)} [{target.code}]",
        '',
        f"Rows moving if merged now: {preview.get('total_rows', 0)}",
        f"• Assignments: {preview.get('assignments', 0)}",
        f"• VO submissions: {preview.get('vo_submissions', 0)}",
        f"• Translation tasks: {preview.get('translation_tasks', 0)}",
        f"• Translation submissions: {preview.get('translation_submissions', 0)}",
        f"• Group requests: {preview.get('group_requests', 0)}",
        f"• History events: {preview.get('events', 0)}",
        '',
        'Compare:',
        f"• Assignment overlap: {', '.join((sim.get('diff') or {}).get('assignment_overlap') or ['-'])}",
        f"• VO overlap: {', '.join((sim.get('diff') or {}).get('vo_overlap') or ['-'])}",
        f"• Translator overlap: {', '.join((sim.get('diff') or {}).get('translator_people_overlap') or ['-'])}",
        f"• Source translator: {(sim.get('source') or {}).get('translator_assigned') or '-'}",
        f"• Target translator: {(sim.get('target') or {}).get('translator_assigned') or '-'}",
        '',
        'Warnings:',
    ]
    warnings = list(preview.get('warnings') or [])
    if warnings:
        for item in warnings[:8]:
            lines.append(f"• {item}")
        extra = len(warnings) - 8
        if extra > 0:
            lines.append(f"• +{extra} more warning(s)")
    else:
        lines.append('• No major conflicts detected in preview.')
    lines += ['', 'This is only a dry run. Use /merge_movie SOURCE | TARGET when you are ready.']
    return '\n'.join(lines)
async def _send_merge_movie_preview(msg, user_id: int, source: Movie, target: Movie, delete_source: bool = False):
    token = _new_pending_action('mg', user_id, target, {'source_code': source.code, 'delete_source': bool(delete_source)})
    await msg.reply_text(
        _pending_merge_movie_text(source, target, delete_source=delete_source),
        reply_markup=_pending_action_keyboard('mg', target.code, token),
        disable_web_page_preview=True,
    )
def _movie_lookup_help(query: str, matches: List[Movie]) -> str:
    if not matches:
        return f"❌ Movie not found: {query}"
    lines = [f"Found multiple movies for: {query}", ""]
    for m in matches[:8]:
        lines.append(f"• {fmt_title_year(m.title, m.year)} [{(m.lang or '').upper() or '-'}] — {m.code}")
    lines.append("")
    lines.append("Use the movie code or type a more specific title with year.")
    return "\n".join(lines)
def _require_movie_arg(query: str) -> tuple[Optional[Movie], Optional[str]]:
    movie, matches = _resolve_movie_query(query)
    if movie:
        return movie, None
    return None, _movie_lookup_help(query, matches)
def _pending_action_gc() -> None:
    cutoff = _now_utc() - timedelta(minutes=PENDING_ACTION_TTL_MIN)
    dead = []
    for token, row in list(PENDING_ACTIONS.items()):
        created_at = row.get("created_at")
        if not created_at or created_at < cutoff:
            dead.append(token)
    for token in dead:
        PENDING_ACTIONS.pop(token, None)
def _new_pending_action(kind: str, user_id: int, movie: Movie, payload: Dict[str, Any]) -> str:
    _pending_action_gc()
    token = secrets.token_hex(4)
    PENDING_ACTIONS[token] = {
        "kind": kind,
        "user_id": int(user_id),
        "movie_code": movie.code,
        "payload": payload,
        "created_at": _now_utc(),
    }
    return token
def _take_pending_action(token: str, user_id: int | None = None, kind: str | None = None, consume: bool = False) -> Optional[Dict[str, Any]]:
    _pending_action_gc()
    row = PENDING_ACTIONS.get(token)
    if not row:
        return None
    if user_id is not None and int(row.get("user_id") or 0) != int(user_id):
        return None
    if kind and (row.get("kind") or "") != kind:
        return None
    if consume:
        PENDING_ACTIONS.pop(token, None)
    return row
def _pending_assign_tr_text(movie: Movie, who: str) -> str:
    who = (who or '').strip()
    current = movie.translator_assigned or '-'
    tr = None
    who_norm = who.lstrip('@').strip()
    if who_norm:
        tr = Translator.query.filter(Translator.tg_username.ilike(who_norm)).first()
    if not tr and who_norm:
        tr = Translator.query.filter(Translator.name.ilike(who_norm)).first()
    task = _translation_task_for_movie(movie)
    lines = [
        f"👤 Confirm translator assign — {fmt_title_year(movie.title, movie.year)} [{movie.code}]",
        f"Current translator: {current}",
        f"New translator: {who}",
        f"Roster match: {tr.name if tr else 'manual text / not found'}",
    ]
    if tr:
        lines.append(f"Telegram link: {'yes' if (tr.tg_user_id or tr.tg_username) else 'missing'}")
    if task:
        lines.extend([
            f"Current task status: {task.status or '-'}",
            "Confirming will update/create TranslationTask and set status to SENT.",
        ])
    else:
        lines.append("Confirming will create a new TranslationTask with status SENT.")
    lines.extend(["", "Press Confirm to write changes, or Cancel to abort."])
    return "\n".join(lines)
def _pending_reassign_vo_text(movie: Movie, role: str, who: str) -> str:
    role_key = norm_role(role) or (role or '').strip()
    assigns = Assignment.query.filter(((Assignment.movie_id == movie.id) | (Assignment.project == movie.code)) & (Assignment.role.ilike(role_key))).all()
    current_names = sorted({(a.vo or '-').strip() or '-' for a in assigns})
    gender = role_gender(role_key)
    vo_row, vo_name = _resolve_vo_name(who, gender=gender)
    submitted = VORoleSubmission.query.filter_by(movie=movie.code).filter(VORoleSubmission.role.ilike(role_key)).count()
    lines = [
        f"🎙️ Confirm VO reassign — {fmt_title_year(movie.title, movie.year)} [{movie.code}]",
        f"Role: {role_key}",
        f"Current VO: {', '.join(current_names) if current_names else '-'}",
        f"New VO: {vo_name}",
        f"Roster match: {vo_row.name if vo_row else 'manual text / not found'}",
    ]
    if submitted:
        lines.append(f"Warning: this role has {submitted} submission(s); confirm will reset them.")
    else:
        lines.append("No existing submissions for this role.")
    lines.extend(["", "Press Confirm to write changes, or Cancel to abort."])
    return "\n".join(lines)
def _pending_action_keyboard(kind: str, movie_code: str, token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data=f"mv|cf{kind}|{movie_code}|{token}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"mv|cx{kind}|{movie_code}|{token}"),
    ]])
async def _send_assign_translator_preview(msg, user_id: int, movie: Movie, who: str):
    token = _new_pending_action("tr", user_id, movie, {"who": (who or '').strip()})
    await msg.reply_text(
        _pending_assign_tr_text(movie, who),
        reply_markup=_pending_action_keyboard("tr", movie.code, token),
        disable_web_page_preview=True,
    )
async def _send_reassign_vo_preview(msg, user_id: int, movie: Movie, role: str, who: str):
    token = _new_pending_action("vo", user_id, movie, {"role": (role or '').strip(), "who": (who or '').strip()})
    await msg.reply_text(
        _pending_reassign_vo_text(movie, role, who),
        reply_markup=_pending_action_keyboard("vo", movie.code, token),
        disable_web_page_preview=True,
    )
def _dt_iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None
def _dt_from_iso(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val)
    except Exception:
        return None
def _translation_task_snapshot(task: TranslationTask | None) -> dict | None:
    if not task:
        return None
    return {
        "id": task.id,
        "movie_id": task.movie_id,
        "movie_code": task.movie_code,
        "title": task.title,
        "year": task.year,
        "lang": task.lang,
        "translator_id": task.translator_id,
        "translator_name": task.translator_name,
        "status": task.status,
        "priority_mode": getattr(task, "priority_mode", None),
        "deadline_at": _dt_iso(task.deadline_at),
        "sent_at": _dt_iso(task.sent_at),
        "completed_at": _dt_iso(task.completed_at),
        "last_reminded_at": _dt_iso(task.last_reminded_at),
        "remind_count": task.remind_count,
        "created_at": _dt_iso(task.created_at),
        "updated_at": _dt_iso(task.updated_at),
    }
def _assignment_snapshot(a: Assignment) -> dict:
    return {
        "id": a.id,
        "project": a.project,
        "movie_id": a.movie_id,
        "vo": a.vo,
        "role": a.role,
        "lines": a.lines,
        "urgent": bool(a.urgent),
        "priority_mode": getattr(a, "priority_mode", None),
        "deadline_at": _dt_iso(a.deadline_at),
        "last_reminded_at": _dt_iso(a.last_reminded_at),
        "remind_count": int(a.remind_count or 0),
        "created_at": _dt_iso(a.created_at),
    }
def _vo_submission_snapshot(s: VORoleSubmission) -> dict:
    return {
        "id": s.id,
        "movie": s.movie,
        "vo": s.vo,
        "role": s.role,
        "lines": s.lines,
        "submitted_at": _dt_iso(s.submitted_at),
        "tg_chat_id": s.tg_chat_id,
        "tg_message_id": s.tg_message_id,
        "media_type": s.media_type,
        "file_id": s.file_id,
        "file_name": s.file_name,
    }
def _task_apply_snapshot(task: TranslationTask, snap: dict) -> None:
    task.movie_id = snap.get("movie_id")
    task.movie_code = snap.get("movie_code")
    task.title = snap.get("title")
    task.year = snap.get("year")
    task.lang = snap.get("lang")
    task.translator_id = snap.get("translator_id")
    task.translator_name = snap.get("translator_name")
    task.status = snap.get("status") or 'SENT'
    task.priority_mode = snap.get("priority_mode")
    task.deadline_at = _dt_from_iso(snap.get("deadline_at"))
    task.sent_at = _dt_from_iso(snap.get("sent_at"))
    task.completed_at = _dt_from_iso(snap.get("completed_at"))
    task.last_reminded_at = _dt_from_iso(snap.get("last_reminded_at"))
    task.remind_count = int(snap.get("remind_count") or 0)
    task.created_at = _dt_from_iso(snap.get("created_at"))
    task.updated_at = _dt_from_iso(snap.get("updated_at")) or _now_utc()
def _assignment_from_snapshot(snap: dict) -> Assignment:
    obj = Assignment()
    obj.id = snap.get("id")
    obj.project = snap.get("project")
    obj.movie_id = snap.get("movie_id")
    obj.vo = snap.get("vo")
    obj.role = snap.get("role")
    obj.lines = int(snap.get("lines") or 0)
    obj.urgent = bool(snap.get("urgent"))
    obj.priority_mode = snap.get("priority_mode")
    obj.deadline_at = _dt_from_iso(snap.get("deadline_at"))
    obj.last_reminded_at = _dt_from_iso(snap.get("last_reminded_at"))
    obj.remind_count = int(snap.get("remind_count") or 0)
    obj.created_at = _dt_from_iso(snap.get("created_at")) or _now_utc()
    return obj
def _vo_submission_from_snapshot(snap: dict) -> VORoleSubmission:
    obj = VORoleSubmission()
    obj.id = snap.get("id")
    obj.movie = snap.get("movie")
    obj.vo = snap.get("vo")
    obj.role = snap.get("role")
    obj.lines = int(snap.get("lines") or 0)
    obj.submitted_at = _dt_from_iso(snap.get("submitted_at")) or _now_utc()
    obj.tg_chat_id = snap.get("tg_chat_id")
    obj.tg_message_id = snap.get("tg_message_id")
    obj.media_type = snap.get("media_type")
    obj.file_id = snap.get("file_id")
    obj.file_name = snap.get("file_name")
    return obj
def _undo_action_gc() -> None:
    cutoff = _now_utc() - timedelta(minutes=UNDO_ACTION_TTL_MIN)
    dead = []
    for token, row in list(UNDO_ACTIONS.items()):
        created_at = row.get("created_at")
        if not created_at or created_at < cutoff:
            dead.append(token)
    for token in dead:
        row = UNDO_ACTIONS.pop(token, None)
        if not row:
            continue
        uid = int(row.get("user_id") or 0)
        if uid and UNDO_LAST_BY_USER.get(uid) == token:
            UNDO_LAST_BY_USER.pop(uid, None)
def _new_undo_action(kind: str, user_id: int, movie: Movie, payload: Dict[str, Any]) -> str:
    _undo_action_gc()
    token = secrets.token_hex(4)
    UNDO_ACTIONS[token] = {
        "kind": kind,
        "user_id": int(user_id),
        "movie_code": movie.code,
        "payload": payload,
        "created_at": _now_utc(),
    }
    UNDO_LAST_BY_USER[int(user_id)] = token
    return token
def _take_undo_action(token: str, user_id: int | None = None, kind: str | None = None, consume: bool = False) -> Optional[Dict[str, Any]]:
    _undo_action_gc()
    row = UNDO_ACTIONS.get(token)
    if not row:
        return None
    if user_id is not None and int(row.get("user_id") or 0) != int(user_id):
        return None
    if kind and (row.get("kind") or "") != kind:
        return None
    if consume:
        UNDO_ACTIONS.pop(token, None)
        uid = int(row.get("user_id") or 0)
        if uid and UNDO_LAST_BY_USER.get(uid) == token:
            UNDO_LAST_BY_USER.pop(uid, None)
    return row
def _latest_undo_for_user(user_id: int) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    _undo_action_gc()
    token = UNDO_LAST_BY_USER.get(int(user_id))
    if not token:
        return None, None
    row = UNDO_ACTIONS.get(token)
    if not row:
        UNDO_LAST_BY_USER.pop(int(user_id), None)
        return None, None
    return token, row
def _undo_keyboard(movie_code: str, token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Undo", callback_data=f"mv|undo|{movie_code}|{token}")]])
def _undo_summary(kind: str, movie: Movie) -> str:
    mapping = {
        "tr": "translator assign",
        "vo": "VO reassign",
        "clear": "clear movie",
        "dtr": "translator deadline change",
        "dvo": "VO deadline change",
    }
    label = mapping.get(kind, kind)
    return f"↩️ Undo available for {label} — {fmt_title_year(movie.title, movie.year)} [{movie.code}]\nValid for about {UNDO_ACTION_TTL_MIN} minutes."
async def _send_undo_message(msg, movie: Movie, token: str, kind: str):
    await msg.reply_text(
        _undo_summary(kind, movie),
        reply_markup=_undo_keyboard(movie.code, token),
        disable_web_page_preview=True,
    )
async def _undo_assign_translator(movie: Movie, payload: dict, context: ContextTypes.DEFAULT_TYPE | None = None) -> tuple[bool, str]:
    movie.translator_assigned = payload.get("prev_translator_assigned")
    movie.updated_at = _now_utc()
    snap = payload.get("task_snapshot")
    if snap:
        task = TranslationTask.query.filter_by(id=snap.get("id")).first()
        if not task:
            task = TranslationTask()
            task.id = snap.get("id")
            db.session.add(task)
        _task_apply_snapshot(task, snap)
    else:
        task = TranslationTask.query.filter_by(movie_id=movie.id).first() or TranslationTask.query.filter_by(movie_code=movie.code).first()
        if task:
            db.session.delete(task)
    db.session.commit()
    try:
        if context:
            await _try_update_movie_card(context, movie)
    except Exception:
        pass
    return True, f"↩️ Undo complete: translator restored for {fmt_title_year(movie.title, movie.year)} [{movie.code}]"
async def _undo_reassign_vo(movie: Movie, payload: dict, context: ContextTypes.DEFAULT_TYPE | None = None) -> tuple[bool, str]:
    changed = 0
    for item in payload.get("assignments") or []:
        a = Assignment.query.filter_by(id=item.get("id")).first()
        if a:
            a.vo = item.get("vo")
            changed += 1
    role = payload.get("role")
    if role:
        VORoleSubmission.query.filter_by(movie=movie.code).filter(VORoleSubmission.role.ilike(role)).delete(synchronize_session=False)
    for snap in payload.get("submissions") or []:
        db.session.add(_vo_submission_from_snapshot(snap))
    movie.updated_at = _now_utc()
    db.session.commit()
    try:
        if context:
            await _try_update_movie_card(context, movie)
    except Exception:
        pass
    restored = len(payload.get("submissions") or [])
    return True, f"↩️ Undo complete: restored VO mapping for {fmt_title_year(movie.title, movie.year)} [{movie.code}] • assignments: {changed} • submissions: {restored}"
async def _undo_clear_movie(movie: Movie, payload: dict, context: ContextTypes.DEFAULT_TYPE | None = None) -> tuple[bool, str]:
    for snap in payload.get("assignments") or []:
        db.session.add(_assignment_from_snapshot(snap))
    for snap in payload.get("submissions") or []:
        db.session.add(_vo_submission_from_snapshot(snap))
    movie.updated_at = _now_utc()
    db.session.commit()
    try:
        if context:
            await _try_update_movie_card(context, movie)
    except Exception:
        pass
    return True, f"↩️ Undo complete: restored clear for {fmt_title_year(movie.title, movie.year)} [{movie.code}] • assignments: {len(payload.get('assignments') or [])} • submissions: {len(payload.get('submissions') or [])}"
async def _undo_deadline_tr(movie: Movie, payload: dict, context: ContextTypes.DEFAULT_TYPE | None = None) -> tuple[bool, str]:
    snap = payload.get("task_snapshot")
    if snap:
        task = TranslationTask.query.filter_by(id=snap.get("id")).first()
        if not task:
            task = TranslationTask()
            task.id = snap.get("id")
            db.session.add(task)
        _task_apply_snapshot(task, snap)
        deadline_label = fmt_myt(task.deadline_at)
    else:
        task = TranslationTask.query.filter_by(movie_id=movie.id).first() or TranslationTask.query.filter_by(movie_code=movie.code).first()
        if task:
            db.session.delete(task)
        deadline_label = '-'
    movie.updated_at = _now_utc()
    db.session.commit()
    try:
        if context:
            await _try_update_movie_card(context, movie)
    except Exception:
        pass
    return True, f"↩️ Undo complete: translator deadline restored for {fmt_title_year(movie.title, movie.year)} [{movie.code}]\nDeadline: {deadline_label}"
async def _undo_deadline_vo(movie: Movie, payload: dict, context: ContextTypes.DEFAULT_TYPE | None = None) -> tuple[bool, str]:
    rows = payload.get("assignments") or []
    for snap in rows:
        a = Assignment.query.filter_by(id=snap.get("id")).first()
        if a:
            a.deadline_at = _dt_from_iso(snap.get("deadline_at"))
    movie.updated_at = _now_utc()
    db.session.commit()
    try:
        if context:
            await _try_update_movie_card(context, movie)
    except Exception:
        pass
    return True, f"↩️ Undo complete: VO deadline restored for {fmt_title_year(movie.title, movie.year)} [{movie.code}] • roles: {len(rows)}"
async def _apply_undo_action(movie: Movie, row: dict, context: ContextTypes.DEFAULT_TYPE | None = None) -> tuple[bool, str]:
    kind = row.get("kind")
    payload = row.get("payload") or {}
    if kind == "tr":
        return await _undo_assign_translator(movie, payload, context)
    if kind == "vo":
        return await _undo_reassign_vo(movie, payload, context)
    if kind == "clear":
        return await _undo_clear_movie(movie, payload, context)
    if kind == "dtr":
        return await _undo_deadline_tr(movie, payload, context)
    if kind == "dvo":
        return await _undo_deadline_vo(movie, payload, context)
    return False, "❌ Undo type not supported"
async def _perform_latest_undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token, row = _latest_undo_for_user(update.effective_user.id)
    if not token or not row:
        return await update.effective_message.reply_text("❌ No recent undo action available.")
    movie_code = (row.get("movie_code") or "").strip().upper()
    movie = movie_by_code(movie_code)
    if not movie:
        _take_undo_action(token, user_id=update.effective_user.id, consume=True)
        return await update.effective_message.reply_text("❌ Undo target movie not found anymore.")
    row = _take_undo_action(token, user_id=update.effective_user.id, consume=True)
    if not row:
        return await update.effective_message.reply_text("❌ Undo expired.")
    ok, reply = await _apply_undo_action(movie, row, context)
    await update.effective_message.reply_text(reply, disable_web_page_preview=True)
def _movie_card_text(m: Movie) -> str:
    year = f" ({m.year})" if m.year else ""
    lang = f" - {m.lang}" if m.lang else ""
    tr = f"\n👤 *Translator:* `{m.translator_assigned}`" if m.translator_assigned else ""
    return (
        f"🎬 *{m.title}{year}{lang}*\n"
        f"🆔 *Code:* `{m.code}`\n"
        f"📌 *Status:* `{m.status}`{tr}\n"
        f"🕒 *Received:* {m.received_at.strftime('%Y-%m-%d %H:%M UTC') if m.received_at else '-'}\n"
    )
def _movie_result_label(m: Movie, limit: int = 30) -> str:
    label = fmt_title_year(m.title, m.year) or (m.code or 'Movie')
    label = re.sub(r"\s+", " ", label).strip()
    if len(label) > limit:
        label = label[: limit - 1].rstrip() + '…'
    return label
def _public_deadline_countdown(dt: datetime | None) -> str:
    if not dt:
        return "No deadline"
    delta = dt - _now_utc()
    secs = int(delta.total_seconds())
    future = secs >= 0
    secs = abs(secs)
    if secs < 3600:
        mins = max(1, (secs + 59) // 60)
        label = f"{mins}m"
    else:
        hours = (secs + 3599) // 3600
        if hours < 48:
            label = f"{hours}h"
        else:
            days = secs // 86400
            rem = secs - (days * 86400)
            rem_h = (rem + 3599) // 3600
            if rem_h >= 24:
                days += 1
                rem_h = 0
            label = f"{days}d" + (f" {rem_h}h" if rem_h else "")
    return f"Due in {label}" if future else f"Late by {label}"
def _vo_public_card_text(movie: Movie) -> str:
    assigns = Assignment.query.filter((Assignment.movie_id == movie.id) | (Assignment.project == movie.code)).order_by(Assignment.role.asc()).all()
    if not assigns:
        return "\n".join([
            f"🎙️ VO Assignments — {fmt_title_year(movie.title, movie.year)} — {lang_display(movie.lang or DEFAULT_LANG)}",
            "",
            "⏰ No assignments yet",
        ])
    subs = VORoleSubmission.query.filter_by(movie=movie.code).all()
    submitted_roles = {norm_role(s.role) for s in subs if norm_role(s.role)}
    open_assigns = [a for a in assigns if (norm_role(a.role) or a.role) not in submitted_roles]
    deadline_candidates = [a.deadline_at for a in open_assigns if a.deadline_at] or [a.deadline_at for a in assigns if a.deadline_at]
    next_deadline = min(deadline_candidates) if deadline_candidates else None
    priority_mode = _movie_priority_mode(movie, assigns)
    urgency = _priority_mode_label(priority_mode)
    done_count = len([a for a in assigns if (norm_role(a.role) or a.role) in submitted_roles])
    total_count = len(assigns)
    lines = [
        f"🎙️ VO Assignments — {fmt_title_year(movie.title, movie.year)} — {lang_display(movie.lang or DEFAULT_LANG)}",
        "",
        f"⏰ {urgency} • {_public_deadline_countdown(next_deadline)}",
        "",
    ]
    totals = {}
    for a in assigns:
        role_label = norm_role(a.role) or (a.role or '-')
        who = (a.vo or '-').strip() or '-'
        line_count = int(a.lines or 0)
        done = (role_label in submitted_roles)
        mark = '✅' if done else '⏳'
        lines.append(f"• {mark} {role_label} — {line_count} lines — {who}")
        totals[who] = int(totals.get(who, 0) or 0) + line_count
    lines.extend(["", "Totals"])
    for who, total in sorted(totals.items(), key=lambda kv: (-kv[1], (kv[0] or '').lower())):
        lines.append(f"• {who}: {total}")
    return "\n".join(lines)
def _public_assignment_card_key(movie: Movie) -> str:
    return f"vo_public_card:{movie.code}"
def _public_assignment_card_ref(movie: Movie) -> tuple[int | None, int | None]:
    try:
        row = AppKV.query.filter_by(key=_public_assignment_card_key(movie)).first()
        if not row or not row.value:
            return None, None
        data = json.loads(row.value)
        return int(data.get("chat_id") or 0) or None, int(data.get("message_id") or 0) or None
    except Exception:
        return None, None
def _set_public_assignment_card_ref(movie: Movie, chat_id: int | None, message_id: int | None) -> None:
    try:
        payload = json.dumps({"chat_id": int(chat_id or 0), "message_id": int(message_id or 0)})
        row = AppKV.query.filter_by(key=_public_assignment_card_key(movie)).first()
        if not row:
            row = AppKV(key=_public_assignment_card_key(movie), value=payload)
            db.session.add(row)
        else:
            row.value = payload
        db.session.commit()
    except Exception:
        db.session.rollback()
def _role_req_ack_key(req_id: int) -> str:
    return f"role_req_ack:{int(req_id)}"

def _role_req_helpers_key(req_id: int) -> str:
    return f"role_req_helpers:{int(req_id)}"

def _chat_role_helper_key(chat_id: int) -> str:
    return f"chat_role_helpers:{int(chat_id)}"

def _remember_chat_role_helper_file(chat_id: int, message_id: int | None, file_name: str | None) -> None:
    if not chat_id or not message_id or not file_name:
        return
    low = (file_name or '').strip().lower()
    if not low.endswith('.txt') or 'role' not in low:
        return
    try:
        raw = _kv_get(_chat_role_helper_key(chat_id)) or '[]'
        data = json.loads(raw)
        if not isinstance(data, list):
            data = []
    except Exception:
        data = []
    now_ts = int(_now_utc().timestamp())
    keep = []
    seen = set()
    for row in data:
        try:
            mid = int(row.get('message_id') or 0)
            ts = int(row.get('ts') or 0)
            if not mid or now_ts - ts > 7200:
                continue
            if mid in seen:
                continue
            seen.add(mid)
            keep.append({'message_id': mid, 'file_name': row.get('file_name') or '', 'ts': ts})
        except Exception:
            continue
    if int(message_id) not in seen:
        keep.append({'message_id': int(message_id), 'file_name': file_name, 'ts': now_ts})
    keep = sorted(keep, key=lambda x: int(x.get('ts') or 0))[-12:]
    _kv_set(_chat_role_helper_key(chat_id), json.dumps(keep))

def _claim_recent_chat_role_helper_ids(chat_id: int, *, lookback_seconds: int = 1800) -> List[int]:
    if not chat_id:
        return []
    try:
        raw = _kv_get(_chat_role_helper_key(chat_id)) or '[]'
        data = json.loads(raw)
        if not isinstance(data, list):
            data = []
    except Exception:
        data = []
    now_ts = int(_now_utc().timestamp())
    keep = []
    out = []
    seen = set()
    for row in data:
        try:
            mid = int(row.get('message_id') or 0)
            ts = int(row.get('ts') or 0)
            if not mid or now_ts - ts > 7200:
                continue
            if mid in seen:
                continue
            seen.add(mid)
            if now_ts - ts <= lookback_seconds:
                out.append(mid)
            else:
                keep.append({'message_id': mid, 'file_name': row.get('file_name') or '', 'ts': ts})
        except Exception:
            continue
    _kv_set(_chat_role_helper_key(chat_id), json.dumps(keep))
    return out

def _role_helper_file_name(name: str | None) -> str:
    return ((name or '').strip().lower())

def _is_role_helper_filename(name: str | None) -> bool:
    low = _role_helper_file_name(name)
    if not low or not low.endswith('.txt'):
        return False
    return ('role' in low) and ('censor' not in low)

async def _read_tg_text_document(context: ContextTypes.DEFAULT_TYPE, file_id: str, *, max_bytes: int = 512_000) -> str:
    import tempfile
    tmp_path = os.path.join(tempfile.gettempdir(), f"tg_role_{secrets.token_hex(6)}.txt")
    tg_file = await context.bot.get_file(file_id)
    await tg_file.download_to_drive(custom_path=tmp_path)
    try:
        with open(tmp_path, 'rb') as f:
            data = f.read(max_bytes + 1)
        if len(data) > max_bytes:
            data = data[:max_bytes]
        for enc in ('utf-8-sig', 'utf-8', 'cp1251', 'latin-1'):
            try:
                return data.decode(enc)
            except Exception:
                continue
        return data.decode('utf-8', errors='ignore')
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

def _resolve_group_movie_for_role_import(chat_id: int, context: ContextTypes.DEFAULT_TYPE, helper_name: str | None = None) -> Movie | None:
    movie = Movie.query.filter_by(vo_group_chat_id=int(chat_id)).first()
    movie = _reactivate_movie_if_archived(movie) if movie else None
    if movie:
        return movie
    ctx_row = _ctx_get(int(chat_id))
    if ctx_row and ctx_row.title and ctx_row.year:
        movie = upsert_movie(ctx_row.title, int(ctx_row.year), ctx_row.lang or DEFAULT_LANG)
        movie.vo_group_chat_id = int(chat_id)
        movie.updated_at = _now_utc()
        db.session.commit()
        return movie
    parsed = _parse_movie_from_role_helper_filename(helper_name or '') if helper_name else None
    if parsed and parsed.get('title') and parsed.get('year'):
        movie = upsert_movie(parsed['title'], int(parsed['year']), parsed.get('lang') or DEFAULT_LANG)
        _learn_movie_alias(movie, helper_name or parsed.get('title') or '', source='helper_resolve')
        movie.vo_group_chat_id = int(chat_id)
        movie.updated_at = _now_utc()
        db.session.commit()
        return movie
    now = datetime.utcnow()
    recent_files = _recent_group_file_candidates(context, int(chat_id), now=now, lookback_hours=24)
    for item in recent_files:
        candidate_name = (item.get('file_name') or '').strip()
        if not candidate_name or _is_role_helper_filename(candidate_name):
            continue
        parsed = parse_movie_from_filename(candidate_name) or _parse_movie_from_role_helper_filename(candidate_name)
        if not parsed or not parsed.get('title') or not parsed.get('year'):
            continue
        movie = upsert_movie(parsed['title'], int(parsed['year']), parsed.get('lang') or DEFAULT_LANG)
        _learn_movie_alias(movie, candidate_name or parsed.get('title') or '', source='recent_group_file')
        movie.vo_group_chat_id = int(chat_id)
        movie.updated_at = _now_utc()
        db.session.commit()
        return movie
    cached = _find_cached_candidate(context, int(chat_id), now=now)
    if cached and cached.get('title') and cached.get('year'):
        movie = upsert_movie(cached['title'], int(cached['year']), cached.get('lang') or DEFAULT_LANG)
        movie.vo_group_chat_id = int(chat_id)
        movie.updated_at = _now_utc()
        db.session.commit()
        return movie
    return None

async def _auto_import_role_helper_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not _is_group(update):
        return False
    msg = update.effective_message
    if not msg or not getattr(msg, 'document', None):
        return False
    file_name = getattr(msg.document, 'file_name', None)
    if not _is_role_helper_filename(file_name):
        return False
    file_id = getattr(msg.document, 'file_id', None)
    if not file_id:
        return False
    movie = _resolve_group_movie_for_role_import(int(update.effective_chat.id), context, file_name)
    if not movie:
        try:
            await _notify_admin(
                context,
                "\n".join([
                    "⚠️ *Role helper file received but movie was not resolved*",
                    f"Chat: `{int(update.effective_chat.id)}`",
                    f"File: `{file_name or '-'}`",
                ]),
            )
        except Exception:
            pass
        return False
    try:
        raw_text = await _read_tg_text_document(context, file_id)
    except Exception as e:
        try:
            await _notify_admin(
                context,
                "\n".join([
                    "❌ *Failed to read role helper file*",
                    f"Movie: `{movie.code}` — {fmt_title_year(movie.title, movie.year)}",
                    f"File: `{file_name or '-'}`",
                    f"Error: `{str(e)[:300]}`",
                ]),
            )
        except Exception:
            pass
        return True
    roles = parse_lines(raw_text or '')
    if not roles:
        try:
            await _notify_admin(
                context,
                "\n".join([
                    "⚠️ *Role helper file parsed 0 roles*",
                    f"Movie: `{movie.code}` — {fmt_title_year(movie.title, movie.year)}",
                    f"File: `{file_name or '-'}`",
                ]),
            )
        except Exception:
            pass
        return True
    parsed_helper = _parse_movie_from_role_helper_filename(file_name or '')
    _learn_movie_alias(movie, file_name or '', source='role_helper_filename')
    if parsed_helper:
        helper_title = (parsed_helper.get('title') or '').strip()
        helper_year = str(parsed_helper.get('year') or '').strip()
        helper_lang = _slug_lang(parsed_helper.get('lang') or movie.lang or DEFAULT_LANG)
        changed = False
        if helper_title and (_looks_role_prefixed_title(movie.title or '') or _strip_role_prefix_title(movie.title or '') == helper_title):
            if (movie.title or '').strip() != helper_title:
                movie.title = helper_title
                changed = True
        if helper_year and str(movie.year or '').strip() != helper_year and (not movie.year or _looks_role_prefixed_title(movie.title or '') or _strip_role_prefix_title(movie.title or '') == helper_title):
            movie.year = helper_year
            changed = True
        if helper_lang and (movie.lang or DEFAULT_LANG) != helper_lang:
            movie.lang = helper_lang
            changed = True
        if changed:
            movie.updated_at = _now_utc()
    suggestions = _auto_assign_movie_roles(movie, roles, urgent=True, replace_existing=True, priority_mode="urgent")
    movie.updated_at = _now_utc()
    db.session.commit()
    try:
        record_movie_event(
            movie,
            'AUTO_ROLE_IMPORT',
            f"Auto imported {len(suggestions)} role(s) from helper file",
            detail=f"file={file_name or '-'}",
            actor_source='tg',
            actor_name=(tg_submitter_display(update) or 'telegram'),
        )
    except Exception:
        pass
    try:
        await context.bot.delete_message(chat_id=int(update.effective_chat.id), message_id=int(msg.message_id))
    except Exception:
        pass
    try:
        await _upsert_public_assignment_card(context, movie, pin=True)
    except Exception:
        pass
    try:
        preview = []
        for s in suggestions[:12]:
            preview.append(f"• `{s.get('role')}` → *{s.get('vo')}* ({int(s.get('lines') or 0)} lines)")
        if len(suggestions) > 12:
            preview.append(f"… +{len(suggestions)-12} more")
        await _notify_admin(
            context,
            "\n".join([
                "✅ *Role helper auto-imported*",
                f"Movie: `{movie.code}` — {fmt_title_year(movie.title, movie.year)} [{(movie.lang or DEFAULT_LANG).upper()}]",
                f"File: `{file_name or '-'}`",
                f"Roles: `{len(suggestions)}`",
                f"Chat: `{int(update.effective_chat.id)}`",
                "",
                *preview,
            ]),
        )
    except Exception:
        pass
    return True

def _set_role_req_ack_message_id(req_id: int, message_id: int | None) -> None:
    _kv_set(_role_req_ack_key(req_id), str(int(message_id or 0)))

def _get_role_req_ack_message_id(req_id: int) -> int | None:
    raw = (_kv_get(_role_req_ack_key(req_id)) or '').strip()
    return int(raw) if raw.isdigit() else None

def _set_role_req_helper_ids(req_id: int, ids: List[int]) -> None:
    safe = [int(x) for x in ids if int(x or 0)]
    _kv_set(_role_req_helpers_key(req_id), json.dumps(safe))

def _get_role_req_helper_ids(req_id: int) -> List[int]:
    try:
        raw = _kv_get(_role_req_helpers_key(req_id)) or '[]'
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        return [int(x) for x in data if int(x or 0)]
    except Exception:
        return []

async def _cleanup_role_import_group_noise(context: ContextTypes.DEFAULT_TYPE, req: GroupRoleImportRequest):
    if not req or not req.tg_chat_id:
        return
    message_ids = []
    if getattr(req, 'tg_message_id', None):
        message_ids.append(int(req.tg_message_id))
    ack_id = _get_role_req_ack_message_id(req.id)
    if ack_id:
        message_ids.append(int(ack_id))
    message_ids.extend(_get_role_req_helper_ids(req.id))
    seen = set()
    for mid in message_ids:
        try:
            mid = int(mid or 0)
        except Exception:
            continue
        if not mid or mid in seen:
            continue
        seen.add(mid)
        try:
            await context.bot.delete_message(chat_id=int(req.tg_chat_id), message_id=mid)
        except Exception:
            pass

async def _upsert_public_assignment_card(context: ContextTypes.DEFAULT_TYPE, movie: Movie, *, pin: bool = True):
    if not movie or not movie.vo_group_chat_id:
        return None
    text = _vo_public_card_text(movie)
    chat_id = int(movie.vo_group_chat_id)
    old_chat_id, old_msg_id = _public_assignment_card_ref(movie)
    if old_chat_id and old_msg_id:
        try:
            await context.bot.edit_message_text(chat_id=int(old_chat_id), message_id=int(old_msg_id), text=text, disable_web_page_preview=True)
            return old_msg_id
        except Exception:
            pass
    msg = await context.bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
    _set_public_assignment_card_ref(movie, msg.chat_id, msg.message_id)
    if pin:
        try:
            await context.bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id, disable_notification=True)
        except Exception:
            pass
    return getattr(msg, "message_id", None)
def _movie_search_keyboard(matches: List[Movie]) -> InlineKeyboardMarkup:
    rows = []
    for m in matches[:6]:
        rows.append([
            InlineKeyboardButton(f"🎬 {_movie_result_label(m)}", callback_data=f"mv|card|{m.code}"),
            InlineKeyboardButton("👤 Assign", callback_data=f"mv|assign|{m.code}"),
        ])
    return InlineKeyboardMarkup(rows)
def _who_has_text(m: Movie) -> str:
    assigns = Assignment.query.filter((Assignment.movie_id == m.id) | (Assignment.project == m.code)).order_by(Assignment.role.asc()).all()
    subs = VORoleSubmission.query.filter(VORoleSubmission.movie == m.code).all()
    submitted_roles = {norm_role(s.role) for s in subs if norm_role(s.role)}
    out = [f"🎬 {fmt_title_year(m.title, m.year)} [{m.code}]", f"Status: {m.status}"]
    out.append(f"Translator: {m.translator_assigned or '-'}")
    out.append('')
    if assigns:
        out.append('VO assignments')
        for a in assigns[:30]:
            done = '✅' if norm_role(a.role) in submitted_roles else '⏳'
            out.append(f"• {done} {a.role} → {a.vo} ({int(a.lines or 0)})")
    else:
        out.append('No VO assignments yet.')
    return '\n'.join(out)
def _activity_freshness_label(dt: datetime | None) -> str:
    if not dt:
        return 'activity unknown'
    delta = _now_utc() - dt
    hours = max(0, int(delta.total_seconds() // 3600))
    if hours < 24:
        return 'active today'
    days = max(1, hours // 24)
    return f'active {days}d ago'
def _translator_lang_match(tr: Translator, lang: str | None) -> bool:
    want = (lang or '').strip().lower()
    langs = (tr.languages or '').strip().lower()
    if not want or not langs:
        return True
    parts = [x.strip() for x in re.split(r'[,;/\s]+', langs) if x.strip()]
    return want in parts
def _translator_candidate_rows(movie: Movie, limit: int = 6) -> List[Dict[str, Any]]:
    lang = (movie.lang or '').strip().lower()
    rows = Translator.query.filter_by(active=True).order_by(Translator.name.asc()).all()
    now = _now_utc()
    recent_cutoff = now - timedelta(days=14)
    active_counts: Dict[str, int] = {}
    overdue_counts: Dict[str, int] = {}
    recent_done_counts: Dict[str, int] = {}
    for task in TranslationTask.query.all():
        key = (task.translator_name or '').strip().lower()
        if not key:
            continue
        if (task.status or '').upper() != 'COMPLETED':
            active_counts[key] = active_counts.get(key, 0) + 1
            if task.deadline_at and task.deadline_at < now:
                overdue_counts[key] = overdue_counts.get(key, 0) + 1
        elif task.completed_at and task.completed_at >= recent_cutoff:
            recent_done_counts[key] = recent_done_counts.get(key, 0) + 1
    scored: List[Dict[str, Any]] = []
    for tr in rows:
        name_key = (tr.name or '').strip().lower()
        lang_ok = _translator_lang_match(tr, lang)
        open_jobs = active_counts.get(name_key, 0)
        overdue_jobs = overdue_counts.get(name_key, 0)
        recent_done = recent_done_counts.get(name_key, 0)
        linked = bool(tr.tg_user_id or tr.tg_username)
        seen_penalty = 18
        if tr.last_seen_at:
            seen_hours = max(0, int((now - tr.last_seen_at).total_seconds() // 3600))
            seen_penalty = min(30, seen_hours // 24)
        score = (
            open_jobs * 100
            + overdue_jobs * 250
            + seen_penalty
            + (0 if linked else 35)
            + (0 if lang_ok else (500 if lang else 0))
            - min(recent_done, 4) * 12
        )
        reasons = []
        if lang:
            reasons.append(f'lang {lang if lang_ok else "check"}')
        reasons.append(f'open {open_jobs}')
        if overdue_jobs:
            reasons.append(f'overdue {overdue_jobs}')
        if recent_done:
            reasons.append(f'recent done {recent_done}')
        reasons.append('tg linked' if linked else 'tg link missing')
        reasons.append(_activity_freshness_label(tr.last_seen_at))
        scored.append({
            'translator': tr,
            'score': score,
            'open_jobs': open_jobs,
            'overdue_jobs': overdue_jobs,
            'recent_done': recent_done,
            'lang_ok': lang_ok,
            'linked': linked,
            'reasons': reasons,
        })
    scored.sort(key=lambda row: (row['score'], row['open_jobs'], row['overdue_jobs'], (row['translator'].name or '').lower()))
    return scored[:limit]
def _translator_shortlist(movie: Movie, limit: int = 6) -> List[Translator]:
    return [row['translator'] for row in _translator_candidate_rows(movie, limit=limit)]
def _translator_suggestion_text(movie: Movie, candidates: List[Dict[str, Any]]) -> str:
    lines = [
        f"🎯 Translator picks — {fmt_title_year(movie.title, movie.year)} [{movie.code}]",
        "Sorted by live workload, overdue risk, language fit, and recent activity.",
        "Tap one name below to assign immediately.",
        "",
    ]
    for idx, row in enumerate(candidates[:6], start=1):
        tr = row['translator']
        reasons = ', '.join(row['reasons'][:5])
        lines.append(f"{idx}. {tr.name} — {reasons}")
    if not candidates:
        lines.append('No active translators found.')
    return '\n'.join(lines)
def _translator_pick_keyboard(movie: Movie, candidates: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    rows = []
    for row in candidates[:6]:
        tr = row['translator']
        label_bits = [f"👤 {tr.name}", f"{row['open_jobs']} open"]
        if row['overdue_jobs']:
            label_bits.append(f"{row['overdue_jobs']} late")
        rows.append([InlineKeyboardButton(' • '.join(label_bits), callback_data=f"mv|trpick|{movie.code}|{tr.id}")])
    return InlineKeyboardMarkup(rows)
def _movie_workload_text(m: Movie) -> str:
    assigns = Assignment.query.filter((Assignment.movie_id == m.id) | (Assignment.project == m.code)).order_by(Assignment.role.asc()).all()
    subs = VORoleSubmission.query.filter_by(movie=m.code).order_by(VORoleSubmission.submitted_at.desc()).all()
    submitted_roles = {norm_role(s.role) for s in subs if norm_role(s.role)}
    task = TranslationTask.query.filter_by(movie_id=m.id).first() or TranslationTask.query.filter_by(movie_code=m.code).first()
    lines = [f"📦 Movie workload — {fmt_title_year(m.title, m.year)} [{m.code}]", f"Status: {m.status}", ""]
    if task:
        lines += [
            "Translator",
            f"• Assigned: {task.translator_name or m.translator_assigned or '-'}",
            f"• Task status: {task.status or '-'}",
            f"• Deadline: {task.deadline_at.strftime('%Y-%m-%d %H:%M UTC') if task.deadline_at else '-'}",
            "",
        ]
    else:
        lines += [
            "Translator",
            f"• Assigned: {m.translator_assigned or '-'}",
            "• Task status: -",
            "",
        ]
    if assigns:
        open_roles = []
        done_roles = []
        per_vo: Dict[str, Dict[str, int]] = {}
        for a in assigns:
            role_key = norm_role(a.role) or a.role
            done = role_key in submitted_roles
            bucket = per_vo.setdefault((a.vo or '-').strip() or '-', {'open': 0, 'done': 0, 'lines_open': 0, 'lines_done': 0})
            if done:
                bucket['done'] += 1
                bucket['lines_done'] += int(a.lines or 0)
                done_roles.append(role_key)
            else:
                bucket['open'] += 1
                bucket['lines_open'] += int(a.lines or 0)
                open_roles.append(role_key)
        lines += [
            "VO",
            f"• Total roles: {len(assigns)}",
            f"• Open roles: {len(open_roles)}",
            f"• Submitted roles: {len(done_roles)}",
        ]
        if open_roles:
            lines.append(f"• Missing: {', '.join(sorted(set(open_roles))[:12])}")
        lines.append('')
        lines.append('Per VO')
        for name, stat in sorted(per_vo.items(), key=lambda kv: (-kv[1]['open'], -kv[1]['lines_open'], kv[0].lower()))[:10]:
            lines.append(f"• {name} — open {stat['open']} ({stat['lines_open']} lines), done {stat['done']}")
    else:
        lines += [
            "VO",
            "• No assignments yet.",
        ]
    return '\n'.join(lines)
def _translation_task_for_movie(movie: Movie) -> TranslationTask | None:
    return TranslationTask.query.filter_by(movie_id=movie.id).first() or TranslationTask.query.filter_by(movie_code=movie.code).first()
def _resolve_translator_row_for_task(task: TranslationTask | None) -> Translator | None:
    if not task:
        return None
    if task.translator_id:
        tr = db.session.get(Translator, int(task.translator_id))
        if tr:
            return tr
    who = (task.translator_name or '').strip()
    if not who:
        return None
    who_norm = who.lstrip('@').strip()
    tr = Translator.query.filter(Translator.name.ilike(who_norm)).first()
    if tr:
        return tr
    return Translator.query.filter(Translator.tg_username.ilike(who_norm)).first()
def _resolve_vo_row_for_name(name: str | None) -> VOTeam | None:
    who = (name or '').strip()
    if not who:
        return None
    row = VOTeam.query.filter(VOTeam.name.ilike(who)).first()
    if row:
        return row
    who_norm = who.lstrip('@').strip()
    row = VOTeam.query.filter(VOTeam.tg_username.ilike(who_norm)).first()
    if row:
        return row
    for vo in VOTeam.query.filter_by(active=True).all():
        if vo.name and vo.name.lower() in who.lower():
            return vo
        if vo.tg_username and vo.tg_username.lower() in who_norm.lower():
            return vo
    return None
def _open_assignments_for_movie(movie: Movie) -> tuple[list[Assignment], set[str]]:
    assigns = Assignment.query.filter((Assignment.movie_id == movie.id) | (Assignment.project == movie.code)).order_by(Assignment.role.asc()).all()
    subs = VORoleSubmission.query.filter_by(movie=movie.code).all()
    submitted_roles = {norm_role(s.role) for s in subs if norm_role(s.role)}
    open_assigns = [a for a in assigns if (norm_role(a.role) or a.role) not in submitted_roles]
    return open_assigns, submitted_roles
def _movie_deadline_text(m: Movie) -> str:
    task = _translation_task_for_movie(m)
    open_assigns, submitted_roles = _open_assignments_for_movie(m)
    all_assigns = Assignment.query.filter((Assignment.movie_id == m.id) | (Assignment.project == m.code)).order_by(Assignment.role.asc()).all()
    now = _now_utc()
    lines = [f"⏰ Deadline overview — {fmt_title_year(m.title, m.year)} [{m.code}]", f"Status: {m.status or '-'}", ""]
    lines.append("Translator")
    if task:
        is_done = (task.status or '').upper() == 'COMPLETED'
        is_overdue = (not is_done) and bool(task.deadline_at and task.deadline_at < now)
        lines.append(f"• Assigned: {task.translator_name or m.translator_assigned or '-'}")
        lines.append(f"• Task status: {'OVERDUE' if is_overdue else (task.status or '-')}" )
        lines.append(f"• Deadline: {fmt_myt(task.deadline_at)}")
        lines.append(f"• Reminds sent: {int(task.remind_count or 0)}")
    else:
        lines.append(f"• Assigned: {m.translator_assigned or '-'}")
        lines.append("• Task status: -")
        lines.append("• Deadline: -")
    lines.append("")
    lines.append("VO")
    lines.append(f"• Total roles: {len(all_assigns)}")
    lines.append(f"• Open roles: {len(open_assigns)}")
    overdue_roles = [a for a in open_assigns if a.deadline_at and a.deadline_at < now]
    lines.append(f"• Overdue roles: {len(overdue_roles)}")
    if overdue_roles:
        lines.append("• Overdue list:")
        for a in overdue_roles[:10]:
            lines.append(f"  - {a.role} → {a.vo} • {fmt_myt(a.deadline_at)}")
    elif open_assigns:
        lines.append("• Next open roles:")
        for a in open_assigns[:10]:
            lines.append(f"  - {a.role} → {a.vo} • {fmt_myt(a.deadline_at)}")
    else:
        lines.append("• All VO roles submitted.")
    lines.extend(["", "Quick commands:", f"• /deadline_tr {m.code} | 2026-03-10 22:00", f"• /deadline_vo {m.code} | open | 2026-03-10 22:00", f"• /remind_tr {m.code}", f"• /remind_vo {m.code} | open"])
    return '\n'.join(lines)
def _ensure_translation_task(movie: Movie) -> TranslationTask:
    task = _translation_task_for_movie(movie)
    if task:
        task.movie_id = task.movie_id or movie.id
        task.movie_code = task.movie_code or movie.code
        task.title = movie.title
        task.year = movie.year
        task.lang = movie.lang
        if movie.translator_assigned and not task.translator_name:
            task.translator_name = movie.translator_assigned
        task.priority_mode = getattr(task, 'priority_mode', None) or _movie_priority_mode(movie)
        if not task.deadline_at:
            task.deadline_at = _priority_mode_deadline(task.priority_mode)
        return task
    task = TranslationTask(
        movie_id=movie.id,
        movie_code=movie.code,
        title=movie.title,
        year=movie.year,
        lang=movie.lang,
        translator_name=movie.translator_assigned or None,
        status='SENT',
        priority_mode=_movie_priority_mode(movie),
        deadline_at=_priority_mode_deadline(_movie_priority_mode(movie)),
        sent_at=_now_utc(),
    )
    tr = None
    if movie.translator_assigned:
        who_norm = movie.translator_assigned.lstrip('@').strip()
        tr = Translator.query.filter(Translator.name.ilike(who_norm)).first() or Translator.query.filter(Translator.tg_username.ilike(who_norm)).first()
    if tr:
        task.translator_id = tr.id
        task.translator_name = tr.name
    db.session.add(task)
    db.session.flush()
    return task
def _set_translation_deadline(movie: Movie, dt_utc: datetime | None) -> TranslationTask:
    task = _ensure_translation_task(movie)
    task.deadline_at = dt_utc
    task.updated_at = _now_utc()
    db.session.commit()
    return task
def _set_vo_deadline(movie: Movie, role_token: str, dt_utc: datetime | None) -> tuple[int, list[str]]:
    target = (role_token or 'open').strip().lower()
    open_assigns, submitted_roles = _open_assignments_for_movie(movie)
    all_assigns = Assignment.query.filter((Assignment.movie_id == movie.id) | (Assignment.project == movie.code)).order_by(Assignment.role.asc()).all()
    if target in {'open', 'pending'}:
        picks = open_assigns
    elif target in {'all', '*'}:
        picks = all_assigns
    else:
        norm_target = norm_role(target) or target
        picks = [a for a in all_assigns if (norm_role(a.role) or a.role) == norm_target]
    changed_roles = []
    for a in picks:
        a.deadline_at = dt_utc
        changed_roles.append(a.role)
    if picks:
        db.session.commit()
    return len(picks), changed_roles
async def _send_translation_task_reminder(context: ContextTypes.DEFAULT_TYPE, task: TranslationTask) -> tuple[bool, str]:
    tr = _resolve_translator_row_for_task(task)
    if not tr or not tr.tg_user_id:
        return False, "⚠️ Translator reminder failed: missing linked Telegram ID"
    overdue = bool(task.deadline_at and task.deadline_at < _now_utc())
    text = '\n'.join([
        '⏰ Translation reminder' + (' (overdue)' if overdue else ''),
        f"Movie: {fmt_title_year(task.title, task.year) or '-'}",
        f"Code: {task.movie_code or '-'}",
        f"Lang: {(task.lang or '').upper() or '-'}",
        f"Deadline: {fmt_myt(task.deadline_at)}",
        'Please submit the translated .srt to this bot as soon as possible.',
    ])
    try:
        await context.bot.send_message(chat_id=int(tr.tg_user_id), text=text, disable_web_page_preview=True)
        task.last_reminded_at = _now_utc()
        task.remind_count = int(task.remind_count or 0) + 1
        task.updated_at = _now_utc()
        db.session.commit()
        return True, f"✅ Translator reminded: {tr.name}"
    except Exception as e:
        db.session.rollback()
        return False, f"❌ Translator reminder failed: {e}"
async def _send_vo_assignment_reminder(context: ContextTypes.DEFAULT_TYPE, movie: Movie, a: Assignment) -> tuple[bool, str]:
    vo = _resolve_vo_row_for_name(a.vo)
    if not vo or not vo.tg_user_id:
        return False, f"⚠️ VO reminder skipped: {a.role} → {a.vo} (missing linked Telegram ID)"
    overdue = bool(a.deadline_at and a.deadline_at < _now_utc())
    text = '\n'.join([
        '⏰ VO reminder' + (' (overdue)' if overdue else ''),
        f"Movie: {fmt_title_year(movie.title, movie.year)}",
        f"Code: {movie.code}",
        f"Role: {a.role} ({int(a.lines or 0)} lines)",
        f"Deadline: {fmt_myt(a.deadline_at)}",
        'Please submit your role as soon as possible. Tell admin if you need more time.',
    ])
    try:
        await context.bot.send_message(chat_id=int(vo.tg_user_id), text=text, disable_web_page_preview=True)
        a.last_reminded_at = _now_utc()
        a.remind_count = int(a.remind_count or 0) + 1
        db.session.commit()
        return True, f"✅ VO reminded: {a.role} → {vo.name}"
    except Exception as e:
        db.session.rollback()
        return False, f"❌ VO reminder failed: {a.role} → {a.vo} • {e}"
async def _remind_vo_for_movie(context: ContextTypes.DEFAULT_TYPE, movie: Movie, role_token: str = 'open') -> tuple[int, int, list[str]]:
    target = (role_token or 'open').strip().lower()
    open_assigns, _submitted_roles = _open_assignments_for_movie(movie)
    all_assigns = Assignment.query.filter((Assignment.movie_id == movie.id) | (Assignment.project == movie.code)).order_by(Assignment.role.asc()).all()
    if target in {'open', 'pending'}:
        picks = open_assigns
    elif target in {'all', '*'}:
        picks = all_assigns
    else:
        norm_target = norm_role(target) or target
        picks = [a for a in all_assigns if (norm_role(a.role) or a.role) == norm_target]
    sent = 0
    notes = []
    for a in picks:
        ok, note = await _send_vo_assignment_reminder(context, movie, a)
        if ok:
            sent += 1
        notes.append(note)
    return sent, len(picks), notes
def _vo_candidate_rows(movie: Movie, role: str, limit: int = 6) -> List[Dict[str, Any]]:
    role_key = norm_role(role) or (role or '').strip().lower()
    gender = role_gender(role_key)
    q = VOTeam.query.filter_by(active=True, gender=gender)
    rows = q.order_by(VOTeam.name.asc()).all()
    now = _now_utc()
    recent_cutoff = now - timedelta(days=14)
    assignments = Assignment.query.all()
    submitted = VORoleSubmission.query.all()
    submitted_pairs = {(s.movie, norm_role(s.role), (s.vo or '').strip().lower()) for s in submitted}
    recent_done_counts: Dict[str, int] = {}
    for s in submitted:
        if s.submitted_at and s.submitted_at >= recent_cutoff:
            key = (s.vo or '').strip().lower()
            if key:
                recent_done_counts[key] = recent_done_counts.get(key, 0) + 1
    stats: Dict[str, Dict[str, int]] = {}
    for a in assignments:
        name = (a.vo or '').strip().lower()
        if not name:
            continue
        bucket = stats.setdefault(name, {'open': 0, 'lines_open': 0, 'overdue': 0})
        done = ((a.project or ''), norm_role(a.role), name) in submitted_pairs
        if not done:
            bucket['open'] += 1
            bucket['lines_open'] += int(a.lines or 0)
            if a.deadline_at and a.deadline_at < now:
                bucket['overdue'] += 1
    movie_assigns = Assignment.query.filter((Assignment.movie_id == movie.id) | (Assignment.project == movie.code)).all()
    current_names = {(a.vo or '').strip().lower() for a in movie_assigns}
    role_row = next((a for a in movie_assigns if (norm_role(a.role) or a.role) == role_key), None)
    urgent_needed = bool(role_row.urgent) if role_row else any(bool(a.urgent) for a in movie_assigns)
    level_penalty = {'expert_old': -30, 'trained_new': 0, 'new_limited': 30}
    speed_penalty = {'normal': 0, 'slow': 18}
    scored: List[Dict[str, Any]] = []
    for vo in rows:
        key = (vo.name or '').strip().lower()
        linked = bool(vo.tg_user_id or vo.tg_username)
        seen_penalty = 18
        if vo.last_seen_at:
            seen_hours = max(0, int((now - vo.last_seen_at).total_seconds() // 3600))
            seen_penalty = min(30, seen_hours // 24)
        open_roles = stats.get(key, {}).get('open', 0)
        open_lines = stats.get(key, {}).get('lines_open', 0)
        overdue_roles = stats.get(key, {}).get('overdue', 0)
        recent_done = recent_done_counts.get(key, 0)
        same_movie = key in current_names
        score = (
            open_roles * 100
            + overdue_roles * 220
            + min(open_lines, 999)
            + (45 if same_movie else 0)
            + (60 if urgent_needed and not bool(vo.urgent_ok) else 0)
            + level_penalty.get((vo.level or '').strip().lower(), 10)
            + speed_penalty.get((vo.speed or '').strip().lower(), 10)
            + seen_penalty
            + (0 if linked else 30)
            - min(recent_done, 5) * 10
        )
        reasons = [
            f"{(vo.level or 'unknown').replace('_', ' ')}",
            f"open {open_roles}",
        ]
        if overdue_roles:
            reasons.append(f"overdue {overdue_roles}")
        if recent_done:
            reasons.append(f"recent done {recent_done}")
        if urgent_needed:
            reasons.append('urgent ok' if vo.urgent_ok else 'urgent check')
        if same_movie:
            reasons.append('already on this movie')
        reasons.append(_activity_freshness_label(vo.last_seen_at))
        scored.append({
            'vo': vo,
            'score': score,
            'open_roles': open_roles,
            'open_lines': open_lines,
            'overdue_roles': overdue_roles,
            'recent_done': recent_done,
            'same_movie': same_movie,
            'linked': linked,
            'urgent_needed': urgent_needed,
            'reasons': reasons,
        })
    scored.sort(key=lambda row: (row['score'], row['open_roles'], row['overdue_roles'], row['open_lines'], (row['vo'].name or '').lower()))
    return scored[:limit]
def _vo_shortlist_for_role(movie: Movie, role: str, limit: int = 6) -> List[VOTeam]:
    return [row['vo'] for row in _vo_candidate_rows(movie, role, limit=limit)]
def _vo_suggestion_text(movie: Movie, role: str, candidates: List[Dict[str, Any]]) -> str:
    role_key = norm_role(role) or role
    lines = [
        f"🎧 VO picks — {fmt_title_year(movie.title, movie.year)} [{movie.code}]",
        f"Role: {role_key}",
        "Sorted by live workload, overdue risk, level/speed, and recent activity.",
        "",
    ]
    for idx, row in enumerate(candidates[:6], start=1):
        vo = row['vo']
        reasons = ', '.join(row['reasons'][:5])
        lines.append(f"{idx}. {vo.name} — {reasons}")
    if not candidates:
        lines.append('No matching active VO found.')
    lines.extend(['', f"Quick use: /reassign_vo {movie.code} | {role_key} | <VO name>"])
    return '\n'.join(lines)
def _reassign_vo_prompt_text(movie: Movie) -> str:
    assigns = Assignment.query.filter((Assignment.movie_id == movie.id) | (Assignment.project == movie.code)).order_by(Assignment.role.asc()).all()
    lines = [
        f"🎙️ Reassign VO — {fmt_title_year(movie.title, movie.year)} [{movie.code}]",
        "Send: role | VO name",
        "Example: man1 | Faiz",
        "Use /cancel to stop.",
        "",
    ]
    if assigns:
        lines.append("Current roles")
        for a in assigns[:20]:
            lines.append(f"• {a.role} → {a.vo} ({int(a.lines or 0)})")
            picks = _vo_candidate_rows(movie, a.role, limit=3)
            for row in picks:
                vo = row['vo']
                reasons = ', '.join(row['reasons'][:3])
                lines.append(f"  - {vo.name} • {reasons}")
    else:
        lines.append("No assignments found for this movie.")
    return '\n'.join(lines)
async def _start_reassign_vo_prompt(context: ContextTypes.DEFAULT_TYPE, user_id: int, movie: Movie, source_message=None) -> bool:
    PANEL_PROMPT[user_id] = {"mode": "reassign_vo", "movie_code": movie.code}
    prompt = _reassign_vo_prompt_text(movie)
    if source_message and getattr(getattr(source_message, 'chat', None), 'type', None) == ChatType.PRIVATE:
        await source_message.reply_text(prompt, disable_web_page_preview=True)
        return True
    try:
        await context.bot.send_message(chat_id=int(user_id), text=prompt, disable_web_page_preview=True)
        return True
    except Exception:
        return False
def _resolve_vo_name(who: str, gender: Optional[str] = None) -> tuple[Optional[VOTeam], str]:
    raw = (who or '').strip()
    key = raw.lstrip('@').strip()
    if not key:
        return None, raw
    q = VOTeam.query.filter_by(active=True)
    if gender:
        q = q.filter_by(gender=gender)
    rows = q.order_by(VOTeam.name.asc()).all()
    for vo in rows:
        if vo.tg_username and vo.tg_username.lower() == key.lower():
            return vo, vo.name
        if vo.name and vo.name.lower() == key.lower():
            return vo, vo.name
    for vo in rows:
        if vo.tg_username and key.lower() in vo.tg_username.lower():
            return vo, vo.name
        if vo.name and key.lower() in vo.name.lower():
            return vo, vo.name
    return None, raw
async def _reassign_vo_role(movie: Movie, role_token: str, who: str, context: ContextTypes.DEFAULT_TYPE | None = None) -> tuple[bool, str]:
    role = norm_role(role_token) or (role_token or '').strip()
    if not role:
        return False, '❌ Missing role'
    assigns = Assignment.query.filter(((Assignment.movie_id == movie.id) | (Assignment.project == movie.code)) & (Assignment.role.ilike(role))).all()
    if not assigns:
        return False, f'❌ Role not found for this movie: {role_token}'
    gender = role_gender(role)
    vo_row, vo_name = _resolve_vo_name(who, gender=gender)
    prev_assigns = [{"id": a.id, "vo": a.vo} for a in assigns]
    prev_submissions = [_vo_submission_snapshot(s) for s in VORoleSubmission.query.filter_by(movie=movie.code).filter(VORoleSubmission.role.ilike(role)).all()]
    for a in assigns:
        a.vo = vo_name
    removed = VORoleSubmission.query.filter_by(movie=movie.code).filter(VORoleSubmission.role.ilike(role)).delete(synchronize_session=False)
    movie.updated_at = _now_utc()
    db.session.commit()
    try:
        if context:
            await _try_update_movie_card(context, movie)
    except Exception:
        pass
    if context is not None:
        setattr(context, "_last_undo_payload", {
            "kind": "vo",
            "payload": {
                "role": role,
                "assignments": prev_assigns,
                "submissions": prev_submissions,
            },
        })
    suffix = f" • reset submissions: {removed}" if removed else ''
    roster_note = f" • roster match: {vo_row.name}" if vo_row else ' • roster match: manual text'
    record_movie_event(movie, "REASSIGN_VO", f"Role {role} → {vo_name}", detail=f"reset_submissions={removed}", actor_source="tg", actor_name="reassign_vo")
    return True, f"✅ Reassigned {movie.code} {role} → {vo_name}{suffix}{roster_note}"
def _movie_history_text(movie: Movie, limit: int = 12) -> str:
    rows = fetch_movie_history(movie, limit=limit)
    lines = [f"🕘 Movie history — {fmt_title_year(movie.title, movie.year)} [{movie.code}]", ""]
    if not rows:
        lines.append("No history yet.")
    return "\n".join(lines)
    for ev in rows:
        ts = fmt_myt(getattr(ev, "created_at", None)) if getattr(ev, "created_at", None) else "-"
        actor = " / ".join([x for x in [getattr(ev, "actor_source", None), getattr(ev, "actor_name", None)] if x]) or "-"
        lines.append(f"• {ts} • {ev.event_type or 'INFO'}")
        lines.append(f"  {ev.summary or '-'}")
        if getattr(ev, "detail", None):
            lines.append(f"  {ev.detail}")
        lines.append(f"  actor: {actor}")
        lines.append("")
    return "\n".join(lines)
async def cmd_movie_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    query = _context_args_text(context)
    if not query:
        return await update.effective_message.reply_text("Usage: /movie_history <MOVIE_CODE or title>")
    m, err = _require_movie_arg(query)
    if not m:
        return await update.effective_message.reply_text(err)
    await update.effective_message.reply_text(_movie_history_text(m), disable_web_page_preview=True)
async def cmd_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    args = list(getattr(context, "args", []) or [])
    limit = 12
    source = "all"
    event_type = "all"
    q_parts = []
    known_events = {"CREATE_PROJECT", "ASSIGN_TRANSLATOR", "REASSIGN_VO", "CLEAR_ACTIVE", "ARCHIVE", "UNARCHIVE", "HARD_DELETE", "STATUS"}
    for tok in args:
        t = str(tok or "").strip()
        low = t.lower()
        up = t.upper()
        if t.isdigit():
            limit = max(1, min(int(t), 30))
        elif low in ("web", "tg"):
            source = low
        elif up in known_events:
            event_type = up
        else:
            q_parts.append(t)
    query = " ".join(q_parts).strip().lower()
    rows = []
    try:
        events = fetch_recent_movie_events(limit=max(limit * 6, 60), include_archived=True)
        for ev in events:
            hay = " | ".join([
                str(ev.movie_code or ""),
                str(ev.movie_title or ""),
                str(ev.summary or ""),
                str(ev.detail or ""),
                str(ev.event_type or ""),
                str(ev.actor_source or ""),
                str(ev.actor_name or ""),
            ]).lower()
            if query and query not in hay:
                continue
            if source != "all" and (ev.actor_source or "").lower() != source:
                continue
            if event_type != "all" and (ev.event_type or "").upper() != event_type:
                continue
            rows.append(ev)
            if len(rows) >= limit:
                break
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        rows = []
    header_bits = [f"last {limit}"]
    if source != "all":
        header_bits.append(source)
    if event_type != "all":
        header_bits.append(event_type)
    if query:
        header_bits.append(f"q={query}")
    lines = [f"📡 Recent movie activity — {' • '.join(header_bits)}", ""]
    for ev in rows:
        when = ev.created_at.strftime("%Y-%m-%d %H:%M") if ev.created_at else "-"
        title = fmt_title_year(ev.movie_title, None) if (ev.movie_title or "").strip() else (ev.movie_code or "-")
        code = (ev.movie_code or "").strip() or "-"
        actor = " / ".join([x for x in [ev.actor_source, ev.actor_name] if x]) or "-"
        lines.append(f"• {when} | {title} [{code}]")
        lines.append(f"  {ev.event_type or 'INFO'} — {ev.summary or '-'}")
        if ev.detail:
            lines.append(f"  {ev.detail}")
        lines.append(f"  by {actor}")
        lines.append("")
    if not rows:
        lines.append("No activity matched the current filters.")
    lines.append("Usage: /activity 20 | /activity ARCHIVE | /activity tg | /activity web assign")
    await _send_chunked_text(update.effective_message, "\n".join(lines), disable_web_page_preview=True)
def _movie_keyboard(code: str) -> InlineKeyboardMarkup:
    # Movie status buttons (admin only — enforced in callback)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📥 Received", callback_data=f"mv|recv|{code}"),
                InlineKeyboardButton("🧪 QA Ready", callback_data=f"mv|qa|{code}"),
            ],
            [
                InlineKeyboardButton("🧩 Wait Embed", callback_data=f"mv|embed|{code}"),
                InlineKeyboardButton("✅ Completed", callback_data=f"mv|done|{code}"),
            ],
            [
                InlineKeyboardButton("📊 Progress", callback_data=f"mv|prog|{code}"),
                InlineKeyboardButton("📌 Who Has", callback_data=f"mv|who|{code}"),
            ],
            [
                InlineKeyboardButton("🕘 History", callback_data=f"mv|hist|{code}"),
            ],
            [
                InlineKeyboardButton("🎯 Translator Picks", callback_data=f"mv|picks|{code}"),
                InlineKeyboardButton("👤 Assign Translator", callback_data=f"mv|assign|{code}"),
            ],
            [
                InlineKeyboardButton("🎧 VO Picks", callback_data=f"mv|vopicks|{code}"),
                InlineKeyboardButton("🎙️ Reassign VO", callback_data=f"mv|revo|{code}"),
            ],
            [
                InlineKeyboardButton("📦 Workload", callback_data=f"mv|load|{code}"),
                InlineKeyboardButton("⏰ Deadlines", callback_data=f"mv|dead|{code}"),
            ],
            [
                InlineKeyboardButton("🔔 Remind", callback_data=f"mv|remind|{code}"),
                InlineKeyboardButton("🧹 Clear Movie", callback_data=f"mv|clear|{code}"),
            ],
            [
                InlineKeyboardButton("🗃️ Archive", callback_data=f"mv|archask|{code}"),
                InlineKeyboardButton("💥 Hard Delete", callback_data=f"mv|delask|{code}"),
            ],
            [
                InlineKeyboardButton("🪪 Send Card", callback_data=f"mv|card|{code}"),
            ],
        ]
    )
def _movie_admin_confirm_keyboard(code: str, kind: str) -> InlineKeyboardMarkup:
    if kind == "archive":
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirm Archive", callback_data=f"mv|archgo|{code}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"mv|archcx|{code}"),
        ]])
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm Hard Delete", callback_data=f"mv|delgo|{code}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"mv|delcx|{code}"),
    ]])
def _archive_movie_record_db(movie: Movie) -> None:
    record_movie_event(movie, "ARCHIVE", "Bot archived movie", detail="Hidden from Telegram search", actor_source="tg", actor_name="movie_card")
    movie.is_archived = True
    movie.archived_at = _now_utc()
    movie.status = "ARCHIVED"
    movie.updated_at = _now_utc()
    movie.translator_assigned = None
    Assignment.query.filter((Assignment.movie_id == movie.id) | (Assignment.project == movie.code)).delete(synchronize_session=False)
    VORoleSubmission.query.filter_by(movie=movie.code).delete(synchronize_session=False)
    TranslationTask.query.filter((TranslationTask.movie_id == movie.id) | (TranslationTask.movie_code == movie.code)).delete(synchronize_session=False)
def _hard_delete_movie_record_db(movie: Movie) -> tuple[str, str]:
    code = (movie.code or "").strip()
    label = fmt_title_year(movie.title, movie.year)
    record_movie_event(movie, "HARD_DELETE", "Bot hard deleted movie", detail="Permanent delete from Telegram", actor_source="tg", actor_name="movie_card")
    mid = movie.id
    Assignment.query.filter((Assignment.movie_id == mid) | (Assignment.project == code)).delete(synchronize_session=False)
    VORoleSubmission.query.filter_by(movie=code).delete(synchronize_session=False)
    TranslationTask.query.filter((TranslationTask.movie_id == mid) | (TranslationTask.movie_code == code)).delete(synchronize_session=False)
    TranslationSubmission.query.filter((TranslationSubmission.movie_id == mid) | (TranslationSubmission.movie == code)).delete(synchronize_session=False)
    GroupOpenRequest.query.filter((GroupOpenRequest.movie_id == mid) | (GroupOpenRequest.movie_code == code)).delete(synchronize_session=False)
    db.session.delete(movie)
    return label, code
async def _send_movie_card_message(message, movie: Movie):
    txt = _movie_card_text(movie)
    msg = await message.reply_text(
        txt,
        reply_markup=_movie_keyboard(movie.code),
        parse_mode=ParseMode.MARKDOWN,
    )
    try:
        movie.movie_card_chat_id = msg.chat_id
        movie.movie_card_message_id = msg.message_id
        movie.updated_at = _now_utc()
        db.session.commit()
    except Exception:
        db.session.rollback()
    return msg
async def _start_assign_translator_prompt(context: ContextTypes.DEFAULT_TYPE, user_id: int, movie: Movie, source_message=None) -> bool:
    PANEL_PROMPT[user_id] = {"mode": "assign_tr_name", "movie_code": movie.code}
    prompt = "\n".join([
        f"👤 Assign translator for {fmt_title_year(movie.title, movie.year)} [{movie.code}]",
        "Send translator name or @username.",
        "Example: Ryan  or  @ryan",
        "Use /cancel to stop.",
    ])
    if source_message and getattr(getattr(source_message, 'chat', None), 'type', None) == ChatType.PRIVATE:
        await source_message.reply_text(prompt, disable_web_page_preview=True)
        return True
    try:
        await context.bot.send_message(chat_id=int(user_id), text=prompt, disable_web_page_preview=True)
        return True
    except Exception:
        return False
def _send_text_chunks_sync(text: str, max_len: int = 3500) -> List[str]:
    chunks = []
    current = []
    current_len = 0
    for line in (text or "").split("\n"):
        add_len = len(line) + (1 if current else 0)
        if current and current_len + add_len > max_len:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += add_len
    if current:
        chunks.append("\n".join(current))
    return chunks or [""]
async def _send_chunked_text(message, text: str, disable_web_page_preview: bool = True):
    for chunk in _send_text_chunks_sync(text):
        await message.reply_text(chunk, disable_web_page_preview=disable_web_page_preview)
def _parse_query_limit(args: List[str], default_limit: int = 6, max_limit: int = 20) -> tuple[str, int]:
    raw = " ".join(args or []).strip()
    if not raw:
        return "", default_limit
    parts = raw.split()
    limit = default_limit
    if parts and parts[-1].isdigit():
        limit = max(1, min(max_limit, int(parts[-1])))
        raw = " ".join(parts[:-1]).strip()
    return raw, limit
def _cleanup_bulk_movie_actions() -> None:
    now = _now_utc()
    stale = []
    for token, payload in BULK_MOVIE_ACTIONS.items():
        created = payload.get("created_at")
        if not created or now - created > timedelta(minutes=BULK_MOVIE_ACTION_TTL_MIN):
            stale.append(token)
    for token in stale:
        BULK_MOVIE_ACTIONS.pop(token, None)
def _bulk_movie_confirm_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[ 
        InlineKeyboardButton("✅ Confirm", callback_data=f"bm|go|{token}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"bm|cx|{token}"),
    ]])
def _create_bulk_movie_action(kind: str, user_id: int, codes: List[str]) -> str:
    _cleanup_bulk_movie_actions()
    token = secrets.token_hex(8)
    BULK_MOVIE_ACTIONS[token] = {
        "kind": kind,
        "user_id": int(user_id),
        "codes": [str(c).strip().upper() for c in codes if str(c).strip()],
        "created_at": _now_utc(),
    }
    return token
def _bulk_movie_preview_text(kind: str, movies: List[Movie]) -> str:
    verb = "archive" if kind == "archive" else "unarchive"
    lines = [f"Bulk {verb} preview", ""]
    for m in movies:
        lines.append(f"• {fmt_title_year(m.title, m.year)} [{(m.lang or '').upper() or '-'}] — {m.code}")
    lines.append("")
    lines.append(f"Total: {len(movies)} movie(s)")
    lines.append("Confirm to continue.")
    return "\n".join(lines)
async def _callback_bulk_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    try:
        _, action, token = data.split("|", 2)
    except ValueError:
        return
    if not _is_admin(update):
        return await q.answer("Not allowed", show_alert=True)
    _cleanup_bulk_movie_actions()
    payload = BULK_MOVIE_ACTIONS.get(token)
    if not payload:
        return await q.answer("Bulk action expired", show_alert=True)
    if int(payload.get("user_id") or 0) != int(getattr(getattr(update, "effective_user", None), "id", 0) or 0):
        return await q.answer("This bulk action belongs to another admin", show_alert=True)
    if action == "cx":
        BULK_MOVIE_ACTIONS.pop(token, None)
        return await _safe_edit(q, "Cancelled bulk movie action.")
    kind = payload.get("kind")
    codes = [str(c).strip().upper() for c in (payload.get("codes") or []) if str(c).strip()]
    changed = []
    skipped = []
    for code in codes:
        movie = movie_by_code(code, include_archived=True)
        if not movie:
            skipped.append(f"{code}: missing")
            continue
        if kind == "archive":
            if getattr(movie, "is_archived", False):
                skipped.append(f"{code}: already archived")
                continue
            _archive_movie_record_db(movie)
            changed.append(code)
        elif kind == "unarchive":
            if not getattr(movie, "is_archived", False):
                skipped.append(f"{code}: already active")
                continue
            movie.is_archived = False
            movie.archived_at = None
            if (movie.status or "").upper() == "ARCHIVED":
                movie.status = "RECEIVED"
            movie.updated_at = _now_utc()
            record_movie_event(movie, "UNARCHIVE", "Bot bulk unarchived movie", detail="Bulk Telegram command", actor_source="tg", actor_name="bulk_unarchive")
            changed.append(code)
        else:
            skipped.append(f"{code}: unsupported")
    db.session.commit()
    BULK_MOVIE_ACTIONS.pop(token, None)
    verb = "Archived" if kind == "archive" else "Unarchived"
    lines = [f"✅ {verb} {len(changed)} movie(s)"]
    if changed:
        lines.append("")
        for code in changed[:12]:
            m = movie_by_code(code, include_archived=True)
            if m:
                lines.append(f"• {fmt_title_year(m.title, m.year)} — {code}")
            else:
                lines.append(f"• {code}")
    if skipped:
        lines.append("")
        lines.append("Skipped: " + "; ".join(skipped[:8]) + (" ..." if len(skipped) > 8 else ""))
    return await _safe_edit(q, "\n".join(lines))
def _panel_keyboard_for_update(update: Update) -> InlineKeyboardMarkup:
    is_admin = _is_admin(update)
    buttons = []
    if is_admin:
        buttons.extend(
            [
                [
                    InlineKeyboardButton("🎬 Create Project", callback_data="panel|wizard"),
                    InlineKeyboardButton("🔎 Find Movie", callback_data="panel|find"),
                ],
                [
                    InlineKeyboardButton("🗃️ Archived", callback_data="panel|archived"),
                    InlineKeyboardButton("📡 Activity", callback_data="panel|activity"),
                ],
                [
                    InlineKeyboardButton("🧹 Cleanup", callback_data="panel|cleanup"),
                    InlineKeyboardButton("🧬 Duplicates", callback_data="panel|duplicates"),
                ],
                [
                    InlineKeyboardButton("📦 Workload", callback_data="panel|workload"),
                    InlineKeyboardButton("📌 Who Has", callback_data="panel|whohas"),
                ],
                [
                    InlineKeyboardButton("👤 Assign Translator", callback_data="panel|assign_tr"),
                    InlineKeyboardButton("🎙️ Reassign VO", callback_data="panel|reassign_vo"),
                ],
                [
                    InlineKeyboardButton("🎬 Movie Workload", callback_data="panel|movie_load"),
                    InlineKeyboardButton("🚨 Overdue", callback_data="panel|overdue"),
                ],
                [
                    InlineKeyboardButton("🔥 Priority", callback_data="panel|priority"),
                    InlineKeyboardButton("📣 Remind Overdue", callback_data="panel|remind_overdue"),
                ],
                [
                    InlineKeyboardButton("🗓️ Daily Summary", callback_data="panel|daily_summary"),
                    InlineKeyboardButton("📬 Digest Now", callback_data="panel|digest_now"),
                ],
                [
                    InlineKeyboardButton("💾 Backup Now", callback_data="panel|backup_now"),
                ],
                [
                    InlineKeyboardButton("📍 Backup Status", callback_data="panel|backup_status"),
                ],
            ]
        )
    buttons.extend(
        [
            [
                InlineKeyboardButton("🧾 My Tasks", callback_data="panel|my_tasks"),
                InlineKeyboardButton("🎙️ My Roles", callback_data="panel|my_roles"),
            ],
            [
                InlineKeyboardButton("❓ Help", callback_data="panel|help"),
                InlineKeyboardButton("🔄 Refresh", callback_data="panel|refresh"),
            ],
        ]
    )
    return InlineKeyboardMarkup(buttons)
def _panel_intro_text(update: Update) -> str:
    is_admin = _is_admin(update)
    role = "Admin" if is_admin else ("Translator" if _is_dm(update) else "VO")
    lines = [
        f"🤖 {BOT_NAME} panel",
        f"Role: {role}",
        f"Version: {APP_VERSION}",
        "",
    ]
    if is_admin:
        lines.extend(
            [
                "Quick actions:",
                "• Create project wizard",
                "• Find movie by title",
                "• Workload summary",
                "• Check one movie owner/status",
                "• Reassign VO / movie workload",
                "• Activity feed / archived recovery",
                "• Duplicate search / merge cleanup",
                "• Overdue list / reminder control",
                "• Priority movies / batch reminders / daily summary / digest",
                "• Backup now / backup status",
                "• Archived list / unarchive",
                "",
            ]
        )
    lines.extend(
        [
            "Self-service:",
            "• My Tasks",
            "• My Roles",
            "",
            "Use the buttons below.",
        ]
    )
    return "\n".join(lines)
async def _safe_edit(q, text: str, reply_markup=None):
    try:
        await q.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    except BadRequest as e:
        msg = str(e)
        if "Message is not modified" in msg:
            await q.answer("No changes.")
            return
        if "parse entities" in msg.lower():
            await q.edit_message_text(text=text, reply_markup=reply_markup)
            return
        raise
async def _notify_admin(context: ContextTypes.DEFAULT_TYPE, text_msg: str):
    if not ADMIN_TELEGRAM_CHAT_ID:
        return
    try:
        await context.bot.send_message(chat_id=int(ADMIN_TELEGRAM_CHAT_ID), text=text_msg, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass
async def _try_update_movie_card(context: ContextTypes.DEFAULT_TYPE, movie: Movie):
    if movie.movie_card_chat_id and movie.movie_card_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=int(movie.movie_card_chat_id),
                message_id=int(movie.movie_card_message_id),
                text=_movie_card_text(movie),
                reply_markup=_movie_keyboard(movie.code),
                parse_mode=ParseMode.MARKDOWN,
            )
        except BadRequest:
            pass
        except Exception:
            pass
    try:
        if movie.vo_group_chat_id and not bool(movie.is_archived):
            await _upsert_public_assignment_card(context, movie, pin=False)
    except Exception:
        pass
async def _archive_movie(context: ContextTypes.DEFAULT_TYPE, movie: Movie):
    """Post a completion summary + latest SRT to ARCHIVE_CHAT_ID (optional)."""
    if not ARCHIVE_CHAT_ID:
        return
    try:
        await context.bot.send_message(
            chat_id=int(ARCHIVE_CHAT_ID),
            text="\n".join(
                [
                    "✅ *ARCHIVED*",
                    f"🎬 *{movie.title}*{f' ({movie.year})' if movie.year else ''} [{(movie.lang or '').upper()}]",
                    f"🆔 `{movie.code}`",
                    f"🕒 Completed: {(_now_utc().strftime('%Y-%m-%d %H:%M UTC'))}",
                ]
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass
    # Send latest SRT if available
    try:
        sub = (
            TranslationSubmission.query.filter_by(movie_id=movie.id, content_type="document")
            .order_by(TranslationSubmission.submitted_at.desc())
            .first()
        )
        if sub and sub.file_id:
            caption = f"📎 Latest SRT for `{movie.code}`\nQueue ID: `{sub.id}`"
            await context.bot.send_document(
                chat_id=int(ARCHIVE_CHAT_ID),
                document=sub.file_id,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
            )
    except Exception:
        pass
# -----------------------------
# Admin whitelist commands
# -----------------------------
async def cmd_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await update.effective_message.reply_text(
        f"Your Telegram ID: {u.id}\nUsername: @{u.username or '-'}\nName: {u.full_name}",
        disable_web_page_preview=True,
    )
async def cmd_admin_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return await update.effective_message.reply_text("❌ Owner only")
    if not context.args:
        return await update.effective_message.reply_text("Usage: /admin_add <tg_id> [display_name]")
    try:
        tg_id = int(context.args[0])
    except Exception:
        return await update.effective_message.reply_text("❌ tg_id must be a number")
    name = " ".join(context.args[1:]).strip() or None
    row = AdminTelegramUser.query.filter_by(tg_user_id=tg_id).first()
    if row:
        row.active = True
        row.display_name = name or row.display_name
    else:
        db.session.add(AdminTelegramUser(tg_user_id=tg_id, display_name=name, role="ADMIN", active=True))
    db.session.commit()
    await update.effective_message.reply_text(f"✅ Added admin: {tg_id}")
async def cmd_admin_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return await update.effective_message.reply_text("❌ Owner only")
    if not context.args:
        return await update.effective_message.reply_text("Usage: /admin_remove <tg_id>")
    try:
        tg_id = int(context.args[0])
    except Exception:
        return await update.effective_message.reply_text("❌ tg_id must be a number")
    row = AdminTelegramUser.query.filter_by(tg_user_id=tg_id).first()
    if not row:
        return await update.effective_message.reply_text("Not found")
    row.active = False
    db.session.commit()
    await update.effective_message.reply_text(f"✅ Removed admin: {tg_id}")
async def cmd_admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    rows = AdminTelegramUser.query.filter_by(active=True).order_by(AdminTelegramUser.created_at.desc()).all()
    lines = []
    if OWNER_TG_ID:
        lines.append(f"OWNER_TG_ID: {OWNER_TG_ID}")
    for r in rows:
        nm = r.display_name or "-"
        lines.append(f"• {r.tg_user_id} ({nm})")
    if not lines:
        lines = ["(empty)"]
    await update.effective_message.reply_text("\n".join(lines))
async def cmd_vo_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin report similar to the Excel tracker: totals + pending by VO for a project/movie code."""
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    query = _context_args_text(context)
    if not query:
        return await update.effective_message.reply_text(
            "Usage: /vo_stats <MOVIE_CODE or title>\nExample: /vo_stats Inside Out 2"
        )
    mv, err = _require_movie_arg(query)
    if err:
        return await update.effective_message.reply_text(err)
    project = mv.code
    # Assignments are the source of truth for expected roles.
    assigns = Assignment.query.filter((Assignment.project == project) | (Assignment.movie_id == mv.id)).all()
    if not assigns:
        return await update.effective_message.reply_text(f"No assignments found for: {fmt_title_year(mv.title, mv.year)} [{project}]")
    # Map: vo -> assigned_lines / assigned_roles
    by_vo = {}
    expected_roles = set()
    for a in assigns:
        vo = (a.vo or "-").strip()
        by_vo.setdefault(vo, {"assigned_roles": 0, "assigned_lines": 0, "done_roles": 0, "done_lines": 0})
        by_vo[vo]["assigned_roles"] += 1
        by_vo[vo]["assigned_lines"] += int(a.lines or 0)
        expected_roles.add(a.role)
    subs = VORoleSubmission.query.filter_by(movie=project).all()
    done_roles = set()
    for s in subs:
        done_roles.add(s.role)
        vo = (s.vo or "-").strip()
        by_vo.setdefault(vo, {"assigned_roles": 0, "assigned_lines": 0, "done_roles": 0, "done_lines": 0})
        by_vo[vo]["done_roles"] += 1
        by_vo[vo]["done_lines"] += int(s.lines or 0)
    # Build plain text report (Telegram-safe)
    lines = []
    lines.append(f"VO Stats — {fmt_title_year(mv.title, mv.year)} [{project}]")
    lines.append("VO | roles(done/assigned) | lines(done/assigned) | pending")
    lines.append("-")
    def pending(v):
        return max(0, v["assigned_roles"] - v["done_roles"])
    for vo, v in sorted(by_vo.items(), key=lambda kv: (pending(kv[1]), kv[0].lower()), reverse=True):
        p = pending(v)
        lines.append(
            f"{vo} | {v['done_roles']}/{v['assigned_roles']} | {v['done_lines']}/{v['assigned_lines']} | {p}"
        )
    total_assigned_roles = sum(v["assigned_roles"] for v in by_vo.values())
    total_done_roles = sum(v["done_roles"] for v in by_vo.values())
    total_assigned_lines = sum(v["assigned_lines"] for v in by_vo.values())
    total_done_lines = sum(v["done_lines"] for v in by_vo.values())
    lines.append("")
    lines.append(
        f"TOTAL | {total_done_roles}/{total_assigned_roles} roles | {total_done_lines}/{total_assigned_lines} lines"
    )
    help_text = "\n".join(lines)
    max_len = 3500
    chunks = []
    current = []
    current_len = 0
    for line in help_text.split("\n"):
        add_len = len(line) + (1 if current else 0)
        if current and current_len + add_len > max_len:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += add_len
    if current:
        chunks.append("\n".join(current))
    for chunk in chunks:
        await update.effective_message.reply_text(chunk, disable_web_page_preview=True)
def _group_title_for_movie(m: Movie) -> str:
    title = (m.title or "").strip() or "Movie"
    year = (m.year or "").strip() or "????"
    lang = (m.lang or DEFAULT_LANG).strip() or DEFAULT_LANG
    code = (m.code or "").strip() or "CODE"
    try:
        out = GROUP_TITLE_TEMPLATE.format(code=code, title=title, year=(year or "").strip(), lang=lang)
        out = re.sub(r"\(\s*\)", "", out)
        out = re.sub(r"\s{2,}", " ", out).strip()
        return out
    except Exception:
        # fall back to safe default
        out = f"VO — {code} — {title} ({(year or '').strip()}) [{lang}]"
        out = re.sub(r"\(\s*\)", "", out)
        out = re.sub(r"\s{2,}", " ", out).strip()
        return out
async def cmd_request_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: create a pending request to open/bind a VO group for a movie."""
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    query = _context_args_text(context)
    if not query:
        return await update.effective_message.reply_text("Usage: /request_group <MOVIE_CODE or title>")
    m, err = _require_movie_arg(query)
    if not m:
        return await update.effective_message.reply_text(err)
    code = m.code
    # Avoid duplicate pending requests
    existing = GroupOpenRequest.query.filter_by(movie_id=m.id, status="PENDING").order_by(GroupOpenRequest.id.desc()).first()
    if existing:
        req = existing
    else:
        u = update.effective_user
        req = GroupOpenRequest(
            movie_id=m.id,
            movie_code=m.code or code,
            requested_by_tg_id=u.id,
            requested_by_name=(u.username or u.full_name or ""),
            status="PENDING",
        )
        db.session.add(req)
        db.session.commit()
    title = _group_title_for_movie(m)
    text = (
        f"🟦 Group Open Request (PENDING)\n"
        f"Movie: {fmt_title_year(m.title, m.year)}\n"
        f"Code: {m.code}\n"
        f"Proposed group name:\n{title}\n\n"
        f"Requested by: {req.requested_by_name or req.requested_by_tg_id}"
    )
    kb = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Approve", callback_data=f"grp|approve|{req.id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"grp|reject|{req.id}"),
        ]]
    )
    # Notify admin group if configured; otherwise reply in chat
    if ADMIN_TELEGRAM_CHAT_ID:
        try:
            await context.bot.send_message(chat_id=int(ADMIN_TELEGRAM_CHAT_ID), text=text, reply_markup=kb)
        except Exception as e:
            log.warning("Failed to send admin request message: %s", e)
            await update.effective_message.reply_text(text)
    else:
        await update.effective_message.reply_text(text, reply_markup=kb)
async def cmd_bind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bind the current group chat to a movie code (manual group creation flow)."""
    if update.effective_chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await update.effective_message.reply_text("❌ Use this inside the VO group")
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    query = _context_args_text(context)
    if not query:
        return await update.effective_message.reply_text("Usage: /bind <MOVIE_CODE or title>")
    m, err = _require_movie_arg(query)
    if not m:
        return await update.effective_message.reply_text(err)
    code = m.code
    # Optional gate: require an approved request (owner/admin can still bind)
    approved = GroupOpenRequest.query.filter_by(movie_id=m.id, status="APPROVED").order_by(GroupOpenRequest.id.desc()).first()
    if not approved and not _is_owner(update):
        return await update.effective_message.reply_text("❌ Not approved yet. Use /request_group and get admin approval.")
    m.vo_group_chat_id = update.effective_chat.id
    # Try to create an invite link (requires bot admin permissions)
    invite_link = None
    try:
        inv = await context.bot.create_chat_invite_link(chat_id=update.effective_chat.id, name=f"{m.code}-invite")
        invite_link = getattr(inv, "invite_link", None)
    except Exception as e:
        log.info("Invite link creation failed (needs admin perms): %s", e)
    if invite_link:
        m.vo_group_invite_link = invite_link
    db.session.commit()
    # Post a pinned header / assignments summary
    await update.effective_message.reply_text(
        f"✅ Bound this group to {m.code}\nTitle: {fmt_title_year(m.title, m.year)}\nLang: {lang_display(m.lang or DEFAULT_LANG)}",
        disable_web_page_preview=True,
    )
    # Notify ops
    if ADMIN_TELEGRAM_CHAT_ID:
        msg = f"✅ VO group bound\n{m.code} — {fmt_title_year(m.title, m.year)}\nchat_id: {m.vo_group_chat_id}"
        if invite_link:
            msg += f"\nInvite: {invite_link}"
        await context.bot.send_message(chat_id=int(ADMIN_TELEGRAM_CHAT_ID), text=msg, disable_web_page_preview=True)
    # Best-effort: DM invite link to OWNER + admin whitelist (only works if they started bot)
    if invite_link:
        targets = []
        if OWNER_TG_ID:
            try:
                targets.append(int(OWNER_TG_ID))
            except Exception:
                pass
        for a in AdminTelegramUser.query.filter_by(active=True).all():
            if a.tg_user_id:
                targets.append(int(a.tg_user_id))
        # de-duplicate
        seen = set()
        unique_targets = []
        for t in targets:
            if t not in seen:
                seen.add(t)
                unique_targets.append(t)
        for t in unique_targets:
            try:
                await context.bot.send_message(
                    chat_id=t,
                    text=(
                        f"🔗 Invite link for VO group\n"
                        f"{m.code} — {fmt_title_year(m.title, m.year)}\n"
                        f"{invite_link}"
                    ),
                    disable_web_page_preview=True,
                )
            except Exception:
                pass
    # Auto-post clean public VO card to the bound group + pin
    try:
        rows = Assignment.query.filter_by(project=m.code).order_by(Assignment.role.asc()).all()
        if rows:
            await _upsert_public_assignment_card(context, m, pin=True)
        else:
            await context.bot.send_message(
                chat_id=int(m.vo_group_chat_id),
                text=f"ℹ️ No assignments yet for {fmt_title_year(m.title, m.year)}. Create assignments in Web first.",
            )
    except Exception as e:
        log_event("WARN", "tg.bind", f"Assignment post failed for {m.code}: {e}")
    log_event("INFO", "tg.bind", f"Bound group chat_id={m.vo_group_chat_id} code={m.code} invite={'yes' if invite_link else 'no'}")
async def cmd_group_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reject a pending group-open request with a note."""
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    if len(context.args) < 2:
        return await update.effective_message.reply_text("Usage: /group_reject <REQUEST_ID> <note>")
    try:
        req_id = int(context.args[0])
    except ValueError:
        return await update.effective_message.reply_text("❌ REQUEST_ID must be a number")
    note = " ".join(context.args[1:]).strip()
    req = GroupOpenRequest.query.get(req_id)
    if not req:
        return await update.effective_message.reply_text("❌ Request not found")
    if req.status != "PENDING":
        return await update.effective_message.reply_text(f"❌ Request already {req.status}")
    u = update.effective_user
    req.status = "REJECTED"
    req.reviewed_by_tg_id = u.id
    req.reviewed_by_name = u.username or u.full_name
    req.reviewed_at = _now_utc()
    req.note = note
    db.session.commit()
    human = f"{req.title} ({req.year})" if req.year else req.title
    await update.effective_message.reply_text(f"❌ Rejected: {human}\nNote: {note}")
async def on_group_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not _is_admin(update):
        return await q.edit_message_text("❌ Not allowed")
    parts = (q.data or "").split("|")
    if len(parts) != 3:
        return
    _, action, rid = parts
    try:
        rid_i = int(rid)
    except ValueError:
        return
    req = GroupOpenRequest.query.get(rid_i)
    if not req:
        return await q.edit_message_text("❌ Request not found")
    if req.status != "PENDING":
        return await q.edit_message_text(f"ℹ️ Request already {req.status}")
    m = Movie.query.get(req.movie_id)
    if not m:
        return await q.edit_message_text("❌ Movie not found")
    u = update.effective_user
    if action == "approve":
        req.status = "APPROVED"
        req.reviewed_by_tg_id = u.id
        req.reviewed_by_name = u.username or u.full_name
        req.reviewed_at = _now_utc()
        db.session.commit()
        title = _group_title_for_movie(m)
        instr = (
            f"✅ Approved (Manual create flow A)\n\n"
            f"Next steps:\n"
            f"1) Admin create a Telegram group named:\n{title}\n"
            f"2) Add this bot as ADMIN in that group\n"
            f"3) In that group, run: /bind {m.code}\n\n"
            f"After binding, bot will sync assignments + track VO submissions for {m.code}."
        )
        await q.edit_message_text(instr, disable_web_page_preview=True)
        return
    if action == "reject":
        # Inline reject can't capture note; ask for /group_reject
        await q.edit_message_text(
            f"❌ Reject requested. Send:\n/group_reject {req.id} <note>\n\n(Movie: {fmt_title_year(m.title, m.year)} — {m.lang})",
            disable_web_page_preview=True,
        )
        return
# -----------------------------
# Project wizard (admin interactive)
# -----------------------------
def _project_wizard_lang_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("BN / Bengali", callback_data="wiz|lang|bn"),
                InlineKeyboardButton("MS / Malay", callback_data="wiz|lang|ms"),
            ],
            [
                InlineKeyboardButton("EN / English", callback_data="wiz|lang|en"),
            ],
            [InlineKeyboardButton("Cancel", callback_data="wiz|cancel|")],
        ]
    )
def _project_wizard_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Super Urgent 12h", callback_data="wiz|mode|superurgent"),
                InlineKeyboardButton("Urgent 24h", callback_data="wiz|mode|urgent"),
            ],
            [
                InlineKeyboardButton("Non-Urgent 36h", callback_data="wiz|mode|nonurgent"),
                InlineKeyboardButton("Flexible 48h", callback_data="wiz|mode|flexible"),
            ],
            [InlineKeyboardButton("Cancel", callback_data="wiz|cancel|")],
        ]
    )
def _clear_project_wizard(user_id: int) -> None:
    PROJECT_WIZARD.pop(int(user_id), None)
async def _finish_project_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE, state: Dict[str, Any], roles_blob: str) -> None:
    roles_blob = _normalize_roles_blob(roles_blob)
    parsed = parse_lines(roles_blob)
    if not parsed:
        await update.effective_message.reply_text(
            "❌ I couldn't parse the role list.\nSend lines like:\nman-1 120\nfem-1 80\n\nOr /project_cancel to abort."
        )
        return
    project_raw = (state.get('project_raw') or '').strip()
    year_raw = (state.get('year_raw') or '').strip()
    lang_raw = (state.get('lang_raw') or DEFAULT_LANG).strip()
    title_override = (state.get('title_override') or '').strip()
    mode_raw = _normalize_priority_mode(state.get('mode_raw') or 'urgent')
    year_val = int(year_raw) if year_raw.isdigit() else None
    lang_val = _slug_lang(lang_raw or DEFAULT_LANG)
    exact_code = _extract_movie_code(project_raw)
    created = False
    if exact_code:
        movie = movie_by_code(exact_code)
        if not movie:
            base_title = title_override or exact_code
            movie = upsert_movie(base_title, year_val, lang_val)
            if movie.code != exact_code:
                movie.code = exact_code
            if title_override:
                movie.title = title_override
            if year_val:
                movie.year = str(year_val)
            if lang_val:
                movie.lang = lang_val
            movie.updated_at = _now_utc()
            db.session.commit()
            created = True
        else:
            if title_override:
                movie.title = title_override
            if year_val:
                movie.year = str(year_val)
            if lang_val:
                movie.lang = lang_val
            movie.updated_at = _now_utc()
            db.session.commit()
    else:
        movie, created = get_or_create_movie(project_raw, year_val, lang_val)
        if title_override and (movie.title or '').strip() != title_override:
            movie.title = title_override
            movie.updated_at = _now_utc()
            db.session.commit()
    urgent = _priority_mode_urgent_only(mode_raw)
    results = _auto_assign_movie_roles(movie, parsed, urgent=urgent, replace_existing=True, priority_mode=mode_raw)
    assigned = [r for r in results if r.get('vo')]
    missing = [r for r in results if not r.get('vo')]
    try:
        movie.status = 'VO_ASSIGNED' if assigned else (movie.status or 'RECEIVED')
        movie.updated_at = _now_utc()
        record_movie_event(movie, "CREATE_PROJECT", f"Bot created project with {len(assigned)} assigned role(s)", detail=f"mode={mode_raw} • urgent={urgent} • created_movie={'yes' if created else 'no'}", actor_source="tg", actor_name="create_project")
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
    _clear_project_wizard(update.effective_user.id)
    out = [
        f"✅ Project ready: {fmt_title_year(movie.title, movie.year)} [{movie.code}]",
        f"Mode: {_priority_mode_label(mode_raw)} ({_priority_mode_hours(mode_raw)}h)",
        f"Created movie: {'yes' if created else 'no'}",
        f"Assignments created: {len(assigned)}/{len(results)}",
        '',
    ]
    for row in assigned[:20]:
        out.append(f"• {row['role']} → {row['vo']} ({row['lines']})")
    if missing:
        out.append('')
        out.append('Unassigned:')
        for row in missing[:10]:
            out.append(f"• {row['role']} ({row['lines']})")
    await update.effective_message.reply_text('\n'.join(out), disable_web_page_preview=True)
async def cmd_project_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    raw = _context_args_text(context)
    user_id = update.effective_user.id
    PROJECT_WIZARD[user_id] = {
        'step': 'await_title',
        'project_raw': '',
        'year_raw': '',
        'lang_raw': '',
        'mode_raw': '',
        'title_override': '',
        'chat_id': getattr(update.effective_chat, 'id', None),
    }
    if raw:
        PROJECT_WIZARD[user_id]['project_raw'] = raw
        PROJECT_WIZARD[user_id]['step'] = 'await_year'
        return await update.effective_message.reply_text(
            f"🎬 Project wizard started.\nMovie: {raw}\n\nSend year (example: 2024) or type skip."
        )
    await update.effective_message.reply_text(
        "🎬 Project wizard started.\nSend the movie name or movie code first.\n\nExample: Inside Out 2\nOr: BN-260309-01"
    )
async def cmd_project_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = getattr(getattr(update, 'effective_user', None), 'id', None)
    if uid is None or uid not in PROJECT_WIZARD:
        return await update.effective_message.reply_text("No active project wizard.")
    _clear_project_wizard(uid)
    await update.effective_message.reply_text("Project wizard cancelled.")
async def _handle_project_wizard_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not _is_dm(update):
        return False
    uid = getattr(getattr(update, 'effective_user', None), 'id', None)
    msg = update.effective_message
    if not uid or not msg or not msg.text:
        return False
    state = PROJECT_WIZARD.get(uid)
    if not state:
        return False
    text = (msg.text or '').strip()
    step = state.get('step') or 'await_title'
    if text.lower() in {'/project_cancel', 'cancel', 'stop'}:
        _clear_project_wizard(uid)
        await msg.reply_text("Project wizard cancelled.")
        return True
    if step == 'await_title':
        state['project_raw'] = text
        state['step'] = 'await_year'
        await msg.reply_text("Send year (example: 2024) or type skip.")
        return True
    if step == 'await_year':
        if text.lower() in {'skip', '-', 'none', 'na', 'n/a'}:
            state['year_raw'] = ''
        elif re.fullmatch(r'\d{4}', text):
            state['year_raw'] = text
        else:
            await msg.reply_text("Please send a 4-digit year like 2024, or type skip.")
            return True
        state['step'] = 'await_lang'
        await msg.reply_text("Choose language:", reply_markup=_project_wizard_lang_keyboard())
        return True
    if step == 'await_roles':
        await _finish_project_wizard(update, context, state, text)
        return True
    await msg.reply_text("Continue using the buttons, or /project_cancel to abort.")
    return True
async def _callback_project_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ''
    try:
        _, action, value = data.split('|', 2)
    except ValueError:
        return
    if not _is_admin(update):
        return await q.answer("Not allowed", show_alert=True)
    uid = getattr(getattr(update, 'effective_user', None), 'id', None)
    if not uid or uid not in PROJECT_WIZARD:
        return await q.answer("Wizard expired. Run /project_wizard again.", show_alert=True)
    state = PROJECT_WIZARD[uid]
    if action == 'cancel':
        _clear_project_wizard(uid)
        await q.edit_message_text("Project wizard cancelled.")
        return
    if action == 'lang':
        state['lang_raw'] = _slug_lang(value or DEFAULT_LANG)
        state['step'] = 'await_mode'
        await q.edit_message_text(
            f"Language set: {lang_display(state['lang_raw'])}\n\nChoose assignment mode:",
            reply_markup=_project_wizard_mode_keyboard(),
        )
        return
    if action == 'mode':
        mode_raw = (value or 'urgent').strip().lower()
        if mode_raw not in {'superurgent', 'urgent', 'nonurgent', 'flexible'}:
            mode_raw = 'urgent'
        state['mode_raw'] = mode_raw
        state['step'] = 'await_roles'
        await q.edit_message_text(
            "Mode set: {}\n\nNow send the role list.\nExample:\nman-1 120\nfem-1 80\nman-2 55".format(
                f"{_priority_mode_label(mode_raw)} ({_priority_mode_hours(mode_raw)}h)"
            )
        )
        return
async def cmd_workload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    args = [str(a).strip() for a in (getattr(context, 'args', None) or []) if str(a).strip()]
    scope = (" ".join(args).strip().lower() or 'all')
    now = _now_utc()
    lines = ["📊 Workload summary", ""]
    if scope in {'all', 'translator', 'translators', 'tr'}:
        tasks = TranslationTask.query.order_by(TranslationTask.id.desc()).all()
        active = [t for t in tasks if (t.status or '').upper() != 'COMPLETED']
        overdue = [t for t in active if t.deadline_at and t.deadline_at < now]
        by_tr: Dict[str, Dict[str, int]] = {}
        for t in tasks:
            name = (t.translator_name or '-').strip() or '-'
            row = by_tr.setdefault(name, {'active': 0, 'done': 0, 'overdue': 0})
            if (t.status or '').upper() == 'COMPLETED':
                row['done'] += 1
            else:
                row['active'] += 1
                if t.deadline_at and t.deadline_at < now:
                    row['overdue'] += 1
        top_tr = sorted(by_tr.items(), key=lambda kv: (-kv[1]['active'], -kv[1]['overdue'], kv[0].lower()))[:8]
        lines += [
            f"Translator tasks: {len(tasks)} total",
            f"Active: {len(active)}",
            f"Overdue: {len(overdue)}",
        ]
        if top_tr:
            lines.append('')
            lines.append('Top translators')
            for name, stat in top_tr:
                lines.append(f"• {name} — active {stat['active']}, done {stat['done']}, overdue {stat['overdue']}")
        lines.append('')
    if scope in {'all', 'vo', 'voice', 'team'}:
        assignments = Assignment.query.order_by(Assignment.id.desc()).all()
        submits = VORoleSubmission.query.order_by(VORoleSubmission.id.desc()).all()
        submitted_pairs = {(s.movie, norm_role(s.role), (s.vo or '').strip().lower()) for s in submits}
        by_vo: Dict[str, Dict[str, int]] = {}
        open_count = 0
        for a in assignments:
            name = (a.vo or '-').strip() or '-'
            role_key = norm_role(a.role)
            done = ((a.project or ''), role_key, name.lower()) in submitted_pairs
            row = by_vo.setdefault(name, {'open': 0, 'done': 0, 'lines_open': 0, 'lines_done': 0})
            if done:
                row['done'] += 1
                row['lines_done'] += int(a.lines or 0)
            else:
                row['open'] += 1
                row['lines_open'] += int(a.lines or 0)
                open_count += 1
        top_vo = sorted(by_vo.items(), key=lambda kv: (-kv[1]['open'], -kv[1]['lines_open'], kv[0].lower()))[:8]
        lines += [
            f"VO assignments: {len(assignments)} total",
            f"Open roles: {open_count}",
            f"Submitted roles: {len(assignments) - open_count}",
        ]
        if top_vo:
            lines.append('')
            lines.append('Top VO open load')
            for name, stat in top_vo:
                lines.append(f"• {name} — open {stat['open']} ({stat['lines_open']} lines), done {stat['done']}")
    lines.append('')
    lines.append('Usage: /workload, /workload translator, /workload vo')
    await update.effective_message.reply_text('\n'.join(lines), disable_web_page_preview=True)
async def cmd_who_has(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    query = _context_args_text(context)
    if not query:
        return await update.effective_message.reply_text("Usage: /who_has <MOVIE_CODE or title>")
    m, err = _require_movie_arg(query)
    if not m:
        return await update.effective_message.reply_text(err)
    await update.effective_message.reply_text(_who_has_text(m), disable_web_page_preview=True)
# -----------------------------
# Movie commands
# -----------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # NOTE: Use plain text (no Markdown) to avoid entity parse errors
    # when usernames/env values contain special chars (e.g., underscores).
    text = "\n".join(
        [
            f"✅ {BOT_NAME} online",
            f"Version: {APP_VERSION}",
            "",
            "What to do (quick):",
            "• Translator: DM the bot and send your .srt (recommended filename: MOVIECODE.srt)",
            "• VO: send audio/video/zip in the VO group with caption: MOVIECODE role lines (e.g. BN-260303-01 man-1 120)",
            "• Admin: use /help for admin commands + backups (/backup_here, /backup_now).",
            "• Admin: /create_project lets you create movie + auto-assign straight from Telegram using movie title.",
            "• Admin: /project_wizard gives a guided step-by-step create flow in DM.",
            "• Admin: /panel (or /menu) opens button dashboard in DM.",
            "• Admin: /workload shows translator + VO summary, /who_has shows who owns one movie.",
            "• Admin: /deadline_tr, /deadline_vo, /overdue, /remind_tr, /remind_vo manage deadlines/reminders.",
            "• Admin: /priority, /remind_overdue, /summary_today, /digest_now help you chase urgent jobs faster.",
            "• Admin: /suggest_translator and /suggest_vo now rank picks by workload + overdue risk + recent activity.",
            "• Button flow now supports Find Movie → action card → preview → confirm assign in DM.",
            "• New: key admin writes now also offer short-lived Undo after assign, reassign, clear, and deadline changes.",
            "• Admin: /activity or web Activity page shows latest movie actions across web + Telegram. Activity page now has filters + CSV export.",
            "• New: Bulk Ops page + /bulk_archive /bulk_unarchive help mass archive / recover movies faster.",
            "• New: Cleanup presets + /stale_movies /bulk_archive_stale help chase old inactive movies faster.",
            "• Admin: restore from JSON ZIP via web /restore (dry run first).",
            "• Web: /tips for a short workflow cheat-sheet.",
            "",
            "Tips:",
            "• No caption needed for translator DM SRT.",
            "• Late/Overdue only counts if a deadline exists (deadline empty = NOT late).",
            "• For reminders to work, make sure translator/VO has Telegram ID linked (DM the bot once or set @username in web).",
            "• Translator can use /my_tasks, VO can use /my_roles.",
        ]
    )
    if _is_dm(update):
        await update.effective_message.reply_text(
            text,
            disable_web_page_preview=True,
            reply_markup=_panel_keyboard_for_update(update),
        )
    else:
        await update.effective_message.reply_text(text, disable_web_page_preview=True)
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Role-scoped help (admin/owner sees all, translator sees translator-only, VO sees VO-only)."""
    is_admin = _is_admin(update)
    is_owner = _is_owner(update)
    chat = update.effective_chat
    # Role resolution
    if is_admin or is_owner:
        role = "admin"
    elif chat and chat.type == "private":
        role = "translator"
    else:
        role = "vo"
    lines = [f"{BOT_NAME} — /help", f"Version: {APP_VERSION}", ""]
    lines += ["General", "• /start", "• /help", "• /version", "• /me", ""]
    if role == "admin":
        if is_owner:
            lines += [
                "Owner-only",
                "• /admin_add <tg_id> [display_name]",
                "• /admin_remove <tg_id>",
                "",
            ]
        lines += [
            "Backups (Admin)",
            "• /backup_here        (run in target chat to save destination)",
            "• /backup_status      (shows ENV/DB destination)",
            "• /backup_now [mode] [dest]",
            "  mode: all | json | excel | logs",
            "  dest: add 'dest' to send to saved destination",
            "• Web: /backups  (download/send backups + recent status)",
            "• Web: /restore  (import JSON ZIP backup)",
            "  Restore supports: Replace (wipe) or Append (merge, ignore conflicts).",
            "• Web: /tips     (workflow cheat-sheet)",
            "",
            "Movies / Assignments (Admin)",
            "• Paste: Title (2025) - bn   (auto create movie + card)",
            "• /create_movie Title | 2025 | bn",
            "• /create_project Title | 2025 | bn | superurgent/urgent/nonurgent/flexible | man-1 120; fem-1 80",
            "• /project_wizard   (guided create flow with buttons)",
            "• /panel or /menu  (button dashboard in DM)",
            "• Button flow: Find Movie → movie card → preview → confirm",
            "• /project_cancel   (cancel guided create flow)",
            "• /find_movie <keyword>",
            "• /archived [limit or keyword]",
            "• /unarchive_movie <MOVIE_CODE or title>",
            "• /bulk_archive <keyword> [limit]",
            "• /bulk_unarchive <keyword> [limit]",
            "• /cleanup_presets",
            "• /pending_roles [limit or keyword]",
            "• Role import review buttons: 12h / 24h / 36h / 48h → preview first → confirm",
            "• /review_roles <REQUEST_ID>",
            "• /refresh_role_import <REQUEST_ID>",
            "• /aliases <MOVIE_CODE or title>",
            "• /resolve_movie <filename or title>",
            "• /group_context [chat_id]   (or run inside the group)",
            "• /clear_group_context [chat_id]",
            "• /add_alias <MOVIE_CODE or title> | <alias>",
            "• /delete_alias <ALIAS_ID>",
            "• /duplicates [keyword]",
            "• /merge_simulate <SOURCE> | <TARGET>  (dry run compare)",
            "• /merge_movie <SOURCE> | <TARGET> [| delete]  (conflict preview + confirm)",
            "• /stale_movies [days] [limit]",
            "• /bulk_archive_stale [days] [limit]",
            "• /movie_history <MOVIE_CODE or title>",
            "• /activity [limit] [web|tg] [EVENT] [keyword]",
            "• /movie <MOVIE_CODE or title>",
            "• /rename_movie <MOVIE_CODE or title> | <new title> | <year?> | <lang?>",
            "• /assign_translator <MOVIE_CODE or title> | <name/@user>  (preview + confirm + undo)",
            "• /suggest_translator <MOVIE_CODE or title>",
            "• /reassign_vo <MOVIE_CODE or title> | <role> | <VO name>  (preview + confirm + undo)",
            "• /suggest_vo <MOVIE_CODE or title> | <role?>",
            "• /movie_workload <MOVIE_CODE or title>",
            "• /deadlines <MOVIE_CODE or title>",
            "• /deadline_tr <MOVIE_CODE or title> | <YYYY-MM-DD HH:MM MYT>  (undo available)",
            "• /deadline_vo <MOVIE_CODE or title> | <role/open/all> | <YYYY-MM-DD HH:MM MYT>  (undo available)",
            "• /remind_tr <MOVIE_CODE or title>",
            "• /remind_vo <MOVIE_CODE or title> | <role/open/all>",
            "• /overdue [translator|vo|all]",
            "• /remind_overdue [translator|vo|all] [limit]",
            "• /priority [limit]   (movies sorted by overdue/open pressure)",
            "• /summary_today      (today activity + overdue + top pressure)",
            "• /digest_here        (save current chat for admin digest)",
            "• /digest_status      (show admin digest destination + status)",
            "• /digest_now [dest]  (send admin digest now)",
            "• /digest_on and /digest_off",
            "• /undo_last         (reverse your latest still-valid undo action)",
            "• /panel → Assign Translator / Reassign VO / Movie Workload / Overdue / Priority / Digest / Archived / Activity / Pending Roles",
            "• Movie card: admin-only controls. Public group gets clean VO card with Due in countdown.",
            "• Deadline modes: Super Urgent 12h, Urgent 24h, Non-Urgent 36h, Flexible 48h.",
            "• role*.txt helper files in a bound VO group can now be auto-opened, parsed, and auto-assigned.",
            "• Assign/reassign actions now open a preview with Confirm / Cancel before DB write.",
            "• After assign/reassign/clear/deadline change, bot also gives a short-lived Undo.",
            "• /undo_last can reverse your most recent still-valid undo action.",
            "• Smart picks rank by workload, overdue risk, language/gender fit, and recent activity.",
            "• /progress <MOVIE_CODE or title>",
            "• /who_has <MOVIE_CODE or title>",
            "• /workload [translator|vo|all]",
            "• /bulk_assign <MOVIE_CODE or title>  (run in VO group → paste roles → /done)",
            "• /clear_movie <MOVIE_CODE or title>  (clears active roles only)",
            "• Group/VO view is now cleaner: raw role text can be replaced by a public VO card after approval.",
            "• Admin chat now gets a private review card for auto-detected role imports.",
            "• Bulk Ops page can archive / clear / unarchive many movies at once",
            "• Cleanup page adds saved filters + presets for stale active / old archived movies",
            "• Duplicate movie groups now also support dry-run simulator before the real merge",
            "• Duplicate movie groups can now be merged from web and bot with preview + confirm",
            "• /vo_stats <MOVIE_CODE or title>     (totals + pending per VO)",
            "",
            "VO Group / Binding (Admin)",
            "• /request_group <MOVIE_CODE or title>   (ask approval to open VO group)",
            "• /bind <MOVIE_CODE or title>            (run inside VO group after approval)",
            "• /group_reject <REQUEST_ID> <note>",
            "",
            "Deadlines / Late",
            "• Late/Overdue only counts if a deadline exists.",
            "  (deadline empty = NOT late)",
            "• Bot quick set uses MYT format: YYYY-MM-DD HH:MM",
            "• Use 'clear' as deadline value to remove a deadline.",
            "• /remind_overdue all 10 = batch remind up to 10 overdue jobs.",
            "• /priority shows which movies need attention first.",
            "• /digest_now dest = push one admin digest to saved destination.",
            "• Web: click Late badge → overdue list → set deadline → Remind button",
            "",
        ]
        # Also show translator + vo sections for admin/owner.
        role_blocks = ["translator", "vo"]
    else:
        role_blocks = [role]
    if "translator" in role_blocks:
        lines += [
            "Translator (DM)",
            "• Send your translated .srt in DM (no caption needed).",
            "  Recommended filename: MOVIECODE.srt  (example: BN-260303-01.srt)",
            "  Also ok: Title (Year).srt",
            "• Bot will: create Queue row + forward SRT (if SRT_OUTBOX_CHAT_ID set)",
            "• /submit <MOVIE_CODE or Title>   (optional mode for text/other files)",
            "• /my_tasks   (see your translation jobs)",
            "• /cancel",
            "",
        ]
    if "vo" in role_blocks:
        lines += [
            "VO (Group)",
            "• Upload media/zip with caption containing movie code + roles.",
            "  Example: BN-260303-01 man-1 120",
            "  Example: BN-260303-01 fem-2 80",
            "• ZIP: filenames containing man1/man-1/fem2/fem-2 will be auto-detected.",
            "• role*.txt helper files are auto-opened and imported when the group is already bound.",
            "• /my_roles   (see your assigned roles)",
            "",
        ]
    if role == "admin":
        lines += [
            "Security",
            "• Never share BOT_TOKEN in screenshots/logs.",
            "• If token leaks, revoke it in BotFather and update Render ENV.",
            "",
            "Config (read-only)",
            f"• SRT_OUTBOX_CHAT_ID: {SRT_OUTBOX_CHAT_ID or '-'}",
            f"• Anonymous forward: {int(SRT_FORWARD_ANON)}",
        ]
    await _send_chunked_text(update.effective_message, "\n".join(lines), disable_web_page_preview=True)
async def cmd_create_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin quick create from Telegram using movie title first."""
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    raw = _context_args_text(context)
    if not raw:
        return await update.effective_message.reply_text(
            "Usage: /create_project <title or code> | <year?> | <lang?> | <superurgent/urgent/nonurgent/flexible?> | <role list>"
        )
    parts = [p.strip() for p in raw.split('|')]
    if len(parts) < 2:
        return await update.effective_message.reply_text(
            "Usage: /create_project <title or code> | <year?> | <lang?> | <superurgent/urgent/nonurgent/flexible?> | <role list>"
        )
    mode_idx = None
    for i, part in enumerate(parts):
        probe = (part or '').strip().lower().replace('_','').replace('-', '')
        if probe in {'superurgent', 'super', 'urgent', 'nonurgent', 'normal', 'flexible', 'relaxed', '36h', '48h'}:
            mode_idx = i
            break
    if mode_idx is not None:
        pre = parts[:mode_idx]
        mode_raw = _normalize_priority_mode(parts[mode_idx] or 'urgent')
        roles_blob = '|'.join(parts[mode_idx + 1:]).strip()
    else:
        pre = parts[:4]
        mode_raw = 'urgent'
        roles_blob = '|'.join(parts[4:]).strip() if len(parts) >= 5 else ''
    project_raw = pre[0].strip() if len(pre) >= 1 else ''
    year_raw = pre[1].strip() if len(pre) >= 2 else ''
    lang_raw = pre[2].strip() if len(pre) >= 3 else ''
    title_override = pre[3].strip() if len(pre) >= 4 else ''
    if not project_raw:
        return await update.effective_message.reply_text("❌ Missing movie title or code")
    roles_blob = _normalize_roles_blob(roles_blob)
    parsed = parse_lines(roles_blob)
    if not parsed:
        return await update.effective_message.reply_text(
            "❌ Nothing parsed. Example:\n/create_project Inside Out 2 | 2024 | bn | urgent | man-1 120; fem-1 80"
        )
    year_val = int(year_raw) if year_raw.isdigit() else None
    lang_val = _slug_lang(lang_raw or DEFAULT_LANG)
    title_input = (title_override or '').strip()
    exact_code = _extract_movie_code(project_raw)
    created = False
    if exact_code:
        movie = movie_by_code(exact_code)
        if not movie:
            base_title = title_input or exact_code
            movie = upsert_movie(base_title, year_val, lang_val)
            if movie.code != exact_code:
                movie.code = exact_code
            if title_input:
                movie.title = title_input
            if year_val:
                movie.year = str(year_val)
            if lang_val:
                movie.lang = lang_val
            movie.updated_at = _now_utc()
            db.session.commit()
            created = True
        else:
            if title_input:
                movie.title = title_input
            if year_val:
                movie.year = str(year_val)
            if lang_val:
                movie.lang = lang_val
            movie.updated_at = _now_utc()
            db.session.commit()
    else:
        movie, created = get_or_create_movie(project_raw, year_val, lang_val)
        if title_input and (movie.title or '').strip() != title_input:
            movie.title = title_input
            movie.updated_at = _now_utc()
            db.session.commit()
    urgent = _priority_mode_urgent_only(mode_raw)
    results = _auto_assign_movie_roles(movie, parsed, urgent=urgent, replace_existing=True, priority_mode=mode_raw)
    assigned = [r for r in results if r.get('vo')]
    missing = [r for r in results if not r.get('vo')]
    try:
        movie.status = 'VO_ASSIGNED' if assigned else (movie.status or 'RECEIVED')
        movie.updated_at = _now_utc()
        record_movie_event(movie, "CREATE_PROJECT", f"Bot created project with {len(assigned)} assigned role(s)", detail=f"mode={mode_raw} • urgent={urgent} • created_movie={'yes' if created else 'no'}", actor_source="tg", actor_name="create_project")
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
    out = [
        f"✅ Project ready: {fmt_title_year(movie.title, movie.year)} [{movie.code}]"
        f"Mode: {_priority_mode_label(mode_raw)} ({_priority_mode_hours(mode_raw)}h)"
        f"Created movie: {'yes' if created else 'no'}"
        f"Assignments created: {len(assigned)}/{len(results)}"
        '',
    ]
    for row in assigned[:20]:
        out.append(f"• {row['role']} → {row['vo']} ({row['lines']})")
    if missing:
        out.append('')
        out.append('Unassigned:')
        for row in missing[:10]:
            out.append(f"• {row['role']} ({row['lines']})")
    await update.effective_message.reply_text('\n'.join(out), disable_web_page_preview=True)
async def cmd_rename_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    raw = _context_args_text(context)
    if not raw or '|' not in raw:
        return await update.effective_message.reply_text(
            "Usage: /rename_movie <MOVIE_CODE or title> | <new title> | <year?> | <lang?>"
        )
    parts = [p.strip() for p in raw.split('|')]
    movie_query = parts[0] if len(parts) >= 1 else ''
    new_title = parts[1] if len(parts) >= 2 else ''
    year_raw = parts[2] if len(parts) >= 3 else ''
    lang_raw = parts[3] if len(parts) >= 4 else ''
    if not movie_query or not new_title:
        return await update.effective_message.reply_text(
            "Usage: /rename_movie <MOVIE_CODE or title> | <new title> | <year?> | <lang?>"
        )
    m, err = _require_movie_arg(movie_query)
    if not m:
        return await update.effective_message.reply_text(err)
    old = fmt_title_year(m.title, m.year)
    m.title = new_title
    if year_raw.strip():
        m.year = year_raw.strip()
    if lang_raw.strip():
        m.lang = _slug_lang(lang_raw)
    m.updated_at = _now_utc()
    db.session.commit()
    await update.effective_message.reply_text(
        f"✅ Movie updated\nOld: {old} [{m.code}]\nNew: {fmt_title_year(m.title, m.year)} [{(m.lang or '').upper() or '-'}]"
    )
    await _try_update_movie_card(context, m)

def _movie_matches_lines(matches: List[Movie], limit: int = 8) -> List[str]:
    lines: List[str] = []
    for m in matches[:limit]:
        lines.append(f"• {m.code} — {fmt_title_year(m.title, m.year)} — {(m.lang or '').upper() or 'BN'}{' • archived' if bool(getattr(m, 'is_archived', False)) else ''}")
    return lines


async def cmd_resolve_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update) and not _is_owner(update):
        return await update.effective_message.reply_text('Admin only.')
    query = ' '.join(context.args or []).strip()
    if not query:
        return await update.effective_message.reply_text('Usage: /resolve_movie <filename or title>')
    parsed_general = parse_movie_from_filename(query)
    parsed_helper = _parse_movie_from_role_helper_filename(query)
    clean_title = _clean_movie_title_candidate(query)
    movie, matches = _resolve_movie_query(query)
    lines = ['🧭 Resolve movie', f'Input: {query}', '']
    lines += [
        'Parser',
        f"• clean title: {clean_title or '-'}",
        f"• general: {((parsed_general or {}).get('title') or '-')} | {((parsed_general or {}).get('year') or '-')} | {((parsed_general or {}).get('lang') or '-').upper() if (parsed_general or {}).get('lang') else '-'}",
        f"• helper: {((parsed_helper or {}).get('title') or '-')} | {((parsed_helper or {}).get('year') or '-')} | {((parsed_helper or {}).get('lang') or '-').upper() if (parsed_helper or {}).get('lang') else '-'}",
        '',
    ]
    if movie:
        lines += ['Resolved', f"• {movie.code} — {fmt_title_year(movie.title, movie.year)} — {(movie.lang or '').upper() or 'BN'}", '']
    else:
        lines += ['Resolved', '• (none)', '']
    lines += ['Matches']
    lines += _movie_matches_lines(matches, limit=8) or ['• (none)']
    await update.effective_message.reply_text('\n'.join(lines), disable_web_page_preview=True)


async def cmd_group_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update) and not _is_owner(update):
        return await update.effective_message.reply_text('Admin only.')
    chat = update.effective_chat
    raw = ' '.join(context.args or []).strip()
    chat_id: Optional[int] = None
    if raw:
        try:
            chat_id = int(raw)
        except Exception:
            return await update.effective_message.reply_text('Usage: /group_context [chat_id]')
    elif chat and chat.type != 'private':
        chat_id = int(chat.id)
    else:
        return await update.effective_message.reply_text('Usage: /group_context <chat_id>  (or run inside the group)')
    bound = Movie.query.filter_by(vo_group_chat_id=int(chat_id)).first()
    ctx_row = _ctx_get(int(chat_id))
    now = datetime.utcnow()
    cached = _find_cached_candidate(context, int(chat_id), now=now)
    recent = _recent_group_file_candidates(context, int(chat_id), now=now, lookback_hours=24)
    lines = ['🧩 Group context', f'Chat: {chat_id}', '']
    if bound:
        lines += ['Bound movie', f"• {bound.code} — {fmt_title_year(bound.title, bound.year)} — {(bound.lang or '').upper() or 'BN'}", '']
    else:
        lines += ['Bound movie', '• (none)', '']
    if ctx_row:
        lines += [
            'DB context',
            f"• {fmt_title_year(ctx_row.title, ctx_row.year)} — {(ctx_row.lang or '').upper() or 'BN'}",
            f"• source: {ctx_row.source_file_name or '-'}",
            f"• expires: {ctx_row.expires_at or '-'}",
            '',
        ]
    else:
        lines += ['DB context', '• (none)', '']
    if cached:
        lines += [
            'Latest cached candidate',
            f"• {cached.get('title') or '-'} | {cached.get('year') or '-'} | {(cached.get('lang') or '-').upper() if cached.get('lang') else '-'}",
            f"• source: {cached.get('file_name') or '-'}",
            '',
        ]
    if recent:
        lines += ['Recent group files']
        for item in recent[:8]:
            lines.append(f"• {item.get('file_name') or '-'}")
    else:
        lines += ['Recent group files', '• (none)']
    await update.effective_message.reply_text('\n'.join(lines), disable_web_page_preview=True)


async def cmd_clear_group_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update) and not _is_owner(update):
        return await update.effective_message.reply_text('Admin only.')
    chat = update.effective_chat
    raw = ' '.join(context.args or []).strip()
    chat_id: Optional[int] = None
    if raw:
        try:
            chat_id = int(raw)
        except Exception:
            return await update.effective_message.reply_text('Usage: /clear_group_context [chat_id]')
    elif chat and chat.type != 'private':
        chat_id = int(chat.id)
    else:
        return await update.effective_message.reply_text('Usage: /clear_group_context <chat_id>  (or run inside the group)')
    removed = []
    ctx_row = GroupMovieContext.query.filter_by(tg_chat_id=int(chat_id)).first()
    if ctx_row:
        db.session.delete(ctx_row)
        removed.append('db context')
    store = context.bot_data.get('movie_candidates', {})
    if int(chat_id) in store:
        store.pop(int(chat_id), None)
        removed.append('cached candidates')
    store2 = context.bot_data.get('recent_group_files', {})
    if int(chat_id) in store2:
        store2.pop(int(chat_id), None)
        removed.append('recent files')
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
    await update.effective_message.reply_text(f"🧹 Cleared group context for {chat_id}: {', '.join(removed) if removed else 'nothing to clear'}")


async def cmd_aliases(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text('❌ Not allowed')
    query = _context_args_text(context)
    if not query:
        return await update.effective_message.reply_text('Usage: /aliases <MOVIE_CODE or title>')
    movie, err = _require_movie_arg(query)
    if err:
        return await update.effective_message.reply_text(err)
    rows = find_movie_aliases(movie, limit=50)
    lines = [f'🏷️ Movie aliases — {fmt_title_year(movie.title, movie.year)} [{movie.code}]', '']
    if not rows:
        lines.append('(none)')
    else:
        for row in rows:
            lines.append(f"• {row.alias} ({row.source or 'auto'})")
    return await update.effective_message.reply_text('\n'.join(lines))


async def cmd_add_alias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text('❌ Not allowed')
    raw = _context_args_text(context)
    if '|' not in raw:
        return await update.effective_message.reply_text('Usage: /add_alias <MOVIE_CODE or title> | <alias>')
    left, alias = [x.strip() for x in raw.split('|', 1)]
    movie, err = _require_movie_arg(left)
    if err:
        return await update.effective_message.reply_text(err)
    result = add_movie_alias_db(movie, alias, source='tg_manual')
    if result.get('changed'):
        return await update.effective_message.reply_text(f"✅ Alias added for {movie.code}: {result.get('alias').alias}")
    if result.get('reason') == 'conflict':
        other = result.get('movie')
        return await update.effective_message.reply_text(f"⚠️ Alias already belongs to {(other.code if other else 'another movie')}")
    if result.get('reason') == 'same_title':
        return await update.effective_message.reply_text('ℹ️ Alias is the same as the movie title.')
    return await update.effective_message.reply_text('ℹ️ No alias added.')


async def cmd_delete_alias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text('❌ Not allowed')
    raw = _context_args_text(context)
    if not raw.isdigit():
        return await update.effective_message.reply_text('Usage: /delete_alias <ALIAS_ID>')
    result = delete_movie_alias_db(int(raw))
    if result.get('changed'):
        mv = result.get('movie')
        suffix = f" for {mv.code}" if mv else ''
        return await update.effective_message.reply_text(f"✅ Alias deleted{suffix}: {result.get('alias')}")
    return await update.effective_message.reply_text('❌ Alias not found')


async def cmd_repair_titles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text('Admin only')
    query, limit = _parse_query_limit(getattr(context, 'args', []) or [], default_limit=8, max_limit=20)
    rows = find_repairable_movie_titles(query, limit=limit, include_archived=True)
    if not rows:
        msg = 'No repairable movie titles found.'
        if query:
            msg += f"\nQuery: {query}"
        return await update.effective_message.reply_text(msg)
    lines = ['🩹 Movie title repair candidates', '']
    for row in rows:
        movie = row['movie']
        conflict = row.get('conflict')
        arc = ' • archived' if bool(getattr(movie, 'is_archived', False)) else ''
        lines.append(f"• {movie.code} — {row['old_title']} → {row['new_title']}{arc}")
        if conflict:
            lines.append(f"  conflict: {conflict.code} • {fmt_title_year(conflict.title, conflict.year)}")
    lines.extend(['', 'Run: /repair_movie_title <MOVIE_CODE or title>'])
    await _send_chunked_text(update.effective_message, '\n'.join(lines), disable_web_page_preview=True)

async def cmd_repair_movie_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text('Admin only')
    raw = ' '.join(getattr(context, 'args', []) or []).strip()
    if not raw:
        return await update.effective_message.reply_text('Usage: /repair_movie_title <MOVIE_CODE or title>')
    movie, matches = _resolve_any_movie_query(raw)
    if not movie:
        return await update.effective_message.reply_text(_movie_lookup_help(raw, matches))
    result = repair_movie_title_db(movie, actor_source='tg', actor_name='cmd_repair_movie_title')
    if result.get('changed'):
        await _try_update_movie_card(context, movie)
        return await update.effective_message.reply_text(
            '\n'.join([
                '✅ Movie title repaired',
                f"Old: {result.get('old_title')}",
                f"New: {result.get('new_title')}",
                f"Code: {movie.code}",
            ]),
            disable_web_page_preview=True,
        )
    issue = result.get('issue') or _title_repair_issue(movie)
    if result.get('reason') == 'conflict' and issue:
        conflict = issue.get('conflict')
        return await update.effective_message.reply_text(
            '\n'.join([
                '⚠️ Repair blocked by existing clean title movie.',
                f"Current: {issue.get('old_title')} [{movie.code}]",
                f"Wanted: {issue.get('new_title')}",
                f"Conflict: {fmt_title_year(conflict.title, conflict.year)} [{conflict.code}]" if conflict else 'Conflict detected',
            ]),
            disable_web_page_preview=True,
        )
    return await update.effective_message.reply_text('No title repair needed for that movie.')

async def cmd_pending_roles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text('Admin only')
    query, limit = _parse_query_limit(getattr(context, 'args', []) or [], default_limit=8, max_limit=20)
    q = GroupRoleImportRequest.query.filter_by(status='PENDING').order_by(GroupRoleImportRequest.created_at.desc())
    if query:
        like = f"%{query}%"
        q = q.filter(
            GroupRoleImportRequest.title.ilike(like)
            | GroupRoleImportRequest.requested_by_name.ilike(like)
        )
    rows = q.limit(limit).all()
    if not rows:
        return await update.effective_message.reply_text('No pending auto-detected role approvals.')
    lines = ['🧪 Pending role approvals', '']
    buttons = []
    for req in rows:
        roles = _load_import_req_roles(req)
        lines.append(
            f"• #{req.id} — {fmt_title_year(req.title, req.year)} [{(req.lang or '').upper() or '-'}]"
            f" • roles={len(roles)} • by {req.requested_by_name or req.requested_by_tg_id or '-'}"
        )
        buttons.append([InlineKeyboardButton(f'🔍 Review #{req.id}', callback_data=f'imp|show|{req.id}')])
    await _send_chunked_text(update.effective_message, "\n".join(lines), disable_web_page_preview=True)
    await update.effective_message.reply_text('Tap a request below to open the admin review card.', reply_markup=InlineKeyboardMarkup(buttons[:12]))
async def cmd_review_roles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text('Admin only')
    raw = ' '.join(getattr(context, 'args', []) or []).strip()
    if not raw or not raw.isdigit():
        return await update.effective_message.reply_text('Usage: /review_roles <REQUEST_ID>')
    req = GroupRoleImportRequest.query.filter_by(id=int(raw)).first()
    if not req:
        return await update.effective_message.reply_text('Request not found.')
    await _send_chunked_text(update.effective_message, _admin_import_review_text(req), disable_web_page_preview=True)
    await update.effective_message.reply_text('Actions', reply_markup=_import_review_keyboard(req.id))
async def cmd_refresh_role_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text('Admin only')
    raw = ' '.join(getattr(context, 'args', []) or []).strip()
    if not raw or not raw.isdigit():
        return await update.effective_message.reply_text('Usage: /refresh_role_import <REQUEST_ID>')
    req = GroupRoleImportRequest.query.filter_by(id=int(raw)).first()
    if not req:
        return await update.effective_message.reply_text('Request not found.')
    if (req.status or 'PENDING').upper() != 'PENDING':
        return await update.effective_message.reply_text(f'Request already {req.status}. Refresh only works for PENDING requests.')
    if req.expires_at and req.expires_at < _now_utc():
        req.status = 'EXPIRED'
        db.session.commit()
        return await update.effective_message.reply_text('⏳ Request expired')
    try:
        suggestions = _refresh_role_import_request(req, commit=True)
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        return await update.effective_message.reply_text(f'❌ Refresh failed: {e}')
    await update.effective_message.reply_text(
        f'🔄 Refreshed role import #{req.id} • roles={len(_load_import_req_roles(req))} • suggestions={len(suggestions)}',
        disable_web_page_preview=True,
    )
    await _send_chunked_text(update.effective_message, _admin_import_review_text(req), disable_web_page_preview=True)
    await update.effective_message.reply_text('Actions', reply_markup=_import_review_keyboard(req.id))
async def cmd_my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Translator self-service: show own active/completed translation tasks."""
    _upsert_translator_seen(update)
    tr = _find_translator_for_user(update)
    if not tr:
        return await update.effective_message.reply_text(
            "❌ Translator profile not linked yet. DM the bot once with any message or ask admin to set your Telegram username in translator roster."
        )
    rows = (
        TranslationTask.query
        .filter((TranslationTask.translator_id == tr.id) | (TranslationTask.translator_name.ilike(tr.name)))
        .order_by(TranslationTask.status.asc(), TranslationTask.sent_at.desc().nullslast(), TranslationTask.id.desc())
        .limit(20)
        .all()
    )
    if not rows:
        return await update.effective_message.reply_text(f"No translation tasks for {tr.name}.")
    pending = [r for r in rows if (r.status or '').upper() != 'COMPLETED']
    done = [r for r in rows if (r.status or '').upper() == 'COMPLETED']
    out = [f"📝 My translation tasks — {tr.name}", '']
    if pending:
        out.append('Active')
        for r in pending[:10]:
            out.append(f"• {fmt_title_year(r.title, r.year) or r.movie_code} [{r.movie_code or '-'}] — {r.status}")
        out.append('')
    if done:
        out.append('Completed')
        for r in done[:5]:
            out.append(f"• {fmt_title_year(r.title, r.year) or r.movie_code} [{r.movie_code or '-'}]")
    await update.effective_message.reply_text('\n'.join(out), disable_web_page_preview=True)
async def cmd_my_roles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """VO self-service: show current role assignments and submitted roles."""
    _upsert_vo_seen(update)
    vo = _find_vo_for_user(update)
    if not vo:
        return await update.effective_message.reply_text(
            "❌ VO profile not linked yet. Send media once in your VO group or ask admin to set your Telegram username in VO roster."
        )
    assigns = (
        Assignment.query.filter(Assignment.vo.ilike(vo.name))
        .order_by(Assignment.created_at.desc(), Assignment.id.desc())
        .limit(20)
        .all()
    )
    if not assigns:
        return await update.effective_message.reply_text(f"No assignments for {vo.name}.")
    submitted = {
        norm_role(r.role)
        for r in VORoleSubmission.query.filter(VORoleSubmission.vo.ilike(vo.name)).all()
        if norm_role(r.role)
    }
    out = [f"🎙️ My VO roles — {vo.name}", '']
    current_movie = None
    shown = 0
    for a in assigns:
        movie = None
        if a.movie_id:
            movie = db.session.get(Movie, a.movie_id)
        if not movie and a.project:
            movie = Movie.query.filter_by(code=a.project).first()
        header = fmt_title_year(movie.title, movie.year) if movie else a.project
        code = movie.code if movie and movie.code else a.project
        if header != current_movie:
            if current_movie is not None:
                out.append('')
            current_movie = header
            out.append(f"{header} [{code}]")
        done = '✅' if norm_role(a.role) in submitted else '⏳'
        out.append(f"{done} {a.role} — {int(a.lines or 0)}")
        shown += 1
        if shown >= 20:
            break
    await update.effective_message.reply_text('\n'.join(out), disable_web_page_preview=True)
async def cmd_archived(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("Admin only")
    raw = " ".join(getattr(context, "args", []) or []).strip()
    limit = 10
    query = raw
    if raw.isdigit():
        limit = max(1, min(50, int(raw)))
        query = ""
    matches = _search_archived_movies(query, limit=limit)
    if not matches:
        return await update.effective_message.reply_text("No archived movies found.")
    lines = ["🗃️ Archived movies", ""]
    for m in matches[:limit]:
        archived_at = fmt_myt(m.archived_at) if getattr(m, "archived_at", None) else "-"
        lines.append(f"• {fmt_title_year(m.title, m.year)} [{(m.lang or '').upper() or '-'}] — {m.code}")
        lines.append(f"  Status: {m.status or 'ARCHIVED'} | Archived: {archived_at}")
    lines.append("")
    lines.append("Use /unarchive_movie <MOVIE_CODE or title> to bring one back.")
    await update.effective_message.reply_text("\n".join(lines), disable_web_page_preview=True)
async def cmd_unarchive_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("Admin only")
    query = " ".join(getattr(context, "args", []) or []).strip()
    if not query:
        return await update.effective_message.reply_text("Usage: /unarchive_movie <MOVIE_CODE or title>")
    movie, matches = _resolve_archived_movie_query(query)
    if not movie:
        return await update.effective_message.reply_text(_archived_lookup_help(query, matches))
    movie.is_archived = False
    movie.archived_at = None
    if (movie.status or "").upper() == "ARCHIVED":
        movie.status = "RECEIVED"
    movie.updated_at = _now_utc()
    record_movie_event(movie, "UNARCHIVE", "Bot unarchived movie", detail="Visible in Telegram search again", actor_source="tg", actor_name="unarchive_movie")
    db.session.commit()
    await update.effective_message.reply_text(
        f"♻️ Unarchived {fmt_title_year(movie.title, movie.year)} [{movie.code}]\nIt will appear again in Telegram search.",
        disable_web_page_preview=True,
    )
def _parse_days_limit(args: list, default_days: int = 14, default_limit: int = 8, max_limit: int = 20) -> tuple[int, int]:
    days = default_days
    limit = default_limit
    vals = [str(x).strip() for x in (args or []) if str(x).strip()]
    if vals and vals[0].isdigit():
        days = int(vals[0])
    if len(vals) > 1 and vals[1].isdigit():
        limit = int(vals[1])
    days = max(1, min(days, 365))
    limit = max(1, min(limit, max_limit))
    return days, limit
def _stale_movie_candidates(days: int = 14, limit: int = 8) -> list[dict[str, Any]]:
    cutoff = _now_utc() - timedelta(days=days)
    movies = (
        _active_movie_query()
        .filter(func.coalesce(Movie.updated_at, Movie.created_at) <= cutoff)
        .order_by(func.coalesce(Movie.updated_at, Movie.created_at).asc(), Movie.id.asc())
        .limit(250)
        .all()
    )
    codes = [str((m.code or '')).strip() for m in movies if (m.code or '').strip()]
    mids = [m.id for m in movies if getattr(m, 'id', None)]
    role_counts = {}
    if codes or mids:
        for project, cnt in (
            db.session.query(Assignment.project, func.count(Assignment.id))
            .filter((Assignment.project.in_(codes or ['__none__'])) | (Assignment.movie_id.in_(mids or [-1])))
            .group_by(Assignment.project)
            .all()
        ):
            role_counts[str(project or '').strip()] = int(cnt or 0)
    task_map = {}
    if codes or mids:
        tasks = (
            TranslationTask.query
            .filter((TranslationTask.movie_code.in_(codes or ['__none__'])) | (TranslationTask.movie_id.in_(mids or [-1])))
            .order_by(TranslationTask.updated_at.desc().nullslast(), TranslationTask.id.desc())
            .all()
        )
        for t in tasks:
            key = str((t.movie_code or '')).strip()
            if not key and getattr(t, 'movie_id', None):
                for m in movies:
                    if m.id == t.movie_id:
                        key = str((m.code or '')).strip()
                        break
            if key and key not in task_map:
                task_map[key] = t
    rows = []
    for m in movies:
        code = str((m.code or '')).strip()
        roles = int(role_counts.get(code, 0))
        task = task_map.get(code)
        has_tr = bool((m.translator_assigned or '').strip()) or bool(task)
        if roles != 0 or has_tr:
            continue
        updated_dt = m.updated_at or m.created_at
        age_days = max(0, (_now_utc() - updated_dt).days) if updated_dt else days
        rows.append({
            'movie': m,
            'code': code,
            'title': fmt_title_year(m.title, m.year),
            'age_days': age_days,
            'lang': (m.lang or '').upper() or '-',
        })
    return rows[:limit]
async def cmd_cleanup_presets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text('Admin only')
    lines = [
        '🧹 Cleanup presets',
        '',
        'Quick cleanup shortcuts now available in web Bulk Ops / Cleanup pages:',
        '• inactive14        — active movies with no translator and no roles, untouched 14+ days',
        '• inactive30        — stricter 30-day cleanup view',
        '• no_tr_no_roles    — active movies with zero translator + zero roles',
        '• translator_only14 — translator/task present but no VO roles for 14+ days',
        '• archived14        — archived movies older than 14 days',
        '• archived30        — archived movies older than 30 days',
        '',
        'Bot helpers:',
        '• /stale_movies [days] [limit]',
        '• /bulk_archive_stale [days] [limit]',
        '• Web: /cleanup_presets and /bulk_movies',
    ]
    await update.effective_message.reply_text('\n'.join(lines), disable_web_page_preview=True)
async def cmd_stale_movies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text('Admin only')
    days, limit = _parse_days_limit(getattr(context, 'args', []) or [], default_days=14, default_limit=8, max_limit=20)
    rows = _stale_movie_candidates(days=days, limit=limit)
    lines = [f'🧹 Stale active movies — {days}d+', '']
    if rows:
        for row in rows:
            lines.append(f"• {row['title']} [{row['lang']}] — {row['code']} • {row['age_days']}d idle")
    else:
        lines.append('No stale inactive movies matched right now.')
    lines.extend(['', f'Usage: /stale_movies {days} {limit}', f'Bulk action: /bulk_archive_stale {days} {limit}'])
    await update.effective_message.reply_text('\n'.join(lines), disable_web_page_preview=True)
async def cmd_duplicates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    query = " ".join(context.args or []).strip()
    groups = duplicate_groups(q=query, limit=8, include_archived=True)
    if not groups:
        return await update.effective_message.reply_text("No duplicate movie groups found.")
    lines = ["🧬 Duplicate movie groups", ""]
    for idx, g in enumerate(groups, 1):
        target = g.get('target')
        lines.append(f"{idx}. {fmt_title_year(g.get('title'), g.get('year'))} [{(g.get('lang') or '').upper() or '-'}]")
        lines.append(f"   Keep target: {(target.code if target else '-')} • total {g.get('count', 0)}")
        for m in (g.get('items') or [])[:6]:
            flag = 'KEEP' if (target and m.id == target.id) else 'dup'
            arc = ' • archived' if bool(getattr(m, 'is_archived', False)) else ''
            lines.append(f"   - {flag}: {m.code} • {m.status or '-'}{arc}")
        lines.append("")
    lines.append("Merge with: /merge_movie <SOURCE_CODE or title> | <TARGET_CODE or title>")
    await update.effective_message.reply_text("\n".join(lines), disable_web_page_preview=True)
async def cmd_merge_simulate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    payload = (getattr(update.effective_message, 'text', '') or '').split(' ', 1)
    raw = payload[1].strip() if len(payload) > 1 else ''
    if not raw or '|' not in raw:
        return await update.effective_message.reply_text("Usage: /merge_simulate <SOURCE_CODE or title> | <TARGET_CODE or title>")
    parts = [p.strip() for p in raw.split('|')]
    if len(parts) < 2:
        return await update.effective_message.reply_text("Usage: /merge_simulate <SOURCE_CODE or title> | <TARGET_CODE or title>")
    src_q, tgt_q = parts[0], parts[1]
    source, src_matches = _resolve_any_movie_query(src_q)
    if not source:
        return await update.effective_message.reply_text(_movie_lookup_help(src_q, src_matches))
    target, tgt_matches = _resolve_any_movie_query(tgt_q)
    if not target:
        return await update.effective_message.reply_text(_movie_lookup_help(tgt_q, tgt_matches))
    if source.id == target.id:
        return await update.effective_message.reply_text("❌ Source and target cannot be the same movie.")
    await update.effective_message.reply_text(_merge_simulation_text(source, target), disable_web_page_preview=True)
async def cmd_merge_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    payload = (getattr(update.effective_message, 'text', '') or '').split(' ', 1)
    raw = payload[1].strip() if len(payload) > 1 else ''
    if not raw or '|' not in raw:
        return await update.effective_message.reply_text("Usage: /merge_movie <SOURCE_CODE or title> | <TARGET_CODE or title> [| delete]")
    parts = [p.strip() for p in raw.split('|')]
    if len(parts) < 2:
        return await update.effective_message.reply_text("Usage: /merge_movie <SOURCE_CODE or title> | <TARGET_CODE or title> [| delete]")
    src_q, tgt_q = parts[0], parts[1]
    mode = (parts[2].strip().lower() if len(parts) > 2 else '')
    source, src_matches = _resolve_any_movie_query(src_q)
    if not source:
        return await update.effective_message.reply_text(_movie_lookup_help(src_q, src_matches))
    target, tgt_matches = _resolve_any_movie_query(tgt_q)
    if not target:
        return await update.effective_message.reply_text(_movie_lookup_help(tgt_q, tgt_matches))
    if source.id == target.id:
        return await update.effective_message.reply_text("❌ Source and target cannot be the same movie.")
    await _send_merge_movie_preview(update.effective_message, update.effective_user.id, source, target, delete_source=(mode == 'delete'))
async def cmd_bulk_archive_stale(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text('Admin only')
    days, limit = _parse_days_limit(getattr(context, 'args', []) or [], default_days=14, default_limit=8, max_limit=20)
    rows = _stale_movie_candidates(days=days, limit=limit)
    matches = [row['movie'] for row in rows if row.get('movie') and row.get('code')]
    if not matches:
        return await update.effective_message.reply_text('No stale inactive movies matched that cleanup preset.')
    token = _create_bulk_movie_action('archive', update.effective_user.id, [m.code for m in matches if m.code])
    header = [f'🧹 Bulk archive stale movies — {days}d+', '', 'These active movies look stale (no translator + no active roles):', '']
    for row in rows:
        header.append(f"• {row['title']} [{row['lang']}] — {row['code']} • {row['age_days']}d idle")
    header.extend(['', 'Archive these movies and hide them from Telegram search?'])
    await update.effective_message.reply_text('\n'.join(header), disable_web_page_preview=True, reply_markup=_bulk_movie_confirm_keyboard(token))
async def cmd_bulk_archive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("Admin only")
    query, limit = _parse_query_limit(getattr(context, "args", []) or [], default_limit=6, max_limit=20)
    if not query:
        return await update.effective_message.reply_text("Usage: /bulk_archive <keyword> [limit]")
    matches = _search_movies(query, limit=limit)
    if not matches:
        return await update.effective_message.reply_text("No active movies matched that keyword.")
    token = _create_bulk_movie_action("archive", update.effective_user.id, [m.code for m in matches if m.code])
    await update.effective_message.reply_text(
        _bulk_movie_preview_text("archive", matches),
        disable_web_page_preview=True,
        reply_markup=_bulk_movie_confirm_keyboard(token),
    )
async def cmd_bulk_unarchive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("Admin only")
    query, limit = _parse_query_limit(getattr(context, "args", []) or [], default_limit=6, max_limit=20)
    if not query:
        return await update.effective_message.reply_text("Usage: /bulk_unarchive <keyword> [limit]")
    matches = _search_archived_movies(query, limit=limit)
    if not matches:
        return await update.effective_message.reply_text("No archived movies matched that keyword.")
    token = _create_bulk_movie_action("unarchive", update.effective_user.id, [m.code for m in matches if m.code])
    await update.effective_message.reply_text(
        _bulk_movie_preview_text("unarchive", matches),
        disable_web_page_preview=True,
        reply_markup=_bulk_movie_confirm_keyboard(token),
    )
async def cmd_find_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    query = _context_args_text(context)
    if not query:
        return await update.effective_message.reply_text("Usage: /find_movie <title keyword>")
    matches = _search_movies(query, limit=8)
    if not matches:
        return await update.effective_message.reply_text(f"❌ Movie not found: {query}")
    lines = [f"🎬 Movie search — {query}", "Tap a movie below to open its action card.", ""]
    for m in matches:
        lines.append(f"• {fmt_title_year(m.title, m.year)} [{(m.lang or '').upper() or '-'}] — {m.code}")
    await update.effective_message.reply_text(
        "\n".join(lines),
        disable_web_page_preview=True,
        reply_markup=_movie_search_keyboard(matches),
    )
async def cmd_version(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"{BOT_NAME}\nVersion: {APP_VERSION}\nUTC: {_now_utc().isoformat()}",
        disable_web_page_preview=True,
    )
# --------------------------------------------------
# BACKUP COMMANDS (Admin only)
# --------------------------------------------------
def _kv_get(key: str) -> str:
    try:
        row = AppKV.query.filter_by(key=key).first()
        return (row.value or "").strip() if row else ""
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return ""
def _kv_set(key: str, value: str) -> bool:
    try:
        row = AppKV.query.filter_by(key=key).first()
        if not row:
            row = AppKV(key=key, value=value)
        else:
            row.value = value
        db.session.add(row)
        db.session.commit()
        return True
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return False
def _backup_dest_chat_id() -> str:
    env = (os.getenv("BACKUP_TELEGRAM_CHAT_ID") or "").strip()
    return env or _kv_get("backup_chat_id")
def _digest_dest_chat_id() -> str:
    env = (os.getenv("ADMIN_DIGEST_CHAT_ID") or "").strip()
    if env:
        return env
    dbv = _kv_get("digest_chat_id")
    if dbv:
        return dbv
    if ADMIN_TELEGRAM_CHAT_ID:
        return str(ADMIN_TELEGRAM_CHAT_ID).strip()
    return _backup_dest_chat_id()
def _digest_enabled() -> bool:
    dbv = (_kv_get("admin_digest_enabled") or "").strip().lower()
    if dbv in {"1", "true", "yes", "on"}:
        return True
    if dbv in {"0", "false", "no", "off"}:
        return False
    env = (os.getenv("ADMIN_DIGEST_ENABLED") or "1").strip().lower()
    return env not in {"0", "false", "no", "off", ""}
def _build_summary_today_text(limit: int = 5) -> str:
    now = _now_utc()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    new_movies = Movie.query.filter(Movie.created_at >= start).count()
    tr_done_today = TranslationSubmission.query.filter(TranslationSubmission.submitted_at >= start).count()
    vo_done_today = VORoleSubmission.query.filter(VORoleSubmission.submitted_at >= start).count()
    overdue_tr = _overdue_translation_tasks(now)
    overdue_vo = _overdue_vo_assignments(now)
    priority = _priority_movie_rows(max(1, min(limit, 10)))
    lines = [
        "🗓️ Daily admin summary",
        f"Time: {fmt_myt(now)}",
        "",
        "Today",
        f"• New movies: {new_movies}",
        f"• Translator submissions: {tr_done_today}",
        f"• VO role submissions: {vo_done_today}",
        "",
        "Outstanding now",
        f"• Translator overdue: {len(overdue_tr)}",
        f"• VO overdue: {len(overdue_vo)}",
    ]
    if priority:
        lines.extend(["", "Top pressure movies"])
        for score, movie, meta in priority:
            lines.append(
                f"• {fmt_title_year(movie.title, movie.year)} [{movie.code}] — score {score}, "
                f"open roles {meta['open_roles']}, VO overdue {meta['vo_overdue']}, "
                f"TR overdue {'yes' if meta['tr_overdue'] else 'no'}"
            )
    lines.extend(["", "Quick actions:", "• /priority", "• /overdue", "• /remind_overdue all 10"])
    return "\n".join(lines)
def build_admin_digest_text(priority_limit: int = 5) -> str:
    now = _now_utc()
    lines = [
        f"📬 Admin digest • v{APP_VERSION}",
        f"Time: {fmt_myt(now)}",
        "",
        _build_summary_today_text(limit=priority_limit),
        "",
        "Need more detail?",
        "• /priority 10",
        "• /overdue",
        "• /remind_overdue all 10",
    ]
    return "\n".join(lines)
async def _send_admin_digest_via_context(context: ContextTypes.DEFAULT_TYPE, chat_id: str | int, priority_limit: int = 5) -> tuple[bool, str]:
    try:
        text = build_admin_digest_text(priority_limit=priority_limit)
        await context.bot.send_message(chat_id=int(chat_id), text=text, disable_web_page_preview=True)
        return True, f"✅ Admin digest sent to {chat_id}."
    except Exception as e:
        return False, f"❌ Failed sending admin digest: {e}"
async def cmd_backup_here(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set backup destination to current chat."""
    if not is_owner_or_admin(update):
        await update.effective_message.reply_text("Admin only.")
        return
    chat_id = str(update.effective_chat.id)
    ok = _kv_set("backup_chat_id", chat_id)
    log_event("INFO" if ok else "ERROR", "tg.backup_here", f"Set backup_chat_id={chat_id} ok={ok}")
    if ok:
        await update.effective_message.reply_text(
            f"✅ Backup destination saved.\nChat ID: {chat_id}\n\nNow you can run: /backup_now dest"
        )
    else:
        await update.effective_message.reply_text("❌ Failed to save destination. Check web Logs.")
async def cmd_backup_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current backup destination (ENV/DB)."""
    if not is_owner_or_admin(update):
        await update.effective_message.reply_text("Admin only.")
        return
    env = (os.getenv("BACKUP_TELEGRAM_CHAT_ID") or "").strip()
    dbv = _kv_get("backup_chat_id")
    eff = env or dbv
    where = "ENV" if env else ("DB" if dbv else "Not set")
    await update.effective_message.reply_text(
        f"📦 Backup destination status\n"
        f"- Effective: {eff or '-'} ({where})\n"
        f"- ENV BACKUP_TELEGRAM_CHAT_ID: {'set' if env else 'not set'}\n"
        f"- DB backup_chat_id: {dbv or '-'}\n\n"
        f"Set via: /backup_here (in target chat) or Render ENV."
    )
async def cmd_backup_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send backups now. Usage: /backup_now [all|json|excel|logs] [dest]"""
    if not is_owner_or_admin(update):
        await update.effective_message.reply_text("Admin only.")
        return
    args = [a.lower() for a in (context.args or [])]
    mode = "all"
    if args:
        if args[0] in ("json", "jsonzip", "zip"):
            mode = "json"
        elif args[0] in ("excel", "xlsx"):
            mode = "excel"
        elif args[0] in ("logs", "log"):
            mode = "logs"
        elif args[0] in ("all",):
            mode = "all"
    send_to_dest = ("dest" in args) or ("destination" in args)
    chat_id = _backup_dest_chat_id() if send_to_dest else str(update.effective_chat.id)
    if not chat_id:
        await update.effective_message.reply_text("❌ No destination set. Run /backup_here first.")
        return
    await update.effective_message.reply_text(f"⏳ Generating backup ({mode})...")
    sent = []
    errors = []
    report = {"mode": mode, "chat_id": chat_id, "sent": [], "errors": []}
    caption_header = f"VO Tracker Backup • {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    try:
        if mode in ("all", "excel"):
            xlsx_bytes, rep = export_excel_dynamic(db.engine)
            bio = BytesIO(xlsx_bytes)
            fname = "vo_tracker_export_%sZ.xlsx" % datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            bio.name = fname
            msg = await context.bot.send_document(chat_id=chat_id, document=bio, caption=caption_header)
            sent.append({"kind": "excel", "file": fname, "message_id": getattr(msg, "message_id", None)})
            report["excel"] = rep
        if mode in ("all", "json"):
            zbytes, rep = backup_json_zip_dynamic(db.engine, app_version="tg")
            bio = BytesIO(zbytes)
            fname = "vo_tracker_backup_%sZ.zip" % datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            bio.name = fname
            msg = await context.bot.send_document(chat_id=chat_id, document=bio, caption=caption_header)
            sent.append({"kind": "json_zip", "file": fname, "message_id": getattr(msg, "message_id", None)})
            report["json_zip"] = rep
        if mode in ("all", "logs"):
            try:
                limit = int(os.getenv("EXPORT_MAX_LOGS", "5000"))
            except Exception:
                limit = 5000
            limit = max(1, min(limit, 50000))
            items = fetch_logs(limit=limit)
            out_lines = []
            for it in items:
                out_lines.append(f"[{it.get('ts','')}] {it.get('level','INFO')} {it.get('source','')}: {it.get('message','')}")
                tb = (it.get("traceback") or "").strip()
                if tb:
                    out_lines.append(tb)
                    out_lines.append("")
            logs_txt = ("\n".join(out_lines) + "\n").encode("utf-8", errors="ignore")
            bio = BytesIO(logs_txt)
            fname = "logs_%sZ.txt" % datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            bio.name = fname
            msg = await context.bot.send_document(chat_id=chat_id, document=bio, caption=caption_header)
            sent.append({"kind": "logs_txt", "file": fname, "message_id": getattr(msg, "message_id", None)})
    except Exception as e:
        errors.append(str(e))
        try:
            db.session.rollback()
        except Exception:
            pass
    report["sent"] = sent
    report["errors"] = errors
    log_event(
        "INFO" if not errors else "WARN",
        "tg.backup_now",
        f"/backup_now mode={mode} chat={chat_id} sent={len(sent)} errors={len(errors)}",
        traceback=json.dumps(report, ensure_ascii=False, indent=2),
    )
    if errors:
        await update.effective_message.reply_text(f"⚠️ Backup finished with errors: {errors[-1]}")
    else:
        await update.effective_message.reply_text(f"✅ Backup sent: {len(sent)} file(s).")
async def cmd_create_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    raw = _context_args_text(context)
    if not raw:
        return await update.effective_message.reply_text("Usage: /create_movie Title | 2025 | bn")
    parts = [p.strip() for p in raw.split("|")]
    title = parts[0] if len(parts) >= 1 else ""
    year = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else None
    lang = parts[2] if len(parts) >= 3 and parts[2] else "bn"
    if not title:
        return await update.effective_message.reply_text("❌ Missing title")
    m, created = get_or_create_movie(title, year, lang)
    if created:
        await update.effective_message.reply_text(f"✅ Created: {m.code} — {fmt_title_year(m.title, m.year)} [{m.lang}]")
    else:
        await update.effective_message.reply_text(f"ℹ️ Already exists: {m.code} — {fmt_title_year(m.title, m.year)} [{m.lang}]")
    await send_movie_card(update, context, m)
async def cmd_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    query = _context_args_text(context)
    if not query:
        return await update.effective_message.reply_text("Usage: /movie <MOVIE_CODE or title>")
    m, err = _require_movie_arg(query)
    if not m:
        return await update.effective_message.reply_text(err)
    await send_movie_card(update, context, m)
async def cmd_movie_workload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    query = _context_args_text(context)
    if not query:
        return await update.effective_message.reply_text("Usage: /movie_workload <MOVIE_CODE or title>")
    m, err = _require_movie_arg(query)
    if not m:
        return await update.effective_message.reply_text(err)
    await update.effective_message.reply_text(_movie_workload_text(m), disable_web_page_preview=True)
async def cmd_deadlines(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    query = _context_args_text(context)
    if not query:
        return await update.effective_message.reply_text("Usage: /deadlines <MOVIE_CODE or title>")
    m, err = _require_movie_arg(query)
    if not m:
        return await update.effective_message.reply_text(err)
    await update.effective_message.reply_text(_movie_deadline_text(m), disable_web_page_preview=True)
async def cmd_deadline_tr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    raw = (getattr(update.effective_message, 'text', '') or '').split(' ', 1)
    payload = raw[1].strip() if len(raw) > 1 else ''
    if '|' not in payload:
        return await update.effective_message.reply_text("Usage: /deadline_tr <MOVIE_CODE or title> | <YYYY-MM-DD HH:MM MYT or clear>")
    movie_q, dt_text = [x.strip() for x in payload.split('|', 1)]
    m, err = _require_movie_arg(movie_q)
    if not m:
        return await update.effective_message.reply_text(err)
    clear = dt_text.lower() in {'clear', 'none', 'null', '-'}
    dt_utc = None if clear else parse_myt_datetime_local(dt_text)
    if not clear and not dt_utc:
        return await update.effective_message.reply_text("❌ Invalid date. Use MYT like: 2026-03-10 22:00")
    prev_task = _translation_task_for_movie(m)
    undo_token = _new_undo_action("dtr", update.effective_user.id, m, {"task_snapshot": _translation_task_snapshot(prev_task)})
    task = _set_translation_deadline(m, dt_utc)
    who = task.translator_name or m.translator_assigned or '-'
    reply = f"✅ Translation deadline {'cleared' if clear else 'set'} for {fmt_title_year(m.title, m.year)} [{m.code}]\nTranslator: {who}\nDeadline: {fmt_myt(task.deadline_at)}"
    await update.effective_message.reply_text(reply, disable_web_page_preview=True)
    await _send_undo_message(update.effective_message, m, undo_token, "dtr")
    try:
        await _try_update_movie_card(context, m)
    except Exception:
        pass
async def cmd_deadline_vo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    raw = (getattr(update.effective_message, 'text', '') or '').split(' ', 1)
    payload = raw[1].strip() if len(raw) > 1 else ''
    parts = [x.strip() for x in payload.split('|')]
    if len(parts) != 3:
        return await update.effective_message.reply_text("Usage: /deadline_vo <MOVIE_CODE or title> | <role/open/all> | <YYYY-MM-DD HH:MM MYT or clear>")
    movie_q, role_token, dt_text = parts
    m, err = _require_movie_arg(movie_q)
    if not m:
        return await update.effective_message.reply_text(err)
    clear = dt_text.lower() in {'clear', 'none', 'null', '-'}
    dt_utc = None if clear else parse_myt_datetime_local(dt_text)
    if not clear and not dt_utc:
        return await update.effective_message.reply_text("❌ Invalid date. Use MYT like: 2026-03-10 22:00")
    target = (role_token or 'open').strip().lower()
    open_assigns, _submitted_roles = _open_assignments_for_movie(m)
    all_assigns = Assignment.query.filter((Assignment.movie_id == m.id) | (Assignment.project == m.code)).order_by(Assignment.role.asc()).all()
    if target in {'open', 'pending'}:
        picks = open_assigns
    elif target in {'all', '*'}:
        picks = all_assigns
    else:
        norm_target = norm_role(target) or target
        picks = [a for a in all_assigns if (norm_role(a.role) or a.role) == norm_target]
    if not picks:
        return await update.effective_message.reply_text(f"❌ No VO assignments matched: {role_token}")
    undo_token = _new_undo_action("dvo", update.effective_user.id, m, {"assignments": [{"id": a.id, "deadline_at": _dt_iso(a.deadline_at)} for a in picks]})
    count, roles = _set_vo_deadline(m, role_token, dt_utc)
    if count <= 0:
        return await update.effective_message.reply_text(f"❌ No VO assignments matched: {role_token}")
    preview = ', '.join(roles[:8])
    if len(roles) > 8:
        preview += ', ...'
    reply = f"✅ VO deadline {'cleared' if clear else 'set'} for {fmt_title_year(m.title, m.year)} [{m.code}]\nTarget: {role_token}\nMatched roles: {count}\nDeadline: {fmt_myt(dt_utc)}\nRoles: {preview}"
    await update.effective_message.reply_text(reply, disable_web_page_preview=True)
    await _send_undo_message(update.effective_message, m, undo_token, "dvo")
    try:
        await _try_update_movie_card(context, m)
    except Exception:
        pass
async def cmd_remind_tr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    query = _context_args_text(context)
    if not query:
        return await update.effective_message.reply_text("Usage: /remind_tr <MOVIE_CODE or title>")
    m, err = _require_movie_arg(query)
    if not m:
        return await update.effective_message.reply_text(err)
    task = _translation_task_for_movie(m)
    if not task:
        return await update.effective_message.reply_text("❌ No translation task found for this movie.")
    ok, note = await _send_translation_task_reminder(context, task)
    await update.effective_message.reply_text(note, disable_web_page_preview=True)
async def cmd_remind_vo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    raw = (getattr(update.effective_message, 'text', '') or '').split(' ', 1)
    payload = raw[1].strip() if len(raw) > 1 else ''
    if not payload:
        return await update.effective_message.reply_text("Usage: /remind_vo <MOVIE_CODE or title> | <role/open/all>")
    if '|' in payload:
        movie_q, role_token = [x.strip() for x in payload.split('|', 1)]
    else:
        movie_q, role_token = payload, 'open'
    m, err = _require_movie_arg(movie_q)
    if not m:
        return await update.effective_message.reply_text(err)
    sent, total, notes = await _remind_vo_for_movie(context, m, role_token)
    lines = [f"🔔 VO reminder run — {fmt_title_year(m.title, m.year)} [{m.code}]", f"Target: {role_token}", f"Sent: {sent}/{total}"]
    if notes:
        lines.append('')
        lines.extend(notes[:10])
    await update.effective_message.reply_text('\n'.join(lines), disable_web_page_preview=True)
def _overdue_translation_tasks(now: datetime | None = None) -> list[TranslationTask]:
    now = now or _now_utc()
    rows = [
        t for t in TranslationTask.query.order_by(TranslationTask.deadline_at.asc().nullslast(), TranslationTask.updated_at.desc()).all()
        if (t.status or '').upper() != 'COMPLETED' and t.deadline_at and t.deadline_at < now
    ]
    return rows
def _overdue_vo_assignments(now: datetime | None = None) -> list[tuple[Movie, Assignment]]:
    now = now or _now_utc()
    rows: list[tuple[Movie, Assignment]] = []
    for m in Movie.query.order_by(Movie.updated_at.desc(), Movie.id.desc()).all():
        open_assigns, _submitted = _open_assignments_for_movie(m)
        for a in open_assigns:
            if a.deadline_at and a.deadline_at < now:
                rows.append((m, a))
    rows.sort(key=lambda item: ((item[1].deadline_at or now), (item[0].code or ''), (item[1].role or '')))
    return rows
def _priority_movie_rows(limit: int = 10) -> list[tuple[int, Movie, dict[str, Any]]]:
    now = _now_utc()
    scored: list[tuple[int, Movie, dict[str, Any]]] = []
    for m in Movie.query.order_by(Movie.updated_at.desc(), Movie.id.desc()).all():
        task = _translation_task_for_movie(m)
        open_assigns, submitted = _open_assignments_for_movie(m)
        tr_overdue = bool(task and (task.status or '').upper() != 'COMPLETED' and task.deadline_at and task.deadline_at < now)
        vo_overdue = len([a for a in open_assigns if a.deadline_at and a.deadline_at < now])
        translator_open = 1 if task and (task.status or '').upper() != 'COMPLETED' else 0
        open_roles = len(open_assigns)
        if not translator_open and not open_roles and not tr_overdue and not vo_overdue:
            continue
        score = (8 if tr_overdue else 0) + (vo_overdue * 3) + (2 if translator_open else 0) + open_roles
        meta = {
            'translator_open': translator_open,
            'tr_overdue': tr_overdue,
            'open_roles': open_roles,
            'vo_overdue': vo_overdue,
            'status': m.status or '-',
            'translator': task.translator_name if task and task.translator_name else (m.translator_assigned or '-'),
            'deadline': task.deadline_at if task and task.deadline_at else None,
        }
        scored.append((score, m, meta))
    scored.sort(key=lambda row: (-row[0], -(1 if row[2]['tr_overdue'] else 0), -row[2]['vo_overdue'], -row[2]['open_roles'], ((row[1].updated_at or now)), (row[1].code or '')))
    return scored[:max(1, min(limit, 20))]
async def cmd_remind_overdue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    args = [a.strip().lower() for a in (context.args or []) if str(a).strip()]
    scope = 'all'
    limit = 10
    if args:
        if args[0] in {'all', 'translator', 'vo'}:
            scope = args[0]
            if len(args) > 1 and args[1].isdigit():
                limit = max(1, min(int(args[1]), 30))
        elif args[0].isdigit():
            limit = max(1, min(int(args[0]), 30))
    now = _now_utc()
    lines = [f"📣 Batch overdue reminder — {scope}", f"Time: {fmt_myt(now)}", f"Limit: {limit}", ""]
    total_sent = 0
    total_attempted = 0
    if scope in {'all', 'translator'}:
        tasks = _overdue_translation_tasks(now)[:limit]
        sent = 0
        notes: list[str] = []
        for task in tasks:
            ok, note = await _send_translation_task_reminder(context, task)
            total_attempted += 1
            if ok:
                sent += 1
                total_sent += 1
            notes.append(note)
        lines.append(f"Translator overdue picked: {len(tasks)}")
        lines.append(f"Translator reminders sent: {sent}/{len(tasks)}")
        if notes:
            lines.extend(notes[:8])
        lines.append("")
    if scope in {'all', 'vo'}:
        rows = _overdue_vo_assignments(now)[:limit]
        sent = 0
        notes: list[str] = []
        for movie, a in rows:
            ok, note = await _send_vo_assignment_reminder(context, movie, a)
            total_attempted += 1
            if ok:
                sent += 1
                total_sent += 1
            notes.append(note)
        lines.append(f"VO overdue picked: {len(rows)}")
        lines.append(f"VO reminders sent: {sent}/{len(rows)}")
        if notes:
            lines.extend(notes[:8])
        lines.append("")
    lines.append(f"Total reminders sent: {total_sent}/{total_attempted}")
    lines.append("Tip: use /priority to see which movies should be chased first.")
    await update.effective_message.reply_text('\n'.join(lines), disable_web_page_preview=True)
async def cmd_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    limit = 10
    if context.args and str(context.args[0]).isdigit():
        limit = max(1, min(int(context.args[0]), 20))
    rows = _priority_movie_rows(limit)
    if not rows:
        return await update.effective_message.reply_text("No priority movies right now.")
    lines = [f"🔥 Priority movies (top {len(rows)})", f"Time: {fmt_myt(_now_utc())}", ""]
    for idx, (score, movie, meta) in enumerate(rows, 1):
        flags = []
        if meta['tr_overdue']:
            flags.append('TR overdue')
        if meta['vo_overdue']:
            flags.append(f"VO overdue {meta['vo_overdue']}")
        if meta['translator_open']:
            flags.append('TR open')
        if meta['open_roles']:
            flags.append(f"Open roles {meta['open_roles']}")
        flag_text = ' • '.join(flags) or 'Open work'
        lines.append(f"{idx}. {fmt_title_year(movie.title, movie.year)} [{movie.code}] — score {score}")
        lines.append(f"   {flag_text}")
        lines.append(f"   Status: {meta['status']} • Translator: {meta['translator']}")
        if meta['deadline']:
            lines.append(f"   Translation deadline: {fmt_myt(meta['deadline'])}")
    lines.extend(["", "Quick actions:", "• /summary_today", "• /remind_overdue all 10", "• /movie <CODE or title>"])
    await update.effective_message.reply_text('\n'.join(lines), disable_web_page_preview=True)
async def cmd_summary_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    await update.effective_message.reply_text(_build_summary_today_text(limit=5), disable_web_page_preview=True)
async def cmd_digest_here(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    chat_id = str(update.effective_chat.id)
    ok = _kv_set("digest_chat_id", chat_id)
    if ok:
        await update.effective_message.reply_text(f"✅ Admin digest destination saved.\nChat ID: {chat_id}")
    else:
        await update.effective_message.reply_text("❌ Failed to save digest destination.")
async def cmd_digest_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    env = (os.getenv("ADMIN_DIGEST_CHAT_ID") or "").strip()
    dbv = _kv_get("digest_chat_id")
    eff = _digest_dest_chat_id()
    where = "ENV" if env else ("DB" if dbv else ("ADMIN_TELEGRAM_CHAT_ID/BACKUP" if eff else "Not set"))
    enabled_db = (_kv_get("admin_digest_enabled") or "").strip() or "-"
    enabled = "on" if _digest_enabled() else "off"
    await update.effective_message.reply_text(
        f"📬 Admin digest status\n"
        f"- Effective: {eff or '-'} ({where})\n"
        f"- ENV ADMIN_DIGEST_CHAT_ID: {'set' if env else 'not set'}\n"
        f"- DB digest_chat_id: {dbv or '-'}\n"
        f"- Enabled: {enabled}\n"
        f"- DB admin_digest_enabled: {enabled_db}\n\n"
        f"Set via: /digest_here, /digest_on, /digest_off\n"
        f"Cron: /cron/admin_digest?key=CRON_SECRET"
    )
async def cmd_digest_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    ok = _kv_set("admin_digest_enabled", "1")
    await update.effective_message.reply_text("✅ Admin digest enabled." if ok else "❌ Failed to enable admin digest.")
async def cmd_digest_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    ok = _kv_set("admin_digest_enabled", "0")
    await update.effective_message.reply_text("✅ Admin digest disabled." if ok else "❌ Failed to disable admin digest.")
async def cmd_digest_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    args = [a.strip().lower() for a in (context.args or []) if str(a).strip()]
    send_to_dest = ("dest" in args) or ("destination" in args)
    target_chat = _digest_dest_chat_id() if send_to_dest else str(update.effective_chat.id)
    if not target_chat:
        return await update.effective_message.reply_text("❌ No digest destination configured. Run /digest_here first.")
    ok, note = await _send_admin_digest_via_context(context, target_chat, priority_limit=5)
    if send_to_dest and str(update.effective_chat.id) != str(target_chat):
        note += f"\nTarget: {target_chat}"
    await update.effective_message.reply_text(note, disable_web_page_preview=True)
async def cmd_overdue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    scope = (context.args[0] if context.args else 'all').strip().lower()
    if scope not in {'all', 'translator', 'vo'}:
        scope = 'all'
    now = _now_utc()
    lines = [f"🚨 Overdue summary — {scope}", f"Time: {fmt_myt(now)}", ""]
    if scope in {'all', 'translator'}:
        tasks = [t for t in TranslationTask.query.order_by(TranslationTask.updated_at.desc()).all() if (t.status or '').upper() != 'COMPLETED' and t.deadline_at and t.deadline_at < now]
        lines.append(f"Translator overdue: {len(tasks)}")
        for t in tasks[:10]:
            lines.append(f"• {fmt_title_year(t.title, t.year)} [{t.movie_code or '-'}] → {t.translator_name or '-'} • {fmt_myt(t.deadline_at)}")
        lines.append('')
    if scope in {'all', 'vo'}:
        count = 0
        vo_lines = []
        for m in Movie.query.order_by(Movie.updated_at.desc()).all():
            open_assigns, _submitted = _open_assignments_for_movie(m)
            for a in open_assigns:
                if a.deadline_at and a.deadline_at < now:
                    count += 1
                    if len(vo_lines) < 12:
                        vo_lines.append(f"• {m.code} {a.role} → {a.vo} • {fmt_myt(a.deadline_at)}")
        lines.append(f"VO overdue: {count}")
        lines.extend(vo_lines or ["• None"])
    lines.extend(["", "Quick actions:", "• /deadline_tr CODE | 2026-03-10 22:00", "• /deadline_vo CODE | open | 2026-03-10 22:00", "• /remind_tr CODE", "• /remind_vo CODE | open"])
    await update.effective_message.reply_text('\n'.join(lines), disable_web_page_preview=True)
async def cmd_reassign_vo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    raw = _context_args_text(context)
    if not raw or "|" not in raw:
        return await update.effective_message.reply_text("Usage: /reassign_vo <MOVIE_CODE or title> | <role> | <VO name>")
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 3:
        return await update.effective_message.reply_text("Usage: /reassign_vo <MOVIE_CODE or title> | <role> | <VO name>")
    movie_query = parts[0]
    role_text = parts[1]
    who_text = " | ".join(parts[2:]).strip()
    m, err = _require_movie_arg(movie_query)
    if not m:
        return await update.effective_message.reply_text(err)
    await _send_reassign_vo_preview(update.effective_message, update.effective_user.id, m, role_text, who_text)
async def _assign_translator_to_movie(movie: Movie, who: str, context: ContextTypes.DEFAULT_TYPE | None = None) -> tuple[bool, str]:
    who = (who or '').strip()
    if not who:
        return False, '❌ Missing translator name'
    prev_translator_assigned = movie.translator_assigned
    prev_task_snapshot = _translation_task_snapshot(TranslationTask.query.filter_by(movie_id=movie.id).first() or TranslationTask.query.filter_by(movie_code=movie.code).first())
    movie.translator_assigned = who
    movie.updated_at = _now_utc()
    db.session.commit()
    tr = None
    who_norm = who.lstrip('@').strip()
    if who_norm:
        tr = Translator.query.filter(Translator.tg_username.ilike(who_norm)).first()
    if not tr and who_norm:
        tr = Translator.query.filter(Translator.name.ilike(who_norm)).first()
    if not tr and who:
        for t in Translator.query.all():
            if t.tg_username and t.tg_username.lower() in who.lower():
                tr = t
                break
            if t.name and t.name.lower() in who.lower():
                tr = t
                break
    existing = TranslationTask.query.filter_by(movie_id=movie.id).first() or TranslationTask.query.filter_by(movie_code=movie.code).first()
    if not existing:
        existing = TranslationTask(
            movie_id=movie.id,
            movie_code=movie.code,
            title=movie.title,
            year=movie.year,
            lang=movie.lang,
            translator_id=tr.id if tr else None,
            translator_name=tr.name if tr else who,
            status='SENT',
            priority_mode=_movie_priority_mode(movie),
            deadline_at=_priority_mode_deadline(_movie_priority_mode(movie)),
            sent_at=_now_utc(),
        )
        db.session.add(existing)
    else:
        existing.movie_id = existing.movie_id or movie.id
        existing.movie_code = existing.movie_code or movie.code
        existing.title = movie.title
        existing.year = movie.year
        existing.lang = movie.lang
        if tr:
            existing.translator_id = tr.id
            existing.translator_name = tr.name
        else:
            existing.translator_name = who
        existing.status = 'SENT'
        existing.priority_mode = getattr(existing, 'priority_mode', None) or _movie_priority_mode(movie)
        if not existing.deadline_at:
            existing.deadline_at = _priority_mode_deadline(existing.priority_mode)
        existing.sent_at = existing.sent_at or _now_utc()
        existing.completed_at = None
    db.session.commit()
    dm_note = ''
    if context and tr and tr.tg_user_id:
        try:
            await context.bot.send_message(
                chat_id=int(tr.tg_user_id),
                text='\n'.join([
                    '📌 *New Translation Task*',
                    f"Movie: *{fmt_title_year(movie.title, movie.year)}* [{(movie.lang or '').upper() or '-'}]",
                    f"Code: `{movie.code}`",
                    '',
                    'Please submit the translated *.srt* by DM to this bot.',
                    'Filename accepted:',
                    '• `Title (Year).srt`  OR',
                    '• `CODE.srt`',
                ]),
                parse_mode=ParseMode.MARKDOWN,
            )
            dm_note = ' • translator notified by DM'
        except Exception:
            dm_note = ' • translator DM failed'
    try:
        if context:
            setattr(context, "_last_undo_payload", {
                "kind": "tr",
                "payload": {
                    "prev_translator_assigned": prev_translator_assigned,
                    "task_snapshot": prev_task_snapshot,
                },
            })
            await _try_update_movie_card(context, movie)
    except Exception:
        pass
    record_movie_event(movie, "ASSIGN_TRANSLATOR", f"Translator set to {who}", detail=f"prev={prev_translator_assigned or '-'}", actor_source="tg", actor_name="assign_translator")
    return True, f"✅ Translator set: {movie.code} → {who}{dm_note}"
async def cmd_assign_translator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    raw = _context_args_text(context)
    if not raw:
        return await update.effective_message.reply_text("Usage: /assign_translator <MOVIE_CODE or title> | <name/@user>")
    if "|" in raw:
        movie_query, who = [p.strip() for p in raw.split("|", 1)]
    else:
        if len(context.args) < 2:
            return await update.effective_message.reply_text("Usage: /assign_translator <MOVIE_CODE or title> | <name/@user>")
        movie_query = context.args[0].strip()
        who = " ".join(context.args[1:]).strip()
    m, err = _require_movie_arg(movie_query)
    if not m:
        return await update.effective_message.reply_text(err)
    await _send_assign_translator_preview(update.effective_message, update.effective_user.id, m, who)
async def cmd_suggest_translator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    raw = _context_args_text(context)
    if not raw:
        return await update.effective_message.reply_text("Usage: /suggest_translator <MOVIE_CODE or title>")
    m, err = _require_movie_arg(raw)
    if not m:
        return await update.effective_message.reply_text(err)
    candidates = _translator_candidate_rows(m, limit=6)
    if not candidates:
        return await update.effective_message.reply_text("❌ No active translators found")
    await update.effective_message.reply_text(
        _translator_suggestion_text(m, candidates),
        reply_markup=_translator_pick_keyboard(m, candidates),
        disable_web_page_preview=True,
    )
async def cmd_suggest_vo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    raw = _context_args_text(context)
    if not raw:
        return await update.effective_message.reply_text("Usage: /suggest_vo <MOVIE_CODE or title> | <role?>")
    if "|" in raw:
        movie_query, role = [p.strip() for p in raw.split("|", 1)]
    else:
        movie_query, role = raw, ""
    m, err = _require_movie_arg(movie_query)
    if not m:
        return await update.effective_message.reply_text(err)
    if role:
        candidates = _vo_candidate_rows(m, role, limit=6)
        if not candidates:
            return await update.effective_message.reply_text(f"❌ No active VO found for role: {role}")
        return await update.effective_message.reply_text(_vo_suggestion_text(m, role, candidates), disable_web_page_preview=True)
    open_assigns, _submitted = _open_assignments_for_movie(m)
    picks_for = open_assigns[:6]
    if not picks_for:
        picks_for = Assignment.query.filter((Assignment.movie_id == m.id) | (Assignment.project == m.code)).order_by(Assignment.role.asc()).all()[:6]
    if not picks_for:
        return await update.effective_message.reply_text("❌ No roles found for this movie")
    lines = [
        f"🎧 VO picks — {fmt_title_year(m.title, m.year)} [{m.code}]",
        "Sorted by live workload, overdue risk, level/speed, and recent activity.",
        "",
    ]
    for a in picks_for:
        lines.append(f"{a.role} ({int(a.lines or 0)} lines)")
        candidates = _vo_candidate_rows(m, a.role, limit=3)
        if not candidates:
            lines.append("• No matching active VO found.")
        else:
            for row in candidates:
                vo = row['vo']
                reasons = ', '.join(row['reasons'][:4])
                lines.append(f"• {vo.name} — {reasons}")
        lines.append(f"Use: /reassign_vo {m.code} | {a.role} | <VO name>")
        lines.append("")
    await update.effective_message.reply_text("\n".join(lines).strip(), disable_web_page_preview=True)
async def on_text_autodetect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admins can paste `Title (2025) - bn` OR paste CODE to show card."""
    if not _is_admin(update):
        return
    if not update.message or not update.message.text:
        return
    parsed = _parse_movie_text(update.message.text)
    if not parsed:
        return
    title, year, lang = parsed
    if title == "__CODE__":
        m = movie_by_code(lang)
        if not m:
            return await update.effective_message.reply_text("❌ Movie code not found in DB")
        return await send_movie_card(update, context, m)
    m, created = get_or_create_movie(title, year, lang)
    if created:
        await update.effective_message.reply_text(f"✅ Created: {m.code} — {fmt_title_year(m.title, m.year)} [{m.lang}]")
    else:
        await update.effective_message.reply_text(f"ℹ️ Already exists: {m.code} — {fmt_title_year(m.title, m.year)} [{m.lang}]")
    await send_movie_card(update, context, m)
async def send_movie_card(update: Update, context: ContextTypes.DEFAULT_TYPE, movie: Movie):
    await _send_movie_card_message(update.effective_message, movie)
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    # movie card actions
    if data.startswith("mv|"):
        return await _callback_movie(update, context)
    # bulk assign actions
    if data.startswith("bulk|"):
        return await _callback_bulk(update, context)
    # bulk movie actions
    if data.startswith("bm|"):
        return await _callback_bulk_movie(update, context)
    # project wizard actions
    if data.startswith("wiz|"):
        return await _callback_project_wizard(update, context)
    # private panel actions
    if data.startswith("panel|"):
        return await _callback_panel(update, context)
async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_dm(update):
        return await update.effective_message.reply_text(
            "Open me in DM to use the button panel.",
            disable_web_page_preview=True,
        )
    PANEL_PROMPT.pop(update.effective_user.id, None)
    await update.effective_message.reply_text(
        _panel_intro_text(update),
        reply_markup=_panel_keyboard_for_update(update),
        disable_web_page_preview=True,
    )
async def _callback_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    try:
        _, action = data.split("|", 1)
    except ValueError:
        return
    uid = getattr(getattr(update, "effective_user", None), "id", None)
    if uid is None:
        return
    if action == "refresh":
        return await _safe_edit(q, _panel_intro_text(update), _panel_keyboard_for_update(update))
    if action == "help":
        await q.message.reply_text("Opening help...")
        return await cmd_help(update, context)
    if action == "my_tasks":
        return await cmd_my_tasks(update, context)
    if action == "my_roles":
        return await cmd_my_roles(update, context)
    if not _is_admin(update):
        return await q.answer("Admin only", show_alert=True)
    if action == "wizard":
        return await cmd_project_wizard(update, context)
    if action == "workload":
        context.args = []
        return await cmd_workload(update, context)
    if action == "priority":
        context.args = []
        return await cmd_priority(update, context)
    if action == "daily_summary":
        return await cmd_summary_today(update, context)
    if action == "digest_now":
        return await cmd_digest_now(update, context)
    if action == "backup_now":
        return await cmd_backup_now(update, context)
    if action == "backup_status":
        return await cmd_backup_status(update, context)
    if action == "find":
        PANEL_PROMPT[uid] = {"mode": "find_movie"}
        return await q.message.reply_text("Send movie title keyword to search. Example: Inside Out")
    if action == "archived":
        context.args = []
        return await cmd_archived(update, context)
    if action == "activity":
        context.args = []
        return await cmd_activity(update, context)
    if action == "cleanup":
        context.args = []
        return await cmd_cleanup_presets(update, context)
    if action == "duplicates":
        context.args = []
        return await cmd_duplicates(update, context)
    if action == "pending_roles":
        context.args = []
        return await cmd_pending_roles(update, context)
    if action == "whohas":
        PANEL_PROMPT[uid] = {"mode": "who_has"}
        return await q.message.reply_text("Send movie code or title to check owner/status.")
    if action == "assign_tr":
        PANEL_PROMPT[uid] = {"mode": "assign_tr_movie"}
        return await q.message.reply_text("Send movie code or title to choose the movie first.")
    if action == "reassign_vo":
        PANEL_PROMPT[uid] = {"mode": "reassign_vo_movie"}
        return await q.message.reply_text("Send movie code or title to reassign a VO role.")
    if action == "movie_load":
        PANEL_PROMPT[uid] = {"mode": "movie_load"}
        return await q.message.reply_text("Send movie code or title to view workload for one movie.")
    if action == "overdue":
        context.args = ['all']
        return await cmd_overdue(update, context)
    if action == "remind_overdue":
        context.args = ['all', '10']
        return await cmd_remind_overdue(update, context)
async def _handle_panel_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not _is_dm(update) or not update.effective_user or not update.effective_message:
        return False
    msg = update.effective_message
    if not getattr(msg, "text", None):
        return False
    uid = update.effective_user.id
    state = PANEL_PROMPT.get(uid)
    if not state:
        return False
    mode = (state.get("mode") or "").strip()
    text = (msg.text or "").strip()
    if not text:
        return True
    if mode == "find_movie":
        PANEL_PROMPT.pop(uid, None)
        matches = _search_movies(text, limit=8)
        if not matches:
            await msg.reply_text(f"❌ Movie not found: {text}")
        else:
            lines = [f"🎬 Movie search — {text}", "Tap a movie below to open its action card.", ""]
            for m in matches:
                lines.append(f"• {fmt_title_year(m.title, m.year)} [{(m.lang or '').upper() or '-'}] — {m.code}")
            await msg.reply_text("\n".join(lines), disable_web_page_preview=True, reply_markup=_movie_search_keyboard(matches))
        await msg.reply_text(_panel_intro_text(update), reply_markup=_panel_keyboard_for_update(update), disable_web_page_preview=True)
        return True
    if mode == "who_has":
        PANEL_PROMPT.pop(uid, None)
        m, err = _require_movie_arg(text)
        if not m:
            await msg.reply_text(err)
        else:
            await msg.reply_text(_who_has_text(m), disable_web_page_preview=True)
        await msg.reply_text(_panel_intro_text(update), reply_markup=_panel_keyboard_for_update(update), disable_web_page_preview=True)
        return True
    if mode == "assign_tr_movie":
        m, err = _require_movie_arg(text)
        if not m:
            await msg.reply_text(err)
            return True
        PANEL_PROMPT[uid] = {"mode": "assign_tr_name", "movie_code": m.code}
        await msg.reply_text(
            "\n".join([
                f"Selected: {fmt_title_year(m.title, m.year)} [{m.code}]",
                "Now send translator name or @username.",
                "Example: Ryan  or  @ryan",
            ]),
            disable_web_page_preview=True,
        )
        return True
    if mode == "reassign_vo_movie":
        m, err = _require_movie_arg(text)
        if not m:
            await msg.reply_text(err)
            return True
        PANEL_PROMPT[uid] = {"mode": "reassign_vo", "movie_code": m.code}
        await msg.reply_text(_reassign_vo_prompt_text(m), disable_web_page_preview=True)
        return True
    if mode == "movie_load":
        PANEL_PROMPT.pop(uid, None)
        m, err = _require_movie_arg(text)
        if not m:
            await msg.reply_text(err)
        else:
            await msg.reply_text(_movie_workload_text(m), disable_web_page_preview=True)
        await msg.reply_text(_panel_intro_text(update), reply_markup=_panel_keyboard_for_update(update), disable_web_page_preview=True)
        return True
    if mode == "reassign_vo":
        PANEL_PROMPT.pop(uid, None)
        movie_code = (state.get("movie_code") or "").strip().upper()
        m = movie_by_code(movie_code)
        if not m:
            await msg.reply_text("❌ Movie not found anymore. Start again with /panel.")
        else:
            if "|" not in text:
                await msg.reply_text("Usage: role | VO name  (example: man1 | Faiz)")
                PANEL_PROMPT[uid] = {"mode": "reassign_vo", "movie_code": movie_code}
                return True
            role_text, who_text = [part.strip() for part in text.split("|", 1)]
            await _send_reassign_vo_preview(msg, uid, m, role_text, who_text)
        await msg.reply_text(_panel_intro_text(update), reply_markup=_panel_keyboard_for_update(update), disable_web_page_preview=True)
        return True
    if mode == "assign_tr_name":
        PANEL_PROMPT.pop(uid, None)
        movie_code = (state.get("movie_code") or "").strip().upper()
        m = movie_by_code(movie_code)
        if not m:
            await msg.reply_text("❌ Movie not found anymore. Start again with /panel.")
        else:
            await _send_assign_translator_preview(msg, uid, m, text)
        await msg.reply_text(_panel_intro_text(update), reply_markup=_panel_keyboard_for_update(update), disable_web_page_preview=True)
        return True
    return False
async def _callback_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    parts = data.split("|")
    if len(parts) < 3:
        return
    _, action, code, *extra = parts
    if not _is_admin(update):
        return await q.answer("Not allowed", show_alert=True)
    m = movie_by_code(code)
    if not m:
        if action in {"archgo", "archcx", "delgo", "delcx"}:
            m = movie_by_code(code, include_archived=True)
        if not m:
            return await _safe_edit(q, "❌ Movie not found", None)
    if action == "undo":
        if not extra:
            return await q.answer("Undo token missing", show_alert=True)
        row = _take_undo_action(extra[0], user_id=update.effective_user.id, consume=True)
        if not row:
            return await q.answer("Undo expired or not yours", show_alert=True)
        ok, reply = await _apply_undo_action(m, row, context)
        await _safe_edit(q, reply, _movie_keyboard(m.code))
        return
    if action == "archask":
        preview = "\n".join([
            f"🗃️ Archive movie — {fmt_title_year(m.title, m.year)} [{m.code}]",
            "",
            "This hides the movie from Telegram search.",
            "Active assignments, VO submissions, and translation tasks will be cleared.",
            "You can still unarchive later from the Archived page or /unarchive_movie.",
        ])
        await _safe_edit(q, preview, _movie_admin_confirm_keyboard(m.code, "archive"))
        return
    if action == "archcx":
        await _safe_edit(q, _movie_card_text(m), _movie_keyboard(m.code))
        return
    if action == "archgo":
        _archive_movie_record_db(m)
        db.session.commit()
        await _safe_edit(q, f"🗃️ Archived {fmt_title_year(m.title, m.year)} [{m.code}]\nHidden from Telegram search. Use /unarchive_movie {m.code} to bring it back.", None)
        return
    if action == "delask":
        preview = "\n".join([
            f"💥 Hard delete movie — {fmt_title_year(m.title, m.year)} [{m.code}]",
            "",
            "This permanently deletes the movie and related records.",
            "This cannot be undone.",
        ])
        await _safe_edit(q, preview, _movie_admin_confirm_keyboard(m.code, "delete"))
        return
    if action == "delcx":
        await _safe_edit(q, _movie_card_text(m), _movie_keyboard(m.code))
        return
    if action == "delgo":
        label, dead_code = _hard_delete_movie_record_db(m)
        db.session.commit()
        await _safe_edit(q, f"💥 Hard deleted {label} [{dead_code}] permanently.", None)
        return
    if action == "card":
        await _send_movie_card_message(q.message, m)
        return
    if action == "who":
        await q.message.reply_text(_who_has_text(m), disable_web_page_preview=True)
        return
    if action == "hist":
        await q.message.reply_text(_movie_history_text(m), disable_web_page_preview=True)
        return
    if action == "load":
        await q.message.reply_text(_movie_workload_text(m), disable_web_page_preview=True)
        return
    if action == "dead":
        await q.message.reply_text(_movie_deadline_text(m), disable_web_page_preview=True)
        return
    if action == "remind":
        lines = [f"🔔 Reminder run — {fmt_title_year(m.title, m.year)} [{m.code}]", ""]
        task = _translation_task_for_movie(m)
        if task and (task.status or '').upper() != 'COMPLETED':
            ok, note = await _send_translation_task_reminder(context, task)
            lines.append(note)
        else:
            lines.append('ℹ️ Translator reminder skipped: no active translation task')
        sent, total, notes = await _remind_vo_for_movie(context, m, 'open')
        lines.append(f"VO reminders sent: {sent}/{total}")
        if notes:
            lines.extend(notes[:8])
        await q.message.reply_text('\n'.join(lines), disable_web_page_preview=True)
        try:
            await _safe_edit(q, _movie_card_text(m), _movie_keyboard(m.code))
        except Exception:
            pass
        return
    if action == "picks":
        picks = _translator_candidate_rows(m, limit=6)
        if not picks:
            return await q.answer("No active translators found", show_alert=True)
        await q.message.reply_text(
            _translator_suggestion_text(m, picks),
            reply_markup=_translator_pick_keyboard(m, picks),
            disable_web_page_preview=True,
        )
        return
    if action == "vopicks":
        open_assigns, _submitted = _open_assignments_for_movie(m)
        picks_for = open_assigns[:6]
        if not picks_for:
            picks_for = Assignment.query.filter((Assignment.movie_id == m.id) | (Assignment.project == m.code)).order_by(Assignment.role.asc()).all()[:6]
        if not picks_for:
            return await q.answer("No roles found for this movie", show_alert=True)
        lines = [
            f"🎧 VO picks — {fmt_title_year(m.title, m.year)} [{m.code}]",
            "Sorted by live workload, overdue risk, level/speed, and recent activity.",
            "",
        ]
        for a in picks_for:
            lines.append(f"{a.role} ({int(a.lines or 0)} lines)")
            candidates = _vo_candidate_rows(m, a.role, limit=3)
            if not candidates:
                lines.append("• No matching active VO found.")
            else:
                for row in candidates:
                    vo = row['vo']
                    reasons = ', '.join(row['reasons'][:4])
                    lines.append(f"• {vo.name} — {reasons}")
            lines.append(f"Use: /reassign_vo {m.code} | {a.role} | <VO name>")
            lines.append("")
        await q.message.reply_text("\n".join(lines).strip(), disable_web_page_preview=True)
        return
    if action == "trpick":
        if not extra:
            return
        tr = Translator.query.filter_by(id=int(extra[0])).first()
        if not tr:
            return await q.answer("Translator not found", show_alert=True)
        await _send_assign_translator_preview(q.message, update.effective_user.id, m, tr.name)
        return
    if action == "cftr":
        if not extra:
            return
        row = _take_pending_action(extra[0], user_id=update.effective_user.id, kind="tr", consume=True)
        if not row:
            return await q.answer("Preview expired or not yours", show_alert=True)
        who = (row.get("payload") or {}).get("who") or ""
        ok, reply = await _assign_translator_to_movie(m, who, context)
        await _safe_edit(q, reply, _movie_keyboard(m.code))
        undo_meta = getattr(context, "_last_undo_payload", None)
        if undo_meta:
            token = _new_undo_action(undo_meta.get("kind") or "tr", update.effective_user.id, m, undo_meta.get("payload") or {})
            setattr(context, "_last_undo_payload", None)
            await q.message.reply_text(_undo_summary("tr", m), reply_markup=_undo_keyboard(m.code, token), disable_web_page_preview=True)
        return
    if action == "cxtr":
        if not extra:
            return
        row = _take_pending_action(extra[0], user_id=update.effective_user.id, kind="tr", consume=True)
        if not row:
            return await q.answer("Preview expired", show_alert=True)
        await _safe_edit(q, "❌ Translator assign cancelled.", _movie_keyboard(m.code))
        return
    if action == "cfvo":
        if not extra:
            return
        row = _take_pending_action(extra[0], user_id=update.effective_user.id, kind="vo", consume=True)
        if not row:
            return await q.answer("Preview expired or not yours", show_alert=True)
        payload = row.get("payload") or {}
        role_text = payload.get("role") or ""
        who_text = payload.get("who") or ""
        ok, reply = await _reassign_vo_role(m, role_text, who_text, context)
        await _safe_edit(q, reply, _movie_keyboard(m.code))
        undo_meta = getattr(context, "_last_undo_payload", None)
        if undo_meta:
            token = _new_undo_action(undo_meta.get("kind") or "vo", update.effective_user.id, m, undo_meta.get("payload") or {})
            setattr(context, "_last_undo_payload", None)
            await q.message.reply_text(_undo_summary("vo", m), reply_markup=_undo_keyboard(m.code, token), disable_web_page_preview=True)
        return
    if action == "cxvo":
        if not extra:
            return
        row = _take_pending_action(extra[0], user_id=update.effective_user.id, kind="vo", consume=True)
        if not row:
            return await q.answer("Preview expired", show_alert=True)
        await _safe_edit(q, "❌ VO reassign cancelled.", _movie_keyboard(m.code))
        return
    if action == "cfmg":
        if not extra:
            return
        row = _take_pending_action(extra[0], user_id=update.effective_user.id, kind="mg", consume=True)
        if not row:
            return await q.answer("Preview expired or not yours", show_alert=True)
        payload = row.get("payload") or {}
        source = movie_by_code((payload.get("source_code") or "").strip().upper(), include_archived=True)
        if not source:
            return await _safe_edit(q, "❌ Source movie not found anymore.", _movie_keyboard(m.code))
        result = merge_movies(source, m, actor_source="tg", actor_name="merge_movie", delete_source=bool(payload.get("delete_source")))
        db.session.commit()
        moved = result.get("moved") or {}
        reply = f"✅ Merged {source.code} into {m.code} • moved {moved.get('total_rows', 0)} row(s) • source {result.get('source_state')}"
        await _safe_edit(q, reply, _movie_keyboard(m.code))
        return
    if action == "cxmg":
        if not extra:
            return
        _take_pending_action(extra[0], user_id=update.effective_user.id, kind="mg", consume=True)
        await _safe_edit(q, "❌ Movie merge cancelled.", _movie_keyboard(m.code))
        return
    if action == "assign":
        ok = await _start_assign_translator_prompt(context, update.effective_user.id, m, q.message)
        if ok:
            if getattr(getattr(q.message, 'chat', None), 'type', None) == ChatType.PRIVATE:
                return await q.answer("Send translator name in this chat.")
            return await q.answer("Check your DM for the translator prompt.", show_alert=True)
        return await q.answer("Open bot DM first, then try again.", show_alert=True)
    if action == "revo":
        ok = await _start_reassign_vo_prompt(context, update.effective_user.id, m, q.message)
        if ok:
            if getattr(getattr(q.message, 'chat', None), 'type', None) == ChatType.PRIVATE:
                return await q.answer("Send role | VO name in this chat.")
            return await q.answer("Check your DM for the VO prompt.", show_alert=True)
        return await q.answer("Open bot DM first, then try again.", show_alert=True)
    if action == "clear":
        removed_assign = Assignment.query.filter((Assignment.movie_id == m.id) | (Assignment.project == code)).delete(synchronize_session=False)
        removed_sub = VORoleSubmission.query.filter_by(movie=code).delete(synchronize_session=False)
        record_movie_event(m, "CLEAR_ACTIVE", "Bot cleared active roles only", detail=f"assignments={removed_assign} • vo_submissions={removed_sub}", actor_source="tg", actor_name="movie_card")
        m.updated_at = _now_utc()
        db.session.commit()
        await q.message.reply_text(f"✅ Cleared assignments + VO submissions for {fmt_title_year(m.title, m.year)} [{code}]")
        await _safe_edit(q, _movie_card_text(m), _movie_keyboard(m.code))
        return
    if action == "recv":
        m.status = "RECEIVED"
        m.received_at = m.received_at or _now_utc()
    elif action == "qa":
        m.status = "READY_FOR_QA"
        m.submitted_at = m.submitted_at or _now_utc()
    elif action == "embed":
        m.status = "WAIT_EMBED"
    elif action == "done":
        m.status = "COMPLETED"
        m.completed_at = _now_utc()
        await _archive_movie(context, m)
    elif action == "prog":
        txt = await _progress_text(m)
        return await q.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)
    else:
        return
    record_movie_event(m, "STATUS", f"Status set to {m.status}", actor_source="tg", actor_name="movie_card")
    m.updated_at = _now_utc()
    db.session.commit()
    await _safe_edit(q, _movie_card_text(m), _movie_keyboard(m.code))
async def cmd_progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    query = _context_args_text(context)
    if not query:
        return await update.effective_message.reply_text("Usage: /progress <MOVIE_CODE or title>")
    m, err = _require_movie_arg(query)
    if not m:
        return await update.effective_message.reply_text(err)
    txt = await _progress_text(m)
    await update.effective_message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)
async def _progress_text(movie: Movie) -> str:
    expected = _expected_roles_for_movie(movie)
    submitted = _submitted_roles_for_movie(movie.code)
    missing = sorted(list(expected - submitted))
    ok = sorted(list(submitted & expected))
    return "\n".join(
        [
            f"📊 *Progress* `{movie.code}`",
            f"Status: `{movie.status}`",
            f"Expected roles: `{len(expected)}`",
            f"Submitted roles: `{len(submitted)}`",
            f"✅ Done: {', '.join(ok) if ok else '-'}",
            f"⏳ Missing: {', '.join(missing) if missing else 'NONE 🎉'}",
        ]
    )
def _expected_roles_for_movie(movie: Movie) -> set[str]:
    roles: set[str] = set()
    if movie.id:
        rows = Assignment.query.filter_by(movie_id=movie.id).all()
        for r in rows:
            n = norm_role(r.role)
            if n:
                roles.add(n)
    if not roles:
        rows = Assignment.query.filter_by(project=movie.code).all()
        for r in rows:
            n = norm_role(r.role)
            if n:
                roles.add(n)
    return roles
def _submitted_roles_for_movie(movie_code: str) -> set[str]:
    roles: set[str] = set()
    rows = VORoleSubmission.query.filter_by(movie=movie_code).all()
    for r in rows:
        n = norm_role(r.role)
        if n:
            roles.add(n)
    return roles
# -----------------------------
# Bulk Assign (VO assignment) — group friendly
# -----------------------------
async def cmd_bulk_assign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_group(update):
        return await update.effective_message.reply_text("❌ Use this in a group")
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    if not context.args:
        return await update.effective_message.reply_text("Usage: /bulk_assign <MOVIE_CODE or title>")
    query = _context_args_text(context)
    m, err = _require_movie_arg(query)
    if not m:
        return await update.effective_message.reply_text(err)
    code = m.code
    # prevent duplicates
    if Assignment.query.filter((Assignment.movie_id == m.id) | (Assignment.project == m.code)).first():
        return await update.effective_message.reply_text("⚠️ Assignments already exist. Use /clear_movie <CODE> first.")
    BULK_ASSIGN[update.effective_chat.id] = {"movie_code": m.code, "movie_id": m.id, "text": ""}
    await update.effective_message.reply_text(
        "\n".join(
            [
                f"✅ Bulk Assign ON for `{m.code}`",
                "Paste lines like:",
                "`man-1 120`",
                "`fem-1 90`",
                "Then type /done",
            ]
        ),
        parse_mode=ParseMode.MARKDOWN,
    )
async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_group(update):
        return
    if not _is_admin(update):
        return
    state = BULK_ASSIGN.get(update.effective_chat.id)
    if not state:
        return await update.effective_message.reply_text("No active bulk session.")
    parsed = parse_lines(state.get("text", ""))
    if not parsed:
        return await update.effective_message.reply_text(
            "Nothing parsed. Paste lines like `man-1 120` then /done.",
            parse_mode=ParseMode.MARKDOWN,
        )
    preview = "\n".join([f"• {r} {n}" for r, n in parsed])
    code = state["movie_code"]
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Proceed SUPER 12h", callback_data=f"bulk|superurgent|{code}"),
                InlineKeyboardButton("Proceed URGENT 24h", callback_data=f"bulk|urgent|{code}"),
            ],
            [
                InlineKeyboardButton("Proceed NON-URGENT 36h", callback_data=f"bulk|nonurgent|{code}"),
                InlineKeyboardButton("Proceed FLEXIBLE 48h", callback_data=f"bulk|flexible|{code}"),
            ]
        ]
    )
    await update.effective_message.reply_text(
        f"Parsed roles for `{code}`:\n{preview}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb,
    )
async def _callback_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    try:
        _, mode, code = data.split("|", 2)
    except ValueError:
        return
    if not _is_admin(update):
        return await q.answer("Not allowed", show_alert=True)
    state = BULK_ASSIGN.get(q.message.chat_id)
    if not state or state.get("movie_code") != code:
        return await q.answer("No bulk session", show_alert=True)
    mode = _normalize_priority_mode(mode)
    urgent = _priority_mode_urgent_only(mode)
    default_deadline = _priority_mode_deadline(mode)
    parsed = parse_lines(state.get("text", ""))
    if not parsed:
        return await q.answer("Nothing parsed", show_alert=True)
    movie = movie_by_code(code)
    if not movie:
        return await q.answer("Movie not found", show_alert=True)
    load = movie_load(code)
    used = set()
    result_lines: List[str] = []
    for role, lines in parsed:
        gender = role_gender(role)
        qset = VOTeam.query.filter_by(active=True, gender=gender)
        if urgent:
            qset = qset.filter_by(urgent_ok=True)
        picked = pick_vo(qset.all(), used, load)
        if not picked:
            result_lines.append(f"{role}: NO MATCH")
            continue
        used.add(picked.name)
        db.session.add(
            Assignment(
                project=code,
                movie_id=movie.id,
                vo=picked.name,
                role=role,
                lines=lines,
                urgent=urgent,
                priority_mode=mode,
                deadline_at=default_deadline,
            )
        )
        db.session.commit()
        result_lines.append(f"{role}: {picked.name} ({lines})")
    BULK_ASSIGN.pop(q.message.chat_id, None)
    movie.status = "VO_ASSIGNED"
    movie.updated_at = _now_utc()
    db.session.commit()
    await q.message.reply_text("\n".join([f"✅ Assignments for `{code}`" , *result_lines]), parse_mode=ParseMode.MARKDOWN)
    await _try_update_movie_card(context, movie)
async def cmd_clear_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    query = _context_args_text(context)
    if not query:
        return await update.effective_message.reply_text("Usage: /clear_movie <MOVIE_CODE or title>")
    m, err = _require_movie_arg(query)
    if not m:
        return await update.effective_message.reply_text(err)
    code = m.code
    assignment_rows = Assignment.query.filter((Assignment.movie_id == m.id) | (Assignment.project == code)).all()
    submission_rows = VORoleSubmission.query.filter_by(movie=code).all()
    undo_token = _new_undo_action("clear", update.effective_user.id, m, {
        "assignments": [_assignment_snapshot(a) for a in assignment_rows],
        "submissions": [_vo_submission_snapshot(s) for s in submission_rows],
    })
    Assignment.query.filter((Assignment.movie_id == m.id) | (Assignment.project == code)).delete(synchronize_session=False)
    VORoleSubmission.query.filter_by(movie=code).delete(synchronize_session=False)
    db.session.commit()
    await update.effective_message.reply_text(f"✅ Cleared assignments + VO submissions for {fmt_title_year(m.title, m.year)} [{code}]", disable_web_page_preview=True)
    await _send_undo_message(update.effective_message, m, undo_token, "clear")
async def cmd_undo_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return await update.effective_message.reply_text("❌ Not allowed")
    await _perform_latest_undo(update, context)
# -----------------------------
# Translator Queue Automation (DM)
# -----------------------------
async def on_dm_srt_auto_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Translator DM uploads a translated `.srt`.
    Expected filename patterns:
      - Example 3: `Inside Out (2015).srt`  (no submitter)
      - `Dune (2021) (Shazia).srt`
      - `Avatar (2009) [BN] (Rezaul).srt`
    Behavior:
      1) Parse title/year/lang/submitter from filename.
      2) If submitter missing, fallback to Telegram uploader.
      3) Create movie (if missing), create `TranslationSubmission` (READY_FOR_QA).
      4) Forward the SRT to `SRT_OUTBOX_CHAT_ID` (if set) with caption including submitter + timestamp.
    Notes:
      - This works WITHOUT /submit.
      - If user was in /submit mode, this will also consume that mode to avoid double insert.
    """
    if not _is_dm(update):
        return
    msg = update.effective_message
    if not msg or not msg.document:
        return
    file_name = msg.document.file_name or ""
    if not file_name.lower().endswith(".srt"):
        return
    uid = update.effective_user.id if update.effective_user else None
    if not uid:
        return
    try:
        log.info(
            "DM SRT received uid=%s username=%s file=%s chat_id=%s msg_id=%s",
            uid,
            getattr(update.effective_user, "username", None),
            file_name,
            getattr(getattr(update, "effective_chat", None), "id", None),
            getattr(msg, "message_id", None),
        )
    except Exception:
        pass
    # update translator roster (auto-fill tg id + last seen)
    _upsert_translator_seen(update)
    # dedupe on message_id (webhook retry safe)
    if getattr(msg, "message_id", None) and _dedupe_submission(uid, msg.message_id):
        SUBMIT_MODE.pop(uid, None)
        return
    # -----------------------------
    # Detailed ops log (translator submit)
    # -----------------------------
    detail: Dict[str, Any] = {
        "event": "translator_submit_srt",
        "app_version": APP_VERSION,
        "ts_utc": _now_utc().strftime("%Y-%m-%d %H:%M:%S"),
        "telegram": {
            "chat_id": int(getattr(update.effective_chat, "id", 0) or 0),
            "message_id": int(getattr(msg, "message_id", 0) or 0),
            "date": (msg.date.strftime("%Y-%m-%d %H:%M:%S") if getattr(msg, "date", None) else None),
            "user_id": int(uid) if uid else None,
            "username": (getattr(update.effective_user, "username", None) or None),
            "full_name": (getattr(update.effective_user, "full_name", None) or None),
            "is_admin": bool(_is_admin_id(uid)),
        },
        "document": {
            "file_name": file_name,
            "file_id": getattr(msg.document, "file_id", None),
            "file_unique_id": getattr(msg.document, "file_unique_id", None),
            "file_size": getattr(msg.document, "file_size", None),
            "mime_type": getattr(msg.document, "mime_type", None),
        },
        "caption": (msg.caption or "")[:2048] or None,
        "parse": {},
        "movie": {},
        "translation_task": {},
        "forward": {},
        "result": {},
        "errors": [],
    }
    meta = parse_srt_filename(file_name)
    title = meta.get("title")
    year = meta.get("year")
    lang_tag = meta.get("lang")
    submitter_in_name = meta.get("submitter")
    detail["parse"].update(
        {
            "meta": meta,
        }
    )
    
    # Language is optional; default if not present.
    lang = _slug_lang((lang_tag or DEFAULT_LANG).lower())
    submitter = submitter_in_name or tg_submitter_display(update)
    shown_submitter = "Anonymous" if SRT_FORWARD_ANON else submitter
    
    submitted_at = msg.date or _now_utc()  # Telegram timestamp (UTC)
    
    # Accept 2 modes:
    #   A) Title (Year).srt  -> create/find movie by title/year/lang (generates code)
    #   B) CODE.srt or caption contains CODE -> attach submission to that movie code
    code_from_any = (
        _extract_movie_code(file_name)
        or _extract_movie_code(msg.caption or "")
        or (title.upper() if title and re.fullmatch(r"[A-Za-z]{2,5}-\d{6}-\d{2}", title) else None)
    )
    detail["parse"].update(
        {
            "title": title,
            "year": year,
            "lang_tag": lang_tag,
            "submitter_in_name": submitter_in_name,
            "resolved_lang": lang,
            "resolved_submitter": submitter,
            "code_from_any": code_from_any,
        }
    )
    
    movie = None
    created_placeholder = False
    if code_from_any and (not year or (title and title.upper() == code_from_any)):
        code = code_from_any.upper()
        movie = Movie.query.filter_by(code=code).first()
        if not movie:
            # Placeholder movie: you prefer tracking by CODE; title can be edited later in web.
            movie = Movie(code=code, title=code, year=None, lang=lang, status="NEW")
            db.session.add(movie)
            db.session.flush()
            created_placeholder = True
        # normalize display fields
        title = (movie.title or code).strip()
        year = (movie.year.strip() if movie.year else None)
        lang = _slug_lang((movie.lang or lang).lower())
    else:
        if not title or not year:
            await msg.reply_text(
                "❌ I couldn’t detect *Title* and *(Year)* from the filename.\n"
                "Rename like: `Inside Out (2015).srt`  OR  send `BN-260303-01.srt` (code only).",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
    
        # Create/get movie
        movie = _reactivate_movie_if_archived(Movie.query.filter_by(title=title, year=str(year), lang=lang).first())
        if not movie:
            movie = upsert_movie(title, int(year), lang)
    detail["movie"].update(
        {
            "created_placeholder": bool(created_placeholder),
            "movie_id": int(movie.id) if movie else None,
            "code": movie.code if movie else None,
            "title": movie.title if movie else None,
            "year": movie.year if movie else None,
            "lang": movie.lang if movie else None,
        }
    )
    
    # Create queue row
    sub = TranslationSubmission(
        movie=movie.code,
        movie_id=movie.id,
        status="READY_FOR_QA",
        submitted_at=submitted_at,
        submitter_id=uid,
        submitter_username=submitter,
        content_type="document",
        text=None,
        file_id=msg.document.file_id,
        file_name=file_name,
        telegram_event_id=getattr(msg, "message_id", None),
    )
    db.session.add(sub)
    # Sync movie status
    movie.status = "READY_FOR_QA"
    movie.submitted_at = movie.submitted_at or submitted_at
    movie.updated_at = _now_utc()
    db.session.commit()
    # -----------------------------
    # Auto-complete TranslationTask (deadline-only overdue relies on this)
    # -----------------------------
    task_match_method = None
    matched_task_id = None
    try:
        tr_row = Translator.query.filter_by(tg_user_id=int(uid)).first()
        detail["translation_task"].update(
            {
                "translator_id": int(tr_row.id) if tr_row else None,
                "translator_name": (tr_row.name if tr_row else None),
                "translator_tg_user_id": int(uid),
            }
        )
        if tr_row:
            sub_dt = submitted_at
            try:
                # keep DB timestamps naive UTC
                if getattr(sub_dt, "tzinfo", None) is not None:
                    sub_dt = sub_dt.replace(tzinfo=None)
            except Exception:
                sub_dt = submitted_at
            qbase = (
                TranslationTask.query.filter_by(translator_id=tr_row.id)
                .filter(TranslationTask.status.ilike("SENT"))
                .order_by(TranslationTask.sent_at.desc().nullslast(), TranslationTask.id.desc())
            )
            candidates = qbase.all()
            detail["translation_task"]["candidates"] = [
                {
                    "id": int(t.id),
                    "movie_code": t.movie_code,
                    "movie_id": t.movie_id,
                    "title": t.title,
                    "year": t.year,
                    "lang": t.lang,
                    "deadline_at": (t.deadline_at.isoformat() if t.deadline_at else None),
                    "sent_at": (t.sent_at.isoformat() if t.sent_at else None),
                }
                for t in candidates[:20]
            ]
            detail["translation_task"]["candidate_count"] = len(candidates)
            task = (
                qbase.filter((TranslationTask.movie_id == movie.id) | (TranslationTask.movie_code == movie.code)).first()
                if movie
                else None
            )
            if task:
                task_match_method = "movie_id_or_code"
            if not task and title and year:
                task = (
                    qbase.filter(TranslationTask.title.ilike(title))
                    .filter(TranslationTask.year == str(year))
                    .first()
                )
                if task:
                    task_match_method = "title_year"
            if task:
                before = {
                    "status": task.status,
        "priority_mode": getattr(task, "priority_mode", None),
                    "completed_at": (task.completed_at.isoformat() if task.completed_at else None),
                }
                task.status = "COMPLETED"
                task.completed_at = sub_dt
                if not task.sent_at:
                    task.sent_at = task.created_at or _now_utc()
                db.session.commit()
                matched_task_id = int(task.id)
                detail["translation_task"].update(
                    {
                        "matched_task_id": matched_task_id,
                        "match_method": task_match_method,
                        "before": before,
                        "after": {
                            "status": task.status,
        "priority_mode": getattr(task, "priority_mode", None),
                            "completed_at": (task.completed_at.isoformat() if task.completed_at else None),
                        },
                    }
                )
            else:
                detail["translation_task"].update(
                    {
                        "matched_task_id": None,
                        "match_method": None,
                        "note": "No SENT TranslationTask matched for this translator.",
                    }
                )
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        detail["errors"].append(f"translation_task_complete_error: {e}")
    # Forward to group/channel
    forwarded = False
    if SRT_OUTBOX_CHAT_ID:
        caption = (
            "📥 *Translated SRT*\n"
            f"🎬 *{fmt_title_year(title, year)}* [{lang.upper()}]\n"
            f"🆔 `{movie.code}`\n"
            f"👤 ({shown_submitter})\n"
            f"🕒 {submitted_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"🧾 Queue ID: `{sub.id}`"
        )
        try:
            sent = await context.bot.send_document(
                chat_id=int(SRT_OUTBOX_CHAT_ID),
                document=msg.document.file_id,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
            )
            sub.drop_chat_id = int(SRT_OUTBOX_CHAT_ID)
            sub.drop_message_id = sent.message_id
            db.session.commit()
            forwarded = True
            detail["forward"].update(
                {
                    "enabled": True,
                    "outbox_chat_id": int(SRT_OUTBOX_CHAT_ID),
                    "forwarded": True,
                    "outbox_message_id": int(getattr(sent, "message_id", 0) or 0),
                    "anon": bool(SRT_FORWARD_ANON),
                }
            )
        except Exception as e:
            log.warning("SRT forward failed: %s", e)
            detail["forward"].update(
                {
                    "enabled": True,
                    "outbox_chat_id": int(SRT_OUTBOX_CHAT_ID),
                    "forwarded": False,
                    "error": str(e),
                    "anon": bool(SRT_FORWARD_ANON),
                }
            )
    else:
        detail["forward"].update({"enabled": False, "forwarded": False})
    # Notify admin
    await _notify_admin(
        context,
        "\n".join(
            [
                "📥 *Auto SRT → Queue Created*",
                f"🎬 {fmt_title_year(title, year)} [{lang.upper()}]",
                f"🆔 `{movie.code}`",
                f"👤 ({shown_submitter})",
                f"🧾 Queue ID: `{sub.id}`",
                f"Forwarded: `{int(forwarded)}`",
            ]
        ),
    )
    # Write a single rich log entry (DB-backed) for debugging.
    try:
        detail["result"].update(
            {
                "submission_id": int(sub.id) if getattr(sub, "id", None) else None,
                "queue_status": sub.status,
                "movie_status": movie.status if movie else None,
                "forwarded": bool(forwarded),
                "translation_task_completed": bool(matched_task_id),
            }
        )
        log_event("INFO", "tg.translator_srt", _human_translator_srt_log(detail), traceback=json.dumps(detail, ensure_ascii=False, indent=2))
    except Exception:
        pass
    try:
        log.info(
            "DM SRT processed movie=%s submission_id=%s forwarded=%s task_completed=%s",
            (movie.code if movie else None),
            getattr(sub, "id", None),
            int(bool(forwarded)),
            int(bool(matched_task_id)),
        )
    except Exception:
        pass
    # Consume submit mode to avoid duplicates
    SUBMIT_MODE.pop(uid, None)
    # Translator/public message: hide internal code/ids. Admins can still see them via admin chat.
    public_lines = [
        "✅ Received.",
        f"Movie: *{fmt_title_year(title, year)}*",
        f"Language: *{lang_label(lang)}*",
        "Queue: `READY_FOR_QA`",
    ]
    if _is_admin_id(uid):
        public_lines.extend(
            [
                f"Code: `{movie.code}`",
                f"Queue ID: `{sub.id}`",
                f"Sent to group: `{int(forwarded)}`",
            ]
        )
    await msg.reply_text("\n".join(public_lines), parse_mode=ParseMode.MARKDOWN)
async def cmd_submit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_dm(update):
        return await update.effective_message.reply_text("❌ Please DM me for submissions.")
    if not context.args:
        return await update.effective_message.reply_text("Usage: /submit <MOVIE_CODE or Title>")
    token = _context_args_text(context)
    SUBMIT_MODE[update.effective_user.id] = token
    await update.effective_message.reply_text(
        "✅ Submit mode ON.\n\nNow send:\n- translated *text*, or\n- upload the *document/file*.\n\nUse /cancel to stop.",
        parse_mode=ParseMode.MARKDOWN,
    )
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = getattr(getattr(update, 'effective_user', None), 'id', None)
    if uid is not None:
        SUBMIT_MODE.pop(uid, None)
        PANEL_PROMPT.pop(uid, None)
        PROJECT_WIZARD.pop(uid, None)
    await update.effective_message.reply_text("Cancelled.")
def _dedupe_submission(uid: int, event_id: int) -> bool:
    # Avoid double insert on webhook retry
    row = TranslationSubmission.query.filter_by(submitter_id=uid, telegram_event_id=event_id).first()
    return bool(row)
async def on_dm_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_dm(update):
        return
    if not update.effective_user or not update.effective_message:
        return
    uid = update.effective_user.id
    msg = update.effective_message
    if await _handle_project_wizard_message(update, context):
        return
    if await _handle_panel_prompt(update, context):
        return
    if uid not in SUBMIT_MODE:
        return
    # dedupe on message_id
    if getattr(msg, "message_id", None) and _dedupe_submission(uid, msg.message_id):
        SUBMIT_MODE.pop(uid, None)
        return
    token = (SUBMIT_MODE.get(uid) or "").strip()
    # -----------------------------
    # Detailed ops log (/submit mode)
    # -----------------------------
    detail: Dict[str, Any] = {
        "event": "submit_mode",
        "app_version": APP_VERSION,
        "ts_utc": _now_utc().strftime("%Y-%m-%d %H:%M:%S"),
        "token": token,
        "telegram": {
            "chat_id": int(getattr(getattr(update, "effective_chat", None), "id", 0) or 0),
            "message_id": int(getattr(msg, "message_id", 0) or 0),
            "date": (msg.date.strftime("%Y-%m-%d %H:%M:%S") if getattr(msg, "date", None) else None),
            "user_id": int(uid),
            "username": getattr(update.effective_user, "username", None),
            "full_name": getattr(update.effective_user, "full_name", None),
            "is_admin": bool(_is_admin_id(uid)),
        },
        "movie": {},
        "submission": {},
        "forward": {},
        "result": {},
        "errors": [],
    }
    movie = None
    movie_code = token
    # resolve movie
    try:
        if re.fullmatch(r"[A-Za-z]{2,5}-\d{6}-\d{2}", token.strip()):
            movie = movie_by_code(token)
            if movie:
                movie_code = movie.code
        else:
            p = _parse_movie_text(token)
            if p and p[0] != "__CODE__":
                title, year, lang = p
                movie = _reactivate_movie_if_archived(Movie.query.filter_by(title=title, year=str(year) if year else None, lang=_slug_lang(lang)).first())
                if not movie:
                    movie = upsert_movie(title, year, lang)
                movie_code = movie.code
    except Exception as e:
        detail["errors"].append(f"movie_resolve_error: {e}")
    submitted_at = msg.date or _now_utc()
    content_type = "text"
    text_val = msg.text if msg.text else None
    file_id = None
    file_name = None
    if msg.document:
        content_type = "document"
        file_id = msg.document.file_id
        file_name = msg.document.file_name
        text_val = None
    sub = TranslationSubmission(
        movie=movie_code,
        movie_id=movie.id if movie else None,
        status="READY_FOR_QA",
        submitted_at=submitted_at,
        submitter_id=uid,
        submitter_username=tg_submitter_display(update),
        content_type=content_type,
        text=text_val,
        file_id=file_id,
        file_name=file_name,
        telegram_event_id=getattr(msg, "message_id", None),
    )
    db.session.add(sub)
    if movie:
        movie.status = "READY_FOR_QA"
        movie.submitted_at = movie.submitted_at or submitted_at
        movie.updated_at = _now_utc()
    db.session.commit()
    # Fill log snapshot after commit (IDs are available now)
    try:
        detail["movie"] = {
            "movie_id": int(movie.id) if movie else None,
            "code": (movie_code or None),
            "title": (movie.title if movie else None),
            "year": (movie.year if movie else None),
            "lang": (movie.lang if movie else None),
            "status": (movie.status if movie else None),
        }
        detail["submission"] = {
            "content_type": content_type,
            "file_id": file_id,
            "file_name": file_name,
            "text_len": (len(text_val) if text_val else 0),
            "text_preview": ((text_val or "")[:400] if content_type == "text" else None),
        }
        detail["result"] = {
            "submission_id": int(sub.id) if getattr(sub, "id", None) else None,
            "queue_status": sub.status,
        }
    except Exception:
        pass
    forwarded = False
    # optional anon forward
    if DROP_CHAT_ID:
        try:
            if content_type == "text":
                sent = await context.bot.send_message(
                    chat_id=int(DROP_CHAT_ID),
                    text=f"[{movie_code}] (anon)\n\n{text_val or ''}",
                )
            else:
                sent = await context.bot.send_document(
                    chat_id=int(DROP_CHAT_ID),
                    document=file_id,
                    caption=f"[{movie_code}] (anon)\n{file_name or ''}",
                )
            sub.drop_chat_id = int(DROP_CHAT_ID)
            sub.drop_message_id = int(getattr(sent, "message_id", 0) or 0)
            db.session.commit()
            forwarded = True
            detail["forward"] = {
                "enabled": True,
                "drop_chat_id": int(DROP_CHAT_ID),
                "forwarded": True,
                "drop_message_id": int(getattr(sent, "message_id", 0) or 0),
            }
        except Exception as e:
            detail["forward"] = {"enabled": True, "drop_chat_id": int(DROP_CHAT_ID), "forwarded": False, "error": str(e)}
    else:
        detail["forward"] = {"enabled": False, "forwarded": False}
    await _notify_admin(
        context,
        "\n".join(
            [
                "📥 *New Translation Submission*",
                f"Movie: `{movie_code}`",
                f"By: @{update.effective_user.username or '-'} (id `{uid}`)",
                f"Type: `{content_type}`",
                f"Queue ID: `{sub.id}`",
                "Status: `READY_FOR_QA`",
            ]
        ),
    )
    # DB-backed rich log
    try:
        detail["result"].update({"forwarded": bool(forwarded)})
        log_event("INFO", "tg.submit_mode", _human_submit_mode_log(detail), traceback=json.dumps(detail, ensure_ascii=False, indent=2))
    except Exception:
        pass
    SUBMIT_MODE.pop(uid, None)
    await msg.reply_text("✅ Submitted. QA will review soon.")
# -----------------------------
# Option A (Group Auto Detect) helpers
# -----------------------------
async def on_group_media_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store last detected (title,year,lang) context for this group.
    Triggered by ANY media/document/video posted in a group.
    Year is mandatory; if not detected, we do nothing.
    """
    if not _is_group(update):
        return
    msg = update.effective_message
    if not msg:
        return
    # Telegram nuance: videos sent "as video" often don't have file_name.
    # But the *caption* frequently carries the original filename.
    file_name = None
    if msg.document and getattr(msg.document, "file_name", None):
        file_name = msg.document.file_name
    elif msg.audio and getattr(msg.audio, "file_name", None):
        file_name = msg.audio.file_name
    else:
        # video/file without a filename → fall back to caption/text
        file_name = getattr(getattr(msg, "video", None), "file_name", None)
        if not file_name:
            cap = (msg.caption or "").strip()
            if cap:
                file_name = cap.splitlines()[0].strip()
        if not file_name:
            # last resort: sometimes filename is mirrored in message text
            txt = (msg.text or "").strip()
            if txt and any(ext in txt.lower() for ext in (".srt", ".ass", ".mp4", ".mkv", ".avi", ".mov")):
                file_name = txt.splitlines()[0].strip()
    if not file_name:
        return
    try:
        if msg.document and _is_role_helper_filename(getattr(msg.document, "file_name", None)):
            handled = await _auto_import_role_helper_document(update, context)
            if handled:
                return
    except Exception as e:
        try:
            await _notify_admin(
                context,
                "\n".join([
                    "❌ *Role helper auto-import failed*",
                    f"Chat: `{int(getattr(getattr(update, 'effective_chat', None), 'id', 0) or 0)}`",
                    f"File: `{getattr(msg.document, 'file_name', None) or '-'}`",
                    f"Error: `{str(e)[:300]}`",
                ]),
            )
        except Exception:
            pass
        return
    try:
        _cache_recent_group_file(context, int(update.effective_chat.id), file_name, int(msg.message_id) if msg.message_id else None)
    except Exception:
        pass
    try:
        if msg.document and _is_role_helper_filename(getattr(msg.document, "file_name", None)):
            _remember_chat_role_helper_file(int(update.effective_chat.id), int(msg.message_id) if msg.message_id else None, msg.document.file_name)
    except Exception:
        pass
    parsed = parse_movie_from_filename(file_name)
    if not parsed or not parsed.get("year"):
        return
    _ctx_upsert(
        chat_id=int(update.effective_chat.id),
        title=parsed["title"],
        year=str(parsed["year"]),
        lang=parsed.get("lang") or DEFAULT_LANG,
        file_name=file_name,
        msg_id=int(msg.message_id) if msg.message_id else None,
    )
    # Cache latest detection for "nearest latest" role-list binding.
    _cache_movie_candidate(
        context,
        int(update.effective_chat.id),
        {
            "title": parsed["title"],
            "year": int(parsed["year"]),
            "lang": parsed.get("lang") or DEFAULT_LANG,
            "file_name": file_name,
            "msg_id": int(msg.message_id) if msg.message_id else None,
            "detected_at": datetime.utcnow(),
        },
    )
    # --- Option 2: always notify + duplicate check ---
    # Do not spam on the same Telegram event (webhook retries / edits).
    try:
        chat_id = int(update.effective_chat.id)
        msg_id = int(msg.message_id) if msg.message_id else None
        _notice_store = context.bot_data.setdefault("group_movie_notice_last", {})
        do_notify = True
        if msg_id and _notice_store.get(chat_id) == msg_id:
            do_notify = False
        if msg_id and do_notify:
            _notice_store[chat_id] = msg_id
        # Resolve by normalized title/year/lang.
        title = parsed["title"]
        year = int(parsed["year"])
        lang = parsed.get("lang") or DEFAULT_LANG
        existing = _reactivate_movie_if_archived(Movie.query.filter_by(title=title, year=str(year), lang=_slug_lang(lang)).first())
        created = False
        # If already exists, optionally bind this group (admin only).
        if existing:
            movie = existing
            if _is_admin(update) and not movie.vo_group_chat_id:
                movie.vo_group_chat_id = chat_id
                try:
                    chat_obj = await context.bot.get_chat(chat_id)
                    if getattr(chat_obj, "invite_link", None):
                        movie.vo_group_invite_link = chat_obj.invite_link
                except Exception:
                    pass
                movie.updated_at = _now_utc()
                db.session.commit()
        else:
            movie = None
            if _is_admin(update):
                movie = upsert_movie(title, year, lang)
                created = True
                movie.vo_group_chat_id = chat_id
                try:
                    chat_obj = await context.bot.get_chat(chat_id)
                    if getattr(chat_obj, "invite_link", None):
                        movie.vo_group_invite_link = chat_obj.invite_link
                except Exception:
                    pass
                movie.updated_at = _now_utc()
                db.session.commit()
        # Public group stays clean: detailed movie detection goes to admin only.
        if movie:
            bound_state = "✅ linked" if movie.vo_group_chat_id == chat_id else "⚠️ linked elsewhere"
            if do_notify:
                await _notify_admin(
                    context,
                    "\n".join(
                        [
                            "🎬 *Group movie detected*",
                            f"Chat: `{chat_id}`",
                            f"Movie: `{movie.code}` — {fmt_title_year(movie.title, movie.year)} [{movie.lang.upper()}]",
                            f"File: `{file_name}`",
                            f"Created: `{int(created)}`",
                            f"Bind: `{bound_state}`",
                        ]
                    ),
                )
        else:
            # No admin rights, so only notify admin; keep VO group clean.
            if do_notify:
                await _notify_admin(
                    context,
                    "\n".join(
                        [
                            "⚠️ *Movie detected but not auto-bound*",
                            f"Chat: `{chat_id}`",
                            f"Movie: {title} ({year}) [{_slug_lang(lang).upper()}]",
                            f"File: `{file_name}`",
                            "Bot could not create/bind automatically in this group.",
                        ]
                    ),
                )
    except Exception as e:
        # Never crash group handlers.
        log_event("ERROR", "tg.group_media", f"group_movie_notify_error chat_id={int(update.effective_chat.id)} err={str(e)}")
    # Option A: auto-bind group -> movie (owner/admin only)
    if _is_admin(update):
        try:
            bound = Movie.query.filter_by(vo_group_chat_id=int(update.effective_chat.id)).first()
            if not bound:
                movie = upsert_movie(parsed["title"], int(parsed["year"]), parsed.get("lang") or DEFAULT_LANG)
                movie.vo_group_chat_id = int(update.effective_chat.id)
                # best-effort invite link (requires bot admin); ignore failures
                try:
                    chat_obj = await context.bot.get_chat(update.effective_chat.id)
                    if getattr(chat_obj, "invite_link", None):
                        movie.vo_group_invite_link = chat_obj.invite_link
                except Exception:
                    pass
                db.session.commit()
        except Exception as e:
            log_event("ERROR", "tg.group_media", f"group_bind_failed chat_id={int(update.effective_chat.id)} error={str(e)}")
async def on_group_rolelist_autodetect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detect a role list text block without movie code and ask admin to approve.
    Requirements:
    - group must have a recent GroupMovieContext (72h)
    - text must look like role list
    - year is mandatory (stored in context)
    """
    if not _is_group(update):
        return
    msg = update.effective_message
    if not msg or not msg.text:
        return
    text = (msg.text or "").strip()
    if not text:
        return
    # If message already has movie code, let existing flows handle it
    if _extract_movie_code(text):
        return
    if not ROLELIST_HINT_RE.search(text):
        return
    # If this group is already bound to a movie, we still allow role-lists:
    # - If assignments don't exist yet, create a pending import request
    # - If assignments exist, ignore to avoid duplicates
    bound = Movie.query.filter_by(vo_group_chat_id=update.effective_chat.id).first()
    # Prefer bound movie context; otherwise fallback to temporary group context (72h)
    # NOTE: GroupMovieContext in DB uses fields: chat_id, detected_at, expires_at ...
    # So we **do not** instantiate it ad-hoc here (that caused "created_at invalid keyword" crashes).
    ctx_row = None
    if bound:
        # Year wajib
        if not bound.year:
            try:
                await msg.reply_text("⚠️ Movie year is missing for this group. Please set movie year first.")
            except Exception:
                pass
            return
        # Avoid duplicate re-import if assignments already exist
        existing = Assignment.query.filter_by(project=bound.code).first()
        if existing:
            return
    else:
        ctx_row = _ctx_get(int(update.effective_chat.id))
    if not ctx_row:
        # Helpful fallback: if the rolelist is sent as a REPLY to ...
        r = getattr(msg, "reply_to_message", None)
        if r:
            candidate = None
            if getattr(r, "document", None) and getattr(r.document, "file_name", None):
                candidate = r.document.file_name
            elif getattr(r, "audio", None) and getattr(r.audio, "file_name", None):
                candidate = r.audio.file_name
            else:
                # Video messages often don't carry file_name; fallback to caption.
                candidate = (getattr(r, "caption", None) or "").strip() or None
            if candidate:
                parsed = parse_movie_from_filename(candidate)
                if parsed:
                    # parse_movie_from_filename returns a dict
                    title = parsed.get("title")
                    year = parsed.get("year")
                    lang = parsed.get("lang") or detect_lang_from_filename(candidate)
                    if title and year:
                        _ctx_upsert(int(update.effective_chat.id), title, int(year), lang)
                    ctx_row = _ctx_get(int(update.effective_chat.id))
        # Nearest-latest fallback: use recently detected media context in this chat
        if not ctx_row:
            cached = _find_cached_candidate(context, int(update.effective_chat.id), now=datetime.utcnow())
            if cached:
                _ctx_upsert(
                    chat_id=int(update.effective_chat.id),
                    title=cached["title"],
                    year=str(cached["year"]),
                    lang=cached.get("lang") or DEFAULT_LANG,
                    file_name=cached.get("file_name"),
                    msg_id=cached.get("msg_id"),
                )
                ctx_row = _ctx_get(int(update.effective_chat.id))
        if not ctx_row:
            # year wajib: without context we cannot proceed
            try:
                await msg.reply_text(
                    "⚠️ Movie context not found. Please forward a file/video with filename containing *Title + Year* (e.g. `The Big Whoop 2025.mp4`) first (or reply to that message with the role list).",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
            return
    # Parse roles (accept extra tokens like character names)
    roles = parse_lines(text)
    if not roles:
        return
    project_key = f"{ctx_row.title} ({ctx_row.year}) [{_slug_lang(ctx_row.lang or DEFAULT_LANG)}]"
    suggestions = _suggest_assignments(project_key, roles)
    # Create pending request (one per message)
    expires_at = _now_utc() + timedelta(hours=GROUP_CTX_TTL_HOURS)
    req = GroupRoleImportRequest(
        tg_chat_id=int(update.effective_chat.id),
        tg_message_id=int(msg.message_id) if msg.message_id else None,
        title=ctx_row.title,
        year=ctx_row.year,
        lang=_slug_lang(ctx_row.lang or DEFAULT_LANG),
        roles_text=text,
        roles_json=json.dumps(roles),
        suggested_json=json.dumps(suggestions),
        status="PENDING",
        created_at=_now_utc(),
        expires_at=expires_at,
        requested_by_tg_id=int(update.effective_user.id) if update.effective_user else None,
        requested_by_name=tg_submitter_display(update),
    )
    db.session.add(req)
    db.session.commit()
    helper_ids: List[int] = []
    try:
        if getattr(getattr(msg, "reply_to_message", None), "document", None) and getattr(msg.reply_to_message.document, "file_name", None):
            ref_name = (msg.reply_to_message.document.file_name or "").strip().lower()
            if ref_name.endswith(".txt") and "role" in ref_name and getattr(msg.reply_to_message, "message_id", None):
                helper_ids.append(int(msg.reply_to_message.message_id))
    except Exception:
        pass
    try:
        helper_ids.extend(_claim_recent_chat_role_helper_ids(int(update.effective_chat.id), lookback_seconds=1800))
    except Exception:
        pass
    if helper_ids:
        _set_role_req_helper_ids(req.id, sorted({int(x) for x in helper_ids if int(x or 0)}))
    admin_review = _admin_import_review_text(req)
    kb = _import_review_keyboard(req.id)
    # Notify admin group (preferred) with full private review card
    if ADMIN_TELEGRAM_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=int(ADMIN_TELEGRAM_CHAT_ID),
                text=admin_review,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
        except Exception as e:
            detail["forward"] = {"enabled": True, "outbox_chat_id": int(SRT_OUTBOX_CHAT_ID), "forwarded": False, "error": str(e)}
    # Acknowledge in group without replying to the raw role message (keeps cleanup cleaner).
    try:
        ack = await context.bot.send_message(
            chat_id=int(update.effective_chat.id),
            text="🧠 Auto-detected roles. Waiting *admin approval*…",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
        _set_role_req_ack_message_id(req.id, int(getattr(ack, "message_id", 0) or 0))
    except Exception:
        pass
async def on_group_role_helper_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    handled = False
    try:
        handled = await _auto_import_role_helper_document(update, context)
    except Exception as e:
        try:
            await _notify_admin(
                context,
                "\n".join([
                    "❌ *Role helper auto-import failed*",
                    f"Chat: `{int(getattr(getattr(update, 'effective_chat', None), 'id', 0) or 0)}`",
                    f"Error: `{str(e)[:300]}`",
                ]),
            )
        except Exception:
            pass
        handled = True
    if handled:
        return

async def on_group_srt_to_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """When an .srt is uploaded/forwarded inside a group, create a Queue entry.
    Common workflow: VO group is bound to a movie code. Translator forwards SRT into the group.
    This handler creates a `TranslationSubmission` (READY_FOR_QA) + logs full detail.
    """
    msg = update.effective_message
    if not msg or not msg.document:
        return
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    fname = (msg.document.file_name or "")
    if not fname.lower().endswith(".srt"):
        return
    # Skip helper files
    low = fname.lower()
    if "role" in low or "censor" in low:
        return
    uid = int(getattr(getattr(update, "effective_user", None), "id", 0) or 0) or None
    submitter = tg_submitter_display(update)
    submitted_at = msg.date or _now_utc()
    detail: Dict[str, Any] = {
        "event": "group_srt_to_queue",
        "app_version": APP_VERSION,
        "ts_utc": _now_utc().strftime("%Y-%m-%d %H:%M:%S"),
        "telegram": {
            "chat_id": int(chat.id),
            "chat_type": getattr(chat, "type", None),
            "message_id": int(getattr(msg, "message_id", 0) or 0),
            "date": (msg.date.strftime("%Y-%m-%d %H:%M:%S") if getattr(msg, "date", None) else None),
            "user_id": uid,
            "username": getattr(getattr(update, "effective_user", None), "username", None),
            "full_name": getattr(getattr(update, "effective_user", None), "full_name", None),
        },
        "document": {
            "file_name": fname,
            "file_id": getattr(msg.document, "file_id", None),
            "file_unique_id": getattr(msg.document, "file_unique_id", None),
            "file_size": getattr(msg.document, "file_size", None),
            "mime_type": getattr(msg.document, "mime_type", None),
        },
        "movie": {},
        "result": {},
        "errors": [],
    }
    # Find bound movie (preferred) or cached context
    bound = Movie.query.filter_by(vo_group_chat_id=int(chat.id)).first()
    if not bound:
        ctx_row = GroupMovieContext.query.filter_by(tg_chat_id=int(chat.id)).first()
        if not ctx_row:
            return
        bound = upsert_movie(ctx_row.title, int(ctx_row.year), ctx_row.lang or DEFAULT_LANG)
        bound.vo_group_chat_id = int(chat.id)
        try:
            bound.vo_group_invite_link = (await context.bot.export_chat_invite_link(int(chat.id)))
        except Exception as e:
            detail["errors"].append(f"export_invite_link_error: {e}")
        db.session.commit()
    detail["movie"] = {
        "movie_id": int(bound.id) if bound else None,
        "code": bound.code if bound else None,
        "title": bound.title if bound else None,
        "year": bound.year if bound else None,
        "lang": bound.lang if bound else None,
    }
    # Dedup by tg chat+message id when possible
    try:
        existing = TranslationSubmission.query.filter_by(tg_chat_id=int(chat.id), tg_message_id=int(msg.message_id)).first()
        if existing:
            detail["result"] = {"deduped": True, "existing_submission_id": int(existing.id)}
            try:
                log_event("INFO", "tg.group_srt_queue", _human_group_srt_log(detail), traceback=json.dumps(detail, ensure_ascii=False, indent=2))
            except Exception:
                pass
            return
    except Exception:
        # Columns might not exist in older DB; ignore.
        existing = None
    # Create queue row
    sub = TranslationSubmission(
        movie=bound.code,
        movie_id=bound.id,
        status="READY_FOR_QA",
        submitted_at=submitted_at,
        submitter_id=uid,
        submitter_username=submitter,
        content_type="document",
        text=None,
        file_id=msg.document.file_id,
        file_name=fname,
        telegram_event_id=int(getattr(msg, "message_id", 0) or 0) or None,
    )
    # Optional trace fields (safe if columns exist)
    try:
        sub.tg_chat_id = int(chat.id)
        sub.tg_message_id = int(getattr(msg, "message_id", 0) or 0) or None
    except Exception:
        pass
    db.session.add(sub)
    # Sync movie status (keeps dashboard accurate)
    try:
        bound.status = "READY_FOR_QA"
        bound.submitted_at = bound.submitted_at or submitted_at
        bound.updated_at = _now_utc()
    except Exception:
        pass
    db.session.commit()
    detail["result"] = {
        "deduped": False,
        "submission_id": int(sub.id) if getattr(sub, "id", None) else None,
        "movie_status": getattr(bound, "status", None),
    }
    # DB-backed log
    try:
        log_event("INFO", "tg.group_srt_queue", _human_group_srt_log(detail), traceback=json.dumps(detail, ensure_ascii=False, indent=2))
    except Exception:
        pass
    # Optional: acknowledgement
    try:
        await msg.reply_text("✅ Added to Queue (READY_FOR_QA)")
    except Exception:
        pass
async def on_import_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve/reject Option A import request."""
    q = update.callback_query
    if not q:
        return
    try:
        await q.answer()
    except Exception:
        pass
    parts = (q.data or "").split("|")
    if len(parts) < 3:
        return
    _, action, sid, *rest = parts
    mode = _normalize_priority_mode(rest[0] if rest else 'urgent')
    try:
        rid = int(sid)
    except Exception:
        return
    # admin/owner only
    if not _is_admin(update):
        try:
            await q.edit_message_text("❌ Not allowed")
        except Exception as e:
            detail["forward"] = {"enabled": True, "outbox_chat_id": int(SRT_OUTBOX_CHAT_ID), "forwarded": False, "error": str(e)}
        return
    req = GroupRoleImportRequest.query.filter_by(id=rid).first()
    if not req:
        try:
            await q.edit_message_text("Not found")
        except Exception as e:
            detail["forward"] = {"enabled": True, "outbox_chat_id": int(SRT_OUTBOX_CHAT_ID), "forwarded": False, "error": str(e)}
        return
    if req.status != "PENDING":
        try:
            await q.edit_message_text(f"Already {req.status}")
        except Exception as e:
            detail["forward"] = {"enabled": True, "outbox_chat_id": int(SRT_OUTBOX_CHAT_ID), "forwarded": False, "error": str(e)}
        return
    if req.expires_at and req.expires_at < _now_utc():
        req.status = "EXPIRED"
        db.session.commit()
        try:
            await q.edit_message_text("⏳ Request expired")
        except Exception as e:
            detail["forward"] = {"enabled": True, "outbox_chat_id": int(SRT_OUTBOX_CHAT_ID), "forwarded": False, "error": str(e)}
        return
    if action == "show":
        try:
            await q.edit_message_text(_admin_import_review_text(req), reply_markup=_import_review_keyboard(req.id), disable_web_page_preview=True)
        except Exception:
            pass
        return
    if action == "refresh":
        try:
            _refresh_role_import_request(req, commit=True)
            await q.edit_message_text(_admin_import_review_text(req), reply_markup=_import_review_keyboard(req.id), disable_web_page_preview=True)
        except Exception as e:
            try:
                await q.edit_message_text(f'❌ Refresh failed: {str(e)[:300]}', disable_web_page_preview=True)
            except Exception:
                pass
        return
    if action == "preview":
        try:
            await q.edit_message_text(
                _admin_import_mode_preview_text(req, mode),
                reply_markup=_import_mode_preview_keyboard(req.id, mode),
                disable_web_page_preview=True,
            )
        except Exception:
            pass
        return
    if action == "reject":
        req.status = "REJECTED"
        req.reviewed_by_tg_id = int(q.from_user.id) if q.from_user else None
        req.reviewed_by_name = q.from_user.full_name if q.from_user else None
        req.reviewed_at = _now_utc()
        db.session.commit()
        try:
            await q.edit_message_text(
                "\n".join([
                    '❌ Role import rejected',
                    f'Request ID: {req.id}',
                    f'Movie: {fmt_title_year(req.title, req.year)}',
                    f'Reviewed by: {req.reviewed_by_name or req.reviewed_by_tg_id or "-"}',
                ]),
                disable_web_page_preview=True,
            )
        except Exception as e:
            detail["forward"] = {"enabled": True, "outbox_chat_id": int(SRT_OUTBOX_CHAT_ID), "forwarded": False, "error": str(e)}
        return
    if action != "approve":
        return
    # APPROVE: create/bind movie + create assignments (queue is created only when SRT arrives; user chose C)
    movie = upsert_movie(req.title, int(req.year), req.lang or DEFAULT_LANG)
    movie.vo_group_chat_id = int(req.tg_chat_id)
    movie.updated_at = _now_utc()
    db.session.commit()
    # Create assignments using selected deadline mode
    try:
        roles: List[Tuple[str, int]] = json.loads(req.roles_json or "[]")
    except Exception:
        roles = parse_lines(req.roles_text)
    suggestions = _auto_assign_movie_roles(
        movie,
        roles,
        urgent=_priority_mode_urgent_only(mode),
        replace_existing=True,
        priority_mode=mode,
    )
    db.session.commit()
    req.status = "APPROVED"
    req.reviewed_by_tg_id = int(q.from_user.id) if q.from_user else None
    req.reviewed_by_name = q.from_user.full_name if q.from_user else None
    req.reviewed_at = _now_utc()
    db.session.commit()
    # Update admin preview message
    try:
        await q.edit_message_text(
            "\n".join([
                '✅ Role import approved',
                f'Movie: {fmt_title_year(movie.title, movie.year)} [{movie.code}]',
                f'Group chat: {req.tg_chat_id}',
                f'Mode: {_priority_mode_label(mode)} ({_priority_mode_hours(mode)}h)',
                f'Assignments created: {len(suggestions)}',
                f'Reviewed by: {req.reviewed_by_name or req.reviewed_by_tg_id or "-"}',
                'Public VO card will be posted to the bound group.',
            ]),
            disable_web_page_preview=True,
        )
    except Exception:
        pass
    # Keep group/public clean: delete raw role text, helper role file(s), and waiting notice; then post/update the public VO card.
    try:
        await _cleanup_role_import_group_noise(context, req)
    except Exception:
        pass
    try:
        await _upsert_public_assignment_card(context, movie, pin=True)
    except Exception:
        pass
# -----------------------------
# VO submission detector (Group) → auto WAIT_EMBED
# -----------------------------
def _parse_role_and_lines(text: str) -> Tuple[Optional[str], int]:
    """Find role (man/fem) and optional lines in caption.
    Example:
      BN-... man-1 120
      role: fem2 lines: 98
    """
    t = (text or "").strip().lower()
    role = None
    lines = 0
    # Find first role token anywhere in the text (avoid grabbing digits from movie code).
    m = re.search(r"\b(man|fem|male|female|m|f)\s*[-_ ]?\s*(\d{1,2})\b", t)
    if m:
        prefix = "man" if m.group(1) in ("man", "male", "m") else "fem"
        role = f"{prefix}{int(m.group(2))}"
        tail = t[m.end():]
        m2 = re.search(r"\b(\d+)\b", tail)
        if m2:
            try:
                lines = int(m2.group(1))
            except Exception:
                lines = 0
        return role, lines
    # Whole text might just be a role token.
    role = norm_role(t)
    return role, 0
async def on_vo_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_group(update):
        return
    msg = update.effective_message
    if not msg:
        return
    # Only treat media posts (document/audio/voice/video)
    has_media = bool(msg.document or msg.audio or msg.voice or msg.video)
    if not has_media:
        return
    text = (msg.caption or msg.text or "").strip()
    movie_code = _extract_movie_code(text) if text else None
    if not movie_code:
        # Fallback: if this group is bound to a movie, allow captions without code
        bound = Movie.query.filter_by(vo_group_chat_id=update.effective_chat.id).first()
        if not bound:
            return
        movie_code = bound.code
    # Capture media trace for optional archive/audit
    media_type = None
    file_id = None
    file_name = None
    if msg.document:
        media_type = "document"
        file_id = msg.document.file_id
        file_name = msg.document.file_name
    elif msg.audio:
        media_type = "audio"
        file_id = msg.audio.file_id
        file_name = msg.audio.file_name
    elif msg.voice:
        media_type = "voice"
        file_id = msg.voice.file_id
    elif msg.video:
        media_type = "video"
        file_id = msg.video.file_id
    if not text and not file_name:
        return
    uid = int(getattr(getattr(update, "effective_user", None), "id", 0) or 0) or None
    detail: Dict[str, Any] = {
        "event": "vo_submission",
        "app_version": APP_VERSION,
        "ts_utc": _now_utc().strftime("%Y-%m-%d %H:%M:%S"),
        "telegram": {
            "chat_id": int(getattr(getattr(update, "effective_chat", None), "id", 0) or 0),
            "message_id": int(getattr(msg, "message_id", 0) or 0),
            "date": (msg.date.strftime("%Y-%m-%d %H:%M:%S") if getattr(msg, "date", None) else None),
            "user_id": uid,
            "username": getattr(getattr(update, "effective_user", None), "username", None),
            "full_name": getattr(getattr(update, "effective_user", None), "full_name", None),
        },
        "movie": {"code": movie_code},
        "media": {"media_type": media_type, "file_id": file_id, "file_name": file_name},
        "caption": (text or "")[:2048],
        "detect": {},
        "result": {},
        "errors": [],
    }
    # -----------------------------
    # Role detection
    # -----------------------------
    detect_method = None
    # Allow multi-role captions (e.g. zip uploads with multiple roles).
    text_wo_code = text
    if movie_code:
        try:
            text_wo_code = re.sub(re.escape(movie_code), " ", text_wo_code, flags=re.I)
        except Exception:
            pass
    parsed_roles = parse_lines(text_wo_code)
    roles_to_save: List[Tuple[str, int]] = parsed_roles[:] if parsed_roles else []
    if roles_to_save:
        detect_method = "parse_lines"
    if not roles_to_save:
        # Single-role caption fallback
        role, lines = _parse_role_and_lines(text_wo_code or text)
        if role:
            roles_to_save = [(role, int(lines or 0))]
            detect_method = "single_role_caption"
        else:
            # Fallback: infer from file name
            role2, lines2 = _parse_role_and_lines(file_name or "")
            if role2:
                roles_to_save = [(role2, int(lines2 or 0))]
                detect_method = "filename_fallback"
    # If still no role, try extract from ZIP archive contents.
    zip_found = []
    if not roles_to_save and media_type == "document" and (file_name or "").lower().endswith(".zip") and file_id:
        try:
            import zipfile
            import tempfile
            tmp_path = os.path.join(tempfile.gettempdir(), f"tg_{movie_code}_{msg.message_id}.zip")
            tg_file = await context.bot.get_file(file_id)
            await tg_file.download_to_drive(custom_path=tmp_path)
            found: List[Tuple[str, int]] = []
            seen = set()
            with zipfile.ZipFile(tmp_path, "r") as z:
                names = z.namelist()
                detail["detect"]["zip_names_count"] = len(names)
                # keep small preview only
                detail["detect"]["zip_names_preview"] = names[:50]
                for n in names:
                    for m in re.finditer(r"(?i)(man|male|m|fem|female|f)[-_ ]?\d{1,2}", n):
                        r = norm_role(m.group(0))
                        if r and r not in seen:
                            seen.add(r)
                            found.append((r, 0))
            roles_to_save = found
            zip_found = found[:]
            if roles_to_save:
                detect_method = "zip_scan"
        except Exception as e:
            detail["errors"].append(f"zip_scan_error: {e}")
            roles_to_save = []
        finally:
            try:
                if 'tmp_path' in locals() and os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
    detail["detect"].update(
        {
            "detect_method": detect_method,
            "roles_parsed": [(r, int(l or 0)) for r, l in roles_to_save][:80],
        }
    )
    if not roles_to_save:
        return
    movie = movie_by_code(movie_code)
    if not movie:
        detail["errors"].append("movie_not_found_for_code")
        try:
            log_event("WARN", "tg.vo_submission", _human_vo_submission_log(detail), traceback=json.dumps(detail, ensure_ascii=False, indent=2))
        except Exception:
            pass
        return
    detail["movie"].update(
        {
            "movie_id": int(movie.id),
            "title": movie.title,
            "year": movie.year,
            "lang": movie.lang,
            "status": movie.status,
        }
    )
    # best-effort: link VO Telegram ID to roster for reminders
    _upsert_vo_seen(update)
    user = update.effective_user
    vo_name = (user.full_name or user.username or str(user.id)).strip()
    # Save each role bucket (dedupe: same movie+role+vo within last 24h)
    since = _now_utc() - timedelta(hours=24)
    saved_roles: List[Dict[str, Any]] = []
    skipped_dupe = 0
    for role, lines in roles_to_save:
        exists = (
            VORoleSubmission.query.filter_by(movie=movie_code, role=role, vo=vo_name)
            .filter(VORoleSubmission.submitted_at >= since)
            .first()
        )
        if exists:
            skipped_dupe += 1
            continue
        row = VORoleSubmission(
            movie=movie_code,
            vo=vo_name,
            role=role,
            lines=int(lines or 0),
            tg_chat_id=int(getattr(update.effective_chat, "id", 0) or 0) or None,
            tg_message_id=int(getattr(msg, "message_id", 0) or 0) or None,
            media_type=media_type,
            file_id=file_id,
            file_name=file_name,
        )
        db.session.add(row)
        try:
            db.session.flush()
        except Exception:
            pass
        saved_roles.append({"id": getattr(row, "id", None), "role": role, "lines": int(lines or 0)})
    if not saved_roles:
        detail["result"] = {"saved_count": 0, "skipped_dupe_count": skipped_dupe, "roles_count": len(roles_to_save), "detect_method": detect_method}
        try:
            log_event("INFO", "tg.vo_submission", _human_vo_submission_log(detail), traceback=json.dumps(detail, ensure_ascii=False, indent=2))
        except Exception:
            pass
        return
    db.session.commit()
    expected = _expected_roles_for_movie(movie)
    submitted = _submitted_roles_for_movie(movie_code)
    wait_embed_triggered = False
    if expected and expected.issubset(submitted):
        if movie.status != "WAIT_EMBED":
            movie.status = "WAIT_EMBED"
            movie.updated_at = _now_utc()
            db.session.commit()
            wait_embed_triggered = True
            await _notify_admin(
                context,
                "\n".join(
                    [
                        "🧩 *WAIT EMBED reached*",
                        f"Movie: `{movie_code}`",
                        f"Roles completed: `{len(submitted)}/{len(expected)}`",
                    ]
                ),
            )
            await _try_update_movie_card(context, movie)
    detail["result"] = {
        "detect_method": detect_method,
        "roles_count": len(roles_to_save),
        "saved_count": len(saved_roles),
        "skipped_dupe_count": skipped_dupe,
        "saved_roles": saved_roles,
        "wait_embed_triggered": bool(wait_embed_triggered),
        "movie_status": movie.status,
        "expected_roles": len(expected) if expected else 0,
        "submitted_roles": len(submitted) if submitted else 0,
    }
    # DB-backed log
    try:
        log_event("INFO", "tg.vo_submission", _human_vo_submission_log(detail), traceback=json.dumps(detail, ensure_ascii=False, indent=2))
    except Exception:
        pass
    try:
        await _upsert_public_assignment_card(context, movie, pin=False)
    except Exception:
        pass
# -----------------------------
# Message capture for bulk role list
# -----------------------------
async def capture_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text:
        return
    # Bulk role capture in group
    if _is_group(update):
        state = BULK_ASSIGN.get(update.effective_chat.id)
        if state and not msg.text.startswith("/"):
            state["text"] += msg.text + "\n"
        return
def build_bot(token: str) -> Application:
    app = Application.builder().token(token).build()
    # Catch exceptions (prevents "No error handlers are registered" spam)
    app.add_error_handler(on_error)
    # base
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("panel", cmd_panel))
    app.add_handler(CommandHandler("menu", cmd_panel))
    app.add_handler(CommandHandler("version", cmd_version))
    app.add_handler(CommandHandler("me", cmd_me))
    # admin whitelist
    app.add_handler(CommandHandler("admin_add", cmd_admin_add))
    app.add_handler(CommandHandler("admin_remove", cmd_admin_remove))
    app.add_handler(CommandHandler("admin_list", cmd_admin_list))
    # backups
    app.add_handler(CommandHandler("backup_here", cmd_backup_here))
    app.add_handler(CommandHandler("backup_status", cmd_backup_status))
    app.add_handler(CommandHandler("backup_now", cmd_backup_now))
    # movie ops
    app.add_handler(CommandHandler("create_movie", cmd_create_movie))
    app.add_handler(CommandHandler("create_project", cmd_create_project))
    app.add_handler(CommandHandler("project", cmd_create_project))
    app.add_handler(CommandHandler("project_wizard", cmd_project_wizard))
    app.add_handler(CommandHandler("new_project", cmd_project_wizard))
    app.add_handler(CommandHandler("project_cancel", cmd_project_cancel))
    app.add_handler(CommandHandler("find_movie", cmd_find_movie))
    app.add_handler(CommandHandler("aliases", cmd_aliases))
    app.add_handler(CommandHandler("resolve_movie", cmd_resolve_movie))
    app.add_handler(CommandHandler("group_context", cmd_group_context))
    app.add_handler(CommandHandler("clear_group_context", cmd_clear_group_context))
    app.add_handler(CommandHandler("add_alias", cmd_add_alias))
    app.add_handler(CommandHandler("delete_alias", cmd_delete_alias))
    app.add_handler(CommandHandler("repair_titles", cmd_repair_titles))
    app.add_handler(CommandHandler("repair_movie_title", cmd_repair_movie_title))
    app.add_handler(CommandHandler("archived", cmd_archived))
    app.add_handler(CommandHandler("unarchive_movie", cmd_unarchive_movie))
    app.add_handler(CommandHandler("bulk_archive", cmd_bulk_archive))
    app.add_handler(CommandHandler("bulk_unarchive", cmd_bulk_unarchive))
    app.add_handler(CommandHandler("cleanup_presets", cmd_cleanup_presets))
    app.add_handler(CommandHandler("pending_roles", cmd_pending_roles))
    app.add_handler(CommandHandler("review_roles", cmd_review_roles))
    app.add_handler(CommandHandler("refresh_role_import", cmd_refresh_role_import))
    app.add_handler(CommandHandler("duplicates", cmd_duplicates))
    app.add_handler(CommandHandler("merge_simulate", cmd_merge_simulate))
    app.add_handler(CommandHandler("merge_movie", cmd_merge_movie))
    app.add_handler(CommandHandler("stale_movies", cmd_stale_movies))
    app.add_handler(CommandHandler("bulk_archive_stale", cmd_bulk_archive_stale))
    app.add_handler(CommandHandler("movie_history", cmd_movie_history))
    app.add_handler(CommandHandler("activity", cmd_activity))
    app.add_handler(CommandHandler("movie", cmd_movie))
    app.add_handler(CommandHandler("rename_movie", cmd_rename_movie))
    app.add_handler(CommandHandler("set_movie", cmd_rename_movie))
    app.add_handler(CommandHandler("assign_translator", cmd_assign_translator))
    app.add_handler(CommandHandler("suggest_translator", cmd_suggest_translator))
    app.add_handler(CommandHandler("reassign_vo", cmd_reassign_vo))
    app.add_handler(CommandHandler("suggest_vo", cmd_suggest_vo))
    app.add_handler(CommandHandler("movie_workload", cmd_movie_workload))
    app.add_handler(CommandHandler("deadlines", cmd_deadlines))
    app.add_handler(CommandHandler("deadline_tr", cmd_deadline_tr))
    app.add_handler(CommandHandler("deadline_vo", cmd_deadline_vo))
    app.add_handler(CommandHandler("remind_tr", cmd_remind_tr))
    app.add_handler(CommandHandler("remind_vo", cmd_remind_vo))
    app.add_handler(CommandHandler("remind_overdue", cmd_remind_overdue))
    app.add_handler(CommandHandler("priority", cmd_priority))
    app.add_handler(CommandHandler("priority_movies", cmd_priority))
    app.add_handler(CommandHandler("summary_today", cmd_summary_today))
    app.add_handler(CommandHandler("daily_summary", cmd_summary_today))
    app.add_handler(CommandHandler("digest_here", cmd_digest_here))
    app.add_handler(CommandHandler("digest_status", cmd_digest_status))
    app.add_handler(CommandHandler("digest_on", cmd_digest_on))
    app.add_handler(CommandHandler("digest_off", cmd_digest_off))
    app.add_handler(CommandHandler("digest_now", cmd_digest_now))
    app.add_handler(CommandHandler("undo_last", cmd_undo_last))
    app.add_handler(CommandHandler("overdue", cmd_overdue))
    app.add_handler(CommandHandler("late", cmd_overdue))
    app.add_handler(CommandHandler("progress", cmd_progress))
    app.add_handler(CommandHandler("who_has", cmd_who_has))
    app.add_handler(CommandHandler("workload", cmd_workload))
    app.add_handler(CommandHandler("vo_stats", cmd_vo_stats))
    app.add_handler(CommandHandler("request_group", cmd_request_group))
    app.add_handler(CommandHandler("bind", cmd_bind))
    app.add_handler(CommandHandler("group_reject", cmd_group_reject))
    app.add_handler(CallbackQueryHandler(on_group_request_callback, pattern=r"^grp\|"))
    app.add_handler(CallbackQueryHandler(callback_router, pattern=r"^(mv|bulk|bm|wiz|panel)\|"))
    # bulk assign (group)
    app.add_handler(CommandHandler("bulk_assign", cmd_bulk_assign))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("clear_movie", cmd_clear_movie))
    # self-service
    app.add_handler(CommandHandler("my_tasks", cmd_my_tasks))
    app.add_handler(CommandHandler("my_roles", cmd_my_roles))
    # Translator DM: upload .srt → auto queue + auto forward
    app.add_handler(MessageHandler(filters.Document.ALL & filters.ChatType.PRIVATE, on_dm_srt_auto_forward))
    # Option A: Group auto-detect from filenames + role list (no movie code)
    # In python-telegram-bot v20+, ChatType.GROUPS already matches both
    # normal groups and supergroups. There is no ChatType.SUPERGROUPS.
    group_filter = filters.ChatType.GROUPS
    app.add_handler(MessageHandler((filters.Document.ALL | filters.AUDIO | filters.VIDEO) & group_filter, on_group_media_context))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND) & group_filter, on_group_rolelist_autodetect))
    app.add_handler(MessageHandler(filters.Document.ALL & group_filter, on_group_role_helper_document))
    # Group: .srt dropped into VO group -> auto create Queue item (ready for QA)
    app.add_handler(MessageHandler(filters.Document.ALL & group_filter, on_group_srt_to_queue))
    app.add_handler(CallbackQueryHandler(on_import_request_callback, pattern=r"^imp\|"))
    # VO detector (group media)
    app.add_handler(MessageHandler((filters.Document.ALL | filters.AUDIO | filters.VOICE | filters.VIDEO), on_vo_submission))
    # translator queue automation (DM)
    app.add_handler(CommandHandler("submit", cmd_submit))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND) & filters.ChatType.PRIVATE, on_dm_submission))
    # admin auto-detect paste (text)
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), on_text_autodetect))
    # capture bulk text
    app.add_handler(MessageHandler(filters.TEXT, capture_text))
    return app
# --------------------------------------------------
# Web-triggered ops (called from Flask /ops/run)
# --------------------------------------------------
async def ops_request_group(bot, movie_code: str, actor: str = "web") -> None:
    """Create a GroupOpenRequest and notify admin chat.
    This mirrors /request_group but is callable from the web UI.
    """
    movie_code = (movie_code or "").strip().upper()
    if not movie_code:
        raise ValueError("movie_code required")
    m = Movie.query.filter_by(code=movie_code).first()
    if not m:
        raise ValueError(f"Movie not found: {movie_code}")
    req = GroupOpenRequest.query.filter_by(movie_id=m.id, movie_code=movie_code, status="PENDING").first()
    if not req:
        req = GroupOpenRequest(
            movie_id=m.id,
            movie_code=movie_code,
            requested_by_tg_id=0,
            requested_by_name=actor,
        )
        db.session.add(req)
        db.session.commit()
    log_event("INFO", "ops.request_group", f"{actor}: request_group {movie_code} req_id={req.id}")
    if not ADMIN_TELEGRAM_CHAT_ID:
        return
    kb = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Approve", callback_data=f"grp|approve|{req.id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"grp|reject|{req.id}"),
        ]]
    )
    title = f"{m.title} ({m.year or '?'})"
    await bot.send_message(
        chat_id=int(ADMIN_TELEGRAM_CHAT_ID),
        text=(
            "🆕 VO Group Request\n"
            f"• Code: {movie_code}\n"
            f"• Movie: {title}\n"
            f"• Lang: {m.lang or '-'}\n"
            f"• Request ID: {req.id}\n"
            "\nApprove to allow manual group creation + /bind."
        ),
        reply_markup=kb,
        disable_web_page_preview=True,
    )
async def ops_approve_request(bot, request_id: int, actor: str = "web") -> None:
    req = GroupOpenRequest.query.filter_by(id=int(request_id)).first()
    if not req:
        raise ValueError(f"Request not found: {request_id}")
    if req.status != "PENDING":
        return
    m = Movie.query.filter_by(id=req.movie_id).first()
    if not m:
        raise ValueError("Movie missing for request")
    req.status = "APPROVED"
    req.reviewed_by_tg_id = 0
    req.reviewed_by_name = actor
    req.reviewed_at = _now_utc()
    db.session.commit()
    log_event("INFO", "ops.approve", f"{actor}: approve request_id={request_id} code={req.movie_code}")
    if not ADMIN_TELEGRAM_CHAT_ID:
        return
    group_name = GROUP_TITLE_TEMPLATE.format(
        code=req.movie_code,
        title=m.title,
        year=m.year or "?",
        lang=lang_display(m.lang or DEFAULT_LANG),
    )
    await bot.send_message(
        chat_id=int(ADMIN_TELEGRAM_CHAT_ID),
        text=(
            "✅ Approved (Manual Group Flow A)\n"
            f"1) Create a new group named:\n{group_name}\n\n"
            "2) Add OWNER/Admins + add this bot as ADMIN\n"
            f"3) In that group run: /bind {req.movie_code}\n\n"
            "Bot will then DM invite link to admins (best-effort) and auto-post assignments." 
        ),
        disable_web_page_preview=True,
    )
async def ops_reject_request(bot, request_id: int, note: str, actor: str = "web") -> None:
    req = GroupOpenRequest.query.filter_by(id=int(request_id)).first()
    if not req:
        raise ValueError(f"Request not found: {request_id}")
    if req.status != "PENDING":
        return
    req.status = "REJECTED"
    req.note = (note or "").strip()[:2000]
    req.reviewed_by_tg_id = 0
    req.reviewed_by_name = actor
    req.reviewed_at = _now_utc()
    db.session.commit()
    log_event("WARN", "ops.reject", f"{actor}: reject request_id={request_id} code={req.movie_code} note={req.note}")
    if ADMIN_TELEGRAM_CHAT_ID:
        # Show full movie title instead of internal code
        m = Movie.query.filter_by(code=req.movie_code).first()
        full_title = (
            f"{m.title} ({m.year})" if (m and m.title and m.year) else (m.title if (m and m.title) else req.movie_code)
        )
        await bot.send_message(
            chat_id=int(ADMIN_TELEGRAM_CHAT_ID),
            text=f"❌ Rejected VO group request {request_id} for {full_title}\nNote: {req.note}",
        )
async def ops_post_assignments(bot, movie_code: str, actor: str = "web") -> None:
    """Post current clean public VO assignment card into the bound group."""
    code = (movie_code or "").strip().upper()
    m = Movie.query.filter_by(code=code).first()
    if not m:
        raise ValueError(f"Movie not found: {code}")
    if not m.vo_group_chat_id:
        raise ValueError(f"Movie not bound to a VO group yet. Run /bind in the group.")
    rows = Assignment.query.filter_by(project=code).order_by(Assignment.role.asc()).all()
    if not rows:
        raise ValueError(f"No assignments found for {code}. Create assignments in Web first.")
    log_event("INFO", "ops.post_assignments", f"{actor}: post_assignments {code} -> chat {m.vo_group_chat_id}")
    text = _vo_public_card_text(m)
    old_chat_id, old_msg_id = _public_assignment_card_ref(m)
    sent = None
    if old_chat_id and old_msg_id:
        try:
            await bot.edit_message_text(chat_id=int(old_chat_id), message_id=int(old_msg_id), text=text, disable_web_page_preview=True)
            return
        except Exception:
            sent = None
    sent = await bot.send_message(chat_id=int(m.vo_group_chat_id), text=text, disable_web_page_preview=True)
    _set_public_assignment_card_ref(m, sent.chat_id, sent.message_id)
    try:
        await bot.pin_chat_message(chat_id=int(m.vo_group_chat_id), message_id=sent.message_id, disable_notification=True)
    except Exception:
        pass