"""Opt-in, minimized task and semantic-tool trajectory storage."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import uuid
from typing import Any

from . import ontology_versions

MAX_TASKS = 100
MAX_EVENTS_PER_TASK = 500


def create_task(conn: sqlite3.Connection, ontology_version_id: str | None = None) -> dict[str, Any]:
    if ontology_version_id:
        version = ontology_versions.get_version(conn, ontology_version_id)
        if version is None or version["status"] != "accepted":
            raise ValueError("Task version must be an accepted ontology version")
    task_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO agent_tasks(id, ontology_version_id) VALUES (?, ?)",
        (task_id, ontology_version_id),
    )
    conn.commit()
    return get_task(conn, task_id)  # type: ignore[return-value]


def complete_task(conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
    task = _require_active(conn, task_id)
    conn.execute(
        "UPDATE agent_tasks SET status='completed', completed_at=datetime('now') WHERE id=?",
        (task["id"],),
    )
    conn.commit()
    return get_task(conn, task_id)  # type: ignore[return-value]


def record_event(
    conn: sqlite3.Connection,
    task_id: str,
    tool_name: str,
    outcome: str,
    *,
    query: str = "",
    context: str = "",
    result_count: int = 0,
    semantic_object_ids: list[str] | None = None,
    evidence_ref_ids: list[str] | None = None,
) -> dict[str, Any]:
    task = _require_active(conn, task_id)
    if outcome not in {"success", "not_found", "ambiguous", "error"}:
        raise ValueError("Unsupported trajectory outcome")
    if type(result_count) is not int or result_count < 0:
        raise ValueError("result_count must be a non-negative integer")
    objects = _bounded_ids(semantic_object_ids or [])
    evidence_ids = _bounded_ids(evidence_ref_ids or [])
    current_count = conn.execute(
        "SELECT COUNT(*) FROM agent_task_events WHERE task_id=?", (task_id,)
    ).fetchone()[0]
    if current_count >= MAX_EVENTS_PER_TASK:
        raise ValueError("Task trajectory reached the event limit")
    sequence = conn.execute(
        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_task_events WHERE task_id=?",
        (task_id,),
    ).fetchone()[0]
    event_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO agent_task_events"
        "(id, task_id, sequence, tool_name, outcome, query_fingerprint, result_count, semantic_object_ids_json, evidence_ref_ids_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event_id,
            task_id,
            sequence,
            _required_text(tool_name, "tool_name", 80),
            outcome,
            _fingerprint(query, context),
            result_count,
            json.dumps(objects, ensure_ascii=False),
            json.dumps(evidence_ids, ensure_ascii=False),
        ),
    )
    conn.commit()
    return _event(conn.execute("SELECT * FROM agent_task_events WHERE id=?", (event_id,)).fetchone())


def get_task(conn: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        return None
    task = dict(row)
    task["event_count"] = conn.execute(
        "SELECT COUNT(*) FROM agent_task_events WHERE task_id=?", (task_id,)
    ).fetchone()[0]
    task["events"] = [
        _event(event)
        for event in conn.execute(
            "SELECT * FROM agent_task_events WHERE task_id=? ORDER BY sequence, created_at, id LIMIT ?",
            (task_id, MAX_EVENTS_PER_TASK),
        ).fetchall()
    ]
    return task


def list_tasks(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    if type(limit) is not int or not 1 <= limit <= MAX_TASKS:
        raise ValueError(f"limit must be between 1 and {MAX_TASKS}")
    rows = conn.execute(
        "SELECT id, status, ontology_version_id, created_at, completed_at "
        "FROM agent_tasks ORDER BY created_at DESC, id LIMIT ?",
        (limit,),
    ).fetchall()
    tasks = []
    for row in rows:
        task = dict(row)
        task["event_count"] = conn.execute(
            "SELECT COUNT(*) FROM agent_task_events WHERE task_id=?", (task["id"],)
        ).fetchone()[0]
        tasks.append(task)
    return tasks


def delete_task(conn: sqlite3.Connection, task_id: str) -> bool:
    cursor = conn.execute("DELETE FROM agent_tasks WHERE id=?", (task_id,))
    conn.commit()
    return cursor.rowcount > 0


def _require_active(conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
    task_id = _required_text(task_id, "task_id", 80)
    row = conn.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        raise ValueError("Task does not exist")
    task = dict(row)
    if task["status"] != "active":
        raise ValueError("Task is already completed")
    return task


def _fingerprint(query: str, context: str) -> str:
    key = os.environ.get("UTOPIA_TRAJECTORY_HMAC_KEY", "").encode("utf-8")
    if not key:
        return ""
    material = json.dumps([query, context], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hmac.new(key, material, hashlib.sha256).hexdigest()


def _bounded_ids(values: list[str]) -> list[str]:
    if len(values) > 20 or any(not isinstance(value, str) or len(value) > 300 for value in values):
        raise ValueError("Trajectory identifier list exceeds its limit")
    return list(dict.fromkeys(values))


def _required_text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    value = value.strip()
    if not value or len(value) > maximum:
        raise ValueError(f"{label} must contain 1 to {maximum} characters")
    return value


def _event(row: sqlite3.Row) -> dict[str, Any]:
    event = dict(row)
    event["semantic_object_ids"] = json.loads(event.pop("semantic_object_ids_json"))
    event["evidence_ref_ids"] = json.loads(event.pop("evidence_ref_ids_json"))
    return event