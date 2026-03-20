"""Restore utilities for JSON ZIP backups.

Backups are produced by export_dynamic.backup_json_zip_dynamic(), which stores:
  - tables/<table>.json  (payload: {table, columns, rows})
  - meta.json

This module intentionally avoids ORM models and instead uses live schema
introspection to restore "what fits" into the current database.
"""

from __future__ import annotations

import base64
import json
import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Dict, List, Tuple

from sqlalchemy import inspect, text as sql_text
from sqlalchemy.engine import Engine


def _decode_special(v: Any) -> Any:
    """Reverse a small subset of export_dynamic._iso transformations."""
    if isinstance(v, dict) and "__bytes__" in v:
        try:
            return base64.b64decode(v["__bytes__"])
        except Exception:
            return None
    return v


def _read_backup_zip(zip_bytes: bytes) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """Return (meta, tables_payload).

    tables_payload maps table_name -> payload dict.
    """
    meta: Dict[str, Any] = {}
    tables: Dict[str, Dict[str, Any]] = {}

    bio = BytesIO(zip_bytes)
    with zipfile.ZipFile(bio, "r") as z:
        # meta.json is optional
        if "meta.json" in z.namelist():
            try:
                meta = json.loads(z.read("meta.json").decode("utf-8", errors="replace"))
            except Exception:
                meta = {}

        for name in z.namelist():
            if not name.startswith("tables/") or not name.endswith(".json"):
                continue
            raw = z.read(name).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
                t = payload.get("table") or name.split("/")[-1].rsplit(".", 1)[0]
                if isinstance(t, str) and t:
                    tables[t] = payload
            except Exception:
                # skip bad file
                continue

    return meta, tables


@dataclass
class DryRunTableReport:
    table: str
    backup_rows: int
    db_exists: bool
    db_columns: List[str]
    backup_columns: List[str]
    insertable_columns: List[str]
    extra_in_backup: List[str]
    missing_in_backup: List[str]


def restore_dry_run(engine: Engine, zip_bytes: bytes) -> Dict[str, Any]:
    """Analyze a backup zip against the current DB schema (no writes)."""
    meta, tables = _read_backup_zip(zip_bytes)
    insp = inspect(engine)
    db_tables = set(insp.get_table_names())

    table_reports: List[Dict[str, Any]] = []
    for t, payload in sorted(tables.items(), key=lambda x: x[0]):
        backup_cols = list(payload.get("columns") or [])
        rows = payload.get("rows") or []
        db_exists = t in db_tables
        db_cols: List[str] = []
        if db_exists:
            try:
                db_cols = [c["name"] for c in insp.get_columns(t)]
            except Exception:
                db_cols = []

        insertable = [c for c in backup_cols if c in db_cols] if db_cols else []
        extra = [c for c in backup_cols if c not in db_cols] if db_cols else backup_cols
        missing = [c for c in db_cols if c not in backup_cols] if db_cols else []

        rep = DryRunTableReport(
            table=t,
            backup_rows=len(rows) if isinstance(rows, list) else 0,
            db_exists=db_exists,
            db_columns=db_cols,
            backup_columns=backup_cols,
            insertable_columns=insertable,
            extra_in_backup=extra,
            missing_in_backup=missing,
        )
        table_reports.append(rep.__dict__)

    return {
        "meta": meta,
        "tables_in_backup": len(tables),
        "table_reports": table_reports,
    }


