"""图谱层：实体 / 事实 CRUD + 双时态（bitemporal）查询。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def list_entities(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM entities ORDER BY id").fetchall()


def add_entity(conn: sqlite3.Connection, name: str, type_: str = "Thing") -> int:
    cur = conn.execute("INSERT INTO entities(name, type) VALUES (?, ?)", (name, type_))
    conn.commit()
    return cur.lastrowid


def add_fact(
    conn: sqlite3.Connection,
    subject_id: int,
    predicate: str,
    object_id: int,
    valid_from: str,
    valid_to: str | None = None,
    source: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO facts(subject_id, predicate, object_id, valid_from, valid_to, source) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (subject_id, predicate, object_id, valid_from, valid_to, source),
    )
    conn.commit()
    return cur.lastrowid


def correct_fact(
    conn: sqlite3.Connection,
    fact_id: int,
    new_object_id: int,
    note: str = "",
) -> int:
    """双时态纠错：关闭旧事实并链接新事实，而非覆盖。

    - 旧事实：status → 'superseded'，valid_to 设为当前时刻，superseded_by → 新 id
    - 新事实：asserted_at = 当前时刻，source 记录「更正自 #旧id」
    这样图谱保留了「系统曾经相信过旧值」这条认知历史。
    """
    old = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    if old is None or old["status"] != "active":
        raise ValueError(f"事实 #{fact_id} 不存在或已失效")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    cur = conn.execute(
        "INSERT INTO facts(subject_id, predicate, object_id, valid_from, valid_to, asserted_at, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            old["subject_id"],
            old["predicate"],
            new_object_id,
            now,
            old["valid_to"],
            now,
            f"correction of #{fact_id}" + (f": {note}" if note else ""),
        ),
    )
    new_id = cur.lastrowid
    conn.execute(
        "UPDATE facts SET status = 'superseded', valid_to = ?, superseded_by = ? WHERE id = ?",
        (now, new_id, fact_id),
    )
    conn.commit()
    return new_id


def active_facts(
    conn: sqlite3.Connection,
    as_of: str | None = None,
    believed_at: str | None = None,
) -> list[sqlite3.Row]:
    """查询「当前有效」的事实，可选两条时间线过滤：

    - as_of：真实世界时间线 —— 事实在该日期是否成立
    - believed_at：系统认知时间线 —— 系统在该时刻是否已记录
    二者独立，正是双时态与普通「单时间戳」的区别。
    """
    q = "SELECT * FROM facts WHERE status = 'active'"
    params: list[str] = []
    if as_of:
        q += " AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)"
        params += [as_of, as_of]
    if believed_at:
        q += " AND asserted_at <= ?"
        params.append(believed_at)
    return conn.execute(q, params).fetchall()


def entity_facts(conn: sqlite3.Connection, entity_id: int, as_of: str | None = None) -> list[sqlite3.Row]:
    """某实体（作为主语）的事实，含被取代的历史链。"""
    q = (
        "SELECT f.*, o.name AS object_name "
        "FROM facts f JOIN entities o ON f.object_id = o.id "
        "WHERE f.subject_id = ?"
    )
    params: list = [entity_id]
    if as_of:
        q += " AND f.valid_from <= ? AND (f.valid_to IS NULL OR f.valid_to > ?)"
        params += [as_of, as_of]
    q += " ORDER BY f.id"
    return conn.execute(q, params).fetchall()


def set_fact_attribute(conn: sqlite3.Connection, fact_id: int, key: str, value: str) -> None:
    """边物化：给事实（边）设置一个属性键值对（如 since/confidence/role）。"""
    conn.execute(
        "INSERT INTO fact_attributes(fact_id, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(fact_id, key) DO UPDATE SET value=excluded.value",
        (fact_id, key, value),
    )
    conn.commit()


def get_fact_attributes(conn: sqlite3.Connection, fact_id: int) -> dict[str, str]:
    """读取某事实（边）的全部属性。"""
    return {
        r["key"]: r["value"]
        for r in conn.execute("SELECT key, value FROM fact_attributes WHERE fact_id=?", (fact_id,)).fetchall()
    }


def set_entity_attribute(conn: sqlite3.Connection, entity_id: int, key: str, value: str) -> None:
    """实体属性：给实体（节点）设置一个属性键值对（如 salary=30000）。"""
    conn.execute(
        "INSERT INTO entity_attributes(entity_id, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(entity_id, key) DO UPDATE SET value=excluded.value",
        (entity_id, key, value),
    )
    conn.commit()


def get_entity_attributes(conn: sqlite3.Connection, entity_id: int) -> dict[str, str]:
    """读取某实体（节点）的全部属性。"""
    return {
        r["key"]: r["value"]
        for r in conn.execute("SELECT key, value FROM entity_attributes WHERE entity_id=?", (entity_id,)).fetchall()
    }
