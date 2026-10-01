"""Append-only references to existing evidence sources, without source copies."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from typing import Any

from . import local_markdown

SOURCE_TYPES = {"fact", "document", "markdown", "materialization"}
MAX_SOURCE_OPTIONS = 100


def list_sources(
    conn: sqlite3.Connection,
    source_type: str,
    limit: int = MAX_SOURCE_OPTIONS,
) -> list[dict[str, str]]:
    if type(limit) is not int or not 1 <= limit <= MAX_SOURCE_OPTIONS:
        raise ValueError(f"limit must be between 1 and {MAX_SOURCE_OPTIONS}")
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"source_type must be one of: {', '.join(sorted(SOURCE_TYPES))}")
    if source_type == "fact":
        rows = conn.execute(
            "SELECT f.id, s.name AS subject, f.predicate, o.name AS object "
            "FROM facts f JOIN entities s ON s.id=f.subject_id JOIN entities o ON o.id=f.object_id "
            "ORDER BY f.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [{
            "source_type": source_type,
            "source_id": str(row["id"]),
            "label": f"事实 · {row['subject']} {row['predicate']} {row['object']} (#{row['id']})",
        } for row in rows]
    if source_type == "document":
        rows = conn.execute("SELECT id, title FROM documents ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"source_type": source_type, "source_id": str(row["id"]), "label": f"文档 · {row['title']} (#{row['id']})"} for row in rows]
    if source_type == "markdown":
        return [{
            "source_type": source_type,
            "source_id": item["file_id"],
            "label": f"Markdown · {item['title']} ({'可用' if not item['missing'] else '源文件缺失'})",
        } for item in local_markdown.list_files(conn)[:limit]]
    rows = conn.execute(
        "SELECT id, mount_name, created_at FROM materialization_batches ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [{
        "source_type": source_type,
        "source_id": str(row["id"]),
        "label": f"物化批次 · {row['mount_name']} (#{row['id']} · {row['created_at']})",
    } for row in rows]


def add_reference(
    conn: sqlite3.Connection,
    semantic_object_id: str,
    source_type: str,
    source_id: str,
    locator: str = "",
    label: str = "",
) -> dict[str, Any]:
    semantic_object_id = _required_text(semantic_object_id, "semantic_object_id", 300)
    source_type = _required_text(source_type, "source_type", 32)
    source_id = _required_text(source_id, "source_id", 200)
    locator = _optional_text(locator, "locator", 500)
    label = _optional_text(label, "label", 200)
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"source_type must be one of: {', '.join(sorted(SOURCE_TYPES))}")

    _source, digest = source_snapshot(conn, source_type, source_id)
    reference_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO semantic_evidence_refs"
        "(id, semantic_object_id, source_type, source_id, locator, label, captured_sha256) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (reference_id, semantic_object_id, source_type, source_id, locator, label, digest),
    )
    conn.commit()
    return get_reference(conn, reference_id)  # type: ignore[return-value]


def list_references(conn: sqlite3.Connection, semantic_object_id: str) -> list[dict[str, Any]]:
    semantic_object_id = _required_text(semantic_object_id, "semantic_object_id", 300)
    rows = conn.execute(
        "SELECT * FROM semantic_evidence_refs WHERE semantic_object_id=? ORDER BY created_at, id",
        (semantic_object_id,),
    ).fetchall()
    return [_with_current_state(conn, dict(row)) for row in rows]


def get_reference(conn: sqlite3.Connection, reference_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM semantic_evidence_refs WHERE id=?", (reference_id,)).fetchone()
    return _with_current_state(conn, dict(row)) if row else None


def source_snapshot(conn: sqlite3.Connection, source_type: str, source_id: str) -> tuple[dict[str, Any], str]:
    """Read one allowed source through its existing storage API and hash it."""
    if source_type == "fact":
        row = conn.execute(
            "SELECT f.id, f.subject_id, f.predicate, f.object_id, f.valid_from, f.valid_to, "
            "f.asserted_at, f.superseded_by, f.source, f.derived, f.status, "
            "s.name AS subject_name, s.type AS subject_type, "
            "o.name AS object_name, o.type AS object_type "
            "FROM facts f JOIN entities s ON s.id=f.subject_id JOIN entities o ON o.id=f.object_id "
            "WHERE f.id=?",
            (_as_int(source_id, "fact source_id"),),
        ).fetchone()
        if row is None:
            raise ValueError("fact source does not exist")
        source = dict(row)
    elif source_type == "document":
        row = conn.execute("SELECT id, title, content FROM documents WHERE id=?", (_as_int(source_id, "document source_id"),)).fetchone()
        if row is None:
            raise ValueError("document source does not exist")
        source = dict(row)
    elif source_type == "markdown":
        try:
            source = local_markdown.get_file(conn, source_id)
        except (OSError, ValueError) as error:
            raise ValueError(str(error)) from error
        if source is None:
            raise ValueError("Markdown source does not exist")
        source = {key: source[key] for key in ("file_id", "title", "ontology_document_id", "sha256", "updated_at")}
    elif source_type == "materialization":
        row = conn.execute(
            "SELECT id, mount_name, source_sha256, mapping_sha256, status, stats_json, created_at "
            "FROM materialization_batches WHERE id=?",
            (_as_int(source_id, "materialization source_id"),),
        ).fetchone()
        if row is None:
            raise ValueError("materialization source does not exist")
        source = dict(row)
    else:
        raise ValueError(f"unsupported source_type: {source_type}")
    return source, _digest(source)


def _with_current_state(conn: sqlite3.Connection, reference: dict[str, Any]) -> dict[str, Any]:
    try:
        _source, current_digest = source_snapshot(conn, reference["source_type"], reference["source_id"])
    except (ValueError, OSError, sqlite3.Error):
        state = "missing"
    else:
        state = "current" if current_digest == reference["captured_sha256"] else "stale"
    reference["state"] = state
    return reference


def _digest(value: dict[str, Any]) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _required_text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    value = value.strip()
    if not value or len(value) > maximum:
        raise ValueError(f"{label} must contain 1 to {maximum} characters")
    return value


def _optional_text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{label} must be a string of at most {maximum} characters")
    return value.strip()


def _as_int(value: str, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be an integer") from error
    if result < 1:
        raise ValueError(f"{label} must be positive")
    return result