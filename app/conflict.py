"""冲突检测与处置：断言事实相互矛盾时的三种选择。

对应 Utopia 的 conflict detection：
    「A new fact that clashes with an older one: close the old, keep both, or reject the new.」

「矛盾」的定义：同一 (subject, predicate) 上存在 object 不同、且有效区间重叠的
**断言事实**（derived=0）。这对应「函数型关系」——同一主体同一谓词在重叠时段内
只能有一个值，例如 status / capital_of / ceo_of。

三种处置（Utopia 原话的三选一）：
- close  —— 关闭旧事实（旧事实 superseded，新事实生效）
- keep   —— 两者都保留，同时把冲突登记下来（可追溯）
- reject —— 拒绝新事实（不写入，旧事实保持不变）

每次处置都写入 conflicts 表，形成可审计的「决策账本」（对应 Utopia 的 decision ledger）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

RESOLUTIONS = ("close", "keep", "reject")


def detect(
    conn: sqlite3.Connection,
    subject_id: int,
    predicate: str,
    object_id: int,
    valid_from: str,
    valid_to: str | None = None,
) -> list[sqlite3.Row]:
    """检测冲突：返回与新事实矛盾的 active 断言事实列表（不改动数据）。"""
    end = valid_to or "9999-12-31"  # 开放区间用远未来哨兵
    return conn.execute(
        "SELECT * FROM facts "
        "WHERE status='active' AND derived=0 "
        "  AND subject_id=? AND predicate=? AND object_id<>? "
        "  AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?) "
        "ORDER BY id",
        (subject_id, predicate, object_id, end, valid_from),
    ).fetchall()


def resolve(
    conn: sqlite3.Connection,
    new_fact: dict,
    resolution: str,
    note: str = "",
) -> dict:
    """按 resolution 处置冲突，返回摘要。

    new_fact 需含：subject_id, predicate, object_id, valid_from, valid_to(可选), source(可选)
    resolution ∈ {"close", "keep", "reject"}
    """
    if resolution not in RESOLUTIONS:
        raise ValueError(f"resolution 必须是 {RESOLUTIONS} 之一，收到 {resolution!r}")

    conflicts = detect(
        conn,
        new_fact["subject_id"],
        new_fact["predicate"],
        new_fact["object_id"],
        new_fact["valid_from"],
        new_fact.get("valid_to"),
    )

    # reject：不写新事实，只登记冲突
    if resolution == "reject":
        for c in conflicts:
            _record(conn, new_fact["subject_id"], new_fact["predicate"], c["id"], None, "reject", note)
        conn.commit()
        return {"resolution": "reject", "new_fact_id": None, "conflicts": [c["id"] for c in conflicts]}

    # close / keep 都要写入新事实
    new_id = _insert(conn, new_fact)

    if resolution == "close":
        now = _now()
        for c in conflicts:
            conn.execute(
                "UPDATE facts SET status='superseded', valid_to=?, superseded_by=? WHERE id=?",
                (now, new_id, c["id"]),
            )
            _record(conn, new_fact["subject_id"], new_fact["predicate"], c["id"], new_id, "close", note)
    else:  # keep：旧事实保持 active，与新事实并存
        for c in conflicts:
            _record(conn, new_fact["subject_id"], new_fact["predicate"], c["id"], new_id, "keep", note)

    conn.commit()
    return {"resolution": resolution, "new_fact_id": new_id, "conflicts": [c["id"] for c in conflicts]}


def list_conflicts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM conflicts ORDER BY id").fetchall()


def _insert(conn: sqlite3.Connection, f: dict) -> int:
    cur = conn.execute(
        "INSERT INTO facts(subject_id, predicate, object_id, valid_from, valid_to, source) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (f["subject_id"], f["predicate"], f["object_id"], f["valid_from"], f.get("valid_to"), f.get("source")),
    )
    return cur.lastrowid


def _record(
    conn: sqlite3.Connection,
    subject_id: int,
    predicate: str,
    fact_old: int,
    fact_new: int | None,
    resolution: str,
    note: str,
) -> None:
    conn.execute(
        "INSERT INTO conflicts(subject_id, predicate, fact_old, fact_new, resolution, note) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (subject_id, predicate, fact_old, fact_new, resolution, note),
    )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
