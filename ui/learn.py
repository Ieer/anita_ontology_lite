import json
from pathlib import Path

from dash import dcc, html
from dash.dependencies import Input, Output, State

from ui.common import page_layout


CONTENT_PATH = Path(__file__).resolve().parents[1] / "content" / "learn" / "learning-path.json"


def _articles():
    return json.loads(CONTENT_PATH.read_text(encoding="utf-8"))


def learn_layout(article_slug=None):
    articles = _articles()
    if article_slug is None:
        cards = [
            html.A(
                [
                    html.Div(f"第 {index:02d} 节", className="catalogue-kicker"),
                    html.H2(article["title"]),
                    html.P(article["summary"]),
                ],
                href=f"/learn/{article['slug']}",
                className="catalogue-item learn-item",
            )
            for index, article in enumerate(articles, start=1)
        ]
        return page_layout("learn", "学习路径", [html.Div(cards, className="catalogue-grid")])

    article = next((item for item in articles if item["slug"] == article_slug), None)
    if article is None:
        return page_layout("learn", "学习内容不存在", [dcc.Link("返回学习路径", href="/learn")])

    return page_layout(
        "learn",
        article["title"],
        [
            html.Div(
                [
                    dcc.Markdown(article["body"], className="learn-article"),
                    html.Div(className="sidebar-rule"),
                    html.Strong(article["question"], className="quiz-question"),
                    dcc.RadioItems(
                        id="learn-answer",
                        options=[{"label": choice, "value": choice} for choice in article["choices"]],
                        value=None,
                        className="quiz-options",
                    ),
                    html.Button("检查答案", id="learn-check", n_clicks=0, className="button button-accent"),
                    html.Div(id="learn-feedback", className="status-message"),
                    dcc.Store(id="learn-article-slug", data=article_slug),
                    dcc.Store(id="learn-progress", storage_type="local", data=[]),
                    html.Div(dcc.Link("回到学习路径", href="/learn", className="side-link"), className="learn-back"),
                ],
                className="learn-panel",
            )
        ],
    )


def register_learn_callbacks(app):
    @app.callback(
        Output("learn-feedback", "children"),
        Output("learn-progress", "data"),
        Input("learn-check", "n_clicks"),
        State("learn-answer", "value"),
        State("learn-article-slug", "data"),
        State("learn-progress", "data"),
        prevent_initial_call=True,
    )
    def check_answer(_clicks, answer, article_slug, progress):
        article = next((item for item in _articles() if item["slug"] == article_slug), None)
        if article is None:
            return "学习内容不存在。", progress or []
        if answer != article["answer"]:
            return "再想一想，然后重新选择。", progress or []
        completed = list(dict.fromkeys([*(progress or []), article_slug]))
        return "回答正确，进度已保存在本机浏览器。", completed