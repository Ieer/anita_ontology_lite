"""FastAPI 后端：图谱 CRUD + 双时态查询 + 混合检索。"""

from __future__ import annotations

import hmac
import ipaddress
import os
import sqlite3
from typing import Any

import json as _json

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from . import db as dbmod
from . import conflict, evidence, graph, ingest, llm, local_data, local_markdown, ontology, ontology_documents, ontology_rdf, ontology_suggestions, ontology_versions, ontosql, rbac, reason, resolution, search, trajectory


def get_conn():
    """每个请求一个新连接（WAL 支持并发读，教学场景足够）。"""
    return dbmod.init_db()


app = FastAPI(title="Utopia Lite", version="0.1.0")


@app.middleware("http")
async def protect_local_data_endpoints(request: Request, call_next):
    """Keep mounted local files and Markdown private when the app is LAN-bound."""
    if request.method == "POST" and request.headers.get("content-length"):
        content_length = int(request.headers["content-length"])
        if request.url.path == "/api/mounted/upload" and content_length > local_data.max_upload_bytes() + 1024 * 1024:
            return Response(status_code=413, content="SQLite 上传请求超过大小上限。")
        if request.url.path.startswith("/api/local-markdown") and content_length > local_markdown.MAX_MARKDOWN_BYTES + 1024 * 1024:
            return Response(status_code=413, content="Markdown 请求超过大小上限。")
    if request.url.path.startswith(("/api/mounted", "/api/local-markdown", "/api/ontology-suggestions")):
        host = request.client.host if request.client else ""
        try:
            is_local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_local = host == "testclient"
        if not is_local:
            expected = os.environ.get("UTOPIA_LOCAL_ADMIN_TOKEN", "")
            supplied = request.headers.get("x-utopia-local-token", "")
            if not expected or not hmac.compare_digest(supplied, expected):
                return Response(status_code=403, content="本地数据接口仅限本机；远程访问需配置管理员令牌。")
    return await call_next(request)


# ---------- 请求模型 ----------

class EntityIn(BaseModel):
    name: str
    type: str = "Thing"


class FactIn(BaseModel):
    subject_id: int
    predicate: str
    object_id: int
    valid_from: str
    valid_to: str | None = None
    source: str | None = None


class CorrectIn(BaseModel):
    new_object_id: int
    note: str = ""


class ConflictDetectIn(BaseModel):
    subject_id: int
    predicate: str
    object_id: int
    valid_from: str
    valid_to: str | None = None


class ConflictResolveIn(BaseModel):
    subject_id: int
    predicate: str
    object_id: int
    valid_from: str
    valid_to: str | None = None
    source: str | None = None
    resolution: str  # close | keep | reject
    note: str = ""


class ChatIn(BaseModel):
    query: str
    k: int = 5


class DocumentIn(BaseModel):
    title: str
    content: str
    type: str = "text"  # text | markdown | html | csv | json


class AxiomResolveIn(BaseModel):
    fact_id: int
    choice: str  # retract | accept | relax
    note: str = ""


class FactAttributeIn(BaseModel):
    key: str
    value: str


class TypeIn(BaseModel):
    name: str


class SubclassIn(BaseModel):
    child: str
    parent: str


class TypeEquivIn(BaseModel):
    type_a: str
    type_b: str


class AliasIn(BaseModel):
    alias: str


class MergeIn(BaseModel):
    keep_id: int
    merge_id: int
    method: str = "manual"


class UserIn(BaseModel):
    username: str
    password: str
    role: str = "viewer"


class AuthIn(BaseModel):
    username: str
    password: str


class MountIn(BaseModel):
    name: str
    path: str


class MapTableIn(BaseModel):
    mount_name: str
    table: str
    entity_type: str
    name_col: str
    key_col: str = ""


class QueryIn(BaseModel):
    mount_name: str
    sql: str


class TableQueryIn(BaseModel):
    mount_name: str
    table: str
    columns: list[str] = Field(default_factory=list)
    filters: list[dict[str, Any]] = Field(default_factory=list)
    limit: int = 100
    offset: int = 0


class WriteIn(BaseModel):
    mount_name: str
    sql: str


class NlQueryIn(BaseModel):
    mount_name: str
    question: str


class NlWriteIn(BaseModel):
    mount_name: str
    instruction: str


