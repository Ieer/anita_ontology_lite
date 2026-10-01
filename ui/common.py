from dash import dcc, html


def page_header(active: str):
    links = [
        ("图谱", "/", "workbench"),
        ("目录", "/catalogue", "catalogue"),
        ("设计器", "/designer", "designer"),
        ("数据", "/data", "data"),
        ("学习", "/learn", "learn"),
    ]
    return html.Header(
        [
            html.Div(
                [
                    html.Span("U", className="brand-mark"),
                    html.Div([html.Strong("Utopia Lite"), html.Span("知识工程工作台")], className="brand-copy"),
                ],
                className="brand-lockup",
            ),
            html.Nav(
                [
                    dcc.Link(label, href=href, className=f"page-link{' active' if key == active or (active == 'share' and key == 'designer') else ''}")
                    for label, href, key in links
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
        className="topbar",
    )


def page_layout(active: str, title: str, children):
    page_kickers = {
        "catalogue": "LOCAL ONTOLOGY LIBRARY",
        "designer": "MODEL STUDIO",
        "data": "LOCAL DATA WORKSPACE",
        "learn": "FIELD GUIDE",
        "share": "READ-ONLY SNAPSHOT",
    }
    return html.Div(
        [
            page_header(active),
            html.Main(
                [
                    html.Div(
                        [html.Span(page_kickers.get(active, "UTOPIA WORKSPACE"), className="page-kicker"), html.H1(title, className="page-title")],
                        className="page-heading",
                    ),
                    *children,
                ],
                className="page-main",
            ),
        ],
        className=f"app-shell page-shell page-shell-{active}",
    )