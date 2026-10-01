"""Local SQLite sources, mapping snapshots, and Markdown sidecar workspace."""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

from dash import ctx, dash_table, dcc, html, no_update
from dash.dependencies import Input, Output, State

from app import db, evidence, local_data, local_markdown, ontology, ontology_documents, ontosql, semantic_tools, trajectory
from ui.common import page_layout


def _initial_options():
    conn = db.init_db()
    try:
        sources = ontosql.list_mounted(conn)
        markdown_files = local_markdown.list_files(conn)
        documents = ontology_documents.list_documents(conn)
        types = ontology.list_types(conn)
        semantic_objects = semantic_tools.list_semantic_objects(conn)
        evidence_sources = evidence.list_sources(conn, "fact")
        accepted_versions = conn.execute(
            "SELECT v.id, d.name FROM ontology_versions v "
            "JOIN ontology_documents d ON d.id=v.document_id "
            "WHERE v.status='accepted' ORDER BY v.decided_at DESC LIMIT 100"
        ).fetchall()
        tasks = trajectory.list_tasks(conn, limit=100)
    finally:
        conn.close()
    return (
        [{"label": f"{item['name']} · {item['source_filename'] or item['source_kind']}", "value": item["name"]} for item in sources],
        [{"label": f"{item['title']} · {item['file_id'][:8]}", "value": item["file_id"]} for item in markdown_files],
        [{"label": item["name"], "value": item["id"]} for item in documents],
        [{"label": value, "value": value} for value in types],
        [{"label": item["label"], "value": item["value"]} for item in semantic_objects],
        [{"label": item["label"], "value": item["source_id"]} for item in evidence_sources],
        [{"label": f"{item['name']} · {item['id'][:8]}", "value": item["id"]} for item in accepted_versions],
        [{"label": f"{item['status']} · {item['id'][:8]} · {item['event_count']} 次调用", "value": item["id"]} for item in tasks],
    )


def _table(columns, rows, table_id, page_size=12):
    return dash_table.DataTable(
        id=table_id,
        columns=columns,
        data=rows,
        page_size=page_size,
        page_action="native",
        sort_action="native",
        filter_action="none",
        style_table={"overflowX": "auto", "minWidth": 0},
        style_cell={"fontFamily": "inherit", "fontSize": "12px", "padding": "8px", "maxWidth": "260px", "overflow": "hidden", "textOverflow": "ellipsis"},
        style_header={"fontWeight": "650", "backgroundColor": "#f1f3f5", "border": "1px solid #dce0e5"},
        style_data={"border": "1px solid #e6e9ed", "whiteSpace": "normal", "height": "auto"},
    )


