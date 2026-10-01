"""Local Markdown source files linked to ontology JSON documents and search indexes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from . import ingest, local_data, search

MAX_MARKDOWN_BYTES = 5 * 1024 * 1024


def list_files(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT file_id, relative_path, title, ontology_document_id, sha256, indexed_sha256, updated_at "
        "FROM local_markdown_files ORDER BY title COLLATE NOCASE"
    ).fetchall()
    files = []
    for row in rows:
        result = dict(row)
        try:
            path = _resolve_relative_path(row["relative_path"])
            current_hash = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
        except (OSError, ValueError):
            current_hash = ""
        result["indexed"] = bool(current_hash and row["indexed_sha256"] == current_hash)
        result["missing"] = not bool(current_hash)
        files.append(result)
    return files


def get_file(conn, file_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM local_markdown_files WHERE file_id=?", (file_id,)).fetchone()
    if not row:
        return None
    path = _resolve_relative_path(row["relative_path"])
    if not path.is_file():
        raise ValueError("Markdown 源文件已不存在；可重新导入或移除该记录。")
    metadata, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    if metadata.get("file_id") != file_id:
        raise ValueError("Markdown front matter 的文件 ID 与索引记录不匹配。")
    current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "file_id": row["file_id"],
        "title": row["title"],
        "ontology_document_id": row["ontology_document_id"],
        "body": body,
        "sha256": current_hash,
        "indexed_sha256": row["indexed_sha256"],
        "indexed": bool(row["indexed_sha256"] and row["indexed_sha256"] == current_hash),
        "updated_at": row["updated_at"],
        "metadata": metadata,
    }


def save_file(conn, title: str, body: str, ontology_document_id: str = "", file_id: str = "") -> dict[str, Any]:
    title = (title or "").strip()
    if not title or len(title) > 200:
        raise ValueError("标题不能为空且不能超过 200 个字符。")
    body = body or ""
    if len(body.encode("utf-8")) > MAX_MARKDOWN_BYTES:
        raise ValueError("Markdown 正文超过 5 MiB。")
    ontology_document_id = (ontology_document_id or "").strip()
    if ontology_document_id:
        from . import ontology_documents

        if ontology_documents.get_document(conn, ontology_document_id) is None:
            raise ValueError("关联的本体 JSON 文档不存在。")

    existing = conn.execute("SELECT * FROM local_markdown_files WHERE file_id=?", (file_id,)).fetchone() if file_id else None
    if file_id and not existing:
        raise ValueError("Markdown 文件不存在；请先新建或导入。")
    file_id = str(existing["file_id"]) if existing else uuid.uuid4().hex
    relative_path = existing["relative_path"] if existing else f"{file_id}.md"
    path = _resolve_relative_path(relative_path)
    metadata = {"file_id": file_id, "title": title, "ontology_document_id": ontology_document_id or None}
    file_text = render_frontmatter(metadata, body)
    file_bytes = file_text.encode("utf-8")
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    old_bytes = path.read_bytes() if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".markdown-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(file_bytes)
            target.flush()
            os.fsync(target.fileno())
        conn.execute("SAVEPOINT save_local_markdown")
        os.replace(temporary, path)
        indexed_body = ingest.markdown_to_text(body)
        if existing and existing["document_id"]:
            document_id = int(existing["document_id"])
            search.replace_indexed_document(conn, document_id, title, indexed_body, commit=False, vector_index=False)
        else:
            document_id = search.index_document(conn, title, indexed_body, commit=False, vector_index=False)
        conn.execute(
            "INSERT INTO local_markdown_files(file_id, relative_path, title, ontology_document_id, document_id, sha256, indexed_sha256, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(file_id) DO UPDATE SET relative_path=excluded.relative_path, title=excluded.title, "
            "ontology_document_id=excluded.ontology_document_id, document_id=excluded.document_id, "
            "sha256=excluded.sha256, indexed_sha256=excluded.indexed_sha256, updated_at=excluded.updated_at",
            (file_id, relative_path, title, ontology_document_id or None, document_id, file_hash, file_hash),
        )
        conn.execute("RELEASE SAVEPOINT save_local_markdown")
        conn.commit()
    except Exception:
        try:
            conn.execute("ROLLBACK TO SAVEPOINT save_local_markdown")
            conn.execute("RELEASE SAVEPOINT save_local_markdown")
        except Exception:
            pass
        if old_bytes is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(old_bytes)
        raise
    finally:
        Path(temporary).unlink(missing_ok=True)
    return get_file(conn, file_id)


def import_file(conn, filename: str, content: bytes) -> dict[str, Any]:
    if len(content) > MAX_MARKDOWN_BYTES:
        raise ValueError("Markdown 文件超过 5 MiB。")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Markdown 文件必须使用 UTF-8 编码。") from error
    metadata, body = parse_frontmatter(text, strict=False)
    title = str(metadata.get("title") or Path(filename or "说明.md").stem)
    linked_id = str(metadata.get("ontology_document_id") or "")
    imported = save_file(conn, title, body, linked_id)
    return imported


def sync_index(conn, file_id: str) -> dict[str, Any]:
    source = get_file(conn, file_id)
    if source is None:
        raise ValueError("Markdown 文件不存在。")
    current_hash = source["sha256"]
    if source["indexed"]:
        return {"file_id": file_id, "indexed": True, "unchanged": True}
    row = conn.execute("SELECT * FROM local_markdown_files WHERE file_id=?", (file_id,)).fetchone()
    indexed_body = ingest.markdown_to_text(source["body"])
    title = str(source["metadata"].get("title") or row["title"]).strip()
    if not title or len(title) > 200:
        raise ValueError("Markdown front matter 标题不能为空且不能超过 200 个字符。")
    ontology_id = str(source["metadata"].get("ontology_document_id") or "").strip()
    if ontology_id:
        from . import ontology_documents

        if ontology_documents.get_document(conn, ontology_id) is None:
            raise ValueError("Markdown front matter 关联的本体 JSON 文档不存在。")
    if row["document_id"]:
        search.replace_indexed_document(conn, int(row["document_id"]), title, indexed_body, vector_index=False)
    else:
        document_id = search.index_document(conn, title, indexed_body, vector_index=False)
        conn.execute("UPDATE local_markdown_files SET document_id=? WHERE file_id=?", (document_id, file_id))
    conn.execute(
        "UPDATE local_markdown_files SET title=?, ontology_document_id=?, sha256=?, indexed_sha256=?, updated_at=datetime('now') WHERE file_id=?",
        (title, ontology_id or None, current_hash, current_hash, file_id),
    )
    conn.commit()
    return {"file_id": file_id, "indexed": True, "unchanged": False}


def remove_index(conn, file_id: str) -> bool:
    row = conn.execute("SELECT document_id FROM local_markdown_files WHERE file_id=?", (file_id,)).fetchone()
    if not row:
        return False
    conn.execute("SAVEPOINT remove_local_markdown_index")
    try:
        conn.execute("UPDATE local_markdown_files SET document_id=NULL, indexed_sha256='' WHERE file_id=?", (file_id,))
        if row["document_id"]:
            search.delete_indexed_document(conn, int(row["document_id"]), commit=False)
        conn.execute("RELEASE SAVEPOINT remove_local_markdown_index")
        conn.commit()
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT remove_local_markdown_index")
        conn.execute("RELEASE SAVEPOINT remove_local_markdown_index")
        raise
    return True


def delete_source(conn, file_id: str) -> bool:
    row = conn.execute("SELECT relative_path FROM local_markdown_files WHERE file_id=?", (file_id,)).fetchone()
    if not row:
        return False
    remove_index(conn, file_id)
    path = _resolve_relative_path(row["relative_path"])
    path.unlink(missing_ok=True)
    conn.execute("DELETE FROM local_markdown_files WHERE file_id=?", (file_id,))
    conn.commit()
    return True


def render_frontmatter(metadata: dict[str, Any], body: str) -> str:
    return "---\n" + json.dumps(metadata, ensure_ascii=False, sort_keys=True) + "\n---\n\n" + body.rstrip() + "\n"


def parse_frontmatter(text: str, strict: bool = True) -> tuple[dict[str, Any], str]:
    match = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", text, flags=re.S)
    if not match:
        if strict:
            raise ValueError("Markdown front matter 缺失。")
        return {}, text
    try:
        metadata = json.loads(match.group(1))
    except json.JSONDecodeError as error:
        raise ValueError("Markdown front matter 必须是合法的 JSON/YAML 对象。") from error
    if not isinstance(metadata, dict):
        raise ValueError("Markdown front matter 必须是对象。")
    return metadata, text[match.end():]


def _resolve_relative_path(relative_path: str) -> Path:
    root = local_data.markdown_root()
    candidate = (root / relative_path).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("Markdown 路径超出本地文档目录。")
    return candidate
