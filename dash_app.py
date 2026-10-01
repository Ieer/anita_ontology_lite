"""Dash 前端：知识图谱浏览 + 双时态时间轴 + 混合检索。

运行：python dash_app.py  →  http://127.0.0.1:8050
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import os

import dash
import dash_cytoscape as cyto
from dash import dcc, html
from dash.dependencies import Input, Output, State
from flask import abort, request

from app import conflict, db, graph, ontology, ontosql, reason, search

app = dash.Dash(__name__, suppress_callback_exceptions=True)
app.title = "Utopia Lite · 知识图谱"


@app.server.before_request
def protect_remote_data_callbacks():
    if request.path != "/_dash-update-component" or request.method != "POST":
        return None
    client_host = request.remote_addr or ""
    try:
        if ipaddress.ip_address(client_host).is_loopback:
            return None
    except ValueError:
        if client_host == "testclient":
            return None
    payload = request.get_json(silent=True) or {}
    protected_ids = ("local-source-", "local-map-", "local-materialize-", "local-markdown-", "cm-map-btn", "cm-materialize-btn", "designer-suggestion-")

    def has_protected_id(value):
        if isinstance(value, dict):
            component_id = value.get("id")
            if component_id == "route-location" and value.get("value") == "/data":
                return True
            if isinstance(component_id, str) and component_id.startswith(protected_ids):
                return True
            return any(has_protected_id(item) for item in value.values())
        if isinstance(value, list):
            return any(has_protected_id(item) for item in value)
        if isinstance(value, str) and value.startswith("_"):
            return any(component_id in value for component_id in protected_ids)
        return False

    if not has_protected_id(payload):
        return None
    expected = os.environ.get("UTOPIA_LOCAL_ADMIN_TOKEN", "")
    supplied = request.headers.get("x-utopia-local-token", "")
    if not expected or not hmac.compare_digest(supplied, expected):
        abort(403, description="本地数据操作仅限本机；远程访问需管理员令牌。")
    return None

from ui.catalogue import catalogue_layout, register_catalogue_callbacks
from ui.data_sources import local_data_layout, register_data_source_callbacks
from ui.designer import designer_layout, register_designer_callbacks
from ui.learn import learn_layout, register_learn_callbacks
from ui.share import register_share_callbacks, share_layout

# 基础样式：节点圆、边带标签
STYLESHEET = [
    {"selector": "node", "style": {"label": "data(label)", "background-color": "#566075", "border-width": 6, "border-color": "#e2e6ec", "color": "#262a30", "font-size": 11, "font-weight": 600, "text-valign": "bottom", "text-margin-y": 9, "text-wrap": "wrap", "text-max-width": 130, "width": 36, "height": 36}},
    {"selector": 'node[type = "Person"]', "style": {"background-color": "#167568", "border-color": "#d4eee7"}},
    {"selector": 'node[type = "Company"]', "style": {"background-color": "#a83f58", "border-color": "#f4dce3", "shape": "round-rectangle"}},
    {"selector": 'node[type = "Project"]', "style": {"background-color": "#5166ae", "border-color": "#e0e5fa", "shape": "diamond", "width": 44, "height": 44}},
    {"selector": "edge", "style": {"label": "data(label)", "curve-style": "bezier", "target-arrow-shape": "triangle", "width": 1.4, "line-color": "#b0bac1", "target-arrow-color": "#89959e", "font-size": 10, "color": "#525c65", "text-background-color": "#fafbfc", "text-background-opacity": 0.92, "text-background-padding": "3px", "text-rotation": "autorotate"}},
    {"selector": "edge.derived", "style": {"line-style": "dashed", "line-color": "#ab702b", "target-arrow-color": "#ab702b"}},
    {"selector": ":selected", "style": {"border-width": 6, "border-color": "#e6a9b8", "line-color": "#a83f58", "target-arrow-color": "#a83f58", "z-index": 10}},
]

# 本体层级图样式：用形状与颜色区分根类型、直接子类和更深层类型。
ONTO_STYLESHEET = [
    {"selector": "node", "style": {"label": "data(label)", "background-color": "#566075", "border-width": 2, "border-color": "#E2E6EC", "color": "#FFFFFF", "font-size": 13, "min-zoomed-font-size": 10, "font-weight": 600, "text-valign": "center", "text-halign": "center", "text-wrap": "wrap", "text-max-width": 92, "width": 58, "height": 58}},
    {"selector": "node.level-one", "style": {"background-color": "#167568", "border-color": "#D4EEE7", "shape": "ellipse"}},
    {"selector": "node.level-deep", "style": {"background-color": "#A83F58", "border-color": "#F4DCE3", "shape": "round-rectangle"}},
    {"selector": "node.root", "style": {"background-color": "#A56D23", "border-color": "#F0C66A", "shape": "diamond", "width": 72, "height": 72}},
    {"selector": "edge", "style": {"label": "data(label)", "curve-style": "bezier", "target-arrow-shape": "triangle", "width": 1.5, "line-color": "#A5AFB8", "target-arrow-color": "#76828D", "font-size": 10, "min-zoomed-font-size": 8, "color": "#4D5863", "text-background-color": "#FBFCFD", "text-background-opacity": 0.95, "text-background-padding": "3px", "text-rotation": "autorotate"}},
]


def build_ontology_elements() -> list[dict]:
    """本体层级图：类型为节点，child ⊑ parent 为边，Thing 标为根。"""
    conn = db.init_db()
    subclass_relations = ontology.list_subclasses(conn)
    parents: dict[str, set[str]] = {}
    for child, parent in subclass_relations:
        parents.setdefault(child, set()).add(parent)
    nodes = []
    for t in ontology.list_types(conn):
        n = {"data": {"id": t, "label": t}}
        if t == "Thing":
            n["classes"] = "root"
        elif "Thing" in parents.get(t, set()):
            n["classes"] = "level-one"
        elif parents.get(t):
            n["classes"] = "level-deep"
        nodes.append(n)
    edges = [
        {"data": {"id": f"sub:{c}->{p}", "source": c, "target": p, "label": "\u2291"}}
        for c, p in subclass_relations
    ]
    conn.close()
    return nodes + edges


def build_elements(
    as_of: str | None = None,
    entity_types: list[str] | None = None,
    predicates: list[str] | None = None,
) -> list[dict]:
    conn = db.init_db()
    entities = graph.list_entities(conn)
    entity_names = {str(r["id"]): r["name"] for r in entities}
    selected_types = set(entity_types or [])
    selected_predicates = set(predicates or [])
    if selected_types:
        entities = [entity for entity in entities if entity["type"] in selected_types]
    visible_ids = {str(entity["id"]) for entity in entities}
    nodes = [
        {
            "data": {
                "id": str(r["id"]),
                "entity_id": r["id"],
                "name": r["name"],
                "type": r["type"],
                "label": f"{r['name']} ({r['type']})",
            }
        }
        for r in entities
    ]
    edges = []
    for r in graph.active_facts(conn, as_of=as_of):
        if selected_predicates and r["predicate"] not in selected_predicates:
            continue
        if str(r["subject_id"]) not in visible_ids or str(r["object_id"]) not in visible_ids:
            continue
        e = {
            "data": {
                "id": f"e{r['id']}",
                "fact_id": r["id"],
                "source": str(r["subject_id"]),
                "target": str(r["object_id"]),
                "subject_name": entity_names.get(str(r["subject_id"]), str(r["subject_id"])),
                "object_name": entity_names.get(str(r["object_id"]), str(r["object_id"])),
                "label": r["predicate"],
                "valid_from": r["valid_from"],
                "valid_to": r["valid_to"],
                "asserted_at": r["asserted_at"],
                "derived": bool(r["derived"]),
            }
        }
        if r["derived"]:
            e["classes"] = "derived"  # 虚线表示推导事实
        edges.append(e)
    conn.close()
    return nodes + edges


def graph_filter_options():
    conn = db.init_db()
    entities = graph.list_entities(conn)
    facts = graph.active_facts(conn)
    conn.close()
    return (
        [{"label": name, "value": name} for name in sorted({row["type"] for row in entities})],
        [{"label": name, "value": name} for name in sorted({row["predicate"] for row in facts})],
    )


def workbench_layout():
    header = html.Header(
            className="topbar",
            children=[
                html.Div(
                    className="brand-lockup",
                    children=[
                        html.Span("U", className="brand-mark"),
                        html.Div(
                            [
                                html.Strong("Utopia Lite"),
                                html.Span("知识工程工作台"),
                            ],
                            className="brand-copy",
                        ),
                    ],
                ),
                html.Nav(
                    [
                        dcc.Link("图谱", href="/", className="page-link active"),
                        dcc.Link("目录", href="/catalogue", className="page-link"),
                        dcc.Link("设计器", href="/designer", className="page-link"),
                        dcc.Link("数据", href="/data", className="page-link"),
                        dcc.Link("学习", href="/learn", className="page-link"),
                    ],
                    className="page-links",
                ),
                html.Button(
                    html.Img(id="theme-toggle-icon", src="/assets/icons/moon.svg", alt=""),
                    id="theme-toggle",
                    n_clicks=0,
                    disabled=True,
                    className="theme-toggle-button",
                    title="切换到深色主题",
                    **{"aria-label": "切换到深色主题"},
                ),
            ],
        )
    controls = html.Aside(
                    className="left-sidebar",
                    children=[
                        html.Div("图谱控制", className="panel-heading"),
                        html.Label("真实世界时间", htmlFor="asof", className="field-label"),
                        dcc.Input(
                            id="asof",
                            type="text",
                            value="2023-06-01",
                            placeholder="如 2020-06-01",
                            debounce=True,
                            className="field-control",
                        ),
                        html.Label("实体类型", htmlFor="entity-type-filter", className="field-label filter-label"),
                        dcc.Dropdown(
                            id="entity-type-filter",
                            options=graph_filter_options()[0],
                            multi=True,
                            placeholder="全部类型",
                            className="graph-filter",
                        ),
                        html.Label("关系类型", htmlFor="predicate-filter", className="field-label filter-label"),
                        dcc.Dropdown(
                            id="predicate-filter",
                            options=graph_filter_options()[1],
                            multi=True,
                            placeholder="全部关系",
                            className="graph-filter",
                        ),
                        html.Button(
                            "运行传递性推导",
                            id="derive-btn",
                            n_clicks=0,
                            className="button button-accent button-full",
                        ),
                    ],
                )
    graph_and_tools = [
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                        html.H1("知识图谱", className="canvas-title"),
                                        html.P("KNOWLEDGE EXPLORER", className="canvas-subtitle"),
                                    ]
                                ),
                                html.Span("AS OF 2023-06-01", id="canvas-asof-label", className="canvas-chip"),
                            ],
                            className="canvas-header",
                        ),
                        cyto.Cytoscape(
                            id="graph",
                            elements=build_elements("2023-06-01"),
                            layout={"name": "cose"},
                            className="graph-canvas",
                            style={"width": "100%", "height": "clamp(400px, 65vh, 820px)"},
                            stylesheet=STYLESHEET,
                        ),
                        html.Section(
                            id="ontology-section",
                            className="tool-section",
                            children=[
                                html.H3("本体编辑与类型推断", className="studio-panel-title"),
                                html.P(
                                    "维护类型层级、检查环与等价类型，并查看实体的推断类型。",
                                    className="section-description",
                                ),
                                html.Div(
                                    [
                                                html.Div(
                                                    [
                                                        html.Div(
                                                            [
                                                                html.Div("类型关系图", className="ontology-graph-title"),
                                                                html.Div(id="ontology-graph-metrics", className="ontology-graph-metrics"),
                                                            ],
                                                            className="ontology-graph-caption",
                                                        ),
                                                        html.Div(
                                                            [
                                                                html.Label("布局", htmlFor="ontology-layout-mode", className="field-label"),
                                                                dcc.Dropdown(
                                                                    id="ontology-layout-mode",
                                                                    options=[{"label": label, "value": value} for label, value in (("层级", "breadthfirst"), ("力导向", "cose"), ("环形", "circle"))],
                                                                    value="cose",
                                                                    clearable=False,
                                                                    searchable=False,
                                                                    className="graph-filter ontology-layout-select",
                                                                ),
                                                                html.Div(
                                                                    [
                                                                        html.Button(html.Img(src=f"/assets/icons/{icon}.svg", alt=""), id=button_id, n_clicks=0, title=label, className="ontology-graph-icon", **{"aria-label": label})
                                                                        for button_id, icon, label in (
                                                                            ("ontology-zoom-out", "zoom-out", "缩小类型图"),
                                                                            ("ontology-zoom-in", "zoom-in", "放大类型图"),
                                                                            ("ontology-fit", "maximize", "适配类型图"),
                                                                        )
                                                                    ],
                                                                    className="ontology-graph-actions",
                                                                ),
                                                                html.Button(
                                                                    html.Img(id="ontology-graph-maximize-icon", src="/assets/icons/maximize.svg", alt=""),
                                                                    id="ontology-graph-maximize",
                                                                    n_clicks=0,
                                                                    title="放大预览",
                                                                    className="ontology-graph-icon ontology-graph-maximize",
                                                                    **{"aria-label": "放大预览"},
                                                                ),
                                                            ],
                                                            className="ontology-graph-toolbar",
                                                        ),
                                                    ],
                                                    className="ontology-graph-header",
                                                ),
                                                cyto.Cytoscape(
                                                    id="onto-graph",
                                                    elements=build_ontology_elements(),
                                                    layout={"name": "cose", "fit": True, "nodeRepulsion": 6500, "idealEdgeLength": 75, "componentSpacing": 25},
                                                    className="ontology-canvas",
                                                    style={"width": "100%", "height": 430},
                                                    minZoom=0.2,
                                                    maxZoom=3,
                                                    responsive=True,
                                                    stylesheet=ONTO_STYLESHEET,
                                                ),
                                                dcc.Store(id="ontology-graph-viewport"),
                                                dcc.Store(id="ontology-graph-expanded", data=False),
                                            ],
                                            id="ontology-graph-wrap",
                                            className="ontology-graph-panel",
                                        ),
                                dcc.RadioItems(
                                    id="ontology-operation-mode",
                                    options=[
                                        {"label": "类型维护", "value": "types"},
                                        {"label": "层级关系", "value": "hierarchy"},
                                        {"label": "命名与校验", "value": "advanced"},
                                    ],
                                    value="types",
                                    className="studio-segments ontology-operation-tabs",
                                ),
                                html.Div(
                                    [
                                        html.Div("添加或删除一个类型定义。", className="ontology-operation-hint"),
                                        html.Div(
                                            [
                                                html.Div(
                                                    [
                                                        html.Label("新类型名", htmlFor="new-type-name", className="field-label"),
                                                        dcc.Input(id="new-type-name", type="text", placeholder="如 Engineer", className="field-control"),
                                                        html.Button("添加类型", id="add-type-btn", n_clicks=0, className="button button-accent"),
                                                    ],
                                                    className="form-field",
                                                ),
                                                html.Div(
                                                    [
                                                        html.Label("删除类型", htmlFor="del-type-name", className="field-label"),
                                                        dcc.Input(id="del-type-name", type="text", placeholder="类型名", className="field-control"),
                                                        html.Button("删除类型", id="del-type-btn", n_clicks=0, className="button button-danger"),
                                                    ],
                                                    className="form-field",
                                                ),
                                            ],
                                            className="form-grid ontology-operation-grid",
                                        ),
                                    ],
                                    id="ontology-operation-types",
                                    className="ontology-operation-panel",
                                ),
                                html.Div(
                                    [
                                        html.Div("用子类和父类描述类型继承。", className="ontology-operation-hint"),
                                        html.Div(
                                            [
                                                html.Div(
                                                    [
                                                        html.Label("子类 → 父类", className="field-label"),
                                                        dcc.Input(id="sub-child", type="text", placeholder="子类，如 Engineer", className="field-control"),
                                                        dcc.Input(id="sub-parent", type="text", placeholder="父类，如 Person", className="field-control"),
                                                        html.Button("添加关系", id="add-subclass-btn", n_clicks=0, className="button button-accent"),
                                                    ],
                                                    className="form-field",
                                                ),
                                                html.Div(
                                                    [
                                                        html.Label("删除子类关系", className="field-label"),
                                                        dcc.Input(id="del-sub-child", type="text", placeholder="子类", className="field-control"),
                                                        dcc.Input(id="del-sub-parent", type="text", placeholder="父类", className="field-control"),
                                                        html.Button("删除关系", id="del-subclass-btn", n_clicks=0, className="button button-danger"),
                                                    ],
                                                    className="form-field",
                                                ),
                                            ],
                                            className="form-grid ontology-operation-grid",
                                        ),
                                    ],
                                    id="ontology-operation-hierarchy",
                                    hidden=True,
                                    className="ontology-operation-panel",
                                ),
                                html.Div(
                                    [
                                        html.Div("整理类型命名，并检查层级是否成环。", className="ontology-operation-hint"),
                                        html.Div(
                                            [
                                                html.Div(
                                                    [
                                                        html.Label("重命名类型", className="field-label"),
                                                        dcc.Input(id="rename-old", type="text", placeholder="旧名", className="field-control"),
                                                        dcc.Input(id="rename-new", type="text", placeholder="新名", className="field-control"),
                                                        html.Button("重命名", id="rename-btn", n_clicks=0, className="button"),
                                                    ],
                                                    className="form-field",
                                                ),
                                                html.Div(
                                                    [
                                                        html.Label("类型等价", className="field-label"),
                                                        dcc.Input(id="equiv-a", type="text", placeholder="如 Person", className="field-control"),
                                                        dcc.Input(id="equiv-b", type="text", placeholder="如 Human", className="field-control"),
                                                        html.Button("声明等价", id="add-equiv-btn", n_clicks=0, className="button"),
                                                    ],
                                                    className="form-field",
                                                ),
                                                html.Div(
                                                    html.Button("检测类型环", id="detect-cycle-btn", n_clicks=0, className="button button-secondary"),
                                                    className="form-field form-field-action",
                                                ),
                                            ],
                                            className="form-grid ontology-operation-grid",
                                        ),
                                    ],
                                    id="ontology-operation-advanced",
                                    hidden=True,
                                    className="ontology-operation-panel",
                                ),
                                html.Div(id="onto-status", className="status-message"),
                                html.Div(
                                    [
                                        html.Label("实体 ID", htmlFor="infer-entity-id", className="field-label"),
                                        dcc.Input(id="infer-entity-id", type="number", placeholder="如 3", className="field-control field-control-compact"),
                                        html.Div(id="entity-types", className="entity-types-result"),
                                    ],
                                    className="infer-row",
                                ),
                            ],
                        ),
                        html.Section(
                            id="mapping-section",
                            className="tool-section",
                            children=[
                                html.H3("字段级映射", className="studio-panel-title"),
                                html.P(
                                    "旧映射记录继续兼容；数据库浏览、查询与快照物化请使用本地数据工作台。",
                                    className="section-description",
                                ),
                                dcc.Link("打开本地数据工作台", href="/data", className="button button-secondary"),
                                html.Div(
                                    [
                                        html.H4("来源字段", className="mapping-group-title"),
                                        html.Div(
                                            [
                                                html.Div([html.Label("挂载名", className="field-label"), dcc.Input(id="cm-mount", type="text", value="hr", placeholder="如 hr", className="field-control")], className="form-field"),
                                                html.Div([html.Label("表名", className="field-label"), dcc.Input(id="cm-table", type="text", value="employees", placeholder="如 employees", className="field-control")], className="form-field"),
                                                html.Div([html.Label("列名", className="field-label"), dcc.Input(id="cm-column", type="text", value="dept", placeholder="如 dept", className="field-control")], className="form-field"),
                                            ],
                                            className="form-grid mapping-grid",
                                        ),
                                    ],
                                    className="mapping-group",
                                ),
                                html.Div(
                                    [
                                        html.H4("目标声明", className="mapping-group-title"),
                                        html.Div(
                                            [
                                                html.Div([html.Label("目标类型", className="field-label"), dcc.Input(id="cm-target-type", type="text", value="Department", placeholder="关系模式", className="field-control")], className="form-field"),
                                                html.Div([html.Label("关系名", className="field-label"), dcc.Input(id="cm-predicate", type="text", value="works_in", placeholder="关系模式", className="field-control")], className="form-field"),
                                                html.Div([html.Label("属性名", className="field-label"), dcc.Input(id="cm-attr-name", type="text", value="", placeholder="属性模式，如 salary", className="field-control")], className="form-field"),
                                            ],
                                            className="form-grid mapping-grid",
                                        ),
                                        html.P("填写属性名时声明属性映射；留空则使用目标类型和关系名声明关系映射。", className="mapping-hint"),
                                    ],
                                    className="mapping-group",
                                ),
                                html.Div(
                                    [
                                        html.Button("声明映射", id="cm-map-btn", n_clicks=0, className="button"),
                                        html.Button("物化流程", id="cm-materialize-btn", n_clicks=0, className="button button-secondary"),
                                    ],
                                    className="button-row",
                                ),
                                html.Div(id="cm-status", className="status-message"),
                            ],
                        ),
                        html.Section(
                            id="instance-section",
                            className="tool-section",
                            children=[
                                html.H3("实例图数据操作", className="studio-panel-title"),
                                dcc.RadioItems(id="instance-mode", options=[{"label": "新增实体", "value": "entity"}, {"label": "新增事实", "value": "fact"}, {"label": "历史纠错", "value": "correct"}], value="entity", className="studio-segments"),
                                html.P("创建实体和事实，或记录一条保留历史的事实纠错。", className="section-description"),
                                html.Div(
                                    [
                                        html.Div(
                                            [
                                                html.Label("实体名称", className="field-label"),
                                                dcc.Input(id="entity-name-input", type="text", placeholder="如 Acme", className="field-control"),
                                                html.Label("实体类型", className="field-label"),
                                                dcc.Input(id="entity-type-input", type="text", value="Thing", className="field-control"),
                                                html.Button("新增实体", id="add-entity-btn", n_clicks=0, className="button button-accent"),
                                            ],
                                            id="instance-entity-form",
                                            className="form-field",
                                        ),
                                        html.Div(
                                            [
                                                html.Label("主体 ID", className="field-label"),
                                                dcc.Input(id="fact-subject-input", type="number", placeholder="实体 ID", className="field-control"),
                                                html.Label("关系", className="field-label"),
                                                dcc.Input(id="fact-predicate-input", type="text", placeholder="如 works_at", className="field-control"),
                                                html.Label("客体 ID", className="field-label"),
                                                dcc.Input(id="fact-object-input", type="number", placeholder="实体 ID", className="field-control"),
                                                html.Label("现实有效期", className="field-label"),
                                                html.Div(
                                                    [
                                                        dcc.Input(id="fact-valid-from-input", type="text", placeholder="起始日期", className="field-control"),
                                                        dcc.Input(id="fact-valid-to-input", type="text", placeholder="结束日期（可空）", className="field-control"),
                                                    ],
                                                    className="inline-fields",
                                                ),
                                                html.Button("新增事实", id="add-fact-btn", n_clicks=0, className="button button-accent"),
                                            ],
                                            id="instance-fact-form",
                                            hidden=True,
                                            className="form-field",
                                        ),
                                        html.Div(
                                            [
                                                html.Label("事实 ID", className="field-label"),
                                                dcc.Input(id="correct-fact-id-input", type="number", placeholder="要纠正的事实 ID", className="field-control"),
                                                html.Label("新客体 ID", className="field-label"),
                                                dcc.Input(id="correct-object-id-input", type="number", placeholder="更正后的实体 ID", className="field-control"),
                                                html.Label("纠错说明", className="field-label"),
                                                dcc.Input(id="correct-note-input", type="text", placeholder="可选", className="field-control"),
                                                html.Button("记录纠错", id="correct-fact-btn", n_clicks=0, className="button button-secondary"),
                                            ],
                                            id="instance-correct-form",
                                            hidden=True,
                                            className="form-field",
                                        ),
                                    ],
                                    className="form-grid mapping-grid",
                                ),
                                html.Div(id="instance-status", className="status-message"),
                            ],
                        ),
                        html.Section(
                            id="resolution-section",
                            className="tool-section",
                            children=[
                                html.H3("冲突与公理处置", className="studio-panel-title"),
                                dcc.RadioItems(id="resolution-mode", options=[{"label": "事实冲突", "value": "conflict"}, {"label": "公理违反", "value": "axiom"}], value="conflict", className="studio-segments"),
                                html.P("先检测事实冲突或公理违反，再选择处置方式；所有决定写入审计记录。", className="section-description"),
                                html.Div([
                                html.Div("事实冲突", className="panel-heading panel-heading-small"),
                                html.Div(
                                    [
                                        html.Div([html.Label("主体 ID", className="field-label"), dcc.Input(id="conflict-subject-id", type="number", placeholder="实体 ID", className="field-control")], className="form-field"),
                                        html.Div([html.Label("关系", className="field-label"), dcc.Input(id="conflict-predicate", type="text", placeholder="如 capital_of", className="field-control")], className="form-field"),
                                        html.Div([html.Label("候选客体 ID", className="field-label"), dcc.Input(id="conflict-object-id", type="number", placeholder="实体 ID", className="field-control")], className="form-field"),
                                        html.Div([html.Label("有效起始日期", className="field-label"), dcc.Input(id="conflict-valid-from", type="text", placeholder="YYYY-MM-DD", className="field-control")], className="form-field"),
                                        html.Div([html.Label("有效结束日期", className="field-label"), dcc.Input(id="conflict-valid-to", type="text", placeholder="可空", className="field-control")], className="form-field"),
                                        html.Div([html.Label("处置方式", className="field-label"), dcc.Dropdown(id="conflict-resolution", options=[{"label": "关闭旧事实", "value": "close"}, {"label": "保留并存", "value": "keep"}, {"label": "拒绝新事实", "value": "reject"}], value="close", clearable=False, className="graph-filter")], className="form-field"),
                                        html.Div([html.Label("处置说明", className="field-label"), dcc.Input(id="conflict-note", type="text", placeholder="可选", className="field-control")], className="form-field"),
                                    ],
                                    className="form-grid mapping-grid",
                                ),
                                html.Div(
                                    [
                                        html.Button("检测事实冲突", id="detect-conflicts-btn", n_clicks=0, className="button"),
                                        html.Button("执行冲突处置", id="resolve-conflicts-btn", n_clicks=0, className="button button-accent"),
                                    ],
                                    className="button-row",
                                ),
                                html.Div(id="conflict-status", className="status-message"),
                                html.Div(id="conflict-results", className="search-results"),
                                ], id="resolution-conflict-form"),
                                html.Div([
                                html.Div("公理违反", className="panel-heading panel-heading-small"),
                                html.Div(
                                    [
                                        html.Div([html.Label("处置事实 ID", className="field-label"), dcc.Input(id="axiom-fact-id", type="number", placeholder="违规事实 ID", className="field-control")], className="form-field"),
                                        html.Div([html.Label("处置方式", className="field-label"), dcc.Dropdown(id="axiom-resolution", options=[{"label": "撤回事实", "value": "retract"}, {"label": "接受并存", "value": "accept"}, {"label": "放宽公理", "value": "relax"}], value="retract", clearable=False, className="graph-filter")], className="form-field"),
                                        html.Div([html.Label("处置说明", className="field-label"), dcc.Input(id="axiom-note", type="text", placeholder="可选", className="field-control")], className="form-field"),
                                    ],
                                    className="form-grid mapping-grid",
                                ),
                                html.Div(
                                    [
                                        html.Button("检测公理违反", id="detect-axioms-btn", n_clicks=0, className="button"),
                                        html.Button("执行公理处置", id="resolve-axiom-btn", n_clicks=0, className="button button-secondary"),
                                    ],
                                    className="button-row",
                                ),
                                html.Div(id="axiom-status", className="status-message"),
                                html.Div(id="axiom-results", className="search-results"),
                                ], id="resolution-axiom-form", hidden=True),
                            ],
                        ),
                    ]
    context_panels = [
                        html.Section(
                            className="sidebar-section search-section",
                            children=[
                                html.Div("文档检索", className="panel-heading"),
                                dcc.Input(
                                    id="query",
                                    type="text",
                                    value="知识底座 双时态",
                                    placeholder="搜索知识与关系…",
                                    className="field-control search-input",
                                ),
                                html.Div(id="results", className="search-results"),
                            ],
                        ),
                        html.Section(
                            className="sidebar-section inspector-section",
                            children=[
                                html.Div(
                                    [
                                        html.Div("实体与关系详情", className="panel-heading"),
                                        html.Button("清空详情", id="clear-inspector-btn", n_clicks=0, className="text-button"),
                                    ],
                                    className="inspector-heading",
                                ),
                                html.Div(
                                    [
                                        html.Div("⌕", className="inspector-empty-icon"),
                                        html.Strong("选择一个实体或关系"),
                                        html.P("查看类型、属性与时间信息。"),
                                    ],
                                    id="inspector-content",
                                    className="inspector-content",
                                ),
                            ],
                        ),
                    ]
    graph_header, graph_canvas, *tools = graph_and_tools
    graph_canvas.layout = {"name": "cose", "padding": 55, "animate": False, "nodeRepulsion": 12000, "idealEdgeLength": 110}
    graph_canvas.minZoom = 0.2
    graph_canvas.maxZoom = 3
    graph_canvas.responsive = True
    search_panel, inspector_panel = context_panels
    panels = {tool.id: tool for tool in tools}
    panels["inspect"] = inspector_panel
    panels["search"] = search_panel
    workflow_options = [
        ("inspect", "详情"),
        ("instance-section", "录入"),
        ("resolution-section", "处置"),
        ("ontology-section", "本体"),
        ("mapping-section", "映射"),
        ("search", "检索"),
    ]
    workspace = html.Main(
        [
            controls,
            html.Section(
                [
                    graph_header,
                    html.Div(id="graph-metrics", className="studio-metrics", **{"aria-live": "polite"}),
                    html.Div(
                        [
                            html.Div(
                                [
                                    dcc.RadioItems(
                                        id="graph-render-mode",
                                        value="2d",
                                        options=[
                                            {
                                                "label": html.Span(
                                                    [html.Img(src="/assets/icons/layout-grid.svg", alt=""), html.Span("二维", className="sr-only")],
                                                    title="切换到二维图谱",
                                                    className="studio-render-icon-content",
                                                ),
                                                "value": "2d",
                                            },
                                            {
                                                "label": html.Span(
                                                    [html.Img(src="/assets/icons/box-3d.svg", alt=""), html.Span("三维", className="sr-only")],
                                                    title="切换到三维图谱",
                                                    className="studio-render-icon-content",
                                                ),
                                                "value": "3d",
                                            },
                                        ],
                                        labelClassName="studio-render-option",
                                        inputClassName="studio-tab-input",
                                        className="studio-render-modes",
                                    ),
                                ],
                                className="studio-render-control",
                            ),
                                html.Div(
                                    [
                            html.Label("布局", htmlFor="graph-layout-mode", className="field-label"),
                            dcc.Dropdown(id="graph-layout-mode", options=[{"label": "力导向", "value": "cose"}, {"label": "环形", "value": "circle"}, {"label": "层级", "value": "breadthfirst"}], value="cose", clearable=False, searchable=False, className="studio-layout-select"),
                                    ],
                                    id="graph-2d-layout-control",
                                    className="studio-render-control",
                                ),
                                html.Div(
                                    [
                                        html.Label("三维布局", htmlFor="graph-3d-layout-mode", className="field-label"),
                                        dcc.Dropdown(
                                            id="graph-3d-layout-mode",
                                            options=[{"label": "力导向", "value": "force"}, {"label": "球形", "value": "sphere"}],
                                            value="force",
                                            clearable=False,
                                            searchable=False,
                                            className="studio-layout-select",
                                        ),
                                    ],
                                    id="graph-3d-layout-control",
                                    className="studio-render-control",
                                    style={"display": "none"},
                                ),
                            html.Div(
                                [
                                    html.Button(html.Img(src=f"/assets/icons/{icon}.svg", alt=""), id=button_id, n_clicks=0, title=label, className="studio-icon-button", **{"aria-label": label})
                                    for button_id, icon, label in [("graph-zoom-out", "zoom-out", "缩小图谱"), ("graph-zoom-in", "zoom-in", "放大图谱"), ("graph-fit", "maximize", "适配画布"), ("graph-download", "download", "导出 PNG")]
                                ],
                                className="studio-view-actions",
                            ),
                            html.Div(id="graph-render-status", className="graph-render-status", role="status"),
                        ],
                        className="studio-toolbar",
                    ),
                    html.Div(
                        [
                            html.Div(graph_canvas, id="graph-2d-view", className="graph-render-view"),
                            html.Div(
                                html.Div(id="graph-3d-status", className="graph-3d-status", role="status"),
                                id="graph-3d-view",
                                className="graph-render-view graph-3d-canvas",
                                style={"display": "none"},
                            ),
                            html.Details(
                                [
                                    html.Summary("图例"),
                                    html.Div(
                                        [
                                            html.Div([html.Span(className="legend-dot legend-entity"), html.Span("人员 Person")], className="legend-row"),
                                            html.Div([html.Span(className="legend-dot legend-company"), html.Span("企业 Company")], className="legend-row"),
                                            html.Div([html.Span(className="legend-dot legend-project"), html.Span("项目 Project")], className="legend-row"),
                                            html.Div([html.Span(className="legend-dot legend-other"), html.Span("其他实体")], className="legend-row"),
                                            html.Div([html.Span(className="legend-line"), html.Span("已记录关系")], className="legend-row"),
                                            html.Div([html.Span(className="legend-line legend-derived"), html.Span("推导关系")], className="legend-row"),
                                        ],
                                        className="graph-legend-items",
                                    ),
                                ],
                                id="graph-legend",
                                className="graph-legend",
                            ),
                        ],
                        className="graph-viewport-shell",
                    ),
                    html.Div(
                        [html.Span("未选择实体", id="graph-selection-label"), html.Button("填入主体", id="use-selected-entity", n_clicks=0, disabled=True, className="button")],
                        className="studio-selection",
                    ),
                    html.Div(id="studio-operation-status", className="studio-operation-status", role="status"),
                ],
                className="studio-stage",
            ),
            html.Aside(
                [
                    html.Div(
                        [
                            html.Div([html.H2("工作流"), html.Span("本地作业空间")]),
                            html.Button(
                                html.Img(id="workflow-collapse-icon", src="/assets/icons/panel-right-close.svg", alt=""),
                                id="workflow-collapse",
                                n_clicks=0,
                                className="studio-collapse-button",
                                title="收起工作流",
                                **{"aria-label": "收起工作流"},
                            ),
                        ],
                        className="studio-dock-heading",
                    ),
                    dcc.RadioItems(
                        id="workflow-mode",
                        value="instance-section",
                        options=[{"label": label, "value": panel_id} for panel_id, label in workflow_options],
                        labelClassName="studio-tab",
                        inputClassName="studio-tab-input",
                        className="studio-tabs",
                    ),
                    *[
                        html.Div(panel, id=f"workflow-{panel_id}", hidden=panel_id != "instance-section", className="studio-workflow-panel")
                        for panel_id, panel in panels.items()
                    ],
                ],
                className="studio-dock",
            ),
        ],
        id="studio-workspace",
        className="studio-workspace",
    )
    return html.Div(
        [
            header,
            workspace,
            dcc.Store(id="graph-viewport"),
            dcc.Store(id="graph-3d-event"),
            dcc.Store(id="graph-selection"),
            dcc.Store(id="graph-3d-selection-sync"),
            dcc.Store(id="graph-render-error"),
            dcc.Store(id="workflow-collapsed", storage_type="session", data=False),
        ],
        className="app-shell studio-shell",
    )


@app.callback(
    [Output(f"workflow-{panel_id}", "hidden") for panel_id in ("inspect", "instance-section", "resolution-section", "ontology-section", "mapping-section", "search")],
    Input("workflow-mode", "value"),
)
def switch_workflow(mode):
    return [mode != panel_id for panel_id in ("inspect", "instance-section", "resolution-section", "ontology-section", "mapping-section", "search")]


@app.callback(
    Output("workflow-collapsed", "data"),
    Input("workflow-collapse", "n_clicks"),
    State("workflow-collapsed", "data"),
    prevent_initial_call=True,
)
def toggle_workflow_panel(_clicks, collapsed):
    return not bool(collapsed)


@app.callback(
    Output("studio-workspace", "className"),
    Output("workflow-collapse-icon", "src"),
    Output("workflow-collapse", "title"),
    Output("workflow-collapse", "aria-label"),
    Input("workflow-collapsed", "data"),
)
def render_workflow_panel(collapsed):
    if collapsed:
        return "studio-workspace studio-workspace-collapsed", "/assets/icons/panel-right-open.svg", "展开工作流", "展开工作流"
    return "studio-workspace", "/assets/icons/panel-right-close.svg", "收起工作流", "收起工作流"


app.clientside_callback(
    """
    function(hidden, mode, expanded) {
        if (!hidden) {
            requestAnimationFrame(() => requestAnimationFrame(() => {
                const container = document.getElementById('onto-graph');
                const cy = container && container._cyreg && container._cyreg.cy;
                if (cy && !cy.destroyed() && container.offsetWidth) {
                    cy.resize();
                    const padding = expanded ? 70 : 40;
                    const layoutOptions = {name: mode || 'cose', fit: false, animate: false, randomize: mode === 'cose'};
                    if (mode === 'breadthfirst') {
                        layoutOptions.directed = false;
                        layoutOptions.roots = '#Thing';
                        layoutOptions.spacingFactor = 0.75;
                    } else if (mode === 'cose') {
                        layoutOptions.nodeRepulsion = 6500;
                        layoutOptions.idealEdgeLength = 75;
                        layoutOptions.componentSpacing = 25;
                    }
                    const layout = cy.layout(layoutOptions);
                    layout.one('layoutstop', () => requestAnimationFrame(() => {
                        cy.resize();
                        cy.fit(cy.elements(), padding);
                    }));
                    layout.run();
                }
            }));
        }
        return dash_clientside.no_update;
    }
    """,
    Output("onto-graph", "layout"),
    Input("workflow-ontology-section", "hidden"),
    Input("ontology-layout-mode", "value"),
    Input("ontology-graph-expanded", "data"),
)


@app.callback(Output("ontology-graph-metrics", "children"), Input("onto-graph", "elements"))
def ontology_graph_metrics(elements):
    elements = elements or []
    types = sum("source" not in element.get("data", {}) for element in elements)
    relationships = sum("source" in element.get("data", {}) for element in elements)
    return f"{types} 个类型 · {relationships} 条子类关系"


app.clientside_callback(
    """
    function(zoomIn, zoomOut, fitClicks) {
        const container = document.getElementById('onto-graph');
        const cy = container && container._cyreg && container._cyreg.cy;
        if (!cy || cy.destroyed()) return dash_clientside.no_update;
        const trigger = dash_clientside.callback_context.triggered_id;
        const expanded = document.getElementById('ontology-graph-wrap').classList.contains('ontology-graph-panel-expanded');
        if (trigger === 'ontology-fit') {
            cy.resize();
            cy.fit(cy.elements(), expanded ? 70 : 24);
        } else {
            const factor = trigger === 'ontology-zoom-in' ? 1.25 : 0.8;
            cy.zoom({
                level: Math.min(cy.maxZoom(), Math.max(cy.minZoom(), cy.zoom() * factor)),
                renderedPosition: {x: cy.width() / 2, y: cy.height() / 2}
            });
        }
        return {zoom: cy.zoom(), pan: cy.pan()};
    }
    """,
    Output("ontology-graph-viewport", "data"),
    Input("ontology-zoom-in", "n_clicks"),
    Input("ontology-zoom-out", "n_clicks"),
    Input("ontology-fit", "n_clicks"),
    prevent_initial_call=True,
)


@app.callback(
    Output("ontology-graph-expanded", "data"),
    Input("ontology-graph-maximize", "n_clicks"),
    State("ontology-graph-expanded", "data"),
    prevent_initial_call=True,
)
def toggle_ontology_graph(_clicks, expanded):
    return not bool(expanded)


@app.callback(
    Output("ontology-graph-wrap", "className"),
    Output("ontology-graph-maximize-icon", "src"),
    Output("ontology-graph-maximize", "title"),
    Output("ontology-graph-maximize", "aria-label"),
    Input("ontology-graph-expanded", "data"),
)
def render_ontology_graph(expanded):
    if expanded:
        return "ontology-graph-panel ontology-graph-panel-expanded", "/assets/icons/minimize-2.svg", "还原预览", "还原预览"
    return "ontology-graph-panel", "/assets/icons/maximize.svg", "放大预览", "放大预览"


@app.callback(
    Output("ontology-operation-types", "hidden"),
    Output("ontology-operation-hierarchy", "hidden"),
    Output("ontology-operation-advanced", "hidden"),
    Input("ontology-operation-mode", "value"),
)
def switch_ontology_operations(mode):
    return mode != "types", mode != "hierarchy", mode != "advanced"


@app.callback([Output(f"instance-{mode}-form", "hidden") for mode in ("entity", "fact", "correct")], Input("instance-mode", "value"))
def switch_instance_form(mode):
    return [mode != form for form in ("entity", "fact", "correct")]


@app.callback(Output("resolution-conflict-form", "hidden"), Output("resolution-axiom-form", "hidden"), Input("resolution-mode", "value"))
def switch_resolution_form(mode):
    return mode != "conflict", mode != "axiom"


@app.callback(Output("graph-metrics", "children"), Input("graph", "elements"))
def graph_metrics(elements):
    nodes = [element for element in elements or [] if "source" not in element["data"]]
    edges = [element for element in elements or [] if "source" in element["data"]]
    derived = sum(bool(edge["data"].get("derived")) for edge in edges)
    return [html.Div([html.Strong(str(count)), html.Span(label)]) for label, count in [("当前实体", len(nodes)), ("有效关系", len(edges)), ("推导事实", derived)]]


@app.callback(Output("graph", "layout"), Input("graph-layout-mode", "value"))
def graph_layout(mode):
    return {"name": mode if mode in ("cose", "circle", "breadthfirst") else "cose", "fit": True, "padding": 55, "animate": False, "randomize": False, "nodeRepulsion": 12000, "idealEdgeLength": 110}


@app.callback(
    Output("graph-2d-view", "style"),
    Output("graph-3d-view", "style"),
    Output("graph-2d-layout-control", "style"),
    Output("graph-3d-layout-control", "style"),
    Output("graph-download", "title"),
    Output("graph-download", "aria-label"),
    Input("graph-render-mode", "value"),
)
def switch_graph_renderer(mode):
    use_3d = mode == "3d"
    return (
        {"display": "none"} if use_3d else {"display": "flex"},
        {"display": "flex"} if use_3d else {"display": "none"},
        {"display": "none"} if use_3d else {"display": "flex"},
        {"display": "flex"} if use_3d else {"display": "none"},
        "导出当前三维视角 PNG" if use_3d else "导出完整图谱 PNG",
        "导出当前三维视角 PNG" if use_3d else "导出完整图谱 PNG",
    )


@app.callback(Output("graph-render-status", "children"), Input("graph-render-error", "data"), Input("graph-render-mode", "value"))
def show_graph_render_error(message, mode):
    return "" if mode == "3d" else (message or "")


app.clientside_callback(
    """
    function(elements, renderMode, layoutMode) {
        if (!window.utopiaGraph3d) return "三维图谱资源正在加载。";
        return window.utopiaGraph3d.updateGraph(elements, renderMode === "3d", layoutMode || "force");
    }
    """,
    Output("graph-3d-status", "children"),
    Input("graph", "elements"), Input("graph-render-mode", "value"), Input("graph-3d-layout-mode", "value"),
)


app.clientside_callback(
    """
    function(selection) {
        if (window.utopiaGraph3d) window.utopiaGraph3d.setSelection(selection);
        return selection || null;
    }
    """,
    Output("graph-3d-selection-sync", "data"),
    Input("graph-selection", "data"),
)


app.clientside_callback(
    """
    function(zoomIn, zoomOut, fitClicks, renderMode) {
        const trigger = dash_clientside.callback_context.triggered_id;
        if (renderMode === "3d") {
            const host = document.getElementById('graph-3d-view');
            const graph = host && host.__utopiaGraph3d && host.__utopiaGraph3d.graph;
            if (!graph) return dash_clientside.no_update;
            if (trigger === 'graph-fit') {
                graph.width(host.clientWidth).height(host.clientHeight).zoomToFit(300, 42);
            } else {
                const camera = graph.cameraPosition();
                const factor = trigger === 'graph-zoom-in' ? 0.8 : 1.25;
                const distance = Math.hypot(camera.x, camera.y, camera.z) * factor;
                const limit = Math.max(80, Math.min(2500, distance));
                const scale = limit / Math.max(1, Math.hypot(camera.x, camera.y, camera.z));
                graph.cameraPosition({x: camera.x * scale, y: camera.y * scale, z: camera.z * scale}, {x: 0, y: 0, z: 0}, 180);
            }
            return {camera: graph.cameraPosition()};
        }
        const container = document.getElementById('graph');
        const cy = container && container._cyreg && container._cyreg.cy;
        if (!cy || cy.destroyed()) return dash_clientside.no_update;
        if (trigger === 'graph-fit') {
            cy.resize();
            cy.fit(cy.elements(), 55);
        } else {
            const factor = trigger === 'graph-zoom-in' ? 1.25 : 0.8;
            cy.zoom({
                level: Math.min(cy.maxZoom(), Math.max(cy.minZoom(), cy.zoom() * factor)),
                renderedPosition: {x: cy.width() / 2, y: cy.height() / 2}
            });
        }
        return {zoom: cy.zoom(), pan: cy.pan()};
    }
    """,
    Output("graph-viewport", "data"),
    Input("graph-zoom-in", "n_clicks"), Input("graph-zoom-out", "n_clicks"), Input("graph-fit", "n_clicks"),
    State("graph-render-mode", "value"),
    prevent_initial_call=True,
)


@app.callback(Output("graph", "generateImage"), Input("graph-download", "n_clicks"), State("graph-render-mode", "value"), prevent_initial_call=True)
def download_graph(_clicks, render_mode="2d"):
    if render_mode == "3d":
        return dash.no_update
    return {"type": "png", "action": "download", "filename": "utopia-graph", "options": {"bg": "#fafbfc", "full": True, "scale": 2}}


@app.callback(
    Output("graph-selection", "data"),
    Input("graph", "tapNodeData"),
    Input("graph", "tapEdgeData"),
    Input("graph-3d-event", "data"),
    Input("graph", "elements"),
    Input("clear-inspector-btn", "n_clicks"),
    State("graph-selection", "data"),
)
def update_graph_selection(node, edge, three_d_event, elements, _clear_clicks, current):
    triggered = dash.callback_context.triggered
    if not triggered:
        return dash.no_update
    prop_id = triggered[0]["prop_id"]
    if prop_id.startswith("clear-inspector-btn."):
        return None
    if prop_id.endswith("tapNodeData"):
        kind, selected_id = "node", (node or {}).get("id")
    elif prop_id.endswith("tapEdgeData"):
        kind, selected_id = "edge", (edge or {}).get("id")
    elif prop_id == "graph-3d-event.data":
        kind, selected_id = (three_d_event or {}).get("kind"), (three_d_event or {}).get("id")
    elif current:
        kind, selected_id = current.get("kind"), current.get("id")
    else:
        return None

    if not kind or selected_id is None:
        return None
    for element in elements or []:
        data = element.get("data", {})
        is_node = "source" not in data
        if str(data.get("id")) == str(selected_id) and ((kind == "node") == is_node):
            return {"kind": kind, "id": str(data["id"])}
    return None


def _selected_graph_data(selection, elements):
    if not selection:
        return None
    kind = selection.get("kind", "node")
    selected_id = selection.get("id")
    for element in elements or []:
        data = element.get("data", {})
        is_node = "source" not in data
        if str(data.get("id")) == str(selected_id) and ((kind == "node") == is_node):
            return data
    return None


@app.callback(Output("graph-selection-label", "children"), Output("use-selected-entity", "disabled"), Input("graph-selection", "data"), Input("graph", "elements"))
def selection_label(selection, elements):
    selected = _selected_graph_data(selection, elements)
    if not selected:
        return "未选择实体", True
    if selection.get("kind") == "edge":
        return f"已选择关系 · {selected.get('label', '')}", True
    return f"{selected['name']} · {selected['type']} · #{selected['entity_id']}", False


@app.callback(
    Output("fact-subject-input", "value"), Output("conflict-subject-id", "value"), Output("infer-entity-id", "value"), Output("workflow-mode", "value"), Output("instance-mode", "value"),
    Input("use-selected-entity", "n_clicks"), State("graph-selection", "data"), State("graph", "elements"), prevent_initial_call=True,
)
def use_selected_entity(_clicks, node, elements):
    node = _selected_graph_data(node, elements)
    if not node or "entity_id" not in node:
        return (dash.no_update,) * 5
    entity_id = node["entity_id"]
    return entity_id, entity_id, entity_id, "instance-section", "fact"


@app.callback(Output("studio-operation-status", "children"), Input("instance-status", "children"))
def show_operation_status(status):
    return status or ""


@app.callback(Output("theme-mode", "data"), Input("theme-toggle", "n_clicks"), State("theme-mode", "data"), prevent_initial_call=True)
def toggle_theme(_clicks, mode):
    if not _clicks:
        return dash.no_update
    return "dark" if mode != "dark" else "light"


@app.callback(
    Output("theme-toggle-icon", "src"),
    Output("theme-toggle", "title"),
    Output("theme-toggle", "aria-label"),
    Output("theme-toggle", "disabled"),
    Input("theme-mode", "data"),
)
def update_theme_toggle(mode):
    if mode == "dark":
        return "/assets/icons/sun.svg", "切换到浅色主题", "切换到浅色主题", False
    if mode == "light":
        return "/assets/icons/moon.svg", "切换到深色主题", "切换到深色主题", False
    return "/assets/icons/moon.svg", "正在恢复主题设置", "正在恢复主题设置", True


app.clientside_callback(
    """
    function(mode) {
        if (mode !== "dark" && mode !== "light") return dash_clientside.no_update;
        const theme = mode === "dark" ? "dark" : "light";
        localStorage.setItem("utopia-theme-mode", theme);
        document.documentElement.dataset.theme = theme;
        return theme;
    }
    """,
    Output("theme-applied", "data"),
    Input("theme-mode", "data"),
)


app.clientside_callback(
    """
    function(mode) {
        if (mode !== "dark" && mode !== "light") return dash_clientside.no_update;
        window.__utopiaThemeMode = mode;
        const applyGraphTheme = function() {
            const container = document.getElementById("graph");
            const cy = container && container._cyreg && container._cyreg.cy;
            if (cy && !(typeof cy.destroyed === "function" && cy.destroyed())) {
                const dark = window.__utopiaThemeMode === "dark";
                cy.nodes().style("color", dark ? "#f1f3f5" : "#262a30");
                cy.edges()
                    .style("color", dark ? "#d1d7de" : "#525c65")
                    .style("text-background-color", dark ? "#20262d" : "#fafbfc")
                    .style("text-background-opacity", dark ? 0.96 : 0.92);
            }
            if (window.utopiaGraph3d) window.utopiaGraph3d.setTheme(window.__utopiaThemeMode);
        };
        window.__utopiaApplyGraphTheme = applyGraphTheme;
        if (!window.__utopiaThemeObserver) {
            window.__utopiaThemeObserver = new MutationObserver(() => window.__utopiaApplyGraphTheme());
            window.__utopiaThemeObserver.observe(document.documentElement, {childList: true, subtree: true});
        }
        applyGraphTheme();
        return {mode: mode};
    }
    """,
    Output("theme-graph-synced", "data"),
    Input("theme-mode", "data"),
)


app.layout = html.Div(
    [
        dcc.Location(id="route-location", refresh=False),
        html.Div(id="route-content"),
        dcc.Store(id="theme-mode"),
        dcc.Store(id="theme-applied"),
        dcc.Store(id="theme-graph-synced"),
    ],
    className="route-shell",
)


@app.callback(
    Output("route-content", "children"),
    Input("route-location", "pathname"),
)
def render_route(pathname: str | None):
    path = (pathname or "/").rstrip("/") or "/"
    if path == "/":
        return workbench_layout()
    if path == "/catalogue":
        return catalogue_layout()
    if path == "/designer":
        return designer_layout()
    if path == "/designer/new":
        return designer_layout()
    if path == "/designer/template-commerce":
        return designer_layout(template="commerce")
    if path == "/data":
        return local_data_layout()
    if path == "/data":
        return local_data_layout()
    if path == "/learn":
        return learn_layout()
    if path.startswith("/learn/"):
        return learn_layout(article_slug=path.rsplit("/", 1)[-1])
    if path.startswith("/share/"):
        return share_layout(path.rsplit("/", 1)[-1])
    if path.startswith("/designer/"):
        return designer_layout(document_id=path.rsplit("/", 1)[-1])
    return html.Div(
        [html.H1("页面不存在"), dcc.Link("返回图谱", href="/")],
        className="not-found-page",
    )


register_designer_callbacks(app)
register_catalogue_callbacks(app)
register_learn_callbacks(app)
register_share_callbacks(app)
register_data_source_callbacks(app)


@app.callback(
    Output("graph", "elements"),
    Output("canvas-asof-label", "children"),
    Output("instance-status", "children"),
    Input("asof", "value"),
    Input("derive-btn", "n_clicks"),
    Input("cm-materialize-btn", "n_clicks"),
    Input("entity-type-filter", "value"),
    Input("predicate-filter", "value"),
    Input("add-entity-btn", "n_clicks"),
    Input("add-fact-btn", "n_clicks"),
    Input("correct-fact-btn", "n_clicks"),
    Input("conflict-status", "children"),
    Input("axiom-status", "children"),
    Input("onto-status", "children"),
    State("entity-name-input", "value"),
    State("entity-type-input", "value"),
    State("fact-subject-input", "value"),
    State("fact-predicate-input", "value"),
    State("fact-object-input", "value"),
    State("fact-valid-from-input", "value"),
    State("fact-valid-to-input", "value"),
    State("correct-fact-id-input", "value"),
    State("correct-object-id-input", "value"),
    State("correct-note-input", "value"),
)
def update_graph(
    asof,
    _derive_clicks,
    _materialize_clicks,
    entity_types,
    predicates,
    _add_entity_clicks,
    _add_fact_clicks,
    _correct_clicks,
    _conflict_status,
    _axiom_status,
    _ontology_status,
    entity_name,
    entity_type,
    fact_subject,
    fact_predicate,
    fact_object,
    valid_from,
    valid_to,
    correct_fact_id,
    correct_object_id,
    correct_note,
):
    triggered_id = dash.callback_context.triggered_id
    status = ""
    if triggered_id == "derive-btn":
        conn = db.init_db()
        derived_count = reason.derive_transitive(conn)
        conn.close()
        status = f"本次新增 {derived_count} 条推导事实。"
    elif triggered_id == "add-entity-btn":
        if not entity_name or not entity_name.strip():
            status = "请输入实体名称。"
        else:
            conn = db.init_db()
            type_name = (entity_type or "Thing").strip() or "Thing"
            ontology.add_type(conn, type_name)
            entity_id = graph.add_entity(conn, entity_name.strip(), type_name)
            conn.close()
            status = f"已新增实体 #{entity_id}。"
    elif triggered_id == "add-fact-btn":
        if not fact_subject or not fact_object or not fact_predicate or not valid_from:
            status = "请填写主体、关系、客体和起始日期。"
        else:
            conn = db.init_db()
            try:
                fact_id = graph.add_fact(
                    conn,
                    int(fact_subject),
                    fact_predicate.strip(),
                    int(fact_object),
                    valid_from.strip(),
                    (valid_to or "").strip() or None,
                    "dash-workbench",
                )
                status = f"已新增事实 #{fact_id}。"
            except Exception as error:
                status = f"新增事实失败：{error}"
            finally:
                conn.close()
    elif triggered_id == "correct-fact-btn":
        if not correct_fact_id or not correct_object_id:
            status = "请填写事实 ID 和新客体 ID。"
        else:
            conn = db.init_db()
            try:
                new_fact_id = graph.correct_fact(
                    conn,
                    int(correct_fact_id),
                    int(correct_object_id),
                    correct_note or "",
                )
                status = f"已记录纠错，新事实 #{new_fact_id}。"
            except ValueError as error:
                status = f"纠错失败：{error}"
            finally:
                conn.close()
    if triggered_id in ("conflict-status", "axiom-status", "onto-status"):
        status = dash.no_update
    return (
        build_elements(asof if asof else None, entity_types, predicates),
        f"AS OF {asof}" if asof else "ALL DATES",
        status,
    )


@app.callback(Output("results", "children"), Input("query", "value"))
def do_search(query: str | None):
    if not query:
        return html.P("输入查询词…")
    conn = db.init_db()
    rows = search.hybrid_search(conn, query, k=5)
    conn.close()
    if not rows:
        return html.P("无结果。")
    items = []
    for r in rows:
        items.append(
            html.Div(
                className="search-result",
                children=[
                    html.Div(f"{r['title']} · 融合分 {r['rrf_score']:.3f}", className="search-result-title"),
                    html.Div(r["content"], className="search-result-content"),
                ],
            )
        )
    return items


@app.callback(
    Output("onto-graph", "elements"),
    Output("onto-status", "children"),
    Input("add-type-btn", "n_clicks"),
    Input("add-subclass-btn", "n_clicks"),
    Input("del-type-btn", "n_clicks"),
    Input("del-subclass-btn", "n_clicks"),
    Input("rename-btn", "n_clicks"),
    Input("detect-cycle-btn", "n_clicks"),
    Input("add-equiv-btn", "n_clicks"),
    State("new-type-name", "value"),
    State("sub-child", "value"),
    State("sub-parent", "value"),
    State("del-type-name", "value"),
    State("del-sub-child", "value"),
    State("del-sub-parent", "value"),
    State("rename-old", "value"),
    State("rename-new", "value"),
    State("equiv-a", "value"),
    State("equiv-b", "value"),
)
def edit_ontology(n_type, n_sub, n_del_type, n_del_sub, n_rename, n_cycle, n_equiv, type_name, child, parent, del_type_name, del_child, del_parent, rename_old, rename_new, equiv_a, equiv_b):
    ctx = dash.callback_context
    if not ctx.triggered:
        return build_ontology_elements(), ""
    trig = ctx.triggered[0]["prop_id"].split(".")[0]
    conn = db.init_db()
    msg = ""
    if trig == "add-type-btn":
        if type_name and type_name.strip():
            ontology.add_type(conn, type_name.strip())
            msg = f"✅ 已添加类型：{type_name.strip()}"
        else:
            msg = "⚠️ 请输入类型名"
    elif trig == "add-subclass-btn":
        if child and child.strip() and parent and parent.strip():
            ok, m = ontology.add_subclass_checked(conn, child.strip(), parent.strip())
            msg = ("✅ " if ok else "⚠️ ") + m
        else:
            msg = "⚠️ 请同时填写子类和父类"
    elif trig == "del-type-btn":
        if del_type_name and del_type_name.strip():
            ok, m = ontology.remove_type(conn, del_type_name.strip())
            msg = ("✅ " if ok else "⚠️ ") + m
        else:
            msg = "⚠️ 请输入要删除的类型名"
    elif trig == "del-subclass-btn":
        if del_child and del_child.strip() and del_parent and del_parent.strip():
            ok, m = ontology.remove_subclass(conn, del_child.strip(), del_parent.strip())
            msg = ("✅ " if ok else "⚠️ ") + m
        else:
            msg = "⚠️ 请同时填写要删除的子类和父类"
    elif trig == "rename-btn":
        if rename_old and rename_old.strip() and rename_new and rename_new.strip():
            ok, m = ontology.rename_type(conn, rename_old.strip(), rename_new.strip())
            msg = ("✅ " if ok else "⚠️ ") + m
        else:
            msg = "⚠️ 请填写旧名和新名"
    elif trig == "detect-cycle-btn":
        cycles = ontology.detect_cycles(conn)
        if cycles:
            cyc_strs = [" → ".join(c) for c in cycles]
            msg = "⚠️ 检测到环：" + "；".join(cyc_strs)
        else:
            msg = "✅ 无环，类型层级是 DAG"
    elif trig == "add-equiv-btn":
        if equiv_a and equiv_a.strip() and equiv_b and equiv_b.strip():
            ok, m = ontology.add_type_equivalence(conn, equiv_a.strip(), equiv_b.strip())
            msg = ("✅ " if ok else "⚠️ ") + m
        else:
            msg = "⚠️ 请填写两个要声明等价的类型"
    elems = build_ontology_elements()
    conn.close()
    return elems, msg


@app.callback(
    Output("entity-types", "children"),
    Input("infer-entity-id", "value"),
)
def show_entity_types(entity_id):
    if entity_id is None:
        return html.P("输入实体 ID 查看推断类型…", style={"fontSize": 13, "color": "#555"})
    conn = db.init_db()
    types = sorted(ontology.infer_entity_types(conn, int(entity_id)))
    conn.close()
    if not types:
        return html.P(f"实体 {entity_id} 不存在或未声明类型", style={"color": "#c00", "fontSize": 13})
    return html.Div(
        [
            html.Span("推断类型：", style={"fontWeight": 600}),
            html.Span(" → ".join(types), style={"color": "#2E9E6B"}),
        ],
        style={"fontSize": 14},
    )


@app.callback(
    Output("cm-status", "children"),
    Input("cm-map-btn", "n_clicks"),
    Input("cm-materialize-btn", "n_clicks"),
    State("cm-mount", "value"),
    State("cm-table", "value"),
    State("cm-column", "value"),
    State("cm-target-type", "value"),
    State("cm-predicate", "value"),
    State("cm-attr-name", "value"),
)
def edit_column_mapping(n_map, n_mat, mount, table, column, target_type, predicate, attr_name):
    ctx = dash.callback_context
    if not ctx.triggered:
        return ""
    trig = ctx.triggered[0]["prop_id"].split(".")[0]
    conn = db.init_db()
    msg = ""
    if trig == "cm-map-btn":
        if not (mount and table and column):
            msg = "⚠️ 请填写挂载名/表名/列名"
        elif attr_name and attr_name.strip():
            ontosql.map_column(conn, mount.strip(), table.strip(), column.strip(), attr_name=attr_name.strip())
            msg = f"✅ 已声明属性映射：{table.strip()}.{column.strip()} → 属性 {attr_name.strip()}"
        elif target_type and predicate:
            ontosql.map_column(conn, mount.strip(), table.strip(), column.strip(), target_type.strip(), predicate.strip())
            msg = f"✅ 已声明关系映射：{table.strip()}.{column.strip()} → ({target_type.strip()}, {predicate.strip()})"
        else:
            msg = "⚠️ 关系模式需填目标类型+关系名；属性模式需填属性名"
    elif trig == "cm-materialize-btn":
        msg = "快照必须先预览并确认，请使用上方本地数据工作台入口。"
    conn.close()
    return msg


@app.callback(
    Output("conflict-status", "children"),
    Output("conflict-results", "children"),
    Input("detect-conflicts-btn", "n_clicks"),
    Input("resolve-conflicts-btn", "n_clicks"),
    State("conflict-subject-id", "value"),
    State("conflict-predicate", "value"),
    State("conflict-object-id", "value"),
    State("conflict-valid-from", "value"),
    State("conflict-valid-to", "value"),
    State("conflict-resolution", "value"),
    State("conflict-note", "value"),
    prevent_initial_call=True,
)
def handle_conflict_action(_detect_clicks, _resolve_clicks, subject_id, predicate, object_id, valid_from, valid_to, resolution, note):
    if not subject_id or not object_id or not predicate or not valid_from:
        return "请填写主体、关系、候选客体和有效起始日期。", no_update
    conn = db.init_db()
    try:
        if dash.callback_context.triggered_id == "detect-conflicts-btn":
            rows = conflict.detect(conn, int(subject_id), predicate.strip(), int(object_id), valid_from.strip(), (valid_to or "").strip() or None)
            names = {row["id"]: row["name"] for row in graph.list_entities(conn)}
            items = [
                html.Div(
                    f"事实 #{row['id']}：{names.get(row['subject_id'], row['subject_id'])} "
                    f"{row['predicate']} {names.get(row['object_id'], row['object_id'])} · "
                    f"{row['valid_from']} 至 {row['valid_to'] or '至今'}",
                    className="search-result-content",
                )
                for row in rows
            ]
            if not rows:
                return "未发现有效期重叠的断言事实冲突。", []
            return f"发现 {len(rows)} 条冲突候选。", items

        new_fact = {
            "subject_id": int(subject_id),
            "predicate": predicate.strip(),
            "object_id": int(object_id),
            "valid_from": valid_from.strip(),
            "valid_to": (valid_to or "").strip() or None,
            "source": "dash-workbench",
        }
        result = conflict.resolve(conn, new_fact, resolution or "close", note or "")
        audit_rows = conflict.list_conflicts(conn)
        items = [
            html.Div(
                f"#{row['id']} · 旧事实 #{row['fact_old']} · 新事实 {('#' + str(row['fact_new'])) if row['fact_new'] else '已拒绝'} · {row['resolution']} · {row['note'] or '无说明'}",
                className="search-result-content",
            )
            for row in audit_rows[-10:]
        ]
        return f"处置完成：{result['resolution']}，新事实 ID {result['new_fact_id'] or '无'}。", items
    except (ValueError, TypeError) as error:
        return f"冲突处置失败：{error}", no_update
    finally:
        conn.close()


@app.callback(
    Output("axiom-status", "children"),
    Output("axiom-results", "children"),
    Input("detect-axioms-btn", "n_clicks"),
    Input("resolve-axiom-btn", "n_clicks"),
    State("axiom-fact-id", "value"),
    State("axiom-resolution", "value"),
    State("axiom-note", "value"),
    prevent_initial_call=True,
)
def handle_axiom_action(_detect_clicks, _resolve_clicks, fact_id, resolution, note):
    conn = db.init_db()
    try:
        if dash.callback_context.triggered_id == "detect-axioms-btn":
            violations = reason.detect_axiom_violations(conn)
            items = [
                html.Div(json.dumps(violation, ensure_ascii=False), className="search-result-content")
                for violation in violations
            ]
            return (f"检测到 {len(violations)} 条公理违反。" if violations else "未检测到公理违反。", items)

        if fact_id is None:
            return "请填写要处置的事实 ID。", no_update
        result = reason.resolve_axiom_violation(conn, int(fact_id), resolution or "retract", note or "")
        ledger = conn.execute(
            "SELECT fact_id, resolution, note, detected_at FROM axiom_violations ORDER BY id DESC LIMIT 10"
        ).fetchall()
        items = [
            html.Div(
                f"事实 #{row['fact_id'] or '无'} · {row['resolution']} · {row['note'] or '无说明'} · {row['detected_at']}",
                className="search-result-content",
            )
            for row in ledger
        ]
        return f"公理处置已记录：事实 #{result['fact_id']} · {result['resolution']}。", items
    except (ValueError, TypeError) as error:
        return f"公理处置失败：{error}", no_update
    finally:
        conn.close()


@app.callback(
    Output("inspector-content", "children"),
    Input("graph-selection", "data"),
    Input("graph", "elements"),
    Input("clear-inspector-btn", "n_clicks"),
)
def inspect_graph_element(selection, elements, _clear_clicks):
    triggered = dash.callback_context.triggered
    if not triggered or triggered[0]["prop_id"].startswith("clear-inspector-btn."):
        return [
            html.Div("⌕", className="inspector-empty-icon"),
            html.Strong("选择一个实体或关系"),
            html.P("查看类型、属性与时间信息。"),
        ]

    selected = _selected_graph_data(selection, elements)
    if not selected:
        return [
            html.Div("⌕", className="inspector-empty-icon"),
            html.Strong("选择一个实体或关系"),
            html.P("查看类型、属性与时间信息。"),
        ]

    def detail_row(label: str, value: object) -> html.Div:
        return html.Div(
            [html.Span(label, className="detail-label"), html.Span(str(value), className="detail-value")],
            className="detail-row",
        )

    conn = db.init_db()
    if selection.get("kind") == "node":
        entity_id = int(selected["entity_id"])
        attributes = graph.get_entity_attributes(conn, entity_id)
        conn.close()
        details = [
            detail_row("名称", selected.get("name", selected.get("label", ""))),
            detail_row("类型", selected.get("type", "Thing")),
            detail_row("实体 ID", entity_id),
        ]
        if attributes:
            details.extend(detail_row(key, value) for key, value in sorted(attributes.items()))
        else:
            details.append(html.Div("暂无实体属性", className="detail-empty"))
        return [
            html.Div("实体", className="inspector-kind"),
            html.H2(selected.get("name", selected.get("label", "实体")), className="inspector-title"),
            html.Div(details, className="detail-list"),
        ]

    fact_id = int(selected["fact_id"])
    attributes = graph.get_fact_attributes(conn, fact_id)
    conn.close()
    valid_to = selected.get("valid_to") or "至今"
    details = [
        detail_row("关系", selected.get("label", "")),
        detail_row("主体", selected.get("subject_name", selected.get("source", ""))),
        detail_row("客体", selected.get("object_name", selected.get("target", ""))),
        detail_row("事实 ID", fact_id),
        detail_row("现实有效期", f"{selected.get('valid_from') or '未知'} 至 {valid_to}"),
        detail_row("系统记录时间", selected.get("asserted_at") or "未知"),
        detail_row("来源", "推导事实" if selected.get("derived") else "已声明事实"),
    ]
    if attributes:
        details.extend(detail_row(key, value) for key, value in sorted(attributes.items()))
    else:
        details.append(html.Div("暂无关系属性", className="detail-empty"))
    return [
        html.Div("关系", className="inspector-kind"),
        html.H2(selected.get("label", "关系"), className="inspector-title"),
        html.Div(details, className="detail-list"),
    ]


if __name__ == "__main__":
    import os
    host = os.environ.get("UTOPIA_HOST", "127.0.0.1")
    port = int(os.environ.get("UTOPIA_PORT", "18050"))
    app.run(debug=False, host=host, port=port)