def local_data_layout():
    source_options, markdown_options, document_options, type_options, semantic_options, evidence_source_options, version_options, task_options = _initial_options()
    return page_layout(
        "data",
        "本地数据工作台",
        [
            html.P("浏览只读 SQLite 数据源、预览本体映射，并管理关联本体文档的 Markdown 说明。", className="page-description"),
            dcc.Store(id="local-materialization-preview"),
            dcc.Store(id="local-markdown-file-id", data=""),
            dcc.Store(id="local-query-page", data=0),
            dcc.Store(id="local-query-has-more", data=False),
            html.Section(
                [
                    html.Div([html.Span("01", className="local-data-index"), html.Div([html.H2("SQLite 数据源"), html.P("挂载受限目录内的文件，或上传只读副本。查询不会修改源数据库。")])], className="local-data-heading"),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Label("挂载名称", className="field-label"),
                                    dcc.Input(id="local-source-name", type="text", placeholder="如 hr_snapshot", className="field-control"),
                                    html.Label("允许目录内的文件路径", className="field-label"),
                                    dcc.Input(id="local-source-path", type="text", placeholder="由 UTOPIA_SQLITE_ROOT 限定", className="field-control"),
                                    html.Button("挂载本地文件", id="local-source-mount-path", n_clicks=0, className="button"),
                                ],
                                className="local-source-form",
                            ),
                            html.Div(
                                [
                                    html.Label("上传 SQLite 副本", className="field-label"),
                                    dcc.Upload(
                                        id="local-source-upload",
                                        children=html.Div([html.Strong("选择 .db / .sqlite 文件"), html.Span(" 上传后保存到本地数据目录")]),
                                        accept=".db,.sqlite,.sqlite3",
                                        multiple=False,
                                        className="local-dropzone",
                                    ),
                                ],
                                className="local-source-upload",
                            ),
                        ],
                        className="local-source-add-grid",
                    ),
                    html.Div(id="local-source-status", className="local-feedback", role="status"),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Label("数据源", className="field-label"),
                                    dcc.Dropdown(id="local-source-select", options=source_options, placeholder="选择已挂载的数据源", clearable=False, className="graph-filter"),
                                    html.Label("数据表", className="field-label"),
                                    dcc.Dropdown(id="local-source-table", options=[], placeholder="先选择数据源", clearable=False, className="graph-filter"),
                                    html.Div(id="local-source-schema", className="local-schema-list"),
                                ],
                                className="local-schema-pane",
                            ),
                            html.Div(
                                [
                                    html.Div([html.H3("只读查询"), html.Span("结构化筛选")], className="local-subheading"),
                                    html.Div(
                                        [
                                            html.Div([html.Label("返回列", className="field-label"), dcc.Checklist(id="local-query-columns", options=[], value=[], className="local-column-options")], className="local-query-columns"),
                                            html.Div(
                                                [
                                                    html.Div([html.Label("筛选列", className="field-label"), dcc.Dropdown(id="local-filter-column", options=[], placeholder="可选", clearable=True, className="graph-filter")], className="local-filter-control"),
                                                    html.Div([html.Label("条件", className="field-label"), dcc.Dropdown(id="local-filter-operator", options=[{"label": label, "value": value} for label, value in (("等于", "eq"), ("包含", "contains"), (">", "gt"), (">=", "gte"), ("<", "lt"), ("<=", "lte"), ("为空", "is_null"), ("非空", "not_null"))], value="eq", clearable=False, className="graph-filter")], className="local-filter-control"),
                                                    html.Div([html.Label("值", className="field-label"), dcc.Input(id="local-filter-value", type="text", className="field-control")], className="local-filter-control"),
                                                    html.Button("运行查询", id="local-query-run", n_clicks=0, className="button button-accent"),
                                                ],
                                                className="local-query-filter-grid",
                                            ),
                                        ],
                                        className="local-query-options",
                                    ),
                                    html.Details(
                                        [
                                            html.Summary("只读 SQL 专家模式"),
                                            dcc.Textarea(id="local-query-sql", placeholder="SELECT ... FROM ... LIMIT 100", className="field-control local-sql-editor"),
                                            html.Button("执行只读 SQL", id="local-query-sql-run", n_clicks=0, className="button button-secondary"),
                                        ],
                                        className="local-sql-details",
                                    ),
                                    html.Div(id="local-query-status", className="local-feedback", role="status"),
                                    _table([], [], "local-query-results"),
                                    html.Div(
                                        [
                                            html.Button("上一页", id="local-query-previous", n_clicks=0, disabled=True, className="button button-secondary"),
                                            html.Span("第 1 页", id="local-query-page-label", className="local-query-page-label"),
                                            html.Button("下一页", id="local-query-next", n_clicks=0, disabled=True, className="button button-secondary"),
                                        ],
                                        className="local-query-pagination",
                                    ),
                                ],
                                className="local-query-pane",
                            ),
                        ],
                        className="local-source-browser",
                    ),
                ],
                className="local-data-section",
            ),
            dcc.Store(id="agent-task-id", data=""),
            html.Section(
                [
                    html.Div(
                        [html.Span("05", className="local-data-index"), html.Div([html.H2("Agent 语义轨迹"), html.P("仅在显式开始后记录 MCP 语义工具摘要；不保存提问原文、SQL 或数据行。task ID 可传给 browse_semantics / resolve_semantics 关联调用。")])],
                        className="local-data-heading",
                    ),
                    html.Div(
                        [
                            html.Div([html.Label("关联已发布版本（可选）", className="field-label"), dcc.Dropdown(id="agent-task-version", options=version_options, placeholder="不固定版本", clearable=True, className="graph-filter")], className="local-map-control local-evidence-control-wide"),
                            html.Div([html.Label("历史任务", className="field-label"), dcc.Dropdown(id="agent-task-select", options=task_options, placeholder="选择任务查看轨迹", clearable=True, className="graph-filter")], className="local-map-control local-evidence-control-wide"),
                            html.Div(
                                [
                                    html.Button("开始记录", id="agent-task-start", n_clicks=0, className="button button-accent"),
                                    html.Button("刷新轨迹", id="agent-task-refresh", n_clicks=0, className="button button-secondary"),
                                    html.Button("完成任务", id="agent-task-complete", n_clicks=0, disabled=True, className="button button-secondary"),
                                    html.Button("删除任务与轨迹", id="agent-task-delete", n_clicks=0, disabled=True, className="button button-danger"),
                                ],
                                className="agent-task-actions",
                            ),
                        ],
                        className="agent-task-controls",
                    ),
                    html.Div(id="agent-task-status", className="local-feedback", role="status"),
                    html.Div(id="agent-task-events", className="agent-task-events"),
                ],
                className="local-data-section",
            ),
            html.Section(
                [
                    html.Div([html.Span("02", className="local-data-index"), html.Div([html.H2("映射与快照"), html.P("使用稳定源键区分同名记录。物化先预览，只有显式确认后才写入实例图。")])], className="local-data-heading"),
                    html.Div(
                        [
                            html.Div([html.Label("实体类型", className="field-label"), dcc.Dropdown(id="local-map-entity-type", options=type_options, placeholder="选择或输入类型", searchable=True, className="graph-filter")], className="local-map-control"),
                            html.Div([html.Label("显示名称列", className="field-label"), dcc.Dropdown(id="local-map-name-column", options=[], placeholder="选择列", clearable=False, className="graph-filter")], className="local-map-control"),
                            html.Div([html.Label("稳定源键", className="field-label"), dcc.Dropdown(id="local-map-key-column", options=[], placeholder="选择唯一列", clearable=False, className="graph-filter")], className="local-map-control"),
                            html.Button("保存表映射", id="local-map-table-save", n_clicks=0, className="button"),
                        ],
                        className="local-map-table-grid",
                    ),
                    html.Div(
                        [
                            dcc.RadioItems(id="local-map-column-mode", options=[{"label": "属性", "value": "attribute"}, {"label": "关系", "value": "relationship"}], value="attribute", labelClassName="local-mode-option", inputClassName="local-mode-input", className="local-mode-tabs"),
                            html.Div([html.Label("来源列", className="field-label"), dcc.Dropdown(id="local-map-source-column", options=[], placeholder="选择列", clearable=False, className="graph-filter")], className="local-map-control"),
                            html.Div([html.Label("属性名", className="field-label"), dcc.Input(id="local-map-attribute-name", type="text", placeholder="如 salary", className="field-control")], id="local-map-attribute-panel", className="local-map-control"),
                            html.Div([html.Label("目标类型", className="field-label"), dcc.Input(id="local-map-target-type", type="text", placeholder="如 Department", className="field-control"), html.Label("关系名", className="field-label"), dcc.Input(id="local-map-predicate", type="text", placeholder="如 works_in", className="field-control")], id="local-map-relationship-panel", className="local-map-control", hidden=True),
                            html.Button("添加列映射", id="local-map-column-save", n_clicks=0, className="button"),
                        ],
                        className="local-map-column-grid",
                    ),
                    html.Div(id="local-map-status", className="local-feedback", role="status"),
                    html.Div([html.H3("当前列映射"), _table([], [], "local-mappings-table", 8)], className="local-mappings-view"),
                    html.Div(
                        [
                            html.Button("预览导入", id="local-materialize-preview-button", n_clicks=0, className="button button-secondary"),
                            html.Button("确认物化到图谱", id="local-materialize-confirm", n_clicks=0, disabled=True, className="button button-accent"),
                            html.Div(id="local-materialize-preview-status", className="local-feedback", role="status"),
                        ],
                        className="local-materialize-bar",
                    ),
                ],
                className="local-data-section",
            ),
            html.Section(
                [
                    html.Div([html.Span("03", className="local-data-index"), html.Div([html.H2("Markdown 本体说明"), html.P("本地 Markdown 文件是说明正文的来源；SQLite 全文/向量内容可从文件重建。结构化 schema 仍保存在 JSON 本体文档中。")])], className="local-data-heading"),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Label("本地说明文件", className="field-label"),
                                    dcc.Dropdown(id="local-markdown-select", options=markdown_options, placeholder="新建或选择文件", clearable=True, className="graph-filter"),
                                    html.Div(
                                        [
                                            html.Button("载入所选文件（替换编辑区）", id="local-markdown-load", n_clicks=0, className="button button-secondary"),
                                            html.Button("新建空白说明", id="local-markdown-new", n_clicks=0, className="button"),
                                        ],
                                        className="local-markdown-actions",
                                    ),
                                    dcc.Upload(id="local-markdown-upload", children=html.Div("导入 Markdown 文件"), accept=".md,text/markdown,text/plain", multiple=False, className="local-markdown-upload"),
                                    html.Label("标题", className="field-label"),
                                    dcc.Input(id="local-markdown-title", type="text", placeholder="本体说明标题", className="field-control"),
                                    html.Label("关联 JSON 本体（可选）", className="field-label"),
                                    dcc.Dropdown(id="local-markdown-ontology", options=document_options, placeholder="选择本体文档", clearable=True, className="graph-filter"),
                                    dcc.Checklist(id="local-markdown-delete-confirm", options=[{"label": "确认删除源文件及其索引", "value": "confirm"}], value=[], className="local-delete-confirm"),
                                    html.Div([html.Button("保存并更新索引", id="local-markdown-save", n_clicks=0, className="button button-accent"), html.Button("移除索引", id="local-markdown-remove-index", n_clicks=0, className="button button-secondary"), html.Button("删除源文件", id="local-markdown-delete", n_clicks=0, disabled=True, className="button button-danger")], className="local-markdown-actions"),
                                    html.Div(id="local-markdown-status", className="local-feedback", role="status"),
                                ],
                                className="local-markdown-meta",
                            ),
                            html.Div(
                                [
                                    html.Label("Markdown 正文", className="field-label"),
                                    dcc.Textarea(id="local-markdown-body", placeholder="# 本体说明\n\n编写本地领域知识……", className="field-control local-markdown-editor"),
                                    html.Div([html.H3("预览"), dcc.Markdown(id="local-markdown-preview", className="local-markdown-preview")], className="local-markdown-preview-pane"),
                                ],
                                className="local-markdown-content",
                            ),
                        ],
                        className="local-markdown-workspace",
                    ),
                ],
                className="local-data-section",
            ),
            html.Section(
                [
                    html.Div(
                        [html.Span("04", className="local-data-index"), html.Div([html.H2("语义证据"), html.P("为本体术语或映射关联可核验来源；只保存引用和 hash，不复制源内容。")])],
                        className="local-data-heading",
                    ),
                    html.Div(
                        [
                            html.Div([html.Label("语义对象", className="field-label"), dcc.Dropdown(id="evidence-semantic-object", options=semantic_options, placeholder="选择术语、关系或映射", clearable=False, className="graph-filter")], className="local-map-control local-evidence-control-wide"),
                            html.Div([html.Label("来源类型", className="field-label"), dcc.Dropdown(id="evidence-source-type", options=[{"label": label, "value": value} for label, value in (("事实", "fact"), ("文档", "document"), ("Markdown", "markdown"), ("物化批次", "materialization"))], value="fact", clearable=False, className="graph-filter")], className="local-map-control"),
                            html.Div([html.Label("来源记录", className="field-label"), dcc.Dropdown(id="evidence-source", options=evidence_source_options, placeholder="选择来源", clearable=False, className="graph-filter")], className="local-map-control local-evidence-control-wide"),
                            html.Div([html.Label("定位说明（可选）", className="field-label"), dcc.Input(id="evidence-locator", type="text", placeholder="如段落、字段或记录号", className="field-control")], className="local-map-control"),
                            html.Div([html.Label("显示名称（可选）", className="field-label"), dcc.Input(id="evidence-label", type="text", placeholder="便于识别的来源描述", className="field-control")], className="local-map-control"),
                            html.Button("关联证据", id="evidence-add", n_clicks=0, className="button button-accent"),
                        ],
                        className="local-evidence-form",
                    ),
                    html.Div(id="evidence-status", className="local-feedback", role="status"),
                    html.Div(id="evidence-list", className="local-evidence-list"),
                ],
                className="local-data-section",
            ),
        ],
    )


