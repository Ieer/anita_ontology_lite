"""Local-only storage and safe SQLite source access helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from werkzeug.utils import secure_filename


def data_root() -> Path:
    configured = os.environ.get("UTOPIA_DATA_ROOT")
    root = Path(configured).expanduser() if configured else Path.home() / ".local" / "share" / "utopia-lite"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def sqlite_root() -> Path:
    root = Path(os.environ.get("UTOPIA_SQLITE_ROOT", str(data_root() / "sources"))).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def markdown_root() -> Path:
    root = Path(os.environ.get("UTOPIA_MARKDOWN_ROOT", str(data_root() / "markdown"))).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def max_upload_bytes() -> int:
    return max(1, int(os.environ.get("UTOPIA_LOCAL_UPLOAD_MAX_BYTES", str(256 * 1024 * 1024))))


def _allowed_roots() -> list[Path]:
    roots = [sqlite_root()]
    extra = os.environ.get("UTOPIA_SQLITE_ALLOWED_ROOTS", "")
    for entry in extra.split(os.pathsep):
        if entry.strip():
            root = Path(entry).expanduser().resolve()
            if root.exists() and root.is_dir():
                roots.append(root)
    return roots


def resolve_sqlite_path(path: str, trusted_seed: bool = False) -> Path:
    if not path or "\x00" in path:
        raise ValueError("请选择有效的 SQLite 文件路径。")
    candidate = Path(path).expanduser().resolve(strict=True)
    if not candidate.is_file():
        raise ValueError("数据源必须是一个 SQLite 文件。")
    seed_candidate = candidate.name == "sample_hr.db" and trusted_seed
    if not seed_candidate and not any(candidate == root or root in candidate.parents for root in _allowed_roots()):
        raise ValueError("数据源不在允许的本地目录中。可上传副本或配置 UTOPIA_SQLITE_ALLOWED_ROOTS。")
    validate_sqlite_file(candidate)
    return candidate


def validate_sqlite_file(path: Path) -> None:
    if path.stat().st_size > max_upload_bytes():
        raise ValueError("SQLite 文件超过本地数据源大小上限。")
    try:
        with open_sqlite_readonly(path) as connection:
            result = connection.execute("PRAGMA quick_check(1)").fetchone()
            if not result or result[0] != "ok":
                raise ValueError("SQLite 文件完整性检查失败。")
    except sqlite3.Error as error:
        raise ValueError("文件不是可读取的 SQLite 数据库。") from error


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    for source_path in (path, Path(str(path) + "-wal")):
        if not source_path.exists():
            continue
        before = source_path.stat()
        digest.update(source_path.name.encode("utf-8"))
        with source_path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        after = source_path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("SQLite 数据源在读取期间发生变化，请稍后重试。")
    return digest.hexdigest()


def store_sqlite_upload(filename: str, content: bytes) -> tuple[Path, str]:
    if len(content) > max_upload_bytes():
        raise ValueError("上传文件超过本地数据源大小上限。")
    safe_name = secure_filename(Path(filename or "database.sqlite").name) or "database.sqlite"
    if not safe_name.lower().endswith((".db", ".sqlite", ".sqlite3")):
        safe_name += ".sqlite"
    target_root = sqlite_root()
    descriptor, temp_name = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=target_root)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        temp_path = Path(temp_name)
        validate_sqlite_file(temp_path)
        original = Path(safe_name)
        unique_name = f"{original.stem}-{uuid.uuid4().hex[:10]}{original.suffix or '.sqlite'}"
        destination = (target_root / unique_name).resolve()
        if target_root not in destination.parents:
            raise ValueError("上传文件名无效。")
        os.replace(temp_path, destination)
        return destination, file_sha256(destination)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def open_sqlite_readonly(path: Path) -> sqlite3.Connection:
    resolved = path.resolve(strict=True)
    uri = f"file:{quote(resolved.as_posix(), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def sqlite_schema(path: Path) -> list[dict[str, Any]]:
    with open_sqlite_readonly(path) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        result = []
        for row in tables:
            name = str(row["name"])
            columns = connection.execute(f"PRAGMA table_info({_quote_identifier(name)})").fetchall()
            result.append({
                "name": name,
                "columns": [
                    {
                        "name": str(column["name"]),
                        "type": str(column["type"] or "TEXT"),
                        "not_null": bool(column["notnull"]),
                        "primary_key_order": int(column["pk"]),
                    }
                    for column in columns
                ],
            })
        return result


def query_table(
    path: Path,
    table: str,
    columns: list[str] | None = None,
    filters: list[dict[str, Any]] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    schema = {item["name"]: item["columns"] for item in sqlite_schema(path)}
    if table not in schema:
        raise ValueError("所选数据表不存在。")
    available = {column["name"] for column in schema[table]}
    selected = columns or [column["name"] for column in schema[table]]
    if not selected or any(column not in available for column in selected):
        raise ValueError("所选列无效。")
    limit = min(max(int(limit), 1), 500)
    offset = max(int(offset), 0)
    predicates = []
    parameters: list[Any] = []
    allowed_operators = {"eq", "contains", "gt", "gte", "lt", "lte", "is_null", "not_null"}
    for item in (filters or [])[:20]:
        column = item.get("column")
        operator = item.get("operator")
        if column not in available or operator not in allowed_operators:
            raise ValueError("筛选列或操作符无效。")
        quoted = _quote_identifier(column)
        if operator == "is_null":
            predicates.append(f"{quoted} IS NULL")
        elif operator == "not_null":
            predicates.append(f"{quoted} IS NOT NULL")
        else:
            value = item.get("value")
            if operator == "contains":
                predicates.append(f"CAST({quoted} AS TEXT) LIKE ? ESCAPE '\\'")
                parameters.append("%" + _escape_like(str(value or "")) + "%")
            else:
                sql_operator = {"eq": "=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[operator]
                predicates.append(f"{quoted} {sql_operator} ?")
                parameters.append(value)
    sql = "SELECT " + ", ".join(_quote_identifier(column) for column in selected)
    sql += " FROM " + _quote_identifier(table)
    primary_key = [item["name"] for item in sorted(schema[table], key=lambda item: item["primary_key_order"]) if item["primary_key_order"]]
    if predicates:
        sql += " WHERE " + " AND ".join(predicates)
    if primary_key:
        sql += " ORDER BY " + ", ".join(_quote_identifier(column) for column in primary_key)
    sql += " LIMIT ? OFFSET ?"
    parameters.extend((limit + 1, offset))
    rows = execute_readonly(path, sql, parameters, row_limit=limit + 1)
    return {"columns": selected, "rows": rows["rows"][:limit], "has_more": len(rows["rows"]) > limit, "limit": limit, "offset": offset}


def read_table_rows(path: Path, table: str, columns: list[str], key_column: str, limit: int) -> list[dict[str, Any]]:
    schema = {item["name"]: item["columns"] for item in sqlite_schema(path)}
    if table not in schema:
        raise ValueError(f"数据表不存在：{table}")
    available = {column["name"] for column in schema[table]}
    if key_column not in available or any(column not in available for column in columns):
        raise ValueError("映射字段与当前数据表结构不匹配。")
    selected = list(dict.fromkeys([key_column, *columns]))
    statement = "SELECT " + ", ".join(_quote_identifier(column) for column in selected)
    statement += " FROM " + _quote_identifier(table) + " ORDER BY " + _quote_identifier(key_column)
    statement += " LIMIT ?"
    result = execute_readonly(path, statement, [max(1, limit) + 1], row_limit=max(1, limit) + 1)
    return result["rows"]


def execute_readonly(path: Path, sql: str, parameters: list[Any] | tuple[Any, ...] = (), row_limit: int = 1000) -> dict[str, Any]:
    statement = sql.strip()
    if len(statement) > 100_000:
        raise ValueError("只读 SQL 文本超过 100 KiB。")
    if not re.match(r"(?is)^(select|with)\b", statement):
        raise ValueError("仅允许 SELECT 或只读 WITH 查询。")
    started = time.monotonic()
    connection = open_sqlite_readonly(path)
    blocked_actions = {
        sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_TEMP_INDEX,
        sqlite3.SQLITE_CREATE_TEMP_TABLE, sqlite3.SQLITE_CREATE_TEMP_TRIGGER, sqlite3.SQLITE_CREATE_TEMP_VIEW,
        sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_CREATE_VIEW, sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_TEMP_INDEX, sqlite3.SQLITE_DROP_TEMP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_TRIGGER, sqlite3.SQLITE_DROP_TEMP_VIEW, sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA, sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_SAVEPOINT, sqlite3.SQLITE_ANALYZE, sqlite3.SQLITE_REINDEX,
    }

    def authorize(action: int, arg1: str | None, arg2: str | None, _database: str | None, _source: str | None) -> int:
        if action in blocked_actions:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or arg1 or "").lower() in {"load_extension", "writefile", "readfile", "randomblob", "zeroblob"}:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)
    connection.set_progress_handler(lambda: int(time.monotonic() - started > 2.0), 1000)
    try:
        cursor = connection.execute(statement, tuple(parameters))
        rows = []
        result_bytes = 0
        byte_limit = max(1024, int(os.environ.get("UTOPIA_LOCAL_QUERY_MAX_BYTES", str(5 * 1024 * 1024))))
        safe_fetch_limit = max(1001, int(os.environ.get("UTOPIA_MATERIALIZE_MAX_ROWS", "10000")) + 1)
        for row in cursor.fetchmany(min(max(row_limit, 1), safe_fetch_limit)):
            values = {key: (f"<BLOB {len(value)} bytes>" if isinstance(value, bytes) else value) for key, value in dict(row).items()}
            result_bytes += len(json.dumps(values, ensure_ascii=False, default=str).encode("utf-8"))
            if result_bytes > byte_limit:
                raise ValueError(f"查询结果超过 {byte_limit // (1024 * 1024)} MiB 显示上限。")
            rows.append(values)
        return {"columns": [item[0] for item in cursor.description or []], "rows": rows, "has_more": len(rows) >= row_limit}
    except ValueError:
        raise
    except sqlite3.Error as error:
        if "interrupted" in str(error).lower():
            raise ValueError("查询超过 2 秒执行时限。") from error
        raise ValueError(f"只读查询失败：{error}") from error
    finally:
        connection.close()


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
