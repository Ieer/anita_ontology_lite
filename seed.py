"""写入演示数据：若干实体、事实（含一次双时态纠错）、几篇文档。

运行：python seed.py
"""

from __future__ import annotations

from app import db, graph, ontology, ontosql, search

DOCS = [
    (
        "DeepLethe 公司简介",
        "DeepLethe 是一家专注企业知识工程的公司。它构建的知识底座把本体、双时态图谱、"
        "冲突检测与可审计的决策账本做在底层，让企业能在自己控制的硬件上离线部署。"
        "团队相信知识系统应当像科学史一样，记录理解是如何一步步演进的，而不是只保存当下正确的结论。",
    ),
    (
        "Utopia 产品说明",
        "Utopia 是 DeepLethe 的企业世界模型，一个 Rust 二进制加一个 Postgres 即可运行。"
        "它支持 PDF、DOCX、PPTX、XLSX、CSV 等文档摄入，全文用 Tantivy、向量用 pgvector，"
        "并用 RRF 融合两路召回，回答时带行内引用。本体公理会被编译成规则做前向链推理。",
    ),
    (
        "Alice 的履历",
        "Alice 于 2020 年加入 DeepLethe，2021 年创立了 Utopia 项目。她长期研究知识表示与"
        "时间数据库，主张用双时态模型记录企业知识的变迁历史。她的工作重点是本体工程与推理规则。",
    ),
]


def main(reset: bool = False) -> None:
    import os

    if reset:
        for suffix in ("", "-wal", "-shm"):
            p = db.DB_PATH + suffix
            if os.path.exists(p):
                os.remove(p)

    conn = db.init_db()
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    state_tables = (
        "entities",
        "facts",
        "documents",
        "types",
        "subclass_of",
        "ontology_documents",
        "mounted_dbs",
        "table_mappings",
        "column_mappings",
    )
    has_data = any(
        table in tables and conn.execute(f'SELECT EXISTS(SELECT 1 FROM "{table}")').fetchone()[0]
        for table in state_tables
    )
    if has_data and not reset:
        conn.close()
        print("已有数据，跳过种子初始化；如需重建，请使用 seed.py --reset。")
        return

    # ---- 本体：类型层级（subClassOf）----
    for t in ["Thing", "Agent", "Organization", "Person", "Company", "Product",
              "State", "City", "Country", "Continent"]:
        ontology.add_type(conn, t)
    ontology.add_subclass(conn, "Person", "Agent")
    ontology.add_subclass(conn, "Company", "Organization")
    ontology.add_subclass(conn, "Agent", "Thing")
    ontology.add_subclass(conn, "Organization", "Thing")
    ontology.add_subclass(conn, "Product", "Thing")
    ontology.add_subclass(conn, "State", "Thing")
    ontology.add_subclass(conn, "City", "Thing")
    ontology.add_subclass(conn, "Country", "Thing")
    ontology.add_subclass(conn, "Continent", "Thing")

    # ---- 实体 ----
    co = graph.add_entity(conn, "DeepLethe", "Company")
    utopia = graph.add_entity(conn, "Utopia", "Product")
    alice = graph.add_entity(conn, "Alice", "Person")
    bob = graph.add_entity(conn, "Bob", "Person")
    carol = graph.add_entity(conn, "Carol", "Person")

    # ---- 事实（带真实时间线 valid_from/valid_to）----
    graph.add_fact(conn, alice, "works_at", co, "2020-01-01", None, "hr-system")
    graph.add_fact(conn, alice, "founded", utopia, "2021-01-01", None, "company-registry")
    graph.add_fact(conn, bob, "works_at", co, "2019-01-01", "2023-06-30", "hr-system")
    graph.add_fact(conn, carol, "works_at", co, "2023-01-01", None, "hr-system")
    # 一条会被纠错的事实：Utopia 状态最初记为 'beta'
    status_fact = graph.add_fact(conn, utopia, "status", co, "2021-06-01", None, "product-page")

    # 再建一个实体表示状态值，用于纠错演示：把 status 的对象从 beta 更正为 released
    beta = graph.add_entity(conn, "beta", "State")
    released = graph.add_entity(conn, "released", "State")
    # 重新把 status 事实指向 beta（上面的 status_fact 对象先指向 co 是不对的，修正为 beta）
    # 简化：直接新建指向 beta 的事实，并演示一次纠错链
    conn.execute("DELETE FROM facts WHERE id = ?", (status_fact,))
    status_beta = graph.add_fact(conn, utopia, "status", beta, "2021-06-01", None, "product-page")
    # 双时态纠错：2022-01-01 起，状态从 beta 更正为 released
    graph.correct_fact(conn, status_beta, released, "产品正式发布")

    # ---- 传递性推导的素材：地理位置层级（located_in 是传递关系）----
    shanghai = graph.add_entity(conn, "上海", "City")
    china = graph.add_entity(conn, "中国", "Country")
    asia = graph.add_entity(conn, "亚洲", "Continent")
    graph.add_fact(conn, shanghai, "located_in", china, "2020-01-01", None, "geo")
    graph.add_fact(conn, china, "located_in", asia, "2020-01-01", None, "geo")
    # 注意：这里不主动推导。推导由 reason.derive_transitive 按需执行，
    # 对应 Utopia「推导默认关闭」的哲学（Dash 按钮或 /api/reason/transitive 触发）。

    # ---- 冲突处置素材：中国 capital_of 北京（capital_of 是函数型关系）----
    beijing = graph.add_entity(conn, "北京", "City")
    graph.add_fact(conn, china, "capital_of", beijing, "1949-10-01", None, "geo")

    # ---- 文档（演示全文 + 向量检索）----
    for title, content in DOCS:
        search.index_document(conn, title, content)

    # ---- Ontology2SQL：挂载一个示例外部库（hr 员工表）----
    import sqlite3
    sample_path = os.path.join(os.path.dirname(db.DB_PATH), "sample_hr.db")
    if os.path.exists(sample_path):
        os.remove(sample_path)
    mconn = sqlite3.connect(sample_path)
    mconn.execute("CREATE TABLE employees(id INTEGER PRIMARY KEY, name TEXT, dept TEXT, salary INTEGER)")
    mconn.executemany(
        "INSERT INTO employees(name, dept, salary) VALUES (?, ?, ?)",
        [("张三", "工程", 30000), ("李四", "市场", 25000), ("王五", "工程", 28000)],
    )
    mconn.commit()
    mconn.close()
    ontosql.mount_db(conn, "hr", sample_path, source_kind="seed", trusted_seed=True)
    ontosql.map_table(conn, "hr", "employees", "Person", "name")

    conn.close()
    print("✅ 种子数据写入完成。实体、事实、文档已就绪。")
    print(f"   数据库路径：{db.DB_PATH}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="初始化或重置 Utopia Lite 演示数据。")
    parser.add_argument("--reset", action="store_true", help="删除现有数据库并重建演示数据")
    args = parser.parse_args()
    main(reset=args.reset)