def register_data_source_callbacks(app):
    @app.callback(
        Output("local-source-status", "children"),
        Output("local-source-select", "options"),
        Output("local-source-select", "value"),
        Input("local-source-mount-path", "n_clicks"),
        Input("local-source-upload", "contents"),
        State("local-source-upload", "filename"),
        State("local-source-name", "value"),
        State("local-source-path", "value"),
        prevent_initial_call=True,
    )
    def register_sqlite_source(_mount_clicks, upload_contents, filename, source_name, source_path):
        conn = db.init_db()
        try:
            if ctx.triggered_id == "local-source-upload":
                if not upload_contents:
                    return no_update, no_update, no_update
                header, encoded = upload_contents.split(",", 1)
                if len(encoded) > int(local_data.max_upload_bytes() * 1.4):
                    raise ValueError("上传文件超过大小上限。")
                content = base64.b64decode(encoded, validate=True)
                path, _digest = local_data.store_sqlite_upload(filename or "database.sqlite", content)
                name = (source_name or path.stem).strip() or path.stem
                ontosql.mount_db(conn, name, str(path), source_kind="upload", source_filename=filename or path.name)
            else:
                if not source_path:
                    raise ValueError("请填写受限目录内的 SQLite 文件路径，或上传文件副本。")
                name = (source_name or "").strip() or Path(source_path).stem
                ontosql.mount_db(conn, name, source_path, source_kind="path")
            options = [{"label": f"{item['name']} · {item['source_filename'] or item['source_kind']}", "value": item["name"]} for item in ontosql.list_mounted(conn)]
            return f"数据源“{name}”已挂载为只读。", options, name
        except (ValueError, OSError, sqlite3.Error) as error:
            return f"挂载失败：{error}", no_update, no_update
        finally:
            conn.close()

    @app.callback(
        Output("local-source-table", "options"),
        Output("local-source-table", "value"),
        Output("local-source-schema", "children"),
        Input("local-source-select", "value"),
    )
    def load_source_schema(mount_name):
        if not mount_name:
            return [], None, "选择数据源后查看表结构。"
        conn = db.init_db()
        try:
            path = ontosql.mounted_source_path(conn, mount_name)
            schema = local_data.sqlite_schema(path)
        except ValueError as error:
            return [], None, html.Div(str(error), className="local-inline-error")
        finally:
            conn.close()
        options = [{"label": table["name"], "value": table["name"]} for table in schema]
        summary = [html.Div([html.Strong(table["name"]), html.Span(f"{len(table['columns'])} 列")], className="local-schema-table") for table in schema]
        return options, (options[0]["value"] if options else None), summary or "没有可浏览的数据表。"

    @app.callback(
        Output("local-query-columns", "options"),
        Output("local-query-columns", "value"),
        Output("local-filter-column", "options"),
        Output("local-map-name-column", "options"),
        Output("local-map-name-column", "value"),
        Output("local-map-key-column", "options"),
        Output("local-map-key-column", "value"),
        Output("local-map-source-column", "options"),
        Input("local-source-select", "value"),
        Input("local-source-table", "value"),
    )
    def load_table_columns(mount_name, table_name):
        if not mount_name or not table_name:
            return [], [], [], [], None, [], None, []
        conn = db.init_db()
        try:
            path = ontosql.mounted_source_path(conn, mount_name)
            schema = next(item for item in local_data.sqlite_schema(path) if item["name"] == table_name)
        except (ValueError, StopIteration):
            return [], [], [], [], None, [], None, []
        finally:
            conn.close()
        columns = schema["columns"]
        options = [{"label": f"{item['name']} · {item['type']}", "value": item["name"]} for item in columns]
        primary = next((item["name"] for item in columns if item["primary_key_order"]), columns[0]["name"] if columns else None)
        return options, [item["name"] for item in columns], options, options, (columns[0]["name"] if columns else None), options, primary, options

    @app.callback(
        Output("local-query-results", "columns"),
        Output("local-query-results", "data"),
        Output("local-query-status", "children"),
        Output("local-query-page", "data"),
        Output("local-query-has-more", "data"),
        Output("local-query-page-label", "children"),
        Output("local-query-previous", "disabled"),
        Output("local-query-next", "disabled"),
        Input("local-query-run", "n_clicks"),
        Input("local-query-sql-run", "n_clicks"),
        Input("local-query-previous", "n_clicks"),
        Input("local-query-next", "n_clicks"),
        State("local-source-select", "value"),
        State("local-source-table", "value"),
        State("local-query-columns", "value"),
        State("local-filter-column", "value"),
        State("local-filter-operator", "value"),
        State("local-filter-value", "value"),
        State("local-query-sql", "value"),
        State("local-query-page", "data"),
        prevent_initial_call=True,
    )
    def run_source_query(_table_clicks, _sql_clicks, _previous_clicks, _next_clicks, mount_name, table_name, selected_columns, filter_column, operator, filter_value, sql, current_page):
        if not mount_name:
            return [], [], "先挂载并选择数据源。", 0, False, "第 1 页", True, True
        page = max(int(current_page or 0), 0)
        if ctx.triggered_id in ("local-query-run", "local-query-sql-run"):
            page = 0
        elif ctx.triggered_id == "local-query-previous":
            page = max(0, page - 1)
        elif ctx.triggered_id == "local-query-next":
            page += 1
        conn = db.init_db()
        try:
            path = ontosql.mounted_source_path(conn, mount_name)
            if ctx.triggered_id == "local-query-sql-run":
                result = local_data.execute_readonly(path, sql or "", row_limit=1001)
                rows = result["rows"][:1000]
                return [{"name": value, "id": value} for value in result["columns"]], rows, f"只读查询返回 {len(rows)} 行。" + ("结果已达到 1000 行上限。" if result["has_more"] else ""), 0, result["has_more"], "SQL 结果", True, True
            if not table_name:
                return [], [], "请先选择数据表。", 0, False, "第 1 页", True, True
            filters = []
            if filter_column:
                filters.append({"column": filter_column, "operator": operator, "value": filter_value})
            result = local_data.query_table(path, table_name, selected_columns, filters, limit=100, offset=page * 100)
            return [{"name": value, "id": value} for value in result["columns"]], result["rows"], f"显示 {len(result['rows'])} 行。", page, result["has_more"], f"第 {page + 1} 页", page == 0, not result["has_more"]
        except ValueError as error:
            return [], [], f"查询失败：{error}", 0, False, "第 1 页", True, True
        finally:
            conn.close()

    @app.callback(
        Output("local-map-status", "children"),
        Input("local-map-table-save", "n_clicks"),
        State("local-source-select", "value"),
        State("local-source-table", "value"),
        State("local-map-entity-type", "value"),
        State("local-map-name-column", "value"),
        State("local-map-key-column", "value"),
        prevent_initial_call=True,
    )
    def save_source_table_mapping(_clicks, mount_name, table, entity_type, name_column, key_column):
        if not all((mount_name, table, entity_type, name_column, key_column)):
            return "请选择数据源、表、显示名称列、稳定源键，并填写实体类型。"
        conn = db.init_db()
        try:
            ontosql.map_table(conn, mount_name, table, entity_type, name_column, key_column)
            return "表映射已保存。"
        except (ValueError, sqlite3.Error) as error:
            return f"表映射失败：{error}"
        finally:
            conn.close()

    @app.callback(
        Output("local-map-attribute-panel", "hidden"),
        Output("local-map-relationship-panel", "hidden"),
        Input("local-map-column-mode", "value"),
    )
    def switch_column_mapping_mode(mode):
        return mode != "attribute", mode != "relationship"

    @app.callback(
        Output("local-map-status", "children", allow_duplicate=True),
        Input("local-map-column-save", "n_clicks"),
        State("local-source-select", "value"),
        State("local-source-table", "value"),
        State("local-map-source-column", "value"),
        State("local-map-column-mode", "value"),
        State("local-map-attribute-name", "value"),
        State("local-map-target-type", "value"),
        State("local-map-predicate", "value"),
        prevent_initial_call=True,
    )
    def save_source_column_mapping(_clicks, mount_name, table, column, mode, attribute_name, target_type, predicate):
        if not all((mount_name, table, column)):
            return "请选择数据源、表和来源列。"
        if mode == "attribute" and not attribute_name:
            return "请填写目标属性名。"
        if mode == "relationship" and not all((target_type, predicate)):
            return "请填写目标类型与关系名。"
        conn = db.init_db()
        try:
            ontosql.map_column(
                conn, mount_name, table, column,
                target_type if mode == "relationship" else "",
                predicate if mode == "relationship" else "",
                attribute_name if mode == "attribute" else "",
            )
            return "列映射已保存。"
        except (ValueError, sqlite3.Error) as error:
            return f"列映射失败：{error}"
        finally:
            conn.close()

    @app.callback(
        Output("local-mappings-table", "columns"),
        Output("local-mappings-table", "data"),
        Input("local-source-select", "value"),
        Input("local-source-table", "value"),
        Input("local-map-table-save", "n_clicks"),
        Input("local-map-column-save", "n_clicks"),
    )
    def show_source_mappings(mount_name, table, _table_clicks, _column_clicks):
        if not mount_name:
            return [], []
        conn = db.init_db()
        try:
            table_mappings = [item for item in ontosql.list_mappings(conn) if item["mount_name"] == mount_name and (not table or item["table_name"] == table)]
            column_mappings = [item for item in ontosql.list_column_mappings(conn) if item["mount_name"] == mount_name and (not table or item["table_name"] == table)]
            rows = [
                {"种类": "表", "表": item["table_name"], "来源列": item["key_col"], "映射": f"{item['entity_type']} · {item['name_col']}"}
                for item in table_mappings
            ] + [
                {"种类": "列", "表": item["table_name"], "来源列": item["column_name"], "映射": item["attr_name"] or f"{item['target_type']} · {item['predicate']}"}
                for item in column_mappings
            ]
            columns = [{"name": key, "id": key} for key in ("种类", "表", "来源列", "映射")]
            return columns, rows
        finally:
            conn.close()

    @app.callback(
        Output("local-materialization-preview", "data"),
        Output("local-materialize-preview-status", "children"),
        Output("local-materialize-confirm", "disabled"),
        Input("local-materialize-preview-button", "n_clicks"),
        State("local-source-select", "value"),
        prevent_initial_call=True,
    )
    def preview_snapshot(_clicks, mount_name):
        if not mount_name:
            return None, "请选择数据源。", True
        conn = db.init_db()
        try:
            preview = ontosql.preview_materialization(conn, mount_name)
        except ValueError as error:
            return None, f"预览失败：{error}", True
        finally:
            conn.close()
        summary = preview["summary"]
        text = f"预览：{summary['rows']} 行，约 {summary['entities']} 个实体、{summary['facts']} 条关系、{summary['attributes']} 个属性；事实有效自 {preview['valid_from']}。"
        if preview["errors"]:
            text += " 阻止原因：" + "；".join(preview["errors"])
        if preview["already_materialized"]:
            text += " 此数据源与映射组合已物化过，重复确认不会重复导入。"
        return preview, text, not preview["ok"] or preview["already_materialized"]

    @app.callback(
        Output("local-materialize-preview-status", "children", allow_duplicate=True),
        Input("local-materialize-confirm", "n_clicks"),
        State("local-materialization-preview", "data"),
        State("local-source-select", "value"),
        prevent_initial_call=True,
    )
    def confirm_snapshot(_clicks, expected, mount_name):
        if not expected or not expected.get("ok") or expected.get("already_materialized"):
            return "请先生成有效预览；已物化的同一快照不会重复导入。"
        if expected.get("mount_name") != mount_name:
            return "数据源已更改，请重新预览。"
        conn = db.init_db()
        try:
            current = ontosql.preview_materialization(conn, mount_name)
            if current["source_sha256"] != expected["source_sha256"] or current["mapping_sha256"] != expected["mapping_sha256"]:
                return "数据源或映射在预览后发生变化，请重新预览。"
            result = ontosql.materialize_mappings(conn, mount_name)
            return f"快照 #{result['batch_id']} 已导入：新增 {result['entities_created']} 个实体、{result['facts_created']} 条关系、{result['attrs_created']} 个属性；有效自 {result['valid_from']}。图谱时间早于该日期时，导入关系不会显示。"
        except ValueError as error:
            return f"物化失败：{error}"
        finally:
            conn.close()

    @app.callback(
        Output("local-markdown-preview", "children"),
        Input("local-markdown-body", "value"),
    )
    def preview_markdown_body(body):
        return body or ""

    @app.callback(
        Output("local-markdown-file-id", "data"),
        Output("local-markdown-select", "value", allow_duplicate=True),
        Output("local-markdown-title", "value"),
        Output("local-markdown-body", "value"),
        Output("local-markdown-ontology", "value"),
        Output("local-markdown-status", "children"),
        Output("local-markdown-delete-confirm", "value"),
        Input("local-markdown-load", "n_clicks"),
        Input("local-markdown-new", "n_clicks"),
        State("local-markdown-select", "value"),
        prevent_initial_call=True,
    )
    def load_markdown_file(_load_clicks, _new_clicks, file_id):
        if ctx.triggered_id == "local-markdown-new":
            return "", None, "", "", None, "新建说明文件。", []
        if not file_id:
            return no_update, no_update, no_update, no_update, no_update, "请先选择要载入的 Markdown 文件。", no_update
        conn = db.init_db()
        try:
            result = local_markdown.get_file(conn, file_id)
        except ValueError as error:
            return file_id, no_update, no_update, no_update, no_update, str(error), no_update
        finally:
            conn.close()
        if result is None:
            return "", None, "", "", None, "Markdown 文件不存在。", []
        return file_id, file_id, result["title"], result["body"], result["ontology_document_id"], "本地 Markdown 已载入。", []

    @app.callback(
        Output("local-markdown-file-id", "data", allow_duplicate=True),
        Output("local-markdown-select", "options"),
        Output("local-markdown-select", "value", allow_duplicate=True),
        Output("local-markdown-status", "children", allow_duplicate=True),
        Input("local-markdown-save", "n_clicks"),
        Input("local-markdown-upload", "contents"),
        State("local-markdown-upload", "filename"),
        State("local-markdown-file-id", "data"),
        State("local-markdown-title", "value"),
        State("local-markdown-body", "value"),
        State("local-markdown-ontology", "value"),
        prevent_initial_call=True,
    )
    def save_or_import_markdown(_save_clicks, upload_contents, filename, file_id, title, body, ontology_id):
        conn = db.init_db()
        try:
            if ctx.triggered_id == "local-markdown-upload":
                if not upload_contents:
                    return no_update, no_update, no_update, no_update
                header, encoded = upload_contents.split(",", 1)
                if len(encoded) > int(local_markdown.MAX_MARKDOWN_BYTES * 1.4):
                    raise ValueError("Markdown 文件超过 5 MiB。")
                imported = local_markdown.import_file(conn, filename or "note.md", base64.b64decode(encoded, validate=True))
                current_id, status = imported["file_id"], "Markdown 文件已导入、保存并建立本地索引。"
            else:
                saved = local_markdown.save_file(conn, title or "", body or "", ontology_id or "", file_id or "")
                current_id, status = saved["file_id"], "Markdown 文件已保存并更新本地索引。"
            options = [{"label": f"{item['title']} · {item['file_id'][:8]}", "value": item["file_id"]} for item in local_markdown.list_files(conn)]
            return current_id, options, current_id, status
        except (ValueError, OSError, sqlite3.Error) as error:
            return no_update, no_update, no_update, f"保存失败：{error}"
        finally:
            conn.close()

    @app.callback(
        Output("local-markdown-status", "children", allow_duplicate=True),
        Input("local-markdown-remove-index", "n_clicks"),
        State("local-markdown-file-id", "data"),
        prevent_initial_call=True,
    )
    def remove_markdown_index(_clicks, file_id):
        if not file_id:
            return "请先选择本地 Markdown 文件。"
        conn = db.init_db()
        try:
            removed = local_markdown.remove_index(conn, file_id)
            return "本地索引已移除；Markdown 源文件仍保留。" if removed else "Markdown 文件不存在。"
        finally:
            conn.close()

    @app.callback(
        Output("local-markdown-delete", "disabled"),
        Input("local-markdown-delete-confirm", "value"),
        Input("local-markdown-file-id", "data"),
    )
    def gate_markdown_source_delete(confirmed, file_id):
        return not (file_id and "confirm" in (confirmed or []))

    @app.callback(
        Output("local-markdown-file-id", "data", allow_duplicate=True),
        Output("local-markdown-select", "options", allow_duplicate=True),
        Output("local-markdown-select", "value", allow_duplicate=True),
        Output("local-markdown-title", "value", allow_duplicate=True),
        Output("local-markdown-body", "value", allow_duplicate=True),
        Output("local-markdown-status", "children", allow_duplicate=True),
        Output("local-markdown-delete-confirm", "value", allow_duplicate=True),
        Input("local-markdown-delete", "n_clicks"),
        State("local-markdown-file-id", "data"),
        prevent_initial_call=True,
    )
    def delete_markdown_source(_clicks, file_id):
        if not file_id:
            return no_update, no_update, no_update, no_update, no_update, "请先选择要删除的 Markdown 源文件。", no_update
        conn = db.init_db()
        try:
            deleted = local_markdown.delete_source(conn, file_id)
            options = [{"label": f"{item['title']} · {item['file_id'][:8]}", "value": item["file_id"]} for item in local_markdown.list_files(conn)]
            return "", options, None, "", "", ("Markdown 源文件与对应索引已删除。" if deleted else "Markdown 文件不存在。"), []
        finally:
            conn.close()

    @app.callback(
        Output("evidence-source", "options"),
        Output("evidence-source", "value"),
        Input("evidence-source-type", "value"),
    )
    def load_evidence_sources(source_type):
        if not source_type:
            return [], None
        conn = db.init_db()
        try:
            sources = evidence.list_sources(conn, source_type)
            options = [{"label": item["label"], "value": item["source_id"]} for item in sources]
            return options, options[0]["value"] if options else None
        except ValueError:
            return [], None
        finally:
            conn.close()

    @app.callback(
        Output("evidence-status", "children"),
        Output("evidence-list", "children"),
        Input("evidence-add", "n_clicks"),
        Input("evidence-semantic-object", "value"),
        State("evidence-source-type", "value"),
        State("evidence-source", "value"),
        State("evidence-locator", "value"),
        State("evidence-label", "value"),
        prevent_initial_call=True,
    )
    def manage_evidence(_clicks, semantic_object_id, source_type, source_id, locator, label):
        if not semantic_object_id:
            return "请选择要关联的语义对象。", []
        conn = db.init_db()
        try:
            if ctx.triggered_id == "evidence-add":
                if not source_type or not source_id:
                    references = evidence.list_references(conn, semantic_object_id)
                    return "请先选择来源记录。", _render_evidence(references)
                evidence.add_reference(conn, semantic_object_id, source_type, source_id, locator or "", label or "")
            references = evidence.list_references(conn, semantic_object_id)
            status = "证据引用已记录。" if ctx.triggered_id == "evidence-add" else ""
            return status, _render_evidence(references)
        except (ValueError, OSError, sqlite3.Error) as error:
            return f"无法关联证据：{error}", []
        finally:
            conn.close()

    @app.callback(
        Output("agent-task-select", "options"),
        Output("agent-task-select", "value"),
        Output("agent-task-id", "data"),
        Output("agent-task-status", "children"),
        Output("agent-task-events", "children"),
        Output("agent-task-complete", "disabled"),
        Output("agent-task-delete", "disabled"),
        Input("agent-task-start", "n_clicks"),
        Input("agent-task-refresh", "n_clicks"),
        Input("agent-task-complete", "n_clicks"),
        Input("agent-task-delete", "n_clicks"),
        Input("agent-task-select", "value"),
        State("agent-task-version", "value"),
        State("agent-task-id", "data"),
        prevent_initial_call=True,
    )
    def manage_agent_task(_start_clicks, _refresh_clicks, _complete_clicks, _delete_clicks, selected_id, version_id, active_task_id):
        conn = db.init_db()
        status = ""
        trigger = ctx.triggered_id
        try:
            if trigger == "agent-task-start":
                task = trajectory.create_task(conn, version_id)
                selected_id = task["id"]
                status = "轨迹记录已开始。将 task ID 传给 MCP 语义工具以关联调用。"
            elif trigger == "agent-task-complete":
                task = trajectory.complete_task(conn, active_task_id or selected_id or "")
                selected_id = task["id"]
                status = "任务已完成；后续工具调用不会再写入此轨迹。"
            elif trigger == "agent-task-delete":
                trajectory.delete_task(conn, active_task_id or selected_id or "")
                selected_id = None
                status = "任务及其轨迹事件已删除。"

            task = trajectory.get_task(conn, selected_id or "") if selected_id else None
            tasks = trajectory.list_tasks(conn)
            options = [
                {"label": f"{item['status']} · {item['id'][:8]} · {item['event_count']} 次调用", "value": item["id"]}
                for item in tasks
            ]
            if task:
                active = task["status"] == "active"
                task_status = html.Div(
                    [
                        html.Span(status or ("记录中" if active else "已完成")),
                        html.Code(f"task_id={task['id']}"),
                        html.Span(f" · 固定版本：{task['ontology_version_id'][:8]}" if task["ontology_version_id"] else " · 未固定版本"),
                    ],
                    className="agent-task-status-row",
                )
                return options, selected_id, task["id"] if active else "", task_status, _render_task_events(task["events"]), not active, False
            if trigger == "agent-task-select" and selected_id:
                status = "找不到该任务。"
            return options, selected_id, "", status, html.P("暂无轨迹事件。", className="local-evidence-empty"), True, True
        except ValueError as error:
            task = trajectory.get_task(conn, selected_id or "") if selected_id else None
            return (
                trajectory_options(conn),
                selected_id,
                task["id"] if task and task["status"] == "active" else "",
                str(error),
                _render_task_events(task["events"]) if task else html.P("暂无轨迹事件。", className="local-evidence-empty"),
                not bool(task and task["status"] == "active"),
                not bool(task),
            )
        finally:
            conn.close()