class MapColumnIn(BaseModel):
    mount_name: str
    table: str
    column: str
    target_type: str = ""   # 关系模式必填
    predicate: str = ""     # 关系模式必填
    attr_name: str = ""     # 属性模式必填（非空即属性模式）


class MaterializeIn(BaseModel):
    mount_name: str
    confirmed: bool = False
    source_sha256: str = ""
    mapping_sha256: str = ""


class LocalMarkdownIn(BaseModel):
    title: str
    body: str
    file_id: str = ""
    ontology_document_id: str = ""


class OntologyDocumentIn(BaseModel):
    document: dict[str, Any]
    rdf_source_xml: str | None = None
    rdf_source_document: dict[str, Any] | None = None


class OntologySuggestionIn(BaseModel):
    provider: str
    mount_names: list[str] = Field(default_factory=list)
    document_ids: list[int] = Field(default_factory=list)
    confirmed: bool = False


class EvidenceReferenceIn(BaseModel):
    semantic_object_id: str
    source_type: str
    source_id: str
    locator: str = ""
    label: str = ""


class CandidateVersionIn(BaseModel):
    parent_version_id: str | None = None


class CandidateDecisionIn(BaseModel):
    decision: str
    reason: str = ""


class AgentTaskIn(BaseModel):
    ontology_version_id: str | None = None


class RDFImportIn(BaseModel):
    rdf_xml: str


# ---------- 图谱 ----------

@app.get("/api/entities")
def api_entities() -> list[dict[str, Any]]:
    conn = get_conn()
    return [dict(r) for r in graph.list_entities(conn)]


@app.post("/api/entities")
def api_add_entity(body: EntityIn) -> dict[str, Any]:
    conn = get_conn()
    eid = graph.add_entity(conn, body.name, body.type)
    return {"id": eid}


@app.get("/api/graph")
def api_graph(as_of: str | None = None, believed_at: str | None = None) -> dict[str, Any]:
    """返回 Cytoscape 可直接用的节点 + 边。"""
    conn = get_conn()
    nodes = [
        {"data": {"id": str(r["id"]), "label": f"{r['name']} ({r['type']})"}}
        for r in graph.list_entities(conn)
    ]
    edges = [
        {
            "data": {
                "id": f"e{r['id']}",
                "source": str(r["subject_id"]),
                "target": str(r["object_id"]),
                "label": r["predicate"],
                "derived": r["derived"],
            }
        }
        for r in graph.active_facts(conn, as_of=as_of, believed_at=believed_at)
    ]
    return {"nodes": nodes, "edges": edges}


@app.post("/api/facts")
def api_add_fact(body: FactIn) -> dict[str, Any]:
    conn = get_conn()
    try:
        fid = graph.add_fact(
            conn, body.subject_id, body.predicate, body.object_id,
            body.valid_from, body.valid_to, body.source,
        )
    except Exception as e:  # 外键约束等
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": fid}


