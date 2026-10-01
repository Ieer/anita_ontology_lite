"""Utopia Lite — 教学版知识底座骨架。

从 Utopia（deeplethe/utopia）提炼的最小可运行 Demo：
- 存储：SQLite + sqlite-vec（向量）+ FTS5（全文）
- 后端：FastAPI
- 前端：Dash + Cytoscape（知识图谱浏览）

保留的核心概念：
1. 知识图谱 CRUD（实体 / 事实 / 关系）
2. 双时态（bitemporal）：真实时间线 + 系统认知时间线
3. 检索：全文 + 向量 + RRF 融合 → 问答带引用
4. 推理：传递性前向链推导 + 级联撤销
5. 冲突：断言事实矛盾的检测与处置（关闭/保留/拒绝）
"""
