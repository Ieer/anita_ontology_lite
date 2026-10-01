import json
import base64
import re

import dash_cytoscape as cyto
from dash import ctx, dcc, html, no_update
from dash.dependencies import Input, Output, State
from flask import request

from app import db, ontology_documents, ontology_rdf, ontology_sharing, ontology_suggestions, ontology_versions, ontosql
from ui.common import page_layout
from ui.templates import COMMERCE_TEMPLATE, empty_ontology


def _load_document(document_id=None, template=None):
    if template == "commerce":
        return None, COMMERCE_TEMPLATE, None, None
    if not document_id:
        return None, empty_ontology(), None, None
    conn = db.init_db()
    stored = ontology_documents.get_document(conn, document_id)
    conn.close()
    if stored is None:
        return document_id, empty_ontology(), None, None
    return document_id, stored["document"], stored.get("rdf_source_xml"), stored.get("rdf_source_document")


def _suggestion_sources():
    conn = db.init_db()
    try:
        return (
            [{"label": item["name"], "value": item["name"]} for item in ontosql.list_mounted(conn)],
            [{"label": row["title"], "value": row["id"]} for row in conn.execute("SELECT id, title FROM documents ORDER BY id")],
        )
    finally:
        conn.close()


def designer_layout(document_id=None, template=None):
    current_id, document, source_xml, source_document = _load_document(document_id, template)
    raw_document = json.dumps(document, ensure_ascii=False, indent=2)
    mounts, documents = _suggestion_sources()
    return page_layout(
        "designer",
        "本体设计器",
        [
            html.P("编辑 JSON 本体文档，校验类型与关系引用，并查看图结构预览。", className="page-description"),
            dcc.Store(id="designer-document-id", data=current_id),
            html.Div(
                [
                    html.Button("校验", id="designer-validate", n_clicks=0, className="button"),
                    html.Button("撤销", id="designer-undo", n_clicks=0, disabled=True, className="button button-secondary"),
                    html.Button("重做", id="designer-redo", n_clicks=0, disabled=True, className="button button-secondary"),
                    html.Button("AI 初稿", id="designer-suggestion-toggle", n_clicks=0, className="button button-secondary designer-suggestion-toggle", **{"aria-expanded": "false", "aria-controls": "designer-suggestion-panel"}),
                    html.Button("保存草稿", id="designer-save", n_clicks=0, className="button button-accent"),
                    dcc.Upload(
                        id="designer-rdf-upload",
                        children=html.Button("导入 RDF/XML", className="button button-secondary"),
                        accept=".rdf,.owl,application/rdf+xml,application/xml,text/xml",
                        multiple=False,
                    ),
                    html.Button("导出 RDF/XML", id="designer-rdf-export", n_clicks=0, className="button"),
                    dcc.Link("新建", href="/designer/new", className="button"),
                    dcc.Link("返回目录", href="/catalogue", className="button button-secondary"),
                ],
                className="button-row designer-actions",
            ),
            html.Div(id="designer-status", className="status-message"),
            html.Div(
                [
                    html.H2("AI 初稿", id="designer-suggestion-heading", tabIndex=-1, className="designer-suggestion-heading"),
                    html.Div(
                        [
                            html.Div([
                                html.Label("模型", htmlFor="designer-suggestion-provider", className="field-label"),
                                dcc.Dropdown(id="designer-suggestion-provider", options=[{"label": item["label"], "value": item["id"]} for item in ontology_suggestions.providers()], placeholder="选择模型", className="graph-filter"),
                            ], className="designer-suggestion-field"),
                            html.Div([
                                html.Label("SQLite 数据源", htmlFor="designer-suggestion-mounts", className="field-label"),
                                dcc.Dropdown(id="designer-suggestion-mounts", options=mounts, multi=True, placeholder="选择挂载库", className="graph-filter"),
                            ], className="designer-suggestion-field"),
                            html.Div([
                                html.Label("文档", htmlFor="designer-suggestion-documents", className="field-label"),
                                dcc.Dropdown(id="designer-suggestion-documents", options=documents, multi=True, placeholder="选择全文文档", className="graph-filter"),
                            ], className="designer-suggestion-field designer-suggestion-wide"),
                        ],
                        className="designer-suggestion-fields",
                    ),
                    html.Div([
                        dcc.Input(id="designer-suggestion-token", type="password", placeholder="外部模型管理员令牌", className="field-control"),
                        dcc.Checklist(id="designer-suggestion-confirm", options=[{"label": "确认将所选表结构和完整文档发送至所选外部模型", "value": "confirmed"}], value=[], className="quiz-options"),
                    ], id="designer-suggestion-remote", hidden=True, className="designer-suggestion-remote"),
                    html.Button("生成初稿", id="designer-suggestion-generate", n_clicks=0, className="button button-accent"),
                    html.Div(id="designer-suggestion-status", className="status-message", role="status"),
                ],
                id="designer-suggestion-panel",
                hidden=True,
                className="tool-section designer-suggestion-panel",
            ),
            dcc.Store(id="designer-suggestion-focus"),
            html.Details(
                [
                    html.Summary("版本评估与发布"),
                    html.P(
                        "保存草稿后创建不可变候选快照。自动检查结构与语义差异；发布需人工确认，只影响 MCP 语义查询，不改写实例图。",
                        className="designer-version-description",
                    ),
                    html.Div(id="designer-version-active", className="designer-version-active", role="status"),
                    dcc.Dropdown(
                        id="designer-version-select",
                        options=[],
                        placeholder="选择候选或已发布版本",
                        clearable=False,
                        className="graph-filter",
                    ),
                    html.Div(
                        [
                            html.Button("创建 Candidate", id="designer-version-create", n_clicks=0, disabled=not current_id, className="button"),
                            html.Button("评估", id="designer-version-evaluate", n_clicks=0, disabled=True, className="button button-secondary"),
                            html.Button("接受并发布", id="designer-version-accept", n_clicks=0, disabled=True, className="button button-accent"),
                            html.Button("拒绝", id="designer-version-reject", n_clicks=0, disabled=True, className="button button-danger"),
                            html.Button("激活所选历史版本", id="designer-version-activate", n_clicks=0, disabled=True, className="button button-secondary"),
                            html.Button("导出所选已批准版", id="designer-version-export", n_clicks=0, className="button button-secondary"),
                        ],
                        className="designer-version-actions",
                    ),
                    dcc.Input(
                        id="designer-version-reason",
                        type="text",
                        placeholder="拒绝原因或发布备注",
                        className="field-control",
                    ),
                    html.Div(id="designer-version-status", className="status-message", role="status"),
                ],
                className="tool-section designer-version-panel",
            ),
            html.Div(id="designer-rdf-status", className="status-message"),
            dcc.Download(id="designer-rdf-download"),
            dcc.Store(
                id="designer-history",
                data={"past": [], "future": [], "current": raw_document},
            ),
            dcc.Store(id="designer-rdf-source-xml", data=source_xml),
            dcc.Store(id="designer-rdf-source-document", data=source_document),
            html.Details(
                className="tool-section designer-tools",
                children=[
                    html.Summary("结构化建模"),
                    html.P("按要素类型分组维护字段，切换分组会保留尚未提交的输入。", className="designer-model-description"),
                    dcc.RadioItems(
                        id="designer-model-mode",
                        value="entity",
                        options=[
                            {"label": "类型", "value": "entity"},
                            {"label": "属性", "value": "property"},
                            {"label": "关系", "value": "relation"},
                        ],
                        labelClassName="designer-model-tab",
                        inputClassName="designer-model-tab-input",
                        className="designer-model-tabs",
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.P("创建一种可在图谱中使用的实体类型。", className="designer-model-hint"),
                                    html.Div(
                                        [
                                            html.Div([html.Label("类型 ID", className="field-label"), dcc.Input(id="designer-entity-id", type="text", placeholder="如 customer", className="field-control")], className="designer-model-control"),
                                            html.Div([html.Label("类型名称", className="field-label"), dcc.Input(id="designer-entity-name", type="text", placeholder="如 Customer", className="field-control")], className="designer-model-control"),
                                        ],
                                        className="designer-model-fields",
                                    ),
                                    html.Button("添加类型", id="designer-add-entity", n_clicks=0, className="button"),
                                ],
                                id="designer-model-entity-panel",
                                className="designer-model-panel",
                            ),
                            html.Div(
                                [
                                    html.P("为已有类型定义属性名称、数据类型与唯一标识。", className="designer-model-hint"),
                                    html.Div(
                                        [
                                            html.Div([html.Label("所属类型", className="field-label"), dcc.Dropdown(id="designer-property-entity", options=[], placeholder="选择类型", className="graph-filter")], className="designer-model-control designer-model-control-wide"),
                                            html.Div([html.Label("属性名称", className="field-label"), dcc.Input(id="designer-property-name", type="text", placeholder="如 email", className="field-control")], className="designer-model-control"),
                                            html.Div(
                                                [
                                                    html.Label("数据类型", className="field-label"),
                                                    dcc.Dropdown(
                                                        id="designer-property-type",
                                                        options=[{"label": value, "value": value} for value in ("string", "integer", "decimal", "double", "date", "datetime", "boolean", "enum")],
                                                        value="string",
                                                        clearable=False,
                                                        className="graph-filter",
                                                    ),
                                                ],
                                                className="designer-model-control",
                                            ),
                                        ],
                                        className="designer-model-fields",
                                    ),
                                    dcc.Checklist(
                                        id="designer-property-identifier",
                                        options=[{"label": "唯一标识", "value": "identifier"}],
                                        value=[],
                                        className="quiz-options",
                                    ),
                                    html.Button("添加属性", id="designer-add-property", n_clicks=0, className="button"),
                                ],
                                id="designer-model-property-panel",
                                className="designer-model-panel",
                                hidden=True,
                            ),
                            html.Div(
                                [
                                    html.P("连接两个实体类型，并声明关系基数。", className="designer-model-hint"),
                                    html.Div(
                                        [
                                            html.Div([html.Label("关系名称", className="field-label"), dcc.Input(id="designer-relation-name", type="text", placeholder="如 places", className="field-control")], className="designer-model-control designer-model-control-wide"),
                                            html.Div([html.Label("起点类型", className="field-label"), dcc.Dropdown(id="designer-relation-from", options=[], placeholder="选择类型", className="graph-filter")], className="designer-model-control"),
                                            html.Div([html.Label("终点类型", className="field-label"), dcc.Dropdown(id="designer-relation-to", options=[], placeholder="选择类型", className="graph-filter")], className="designer-model-control"),
                                            html.Div(
                                                [
                                                    html.Label("基数", className="field-label"),
                                                    dcc.Dropdown(
                                                        id="designer-relation-cardinality",
                                                        options=[{"label": value, "value": value} for value in ("one-to-one", "one-to-many", "many-to-one", "many-to-many")],
                                                        value="many-to-many",
                                                        clearable=False,
                                                        className="graph-filter",
                                                    ),
                                                ],
                                                className="designer-model-control designer-model-control-wide",
                                            ),
                                        ],
                                        className="designer-model-fields",
                                    ),
                                    html.Button("添加关系", id="designer-add-relation", n_clicks=0, className="button"),
                                ],
                                id="designer-model-relation-panel",
                                className="designer-model-panel",
                                hidden=True,
                            ),
                        ],
                        className="form-grid designer-form-grid",
                    ),
                    html.Div(id="designer-model-status", className="status-message"),
                ],
            ),
            html.Div(
                [
                    html.Section(
                        [
                            html.Label("本体文档", htmlFor="designer-json", className="field-label"),
                            dcc.Textarea(
                                id="designer-json",
                                value=raw_document,
                                className="ontology-json",
                                spellCheck=False,
                            ),
                        ],
                        className="designer-editor",
                    ),
                    html.Section(
                        [
                            html.Div(
                                [
                                    html.Div("模式预览", className="panel-heading"),
                                    html.Div(
                                        [
                                            html.Label("布局", htmlFor="designer-preview-layout", className="field-label"),
                                            dcc.Dropdown(
                                                id="designer-preview-layout",
                                                options=[{"label": label, "value": value} for label, value in (("力导向", "cose"), ("环形", "circle"), ("层级", "breadthfirst"))],
                                                value="cose",
                                                clearable=False,
                                                searchable=False,
                                                className="graph-filter designer-preview-mode",
                                            ),
                                            html.Div(
                                                [
                                                    html.Button(html.Img(src=f"/assets/icons/{icon}.svg", alt=""), id=button_id, n_clicks=0, title=label, className="designer-preview-icon", **{"aria-label": label})
                                                    for button_id, icon, label in (
                                                        ("designer-preview-zoom-out", "zoom-out", "缩小预览"),
                                                        ("designer-preview-zoom-in", "zoom-in", "放大预览"),
                                                        ("designer-preview-fit", "maximize", "适配预览"),
                                                        ("designer-preview-download", "download", "导出预览 PNG"),
                                                    )
                                                ],
                                                className="designer-preview-actions",
                                            ),
                                        ],
                                        className="designer-preview-toolbar",
                                    ),
                                ],
                                className="designer-preview-heading",
                            ),
                            cyto.Cytoscape(
                                id="designer-preview",
                                elements=preview_elements(document),
                                layout={"name": "cose"},
                                className="designer-graph",
                                style={"width": "100%", "height": 520},
                                minZoom=0.2,
                                maxZoom=3,
                                responsive=True,
                                stylesheet=[
                                    {"selector": "node", "style": {"label": "data(label)", "background-color": "data(color)", "color": "#f4f7f5", "text-valign": "center", "text-halign": "center", "width": 74, "height": 74, "font-size": 11}},
                                    {"selector": "edge", "style": {"label": "data(label)", "curve-style": "bezier", "target-arrow-shape": "triangle", "line-color": "#84938a", "target-arrow-color": "#84938a", "font-size": 10}},
                                ],
                            ),
                            dcc.Store(id="designer-preview-viewport"),
                        ],
                        className="designer-preview",
                    ),
                ],
                className="designer-grid",
            ),
            html.Div(
                [
                    html.Button("生成分享链接", id="designer-share", n_clicks=0, className="button"),
                    dcc.Input(id="designer-share-url", readOnly=True, placeholder="分享链接将在此生成", className="field-control share-url"),
                ],
                className="share-row",
            ),
            html.Div(id="designer-share-status", className="status-message"),
        ],
    )