def _render_evidence(references):
    if not references:
        return html.P("该语义对象尚无证据引用。", className="local-evidence-empty")
    return [
        html.Div(
            [
                html.Div(
                    [
                        html.Strong(reference["label"] or f"{reference['source_type']} · {reference['source_id']}"),
                        html.Span(reference["state"], className=f"local-evidence-state is-{reference['state']}"),
                    ],
                    className="local-evidence-item-heading",
                ),
                html.P(f"来源：{reference['source_type']} · {reference['source_id']}"),
                html.P(f"定位：{reference['locator'] or '未指定'} · SHA-256：{reference['captured_sha256'][:16]}…"),
            ],
            className="local-evidence-item",
        )
        for reference in references
    ]


def trajectory_options(conn):
    return [
        {"label": f"{item['status']} · {item['id'][:8]} · {item['event_count']} 次调用", "value": item["id"]}
        for item in trajectory.list_tasks(conn)
    ]


def _render_task_events(events):
    if not events:
        return html.P("暂无轨迹事件。", className="local-evidence-empty")
    return [
        html.Div(
            [
                html.Div([html.Strong(event["tool_name"]), html.Span(event["outcome"])], className="agent-task-event-heading"),
                html.P("语义对象：" + ("、".join(event["semantic_object_ids"]) or "无")),
                html.P("证据引用：" + ("、".join(event["evidence_ref_ids"]) or "无")),
                html.P(f"结果数：{event['result_count']} · {event['created_at']}"),
            ],
            className="agent-task-event",
        )
        for event in events
    ]
