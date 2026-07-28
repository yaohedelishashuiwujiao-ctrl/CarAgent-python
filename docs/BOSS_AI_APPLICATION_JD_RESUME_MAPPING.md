# BOSS 直聘 AI 应用开发岗位与简历映射

## 调研范围

调研时间：2026-07-19

样本来自 BOSS 直聘北京、上海、苏州、深圳和广州的 AI 应用开发、AI Agent 开发、大模型应用开发和工业 Agent 岗位，重点参考汽车、工业和企业级 Agent 场景。

主要样本页：

- [上海 AI 应用研发工程师](https://www.zhipin.com/zhaopin/fb935b1cb5b294eb1Hd53N-9GQ~~/)
- [北京 AI 开发工程师](https://www.zhipin.com/zhaopin/4bf879c1368205460nB_39q7Fg~~/)
- [上海 AI Agent 开发工程师](https://www.zhipin.com/zhaopin/42555746a85eb0741HB43di7GQ~~/)
- [苏州 AI Agent 开发工程师](https://www.zhipin.com/zhaopin/e62ced1a0c5547ef0nR-3tm6Ew~~/)
- [苏州 AI 工程师](https://www.zhipin.com/zhaopin/77357c4135a111b203Ny2Ni7EQ~~/)
- [广州 AI 开发工程师](https://www.zhipin.com/zhaopin/694c682c6021630e0nB82Nu-FQ~~/)

## JD 关注层级

### A 级：简历必须直接出现

| JD 关注点 | BOSS 常见表述 | 本项目对应能力 |
|---|---|---|
| 真实业务落地 | “真实业务场景的设计、开发与落地” | 汽车研发场景，打通数据查询、分析、研究和交付 |
| Agent 整体架构 | “从 0 到 1”“Agent 核心架构”“工业 Agent” | 用户问题到异步任务、Agent 决策、工具执行、证据交付的完整链路 |
| Agent 循环 | “任务规划”“工具调用”“结果总结”“反思/自我修复” | 任务契约、计划、工具观察、状态更新、重规划和完成条件 |
| Tool Calling | “Tool Calling”“Function Calling”“Skill”“工具编排” | 动态工具候选、预检、批处理/并行、精确重试和结果回写 |
| RAG | “RAG 集成”“RAG 全链路”“知识与数据通道” | 结构化查询 + BM25/TF-IDF/RRF 混合检索 + 元数据过滤 + Search/Fetch |
| 工程开发 | Python、FastAPI/后端服务、数据库、API 与服务化部署 | Python + FastAPI + MySQL + Redis + React + SSE 的完整应用链路 |

### B 级：高级/企业级岗位的区分度

| JD 关注点 | BOSS 常见表述 | 简历应如何写 |
|---|---|---|
| 上下文与记忆 | “上下文窗口利用率”“Memory”“长短期记忆” | 写 Token 预算、工具结果外置和长会话压缩，不展开软/硬阈值实现 |
| 稳定与可扩展 | “高并发分布式架构”“稳定、高效、可扩展” | 写异步任务、公平调度、资源隔离、工具并行和会话一致性 |
| 可审计/可观测 | “可审计、可观测、可维护” | 写路由、工具、Token、证据和异常全链路追踪 |
| 评测与优化 | “效果持续优化”“检索算法优化” | 写路由/工具/RAG/证据/任务分层评测，保留 Recall@K/MRR 两个可识别指标 |
| 安全与数据权限 | 企业级岗位常以“权限、审计、数据安全”概括 | 写身份/数据范围/工具三层权限和 SQL/RAG 安全边界 |
| 成本与性能 | 高级岗位关注 Token、上下文、延迟、资源占用 | 写任务路由、模型/工具预算、确定性快速路径和低收益终止 |

### C 级：应下沉到面试展开的实现细节

以下能力很有价值，但不应在一页简历中并列堆叠：

- Outbox、DRR 积分、execution/fencing token、租约心跳的具体协议。
- HMAC Token 的 issuer/audience/session/防重放校验细节。
- SQL AST、EXPLAIN、返回行数/字节限制的完整安全规则。
- `outcome_status`、`reason_code`、`coverage_boundary` 等内部字段名。
- evidence hash、claim→citation 的具体存储结构和修复算法。
- ToolSearch 正反例、候选工具阶段、预检 reason code 和精确重试白名单。
- 上下文软/硬阈值、tool-call/tool-result 原子裁剪和证据句柄格式。

这些细节不是删除，而是转译成简历中的上层能力：

| 实现细节 | 简历表达 |
|---|---|
| Outbox + lock + lease + fencing | 异步任务、会话一致性与故障恢复 |
| HMAC + allowlist + SQL AST + ACL | 身份、工具和数据范围三层安全边界 |
| outcome/reason/coverage | 基于结构化执行结果重规划与降级 |
| evidence hash + claim/citation | 结论—证据映射与引用/数值一致性校验 |
| ToolSearch + Preflight + retry policy | 动态工具发现、调用预检与可恢复失败处理 |
| soft/hard context budget | 基于 Token 预算的长上下文压缩 |

## 对当前六维度的结论

| 维度 | 是否保留 | 调整方向 |
|---|---|---|
| 整体框架 | 必须保留 | 不写“七层”名单，改写从 0 到 1 和端到端业务闭环 |
| 并发能力 | 保留 | 保留异步任务、公平调度、工具并行和会话一致性；下沉协议名称 |
| 安全能力 | 保留 | 保留三层权限、SQL/RAG 边界和证据审计；下沉校验字段和算法细节 |
| Agent 循环 | 必须保留，应提高优先级 | 保留任务契约、计划、观察、重规划和完成条件 |
| 工具调用 + RAG | 必须保留，应提高优先级 | 工具机制和混合 RAG 分开表达，避免一条过载 |
| 成本控制 | 保留，作为区分度 | 保留路由、预算、上下文和低收益终止，不列举所有内部字段 |

## 简历下一版的建议结构

1. 项目定位：业务问题 + 数据基础 + 从 0 到 1 Agent 落地。
2. Agent 架构与循环：任务规划、工具观察、重规划、完成契约。
3. 工具机制：动态发现、预检、Batch/并行、失败处理。
4. 数据分析与 RAG：结构化数据事实源 + 混合 RAG + 检索评测。
5. 成本控制：确定性快速路径 + 模型/工具预算 + 上下文压缩 + 低收益终止。
6. 生产工程：异步调度、工具并行、会话一致性、权限/数据边界、证据审计和分层评测。
