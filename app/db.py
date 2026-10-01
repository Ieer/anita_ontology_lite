"""SQLite + sqlite-vec 的连接与初始化。"""

from __future__ import annotations

import os
import sqlite3

import sqlite_vec

from . import embeddings

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "utopia-lite.db")
DB_PATH = os.environ.get("UTOPIA_LITE_DB", DEFAULT_DB)

_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "schema.sql")


def init_db(path: str | None = None) -> sqlite3.Connection:
    """打开连接，加载 sqlite-vec 扩展并建表（幂等）。"""
    target = path or DB_PATH
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)

    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    _migrate(conn)
    _ensure_vectors(conn, embeddings.get_dim())
    conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """轻量迁移：为老库补齐新增列（幂等）。"""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(facts)")}
    if "derived" not in cols:
        conn.execute("ALTER TABLE facts ADD COLUMN derived INTEGER NOT NULL DEFAULT 0")
    if "derived_from" not in cols:
        conn.execute("ALTER TABLE facts ADD COLUMN derived_from TEXT")
    if "retracted_because" not in cols:
        conn.execute("ALTER TABLE facts ADD COLUMN retracted_because TEXT")
    ecols = {row[1] for row in conn.execute("PRAGMA table_info(entities)")}
    if "merged_into" not in ecols:
        conn.execute("ALTER TABLE entities ADD COLUMN merged_into INTEGER REFERENCES entities(id)")
    ccols = {row[1] for row in conn.execute("PRAGMA table_info(column_mappings)")}
    if "attr_name" not in ccols:
        conn.execute("ALTER TABLE column_mappings ADD COLUMN attr_name TEXT NOT NULL DEFAULT ''")
    mapping_cols = {row[1] for row in conn.execute("PRAGMA table_info(table_mappings)")}
    if "key_col" not in mapping_cols:
        conn.execute("ALTER TABLE table_mappings ADD COLUMN key_col TEXT NOT NULL DEFAULT ''")
    conn.execute("UPDATE table_mappings SET key_col=name_col WHERE key_col='' ")
    doc_cols = {row[1] for row in conn.execute("PRAGMA table_info(ontology_documents)")}
    if doc_cols and "rdf_source_xml" not in doc_cols:
        conn.execute("ALTER TABLE ontology_documents ADD COLUMN rdf_source_xml TEXT")
    if doc_cols and "rdf_source_document_json" not in doc_cols:
        conn.execute("ALTER TABLE ontology_documents ADD COLUMN rdf_source_document_json TEXT")
    trajectory_cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_task_events)")}
    if trajectory_cols and "sequence" not in trajectory_cols:
        conn.execute("ALTER TABLE agent_task_events ADD COLUMN sequence INTEGER NOT NULL DEFAULT 0")
    suggestion_cols = {row[1] for row in conn.execute("PRAGMA table_info(ontology_suggestion_requests)")}
    for column in ("model", "prompt_version", "input_sha256"):
        if column not in suggestion_cols:
            conn.execute(f"ALTER TABLE ontology_suggestion_requests ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")


def _ensure_vectors(conn: sqlite3.Connection, dim: int) -> None:
    """按当前嵌入维度创建/重建 vec0 表；维度变化时自动重建（幂等）。"""
    conn.execute("CREATE TABLE IF NOT EXISTS _meta(key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM _meta WHERE key='embed_dim'").fetchone()
    if row is not None and int(row["value"]) != dim:
        conn.execute("DROP TABLE IF EXISTS chunk_vectors")
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vectors USING vec0(embedding float[{dim}])"
    )
    conn.execute("INSERT OR REPLACE INTO _meta(key, value) VALUES ('embed_dim', ?)", (str(dim),))
