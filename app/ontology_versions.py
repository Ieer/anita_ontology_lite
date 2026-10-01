"""Immutable ontology snapshots with explicit evaluation and publication gates."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from . import ontology_documents, ontology_suggestions


def create_candidate(
    conn: sqlite3.Connection,
    document_id: str,
    parent_version_id: str | None = None,
) -> dict[str, Any]:
    document = ontology_documents.get_document(conn, document_id)
    if document is None:
        raise ValueError("Ontology document does not exist")
    active = get_active_version(conn, document_id)
    if parent_version_id is not None:
        parent = get_version(conn, parent_version_id)
        if parent is None or parent["document_id"] != document_id or parent["status"] != "accepted":
            raise ValueError("Parent version must be an accepted version of this document")
        if active and active["id"] != parent_version_id:
            raise ValueError("Parent version is not the active published version")
        if not active:
            raise ValueError("No published parent is active for this document")
    else:
        parent_version_id = active["id"] if active else None

    version_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO ontology_versions(id, document_id, parent_version_id, draft_version, snapshot_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (version_id, document_id, parent_version_id, document["version"], json.dumps(document["document"], ensure_ascii=False, separators=(",", ":"))),
    )
    conn.commit()
    return get_version(conn, version_id)  # type: ignore[return-value]


def list_versions(conn: sqlite3.Connection, document_id: str) -> list[dict[str, Any]]:
    return [
        _deserialize(row)
        for row in conn.execute(
            "SELECT * FROM ontology_versions WHERE document_id=? ORDER BY created_at DESC, id",
            (document_id,),
        ).fetchall()
    ]


def get_version(conn: sqlite3.Connection, version_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM ontology_versions WHERE id=?", (version_id,)).fetchone()
    return _deserialize(row) if row else None


def get_active_version(conn: sqlite3.Connection, document_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT v.* FROM ontology_active_versions a JOIN ontology_versions v ON v.id=a.version_id "
        "WHERE a.document_id=?",
        (document_id,),
    ).fetchone()
    return _deserialize(row) if row else None


def evaluate_candidate(conn: sqlite3.Connection, version_id: str) -> dict[str, Any]:
    candidate = get_version(conn, version_id)
    if candidate is None:
        raise ValueError("Candidate version does not exist")
    if candidate["status"] != "candidate":
        raise ValueError("Only candidate versions can be evaluated")
    errors = ontology_documents.validate_document(candidate["snapshot"])
    if not errors:
        errors.extend(ontology_suggestions.validate_evidence(conn, candidate["snapshot"]))
    parent = get_version(conn, candidate["parent_version_id"]) if candidate["parent_version_id"] else None
    parent_snapshot = parent["snapshot"] if parent else {"entityTypes": [], "relationships": []}
    candidate_keys = _semantic_keys(candidate["snapshot"])
    parent_keys = _semantic_keys(parent_snapshot)
    added = sorted(candidate_keys - parent_keys)
    removed = sorted(parent_keys - candidate_keys)
    changed = _changed_semantics(parent_snapshot, candidate["snapshot"])
    report = {
        "passed": not errors,
        "requires_human_review": True,
        "validation_errors": errors,
        "parent_version_id": candidate["parent_version_id"],
        "candidate_version_id": version_id,
        "added_semantics": added,
        "removed_semantics": removed,
        "changed_semantics": changed,
        "parent_semantics": len(parent_keys),
        "candidate_semantics": len(candidate_keys),
    }
    conn.execute(
        "UPDATE ontology_versions SET evaluation_json=?, evaluated_at=datetime('now') WHERE id=?",
        (json.dumps(report, ensure_ascii=False), version_id),
    )
    conn.commit()
    return report


def decide_candidate(
    conn: sqlite3.Connection,
    version_id: str,
    decision: str,
    reason: str = "",
) -> dict[str, Any]:
    if decision not in {"accept", "reject"}:
        raise ValueError("decision must be accept or reject")
    if decision == "reject" and not reason.strip():
        raise ValueError("A rejection reason is required")
    candidate = get_version(conn, version_id)
    if candidate is None:
        raise ValueError("Candidate version does not exist")
    if candidate["status"] != "candidate":
        raise ValueError("Candidate has already been decided")
    if not candidate["evaluation"]:
        raise ValueError("Evaluate the candidate before making a decision")
    if decision == "accept" and not candidate["evaluation"].get("passed"):
        raise ValueError("Candidate did not pass structural validation")
    if decision == "accept" and ontology_suggestions.validate_evidence(conn, candidate["snapshot"]):
        raise ValueError("Candidate source evidence changed; re-evaluate before publishing")

    if decision == "accept":
        active = get_active_version(conn, candidate["document_id"])
        active_id = active["id"] if active else None
        if active_id != candidate["parent_version_id"]:
            raise ValueError("Active parent changed; create and evaluate a new candidate")
        conn.execute(
            "UPDATE ontology_versions SET status='accepted', decision_reason=?, decided_at=datetime('now') WHERE id=?",
            (reason.strip(), version_id),
        )
        conn.execute(
            "INSERT INTO ontology_active_versions(document_id, version_id) VALUES (?, ?) "
            "ON CONFLICT(document_id) DO UPDATE SET version_id=excluded.version_id, published_at=datetime('now')",
            (candidate["document_id"], version_id),
        )
    else:
        conn.execute(
            "UPDATE ontology_versions SET status='rejected', decision_reason=?, decided_at=datetime('now') WHERE id=?",
            (reason.strip(), version_id),
        )
    conn.commit()
    return get_version(conn, version_id)  # type: ignore[return-value]


def activate_accepted_version(
    conn: sqlite3.Connection,
    document_id: str,
    version_id: str,
) -> dict[str, Any]:
    version = get_version(conn, version_id)
    if version is None or version["document_id"] != document_id or version["status"] != "accepted":
        raise ValueError("Only an accepted version of this document can be activated")
    conn.execute(
        "INSERT INTO ontology_active_versions(document_id, version_id) VALUES (?, ?) "
        "ON CONFLICT(document_id) DO UPDATE SET version_id=excluded.version_id, published_at=datetime('now')",
        (document_id, version_id),
    )
    conn.commit()
    return get_active_version(conn, document_id)  # type: ignore[return-value]


def _semantic_keys(document: dict[str, Any]) -> set[str]:
    keys = set()
    for item in document.get("entityTypes", []):
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            keys.add(f"term:{item['name'].strip().casefold()}")
    for item in document.get("relationships", []):
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            keys.add(f"relation:{item['name'].strip().casefold()}")
    return keys


def _changed_semantics(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    changes = []
    for collection, fields in (("entityTypes", ("name", "properties")), ("relationships", ("name", "from", "to", "cardinality", "attributes"))):
        old_items = {item["id"]: item for item in before.get(collection, []) if isinstance(item, dict) and isinstance(item.get("id"), str)}
        new_items = {item["id"]: item for item in after.get(collection, []) if isinstance(item, dict) and isinstance(item.get("id"), str)}
        for item_id in old_items.keys() & new_items.keys():
            for field in fields:
                if old_items[item_id].get(field) != new_items[item_id].get(field):
                    changes.append(f"{collection}:{item_id}.{field}")
    return sorted(changes)


def _deserialize(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["snapshot"] = json.loads(result.pop("snapshot_json"))
    result["evaluation"] = json.loads(result.pop("evaluation_json") or "{}")
    return result