def restore_from_backup_zip(
    engine: Engine,
    zip_bytes: bytes,
    *,
    include_admin: bool = False,
    include_logs: bool = False,
    mode: str = "replace",
    only_tables: List[str] | None = None,
) -> Dict[str, Any]:
    """Restore database content from a JSON ZIP backup.

    Params:
      - mode:
          * replace: TRUNCATE/DELETE then insert
          * append:  keep existing rows, insert new rows (best-effort). Conflicts are ignored.
          * merge:   alias of append (reserved for future upsert)
      - only_tables: optional whitelist of table names to restore.

    Safety defaults:
      - admin tables are NOT restored unless include_admin=True
      - system_logs are NOT restored unless include_logs=True

    Returns a report dict.
    """
    meta, tables = _read_backup_zip(zip_bytes)
    insp = inspect(engine)
    db_tables = set(insp.get_table_names())
    dialect = (engine.dialect.name or "").lower()

    mode = (mode or "replace").lower().strip()
    if mode not in ("replace", "append", "merge"):
        mode = "replace"
    if mode == "merge":
        mode = "append"

    whitelist = None
    if only_tables:
        whitelist = {t.strip() for t in only_tables if isinstance(t, str) and t.strip()}

    report: Dict[str, Any] = {
        "restored_at": meta.get("exported_at"),
        "backup_app_version": meta.get("app_version"),
        "mode": mode,
        "include_admin": include_admin,
        "include_logs": include_logs,
        "only_tables": sorted(list(whitelist)) if whitelist else None,
        "tables": [],
        "errors": [],
    }

    # Filter tables by safety flags
    skip_tables = set()
    if not include_admin:
        skip_tables.update({"admin_user", "admin_telegram_user"})
    if not include_logs:
        skip_tables.add("system_logs")

    # Preferred insert order (parents first)
    preferred = [
        "movie",
        "translator",
        "vo_team",
        "assignment",
        "translation_task",
        "translation_submission",
        "vo_role_submission",
        "queue",
        "app_kv",
        "admin_user",
        "admin_telegram_user",
        "system_logs",
    ]

    present = [t for t in preferred if t in tables]
    remaining = [t for t in tables.keys() if t not in present]
    ordered = present + sorted(remaining)

    if whitelist is not None:
        ordered = [t for t in ordered if t in whitelist]

    with engine.begin() as conn:
        for t in ordered:
            if t in skip_tables:
                continue
            if t not in db_tables:
                report["errors"].append({"table": t, "error": "table_missing_in_db"})
                continue

            try:
                cols_db = [c["name"] for c in insp.get_columns(t)]
                payload = tables[t]
                cols_backup = list(payload.get("columns") or [])
                rows = payload.get("rows") or []
                if not isinstance(rows, list):
                    rows = []

                # Columns we can actually insert.
                insert_cols = [c for c in cols_backup if c in cols_db]
                if not insert_cols:
                    report["errors"].append({"table": t, "error": "no_insertable_columns"})
                    continue

                # Replace mode: clear table first
                if mode == "replace":
                    if dialect == "postgresql":
                        conn.execute(sql_text(f'TRUNCATE TABLE "{t}" RESTART IDENTITY CASCADE'))
                    else:
                        conn.execute(sql_text(f'DELETE FROM "{t}"'))

                # Prepare rows
                prepared: List[Dict[str, Any]] = []
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    pr = {}
                    for c in insert_cols:
                        pr[c] = _decode_special(r.get(c))
                    prepared.append(pr)

                # Insert statement (conflict-safe in append mode)
                q_cols = ", ".join([f'"{c}"' for c in insert_cols])
                q_vals = ", ".join([f':{c}' for c in insert_cols])

                if mode == "append":
                    if dialect == "sqlite":
                        ins = sql_text(f'INSERT OR IGNORE INTO "{t}" ({q_cols}) VALUES ({q_vals})')
                    elif dialect == "postgresql":
                        ins = sql_text(f'INSERT INTO "{t}" ({q_cols}) VALUES ({q_vals}) ON CONFLICT DO NOTHING')
                    else:
                        ins = sql_text(f'INSERT INTO "{t}" ({q_cols}) VALUES ({q_vals})')
                else:
                    ins = sql_text(f'INSERT INTO "{t}" ({q_cols}) VALUES ({q_vals})')

                chunk = 1000
                attempted = 0
                inserted = 0
                for i in range(0, len(prepared), chunk):
                    batch = prepared[i : i + chunk]
                    if not batch:
                        continue
                    attempted += len(batch)
                    res = conn.execute(ins, batch)
                    # rowcount can be -1 in some drivers; treat that as unknown
                    rc = getattr(res, "rowcount", None)
                    if isinstance(rc, int) and rc >= 0:
                        inserted += rc
                    else:
                        # best-effort estimate
                        inserted += len(batch) if mode == "replace" else 0

                report["tables"].append(
                    {
                        "table": t,
                        "rows_in_backup": len(prepared),
                        "rows_attempted": attempted,
                        "rows_inserted": inserted,
                        "insert_columns": insert_cols,
                        "skipped_columns": [c for c in cols_backup if c not in cols_db],
                    }
                )
            except Exception as e:
                report["errors"].append({"table": t, "error": str(e)})

    return report
