"""推理层：传递性推导（前向链 forward chaining）。

对应 Utopia 的 utopia-reason：本体公理编译成规则，前向链推导新事实。
这里只演示最经典的一条公理 —— 传递性（transitivity）：

    R(a, b) ∧ R(b, c)  ⇒  R(a, c)

例如：上海 located_in 中国，中国 located_in 亚洲 ⇒ 上海 located_in 亚洲。

推导事实会被**物化**到 facts 表，并标记 derived=1、derived_from 指向两条前提
事实的 id —— 正如 Utopia「推导事实在图上一并标记、并显示它从何推导而来」。
推导默认关闭，按需调用（对应原版「错误公理会推导出错误事实，所以默认关闭」）。
"""

from __future__ import annotations

import sqlite3

# 本体公理（轻量版）：每个谓词的规则标志
#   transitive  —— 传递：R(a,b) ∧ R(b,c) ⇒ R(a,c)
#   asymmetric  —— 反对称：不能同时有 R(a,b) 和 R(b,a)
#   irreflexive —— 非自反：不允许 R(a,a)（自环）
# 对应 Utopia 的 ontology axioms 编译成规则。
AXIOMS: dict[str, dict] = {
    "located_in": {"transitive": True, "asymmetric": True, "irreflexive": True},
    "part_of":    {"transitive": True, "asymmetric": True, "irreflexive": True},
    "reports_to": {"transitive": True, "asymmetric": True, "irreflexive": True},
}


def derive_transitive(conn: sqlite3.Connection, predicates: set[str] | None = None) -> int:
    """对传递关系做前向链推导，直到不动点（fixpoint）。返回新推导的事实数。"""
    preds = predicates or _transitive_predicates()
    if not preds:
        return 0
    placeholders = ",".join("?" for _ in preds)

    total = 0
    while True:
        newly = 0
        facts = conn.execute(
            f"SELECT id, subject_id, predicate, object_id, valid_from, valid_to "
            f"FROM facts WHERE status='active' AND predicate IN ({placeholders})",
            list(preds),
        ).fetchall()

        # 按 (predicate, subject) 建索引，找「a→b」与「b→c」的衔接
        by_subject: dict[str, dict[int, list[sqlite3.Row]]] = {}
        for f in facts:
            by_subject.setdefault(f["predicate"], {}).setdefault(f["subject_id"], []).append(f)

        for f in facts:
            for g in by_subject.get(f["predicate"], {}).get(f["object_id"], []):
                s, o = f["subject_id"], g["object_id"]
                if s == o:  # 避免自环
                    continue
                if _exists(conn, f["predicate"], s, o):  # 去重
                    continue

                # 推导事实的成立区间 = 两条前提的区间交集
                valid_from = max(f["valid_from"], g["valid_from"])
                valid_to = _min_open(f["valid_to"], g["valid_to"])

                conn.execute(
                    "INSERT INTO facts(subject_id, predicate, object_id, valid_from, valid_to, "
                    "source, derived, derived_from) VALUES (?, ?, ?, ?, ?, 'derived', 1, ?)",
                    (s, f["predicate"], o, valid_from, valid_to, f"{f['id']},{g['id']}"),
                )
                newly += 1

        total += newly
        if newly == 0:
            break
    conn.commit()
    return total


def _exists(conn: sqlite3.Connection, predicate: str, s: int, o: int) -> bool:
    r = conn.execute(
        "SELECT 1 FROM facts WHERE predicate=? AND subject_id=? AND object_id=? AND status='active' LIMIT 1",
        (predicate, s, o),
    ).fetchone()
    return r is not None


def _min_open(a: str | None, b: str | None) -> str | None:
    """区间上界取交集：NULL 表示开放（无终点）。"""
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def retract_derived(conn: sqlite3.Connection, fact_id: int, reason: str = "") -> int:
    """级联撤销：撤回所有（直接或间接）依赖 fact_id 的推导事实。

    前提事实被纠错/失效后，任何 derived_from 里包含它的推导事实都不再成立；
    而这些推导事实本身又可能被更上游的推导事实依赖，所以用 BFS 级联传播。
    对应 Utopia 的 retraction：断言事实优先，依赖它的推导随之撤回。

    返回被撤回的推导事实数。
    """
    # 1) 建立「前提 id → 依赖它的推导事实 id」索引（只看仍 active 的推导事实）
    children: dict[int, list[int]] = {}
    rows = conn.execute(
        "SELECT id, derived_from FROM facts WHERE status='active' AND derived=1"
    ).fetchall()
    for r in rows:
        for pid in _parse_ids(r["derived_from"]):
            children.setdefault(pid, []).append(r["id"])

    # 2) BFS 级联传播
    retracted = 0
    queue = [fact_id]
    seen: set[int] = set()
    while queue:
        pid = queue.pop(0)
        for child in children.get(pid, []):
            if child in seen:
                continue
            seen.add(child)
            conn.execute(
                "UPDATE facts SET status='retracted', retracted_because=? WHERE id=?",
                (reason, child),
            )
            retracted += 1
            queue.append(child)
    conn.commit()
    return retracted


