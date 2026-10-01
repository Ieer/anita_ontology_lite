"""Bounded, read-only semantic discovery over Lite's existing models."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from . import reason

KINDS = {"term", "mapping", "relation", "constraint", "all"}
MAX_RESULTS = 6
MAX_MENTIONS = 5
MAX_QUERY_LENGTH = 200
MAX_CONTEXT_LENGTH = 500
MAX_SOURCE_ROWS = 50
MAX_DOCUMENT_BYTES = 256_000
MAX_ITEMS_PER_DOCUMENT = 50


def browse_semantics(
    conn: sqlite3.Connection,
    query: str,
    kind: str = "all",
    limit: int = MAX_RESULTS,
) -> dict[str, Any]:
    """Find a bounded set of relevant terms, mappings, relations, or rules."""
    query = _validate_text(query, "query", MAX_QUERY_LENGTH)
    if kind not in KINDS:
        raise ValueError("kind must be term, mapping, relation, constraint, or all")
    if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
        raise ValueError(f"limit must be between 1 and {MAX_RESULTS}")

    records, truncated = _records(conn)
    ranked = _rank(records, query)
    if kind != "all":
        ranked = [entry for entry in ranked if entry[1]["kind"] == kind]
    return {
        "query": query,
        "kind": kind,
        "results": [item for _, item in ranked[:limit]],
        "has_more": truncated or len(ranked) > limit,
    }


def list_semantic_objects(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, str]]:
    """Provide compact, bounded UI options for existing semantic records."""
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    records, _truncated = _records(conn)
    return [
        {
            "label": f"{item['kind']} · {item['name']} [{item['representation']}]",
            "value": item["id"],
        }
        for item in records[:limit]
    ]


def resolve_semantics(
    conn: sqlite3.Connection,
    mentions: list[str],
    context: str = "",
    as_of: str | None = None,
    believed_at: str | None = None,
) -> dict[str, Any]:
    """Resolve mentions conservatively; tied best matches remain ambiguous."""
    if not isinstance(mentions, list) or not 1 <= len(mentions) <= MAX_MENTIONS:
        raise ValueError(f"mentions must contain 1 to {MAX_MENTIONS} items")
    mentions = [
        _validate_text(mention, f"mentions[{index}]", MAX_QUERY_LENGTH)
        for index, mention in enumerate(mentions)
    ]
    if not isinstance(context, str) or len(context) > MAX_CONTEXT_LENGTH:
        raise ValueError(f"context must be at most {MAX_CONTEXT_LENGTH} characters")
    for label, value in (("as_of", as_of), ("believed_at", believed_at)):
        if value is not None and (not isinstance(value, str) or len(value) > 40):
            raise ValueError(f"{label} must be a string of at most 40 characters")

    records, truncated = _records(conn)
    results = []
    for mention in mentions:
        ranked = _rank(records, mention, context)
        if not ranked:
            results.append({"mention": mention, "status": "not_found", "resolved": None, "candidates": [], "has_more": False})
            continue
        best_score = ranked[0][0]
        all_best = [item for score, item in ranked if score == best_score]
        best = all_best[:MAX_RESULTS]
        if len(best) > 1:
            results.append({"mention": mention, "status": "ambiguous", "resolved": None, "candidates": best, "has_more": len(all_best) > MAX_RESULTS})
            continue
        resolved = best[0]
        results.append({
            "mention": mention,
            "status": "resolved",
            "resolved": resolved,
            "candidates": [],
            "has_more": False,
            "linked": _linked(resolved, records),
            "instances": _sample_instances(conn, resolved, as_of, believed_at),
        })
    return {"as_of": as_of, "believed_at": believed_at, "results": results, "has_more": truncated}


def _records(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], bool]:
    records: list[dict[str, Any]] = []
    truncated = False

    oversized_documents = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM ontology_documents WHERE length(document_json)>?)",
        (MAX_DOCUMENT_BYTES,),
    ).fetchone()[0]
    documents = conn.execute(
        "SELECT d.id, d.name, COALESCE(v.snapshot_json, d.document_json) AS document_json, "
        "d.version, a.version_id AS active_version_id "
        "FROM ontology_documents d "
        "LEFT JOIN ontology_active_versions a ON a.document_id=d.id "
        "LEFT JOIN ontology_versions v ON v.id=a.version_id "
        "WHERE length(COALESCE(v.snapshot_json, d.document_json))<=? "
        "ORDER BY d.updated_at DESC, d.name LIMIT ?",
        (MAX_DOCUMENT_BYTES, MAX_SOURCE_ROWS + 1),
    ).fetchall()
    oversized_documents = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM ontology_documents d "
        "LEFT JOIN ontology_active_versions a ON a.document_id=d.id "
        "LEFT JOIN ontology_versions v ON v.id=a.version_id "
        "WHERE length(COALESCE(v.snapshot_json, d.document_json))>?)",
        (MAX_DOCUMENT_BYTES,),
    ).fetchone()[0]
    truncated |= bool(oversized_documents)
    truncated |= len(documents) > MAX_SOURCE_ROWS
    for row in documents[:MAX_SOURCE_ROWS]:
        try:
            document = json.loads(row["document_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(document, dict):
            continue
        entity_types = document.get("entityTypes", [])
        if not isinstance(entity_types, list):
            entity_types = []
        type_names = {
            item.get("id"): item.get("name", item.get("id", ""))
            for item in entity_types
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        source = (
            {"type": "published_ontology_version", "id": row["active_version_id"], "document_id": row["id"], "name": row["name"]}
            if row["active_version_id"]
            else {"type": "ontology_document_draft", "id": row["id"], "name": row["name"], "draft_version": row["version"]}
        )
        if isinstance(entity_types, list):
            truncated |= len(entity_types) > MAX_ITEMS_PER_DOCUMENT
            for entity_type in entity_types[:MAX_ITEMS_PER_DOCUMENT]:
                if not isinstance(entity_type, dict) or not _text(entity_type.get("name")):
                    continue
                properties = entity_type.get("properties", [])
                records.append({
                    "kind": "term",
                    "id": f"ontology:{row['id']}:term:{_text(entity_type.get('id'))}",
                    "name": _text(entity_type.get("name")),
                    "description": _text(entity_type.get("description")),
                    "properties": [
                        {key: prop[key] for key in ("name", "type", "description") if key in prop}
                        for prop in properties[:20] if isinstance(prop, dict)
                    ] if isinstance(properties, list) else [],
                    "representation": "ontology_document",
                    "source": source,
                })
        relationships = document.get("relationships", [])
        if isinstance(relationships, list):
            truncated |= len(relationships) > MAX_ITEMS_PER_DOCUMENT
            for relation in relationships[:MAX_ITEMS_PER_DOCUMENT]:
                if not isinstance(relation, dict) or not _text(relation.get("name")):
                    continue
                from_id, to_id = _text(relation.get("from")), _text(relation.get("to"))
                records.append({
                    "kind": "relation",
                    "id": f"ontology:{row['id']}:relation:{_text(relation.get('id'))}",
                    "name": _text(relation.get("name")),
                    "from_id": from_id,
                    "from_type": type_names.get(from_id, from_id),
                    "to_id": to_id,
                    "to_type": type_names.get(to_id, to_id),
                    "cardinality": _text(relation.get("cardinality")),
                    "representation": "ontology_document",
                    "source": source,
                })

    types = conn.execute("SELECT name FROM types ORDER BY name LIMIT ?", (MAX_SOURCE_ROWS + 1,)).fetchall()
    truncated |= len(types) > MAX_SOURCE_ROWS
    subclasses = conn.execute(
        "SELECT child, parent FROM subclass_of ORDER BY child, parent LIMIT ?", (MAX_SOURCE_ROWS + 1,)
    ).fetchall()
    truncated |= len(subclasses) > MAX_SOURCE_ROWS
    equivalences = conn.execute(
        "SELECT type_a, type_b FROM type_equivalences ORDER BY type_a, type_b LIMIT ?", (MAX_SOURCE_ROWS + 1,)
    ).fetchall()
    truncated |= len(equivalences) > MAX_SOURCE_ROWS
    for row in types[:MAX_SOURCE_ROWS]:
        name = row["name"]
        records.append({
            "kind": "term",
            "id": f"runtime_type:{name}",
            "name": name,
            "description": "Runtime instance type used by the knowledge graph.",
            "parents": sorted(item["parent"] for item in subclasses if item["child"] == name),
            "equivalents": sorted(
                pair[1] if pair[0] == name else pair[0]
                for pair in equivalences if name in (pair[0], pair[1])
            ),
            "representation": "runtime_type",
            "source": {"type": "runtime_ontology"},
        })

    tables = conn.execute(
        "SELECT mount_name, table_name, entity_type, name_col, key_col FROM table_mappings "
        "ORDER BY mount_name, table_name LIMIT ?",
        (MAX_SOURCE_ROWS + 1,),
    ).fetchall()
    truncated |= len(tables) > MAX_SOURCE_ROWS
    columns = conn.execute(
        "SELECT mount_name, table_name, column_name, target_type, predicate, attr_name "
        "FROM column_mappings ORDER BY mount_name, table_name, column_name LIMIT ?",
        (MAX_SOURCE_ROWS + 1,),
    ).fetchall()
    truncated |= len(columns) > MAX_SOURCE_ROWS
    for row in tables[:MAX_SOURCE_ROWS]:
        records.append({
            "kind": "mapping",
            "id": f"table_mapping:{row['mount_name']}:{row['table_name']}",
            "name": f"{row['entity_type']} {row['mount_name']}.{row['table_name']}",
            "mapping_type": "table",
            "mount_name": row["mount_name"],
            "table_name": row["table_name"],
            "entity_type": row["entity_type"],
            "name_column": row["name_col"],
            "key_column": row["key_col"],
            "representation": "sqlite_mapping",
            "source": {"mount_name": row["mount_name"], "table_name": row["table_name"]},
        })
    for row in columns[:MAX_SOURCE_ROWS]:
        is_attribute = bool(row["attr_name"])
        records.append({
            "kind": "mapping",
            "id": f"column_mapping:{row['mount_name']}:{row['table_name']}:{row['column_name']}",
            "name": row["attr_name"] if is_attribute else row["predicate"],
            "mapping_type": "attribute" if is_attribute else "relation",
            "mount_name": row["mount_name"],
            "table_name": row["table_name"],
            "column_name": row["column_name"],
            "attribute": row["attr_name"],
            "predicate": row["predicate"],
            "target_type": row["target_type"],
            "representation": "sqlite_mapping",
            "source": {"mount_name": row["mount_name"], "table_name": row["table_name"]},
        })

    for predicate, rules in reason.AXIOMS.items():
        records.append({
            "kind": "constraint",
            "id": f"axiom:{predicate}",
            "name": predicate,
            "rules": dict(rules),
            "representation": "runtime_axiom",
            "source": {"type": "runtime_axioms"},
        })
    return records, truncated


def _rank(records: list[dict[str, Any]], query: str, context: str = "") -> list[tuple[int, dict[str, Any]]]:
    needle = query.casefold()
    context_tokens = _tokens(context.casefold())
    ranked = []
    for item in records:
        name = item["name"].casefold()
        searchable = json.dumps(item, ensure_ascii=False, sort_keys=True).casefold()
        if name == needle:
            score = 100
        elif needle in name:
            score = 80
        elif needle in searchable:
            score = 60
        else:
            tokens = _tokens(needle)
            matches = sum(token in searchable for token in tokens)
            if not tokens or not matches:
                continue
            score = (40 + matches) if matches == len(tokens) else 10
        score += min(5, sum(token in searchable for token in context_tokens))
        ranked.append((score, item))
    return sorted(ranked, key=lambda entry: (-entry[0], entry[1]["kind"], entry[1]["name"].casefold(), entry[1]["id"]))


def _linked(selected: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    name = selected["name"].casefold()
    related: dict[str, list[dict[str, Any]]] = {"mappings": [], "relations": [], "constraints": []}
    if selected["kind"] == "term":
        aliases = {name}
        aliases.add(selected["id"].rsplit(":", 1)[-1].casefold())
        for item in records:
            if item["kind"] == "mapping" and (
                item.get("entity_type", "").casefold() in aliases
                or item.get("target_type", "").casefold() in aliases
                or item.get("attribute", "").casefold() in {
                    prop.get("name", "").casefold() for prop in selected.get("properties", [])
                }
            ):
                related["mappings"].append(item)
            elif item["kind"] == "relation" and aliases.intersection({
                item.get("from_id", "").casefold(), item.get("from_type", "").casefold(),
                item.get("to_id", "").casefold(), item.get("to_type", "").casefold(),
            }):
                related["relations"].append(item)
    else:
        related["mappings"] = [item for item in records if item["kind"] == "mapping" and item.get("predicate", "").casefold() == name]
        related["relations"] = [item for item in records if item["kind"] == "relation" and item["name"].casefold() == name]
        related["constraints"] = [item for item in records if item["kind"] == "constraint" and item["name"].casefold() == name]
    return {key: values[:MAX_RESULTS] for key, values in related.items()}


def _sample_instances(
    conn: sqlite3.Connection,
    selected: dict[str, Any],
    as_of: str | None,
    believed_at: str | None,
) -> list[dict[str, Any]]:
    if selected["kind"] != "term":
        return []
    type_name = selected["name"]
    entities = conn.execute(
        "SELECT id, name, type FROM entities WHERE type=? AND merged_into IS NULL ORDER BY id LIMIT 3",
        (type_name,),
    ).fetchall()
    samples = []
    for entity in entities:
        query = (
            "SELECT f.id, f.predicate, o.name AS object_name, f.valid_from, f.valid_to, f.source, f.derived "
            "FROM facts f JOIN entities o ON o.id=f.object_id "
            "WHERE f.subject_id=?"
        )
        params: list[Any] = [entity["id"]]
        if as_of:
            query += " AND f.valid_from<=? AND (f.valid_to IS NULL OR f.valid_to>?)"
            params.extend((as_of, as_of))
        if believed_at:
            query += (
                " AND f.asserted_at<=? AND (f.status='active' OR "
                "(f.status='superseded' AND NOT EXISTS ("
                "SELECT 1 FROM facts replacement WHERE replacement.id=f.superseded_by "
                "AND replacement.asserted_at<=?)))"
            )
            params.extend((believed_at, believed_at))
        else:
            query += " AND f.status='active'"
        query += " ORDER BY f.id DESC LIMIT 3"
        samples.append({"entity": dict(entity), "facts": [dict(row) for row in conn.execute(query, params).fetchall()]})
    return samples


def _tokens(value: str) -> list[str]:
    return re.findall(r"[^\W_]+", value, flags=re.UNICODE)


def _validate_text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    value = value.strip()
    if not value or len(value) > maximum:
        raise ValueError(f"{label} must contain 1 to {maximum} characters")
    return value


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""