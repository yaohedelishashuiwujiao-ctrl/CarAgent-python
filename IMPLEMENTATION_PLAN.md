# 实施计划：SubjectsAgent-Platform → 生产级 AI 应用

## 项目备份

已备份至 `SubjectsAgent-Platform-backup-20260628-164209.tar.gz`（105MB）

---

## 当前代码基线

| 模块 | 行数 | 说明 |
|---|---:|---|
| Agent Runtime (Clawd Codex) | 8,868 | 30+ 工具, SSE 流式, 多 Provider |
| Backend (FastAPI) | 4,624 | 11 个 Router, Service/Repository 层 |
| Frontend (React) | 2,547 | 15 个页面组件 |
| **总计** | **16,039** | 8,192 个文件 |

---

## 技术选型

| 层级 | 选型 | 理由 |
|---|---|---|
| 向量数据库 | **ChromaDB** | 纯 Python、文件持久化、内置 HNSW + BM25 混合检索，无需额外服务 |
| 文档解析 | **PyMuPDF** (PDF) + **BeautifulSoup4** (HTML) + **pandas** (表格) | 已有 BS4，PyMuPDF 性能优于 PyPDF2 |
| Embedding | **OpenAI compatible** (Ark) + **sentence-transformers** (本地备选) | 与现有 Provider 体系一致 |
| Rerank | **BGE-Reranker** (FlagEmbedding) | 开源、中文效果好、本地运行 |
| Agent 编排 | **LangGraph** | JD 高频(23%)、状态机、checkpoint、human-in-the-loop |
| 评测 | **自建 Golden Dataset** + Ragas 部分指标 | 可控、可解释 |
| 容器化 | **Docker Compose** | 一键启动，JD 要求 |

> 注：保持 MySQL 作为主数据库不变（已有 17 张表），ChromaDB 仅用于向量/全文检索。

---

## Phase 1: RAG 全链路（~5 天）

### 1.1 文档处理管道
```
backend/app/rag/
├── parsers/
│   ├── pdf_parser.py       # PyMuPDF 解析，提取标题层级、表格、元数据
│   ├── html_parser.py      # BeautifulSoup4，提取正文、表格
│   ── table_parser.py     # pandas read_excel/csv，结构化表格解析
├── chunker.py              # 按标题层级 + 表格结构切分，保留车型/来源/日期元数据
├── embedder.py             # 调用 Ark OpenAI-compatible embedding API
├── store.py                # ChromaDB 封装：向量存储 + BM25 全文索引
├── retriever.py            # 混合检索：dense(HNSW) + sparse(BM25) + Rerank
└── __init__.py
```

**核心功能：**
- PDF/HTML/表格解析，保留元数据（车型、来源、日期、页码）
- 智能切分（按标题层级、表格边界，不硬截断）
- 混合检索（向量 + BM25 加权融合）
- BGE-Reranker 重排 Top-20 → Top-5
- 引用溯源（返回 chunk 元数据：来源文件、页码、车型）
- 拒答策略（最高 rerank 分数 < 阈值时拒答）

### 1.2 API 接口
```
/api/rag/ingest     POST  文档导入（文件上传/URL），返回解析结果和 chunk 统计
/api/rag/search     POST  混合检索 + rerank，返回带引用的结果
/api/rag/answer     POST  端到端 RAG：检索 → 生成 → 带引用回答
/api/rag/chunks     GET   chunk 列表/详情（管理界面用）
```

### 1.3 依赖安装
```
pip install chromadb pymupdf flagembedding bm25s sentence-transformers ragas
```

---

## Phase 2: LangGraph Agent 工作流（~4 天）

### 2.1 工作流定义
```
backend/app/agent/
├── workflow.py         # LangGraph StateGraph 定义
├── state.py            # AgentState (TypedDict)
├── nodes/
│   ├── planner.py      # 理解问题 → 生成执行计划（查什么表、检索什么文档）
│   ├── retrieve.py     # 结构化查询 (MySQL) + RAG 检索 (ChromaDB)
│   ├── analyze.py      # 基于证据生成分析结论
│   ├── verify.py       # 校验：引用完整性、逻辑一致性、置信度
│   ├── human_gate.py   # Human-in-the-loop：高风险结论需人工确认
│   └── publish.py      # 写入提案库 / 生成报告
└── tools/
    ├── vehicle_query.py    # 结构化车型查询工具
    ├── chart_gen.py        # 参数对比图表生成
    └── proposal_write.py   # 提案写入工具
```

