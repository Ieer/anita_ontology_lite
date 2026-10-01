-- =============================================================
-- Utopia Lite — 数据表设计（教学版）
-- 对应 Utopia 的三个核心概念，映射到 6 张表 + 2 张虚拟表。
-- =============================================================

-- -------------------------------------------------------------
-- 概念一：知识图谱（实体 + 事实）
--   entities  = 图节点
--   facts     = 图边，且自带「双时态」
-- -------------------------------------------------------------

-- 实体（节点）。type 是轻量版「本体」：只用一个字符串给节点分类，
-- 原版 Utopia 这里是完整 ontology（schema.org / PROV-O / FOAF 等 pack）。
CREATE TABLE IF NOT EXISTS entities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'Thing',   -- 例如 Person / Company / Product
    merged_into INTEGER REFERENCES entities(id), -- 实体消解：被合并到哪个实体（NULL=正常）
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 事实（边），核心是「双时态」两个时间维度：
--   valid_from / valid_to   —— 真实世界时间线：这件事在现实里何时成立
--   asserted_at             —— 系统认知时间线：系统何时「相信/记录」了它
-- 纠错不覆盖：status='superseded' 关闭旧版本，superseded_by 链到新版本，
--   保留「系统曾经是怎么认为的」完整历史（Utopia 的 bitemporal 精髓）。
CREATE TABLE IF NOT EXISTS facts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id    INTEGER NOT NULL REFERENCES entities(id),
    predicate     TEXT    NOT NULL,             -- 关系名，如 works_at / founded
    object_id     INTEGER NOT NULL REFERENCES entities(id),
    valid_from    TEXT    NOT NULL,             -- ISO 日期，如 '2020-01-01'
    valid_to      TEXT,                         -- NULL = 至今（开放区间）
    asserted_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    superseded_by INTEGER REFERENCES facts(id), -- 被哪个新事实取代
    source        TEXT,                         -- 事实来源（溯源）
    derived       INTEGER NOT NULL DEFAULT 0,   -- 0=断言 1=推导（推理层写入）
    derived_from  TEXT,                         -- 推导前提事实 id，如 "5,7"
    retracted_because TEXT,                     -- 推导事实被撤销的原因（前提被纠错）
    status        TEXT    NOT NULL DEFAULT 'active'  -- active | superseded | retracted
);

-- -------------------------------------------------------------
-- 概念三：检索（文档 → 分块 → 全文 FTS + 向量 vec）
-- -------------------------------------------------------------

-- 源文档
CREATE TABLE IF NOT EXISTS documents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ontology_suggestion_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    source_ids_json TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    input_sha256 TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 分块（RAG 的检索单元）
CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    seq         INTEGER NOT NULL,               -- 在原文档中的顺序
    content     TEXT    NOT NULL
);

-- 全文检索：FTS5 虚拟表（外部内容表，不重复存储，直接索引 chunks.content）
-- 中文分词：trigram（字符三元组子串匹配，内置、零依赖；<3 字符查询由 search.py 退回 LIKE）。
--   原版 Utopia 用 tantivy-jieba；这里用内置 trigram 开箱即用，jieba 留作扩展点。
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content,
    content='chunks',
    content_rowid='id',
    tokenize='trigram'
);

-- 向量检索：sqlite-vec 的 vec0 虚拟表，由 db.py 按 embeddings.get_dim() 动态创建
--   （维度随嵌入后端变化：hash=64，openai/sentence 取决于模型）。rowid 复用 chunk id，
--   方便 join 回 chunks。见 app/db.py 的 _ensure_vectors()。

-- FTS5 外部内容表需要触发器来同步增删改
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
END;

