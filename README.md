# Utopia Lite — 教学版知识底座 Demo

从 [Utopia](https://github.com/deeplethe/utopia) 提炼的**最小可运行骨架**，用轻量栈重构五个核心概念：

| Utopia 原版 | 本 Demo |
|---|---|
| Postgres + pgvector | SQLite + sqlite-vec |
| Tantivy 全文检索 | SQLite FTS5 |
| Rust (axum) 后端 | Python FastAPI |
| React + sigma.js 前端 | Python Dash + Cytoscape |
| utopia-reason 推理 | reason.py 前向链推导 |

保留五个可讲透的概念：**知识图谱**、**双时态（bitemporal）**、**混合检索 RRF**、**推理（传递+公理违反）**、**冲突处置**；另支持流式问答与文档摄入。

## 目录结构

```
utopia-lite-demo/
├── schema.sql        # 数据表设计（核心交付物，注释详尽）
├── app/
│   ├── db.py         # SQLite + sqlite-vec 初始化
│   ├── embeddings.py # 可插拔嵌入（hash/openai/sentence）
│   ├── search.py     # FTS5 + 向量 + RRF 融合
│   ├── graph.py      # 实体/事实 CRUD + 双时态 + 边属性
│   ├── reason.py     # 推理：传递推导 + 级联撤销 + 公理违反
│   ├── conflict.py   # 冲突检测与处置（close/keep/reject）
│   ├── ontology.py   # 本体：类型层级 + 类型推断
│   ├── resolution.py # 实体消解：三阶段去重 + 可撤销合并
│   ├── rbac.py       # 多用户 RBAC（owner/admin/editor/viewer）
│   ├── ontosql.py    # Ontology2SQL（挂载库 + 表映射 + NL→SQL 读写）
│   ├── llm.py        # 流式问答 + 行内引用
│   ├── ingest.py     # 文档摄入（txt/md/html/csv/json）
│   └── api.py        # FastAPI 路由
├── seed.py           # 演示数据
├── dash_app.py       # Dash 多页 POC：实例图工作台 + 本体目录/设计器/学习/分享
├── ui/               # Dash 页面与回调
├── mcp_server.py     # MCP 服务器（只读工具，供外部 Agent 接入）
├── run.sh            # 一键启动
└── requirements.txt
```

## 数据表设计（详见 schema.sql）

- **entities** —— 图节点，`type` 是轻量本体
- **facts** —— 图边，含**双时态** + 推导追踪：
  - `valid_from` / `valid_to`：真实世界时间线（事实在现实里何时成立）
  - `asserted_at`：系统认知时间线（系统何时记录）
  - `superseded_by` / `status`：纠错不覆盖，链接新旧版本
  - `derived` / `derived_from` / `retracted_because`：推导事实的来源与撤销追踪
- **conflicts** —— 冲突登记：断言事实矛盾的检测与处置审计（decision ledger）
- **axiom_violations** —— 公理违反登记（自环/反对称/传递环）的处置审计
- **fact_attributes** —— 边物化：边（事实）携带自身属性（如 since/confidence/role）
- **documents / chunks** —— RAG 的文档与分块
- **ontology_documents** —— 独立保存本体设计器 JSON 草稿与版本；不替代 `types/subclass_of` 实例类型推断
- **chunks_fts**（FTS5 虚拟表）—— 全文索引
- **chunk_vectors**（vec0 虚拟表）—— 向量索引

## 快速开始

```bash
cd utopia-lite-demo
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 方式一：一键（起 FastAPI + Dash 两个进程）
./run.sh

# 方式二：手动分两个终端
.venv/bin/python seed.py                      # 新库初始化；已有数据时安全跳过
.venv/bin/python -m uvicorn app.api:app --port 18000   # FastAPI
.venv/bin/python dash_app.py                  # Dash → http://127.0.0.1:18050

# 如需显式删除当前库并重建演示数据
.venv/bin/python seed.py --reset
```

打开 http://127.0.0.1:18050 看图谱；API 文档在 http://127.0.0.1:18000/docs。
工作台页面：`/` 图谱、`/catalogue` 本地目录、`/designer` 本体设计器、`/data` 本地数据工作台、`/learn` 学习路径；图谱页支持冲突 close/keep/reject 与公理 retract/accept/relax 审计处置。设计器支持结构化编辑、50 步撤销/重做、JSON 校验/保存、RDF/XML 导入导出及压缩 URL 分享；模式预览支持力导向/环形/层级布局、缩小、放大、适配画布和 PNG 导出。
图谱首页采用单屏工作台：左侧设置时间与类型过滤，中间始终保留图谱，右侧在详情、录入、处置、本体、映射、检索六个页签间切换。本体工具再分为“类型维护、层级关系、命名与校验”三个操作组。工作流侧栏可折叠以扩大图谱区域，折叠状态保存在当前浏览器会话；录入分为新增实体、新增事实和历史纠错，处置分为事实冲突和公理违反。收起工作流或切换页签会保留未提交输入；刷新或离开页面不会保留表单内容。
本体操作组将添加/删除类型、添加/删除继承关系及重命名/等价/环检测分别归类，切换分组不清空已填写内容；窄屏表单改为单列。
图谱工具栏用二维网格/三维立方体图标切换渲染模式，悬停可查看模式名称；图例可在画布右下角折叠/展开，不再占据左侧筛选区。图谱支持二维力导向、环形、层级，以及可旋转的三维力导向和三维球形布局；缩放、适配、PNG 导出和实体/关系检查随当前视图工作。三维球形是可旋转的球面排布，适合观察整体分布，但关系可能因前后遮挡而难以阅读，密集关系分析仍建议使用二维布局。选中实体后点击“填入主体”可直接打开事实录入并带入主体 ID。统计随当前可见图谱更新，冲突、公理和本体操作后自动刷新图谱。桌面和平板的长表单在作业面板内滚动；手机按过滤、图谱、作业面板纵向排列，不压缩成不可操作的单屏。
“本体”工作流中的类型层级图支持层级/力导向/环形布局、缩放、适配和放大预览，可查看类型与子类关系数量；展开预览后可在大画布中浏览，再还原继续编辑类型。

`/data` 本地数据工作台可从受限目录挂载 SQLite 或上传只读副本，浏览表结构、通过表格筛选或折叠 SQL 专家模式查询；查询只允许只读操作。字段映射支持显示名称、稳定源键、属性和关系。物化前先预览行数/异常，再用预览返回的 source/mapping 哈希确认；导入形成带来源批次的本地图谱快照，不做后台同步，重复确认同一快照幂等。
Markdown 本体说明存放在 `UTOPIA_DATA_ROOT/markdown/`，可关联本体 JSON 文档、编辑/导入并更新本地 FTS 检索索引。JSON 本体仍是结构化 schema 的唯一真源；Markdown 是说明文件源，FTS/chunks 是可重建索引。front matter 使用 JSON 语法（属于 YAML 兼容子集）；正文留在本地，不建立向量索引。索引删除与源文件删除是两个独立操作。

### AI 辅助本体初稿

设计器的“AI 初稿”可选择已挂载的 SQLite 数据源和已摄入文档（txt/md/html/csv/json），使用本地模型或管理员预置的外部 OpenAI 兼容聊天服务生成待校核 JSON。只读取表结构，不发送数据行；被选中的文档会发送完整正文。一次最多 5 个数据源、10 篇文档，总输入超过 60000 字符会拒绝生成，不会静默截断。生成请求最多返回 4096 个令牌；若输出不足以形成有效 JSON，本次生成失败，不会保存半成品。新增数据源或文档后刷新设计器以更新选项。

使用外部服务（如 DeepSeek）前，管理员需在服务器环境中配置 `UTOPIA_ONTOLOGY_REMOTE_BASE_URL`（服务的 HTTPS `/v1` 地址）、`UTOPIA_ONTOLOGY_REMOTE_MODEL`、`UTOPIA_ONTOLOGY_REMOTE_API_KEY` 和 `UTOPIA_LOCAL_ADMIN_TOKEN`。不要把密钥写入仓库或浏览器；部署脚本只打包运行文件白名单，不包含本地 `.env`、数据库或用户上传的原始文件。操作人每次在设计器选择外部服务与材料，输入管理员令牌并勾选外发确认；API 调用需通过 `x-utopia-local-token` 请求头提供相同令牌。局域网访问设计器的生成回调还需由可信代理提供该请求头，与本地数据操作的远程访问规则一致。未配置外部服务时仍可使用本地模型；模型不可用时继续手工建模。

模型建议只填入编辑器，不自动保存或发布。`sourceEvidence` 保存每项建议的来源定位；带来源的 Candidate 会在评估和接受前核对当前表列/文档原文，失效时不能接受。来源存在只表示可以核对，不证明业务语义正确；移除来源元数据会失去这层核验。专家应校核、修订并保存草稿，再创建、评估和接受 Candidate。设计器中的“导出所选已批准版”或 `/api/ontology-documents/{id}/versions/{version_id}/rdf` 导出批准快照；原“导出 RDF/XML”仍导出当前编辑草稿。生成的 JSON 保存模型、提示词版本与输入 SHA-256；远程请求审计另记服务类型、所选来源 ID、模型版本、输入哈希与发起时间，不记录密钥或外发正文。模型切换仅作用于本体初稿，既有问答与 NL→SQL 配置保持独立。现有用户创建接口尚无鉴权，不能将当前的人工点击或用户名当成可信专家身份审计；正式使用前须建立受保护的用户创建与审批权限。

图谱主页的独立视觉样式位于 `assets/studio.css`，不覆盖本体设计器等其他页面。三维图谱使用本地打包的 `3d-force-graph` 1.80.0（MIT，许可证位于 `assets/vendor/3d-force-graph-MIT.txt`），运行不依赖 CDN；三维视图需要浏览器支持 WebGL，不支持时会切回二维。Lucide 0.468.0 图标及许可证保存在 `assets/icons/`，无 CDN 运行依赖。
目录、设计器、学习和分享页统一使用 `assets/pages.css` 与品牌导航；设计器优先显示文档编辑和预览，结构化建模工具按需展开。各页面保留现有数据与操作流程。
页面右上角的太阳/月亮按钮可在浅色与深色主题间切换；选择保存在当前浏览器本地，并同步应用于图谱、目录、设计器、数据和学习页面。

> `seed.py --reset` 会删除 `UTOPIA_LITE_DB` 指向的 SQLite 数据库及 WAL 文件，并重建旁边的 `sample_hr.db`；不要对含用户数据的默认库执行，POC 验证建议使用临时数据库。

> RDF/XML 导入会保留原始文件和导入快照：本体未修改时导出原 XML；编辑后更新受支持的类/属性/关系三元组，同时保留未知注解、公理和匿名节点。编辑后的 XML 命名空间/格式可能重排；文件最大 2MB。原生本体 JSON 是设计器字段的完整保真格式。

> **本地与网络访问**：`run.sh` 和直接启动默认监听 `127.0.0.1`。需显式设置 `UTOPIA_HOST=0.0.0.0` 才能从局域网访问 `http://<本机IP>:18050` 和 API 文档 `http://<本机IP>:18000/docs`。此设置不会为所有既有 API 自动增加认证；本地数据挂载/Markdown 路由在非回环访问时还要求 `UTOPIA_LOCAL_ADMIN_TOKEN` 对应的 `x-utopia-local-token` 请求头。端口可用 `UTOPIA_API_PORT`（默认 18000）和 `UTOPIA_PORT`（默认 18050）覆盖。

本地数据目录设置：`UTOPIA_DATA_ROOT`（默认 `~/.local/share/utopia-lite`，源码目录外）、`UTOPIA_SQLITE_ROOT`（上传副本目录）、`UTOPIA_SQLITE_ALLOWED_ROOTS`（额外允许挂载目录，按系统路径分隔符分隔）、`UTOPIA_MARKDOWN_ROOT`（Markdown 源目录，默认 `$UTOPIA_DATA_ROOT/markdown`）、`UTOPIA_LOCAL_UPLOAD_MAX_BYTES`（默认 256 MiB）、`UTOPIA_LOCAL_QUERY_MAX_BYTES`（默认 5 MiB）、`UTOPIA_MATERIALIZE_MAX_ROWS`（默认 10000）。挂载路径必须在允许根目录内；挂载库写入和 NL 写入默认关闭，需显式设置 `UTOPIA_ENABLE_MOUNT_WRITES=1` 才启用。

## MCP 语义工具

外部 Agent 可通过 stdio 启动只读 MCP 服务：

```bash
cd utopia-lite
.venv/bin/python mcp_server.py
```

除文档检索、实体和图谱查询外，语义工具支持按需 grounding：

- `browse_semantics(query, kind, limit, task_id)`：搜索设计器术语/关系、运行时类型、表/列映射和运行时公理；`kind` 可选 `term`、`mapping`、`relation`、`constraint`、`all`，最多返回 6 项。可选 `task_id` 关联脱敏轨迹。
- `resolve_semantics(mentions, context, as_of, believed_at, task_id)`：解析最多 5 个提及；结果区分 `resolved`、`ambiguous` 和 `not_found`，同分候选不会被自动猜选。成功结果附有限的关联映射/关系/约束、Evidence 状态和图谱事实样例。可选 `task_id` 关联脱敏轨迹。

设计器 JSON、运行时类型表和 SQLite 字段映射是彼此独立的现有模型，响应会注明语义对象的 `representation` 和 `source`，不表示它们已自动同步。语义浏览仅读取 Utopia Lite 主库中的 schema/映射元数据，不打开挂载数据库文件；输入、扫描记录和返回条数均有上限。

Agent 轨迹默认不自动创建。通过 `POST /api/agent-tasks` 显式开始任务后，将返回的 `id` 作为 `task_id` 传给语义工具；只记录工具名、状态、语义对象 ID、Evidence ID 和结果数，不记录提问原文或数据行。可选配置 `UTOPIA_TRAJECTORY_HMAC_KEY` 为输入生成 HMAC 指纹；不配置时不存任何输入指纹。任务可完成或删除，删除时其事件一并删除。

## API 速览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/graph?as_of=2023-06-01` | 节点+边（可选双时态过滤） |
| GET | `/api/entities` | 实体列表 |
| POST | `/api/entities` | 新增实体 |
| POST | `/api/facts` | 新增事实 |
| POST | `/api/facts/{id}/correct` | **双时态纠错** |
| POST | `/api/reason/transitive` | **传递性推导**（返回新推导数） |
| POST | `/api/conflicts/detect` | **冲突检测**（返回矛盾事实） |
| POST | `/api/conflicts/resolve` | **冲突处置**（close/keep/reject） |
| GET | `/api/conflicts` | 冲突处置账本 |
| GET | `/api/search?q=...` | 全文+向量 RRF 融合检索 |
| POST | `/api/chat` | **流式问答**（SSE；无 LLM 降级检索） |
| POST | `/api/documents` | 摄入文本/markdown/html/csv/json |
| POST | `/api/documents/upload` | 上传文件摄入 |
| GET | `/api/axioms` | 本体公理注册表 |
| POST | `/api/axioms/violations` | **公理违反检测**（自环/反对称/传递环） |
| POST | `/api/axioms/violations/resolve` | 处置（retract/accept/relax） |
| POST | `/api/facts/{id}/attributes` | 设置边属性（reified edge） |
| GET | `/api/facts/{id}/attributes` | 查看边属性 |
| GET | `/api/ontology` | 本体：类型 + subClassOf 层级 |
| POST | `/api/ontology/types` | 注册类型 |
| POST | `/api/ontology/subclass` | 声明 subClassOf |
| GET | `/api/ontology-documents` | 本体设计器文档列表 |
| POST | `/api/ontology-documents` | 保存本体 JSON 草稿 |
| POST | `/api/ontology-documents/validate` | 校验本体结构与引用 |
| GET/PUT/DELETE | `/api/ontology-documents/{id}` | 读取、更新、删除本体文档 |
| POST | `/api/ontology-documents/import-rdf` | 导入 RDF/XML 子集并返回校验/警告 |
| GET | `/api/ontology-documents/{id}/rdf` | 导出 RDF/XML 子集 |
| GET | `/api/entities/{id}/types` | 实体类型 + 推断超类 |
| GET | `/api/resolution/duplicates` | 三阶段重复实体检测 |
| POST | `/api/entities/{id}/aliases` | 加别名 |
| POST | `/api/resolution/merge` | 合并实体（可撤销） |
| POST | `/api/resolution/undo/{merge_id}` | 撤销合并 |
| POST | `/api/users` | 创建用户（RBAC） |
| GET | `/api/users` | 用户列表 |
| POST | `/api/users/auth` | 登录鉴权 |
| GET | `/api/mounted` | 已挂载数据库 |
| POST | `/api/mounted` | 从本地受限目录挂载 SQLite |
| POST | `/api/mounted/upload` | 上传 SQLite 只读副本 |
| GET | `/api/mounted/{name}/schema` | 浏览表、列、类型和主键 |
| POST | `/api/mounted/query/table` | 参数化表格筛选查询 |
| POST | `/api/mounted/map` | 表→本体映射 |
| POST | `/api/mounted/map/column` | 列→属性/关系映射 |
| POST | `/api/mounted/query` | 只读查询挂载库 |
| GET | `/api/mounted/{name}/materialize-preview` | 预览快照行数、实体和关系 |
| POST | `/api/mounted/materialize` | 以预览哈希显式确认快照物化 |
| POST | `/api/mounted/write` | 显式开启时才允许写挂载库（默认 403） |
| POST | `/api/mounted/nl/query` | **NL→SQL 只读查询** |
| POST | `/api/mounted/nl/write` | 显式开启时才允许 NL 写入（默认 403） |
| GET/POST | `/api/local-markdown` | 列出/保存本地 Markdown 说明和索引 |
| POST | `/api/local-markdown/upload` | 导入 Markdown 源文件 |
| POST | `/api/local-markdown/{id}/sync` | 从源文件重建全文索引 |
| DELETE | `/api/local-markdown/{id}/index` | 仅删除索引，保留源文件 |
| DELETE | `/api/local-markdown/{id}` | 删除源文件及对应索引 |
| POST | `/api/evidence` | 为语义对象添加事实/文档/Markdown/物化批次引用（仅存引用和 hash） |
| GET | `/api/evidence/{semantic_object_id}` | 列出引用并检查 current/stale/missing 状态 |
| POST | `/api/ontology-documents/{id}/versions` | 创建不可变 Candidate 快照 |
| GET | `/api/ontology-documents/{id}/versions` | 查看候选/accepted/rejected 版本 |
| POST | `/api/ontology-versions/{id}/evaluate` | 结构校验和 Parent/Candidate 语义差异报告 |
| POST | `/api/ontology-versions/{id}/decision` | 人工接受或拒绝 Candidate |
| POST | `/api/ontology-documents/{id}/versions/{version_id}/activate` | 激活历史 accepted 版本 |
| POST | `/api/agent-tasks` | 显式开始一条 Agent 任务轨迹 |
| GET | `/api/agent-tasks` | 列出任务摘要 |
| GET | `/api/agent-tasks/{task_id}` | 查看任务与语义工具事件 |
| POST | `/api/agent-tasks/{task_id}/complete` | 停止记录该任务 |
| DELETE | `/api/agent-tasks/{task_id}` | 删除任务及其全部事件 |

## 双时态怎么演示

在 Dash 工作台左侧的「真实世界时间」输入不同日期，观察中间图谱中的边随 `valid_from/valid_to` 出现/消失。右侧提供混合检索与对象检查器；点击实体或关系可查看类型、属性和时间信息：

- 输入 `2019-06-01`：Bob 在 DeepLethe（他 2019 入职）
- 输入 `2023-06-01`：Bob 的边消失（他 2023-06-30 离职），Carol 出现
- 输入 `2024-01-01`：Utopia 的 status 边指向 `released`（已被纠错，不再是 beta）

纠错后旧事实进入 `superseded` 状态、`superseded_by` 指向新事实——这就是"系统曾经相信过 beta"这条认知历史的保留方式。

## 推理怎么演示（传递性推导）

种子数据里放了地理位置层级（`located_in` 是传递关系）：

- 上海 `located_in` 中国
- 中国 `located_in` 亚洲

点击 Dash 的「运行传递性推导」按钮（或 `POST /api/reason/transitive`），前向链推理会物化一条**虚线推导边**：

    上海 located_in 中国 ∧ 中国 located_in 亚洲  ⇒  上海 located_in 亚洲

推导事实写入 `facts` 表，`derived=1`、`derived_from` 记录两条前提事实的 id，
对应 Utopia「推导事实标记来源、推导默认关闭」的设计。前向链会迭代到不动点，多跳链也能推导；
自环与重复会被跳过。

**级联撤销**：若某条前提事实被纠错（`POST /api/facts/{id}/correct`），所有依赖它的
推导事实会被级联撤回（`status='retracted'`，`retracted_because` 记录原因），
对应 Utopia「断言事实优先，依赖它的推导随之撤回」。

## 冲突怎么演示（断言事实矛盾的三选一）

种子数据里 `中国 capital_of 北京`（`capital_of` 是函数型关系：一国只能有一个首都）。

尝试新增 `中国 capital_of 上海`，先检测：

    POST /api/conflicts/detect  → 返回矛盾的「中国 capital_of 北京」

再按三种处置（Utopia 原话：close the old / keep both / reject the new）：

- **close**：resolution=`close` → 北京被关闭（superseded），上海生效
- **keep**：resolution=`keep` → 北京、上海都保留，冲突登记在案
- **reject**：resolution=`reject` → 上海不写入，北京保持不变

每次处置都写入 `conflicts` 表（`GET /api/conflicts` 可查），形成可审计的「决策账本」。

## 公理违反与边物化（P2）

**公理违反检测**（对应 Utopia 冲突检测第二类「data breaks an axiom」）：

`located_in` / `part_of` / `reports_to` 注册了三条公理（`GET /api/axioms`）：传递、反对称、非自反。

    POST /api/axioms/violations          → 检测自环 / 反对称冲突 / 传递环
    POST /api/axioms/violations/resolve  → retract（撤回事实）/ accept（接受并存）/ relax（放宽公理）

**边物化（reified edges）**：边可携带自身属性，对应 Utopia「edges carry attributes of their own」。
`POST /api/facts/{id}/attributes` 给边加 `since` / `confidence` / `role` 等键值对。

## 嵌入后端与中文分词（P1）

**嵌入后端**（环境变量 `UTOPIA_EMBED_BACKEND`，默认 `hash`）：
- `hash`：零依赖字符三元组哈希，64 维（开箱即用）
- `openai`：任意 OpenAI 兼容 API，设 `EMBEDDING_BASE_URL` + `EMBEDDING_MODEL`（可选 `EMBEDDING_API_KEY`）
  - 例（Ollama）：`EMBEDDING_BASE_URL=http://localhost:11434/v1 EMBEDDING_MODEL=nomic-embed-text`
- `sentence`：本地 sentence-transformers（`pip install sentence-transformers`），默认 `all-MiniLM-L6-v2`（384 维）

维度由后端决定，`db.py` 自动按 `get_dim()` 建/重建 vec0 表；配置缺失或探测失败自动回退 hash。

**中文分词**：FTS5 用 `trigram`（字符三元组子串匹配，内置零依赖）；<3 字符查询由 `search.py` 自动退回 LIKE 子串扫描。

## 扩展点（教学留白）

1. **向量距离**：当前用 L2（向量已归一化，等价余弦）；sqlite-vec 也支持 cosine。
2. **NL→SQL 精度**：当前 NL→SQL 靠 LLM 生成 SQL（读+写已实现）；原版 Ontology2SQL 的 SOTA 精度（BIRD 基准）属研究级扩展。

## 环境注意

- sqlite-vec 是原生扩展，需 Python 能 `load_extension`。若报 `enable_load_extension` 相关错误，用 venv（python.org 构建）而非系统 Python；macOS 系统 Python 常见此坑。
- SQLite 需 3.41+ 才支持部分新特性，本 Demo 用到的是 3.40 兼容的写法。
