from __future__ import annotations

from datetime import datetime
from typing import List

from db import db
from models import Movie, MovieEvent


def record_movie_event(movie: Movie | None, event_type: str, summary: str, detail: str | None = None,
                       actor_source: str | None = None, actor_name: str | None = None,
                       commit: bool = False) -> MovieEvent | None:
    if not movie:
        return None
    row = MovieEvent(
        movie_id=getattr(movie, "id", None),
        movie_code=(getattr(movie, "code", None) or "").strip() or None,
        movie_title=(getattr(movie, "title", None) or "").strip() or None,
        event_type=(event_type or "INFO").strip()[:40],
        summary=(summary or "").strip(),
        detail=(detail or "").strip() or None,
        actor_source=(actor_source or "").strip()[:40] or None,
        actor_name=(actor_name or "").strip()[:120] or None,
        created_at=datetime.utcnow(),
    )
    db.session.add(row)
    if commit:
        db.session.commit()
    return row


def fetch_movie_history(movie: Movie | None = None, movie_code: str | None = None, limit: int = 30) -> List[MovieEvent]:
    q = MovieEvent.query
    code = (movie_code or (getattr(movie, "code", None) if movie else None) or "").strip()
    if movie is not None and getattr(movie, "id", None):
        q = q.filter((MovieEvent.movie_id == movie.id) | (MovieEvent.movie_code == code))
    elif code:
        q = q.filter(MovieEvent.movie_code == code)
    else:
        return []
    return q.order_by(MovieEvent.id.desc()).limit(max(1, min(int(limit or 30), 100))).all()


def fetch_recent_movie_events(limit: int = 25, movie_code: str | None = None, include_archived: bool = True) -> List[MovieEvent]:
    q = MovieEvent.query
    code = (movie_code or '').strip()
    if code:
        q = q.filter(MovieEvent.movie_code == code)
    rows = q.order_by(MovieEvent.id.desc()).limit(max(1, min(int(limit or 25), 200))).all()
    if include_archived:
        return rows
    filtered = []
    active_codes = {str((m.code or '')).strip() for m in Movie.query.filter((Movie.is_archived == False) | (Movie.is_archived.is_(None))).all() if (m.code or '').strip()}
    for ev in rows:
        code = (getattr(ev, 'movie_code', None) or '').strip()
        if not code or code in active_codes:
            filtered.append(ev)
    return filtered