def _parse_ids(derived_from: str | None) -> list[int]:
    if not derived_from:
        return []
    return [int(x) for x in derived_from.split(",") if x.strip()]


def _transitive_predicates() -> set[str]:
    return {p for p, a in AXIOMS.items() if a.get("transitive")}


# ---------- 公理违反检测（对应 Utopia 冲突检测的第二类） ----------

def detect_axiom_violations(conn: sqlite3.Connection) -> list[dict]:
    """检测数据违反公理：自环（irreflexive）、反对称冲突、传递环。

    Utopia 原话：「Data that breaks an axiom (self-loop, asymmetry, transitive cycle,
    cardinality): retract the fact, relax the axiom, or accept both.」
    这里实现前三类结构违规；基数（cardinality/函数型）已由 conflict.py 的「事实矛盾」覆盖。
    """
    violations: list[dict] = []
    for pred, axioms in AXIOMS.items():
        facts = conn.execute(
            "SELECT id, subject_id, predicate, object_id FROM facts "
            "WHERE status='active' AND predicate=? ORDER BY id",
            (pred,),
        ).fetchall()
        if not facts:
            continue

        if axioms.get("irreflexive"):
            for f in facts:
                if f["subject_id"] == f["object_id"]:
                    violations.append({
                        "type": "self_loop", "predicate": pred, "fact_id": f["id"],
                        "subject": f["subject_id"], "object": f["object_id"],
                    })

        if axioms.get("asymmetric"):
            seen: dict[tuple, int] = {}
            for f in facts:
                rev = (f["object_id"], f["subject_id"])
                if rev in seen:
                    violations.append({
                        "type": "asymmetry", "predicate": pred,
                        "fact_id": f["id"], "other_id": seen[rev],
                        "subject": f["subject_id"], "object": f["object_id"],
                    })
                seen[(f["subject_id"], f["object_id"])] = f["id"]

        if axioms.get("transitive") and axioms.get("irreflexive"):
            cycle = _find_cycle(facts)
            if cycle:
                violations.append({
                    "type": "transitive_cycle", "predicate": pred,
                    "fact_id": None, "cycle": cycle,
                })

    return violations


def resolve_axiom_violation(conn: sqlite3.Connection, fact_id: int, choice: str, note: str = "") -> dict:
    """处置公理违反（Utopia 三选一）：

    - retract —— 撤回违规事实（status → retracted）
    - accept  —— 接受两者并存（只登记，不改数据）
    - relax   —— 放宽公理（只登记；公理在代码 AXIOMS 里，实际放宽需改代码）

    三者都写入 axiom_violations 表，形成可审计记录。
    """
    if choice not in ("retract", "accept", "relax"):
        raise ValueError(f"choice 必须是 retract/accept/relax，收到 {choice!r}")
    if choice == "retract":
        conn.execute(
            "UPDATE facts SET status='retracted', retracted_because=? WHERE id=?",
            (f"axiom violation: {note}" if note else "axiom violation", fact_id),
        )
    conn.execute(
        "INSERT INTO axiom_violations(fact_id, resolution, note) VALUES (?, ?, ?)",
        (fact_id, choice, note),
    )
    conn.commit()
    return {"fact_id": fact_id, "resolution": choice}


def _find_cycle(facts: list[sqlite3.Row]) -> list[int] | None:
    """在有向边（忽略自环）中找一个环，返回环的节点序列或 None。"""
    adj: dict[int, list[int]] = {}
    for f in facts:
        if f["subject_id"] != f["object_id"]:
            adj.setdefault(f["subject_id"], []).append(f["object_id"])

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[int, int] = {}
    stack: list[int] = []

    def dfs(u: int) -> list[int] | None:
        color[u] = GRAY
        stack.append(u)
        for v in adj.get(u, []):
            if color.get(v, WHITE) == GRAY:
                idx = stack.index(v)
                return stack[idx:] + [v]
            if color.get(v, WHITE) == WHITE:
                r = dfs(v)
                if r:
                    return r
        stack.pop()
        color[u] = BLACK
        return None

    for n in list(adj):
        if color.get(n, WHITE) == WHITE:
            r = dfs(n)
            if r:
                return r
    return None