def preview_elements(document):
    entity_types = document.get("entityTypes", []) if isinstance(document, dict) else []
    relationships = document.get("relationships", []) if isinstance(document, dict) else []
    nodes = []
    for entity in entity_types:
        if isinstance(entity, dict) and entity.get("id"):
            nodes.append(
                {
                    "data": {
                        "id": str(entity["id"]),
                        "label": str(entity.get("name") or entity["id"]),
                        "color": str(entity.get("color") or "#168577"),
                    }
                }
            )
    node_ids = {node["data"]["id"] for node in nodes}
    edges = []
    for index, relationship in enumerate(relationships):
        if not isinstance(relationship, dict):
            continue
        source = relationship.get("from")
        target = relationship.get("to")
        if source in node_ids and target in node_ids:
            edges.append(
                {
                    "data": {
                        "id": str(relationship.get("id") or f"relationship-{index}"),
                        "source": source,
                        "target": target,
                        "label": str(relationship.get("name") or ""),
                    }
                }
            )
    return nodes + edges


def register_designer_callbacks(app):
    @app.callback(
        Output("designer-suggestion-panel", "hidden"),
        Output("designer-suggestion-toggle", "aria-expanded"),
        Input("designer-suggestion-toggle", "n_clicks"),
        State("designer-suggestion-panel", "hidden"),
        prevent_initial_call=True,
    )
    def toggle_suggestion_panel(_clicks, hidden):
        return not hidden, "true" if hidden else "false"

    app.clientside_callback(
        """
        function(hidden) {
            window.requestAnimationFrame(function() {
                const target = document.getElementById(hidden ? 'designer-suggestion-toggle' : 'designer-suggestion-heading');
                if (target) target.focus();
            });
            return hidden;
        }
        """,
        Output("designer-suggestion-focus", "data"),
        Input("designer-suggestion-panel", "hidden"),
        prevent_initial_call=True,
    )

    @app.callback(
        Output("designer-suggestion-remote", "hidden"),
        Input("designer-suggestion-provider", "value"),
    )
    def show_remote_confirmation(provider):
        return provider != "remote"

    @app.callback(
        Output("designer-json", "value", allow_duplicate=True),
        Output("designer-suggestion-status", "children"),
        Output("designer-rdf-source-xml", "data", allow_duplicate=True),
        Output("designer-rdf-source-document", "data", allow_duplicate=True),
        Output("designer-suggestion-confirm", "value", allow_duplicate=True),
        Input("designer-suggestion-generate", "n_clicks"),
        State("designer-suggestion-provider", "value"),
        State("designer-suggestion-mounts", "value"),
        State("designer-suggestion-documents", "value"),
        State("designer-suggestion-confirm", "value"),
        State("designer-suggestion-token", "value"),
        prevent_initial_call=True,
    )
    def generate_suggestion(_clicks, provider, mounts, documents, confirmation, admin_token):
        conn = db.init_db()
        try:
            result = ontology_suggestions.generate(
                conn, provider or "", mounts or [], documents or [],
                "confirmed" in (confirmation or []), request.headers.get("x-utopia-local-token", "") or admin_token or "",
            )
        except (ValueError, PermissionError) as error:
            return no_update, str(error), no_update, no_update, []
        except Exception:
            return no_update, "模型调用失败，请检查服务配置后重试。", no_update, no_update, []
        finally:
            conn.close()
        evidence = [html.Li(f"{item['element_id']} ← {item['source_id']}") for item in result["evidence"]]
        return json.dumps(result["document"], ensure_ascii=False, indent=2), html.Div([
            html.P("初稿已填入编辑区；请核对来源、修改并保存草稿。"),
            html.Ul(evidence) if evidence else html.P("模型未提供具体出处；请逐项核查。"),
        ]), None, None, []

    @app.callback(
        Output("designer-suggestion-confirm", "value", allow_duplicate=True),
        Input("designer-suggestion-provider", "value"),
        Input("designer-suggestion-mounts", "value"),
        Input("designer-suggestion-documents", "value"),
        prevent_initial_call=True,
    )
    def reset_suggestion_confirmation(_provider, _mounts, _documents):
        return []

    @app.callback(
        Output("designer-version-select", "options"),
        Output("designer-version-select", "value"),
        Output("designer-version-active", "children"),
        Output("designer-version-status", "children"),
        Output("designer-version-create", "disabled"),
        Output("designer-version-evaluate", "disabled"),
        Output("designer-version-accept", "disabled"),
        Output("designer-version-reject", "disabled"),
        Output("designer-version-activate", "disabled"),
        Input("designer-document-id", "data"),
        Input("designer-version-create", "n_clicks"),
        Input("designer-version-evaluate", "n_clicks"),
        Input("designer-version-accept", "n_clicks"),
        Input("designer-version-reject", "n_clicks"),
        Input("designer-version-activate", "n_clicks"),
        State("designer-version-select", "value"),
        State("designer-version-reason", "value"),
    )
    def manage_versions(document_id, _create_clicks, _evaluate_clicks, _accept_clicks, _reject_clicks, _activate_clicks, selected_id, reason_text):
        if not document_id:
            return [], None, "保存草稿后可创建候选版本。", "", True, True, True, True, True

        conn = db.init_db()
        status = ""
        trigger = ctx.triggered_id
        try:
            if trigger == "designer-version-create":
                candidate = ontology_versions.create_candidate(conn, document_id)
                selected_id = candidate["id"]
                status = "Candidate 快照已创建；内容不可变。"
            elif trigger == "designer-version-evaluate":
                report = ontology_versions.evaluate_candidate(conn, selected_id or "")
                status = html.Div(
                    [
                        html.P(
                            f"结构校验{'通过' if report['passed'] else '未通过'}；自动检查不代替人工评审。",
                            className="validation-success" if report["passed"] else "validation-errors",
                        ),
                        html.P("新增：" + ("、".join(report["added_semantics"]) or "无")),
                        html.P("移除：" + ("、".join(report["removed_semantics"]) or "无")),
                        html.P("修改：" + ("、".join(report["changed_semantics"]) or "无")),
                        html.Ul(
                            [html.Li(f"{item['path']}：{item['message']}") for item in report["validation_errors"]],
                            className="validation-errors",
                        ) if report["validation_errors"] else None,
                    ],
                    className="designer-version-evaluation",
                )
            elif trigger == "designer-version-accept":
                ontology_versions.decide_candidate(conn, selected_id or "", "accept", reason_text or "")
                status = "Candidate 已接受并发布。"
            elif trigger == "designer-version-reject":
                ontology_versions.decide_candidate(conn, selected_id or "", "reject", reason_text or "")
                status = "Candidate 已拒绝并保留记录。"
            elif trigger == "designer-version-activate":
                ontology_versions.activate_accepted_version(conn, document_id, selected_id or "")
                status = "已激活所选历史版本。"

            versions = ontology_versions.list_versions(conn, document_id)
            active = ontology_versions.get_active_version(conn, document_id)
            if trigger == "designer-document-id":
                selected_id = active["id"] if active else (versions[0]["id"] if versions else None)
            options = [
                {
                    "label": f"{item['status']} · {item['id'][:8]} · 草稿 v{item['draft_version']}",
                    "value": item["id"],
                }
                for item in versions
            ]
            selected = next((item for item in versions if item["id"] == selected_id), None)
            if active:
                active_text = f"当前发布版：{active['id'][:8]} · Candidate #{active['draft_version']}。MCP 语义查询使用此快照。"
            else:
                active_text = "当前尚无已发布版本。"
            can_decide = bool(selected and selected["status"] == "candidate" and selected["evaluation"])
            return (
                options,
                selected_id,
                active_text,
                status,
                False,
                not bool(selected and selected["status"] == "candidate"),
                not can_decide,
                not can_decide,
                not bool(selected and selected["status"] == "accepted"),
            )
        except ValueError as error:
            versions = ontology_versions.list_versions(conn, document_id)
            active = ontology_versions.get_active_version(conn, document_id)
            options = [
                {"label": f"{item['status']} · {item['id'][:8]} · 草稿 v{item['draft_version']}", "value": item["id"]}
                for item in versions
            ]
            selected = next((item for item in versions if item["id"] == selected_id), None)
            active_text = f"当前发布版：{active['id'][:8]}。MCP 语义查询使用此快照。" if active else "当前尚无已发布版本。"
            can_decide = bool(selected and selected["status"] == "candidate" and selected["evaluation"])
            return options, selected_id, active_text, str(error), False, not bool(selected and selected["status"] == "candidate"), not can_decide, not can_decide, not bool(selected and selected["status"] == "accepted")
        finally:
            conn.close()

    @app.callback(
        Output("designer-model-entity-panel", "hidden"),
        Output("designer-model-property-panel", "hidden"),
        Output("designer-model-relation-panel", "hidden"),
        Input("designer-model-mode", "value"),
    )
    def switch_designer_model_mode(mode):
        return mode != "entity", mode != "property", mode != "relation"

    @app.callback(
        Output("designer-json", "value", allow_duplicate=True),
        Output("designer-history", "data"),
        Output("designer-undo", "disabled"),
        Output("designer-redo", "disabled"),
        Input("designer-json", "value"),
        Input("designer-undo", "n_clicks"),
        Input("designer-redo", "n_clicks"),
        State("designer-history", "data"),
        prevent_initial_call=True,
    )
    def manage_history(raw_document, _undo_clicks, _redo_clicks, history):
        history = history or {"past": [], "future": [], "current": raw_document or ""}
        past = list(history.get("past", []))
        future = list(history.get("future", []))
        current = history.get("current", raw_document or "")

        if ctx.triggered_id == "designer-json":
            new_document = raw_document or ""
            if new_document == current:
                return no_update, no_update, not past, not future
            past = [*past, current][-50:]
            return no_update, {"past": past, "future": [], "current": new_document}, not past, True

        if ctx.triggered_id == "designer-undo":
            if not past:
                return no_update, no_update, True, not future
            target = past.pop()
            future.append(current)
            return target, {"past": past, "future": future, "current": target}, not past, False

        if ctx.triggered_id == "designer-redo":
            if not future:
                return no_update, no_update, not past, True
            target = future.pop()
            past = [*past, current][-50:]
            return target, {"past": past, "future": future, "current": target}, False, not future

        return no_update, no_update, not past, not future

    @app.callback(
        Output("designer-property-entity", "options"),
        Output("designer-relation-from", "options"),
        Output("designer-relation-to", "options"),
        Input("designer-json", "value"),
    )
    def update_model_type_options(raw_document):
        try:
            document = json.loads(raw_document or "{}")
            entity_types = document.get("entityTypes", [])
            options = [
                {"label": str(entity.get("name") or entity.get("id")), "value": entity.get("id")}
                for entity in entity_types
                if isinstance(entity, dict) and entity.get("id")
            ]
            return options, options, options
        except (json.JSONDecodeError, AttributeError, TypeError):
            return [], [], []

    @app.callback(
        Output("designer-json", "value", allow_duplicate=True),
        Output("designer-model-status", "children"),
        Input("designer-add-entity", "n_clicks"),
        Input("designer-add-property", "n_clicks"),
        Input("designer-add-relation", "n_clicks"),
        State("designer-json", "value"),
        State("designer-entity-id", "value"),
        State("designer-entity-name", "value"),
        State("designer-property-entity", "value"),
        State("designer-property-name", "value"),
        State("designer-property-type", "value"),
        State("designer-property-identifier", "value"),
        State("designer-relation-name", "value"),
        State("designer-relation-from", "value"),
        State("designer-relation-to", "value"),
        State("designer-relation-cardinality", "value"),
        prevent_initial_call=True,
    )
    def mutate_model(
        _add_entity_clicks,
        _add_property_clicks,
        _add_relation_clicks,
        raw_document,
        entity_id,
        entity_name,
        property_entity_id,
        property_name,
        property_type,
        identifier_values,
        relationship_name,
        relationship_from,
        relationship_to,
        cardinality,
    ):
        try:
            document = json.loads(raw_document or "{}")
            if not isinstance(document, dict):
                raise ValueError("本体文档必须是 JSON 对象。")
        except (json.JSONDecodeError, ValueError) as error:
            return no_update, str(error)

        if not isinstance(document.get("entityTypes"), list) or not isinstance(document.get("relationships"), list):
            return no_update, "entityTypes 与 relationships 必须是 JSON 数组。"
        action = ctx.triggered_id
        if action == "designer-add-entity":
            name = (entity_name or "").strip()
            new_id = (entity_id or "").strip() or re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
            if not name or not new_id:
                return no_update, "请填写类型名称。"
            if any(entity.get("id") == new_id for entity in document["entityTypes"] if isinstance(entity, dict)):
                return no_update, f"类型 ID 已存在：{new_id}。"
            document["entityTypes"].append(
                {"id": new_id, "name": name, "description": "", "icon": "box", "color": "#168577", "properties": []}
            )
            status = f"已添加类型 {name}。"
        elif action == "designer-add-property":
            entity = next(
                (item for item in document["entityTypes"] if isinstance(item, dict) and item.get("id") == property_entity_id),
                None,
            )
            name = (property_name or "").strip()
            if entity is None or not name:
                return no_update, "请选择所属类型并填写属性名称。"
            properties = entity.setdefault("properties", [])
            if not isinstance(properties, list):
                return no_update, "所属类型的 properties 必须是 JSON 数组。"
            if any(prop.get("name") == name for prop in properties if isinstance(prop, dict)):
                return no_update, f"属性已存在：{name}。"
            prop = {"name": name, "type": property_type or "string"}
            if "identifier" in (identifier_values or []):
                prop["isIdentifier"] = True
            properties.append(prop)
            status = f"已为 {entity.get('name', entity['id'])} 添加属性 {name}。"
        else:
            name = (relationship_name or "").strip()
            if not name or not relationship_from or not relationship_to:
                return no_update, "请填写关系名称并选择起点和终点类型。"
            known_ids = {entity.get("id") for entity in document["entityTypes"] if isinstance(entity, dict)}
            if relationship_from not in known_ids or relationship_to not in known_ids:
                return no_update, "关系端点必须引用已存在的类型。"
            relationship_id = re.sub(r"[^a-z0-9]+", "-", f"{relationship_from}-{name}-{relationship_to}".casefold()).strip("-")
            if any(item.get("id") == relationship_id for item in document["relationships"] if isinstance(item, dict)):
                return no_update, f"关系已存在：{name}。"
            document["relationships"].append(
                {
                    "id": relationship_id,
                    "name": name,
                    "from": relationship_from,
                    "to": relationship_to,
                    "cardinality": cardinality or "many-to-many",
                    "attributes": [],
                }
            )
            status = f"已添加关系 {name}。"
        return json.dumps(document, ensure_ascii=False, indent=2), status

    @app.callback(
        Output("designer-preview", "elements"),
        Input("designer-json", "value"),
    )
    def update_designer_preview(raw_document):
        try:
            return preview_elements(json.loads(raw_document or "{}"))
        except (json.JSONDecodeError, TypeError):
            return []

    @app.callback(Output("designer-preview", "layout"), Input("designer-preview-layout", "value"))
    def update_preview_layout(mode):
        name = mode if mode in ("cose", "circle", "breadthfirst") else "cose"
        return {
            "name": name,
            "fit": True,
            "padding": 35,
            "animate": False,
            "randomize": False,
            "nodeRepulsion": 10000,
            "idealEdgeLength": 100,
        }

    app.clientside_callback(
        """
        function(zoomIn, zoomOut, fitClicks) {
            const container = document.getElementById('designer-preview');
            const cy = container && container._cyreg && container._cyreg.cy;
            if (!cy || cy.destroyed()) return dash_clientside.no_update;
            const trigger = dash_clientside.callback_context.triggered_id;
            if (trigger === 'designer-preview-fit') {
                cy.resize();
                cy.fit(cy.elements(), 35);
            } else {
                const factor = trigger === 'designer-preview-zoom-in' ? 1.25 : 0.8;
                cy.zoom({
                    level: Math.min(cy.maxZoom(), Math.max(cy.minZoom(), cy.zoom() * factor)),
                    renderedPosition: {x: cy.width() / 2, y: cy.height() / 2}
                });
            }
            return {zoom: cy.zoom(), pan: cy.pan()};
        }
        """,
        Output("designer-preview-viewport", "data"),
        Input("designer-preview-zoom-in", "n_clicks"),
        Input("designer-preview-zoom-out", "n_clicks"),
        Input("designer-preview-fit", "n_clicks"),
        prevent_initial_call=True,
    )

    @app.callback(
        Output("designer-preview", "generateImage"),
        Input("designer-preview-download", "n_clicks"),
        prevent_initial_call=True,
    )
    def export_preview(_clicks):
        return {
            "type": "png",
            "action": "download",
            "filename": "ontology-preview",
            "options": {"bg": "#fafbfc", "full": True, "scale": 2},
        }

    @app.callback(
        Output("designer-status", "children"),
        Output("designer-document-id", "data"),
        Output("designer-rdf-source-xml", "data"),
        Output("designer-rdf-source-document", "data"),
        Input("designer-validate", "n_clicks"),
        Input("designer-save", "n_clicks"),
        State("designer-json", "value"),
        State("designer-document-id", "data"),
        State("designer-rdf-source-xml", "data"),
        State("designer-rdf-source-document", "data"),
        prevent_initial_call=True,
    )
    def validate_or_save(_validate_clicks, _save_clicks, raw_document, document_id, source_xml, source_document):
        try:
            document = json.loads(raw_document or "{}")
        except json.JSONDecodeError as error:
            return html.Div(f"JSON 错误：{error.msg}（第 {error.lineno} 行）"), document_id, source_xml, source_document
        if not isinstance(document, dict):
            return html.Div("本体文档必须是 JSON 对象。"), document_id, source_xml, source_document

        errors = ontology_documents.validate_document(document)
        if errors:
            messages = [html.Li(f"{error['path']}：{error['message']}") for error in errors]
            return html.Ul(messages, className="validation-errors"), document_id, source_xml, source_document

        if ctx.triggered_id == "designer-validate":
            return html.Div("校验通过。", className="validation-success"), document_id, source_xml, source_document

        conn = db.init_db()
        if document_id:
            saved = ontology_documents.update_document(
                conn, document_id, document, source_xml, source_document
            )
        else:
            saved = ontology_documents.create_document(
                conn, document, source_xml, source_document
            )
        conn.close()
        if saved is None:
            return html.Div("草稿不存在，请新建后再保存。"), document_id, source_xml, source_document
        return (
            html.Div(f"已保存 {saved['name']} · 版本 {saved['version']}", className="validation-success"),
            saved["id"],
            saved.get("rdf_source_xml"),
            saved.get("rdf_source_document"),
        )

    @app.callback(
        Output("designer-json", "value", allow_duplicate=True),
        Output("designer-document-id", "data", allow_duplicate=True),
        Output("designer-rdf-status", "children"),
        Output("designer-rdf-source-xml", "data"),
        Output("designer-rdf-source-document", "data"),
        Input("designer-rdf-upload", "contents"),
        State("designer-rdf-upload", "filename"),
        prevent_initial_call=True,
    )
    def import_rdf(contents, filename):
        if not contents:
            return no_update, no_update, "", no_update, no_update
        if len(contents) > ontology_rdf.MAX_RDF_BYTES * 2:
            return no_update, no_update, "RDF 文件超过 2MB 限制。", no_update, no_update
        try:
            encoded = contents.split(",", 1)[1]
            rdf_xml = base64.b64decode(encoded, validate=True).decode("utf-8")
            document, warnings = ontology_rdf.parse_document(rdf_xml)
        except (IndexError, ValueError, UnicodeDecodeError) as error:
            return no_update, no_update, f"导入失败：{error}", no_update, no_update
        validation_errors = ontology_documents.validate_document(document)
        messages = [f"已解析 {filename or 'RDF/XML'}。"]
        messages.extend(f"警告：{warning}" for warning in warnings)
        messages.extend(f"校验：{error['path']} {error['message']}" for error in validation_errors)
        return (
            json.dumps(document, ensure_ascii=False, indent=2),
            None,
            html.Ul([html.Li(message) for message in messages]),
            rdf_xml,
            document,
        )

    @app.callback(
        Output("designer-rdf-download", "data"),
        Input("designer-rdf-export", "n_clicks"),
        State("designer-json", "value"),
        State("designer-rdf-source-xml", "data"),
        State("designer-rdf-source-document", "data"),
        prevent_initial_call=True,
    )
    def export_rdf(_clicks, raw_document, source_xml, source_document):
        try:
            document = json.loads(raw_document or "{}")
            if ontology_documents.validate_document(document):
                return no_update
            rdf_xml = ontology_rdf.serialize_document(document, source_xml, source_document)
        except (json.JSONDecodeError, KeyError, TypeError):
            return no_update
        return dcc.send_string(rdf_xml, "ontology.rdf")

    @app.callback(
        Output("designer-rdf-download", "data", allow_duplicate=True),
        Output("designer-version-status", "children", allow_duplicate=True),
        Input("designer-version-export", "n_clicks"),
        State("designer-version-select", "value"),
        prevent_initial_call=True,
    )
    def export_accepted_version(_clicks, version_id):
        conn = db.init_db()
        try:
            version = ontology_versions.get_version(conn, version_id or "")
            if not version or version["status"] != "accepted":
                return no_update, "请选择已批准的版本。"
            rdf_xml = ontology_rdf.serialize_document(version["snapshot"])
            return dcc.send_string(rdf_xml, f"ontology-{version_id}.rdf"), "已导出批准快照。"
        finally:
            conn.close()

    @app.callback(
        Output("designer-share-url", "value"),
        Output("designer-share-status", "children"),
        Input("designer-share", "n_clicks"),
        State("designer-json", "value"),
        prevent_initial_call=True,
    )
    def create_share_link(_clicks, raw_document):
        try:
            document = json.loads(raw_document or "{}")
            token = ontology_sharing.encode_document(document)
        except (json.JSONDecodeError, ValueError) as error:
            return "", str(error)
        return f"{request.host_url.rstrip('/')}/share/{token}", "链接已生成。"