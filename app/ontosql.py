"""Ontology2SQL（教学版）：挂载外部数据库 + 表→本体映射 + 查询。

对应 Utopia 的 ontology-driven querying（挂载 Postgres/MySQL/Trino 等，把表映射到
本体，chat 可问数）。教学版只做核心结构：
- mount_db   挂载一个 SQLite 文件作为外部数据源
- map_table  把表映射到本体类型（表 → 实体类型 + 名称列）
- query      对挂载库执行**只读** SQL 查询

NL→SQL（LLM 把自然语言转成 SQL，原版 state-of-the-art 的核心）留作扩展点。
"""

from __future__ import annotations

import os
import re
import sqlite3
import json
from datetime import date

from . import db as dbmod, graph, llm, local_data


def mount_db(
    conn: sqlite3.Connection,
    name: str,
    path: str,
    source_kind: str = "path",
    source_filename: str = "",
    trusted_seed: bool = False,
) -> None:
    if not name or len(name) > 80:
        raise ValueError("挂载名不能为空且不能超过 80 个字符。")
    resolved = local_data.resolve_sqlite_path(path, trusted_seed=trusted_seed)
    source_hash = local_data.file_sha256(resolved)
    existing = conn.execute(
        "SELECT m.path, COALESCE(s.source_sha256, '') AS source_sha256 "
        "FROM mounted_dbs m LEFT JOIN mounted_source_metadata s ON s.mount_name=m.name WHERE m.name=?",
        (name,),
    ).fetchone()
    if existing:
        if os.path.realpath(existing["path"]) != str(resolved) or (existing["source_sha256"] and existing["source_sha256"] != source_hash):
            raise ValueError(f"挂载名“{name}”已被其他数据源占用；请使用新名称创建快照。")
        return
    conn.execute("INSERT OR REPLACE INTO mounted_dbs(name, path) VALUES (?, ?)", (name, str(resolved)))
    conn.execute(
        "INSERT OR REPLACE INTO mounted_source_metadata(mount_name, source_kind, source_filename, source_sha256) "
        "VALUES (?, ?, ?, ?)",
        (name, source_kind, source_filename or resolved.name, source_hash),
    )
    conn.commit()


def list_mounted(conn: sqlite3.Connection) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT m.name, m.path, COALESCE(s.source_kind, 'path') AS source_kind, "
            "COALESCE(s.source_filename, '') AS source_filename, COALESCE(s.source_sha256, '') AS source_sha256 "
            "FROM mounted_dbs m LEFT JOIN mounted_source_metadata s ON s.mount_name=m.name ORDER BY m.name"
        )
    ]


def map_table(
    conn: sqlite3.Connection,
    mount_name: str,
    table: str,
    entity_type: str,
    name_col: str,
    key_col: str = "",
) -> None:
    _mount, path = _mounted_source(conn, mount_name)
    available = {column["name"] for table_info in local_data.sqlite_schema(path) if table_info["name"] == table for column in table_info["columns"]}
    if table not in {item["name"] for item in local_data.sqlite_schema(path)}:
        raise ValueError(f"数据表不存在：{table}")
    if name_col not in available or (key_col and key_col not in available):
        raise ValueError("名称列或源键列不存在于所选数据表。")
    key_col = key_col or name_col
    conn.execute(
        "INSERT OR REPLACE INTO table_mappings(mount_name, table_name, entity_type, name_col, key_col) "
        "VALUES (?, ?, ?, ?, ?)",
        (mount_name, table, entity_type, name_col, key_col),
    )
    conn.commit()


