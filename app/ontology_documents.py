"""Persistent ontology design documents, separate from instance type inference."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any


CARDINALITIES = {"one-to-one", "one-to-many", "many-to-one", "many-to-many"}
PROPERTY_TYPES = {"string", "integer", "decimal", "double", "date", "datetime", "boolean", "enum"}


def validate_document(document: Any) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not isinstance(document, dict):
        return [{"path": "$", "message": "Ontology document must be an object."}]

    if not isinstance(document.get("name"), str) or not document["name"].strip():
        errors.append({"path": "name", "message": "Ontology name is required."})

    entity_types = document.get("entityTypes")
    if not isinstance(entity_types, list):
        errors.append({"path": "entityTypes", "message": "Entity types must be a list."})
        entity_types = []

    entity_ids: set[str] = set()
    for index, entity in enumerate(entity_types):
        path = f"entityTypes[{index}]"
        if not isinstance(entity, dict):
            errors.append({"path": path, "message": "Entity type must be an object."})
            continue
        entity_id = entity.get("id")
        if not isinstance(entity_id, str) or not entity_id.strip():
            errors.append({"path": f"{path}.id", "message": "Entity type ID is required."})
        elif entity_id in entity_ids:
            errors.append({"path": f"{path}.id", "message": f"Duplicate entity type ID: {entity_id}."})
        else:
            entity_ids.add(entity_id)
        if not isinstance(entity.get("name"), str) or not entity["name"].strip():
            errors.append({"path": f"{path}.name", "message": "Entity type name is required."})

        properties = entity.get("properties", [])
        if not isinstance(properties, list):
            errors.append({"path": f"{path}.properties", "message": "Properties must be a list."})
            continue
        property_names: set[str] = set()
        for property_index, prop in enumerate(properties):
            property_path = f"{path}.properties[{property_index}]"
            if not isinstance(prop, dict):
                errors.append({"path": property_path, "message": "Property must be an object."})
                continue
            property_name = prop.get("name")
            if not isinstance(property_name, str) or not property_name.strip():
                errors.append({"path": f"{property_path}.name", "message": "Property name is required."})
            elif property_name in property_names:
                errors.append({"path": f"{property_path}.name", "message": f"Duplicate property name: {property_name}."})
            else:
                property_names.add(property_name)
            if prop.get("type") not in PROPERTY_TYPES:
                errors.append({"path": f"{property_path}.type", "message": "Unsupported property type."})

    relationships = document.get("relationships", [])
    if not isinstance(relationships, list):
        errors.append({"path": "relationships", "message": "Relationships must be a list."})
        relationships = []

    relationship_ids: set[str] = set()
    for index, relationship in enumerate(relationships):
        path = f"relationships[{index}]"
        if not isinstance(relationship, dict):
            errors.append({"path": path, "message": "Relationship must be an object."})
            continue
        relationship_id = relationship.get("id")
        if not isinstance(relationship_id, str) or not relationship_id.strip():
            errors.append({"path": f"{path}.id", "message": "Relationship ID is required."})
        elif relationship_id in relationship_ids:
            errors.append({"path": f"{path}.id", "message": f"Duplicate relationship ID: {relationship_id}."})
        else:
            relationship_ids.add(relationship_id)
        if not isinstance(relationship.get("name"), str) or not relationship["name"].strip():
            errors.append({"path": f"{path}.name", "message": "Relationship name is required."})
        for endpoint in ("from", "to"):
            if relationship.get(endpoint) not in entity_ids:
                errors.append({"path": f"{path}.{endpoint}", "message": f"Relationship {endpoint} must reference an entity type."})
        if relationship.get("cardinality") not in CARDINALITIES:
            errors.append({"path": f"{path}.cardinality", "message": "Unsupported relationship cardinality."})

    return errors


def _prepare_rdf_source(document, rdf_source_xml, rdf_source_document):
    if not rdf_source_xml:
        return None, None
    from . import ontology_rdf

    source_document = rdf_source_document or document
    preserved_xml = ontology_rdf.serialize_document(document, rdf_source_xml, source_document)
    snapshot = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    return preserved_xml, snapshot


def create_document(
    conn: sqlite3.Connection,
    document: dict[str, Any],
    rdf_source_xml: str | None = None,
    rdf_source_document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    document_id = uuid.uuid4().hex
    name = str(document.get("name", "Untitled ontology")).strip() or "Untitled ontology"
    source_xml, source_snapshot = _prepare_rdf_source(document, rdf_source_xml, rdf_source_document)
    conn.execute(
        "INSERT INTO ontology_documents(id, name, document_json, rdf_source_xml, rdf_source_document_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            document_id,
            name,
            json.dumps(document, ensure_ascii=False, separators=(",", ":")),
            source_xml,
            source_snapshot,
        ),
    )
    conn.commit()
    return get_document(conn, document_id)  # type: ignore[return-value]


def list_documents(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, name, version, created_at, updated_at FROM ontology_documents ORDER BY updated_at DESC, name"
    ).fetchall()
    return [dict(row) for row in rows]


def get_document(conn: sqlite3.Connection, document_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, name, document_json, rdf_source_xml, rdf_source_document_json, version, created_at, updated_at "
        "FROM ontology_documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "document": json.loads(row["document_json"]),
        "rdf_source_xml": row["rdf_source_xml"],
        "rdf_source_document": json.loads(row["rdf_source_document_json"])
        if row["rdf_source_document_json"]
        else None,
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def update_document(
    conn: sqlite3.Connection,
    document_id: str,
    document: dict[str, Any],
    rdf_source_xml: str | None = None,
    rdf_source_document: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    name = str(document.get("name", "Untitled ontology")).strip() or "Untitled ontology"
    if rdf_source_xml is None:
        existing = conn.execute(
            "SELECT rdf_source_xml, rdf_source_document_json FROM ontology_documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        if existing is not None and existing["rdf_source_xml"]:
            rdf_source_xml = existing["rdf_source_xml"]
            rdf_source_document = (
                json.loads(existing["rdf_source_document_json"])
                if existing["rdf_source_document_json"]
                else document
            )
    source_xml, source_snapshot = _prepare_rdf_source(document, rdf_source_xml, rdf_source_document)
    cursor = conn.execute(
        "UPDATE ontology_documents SET name = ?, document_json = ?, rdf_source_xml = ?, "
        "rdf_source_document_json = ?, version = version + 1, "
        "updated_at = datetime('now') WHERE id = ?",
        (
            name,
            json.dumps(document, ensure_ascii=False, separators=(",", ":")),
            source_xml,
            source_snapshot,
            document_id,
        ),
    )
    conn.commit()
    if cursor.rowcount == 0:
        return None
    return get_document(conn, document_id)


def delete_document(conn: sqlite3.Connection, document_id: str) -> bool:
    cursor = conn.execute("DELETE FROM ontology_documents WHERE id = ?", (document_id,))
    conn.commit()
    return cursor.rowcount > 0