-- 常用索引
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject_id);
CREATE INDEX IF NOT EXISTS idx_facts_object  ON facts(object_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc    ON chunks(document_id);

-- -------------------------------------------------------------
-- 冲突登记：断言事实矛盾的检测与处置审计（对应 Utopia 的 decision ledger）
-- resolution：close（关旧）/ keep（两者保留）/ reject（拒新）
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conflicts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id  INTEGER NOT NULL REFERENCES entities(id),
    predicate   TEXT    NOT NULL,
    fact_old    INTEGER NOT NULL REFERENCES facts(id),  -- 已有的冲突事实
    fact_new    INTEGER REFERENCES facts(id),           -- 新事实（reject 时为 NULL）
    resolution  TEXT    NOT NULL,                        -- close | keep | reject
    note        TEXT,
    resolved_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- 公理违反登记：数据违反公理（自环/反对称/传递环）的检测与处置审计
CREATE TABLE IF NOT EXISTS axiom_violations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id     INTEGER REFERENCES facts(id),
    resolution  TEXT NOT NULL,   -- retract | accept | relax
    note        TEXT,
    detected_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 边物化（reified edges）：边（事实）可携带自身属性，如 since/confidence/role。
-- 对应 Utopia「Edges are reified, so an edge carries attributes of its own」。
CREATE TABLE IF NOT EXISTS fact_attributes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id INTEGER NOT NULL REFERENCES facts(id),
    key     TEXT    NOT NULL,
    value   TEXT    NOT NULL,
    UNIQUE(fact_id, key)
);
CREATE INDEX IF NOT EXISTS idx_fact_attr ON fact_attributes(fact_id);