def list_mappings(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM table_mappings ORDER BY mount_name")]


# ---- 字段级映射：把表里的列映射为本体关系 ----

def map_column(
    conn: sqlite3.Connection,
    mount_name: str,
    table: str,
    column: str,
    target_type: str = "",
    predicate: str = "",
    attr_name: str = "",
) -> None:
    """声明列级映射，两种模式：
    - 关系模式：target_type + predicate —— 列值变成 target_type 实体，主实体通过 predicate 连到它。
      例 employees.dept → (Department, works_in)。
    - 属性模式：attr_name —— 列值直接作为主实体的属性值。例 employees.salary → salary 属性。
    """
    _mount, path = _mounted_source(conn, mount_name)
    schema = {table_info["name"]: {column["name"] for column in table_info["columns"]} for table_info in local_data.sqlite_schema(path)}
    if table not in schema or column not in schema[table]:
        raise ValueError("所选列不存在于当前挂载数据表。")
    attr_name = (attr_name or "").strip()
    if attr_name:
        target_type = ""
        predicate = ""
    elif not target_type or not predicate:
        raise ValueError("关系映射需要同时填写目标类型和关系名。")
    conn.execute(
        "INSERT OR REPLACE INTO column_mappings(mount_name, table_name, column_name, target_type, predicate, attr_name) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (mount_name, table, column, target_type, predicate, attr_name),
    )
    conn.commit()


def list_column_mappings(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM column_mappings ORDER BY mount_name, table_name, column_name")]


def remove_column_mapping(conn: sqlite3.Connection, mount_name: str, table: str, column: str) -> bool:
    """删除一条列级映射，返回是否删除成功。"""
    cur = conn.execute(
        "DELETE FROM column_mappings WHERE mount_name=? AND table_name=? AND column_name=?",
        (mount_name, table, column),
    )
    conn.commit()
    return cur.rowcount > 0


def _find_or_create_entity(conn: sqlite3.Connection, name: str, type_: str) -> tuple[int, bool]:
    """按 (name, type) 查找实体，不存在则创建。返回 (entity_id, 是否新建)。"""
    row = conn.execute(
        "SELECT id FROM entities WHERE name=? AND type=? AND merged_into IS NULL",
        (name, type_),
    ).fetchone()
    if row:
        return row["id"], False
    return graph.add_entity(conn, name, type_), True


def _mounted_source(conn: sqlite3.Connection, mount_name: str) -> tuple[sqlite3.Row, object]:
    row = conn.execute(
        "SELECT m.path, COALESCE(s.source_kind, 'path') AS source_kind "
        "FROM mounted_dbs m LEFT JOIN mounted_source_metadata s ON s.mount_name=m.name WHERE m.name=?",
        (mount_name,),
    ).fetchone()
    if not row:
        raise ValueError(f"未挂载数据库：{mount_name}")
    bundled_sample = os.path.realpath(os.path.join(os.path.dirname(dbmod.DB_PATH), "sample_hr.db"))
    is_seed = row["source_kind"] == "seed" or os.path.realpath(row["path"]) == bundled_sample
    path = local_data.resolve_sqlite_path(row["path"], trusted_seed=is_seed)
    return row, path


def mounted_source_path(conn: sqlite3.Connection, mount_name: str):
    return _mounted_source(conn, mount_name)[1]


def _mapping_digest(table_mappings, column_mappings) -> str:
    import hashlib

    payload = {
        "tables": [dict(mapping) for mapping in table_mappings],
        "columns": [dict(mapping) for mapping in column_mappings],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def preview_materialization(conn: sqlite3.Connection, mount_name: str) -> dict:
    """Dry-run table/column mappings without mutating the graph."""
    _mount, path = _mounted_source(conn, mount_name)
    tables = [dict(row) for row in conn.execute("SELECT * FROM table_mappings WHERE mount_name=? ORDER BY table_name", (mount_name,))]
    columns = [dict(row) for row in conn.execute("SELECT * FROM column_mappings WHERE mount_name=? ORDER BY table_name, column_name", (mount_name,))]
    if not tables:
        raise ValueError(f"挂载 {mount_name} 无表映射，先声明映射。")
    source_hash = local_data.file_sha256(path)
    mapping_hash = _mapping_digest(tables, columns)
    snapshot_valid_from = date.today().strftime("%Y-%m-%d")
    columns_by_table: dict[str, list[dict]] = {}
    for mapping in columns:
        columns_by_table.setdefault(mapping["table_name"], []).append(mapping)

    max_rows = max(1, int(os.environ.get("UTOPIA_MATERIALIZE_MAX_ROWS", "10000")))
    errors: list[str] = []
    warnings: list[str] = []
    summary = {"rows": 0, "entities": 0, "facts": 0, "attributes": 0}
    table_results = []
    for mapping in tables:
        table = mapping["table_name"]
        key_column = mapping.get("key_col") or mapping["name_col"]
        mappings = columns_by_table.get(table, [])
        selected_columns = [mapping["name_col"], *[item["column_name"] for item in mappings]]
        schema = {item["name"]: item["columns"] for item in local_data.sqlite_schema(path)}
        if table not in schema:
            errors.append(f"数据表不存在：{table}")
            continue
        available = {item["name"] for item in schema[table]}
        missing = sorted({key_column, mapping["name_col"], *[item["column_name"] for item in mappings]} - available)
        if missing:
            errors.append(f"{table} 缺少列：{', '.join(missing)}")
            continue
        for item in mappings:
            if not (item["attr_name"] or "").strip() and (not item["target_type"] or not item["predicate"]):
                errors.append(f"{table}.{item['column_name']} 需要配置属性名，或配置目标类型与关系名。")
        rows = local_data.read_table_rows(path, table, selected_columns, key_column, max_rows)
        if len(rows) > max_rows:
            errors.append(f"{table} 超过单批物化上限 {max_rows} 行。")
            rows = rows[:max_rows]
        keys = [str(row[key_column]) for row in rows if row[key_column] is not None]
        duplicate_count = len(keys) - len(set(keys))
        null_keys = sum(row[key_column] is None for row in rows)
        null_names = sum(row[mapping["name_col"]] is None for row in rows)
        if duplicate_count:
            errors.append(f"{table}.{key_column} 有 {duplicate_count} 个重复源键。")
        if null_keys:
            errors.append(f"{table}.{key_column} 有 {null_keys} 个空源键。")
        if null_names:
            errors.append(f"{table}.{mapping['name_col']} 有 {null_names} 个空显示值。")
        target_values: set[tuple[str, str]] = set()
        row_facts = 0
        row_attributes = 0
        for row in rows:
            for item in mappings:
                value = row.get(item["column_name"])
                if value is None:
                    continue
                attr_name = (item["attr_name"] or "").strip()
                if attr_name:
                    row_attributes += 1
                else:
                    row_facts += 1
                    target_values.add((f"{table}.{item['column_name']}:{item['target_type']}", str(value)))
        table_stats = {
            "table": table,
            "key_column": key_column,
            "rows": len(rows),
            "entities": len(rows) + len(target_values),
            "facts": row_facts,
            "attributes": row_attributes,
            "duplicate_keys": duplicate_count,
            "null_keys": null_keys,
        }
        table_results.append(table_stats)
        for key in summary:
            summary[key] += table_stats[key]
    return {
        "mount_name": mount_name,
        "source_sha256": source_hash,
        "mapping_sha256": mapping_hash,
        "valid_from": snapshot_valid_from,
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": summary,
        "tables": table_results,
        "already_materialized": conn.execute(
            "SELECT 1 FROM materialization_batches WHERE mount_name=? AND source_sha256=? AND mapping_sha256=? AND status='complete'",
            (mount_name, source_hash, mapping_hash),
        ).fetchone() is not None,
    }


def materialize_mappings(conn: sqlite3.Connection, mount_name: str) -> dict:
    """Materialize one confirmed, immutable snapshot; identical snapshots are idempotent."""
    preview = preview_materialization(conn, mount_name)
    if not preview["ok"]:
        raise ValueError("无法物化：" + "；".join(preview["errors"]))
    if preview["already_materialized"]:
        previous = conn.execute(
            "SELECT id, stats_json FROM materialization_batches WHERE mount_name=? AND source_sha256=? AND mapping_sha256=? AND status='complete'",
            (mount_name, preview["source_sha256"], preview["mapping_sha256"]),
        ).fetchone()
        return {**json.loads(previous["stats_json"]), "batch_id": previous["id"], "already_materialized": True}

    _mount, path = _mounted_source(conn, mount_name)
    if local_data.file_sha256(path) != preview["source_sha256"]:
        raise ValueError("数据源在预览后发生变化，请重新预览。")
    tables = [dict(row) for row in conn.execute("SELECT * FROM table_mappings WHERE mount_name=? ORDER BY table_name", (mount_name,))]
    columns = [dict(row) for row in conn.execute("SELECT * FROM column_mappings WHERE mount_name=? ORDER BY table_name, column_name", (mount_name,))]
    columns_by_table: dict[str, list[dict]] = {}
    for mapping in columns:
        columns_by_table.setdefault(mapping["table_name"], []).append(mapping)

    conn.execute("SAVEPOINT materialize_local_snapshot")
    try:
        cursor = conn.execute(
            "INSERT INTO materialization_batches(mount_name, source_sha256, mapping_sha256, status) VALUES (?, ?, ?, 'running')",
            (mount_name, preview["source_sha256"], preview["mapping_sha256"]),
        )
        batch_id = cursor.lastrowid
        stats = {**preview["summary"], "entities_created": 0, "facts_created": 0, "attrs_created": 0, "valid_from": preview["valid_from"]}

        def get_or_create_source_entity(table_key: str, source_key: str, name: str, entity_type: str) -> int:
            existing = conn.execute(
                "SELECT entity_id FROM external_entity_keys WHERE mount_name=? AND table_name=? AND source_sha256=? AND mapping_sha256=? AND source_key=?",
                (mount_name, table_key, preview["source_sha256"], preview["mapping_sha256"], source_key),
            ).fetchone()
            if existing:
                return int(existing["entity_id"])
            entity_id = int(conn.execute(
                "INSERT INTO entities(name, type) VALUES (?, ?)", (name, entity_type)
            ).lastrowid)
            conn.execute(
                "INSERT INTO external_entity_keys(mount_name, table_name, source_sha256, mapping_sha256, source_key, entity_id) VALUES (?, ?, ?, ?, ?, ?)",
                (mount_name, table_key, preview["source_sha256"], preview["mapping_sha256"], source_key, entity_id),
            )
            stats["entities_created"] += 1
            return entity_id

        for table_mapping in tables:
            table = table_mapping["table_name"]
            key_column = table_mapping.get("key_col") or table_mapping["name_col"]
            field_mappings = columns_by_table.get(table, [])
            selected_columns = [table_mapping["name_col"], *[item["column_name"] for item in field_mappings]]
            rows = local_data.read_table_rows(path, table, selected_columns, key_column, int(os.environ.get("UTOPIA_MATERIALIZE_MAX_ROWS", "10000")))
            for row in rows:
                source_key = str(row[key_column])
                subject_id = get_or_create_source_entity(
                    table, source_key, str(row[table_mapping["name_col"]]), table_mapping["entity_type"]
                )
                for field_mapping in field_mappings:
                    value = row.get(field_mapping["column_name"])
                    if value is None:
                        continue
                    attr_name = (field_mapping["attr_name"] or "").strip()
                    if attr_name:
                        conn.execute(
                            "INSERT INTO entity_attributes(entity_id, key, value) VALUES (?, ?, ?) "
                            "ON CONFLICT(entity_id, key) DO UPDATE SET value=excluded.value",
                            (subject_id, attr_name, str(value)),
                        )
                        stats["attrs_created"] += 1
                        continue
                    target_type = field_mapping["target_type"]
                    predicate = field_mapping["predicate"]
                    target_key = f"{table}.{field_mapping['column_name']}:{target_type}"
                    object_id = get_or_create_source_entity(target_key, str(value), str(value), target_type)
                    conn.execute(
                        "INSERT INTO facts(subject_id, predicate, object_id, valid_from, source) VALUES (?, ?, ?, ?, ?)",
                        (subject_id, predicate, object_id, preview["valid_from"], f"sqlite_snapshot:{batch_id}:{table}.{field_mapping['column_name']}"),
                    )
                    stats["facts_created"] += 1

        if local_data.file_sha256(path) != preview["source_sha256"]:
            raise ValueError("数据源在物化期间发生变化，已取消导入。")
        conn.execute(
            "UPDATE materialization_batches SET status='complete', stats_json=? WHERE id=?",
            (json.dumps(stats, ensure_ascii=False), batch_id),
        )
        conn.execute("RELEASE SAVEPOINT materialize_local_snapshot")
        conn.commit()
        return {**stats, "batch_id": batch_id, "already_materialized": False}
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT materialize_local_snapshot")
        conn.execute("RELEASE SAVEPOINT materialize_local_snapshot")
        raise


def query_mounted(conn: sqlite3.Connection, mount_name: str, sql: str) -> list[dict]:
    """受限的只读 SELECT/CTE 查询。"""
    _mount, path = _mounted_source(conn, mount_name)
    result = local_data.execute_readonly(path, sql, row_limit=1001)
    return result["rows"]


def write_mounted(conn: sqlite3.Connection, mount_name: str, sql: str) -> int:
    """对挂载库执行写操作（INSERT/UPDATE/DELETE/DDL），返回受影响行数。"""
    row = conn.execute("SELECT path FROM mounted_dbs WHERE name=?", (mount_name,)).fetchone()
    if not row:
        raise ValueError(f"未挂载数据库：{mount_name}")
    mconn = sqlite3.connect(row["path"])  # 读写模式
    try:
        cur = mconn.execute(sql)
        mconn.commit()
        return cur.rowcount
    finally:
        mconn.close()


def schema_of(conn: sqlite3.Connection, mount_name: str) -> dict[str, list[str]]:
    """探查挂载库的表结构：{表名: [列名...]}，用于 NL→SQL 的 schema 上下文。"""
    _mount, path = _mounted_source(conn, mount_name)
    return {table["name"]: [column["name"] for column in table["columns"]] for table in local_data.sqlite_schema(path)}


def nl_to_sql(question: str, schema: dict, mappings: list[dict]) -> str:
    """用 LLM 把自然语言转成一条 SQL（未配置 LLM 时抛错）。"""
    if not llm.is_configured():
        raise ValueError("未配置 LLM，无法 NL→SQL（设 LLM_BASE_URL + LLM_MODEL）")
    prompt = _build_nl_prompt(question, schema, mappings)
    sql = llm.complete([{"role": "user", "content": prompt}]).strip()
    sql = re.sub(r"^```[a-z]*\s*|\s*```$", "", sql, flags=re.M).strip()
    return sql


def nl_query(conn: sqlite3.Connection, mount_name: str, question: str) -> dict:
    """NL → SQL → 只读查询。返回 {sql, rows}。"""
    schema = schema_of(conn, mount_name)
    mappings = _mappings_for(conn, mount_name)
    sql = nl_to_sql(question, schema, mappings)
    return {"sql": sql, "rows": query_mounted(conn, mount_name, sql)}


def nl_write(conn: sqlite3.Connection, mount_name: str, instruction: str) -> dict:
    """NL → SQL → 写入执行。返回 {sql, affected}。"""
    schema = schema_of(conn, mount_name)
    mappings = _mappings_for(conn, mount_name)
    sql = nl_to_sql(instruction, schema, mappings)
    return {"sql": sql, "affected": write_mounted(conn, mount_name, sql)}


def _mappings_for(conn: sqlite3.Connection, mount_name: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM table_mappings WHERE mount_name=? ORDER BY table_name", (mount_name,)
        )
    ]


def _build_nl_prompt(question: str, schema: dict, mappings: list[dict]) -> str:
    schema_text = "\n".join(f"表 {t}({', '.join(cols)})" for t, cols in schema.items())
    mapping_text = "\n".join(
        f"{m['table_name']} → 实体类型 {m['entity_type']}（名称列 {m['name_col']}）" for m in mappings
    )
    return (
        "你是 SQL 专家。根据数据库 schema 和表→本体映射，把自然语言请求转成一条 SQLite SQL 语句。"
        "只输出 SQL 本身，不要任何解释、注释或 markdown 代码块。\n\n"
        f"【数据库 schema】\n{schema_text}\n\n"
        f"【表→本体映射】\n{mapping_text}\n\n"
        f"【自然语言请求】{question}\n\n"
        "【SQL】"
    )