**状态机：**
```
planner → retrieve → analyze → verify → human_gate? → publish
                    ↓                        ↓
                 retry ←────────────── low_confidence
```

### 2.2 关键特性
- **Checkpoint**：会话中断后可恢复（LangGraph 内置 SQLiteSaver）
- **Human-in-the-loop**：`interrupt_before=["human_gate"]`
- **工具 Schema**：Pydantic 校验、超时控制、重试策略
- **与 Clawd Codex 集成**：LangGraph 做高层编排，Clawd 提供通用工具（Read/Write/Bash/Web）

### 2.3 API 接口
```
/api/agent/workflow/chat    POST  LangGraph 工作流对话（SSE 流式）
/api/agent/workflow/approve POST  人工审批（approve/reject + 反馈）
/api/agent/workflow/state   GET   当前工作流状态/进度
```

---

## Phase 3: 评测体系（~3 天）

### 3.1 Golden Dataset
```
tests/rag_eval/
── golden_dataset.json     # 80+ 测试用例
│   # 分类：单跳查询(20) | 多跳推理(20) | 表格数据(15) |
│   #       无法回答(15) | 拒答测试(10)
├── test_retrieval.py       # Recall@5, MRR 评测
├── test_generation.py      # Faithfulness, Answer Relevancy (Ragas)
├── test_agent.py           # Tool Call Accuracy, 任务成功率
── report.py               # 生成评测报告（前后对比）
```

### 3.2 评测指标
| 指标 | 工具 | 目标 |
|---|---|---:|
| Recall@5 | 自建 | ≥ 80% |
| MRR | 自建 | ≥ 0.75 |
| Faithfulness | Ragas | ≥ 0.85 |
| Answer Relevancy | Ragas | ≥ 0.80 |
| 引用正确率 | 自建 | ≥ 90% |
| Tool Call Accuracy | 自建 | ≥ 85% |
| 任务成功率 | 自建 | ≥ 75% |

---

## Phase 4: 工程化与部署（~3 天）

### 4.1 Docker Compose
```yaml
services:
  mysql:        # 已有，保留
  redis:        # 已有，保留
  backend:      # FastAPI + uvicorn
  agent:        # Clawd Codex
  chromadb:     # ChromaDB 持久化卷
  frontend:     # Vite 构建
```

### 4.2 测试
```
tests/
├── test_rag_pipeline.py        # RAG 端到端测试
├── test_workflow.py            # LangGraph 工作流测试
├── test_tools.py               # 工具调用测试
└── conftest.py                 # fixture
```

### 4.3 可观测性
```
backend/app/
├── middleware/
│   ├── tracing.py          # Trace ID 传播、调用链记录
│   └── metrics.py          # 请求耗时、Token 成本、错误率
└── logging_config.py       # 结构化日志（JSON 格式）
```

---

## 实施顺序与里程碑

| 阶段 | 任务 | 预计 | 里程碑 |
|---|---|---:|---|
| **Phase 1** | RAG 管道 + API | 5 天 | 文档导入→检索→带引用回答跑通 |
| **Phase 2** | LangGraph 工作流 | 4 天 | planner→retrieve→analyze→verify→human→publish 全流程 |
| **Phase 3** | 评测体系 | 3 天 | 80 条 Golden Dataset + 评测报告 |
| **Phase 4** | 工程化 | 3 天 | Docker Compose 一键启动 + 测试覆盖 |
| **总计** | | **~15 天** | 可投递简历 |

---

## 风险与备选方案

| 风险 | 备选方案 |
|---|---|
| ChromaDB BM25 不支持 | 用 `bm25s` 库独立实现，结果融合 |
| FlagEmbedding 安装问题 | 用 `sentence-transformers` 的 cross-encoder 替代 rerank |
| LangGraph 与 Clawd 冲突 | LangGraph 只做编排，工具仍走 Clawd |
| 数据量不足影响评测 | 用汽车之家已导入的 120+ 车型数据 + 公开说明书 |