-- 本体：类型层级（subClassOf），支持传递闭包类型推断
CREATE TABLE IF NOT EXISTS types (
    name TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS subclass_of (
    child  TEXT NOT NULL,
    parent TEXT NOT NULL,
    PRIMARY KEY (child, parent)
);
-- 类型等价（同义）：声明两个类型是同一个概念，如 Person ≡ Human。
--   等价是自反/对称/传递的，由 ontology.equivalent_types 求等价闭包。
--   存储时按字典序规范化，避免 (A,B) 与 (B,A) 重复。
CREATE TABLE IF NOT EXISTS type_equivalences (
    type_a TEXT NOT NULL,
    type_b TEXT NOT NULL,
    PRIMARY KEY (type_a, type_b)
);

-- 本体设计器文档：独立于实例图的 types/subclass_of 类型推断表。
CREATE TABLE IF NOT EXISTS ontology_documents (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    document_json TEXT NOT NULL CHECK (json_valid(document_json)),
    rdf_source_xml TEXT,
    rdf_source_document_json TEXT CHECK (rdf_source_document_json IS NULL OR json_valid(rdf_source_document_json)),
    version       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Immutable ontology snapshots and an explicit active-version pointer.
CREATE TABLE IF NOT EXISTS ontology_versions (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES ontology_documents(id) ON DELETE CASCADE,
    parent_version_id TEXT REFERENCES ontology_versions(id),
    draft_version INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'accepted', 'rejected')),
    evaluation_json TEXT NOT NULL DEFAULT '{}',
    decision_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    evaluated_at TEXT,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ontology_versions_document
    ON ontology_versions(document_id, created_at);
CREATE TABLE IF NOT EXISTS ontology_active_versions (
    document_id TEXT PRIMARY KEY REFERENCES ontology_documents(id) ON DELETE CASCADE,
    version_id TEXT NOT NULL UNIQUE REFERENCES ontology_versions(id) ON DELETE CASCADE,
    published_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TRIGGER IF NOT EXISTS ontology_versions_snapshot_immutable
BEFORE UPDATE OF document_id, parent_version_id, draft_version, snapshot_json ON ontology_versions
BEGIN
    SELECT RAISE(ABORT, 'ontology version snapshots are immutable');
END;

-- 实体消解：别名（stage 1）与合并记录（支持撤销）
CREATE TABLE IF NOT EXISTS aliases (
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    alias     TEXT    NOT NULL,
    UNIQUE(entity_id, alias)
);
CREATE TABLE IF NOT EXISTS entity_merges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kept_id     INTEGER NOT NULL,
    merged_id   INTEGER NOT NULL,
    method      TEXT    NOT NULL,
    moved_facts TEXT    NOT NULL,   -- JSON：被移动的事实 [{fact_id, role}]
    merged_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- 多用户 RBAC：应用层用户与角色（owner/admin/editor/viewer）
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'viewer',  -- owner | admin | editor | viewer
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Ontology2SQL：挂载外部数据库 + 表→本体映射
CREATE TABLE IF NOT EXISTS mounted_dbs (
    name TEXT PRIMARY KEY,
    path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mounted_source_metadata (
    mount_name TEXT PRIMARY KEY REFERENCES mounted_dbs(name) ON DELETE CASCADE,
    source_kind TEXT NOT NULL DEFAULT 'path',
    source_filename TEXT NOT NULL DEFAULT '',
    source_sha256 TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS table_mappings (
    mount_name  TEXT NOT NULL,
    table_name  TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    name_col    TEXT NOT NULL,
    key_col     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (mount_name, table_name)
);
-- 字段级映射：把表里的某个列映射为本体「关系」或「属性」。
--   关系模式：target_type + predicate —— 列值变成 target_type 实体，主实体通过 predicate 连到它。
--     例 employees.dept → (Department, works_in)：每个 dept 值变成 Department 实体，建立「员工 works_in 部门」。
--   属性模式：attr_name —— 列值直接作为主实体的属性值（如 salary 列 → 属性 salary）。
--   物化由 ontosql.materialize_mappings 执行。
CREATE TABLE IF NOT EXISTS column_mappings (
    mount_name  TEXT NOT NULL,
    table_name  TEXT NOT NULL,
    column_name TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',  -- 关系模式：该列值作为实体的类型，如 Department
    predicate   TEXT NOT NULL DEFAULT '',  -- 关系模式：与主实体的关系名，如 works_in
    attr_name   TEXT NOT NULL DEFAULT '',  -- 属性模式：属性键名，如 salary（非空即属性模式）
    PRIMARY KEY (mount_name, table_name, column_name)
);
CREATE TABLE IF NOT EXISTS external_entity_keys (
    mount_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    mapping_sha256 TEXT NOT NULL,
    source_key TEXT NOT NULL,
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    PRIMARY KEY (mount_name, table_name, source_sha256, mapping_sha256, source_key)
);
CREATE TABLE IF NOT EXISTS materialization_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mount_name TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    mapping_sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'complete',
    stats_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (mount_name, source_sha256, mapping_sha256)
);
CREATE TABLE IF NOT EXISTS local_markdown_files (
    file_id TEXT PRIMARY KEY,
    relative_path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    ontology_document_id TEXT,
    document_id INTEGER REFERENCES documents(id),
    sha256 TEXT NOT NULL,
    indexed_sha256 TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Evidence references are append-only pointers; source content is never copied here.
CREATE TABLE IF NOT EXISTS semantic_evidence_refs (
    id TEXT PRIMARY KEY,
    semantic_object_id TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('fact', 'document', 'markdown', 'materialization')),
    source_id TEXT NOT NULL,
    locator TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    captured_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_semantic_evidence_object
    ON semantic_evidence_refs(semantic_object_id, created_at);

-- Task-level semantic tool history is recorded only when a caller supplies a task ID.
CREATE TABLE IF NOT EXISTS agent_tasks (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed')),
    ontology_version_id TEXT REFERENCES ontology_versions(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_task_events (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL DEFAULT 0,
    tool_name TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'not_found', 'ambiguous', 'error')),
    query_fingerprint TEXT NOT NULL DEFAULT '',
    result_count INTEGER NOT NULL DEFAULT 0,
    semantic_object_ids_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(semantic_object_ids_json)),
    evidence_ref_ids_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(evidence_ref_ids_json)),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_agent_task_events_task
    ON agent_task_events(task_id, created_at, id);

-- 实体属性（字段级映射的属性模式落点；对应边属性 fact_attributes）
CREATE TABLE IF NOT EXISTS entity_attributes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id  INTEGER NOT NULL REFERENCES entities(id),
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    UNIQUE(entity_id, key)
);
CREATE INDEX IF NOT EXISTS idx_entity_attr ON entity_attributes(entity_id);
