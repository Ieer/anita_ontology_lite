# Utopia 原版 vs utopia-lite-demo · 对比复盘 & 迭代路线图

> 更新日期：2026-09-13（最终态 + 实测大小）
> 原版：https://github.com/deeplethe/utopia
> 教学版：`staging/utopia-lite-demo/`
> 关联文档：`docs/utopia-lite-demo-usage.md`（使用）、`docs/utopia-lite-demo-ontology-guide.md`（本体建立方法论）

## 一句话复盘

用 **1/50 的代码量**，复刻了原版约 **80% 的核心概念**；砍掉的不是「知识思想」，而是「企业工程」。大小差距的根源：原版把大量代码花在**文档摄入解析、多用户权限、问数引擎精度**这些工程护城河上，而 demo 只保留「双时态 + 推理 + 冲突 + 本体」这些可教学的思想骨架。并在树莓派（Pi 5，4 核/16GB/纯 CPU）上用本地 Ollama 小模型验证了「离线知识底座」的可行性。

---

## 一、规模对比（实测）

| 维度 | 原版 Utopia | utopia-lite-demo | 比例 |
|---|---|---|---|
| **代码行数** | ~12.3 万行（87,424 Rust + 35,518 TS） | ~2,490 行（py + sql + sh） | **1/50** |
| **源码体积** | 38 MB（不含 .git/target） | 3.4 MB（不含 .venv） | **1/11** |
| **源文件数** | ~300+（209 .rs + 94 .ts + 52 迁移 + web 配置） | 23 个 | **1/15** |
| **迁移文件** | 52 个 SQL | 1 个 schema.sql | 1/52 |
| **组织单元** | 8 个 Rust crate | 14 个 Python 模块 | — |
| **运行时依赖** | Docker + Rust 1.85 + Node 20 + Postgres | Python + ~10 个 pip 包（+ 可选本地 Ollama ~1.3GB） | — |
| **本地模型** | 任意 OpenAI 兼容端点（可 air-gapped） | 本地 Ollama：`qwen2.5:1.5b` + `nomic-embed-text` | 同路线 |

## 二、技术栈总览

| 维度 | 原版 Utopia | utopia-lite-demo |
|---|---|---|
| 定位 | 企业世界模型 v0.1 | 教学骨架 |
| 语言 | Rust（8 crate workspace） | Python |
| 存储 | Postgres + pgvector | SQLite + sqlite-vec |
| 全文检索 | Tantivy + tantivy-jieba | SQLite FTS5 (trigram) |
| 后端框架 | axum + tower-http | FastAPI |
| 前端框架 | React 19 + sigma.js + TanStack | Dash + Cytoscape |
| 嵌入模型 | 任意 OpenAI 兼容端点 | hash / openai / sentence（本地 nomic-embed-text） |
| LLM | OpenAI 兼容端点 | 本地 Ollama（qwen2.5:1.5b） |
| 推理引擎 | 规则编译 + 前向链 | 手写公理 + 前向链 |

## 三、模块映射（最终态）

| 原版 crate | 职责 | demo 对应 | 覆盖度 |
|---|---|---|---|
| utopia-core | 领域模型/本体/加密/凭据 | graph.py + ontology.py + rbac.py | ⚠️ 简化（无密码学） |
| utopia-store | 持久化层 | db.py + schema.sql | ✅ |
| utopia-server | HTTP + MCP | api.py + mcp_server.py | ✅ |
| utopia-ingest | 文档摄入 | ingest.py（txt/md/html/csv/json） | ⚠️ 无二进制格式 |
| utopia-extract | PDF/DOCX 解析 | —（二进制格式未做） | ❌ 砍掉 |
| utopia-reason | 规则编译 + 前向链 | reason.py（传递+反对称+非自反+撤销+违反检测） | ✅ |
| utopia-search | Tantivy+pgvector+RRF | search.py（FTS5 + sqlite-vec + RRF） | ✅ |
| utopia-llm | 模型接入 | embeddings.py + llm.py | ✅ |

## 四、功能覆盖（最终态复盘）

