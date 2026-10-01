"""MCP 服务器：以只读工具暴露知识底座（对应 Utopia 的「Agents over MCP」）。

外部 Agent（Claude Desktop / Cursor 等）可通过 MCP 协议接入，只读地检索与浏览图谱。

运行（stdio，供 MCP 客户端 spawn）：
    python mcp_server.py

工具（均为只读）：
    search_documents        混合检索（FTS5 + 向量 + RRF）
    list_entities           列出实体
    get_entity_types        实体类型 + 推断超类
    get_graph               图谱（节点+边，可选 as_of）
    detect_axiom_violations 公理违反检测
    browse_semantics        按需发现术语、映射、关系与约束
    resolve_semantics       解析提及并返回关联语义对象
"""

from typing import Literal

from mcp.server.fastmcp import FastMCP

from app import db, evidence, graph, ontology, reason, search, semantic_tools, trajectory

mcp = FastMCP("utopia-lite")


@mcp.tool()
def search_documents(query: str, k: int = 5) -> list[dict]:
    """混合检索（FTS5 + 向量 + RRF），返回相关分块（带来源文档与融合分）。"""
    return search.hybrid_search(db.init_db(), query, k)


@mcp.tool()
def list_entities() -> list[dict]:
    """列出所有实体（id / name / type）。"""
    return [dict(r) for r in graph.list_entities(db.init_db())]


@mcp.tool()
def get_entity_types(entity_id: int) -> list[str]:
    """实体的声明类型 + 沿类型层级推断出的超类。"""
    return sorted(ontology.infer_entity_types(db.init_db(), entity_id))


@mcp.tool()
def get_graph(as_of: str | None = None) -> dict:
    """知识图谱：节点 + 边（可选按真实世界时间 as_of 过滤）。"""
    conn = db.init_db()
    nodes = [
        {"id": r["id"], "name": r["name"], "type": r["type"]}
        for r in graph.list_entities(conn)
    ]
    edges = [
        {
            "id": f["id"],
            "subject_id": f["subject_id"],
            "predicate": f["predicate"],
            "object_id": f["object_id"],
            "derived": f["derived"],
        }
        for f in graph.active_facts(conn, as_of=as_of)
    ]
    return {"nodes": nodes, "edges": edges}


@mcp.tool()
def detect_axiom_violations() -> list[dict]:
    """检测公理违反（自环 / 反对称 / 传递环）。"""
    return reason.detect_axiom_violations(db.init_db())


@mcp.tool()
def browse_semantics(
    query: str,
    kind: Literal["term", "mapping", "relation", "constraint", "all"] = "all",
    limit: int = semantic_tools.MAX_RESULTS,
    task_id: str | None = None,
) -> dict:
    """按需发现最多 6 个术语、字段映射、关系或运行时约束；task_id 用于选择性记录摘要。"""
    conn = db.init_db()
    try:
        try:
            result = semantic_tools.browse_semantics(conn, query, kind, limit)
        except ValueError:
            if task_id:
                _record_trajectory(conn, task_id, "browse_semantics", "error", query=query)
            raise
        if task_id:
            _record_trajectory(
                conn,
                task_id,
                "browse_semantics",
                "success" if result["results"] else "not_found",
                query=query,
                result_count=len(result["results"]),
                semantic_object_ids=[item["id"] for item in result["results"]],
            )
        return result
    finally:
        conn.close()


@mcp.tool()
def resolve_semantics(
    mentions: list[str],
    context: str = "",
    as_of: str | None = None,
    believed_at: str | None = None,
    task_id: str | None = None,
) -> dict:
    """解析最多 5 个提及；同分歧义会返回候选而不会猜选；task_id 用于选择性记录摘要。"""
    conn = db.init_db()
    try:
        try:
            result = semantic_tools.resolve_semantics(conn, mentions, context, as_of, believed_at)
        except ValueError:
            if task_id:
                _record_trajectory(conn, task_id, "resolve_semantics", "error", query=" ".join(mentions) if isinstance(mentions, list) else "", context=context)
            raise
        for item in result["results"]:
            resolved = item.get("resolved")
            item["evidence"] = evidence.list_references(conn, resolved["id"]) if resolved else []
            if task_id:
                outcome = item["status"] if item["status"] in {"not_found", "ambiguous"} else "success"
                _record_trajectory(
                    conn,
                    task_id,
                    "resolve_semantics",
                    outcome,
                    query=item["mention"],
                    context=context,
                    result_count=len(item["candidates"]) if item["candidates"] else int(resolved is not None),
                    semantic_object_ids=[candidate["id"] for candidate in item["candidates"]] if item["candidates"] else ([resolved["id"]] if resolved else []),
                    evidence_ref_ids=[reference["id"] for reference in item["evidence"]],
                )
        return result
    finally:
        conn.close()


def _record_trajectory(conn, task_id, tool_name, outcome, **kwargs):
    trajectory.record_event(conn, task_id, tool_name, outcome, **kwargs)


if __name__ == "__main__":
    mcp.run()
