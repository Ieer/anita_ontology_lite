from dash import dcc, html
from dash.dependencies import Input, Output

from app import db, ontology_documents
from ui.common import page_layout
from ui.templates import COMMERCE_TEMPLATE


def catalogue_items(query=None):
    conn = db.init_db()
    documents = ontology_documents.list_documents(conn)
    conn.close()
    normalized = (query or "").strip().casefold()
    items = []
    if not normalized or normalized in COMMERCE_TEMPLATE["name"].casefold():
        items.append(
            html.Div(
                [
                    html.Div("样例", className="catalogue-kicker"),
                    html.H2(COMMERCE_TEMPLATE["name"]),
                    html.P(COMMERCE_TEMPLATE["description"]),
                    dcc.Link("从样例开始", href="/designer/template-commerce", className="button button-accent"),
                ],
                className="catalogue-item",
            )
        )
    for document in documents:
        if normalized and normalized not in document["name"].casefold():
            continue
        items.append(
            html.Div(
                [
                    html.Div("已保存", className="catalogue-kicker"),
                    html.H2(document["name"]),
                    html.P(f"版本 {document['version']} · 更新于 {document['updated_at']}"),
                    dcc.Link("打开设计器", href=f"/designer/{document['id']}", className="button"),
                ],
                className="catalogue-item saved-ontology",
                **{"data-name": document["name"].casefold()},
            )
        )
    if not items:
        return [html.P("没有找到匹配的本体。试试缩短关键词。", className="catalogue-empty", role="status")]
    return items


def catalogue_layout():
    return page_layout(
        "catalogue",
        "本体目录",
        [
            html.P("本地样例与已保存的设计草稿。", className="page-description"),
            dcc.Input(
                id="catalogue-search",
                type="search",
                placeholder="搜索本体名称…",
                className="field-control catalogue-search",
            ),
            html.Div(catalogue_items(), id="catalogue-items", className="catalogue-grid"),
        ],
    )


def register_catalogue_callbacks(app):
    @app.callback(Output("catalogue-items", "children"), Input("catalogue-search", "value"))
    def filter_catalogue(query):
        return catalogue_items(query)