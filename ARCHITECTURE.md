# Architecture

## Runtime Flow

```text
Frontend
  -> FastAPI Backend
  -> Agent Runtime
  -> Tool System
  -> KnowledgeSearch / KnowledgeFetch
  -> Backend /api/rag
  -> LlamaIndex Retrieval Layer
  -> Milvus dense retrieval
  -> OpenSearch BM25 retrieval
  -> RRF fusion
  -> Cross-encoder rerank
  -> cited chunks returned to Agent
```

## Layer Responsibilities

| Layer | Path | Responsibility |
|---|---|---|
| Frontend | `apps/frontend` | User-facing UI, API calls, task/session views |
| Backend | `services/backend` | API gateway, auth/data scope, RAG endpoints, platform services |
| Agent Runtime | `agent/runtime` | Agent loop, tool routing, tool schemas, execution control |
| RAG | `services/backend/app/services/rag.py` | LlamaIndex ingestion/retrieval, Milvus/OpenSearch integration, evaluation |
| RAG Infra | `rag/infra` | Milvus, etcd, MinIO, OpenSearch local services |
| RAG Corpus | `rag/resources` | Manuals, repair docs, reports, arXiv PDF corpus and chunk caches |
| Evaluation | `evals`, `scripts/evaluate_current_rag.py` | Retrieval metrics and reproducible evaluation output |
| Vision | `services/vision` | Visual model training/inference service |
| Database | `database` | Schema, seed data, migrations |

## RAG Technical Choices

- Document chunking: LlamaIndex `SentenceSplitter` with metadata preservation.
- Dense retrieval: Milvus HNSW over HuggingFace embedding vectors.
- Sparse retrieval: OpenSearch BM25 inverted index.
- Fusion: Reciprocal Rank Fusion.
- Reranking: `BAAI/bge-reranker-base` cross-encoder via `transformers + torch`.
- Agent integration: `KnowledgeSearch` and `KnowledgeFetch` tools.

## Current Scale

```text
Documents: 500
Chunks:    12120
```

This is a vertical automotive knowledge base suitable for a private-deployment prototype. The architecture is prepared for larger document volume by separating vector search, keyword search, reranking, and agent tool use.
