"""检索层：全文（FTS5）+ 向量（sqlite-vec）+ RRF 融合。"""

from __future__ import annotations

import sqlite3

import sqlite_vec

from .embeddings import embed

RRF_K = 60  # Reciprocal Rank Fusion 的常数


def _serialize(vec: list[float]) -> bytes:
    return sqlite_vec.serialize_float32(vec)


def index_document(
    conn: sqlite3.Connection,
    title: str,
    content: str,
    chunk_size: int = 200,
    commit: bool = True,
    vector_index: bool = True,
) -> int:
    """写入文档并分块、建全文索引 + 向量索引。返回 document id。"""
    cur = conn.execute("INSERT INTO documents(title, content) VALUES (?, ?)", (title, content))
    doc_id = cur.lastrowid
    _insert_chunks(conn, doc_id, content, chunk_size, vector_index)
    if commit:
        conn.commit()
    return doc_id


def replace_indexed_document(
    conn: sqlite3.Connection,
    document_id: int,
    title: str,
    content: str,
    chunk_size: int = 200,
    commit: bool = True,
    vector_index: bool = True,
) -> None:
    """Replace a document and rebuild its FTS/vector chunks atomically."""
    if conn.execute("SELECT 1 FROM documents WHERE id=?", (document_id,)).fetchone() is None:
        raise ValueError(f"文档 #{document_id} 不存在。")
    conn.execute("SAVEPOINT replace_indexed_document")
    try:
        chunk_ids = [row[0] for row in conn.execute("SELECT id FROM chunks WHERE document_id=?", (document_id,))]
        for chunk_id in chunk_ids:
            conn.execute("DELETE FROM chunk_vectors WHERE rowid=?", (chunk_id,))
        conn.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
        conn.execute("UPDATE documents SET title=?, content=? WHERE id=?", (title, content, document_id))
        _insert_chunks(conn, document_id, content, chunk_size, vector_index)
        conn.execute("RELEASE SAVEPOINT replace_indexed_document")
        if commit:
            conn.commit()
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT replace_indexed_document")
        conn.execute("RELEASE SAVEPOINT replace_indexed_document")
        raise


def delete_indexed_document(conn: sqlite3.Connection, document_id: int, commit: bool = True) -> bool:
    """Delete a local search document and all FTS/vector chunks."""
    chunk_ids = [row[0] for row in conn.execute("SELECT id FROM chunks WHERE document_id=?", (document_id,))]
    if conn.execute("SELECT 1 FROM documents WHERE id=?", (document_id,)).fetchone() is None:
        return False
    conn.execute("SAVEPOINT delete_indexed_document")
    try:
        for chunk_id in chunk_ids:
            conn.execute("DELETE FROM chunk_vectors WHERE rowid=?", (chunk_id,))
        conn.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
        conn.execute("DELETE FROM documents WHERE id=?", (document_id,))
        conn.execute("RELEASE SAVEPOINT delete_indexed_document")
        if commit:
            conn.commit()
        return True
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT delete_indexed_document")
        conn.execute("RELEASE SAVEPOINT delete_indexed_document")
        raise


def _insert_chunks(conn: sqlite3.Connection, document_id: int, content: str, chunk_size: int, vector_index: bool = True) -> None:
    for sequence, chunk in enumerate(_split_chunks(content, chunk_size)):
        cursor = conn.execute(
            "INSERT INTO chunks(document_id, seq, content) VALUES (?, ?, ?)",
            (document_id, sequence, chunk),
        )
        if vector_index:
            conn.execute(
                "INSERT INTO chunk_vectors(rowid, embedding) VALUES (?, ?)",
                (cursor.lastrowid, _serialize(embed(chunk))),
            )


def hybrid_search(conn: sqlite3.Connection, query: str, k: int = 5) -> list[dict]:
    """全文 + 向量两路召回，RRF 融合后返回 top-k 分块（带来源文档）。"""
    if not query.strip():
        return []

    # 1) 全文召回（trigram：中文字符三元组子串匹配；<3 字符退回 LIKE）
    fts_rows = _fts_search(conn, query, limit=20)
    fts_rank = [(r["rowid"], r["score"]) for r in fts_rows]

    # 2) 向量召回（L2 距离越小越近；向量已归一化，L2 等价余弦）
    vec_rows = conn.execute(
        "SELECT rowid, distance "
        "FROM chunk_vectors WHERE embedding MATCH ? AND k = ?",
        (_serialize(embed(query)), 20),
    ).fetchall()
    vec_rank = [(r["rowid"], r["distance"]) for r in vec_rows]

    # 3) RRF 融合：两路排名按 1/(k+rank) 加权合并
    fused = _rrf_fuse([fts_rank, vec_rank])
    if not fused:
        return []

    # 4) 回填分块正文 + 来源文档标题
    results = []
    for chunk_id, score in fused[:k]:
        row = conn.execute(
            "SELECT c.id, c.content, d.title, d.id AS doc_id "
            "FROM chunks c JOIN documents d ON c.document_id = d.id "
            "WHERE c.id = ?",
            (chunk_id,),
        ).fetchone()
        if row:
            results.append(
                {
                    "chunk_id": row["id"],
                    "document_id": row["doc_id"],
                    "title": row["title"],
                    "content": row["content"],
                    "rrf_score": round(score, 6),
                }
            )
    return results


def _fts_search(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[sqlite3.Row]:
    """全文召回：trigram MATCH；空结果或异常时退回 LIKE 子串扫描（覆盖 <3 字符查询）。"""
    try:
        rows = conn.execute(
            "SELECT rowid, bm25(chunks_fts) AS score "
            "FROM chunks_fts WHERE chunks_fts MATCH ? "
            "ORDER BY score LIMIT ?",
            (query, limit),
        ).fetchall()
        if rows:
            return rows
    except sqlite3.OperationalError:
        pass
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return conn.execute(
        "SELECT id AS rowid, -1.0 AS score FROM chunks WHERE content LIKE ? ESCAPE '\\' LIMIT ?",
        (f"%{escaped}%", limit),
    ).fetchall()


def _rrf_fuse(rankings: list[list[tuple[int, float]]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion：合并多路排序结果。

    score(id) = Σ 1 / (k + rank)，rank 从 1 开始（最佳为 1）。
    优点：不关心各路分值的量纲（bm25 是负的、L2 是正的都能融合）。
    """
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, (item_id, _score) in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: -x[1])


def _split_chunks(content: str, size: int) -> list[str]:
    """按字符长度切分，尽量在句末/换行处断。"""
    content = content.strip()
    if len(content) <= size:
        return [content]
    out, start = [], 0
    while start < len(content):
        end = min(start + size, len(content))
        # 在窗口内找最后一个合适断点
        for sep in ("\n", "。", ".", "；", ";", "，"):
            pos = content.rfind(sep, start, end)
            if pos > start + size // 2:
                end = pos + 1
                break
        out.append(content[start:end])
        start = end
    return out
