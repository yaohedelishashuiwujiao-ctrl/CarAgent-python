# SubjectsAgent FullStack RAG

这是从 `SubjectsAgent-Platform` 抽取并按架构重新整理的完整项目版本，覆盖前端、后端、Agent Runtime、Agentic RAG、视觉服务、语料资源、评测和部署配置。

## Architecture Layout

```text
apps/frontend/          Vite/React 前端应用
services/backend/       FastAPI 平台后端，包含 /api/rag、/api/agent 等接口
services/vision/        视觉模型服务和训练/推理脚本
agent/runtime/          Agent Runtime、工具系统、KnowledgeSearch/KnowledgeFetch
rag/infra/              Milvus/OpenSearch 本地基础设施
rag/resources/          RAG 文档语料、PDF、文本、预解析 chunk cache
scripts/                数据采集、语料处理、RAG 评测、平台脚本
evals/                  评测集、评测结果、评测说明
database/               MySQL schema、seed、migration
models/                 本地模型文件
docs/                   架构、RAG、Agent、数据链路文档
tests/                  后端、Agent、RAG 测试
config/                 依赖清单和环境变量样例
```

为了保持原代码导入路径和脚本兼容，项目根目录保留了软链接：

```text
backend -> services/backend
frontend -> apps/frontend
agent_runtime -> agent/runtime
resources -> rag/resources
vision_model -> services/vision
```

## Current RAG Stack

```text
Framework: LlamaIndex Agentic RAG
Chunking: SentenceSplitter, chunk_size=1024, overlap=150
Embedding: BAAI/bge-small-zh-v1.5, 512 dim
Vector DB: Milvus, HNSW, COSINE
Sparse Index: OpenSearch BM25
Fusion: RRF
Reranker: BAAI/bge-reranker-base cross-encoder
Agent Tools: KnowledgeSearch / KnowledgeFetch
```

当前本地索引规模：

```text
document_count = 500
chunk_count    = 12120
```

语料组成：车辆手册、维修资料、行业/政策报告、汽车 arXiv 论文。

## Quick Start

1. 安装后端依赖：

```bash
python3 -m pip install -r services/backend/requirements.txt
```

2. 安装前端依赖：

```bash
cd apps/frontend
npm install
```

3. 启动 RAG 基础设施：

```bash
docker compose -f rag/infra/docker-compose.rag.yml up -d
```

4. 配置环境变量：

```bash
cp config/env/.env.example .env.local
```

5. 重建 RAG 索引：

```bash
python3 - <<'PY'
from dotenv import load_dotenv
load_dotenv('.env.local')
from backend.app.services.rag import rag_service
print(rag_service.rebuild())
PY
```

6. 启动后端：

```bash
uvicorn backend.app.main:app --host 127.0.0.1 --port 8010
```

7. 启动前端：

```bash
cd apps/frontend
npm run dev
```

## RAG Evaluation

```bash
python3 scripts/evaluate_current_rag.py \
  --suite evals/suites/rag_pilot_v1.json \
  --out evals/results/llamaindex_light_rag_500doc_pilot_v1.json
```

当前 500 文档 pilot 指标：

```text
HitRate@1  = 0.90
HitRate@10 = 1.00
MRR@10     = 0.9167
p50        = 2937 ms
p95        = 3205 ms
```

## Notes

- `.env.local` 没有复制到该项目，避免泄露本地 API Key。
- `node_modules`、缓存、日志、备份、`.git` 没有复制。
- 代码仍保持原有 Python import 兼容路径，可以直接从项目根目录运行现有脚本。
