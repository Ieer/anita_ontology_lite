"""文档摄入：把不同格式解析成纯文本后，走统一的分块+索引管线。

对应 utopia-ingest / utopia-extract，但只保留零依赖的文本格式：
    txt / md / html / csv / json
PDF / DOCX / PPTX 等二进制格式需额外解析库（原版用 pdf-extract / calamine），
这里留作扩展点，见 README。

核心复用 search.index_document：分块 + FTS5 全文 + 向量，摄入与检索共用一条管线。
"""

from __future__ import annotations

import csv
import io
import json
import re

from . import search


def ingest_text(conn, title: str, content: str) -> int:
    return search.index_document(conn, title, content)


def ingest_markdown(conn, title: str, content: str) -> int:
    return search.index_document(conn, title, markdown_to_text(content))


def markdown_to_text(content: str) -> str:
    return _strip_markdown(content)


def ingest_html(conn, title: str, content: str) -> int:
    text = re.sub(r"<[^>]+>", " ", content)
    text = re.sub(r"\s+", " ", text)
    return search.index_document(conn, title, text)


def ingest_csv(conn, title: str, content: str) -> int:
    rows = list(csv.DictReader(io.StringIO(content)))
    lines = [", ".join(f"{k}={v}" for k, v in row.items()) for row in rows]
    return search.index_document(conn, title, "\n".join(lines))


def ingest_json(conn, title: str, content: str) -> int:
    data = json.loads(content)
    return search.index_document(conn, title, "\n".join(_flatten(data)))


def ingest_file(conn, filename: str, content_bytes: bytes) -> int:
    """按扩展名分派。content_bytes 为原始字节。"""
    text = content_bytes.decode("utf-8", errors="replace")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
    if ext in ("txt", ""):
        return ingest_text(conn, filename, text)
    if ext == "md":
        return ingest_markdown(conn, filename, text)
    if ext == "html":
        return ingest_html(conn, filename, text)
    if ext == "csv":
        return ingest_csv(conn, filename, text)
    if ext == "json":
        return ingest_json(conn, filename, text)
    raise ValueError(f"暂不支持的格式：.{ext}（当前支持 txt/md/html/csv/json）")


def _strip_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)   # 代码块
    text = re.sub(r"`[^`]*`", " ", text)                  # 行内代码
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)    # 图片
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text) # 链接保留文字
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)   # 标题
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M) # 列表
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)        # 加粗
    text = re.sub(r"\*([^*]+)\*", r"\1", text)            # 斜体
    return text


def _flatten(obj, prefix: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(_flatten(v, f"{prefix}{k}." if prefix else f"{k}."))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_flatten(v, f"{prefix}{i}."))
    else:
        out.append(f"{prefix.rstrip('.')}={obj}")
    return out
