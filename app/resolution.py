"""实体消解（entity resolution）：三阶段去重 + 可撤销合并。

对应 Utopia 的 entity resolution：
    「Duplicates are resolved in three stages: exact name or alias, embedding
    similarity, then a model's call on the doubtful pairs. Every merge can be undone.」

三阶段：
- stage 1 精确：同名（不区分大小写）或别名命中 → exact
- stage 2 嵌入：实体名嵌入余弦相似度 ≥ threshold → embedding（高置信）
- stage 3 存疑：相似度介于 low ~ threshold 之间 → doubtful（交模型/人工裁决）

合并可撤销：merge_entities 记录被移动的事实（JSON），undo_merge 逆操作还原。
"""

from __future__ import annotations

import json
import math
import sqlite3

from .embeddings import embed


def add_alias(conn: sqlite3.Connection, entity_id: int, alias: str) -> None:
    conn.execute("INSERT OR IGNORE INTO aliases(entity_id, alias) VALUES (?, ?)", (entity_id, alias))
    conn.commit()


def find_duplicates(conn: sqlite3.Connection, threshold: float = 0.8, low: float = 0.5) -> dict:
    """三阶段检测重复实体，返回 {exact, embedding, doubtful} 三组候选对。"""
    result: dict = {"exact": [], "embedding": [], "doubtful": []}
    entities = conn.execute(
        "SELECT id, name, type FROM entities WHERE merged_into IS NULL ORDER BY id"
    ).fetchall()

    seen: set[tuple[int, int]] = set()

    # stage 1a：同名
    by_name: dict[str, list[int]] = {}
    for e in entities:
        by_name.setdefault(e["name"].lower(), []).append(e["id"])
    for key, ids in by_name.items():
        if len(ids) > 1:
            result["exact"].append({"ids": sorted(ids), "reason": "same_name"})
            for a in ids:
                for b in ids:
                    if a != b:
                        seen.add((min(a, b), max(a, b)))

    # stage 1b：别名命中
    alias_map: dict[str, list[int]] = {}
    for r in conn.execute("SELECT alias, entity_id FROM aliases"):
        alias_map.setdefault(r["alias"].lower(), []).append(r["entity_id"])
    for e in entities:
        for other in alias_map.get(e["name"].lower(), []):
            if other == e["id"]:
                continue
            pair = (min(e["id"], other), max(e["id"], other))
            if pair not in seen:
                result["exact"].append({"ids": list(pair), "reason": "alias"})
                seen.add(pair)

    # stage 2/3：嵌入相似度
    vecs = [(e["id"], e["name"], embed(e["name"])) for e in entities]
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            id1, _, v1 = vecs[i]
            id2, _, v2 = vecs[j]
            pair = (id1, id2)
            if pair in seen:
                continue
            s = _cos(v1, v2)
            if s >= threshold:
                result["embedding"].append({"ids": list(pair), "score": round(s, 4)})
            elif s >= low:
                result["doubtful"].append({"ids": list(pair), "score": round(s, 4)})
    return result


def merge_entities(conn: sqlite3.Connection, keep_id: int, merge_id: int, method: str = "manual") -> dict:
    """把 merge_id 合并进 keep_id：移动所有事实、标记 merged_into，并记录以便撤销。"""
    moved: list[dict] = []
    for f in conn.execute("SELECT id FROM facts WHERE subject_id=?", (merge_id,)):
        moved.append({"fact_id": f["id"], "role": "subject"})
    for f in conn.execute("SELECT id FROM facts WHERE object_id=?", (merge_id,)):
        moved.append({"fact_id": f["id"], "role": "object"})

    conn.execute("UPDATE facts SET subject_id=? WHERE subject_id=?", (keep_id, merge_id))
    conn.execute("UPDATE facts SET object_id=? WHERE object_id=?", (keep_id, merge_id))
    conn.execute("UPDATE entities SET merged_into=? WHERE id=?", (keep_id, merge_id))
    cur = conn.execute(
        "INSERT INTO entity_merges(kept_id, merged_id, method, moved_facts) VALUES (?, ?, ?, ?)",
        (keep_id, merge_id, method, json.dumps(moved)),
    )
    conn.commit()
    return {"merge_id": cur.lastrowid, "kept_id": keep_id, "merged_id": merge_id, "moved": len(moved)}


def undo_merge(conn: sqlite3.Connection, merge_record_id: int) -> dict:
    """撤销合并：按记录还原被移动的事实，解除 merged_into 标记。"""
    rec = conn.execute("SELECT * FROM entity_merges WHERE id=?", (merge_record_id,)).fetchone()
    if not rec:
        raise ValueError(f"合并记录 #{merge_record_id} 不存在")
    for m in json.loads(rec["moved_facts"]):
        if m["role"] == "subject":
            conn.execute("UPDATE facts SET subject_id=? WHERE id=?", (rec["merged_id"], m["fact_id"]))
        else:
            conn.execute("UPDATE facts SET object_id=? WHERE id=?", (rec["merged_id"], m["fact_id"]))
    conn.execute("UPDATE entities SET merged_into=NULL WHERE id=?", (rec["merged_id"],))
    conn.execute("DELETE FROM entity_merges WHERE id=?", (merge_record_id,))
    conn.commit()
    return {"undone": merge_record_id, "restored_entity": rec["merged_id"]}


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
