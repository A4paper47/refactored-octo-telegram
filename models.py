from datetime import datetime
from db import db


class AdminUser(db.Model):
    __tablename__ = "admin_user"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AdminTelegramUser(db.Model):
    __tablename__ = "admin_telegram_user"
    id = db.Column(db.Integer, primary_key=True)
    tg_user_id = db.Column(db.BigInteger, unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(120), nullable=True)
    role = db.Column(db.String(20), nullable=False, default="ADMIN")  # OWNER/ADMIN
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Translator(db.Model):
    __tablename__ = "translator"
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(120), unique=True, nullable=False, index=True)

    # Optional Telegram identity
    tg_user_id = db.Column(db.BigInteger, unique=True, nullable=True, index=True)
    tg_username = db.Column(db.String(120), nullable=True, index=True)  # without "@"

    active = db.Column(db.Boolean, default=True)

    # Optional metadata
    languages = db.Column(db.String(80), nullable=True)  # e.g. "bn,ms"
    note = db.Column(db.Text, nullable=True)

    last_seen_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)



class Movie(db.Model):
    __tablename__ = "movie"
    id = db.Column(db.Integer, primary_key=True)
    # NOTE: Some deployed DBs historically used column name `movie_code` (often NOT NULL)
    # while earlier versions used `code`. Map our `code` attribute to `movie_code` so
    # inserts/queries work against either schema.
    code = db.Column("movie_code", db.String(40), unique=True, nullable=True, index=True)
    title = db.Column(db.String(255), nullable=False)
    year = db.Column(db.String(10), nullable=True)
    lang = db.Column(db.String(30), nullable=True)
    status = db.Column(db.String(40), nullable=False, default="NEW")
    is_archived = db.Column(db.Boolean, nullable=False, default=False, index=True)
    archived_at = db.Column(db.DateTime, nullable=True)

    translator_assigned = db.Column(db.String(120), nullable=True)

    movie_card_chat_id = db.Column(db.BigInteger, nullable=True)
    movie_card_message_id = db.Column(db.BigInteger, nullable=True)

    # VO group binding (manual create + bot setup)
    vo_group_chat_id = db.Column(db.BigInteger, nullable=True, index=True)
    vo_group_invite_link = db.Column(db.Text, nullable=True)

    received_at = db.Column(db.DateTime, nullable=True)
    submitted_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MovieAlias(db.Model):
    __tablename__ = "movie_alias"
    id = db.Column(db.Integer, primary_key=True)
    movie_id = db.Column(db.Integer, nullable=False, index=True)
    alias = db.Column(db.String(255), nullable=False)
    alias_norm = db.Column(db.String(255), nullable=False, index=True)
    year = db.Column(db.String(10), nullable=True, index=True)
    lang = db.Column(db.String(30), nullable=True, index=True)
    source = db.Column(db.String(40), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)



class GroupOpenRequest(db.Model):
    __tablename__ = "group_open_request"
    id = db.Column(db.Integer, primary_key=True)
    movie_id = db.Column(db.Integer, nullable=False, index=True)
    movie_code = db.Column(db.String(40), nullable=False, index=True)

    requested_by_tg_id = db.Column(db.BigInteger, nullable=False)
    requested_by_name = db.Column(db.String(120), nullable=True)
    requested_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    status = db.Column(db.String(20), nullable=False, default="PENDING")  # PENDING/APPROVED/REJECTED

    reviewed_by_tg_id = db.Column(db.BigInteger, nullable=True)
    reviewed_by_name = db.Column(db.String(120), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    note = db.Column(db.Text, nullable=True)


class Assignment(db.Model):
    __tablename__ = "assignment"
    id = db.Column(db.Integer, primary_key=True)

    # project/movie name used by VO assignment flow
    project = db.Column(db.String(200), nullable=False, index=True)

    # optional link to Movie table (added via migrate)
    movie_id = db.Column(db.Integer, nullable=True, index=True)

    vo = db.Column(db.String(120), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False, index=True)  # man1/fem2
    lines = db.Column(db.Integer, nullable=False, default=0)
    urgent = db.Column(db.Boolean, default=True)
    priority_mode = db.Column(db.String(20), nullable=True, index=True)

    # Optional deadline (UTC, stored as naive datetime like the rest of the app).
    # If NULL -> NOT overdue (deadline-only overdue rule).
    deadline_at = db.Column(db.DateTime, nullable=True, index=True)

    # Reminder tracking (best-effort)
    last_reminded_at = db.Column(db.DateTime, nullable=True)
    remind_count = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class VOTeam(db.Model):
    __tablename__ = "vo_team"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False, index=True)
    gender = db.Column(db.String(10), nullable=False)  # male/female
    level = db.Column(db.String(20), nullable=False, default="trained_new")  # expert_old/trained_new/new_limited
    speed = db.Column(db.String(10), nullable=False, default="normal")  # normal/slow
    urgent_ok = db.Column(db.Boolean, default=True)
    active = db.Column(db.Boolean, default=True)

    # Optional Telegram identity for DM reminders
    tg_user_id = db.Column(db.BigInteger, unique=True, nullable=True, index=True)
    tg_username = db.Column(db.String(120), nullable=True, index=True)  # without "@"
    last_seen_at = db.Column(db.DateTime, nullable=True)