@app.post("/api/facts/{fact_id}/correct")
def api_correct_fact(fact_id: int, body: CorrectIn) -> dict[str, Any]:
    """双时态纠错：关闭旧事实、链接新事实，并级联撤销依赖它的推导事实。"""
    conn = get_conn()
    try:
        new_id = graph.correct_fact(conn, fact_id, body.new_object_id, body.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    retracted = reason.retract_derived(conn, fact_id, reason=f"premise #{fact_id} superseded")
    return {"superseded": fact_id, "new_fact_id": new_id, "retracted": retracted}


# ---------- 推理 ----------

@app.post("/api/reason/transitive")
def api_reason_transitive() -> dict[str, Any]:
    """按需执行传递性推导（幂等），返回本次新推导的事实数。"""
    n = reason.derive_transitive(get_conn())
    return {"derived": n}


# ---------- 公理违反 ----------

@app.get("/api/axioms")
def api_axioms() -> dict[str, Any]:
    """查看本体公理注册表。"""
    return reason.AXIOMS


@app.post("/api/axioms/violations")
def api_axiom_violations() -> list[dict[str, Any]]:
    """检测公理违反（自环/反对称/传递环）。"""
    return reason.detect_axiom_violations(get_conn())


@app.post("/api/axioms/violations/resolve")
def api_axiom_resolve(body: AxiomResolveIn) -> dict[str, Any]:
    """处置公理违反（retract/accept/relax）。"""
    try:
        return reason.resolve_axiom_violation(get_conn(), body.fact_id, body.choice, body.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- 边物化（reified edges） ----------

@app.post("/api/facts/{fact_id}/attributes")
def api_set_fact_attribute(fact_id: int, body: FactAttributeIn) -> dict[str, Any]:
    """给边设置属性（如 since/confidence/role）。"""
    graph.set_fact_attribute(get_conn(), fact_id, body.key, body.value)
    return {"fact_id": fact_id, "key": body.key, "value": body.value}


@app.get("/api/facts/{fact_id}/attributes")
def api_get_fact_attributes(fact_id: int) -> dict[str, str]:
    """查看边的全部属性。"""
    return graph.get_fact_attributes(get_conn(), fact_id)


# ---------- 本体（类型层级） ----------

@app.get("/api/ontology")
def api_ontology() -> dict[str, Any]:
    """查看本体：类型列表 + subClassOf 层级。"""
    conn = get_conn()
    types = [r["name"] for r in conn.execute("SELECT name FROM types ORDER BY name")]
    hierarchy = [
        {"child": r["child"], "parent": r["parent"]}
        for r in conn.execute("SELECT child, parent FROM subclass_of ORDER BY child")
    ]
    return {"types": types, "subclass_of": hierarchy}


@app.get("/api/ontology-documents")
def api_ontology_documents() -> list[dict[str, Any]]:
    return ontology_documents.list_documents(get_conn())


@app.post("/api/ontology-documents/{document_id}/versions", status_code=201)
def api_create_ontology_candidate(document_id: str, body: CandidateVersionIn) -> dict[str, Any]:
    conn = get_conn()
    try:
        return ontology_versions.create_candidate(conn, document_id, body.parent_version_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        conn.close()


@app.get("/api/ontology-documents/{document_id}/versions")
def api_list_ontology_versions(document_id: str) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        if ontology_documents.get_document(conn, document_id) is None:
            raise HTTPException(status_code=404, detail="Ontology document not found")
        return ontology_versions.list_versions(conn, document_id)
    finally:
        conn.close()


@app.post("/api/ontology-versions/{version_id}/evaluate")
def api_evaluate_ontology_candidate(version_id: str) -> dict[str, Any]:
    conn = get_conn()
    try:
        return ontology_versions.evaluate_candidate(conn, version_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        conn.close()


@app.post("/api/ontology-versions/{version_id}/decision")
def api_decide_ontology_candidate(version_id: str, body: CandidateDecisionIn) -> dict[str, Any]:
    conn = get_conn()
    try:
        return ontology_versions.decide_candidate(conn, version_id, body.decision, body.reason)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        conn.close()


@app.post("/api/ontology-documents/{document_id}/versions/{version_id}/activate")
def api_activate_ontology_version(document_id: str, version_id: str) -> dict[str, Any]:
    conn = get_conn()
    try:
        try:
            return ontology_versions.activate_accepted_version(conn, document_id, version_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        conn.close()


@app.post("/api/agent-tasks", status_code=201)
def api_create_agent_task(body: AgentTaskIn) -> dict[str, Any]:
    conn = get_conn()
    try:
        return trajectory.create_task(conn, body.ontology_version_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        conn.close()


@app.get("/api/agent-tasks")
def api_list_agent_tasks(limit: int = 50) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        try:
            return trajectory.list_tasks(conn, limit)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        conn.close()


@app.get("/api/agent-tasks/{task_id}")
def api_get_agent_task(task_id: str) -> dict[str, Any]:
    conn = get_conn()
    try:
        task = trajectory.get_task(conn, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Agent task not found")
        return task
    finally:
        conn.close()


@app.post("/api/agent-tasks/{task_id}/complete")
def api_complete_agent_task(task_id: str) -> dict[str, Any]:
    conn = get_conn()
    try:
        try:
            return trajectory.complete_task(conn, task_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        conn.close()


@app.delete("/api/agent-tasks/{task_id}")
def api_delete_agent_task(task_id: str) -> dict[str, bool]:
    conn = get_conn()
    try:
        if not trajectory.delete_task(conn, task_id):
            raise HTTPException(status_code=404, detail="Agent task not found")
        return {"deleted": True}
    finally:
        conn.close()


@app.post("/api/evidence", status_code=201)
def api_add_evidence_reference(body: EvidenceReferenceIn) -> dict[str, Any]:
    conn = get_conn()
    try:
        return evidence.add_reference(
            conn,
            body.semantic_object_id,
            body.source_type,
            body.source_id,
            body.locator,
            body.label,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        conn.close()


@app.get("/api/evidence/{semantic_object_id}")
def api_list_evidence_references(semantic_object_id: str) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        try:
            return evidence.list_references(conn, semantic_object_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        conn.close()


@app.post("/api/ontology-documents", status_code=201)
def api_create_ontology_document(body: OntologyDocumentIn) -> dict[str, Any]:
    return ontology_documents.create_document(
        get_conn(), body.document, body.rdf_source_xml, body.rdf_source_document
    )


@app.post("/api/ontology-documents/validate")
def api_validate_ontology_document(body: OntologyDocumentIn) -> dict[str, Any]:
    errors = ontology_documents.validate_document(body.document)
    return {"valid": not errors, "errors": errors}


@app.get("/api/ontology-documents/{document_id}")
def api_get_ontology_document(document_id: str) -> dict[str, Any]:
    document = ontology_documents.get_document(get_conn(), document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Ontology document not found")
    return document


@app.put("/api/ontology-documents/{document_id}")
def api_update_ontology_document(document_id: str, body: OntologyDocumentIn) -> dict[str, Any]:
    document = ontology_documents.update_document(
        get_conn(), document_id, body.document, body.rdf_source_xml, body.rdf_source_document
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Ontology document not found")
    return document


@app.delete("/api/ontology-documents/{document_id}")
def api_delete_ontology_document(document_id: str) -> dict[str, bool]:
    deleted = ontology_documents.delete_document(get_conn(), document_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Ontology document not found")
    return {"deleted": True}


@app.post("/api/ontology-documents/import-rdf")
def api_import_ontology_rdf(body: RDFImportIn) -> dict[str, Any]:
    try:
        document, warnings = ontology_rdf.parse_document(body.rdf_xml)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {
        "document": document,
        "warnings": warnings,
        "validation": ontology_documents.validate_document(document),
        "rdf_source_xml": body.rdf_xml,
        "rdf_source_document": document,
    }


@app.get("/api/ontology-suggestions/sources")
def api_ontology_suggestion_sources() -> dict[str, Any]:
    conn = get_conn()
    return {
        "providers": ontology_suggestions.providers(),
        "mounts": [item["name"] for item in ontosql.list_mounted(conn)],
        "documents": [dict(row) for row in conn.execute("SELECT id, title FROM documents ORDER BY id")],
    }


@app.post("/api/ontology-suggestions/generate")
def api_generate_ontology_suggestion(body: OntologySuggestionIn, request: Request) -> dict[str, Any]:
    try:
        return ontology_suggestions.generate(
            get_conn(), body.provider, body.mount_names, body.document_ids,
            body.confirmed, request.headers.get("x-utopia-local-token", ""),
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/ontology-documents/{document_id}/rdf")
def api_export_ontology_rdf(document_id: str) -> Response:
    stored = ontology_documents.get_document(get_conn(), document_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Ontology document not found")
    rdf_xml = ontology_rdf.serialize_document(
        stored["document"],
        stored.get("rdf_source_xml"),
        stored.get("rdf_source_document"),
    )
    return Response(
        content=rdf_xml,
        media_type="application/rdf+xml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="ontology-{document_id}.rdf"'},
    )


@app.get("/api/ontology-documents/{document_id}/versions/{version_id}/rdf")
def api_export_accepted_ontology_rdf(document_id: str, version_id: str) -> Response:
    conn = get_conn()
    version = ontology_versions.get_version(conn, version_id)
    if version is None or version["document_id"] != document_id or version["status"] != "accepted":
        raise HTTPException(status_code=404, detail="Accepted ontology version not found")
    rdf_xml = ontology_rdf.serialize_document(version["snapshot"])
    return Response(
        content=rdf_xml,
        media_type="application/rdf+xml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="ontology-{version_id}.rdf"'},
    )


@app.post("/api/ontology/types")
def api_add_type(body: TypeIn) -> dict[str, str]:
    ontology.add_type(get_conn(), body.name)
    return {"name": body.name}


@app.post("/api/ontology/subclass")
def api_add_subclass(body: SubclassIn) -> dict[str, str]:
    ontology.add_subclass(get_conn(), body.child, body.parent)
    return {"child": body.child, "parent": body.parent}


@app.get("/api/ontology/equivalences")
def api_type_equivalences() -> list[dict[str, str]]:
    """查看类型等价（同义）关系。"""
    return [
        {"type_a": a, "type_b": b}
        for a, b in ontology.list_type_equivalences(get_conn())
    ]


@app.post("/api/ontology/equivalence")
def api_add_type_equivalence(body: TypeEquivIn) -> dict[str, Any]:
    ok, msg = ontology.add_type_equivalence(get_conn(), body.type_a, body.type_b)
    return {"ok": ok, "message": msg}


@app.delete("/api/ontology/equivalence")
def api_remove_type_equivalence(type_a: str, type_b: str) -> dict[str, Any]:
    ok, msg = ontology.remove_type_equivalence(get_conn(), type_a, type_b)
    return {"ok": ok, "message": msg}


@app.get("/api/entities/{entity_id}/types")
def api_entity_types(entity_id: int) -> list[str]:
    """实体的声明类型 + 推断出的所有超类。"""
    return sorted(ontology.infer_entity_types(get_conn(), entity_id))


# ---------- 实体消解 ----------

@app.get("/api/resolution/duplicates")
def api_duplicates() -> dict[str, Any]:
    """三阶段检测重复实体（exact / embedding / doubtful）。"""
    return resolution.find_duplicates(get_conn())


@app.post("/api/entities/{entity_id}/aliases")
def api_add_alias(entity_id: int, body: AliasIn) -> dict[str, Any]:
    resolution.add_alias(get_conn(), entity_id, body.alias)
    return {"entity_id": entity_id, "alias": body.alias}


@app.post("/api/resolution/merge")
def api_merge(body: MergeIn) -> dict[str, Any]:
    return resolution.merge_entities(get_conn(), body.keep_id, body.merge_id, body.method)


@app.post("/api/resolution/undo/{merge_id}")
def api_undo(merge_id: int) -> dict[str, Any]:
    try:
        return resolution.undo_merge(get_conn(), merge_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------- 多用户 RBAC ----------

@app.post("/api/users")
def api_create_user(body: UserIn) -> dict[str, Any]:
    try:
        rbac.create_user(get_conn(), body.username, body.password, body.role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"username": body.username, "role": body.role}


@app.get("/api/users")
def api_list_users() -> list[dict[str, Any]]:
    return [dict(r) for r in rbac.list_users(get_conn())]


@app.post("/api/users/auth")
def api_auth(body: AuthIn) -> dict[str, Any]:
    u = rbac.authenticate(get_conn(), body.username, body.password)
    if not u:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {"id": u["id"], "username": u["username"], "role": u["role"]}


# ---------- Ontology2SQL（教学版） ----------

@app.get("/api/mounted")
def api_mounted() -> list[dict[str, Any]]:
    return [
        {key: value for key, value in source.items() if key != "path"}
        for source in ontosql.list_mounted(get_conn())
    ]


@app.post("/api/mounted")
def api_mount(body: MountIn) -> dict[str, str]:
    try:
        ontosql.mount_db(get_conn(), body.name, body.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"name": body.name}


@app.post("/api/mounted/upload")
async def api_upload_mounted_database(name: str = Form(...), file: UploadFile = File(...)) -> dict[str, Any]:
    content = await file.read(local_data.max_upload_bytes() + 1)
    if len(content) > local_data.max_upload_bytes():
        raise HTTPException(status_code=413, detail="SQLite 文件超过本地数据源大小上限。")
    try:
        path, digest = local_data.store_sqlite_upload(file.filename or "database.sqlite", content)
        try:
            ontosql.mount_db(get_conn(), name, str(path), source_kind="upload", source_filename=file.filename or path.name)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return {"name": name, "filename": path.name, "sha256": digest}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/mounted/{mount_name}/schema")
def api_mounted_schema(mount_name: str) -> list[dict[str, Any]]:
    try:
        path = ontosql.mounted_source_path(get_conn(), mount_name)
        return local_data.sqlite_schema(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/mounted/query/table")
def api_query_mounted_table(body: TableQueryIn) -> dict[str, Any]:
    try:
        path = ontosql.mounted_source_path(get_conn(), body.mount_name)
        return local_data.query_table(path, body.table, body.columns, body.filters, body.limit, body.offset)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/mounted/map")
def api_map_table(body: MapTableIn) -> dict[str, str]:
    try:
        ontosql.map_table(get_conn(), body.mount_name, body.table, body.entity_type, body.name_col, body.key_col)
    except (ValueError, sqlite3.Error) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"mount": body.mount_name, "table": body.table}


@app.get("/api/mounted/mappings")
def api_mappings() -> list[dict[str, Any]]:
    return ontosql.list_mappings(get_conn())


@app.post("/api/mounted/map/column")
def api_map_column(body: MapColumnIn) -> dict[str, str]:
    try:
        ontosql.map_column(
            get_conn(), body.mount_name, body.table, body.column,
            body.target_type, body.predicate, body.attr_name,
        )
    except (ValueError, sqlite3.Error) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"mount": body.mount_name, "table": body.table, "column": body.column}


@app.get("/api/mounted/column-mappings")
def api_column_mappings() -> list[dict[str, Any]]:
    return ontosql.list_column_mappings(get_conn())


@app.delete("/api/mounted/map/column")
def api_remove_column_mapping(mount_name: str, table: str, column: str) -> dict[str, bool]:
    return {"removed": ontosql.remove_column_mapping(get_conn(), mount_name, table, column)}


@app.post("/api/mounted/materialize")
def api_materialize(body: MaterializeIn) -> dict[str, Any]:
    """把挂载库数据按映射物化成本体实体/事实/属性。"""
    if not body.confirmed or not body.source_sha256 or not body.mapping_sha256:
        raise HTTPException(status_code=400, detail="请先预览并提交对应 source_sha256、mapping_sha256，显式确认后再物化。")
    try:
        preview = ontosql.preview_materialization(get_conn(), body.mount_name)
        if body.source_sha256 != preview["source_sha256"] or body.mapping_sha256 != preview["mapping_sha256"]:
            raise HTTPException(status_code=409, detail="数据源或映射已变化，请重新预览。")
        return ontosql.materialize_mappings(get_conn(), body.mount_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/mounted/{mount_name}/materialize-preview")
def api_materialize_preview(mount_name: str) -> dict[str, Any]:
    try:
        return ontosql.preview_materialization(get_conn(), mount_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/entities/{entity_id}/attributes")
def api_entity_attributes(entity_id: int) -> dict[str, str]:
    """查看某实体的属性（字段级映射属性模式的落点）。"""
    return graph.get_entity_attributes(get_conn(), entity_id)


@app.post("/api/mounted/query")
def api_query(body: QueryIn) -> list[dict[str, Any]]:
    try:
        return ontosql.query_mounted(get_conn(), body.mount_name, body.sql)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/mounted/write")
def api_write(body: WriteIn) -> dict[str, Any]:
    """兼容旧 API；外部 SQLite 写入默认禁用。"""
    if os.environ.get("UTOPIA_ENABLE_MOUNT_WRITES") != "1":
        raise HTTPException(status_code=403, detail="挂载数据源默认只读；当前配置未启用写入。")
    try:
        return {"affected": ontosql.write_mounted(get_conn(), body.mount_name, body.sql)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/mounted/nl/query")
def api_nl_query(body: NlQueryIn) -> dict[str, Any]:
    """NL→SQL 只读查询（LLM 生成 SQL 后执行）。"""
    try:
        return ontosql.nl_query(get_conn(), body.mount_name, body.question)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/mounted/nl/write")
def api_nl_write(body: NlWriteIn) -> dict[str, Any]:
    """NL→SQL 写入执行（LLM 生成 SQL 后执行写操作）。"""
    if os.environ.get("UTOPIA_ENABLE_MOUNT_WRITES") != "1":
        raise HTTPException(status_code=403, detail="挂载数据源默认只读；当前配置未启用写入。")
    try:
        return ontosql.nl_write(get_conn(), body.mount_name, body.instruction)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))


# ---------- 冲突 ----------

@app.post("/api/conflicts/detect")
def api_conflict_detect(body: ConflictDetectIn) -> list[dict[str, Any]]:
    """检测：给定候选事实，返回与之矛盾的 active 断言事实（不改数据）。"""
    conn = get_conn()
    return [dict(r) for r in conflict.detect(conn, body.subject_id, body.predicate, body.object_id, body.valid_from, body.valid_to)]


@app.post("/api/conflicts/resolve")
def api_conflict_resolve(body: ConflictResolveIn) -> dict[str, Any]:
    """处置：close（关旧）/ keep（两者保留）/ reject（拒新）。"""
    conn = get_conn()
    new_fact = {
        "subject_id": body.subject_id,
        "predicate": body.predicate,
        "object_id": body.object_id,
        "valid_from": body.valid_from,
        "valid_to": body.valid_to,
        "source": body.source,
    }
    try:
        return conflict.resolve(conn, new_fact, body.resolution, body.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/conflicts")
def api_conflicts() -> list[dict[str, Any]]:
    """冲突处置账本（可审计）。"""
    return [dict(r) for r in conflict.list_conflicts(get_conn())]


# ---------- 检索 ----------

@app.get("/api/search")
def api_search(q: str, k: int = 5) -> list[dict[str, Any]]:
    return search.hybrid_search(get_conn(), q, k)


# ---------- 问答（LLM，可选） ----------

@app.post("/api/chat")
def api_chat(body: ChatIn):
    conn = get_conn()
    chunks = search.hybrid_search(conn, body.query, k=body.k)

    # 未配置 LLM：降级为仅返回检索结果（Demo 无 LLM 也能跑）
    if not llm.is_configured():
        return {
            "mode": "retrieval-only",
            "message": "未配置 LLM（设 LLM_BASE_URL + LLM_MODEL 启用流式问答），以下为检索结果",
            "chunks": chunks,
        }

    def gen():
        yield _sse({"type": "citations", "chunks": chunks})
        try:
            for ev in llm.stream_chat(body.query, chunks):
                yield _sse(ev)
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "text": str(e)})
        yield _sse({"type": "done"})

    return StreamingResponse(gen(), media_type="text/event-stream")


def _sse(obj: dict) -> str:
    return f"data: {_json.dumps(obj, ensure_ascii=False)}\n\n"


# ---------- 文档摄入 ----------

@app.post("/api/documents")
def api_add_document(body: DocumentIn) -> dict[str, Any]:
    conn = get_conn()
    handlers = {
        "markdown": ingest.ingest_markdown,
        "html": ingest.ingest_html,
        "csv": ingest.ingest_csv,
        "json": ingest.ingest_json,
    }
    fn = handlers.get(body.type, ingest.ingest_text)
    doc_id = fn(conn, body.title, body.content)
    return {"document_id": doc_id}


@app.post("/api/documents/upload")
async def api_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    content = await file.read()
    conn = get_conn()
    try:
        doc_id = ingest.ingest_file(conn, file.filename or "upload.txt", content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"document_id": doc_id, "filename": file.filename}


# ---------- 本地 Markdown 本体说明 ----------

@app.get("/api/local-markdown")
def api_local_markdown_list() -> list[dict[str, Any]]:
    return local_markdown.list_files(get_conn())


@app.get("/api/local-markdown/{file_id}")
def api_local_markdown_get(file_id: str) -> dict[str, Any]:
    try:
        result = local_markdown.get_file(get_conn(), file_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    if result is None:
        raise HTTPException(status_code=404, detail="Markdown 文件不存在。")
    return result


@app.post("/api/local-markdown")
def api_local_markdown_save(body: LocalMarkdownIn) -> dict[str, Any]:
    try:
        return local_markdown.save_file(get_conn(), body.title, body.body, body.ontology_document_id, body.file_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@app.post("/api/local-markdown/upload")
async def api_local_markdown_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    content = await file.read(local_markdown.MAX_MARKDOWN_BYTES + 1)
    try:
        return local_markdown.import_file(get_conn(), file.filename or "note.md", content)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@app.post("/api/local-markdown/{file_id}/sync")
def api_local_markdown_sync(file_id: str) -> dict[str, Any]:
    try:
        return local_markdown.sync_index(get_conn(), file_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@app.delete("/api/local-markdown/{file_id}/index")
def api_local_markdown_remove_index(file_id: str) -> dict[str, bool]:
    return {"removed": local_markdown.remove_index(get_conn(), file_id)}


@app.delete("/api/local-markdown/{file_id}")
def api_local_markdown_delete(file_id: str) -> dict[str, bool]:
    return {"deleted": local_markdown.delete_source(get_conn(), file_id)}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
