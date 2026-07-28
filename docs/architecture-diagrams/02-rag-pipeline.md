# 2. Agentic RAG Pipeline

```mermaid
flowchart TD
    DOCS[Source corpus<br/>manuals / repair docs / reports / arXiv chunks] --> LOAD[Document loader<br/>500 documents]
    LOAD --> SPLIT[LlamaIndex SentenceSplitter<br/>chunk_size 1024<br/>chunk_overlap 150]
    SPLIT --> NODES[Nodes<br/>12,120 chunks<br/>chunk_id/document_id/source metadata]

    NODES --> EMBED[Embedding<br/>BAAI/bge-small-zh-v1.5<br/>512 dim<br/>batch size 32]
    EMBED --> MILVUS[Milvus vector index<br/>HNSW + COSINE<br/>M 16<br/>efConstruction 200<br/>search ef 64]

    NODES --> OS[OpenSearch sparse index<br/>BM25 inverted index<br/>chunk text + metadata]

    Q[Agent KnowledgeSearch query] --> DENSE[Dense retrieval<br/>Milvus top_k 30]
    Q --> SPARSE[Sparse retrieval<br/>OpenSearch BM25 top_k 30]
    DENSE --> RRF[RRF fusion<br/>k 60]
    SPARSE --> RRF
    RRF --> RERANK[Cross-encoder rerank<br/>BAAI/bge-reranker-base<br/>max_length 512<br/>rerank_top_n 10]
    RERANK --> RETURN[Cited chunks to agent<br/>top_k requested by tool<br/>score / rerank_score / retrieval ranks]
    RETURN --> FETCH[KnowledgeFetch<br/>full chunk + neighbor context by chunk_id]
```

## Key Parameters

| Layer | Current choice |
|---|---|
| RAG provider | `llamaindex-agentic-rag` |
| Chunking | LlamaIndex `SentenceSplitter`, `RAG_CHUNK_SIZE=1024`, `RAG_CHUNK_OVERLAP=150` |
| Corpus size | `500` documents, `12,120` chunks in the current local index |
| Embedding provider | HuggingFace local embedding by default |
| Embedding model | `BAAI/bge-small-zh-v1.5`, `RAG_EMBEDDING_DIM=512`, `RAG_EMBED_BATCH_SIZE=32` |
| Dense vector DB | Milvus, collection `subjects_agent_chunks` |
| Dense index | HNSW, COSINE, `M=16`, `efConstruction=200`, search `ef=64` |
| Sparse index | OpenSearch BM25 inverted index, index `subjects-agent-chunks` |
| Hybrid retrieval | Dense top 30 + sparse top 30, Reciprocal Rank Fusion `RRF_K=60` |
| Rerank | `BAAI/bge-reranker-base` cross-encoder, `RAG_RERANK_TOP_N=10`, `RAG_RERANK_MAX_LENGTH=512`, fp16 enabled by default |
| Tool boundary | Agent calls `KnowledgeSearch` and `KnowledgeFetch`; RAG is exposed as tools, not hidden prompt stuffing. |

## Evaluation Snapshot

The latest local pilot retrieval eval used 10 curated queries over the 500-document corpus:

| Metric | Result |
|---|---:|
| HitRate@1 | 0.90 |
| HitRate@10 | 1.00 |
| MRR@10 | 0.9167 |
| p50 latency | about 2.94s |
| p95 latency | about 3.21s |

This is a pilot smoke eval, not a full benchmark. A stronger evaluation set should include query expansion, hard negatives, answer faithfulness checks, citation correctness, and scenario-level agent task success.

