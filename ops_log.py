"""Lightweight DB-backed system logging used by both web and Telegram bot.

This module is intentionally independent from Flask app creation to avoid
circular imports. It writes to a simple `system_logs` table created in
`app.auto_migrate_schema()`.
"""

from __future__ import annotations

from typing import Optional, List, Dict, Any

from sqlalchemy import text as sql_text

from db import db


def log_event(level: str, source: str, message: str, traceback: Optional[str] = None) -> None:
    """Insert an event into system_logs.

    Never raises (logging must never crash the app/bot).
    """
    try:
        db.session.execute(
            sql_text(
                """
                INSERT INTO system_logs(level, source, message, traceback)
                VALUES (:level, :source, :message, :traceback)
                """
            ),
            {
                "level": (level or "INFO")[:12],
                "source": (source or "")[:64],
                "message": message or "",
                "traceback": traceback,
            },
        )
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def fetch_logs(limit: int = 60) -> List[Dict[str, Any]]:
    """Fetch latest logs (ascending by time for UI rendering)."""
    limit = max(1, min(int(limit or 60), 500))
    rows = (
        db.session.execute(
            sql_text(
                """
                SELECT ts, level, source, message, traceback
                FROM system_logs
                ORDER BY id DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )
        .mappings()
        .all()
    )
    out: List[Dict[str, Any]] = []
    for r in rows[::-1]:
        ts = r["ts"]
        out.append(
            {
                "ts": (ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, "strftime") else (str(ts) if ts else "")),
                "level": r.get("level") or "INFO",
                "source": r.get("source") or "",
                "message": r.get("message") or "",
                "traceback": r.get("traceback") or "",
            }
        )
    return out
