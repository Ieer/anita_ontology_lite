"""本体：类型层级（轻量版，对应 utopia-core 的 ontology + cold start pack）。

原版内置 schema.org / PROV-O / FOAF 等 pack；这里用一张 `types` 表 + `subclass_of`
关系表达类型层级，并支持**传递闭包的类型推断**：实体声明了 Person，若 Person ⊑ Agent
⊑ Thing，则实体也属于 Agent 和 Thing（类型向上传播）。

    ontology.add_type(conn, "Agent")
    ontology.add_subclass(conn, "Person", "Agent")
    ontology.supertypes(conn, "Person")              # -> {"Agent", "Thing", ...}
    ontology.infer_entity_types(conn, entity_id)      # 声明类型 + 所有超类
"""

from __future__ import annotations

import sqlite3


def add_type(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("INSERT OR IGNORE INTO types(name) VALUES (?)", (name,))
    conn.commit()


def add_subclass(conn: sqlite3.Connection, child: str, parent: str) -> None:
    """声明 child ⊑ parent（child 是 parent 的子类）。"""
    add_type(conn, child)
    add_type(conn, parent)
    conn.execute("INSERT OR IGNORE INTO subclass_of(child, parent) VALUES (?, ?)", (child, parent))
    conn.commit()


def list_types(conn: sqlite3.Connection) -> list[str]:
    """列出所有已注册类型（按名称排序）。"""
    return [r["name"] for r in conn.execute("SELECT name FROM types ORDER BY name")]


def list_subclasses(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """列出所有 child ⊑ parent 关系。"""
    return [
        (r["child"], r["parent"])
        for r in conn.execute("SELECT child, parent FROM subclass_of ORDER BY child, parent")
    ]


def remove_type(conn: sqlite3.Connection, name: str) -> tuple[bool, str]:
    """删除类型。若仍被实体引用则拒绝；否则连同其子类关系一起删除。
    返回 (是否成功, 提示信息)。"""
    cnt = conn.execute("SELECT COUNT(*) AS c FROM entities WHERE type=?", (name,)).fetchone()["c"]
    if cnt:
        return False, f"类型 {name} 仍被 {cnt} 个实体引用，无法删除"
    conn.execute("DELETE FROM subclass_of WHERE child=? OR parent=?", (name, name))
    cur = conn.execute("DELETE FROM types WHERE name=?", (name,))
    conn.commit()
    if cur.rowcount == 0:
        return False, f"类型 {name} 不存在"
    return True, f"已删除类型 {name}"


def remove_subclass(conn: sqlite3.Connection, child: str, parent: str) -> tuple[bool, str]:
    """删除一条 child ⊑ parent 关系。返回 (是否成功, 提示信息)。"""
    cur = conn.execute("DELETE FROM subclass_of WHERE child=? AND parent=?", (child, parent))
    conn.commit()
    if cur.rowcount == 0:
        return False, f"关系 {child} ⊑ {parent} 不存在"
    return True, f"已删除关系 {child} ⊑ {parent}"


# ---- 类型等价（同义）：Person ≡ Human ----

def _canonical(a: str, b: str) -> tuple[str, str]:
    """按字典序规范化，保证 (A,B) 与 (B,A) 只存一份。"""
    return (a, b) if a <= b else (b, a)


def add_type_equivalence(conn: sqlite3.Connection, type_a: str, type_b: str) -> tuple[bool, str]:
    """声明两个类型等价（同义）：type_a ≡ type_b。返回 (是否成功, 提示信息)。
    一致性校验：拒绝与已有子类层级矛盾的等价（等价=同概念，子类=更具体，二者互斥）。"""
    a = type_a.strip()
    b = type_b.strip()
    if not a or not b:
        return False, "两个类型名不能为空"
    if a == b:
        return False, "同一类型无需声明等价"
    if a in _raw_supertypes(conn, b) or b in _raw_supertypes(conn, a):
        return False, f"矛盾：{a} 与 {b} 已存在子类关系，声明等价会破坏层级一致性"
    add_type(conn, a)
    add_type(conn, b)
    ca, cb = _canonical(a, b)
    conn.execute("INSERT OR IGNORE INTO type_equivalences(type_a, type_b) VALUES (?, ?)", (ca, cb))
    conn.commit()
    return True, f"已声明等价：{a} ≡ {b}"


def remove_type_equivalence(conn: sqlite3.Connection, type_a: str, type_b: str) -> tuple[bool, str]:
    """删除一条类型等价关系。返回 (是否成功, 提示信息)。"""
    ca, cb = _canonical(type_a.strip(), type_b.strip())
    cur = conn.execute("DELETE FROM type_equivalences WHERE type_a=? AND type_b=?", (ca, cb))
    conn.commit()
    if cur.rowcount == 0:
        return False, f"等价关系 {type_a} ≡ {type_b} 不存在"
    return True, f"已删除等价：{type_a} ≡ {type_b}"


def list_type_equivalences(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """列出所有类型等价关系。"""
    return [
        (r["type_a"], r["type_b"])
        for r in conn.execute("SELECT type_a, type_b FROM type_equivalences ORDER BY type_a, type_b")
    ]


def equivalent_types(conn: sqlite3.Connection, name: str) -> set[str]:
    """求 name 的等价闭包（含 name 自身）。等价关系对称且传递（BFS）。"""
    seen: set[str] = {name}
    queue = [name]
    while queue:
        cur = queue.pop(0)
        rows = conn.execute(
            "SELECT type_a, type_b FROM type_equivalences WHERE type_a=? OR type_b=?",
            (cur, cur),
        ).fetchall()
        for r in rows:
            other = r["type_b"] if r["type_a"] == cur else r["type_a"]
            if other not in seen:
                seen.add(other)
                queue.append(other)
    return seen


def add_subclass_checked(conn: sqlite3.Connection, child: str, parent: str) -> tuple[bool, str]:
    """带环检测地声明 child ⊑ parent：拒绝自环与传递环。返回 (是否成功, 提示信息)。
    成环条件：child 已经是 parent 的超类（即 parent 沿层级向上能到达 child）。"""
    child = child.strip()
    parent = parent.strip()
    if not child or not parent:
        return False, "子类和父类不能为空"
    if child == parent:
        return False, f"自环：{child} ⊑ {child} 不允许"
    if child in supertypes(conn, parent):
        return False, f"会成环：{parent} 已是 {child} 的后代，添加 {child} ⊑ {parent} 会形成环"
    add_subclass(conn, child, parent)
    return True, f"已添加：{child} ⊑ {parent}"


def rename_type(conn: sqlite3.Connection, old: str, new: str) -> tuple[bool, str]:
    """重命名类型，级联更新 types / subclass_of / entities 中的引用。
    返回 (是否成功, 提示信息)。"""
    old = old.strip()
    new = new.strip()
    if not old or not new:
        return False, "新旧名称不能为空"
    if old == new:
        return False, "新旧名称相同，无需重命名"
    if not conn.execute("SELECT 1 FROM types WHERE name=?", (old,)).fetchone():
        return False, f"类型 {old} 不存在"
    if conn.execute("SELECT 1 FROM types WHERE name=?", (new,)).fetchone():
        return False, f"类型 {new} 已存在"
    conn.execute("UPDATE types SET name=? WHERE name=?", (new, old))
    conn.execute("UPDATE subclass_of SET child=? WHERE child=?", (new, old))
    conn.execute("UPDATE subclass_of SET parent=? WHERE parent=?", (new, old))
    conn.execute("UPDATE entities SET type=? WHERE type=?", (new, old))
    conn.execute("UPDATE type_equivalences SET type_a=? WHERE type_a=?", (new, old))
    conn.execute("UPDATE type_equivalences SET type_b=? WHERE type_b=?", (new, old))
    conn.commit()
    return True, f"已将 {old} 重命名为 {new}"


def detect_cycles(conn: sqlite3.Connection) -> list[list[str]]:
    """检测类型层级中的环，含「等价 + 子类」组合环。
    先把等价闭包压缩成代表节点（等价类型视为同一节点），再在压缩图上用 DFS
    三色检测子类环。等价类型之间存在子类边（压缩后自环）也视为矛盾环。"""
    types = list_types(conn)
    # 等价闭包压缩：每个类型映射到等价类代表（字典序最小）
    rep: dict[str, str] = {}
    for t in types:
        rep[t] = min(equivalent_types(conn, t))
    # 压缩图上建子类边；等价后自环 → 矛盾
    adj: dict[str, set[str]] = {}
    cycles: list[list[str]] = []
    for child, parent in list_subclasses(conn):
        rc, rp = rep.get(child, child), rep.get(parent, parent)
        if rc == rp:
            cycles.append([child, parent, child])  # 等价 + 子类矛盾
            continue
        adj.setdefault(rc, set()).add(rp)
    # DFS 三色检测压缩图环
    color: dict[str, int] = {}
    stack: list[str] = []

    def dfs(node: str) -> None:
        color[node] = 1
        stack.append(node)
        for nxt in adj.get(node, []):
            if color.get(nxt, 0) == 1:
                idx = stack.index(nxt)
                cycles.append(stack[idx:] + [nxt])
            elif color.get(nxt, 0) == 0:
                dfs(nxt)
        stack.pop()
        color[node] = 2

    for n in list(adj):
        if color.get(n, 0) == 0:
            dfs(n)
    return cycles


def supertypes(conn: sqlite3.Connection, name: str) -> set[str]:
    """等价感知的超类：name 及其等价类型的所有超类并集（不含 name 及等价类型自身）。
    等价类型共享子类层级约束——若 Human ≡ Person ⊑ Agent，则 Human 的超类也含 Agent。"""
    equiv = equivalent_types(conn, name)
    result: set[str] = set()
    for t in equiv:
        result |= _raw_supertypes(conn, t)
    result -= equiv  # 等价类型自身不算超类
    return result


def _raw_supertypes(conn: sqlite3.Connection, name: str) -> set[str]:
    """纯子类层级的超类（不考虑等价），BFS 传递闭包，不含 name 自身。"""
    seen: set[str] = set()
    queue = [name]
    while queue:
        cur = queue.pop(0)
        for r in conn.execute("SELECT parent FROM subclass_of WHERE child=?", (cur,)):
            p = r["parent"]
            if p not in seen:
                seen.add(p)
                queue.append(p)
    return seen


def infer_entity_types(conn: sqlite3.Connection, entity_id: int) -> set[str]:
    """实体的声明类型 + 等价类型（同义闭包）+ 等价感知的所有超类。"""
    row = conn.execute("SELECT type FROM entities WHERE id=?", (entity_id,)).fetchone()
    if not row:
        return set()
    declared = row["type"]
    return equivalent_types(conn, declared) | supertypes(conn, declared)