class TranslationTask(db.Model):
    """Translator workload tracking.

    Replaces heuristics that used Movie.translator_assigned + Movie.status.
    """

    __tablename__ = "translation_task"

    id = db.Column(db.Integer, primary_key=True)

    movie_id = db.Column(db.Integer, nullable=True, index=True)
    movie_code = db.Column(db.String(40), nullable=True, index=True)
    title = db.Column(db.String(255), nullable=True)
    year = db.Column(db.String(10), nullable=True)
    lang = db.Column(db.String(30), nullable=True)

    translator_id = db.Column(db.Integer, nullable=True, index=True)
    translator_name = db.Column(db.String(120), nullable=True, index=True)

    # NEW -> created, SENT -> assigned to translator, COMPLETED -> SRT received
    status = db.Column(db.String(20), nullable=False, default="SENT", index=True)

    priority_mode = db.Column(db.String(20), nullable=True, index=True)

    # Optional deadline (UTC). If NULL -> NOT overdue.
    deadline_at = db.Column(db.DateTime, nullable=True, index=True)

    sent_at = db.Column(db.DateTime, nullable=True, index=True)
    completed_at = db.Column(db.DateTime, nullable=True, index=True)

    # Reminder tracking
    last_reminded_at = db.Column(db.DateTime, nullable=True)
    remind_count = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TranslationSubmission(db.Model):
    __tablename__ = "translation_submission"
    id = db.Column(db.Integer, primary_key=True)

    movie = db.Column(db.String(200), nullable=False, index=True)
    movie_id = db.Column(db.Integer, nullable=True, index=True)

    status = db.Column(db.String(30), nullable=False, default="READY_FOR_QA")
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    submitter_id = db.Column(db.BigInteger, nullable=True, index=True)
    submitter_username = db.Column(db.String(120), nullable=True)

    content_type = db.Column(db.String(20), nullable=False)  # text/document
    text = db.Column(db.Text, nullable=True)
    file_id = db.Column(db.String(200), nullable=True)
    file_name = db.Column(db.String(255), nullable=True)

    drop_chat_id = db.Column(db.BigInteger, nullable=True)
    drop_message_id = db.Column(db.BigInteger, nullable=True)

    telegram_event_id = db.Column(db.BigInteger, nullable=True)

    # Optional trace back to Telegram group message (for group-forwarded submissions)
    tg_chat_id = db.Column(db.BigInteger, nullable=True)
    tg_message_id = db.Column(db.BigInteger, nullable=True)

    note = db.Column(db.Text, nullable=True)


class VORoleSubmission(db.Model):
    __tablename__ = "vo_role_submission"
    id = db.Column(db.Integer, primary_key=True)
    movie = db.Column(db.String(200), nullable=False, index=True)
    vo = db.Column(db.String(120), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False, index=True)
    lines = db.Column(db.Integer, nullable=False, default=0)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Optional trace back to Telegram message for archiving/auditing
    tg_chat_id = db.Column(db.BigInteger, nullable=True)
    tg_message_id = db.Column(db.BigInteger, nullable=True)
    media_type = db.Column(db.String(20), nullable=True)   # document/audio/voice/video
    file_id = db.Column(db.String(200), nullable=True)
    file_name = db.Column(db.String(255), nullable=True)


# ------------------------------
# Option A (Feb 2026): Auto-detect group context + admin approval
# ------------------------------


class GroupMovieContext(db.Model):
    """Per-group detected movie context (expires).

    Updated whenever a media/doc filename contains a movie title + year.
    Used to interpret subsequent role-list text messages that do not contain
    MOVIE_CODE.
    """

    __tablename__ = "group_movie_context"
    id = db.Column(db.Integer, primary_key=True)

    tg_chat_id = db.Column(db.BigInteger, nullable=False, unique=True, index=True)
    title = db.Column(db.String(255), nullable=False)
    year = db.Column(db.String(10), nullable=False)
    lang = db.Column(db.String(30), nullable=True)

    source_file_name = db.Column(db.String(255), nullable=True)
    source_message_id = db.Column(db.BigInteger, nullable=True)

    detected_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)


class GroupRoleImportRequest(db.Model):
    """Pending admin approval for auto-detected role list in a group."""

    __tablename__ = "group_role_import_request"
    id = db.Column(db.Integer, primary_key=True)

    tg_chat_id = db.Column(db.BigInteger, nullable=False, index=True)
    tg_message_id = db.Column(db.BigInteger, nullable=True)

    title = db.Column(db.String(255), nullable=False)
    year = db.Column(db.String(10), nullable=False)
    lang = db.Column(db.String(30), nullable=True)

    # raw role list text
    roles_text = db.Column(db.Text, nullable=False)
    # JSON strings (to keep things simple across Postgres/sqlite)
    roles_json = db.Column(db.Text, nullable=True)
    suggested_json = db.Column(db.Text, nullable=True)

    status = db.Column(db.String(20), nullable=False, default="PENDING")  # PENDING/APPROVED/REJECTED/EXPIRED
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)

    requested_by_tg_id = db.Column(db.BigInteger, nullable=True)
    requested_by_name = db.Column(db.String(120), nullable=True)

    reviewed_by_tg_id = db.Column(db.BigInteger, nullable=True)
    reviewed_by_name = db.Column(db.String(120), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    note = db.Column(db.Text, nullable=True)


class MovieEvent(db.Model):
    __tablename__ = "movie_event"
    id = db.Column(db.Integer, primary_key=True)
    movie_id = db.Column(db.Integer, nullable=True, index=True)
    movie_code = db.Column(db.String(40), nullable=True, index=True)
    movie_title = db.Column(db.String(255), nullable=True)
    event_type = db.Column(db.String(40), nullable=False, default="INFO", index=True)
    summary = db.Column(db.Text, nullable=False)
    detail = db.Column(db.Text, nullable=True)
    actor_source = db.Column(db.String(40), nullable=True)
    actor_name = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class AppKV(db.Model):
    __tablename__ = "app_kv"
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
