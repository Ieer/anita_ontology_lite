import json

from dash import dcc, html, no_update
from dash.dependencies import Input, Output, State

from app import db, ontology_documents, ontology_sharing
from ui.common import page_layout


def share_layout(token):
    try:
        document = ontology_sharing.decode_document(token)
        error = None
    except ValueError as exception:
        document = None
        error = str(exception)
    children = [
        html.P("此链接包含一份只读本体副本。", className="page-description"),
    ]
    if error:
        children.append(html.Div(f"分享内容无法打开：{error}", className="error-message"))
    else:
        children.extend(
            [
                dcc.Store(id="shared-ontology", data=document),
                html.Div(
                    [
                        html.Strong(document["name"], className="inspector-title"),
                        html.Span(f"{len(document.get('entityTypes', []))} 个类型 · {len(document.get('relationships', []))} 个关系", className="detail-label"),
                    ],
                    className="shared-summary",
                ),
                dcc.Textarea(
                    value=json.dumps(document, ensure_ascii=False, indent=2),
                    readOnly=True,
                    className="ontology-json shared-json",
                ),
                html.Button("保存为我的草稿", id="share-copy", n_clicks=0, className="button button-accent"),
                html.Div(id="share-status", className="status-message"),
            ]
        )
    return page_layout("share", "共享本体", children)


def register_share_callbacks(app):
    @app.callback(
        Output("share-status", "children"),
        Output("route-location", "pathname"),
        Input("share-copy", "n_clicks"),
        State("shared-ontology", "data"),
        prevent_initial_call=True,
    )
    def save_shared_document(_clicks, document):
        if not document:
            return "没有可保存的本体。", no_update
        conn = db.init_db()
        stored = ontology_documents.create_document(conn, document)
        conn.close()
        return "已复制到本地草稿。", f"/designer/{stored['id']}"