| 概念 | demo 现状 | 与原版差距 |
|---|---|---|
| 知识图谱 CRUD | ✅ | 无 |
| 双时态图谱 | ✅ | 无（完整） |
| 混合检索 RRF | ✅ | 无（完整） |
| 流式问答 + 行内引用 | ✅ | 无 |
| 推理：前向链 + 级联撤销 | ✅ | 无（完整） |
| 公理违反检测（自环/反对称/传递环） | ✅ | 无 |
| 冲突处置 close/keep/reject + 决策账本 | ✅ | 无 |
| 本体 + 类型推断（subClassOf） | ✅ | 原版有 5 个 pack，demo 手写层级；已加**可视化编辑 + 环检测 + 重命名 + 类型等价（含层级约束共享 + 一致性校验）** |
| 实体消解三阶段（同名/别名/嵌入） | ✅ | demo 缺「模型裁决」深度 |
| 边物化（reified edges） | ✅ | 无 |
| MCP 服务（5 只读工具） | ✅ | 无 |
| NL→SQL（读写执行） | ✅ 教学版 | **精度差距**（原版 BIRD SOTA） |
| 多用户 RBAC（owner/admin/editor/viewer） | ✅ | demo 应用层简化 |
| Ontology2SQL 问数引擎 | ⚠️ 挂载+映射+读写 | 缺 NL→SQL SOTA 精度；字段级映射（列→关系 + 列→属性）✅ 已做 |
| 文档摄入（10+ 格式） | ⚠️ 仅文本 | **二进制格式砍掉** |

## 五、迭代路线图（已全部落地）

| 阶段 | 步骤 | 对应原版模块 | 状态 |
|---|---|---|---|
| **P1 真实性** | 中文分词（trigram + LIKE 回退） | utopia-search | ✅ |
| | 可插拔 embedding（hash/openai/sentence） | utopia-llm | ✅ |
| | 流式问答 + 行内引用 | utopia-llm | ✅ |
| | 文档摄入（文本格式） | utopia-ingest | ✅ |
| **P2 本体推理** | 本体表 + 类型层级 | utopia-core | ✅ |
| | 更多公理（传递/反对称/非自反） | utopia-reason | ✅ |
| | 公理违反检测 | utopia-reason | ✅ |
| | 边物化 | utopia-core | ✅ |
| **P3 企业能力** | 多用户 RBAC | utopia-core | ✅ |
| | 实体消解三阶段 | utopia-core | ✅ |
| | MCP server | utopia-server | ✅ |
| | Ontology2SQL（含 NL→SQL 读写） | utopia-search | ✅ |

## 六、进度记录

- [x] P1 全部（分词 / 嵌入 / 问答 / 摄入）
- [x] P2 全部（本体 / 公理 / 违反检测 / 边物化）
- [x] P3 全部（MCP / 实体消解 / RBAC / Ontology2SQL）
- [x] 树莓派本地模型（Ollama：qwen2.5:1.5b + nomic-embed-text）
- [ ] 研究级扩展：NL→SQL SOTA 精度（原版 BIRD 基准）
- [ ] 研究级扩展：二进制文档摄入（PDF/DOCX/PPTX）

## 七、复盘经验（4 条结论）

1. **核心是「思想」不是「栈」**——双时态、前向链推理、冲突处置、本体这些概念与 Postgres/Rust 完全解耦，换 SQLite/Python 照样讲透，代码量骤降 50 倍。

2. **大小差距 = 工程护城河**——原版 12 万行里，真正「知识思想」的部分占比不大，大量代码在文档解析（PDF/DOCX 编码检测）、多用户权限、问数引擎 SOTA 精度上。这些是「能不能卖」的工程，不是「能不能懂」的教学。

3. **轻量栈的取舍可逆**——SQLite→Postgres、FTS5→Tantivy、hash→真实嵌入，demo 每个模块都留了明确替换点（`embeddings.py` 一个函数、`db.py` 动态建表），升级生产不用推倒重来。

4. **离线验证成立**——在 Pi 5（4 核/16GB/纯 CPU）用 `qwen2.5:1.5b` + `nomic-embed-text` 跑通 NL→SQL 和检索，证明原版「air-gapped 离线知识底座」路线在小硬件上也走得通。
