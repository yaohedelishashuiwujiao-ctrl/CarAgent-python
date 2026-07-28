from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any


def _find_project_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "rag" / "resources").exists() or (parent / "resources").exists():
            return parent
    return current.parents[3]


ROOT_DIR = _find_project_root()
RESOURCE_ROOT = ROOT_DIR / "rag" / "resources" if (ROOT_DIR / "rag" / "resources").exists() else ROOT_DIR / "resources"
CORPUS_DIR = RESOURCE_ROOT / "manual_corpus"
REPORT_CORPUS_DIR = RESOURCE_ROOT / "report_corpus"
REPAIR_CORPUS_DIR = RESOURCE_ROOT / "repair_corpus"
MANIFESTS = (
    CORPUS_DIR / "downloads" / "manifest.jsonl",
    CORPUS_DIR / "downloads_manual_pages" / "manifest.jsonl",
    CORPUS_DIR / "downloads_manual_pdfs" / "manifest.jsonl",
    REPAIR_CORPUS_DIR / "downloads" / "manifest.jsonl",
    REPORT_CORPUS_DIR / "downloads" / "manifest.jsonl",
)
PRECHUNKED_PDF_MANIFESTS = (
    REPORT_CORPUS_DIR / "arxiv_chunks" / "manifest.jsonl",
)
PDF_TEXT_DIR = CORPUS_DIR / "downloads_manual_pdfs" / "text"
REPORT_TEXT_DIR = REPORT_CORPUS_DIR / "downloads" / "text"
REPAIR_TEXT_DIR = REPAIR_CORPUS_DIR / "downloads" / "text"

DEFAULT_DENSE_TOP_K = 50
DEFAULT_SPARSE_TOP_K = 50
DEFAULT_RERANK_TOP_N = 20
RRF_K = 60


@dataclass(frozen=True)
class RagDocument:
    document_id: str
    dataset_id: str
    title: str
    text: str
    source: str
    metadata: dict[str, str]


class RagDependencyError(RuntimeError):
    pass


class RagService:
    """LlamaIndex-based Agentic Hybrid RAG.

    This service intentionally has no sparse-only fallback.  Rebuild and search
    require LlamaIndex, bge embeddings, Milvus, OpenSearch, and the reranker to
    be configured and available.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._embed_model: Any | None = None
        self._reranker: Any | None = None
        self._index: Any | None = None
        self._index_signature: str | None = None

    def status(self) -> dict[str, Any]:
        self._require_dependencies()
        return {
            "provider": "llamaindex-agentic-rag",
            "ready": self._healthcheck(),
            "index_type": "hybrid-dense-sparse-rrf-rerank",
            "framework": "LlamaIndex",
            "chunking": {
                "parser": "SentenceSplitter",
                "strategy": "structure-aware metadata + semantic token windows",
                "chunk_size": self._chunk_size(),
                "chunk_overlap": self._chunk_overlap(),
            },
            "embedding": {
                "provider": self._embedding_provider(),
                "model": self._embedding_model_name(),
                "dimension": self._embedding_dim(),
            },
            "vector_store": {
                "type": "Milvus",
                "uri": self._milvus_uri(),
                "collection": self._milvus_collection(),
                "metric": "COSINE",
                "index": "HNSW",
            },
            "keyword_store": {
                "type": "OpenSearch",
                "url": self._opensearch_url(),
                "index": self._opensearch_index(),
                "retrieval": "BM25",
            },
            "fusion": {"method": "RRF", "k": RRF_K},
            "reranker": {"model": self._rerank_model_name(), "top_n": self._rerank_top_n()},
            "agent_tools": ["LlamaIndex FunctionTool:KnowledgeSearch", "LlamaIndex FunctionTool:KnowledgeFetch"],
            "index_version": self._index_signature,
        }

    def llamaindex_tools(self) -> list[Any]:
        self._require_dependencies()
        from llama_index.core.tools import FunctionTool

        return [
            FunctionTool.from_defaults(
                fn=self._tool_search,
                name="KnowledgeSearch",
                description="Search the LlamaIndex Agentic Hybrid RAG index and return cited chunks.",
            ),
            FunctionTool.from_defaults(
                fn=self._tool_fetch,
                name="KnowledgeFetch",
                description="Fetch a full cited chunk and adjacent context by chunk_id.",
            ),
        ]

    def rebuild(self) -> dict[str, Any]:
        self._require_dependencies()
        with self._lock:
            started = time.perf_counter()
            documents = self._load_documents()
            if not documents:
                raise ValueError("no source documents available for RAG indexing")
            nodes = self._build_nodes(documents)
            if not nodes:
                raise ValueError("document parsing produced no LlamaIndex nodes")
            self._rebuild_opensearch(nodes)
            self._rebuild_milvus(nodes)
            self._index_signature = self._corpus_signature()
            return {
                **self.status(),
                "document_count": len(documents),
                "chunk_count": len(nodes),
                "rebuild_seconds": round(time.perf_counter() - started, 3),
            }

    def search(
        self,
        query: str,
        *,
        top_k: int = 8,
        document_ids: list[str] | None = None,
        metadata: dict[str, str] | None = None,
        data_scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_dependencies()
        query = query.strip()
        if not query:
            raise ValueError("query is required")
        filters = self._merged_filters(document_ids, metadata, data_scope)
        dense_hits = self._dense_search(query, filters)
        sparse_hits = self._sparse_search(query, filters)
        fused = self._fuse(dense_hits, sparse_hits)
        reranked = self._rerank(query, fused[: self._rerank_top_n()])
        selected = reranked[: max(1, min(top_k, 20))]
        return {
            "provider": "llamaindex-agentic-rag",
            "query": query,
            "result_count": len(selected),
            "results": [self._public_result(hit) for hit in selected],
            "retrieval": {
                "strategy": "milvus-dense + opensearch-bm25 + rrf + cross-encoder-reranker",
                "dense_top_k": self._dense_top_k(),
                "sparse_top_k": self._sparse_top_k(),
                "rerank_top_n": self._rerank_top_n(),
                "filters": filters,
            },
        }

    def fetch(self, chunk_id: str, *, data_scope: dict[str, Any] | None = None) -> dict[str, Any] | None:
        self._require_dependencies()
        chunk_id = chunk_id.strip()
        if not chunk_id:
            raise ValueError("chunk_id is required")
        client = self._opensearch_client()
        try:
            response = client.get(index=self._opensearch_index(), id=chunk_id)
        except Exception:
            return None
        source = response.get("_source") or {}
        if not self._matches_scope(source, data_scope):
            return None
        result = self._public_result({"chunk_id": chunk_id, **source}, full_text=True)
        result["context"] = self._neighbor_context(source, data_scope)
        return result

    def evaluate(self, cases: list[dict[str, Any]]) -> dict[str, Any]:
        if not cases:
            raise ValueError("cases must contain at least one query")
        evaluated: list[dict[str, Any]] = []
        cutoffs = (1, 3, 5, 10)
        hit_at = {k: 0.0 for k in cutoffs}
        recall_at = {k: 0.0 for k in cutoffs}
        reciprocal_rank = 0.0
        latencies_ms: list[float] = []
        for case in cases:
            query = str(case.get("query") or "").strip()
            expected_chunks = {str(value) for value in (case.get("expected_chunk_ids") or [])}
            expected_documents = {str(value) for value in (case.get("expected_document_ids") or [])}
            if not query or not (expected_chunks or expected_documents):
                raise ValueError("each case requires query and expected_document_ids or expected_chunk_ids")
            started = time.perf_counter()
            results = self.search(query, top_k=10, metadata=case.get("metadata") or {})["results"]
            latencies_ms.append((time.perf_counter() - started) * 1000)
            chunks = [str(result["chunk_id"]) for result in results]
            documents = [str(result["document_id"]) for result in results]
            relevant = [
                (chunk in expected_chunks) if expected_chunks else (document in expected_documents)
                for chunk, document in zip(chunks, documents, strict=True)
            ]
            first_rank = next((rank for rank, ok in enumerate(relevant, start=1) if ok), None)
            for k in cutoffs:
                hit_at[k] += float(any(relevant[:k]))
                if expected_chunks:
                    recall_at[k] += len(set(chunks[:k]) & expected_chunks) / len(expected_chunks)
                else:
                    recall_at[k] += float(any(document in expected_documents for document in documents[:k]))
            if first_rank is not None:
                reciprocal_rank += 1 / first_rank
            evaluated.append(
                {
                    "query": query,
                    "expected_document_ids": sorted(expected_documents),
                    "expected_chunk_ids": sorted(expected_chunks),
                    "returned_document_ids": documents,
                    "returned_chunk_ids": chunks,
                    "first_relevant_rank": first_rank,
                    "latency_ms": round(latencies_ms[-1], 3),
                }
            )
        total = len(evaluated)
        latencies = sorted(latencies_ms)
        percentile = lambda value: latencies[round((len(latencies) - 1) * value)]
        return {
            "case_count": total,
            "metrics": {
                **{f"hit_rate_at_{k}": round(hit_at[k] / total, 4) for k in cutoffs},
                **{f"recall_at_{k}": round(recall_at[k] / total, 4) for k in cutoffs},
                "mrr_at_10": round(reciprocal_rank / total, 4),
                "latency_p50_ms": round(percentile(0.5), 3),
                "latency_p95_ms": round(percentile(0.95), 3),
            },
            "cases": evaluated,
            "index_version": self._index_signature,
        }

    def _tool_search(self, query: str, top_k: int = 8) -> str:
        return json.dumps(self.search(query, top_k=top_k), ensure_ascii=False)

    def _tool_fetch(self, chunk_id: str) -> str:
        result = self.fetch(chunk_id)
        return json.dumps(result or {"error": "chunk not found"}, ensure_ascii=False)

    def _require_dependencies(self) -> None:
        missing = []
        for module in (
            "llama_index.core",
            "llama_index.embeddings.huggingface",
            "llama_index.vector_stores.milvus",
            "opensearchpy",
            "transformers",
            "torch",
        ):
            try:
                __import__(module)
            except Exception as exc:
                missing.append(f"{module}: {type(exc).__name__}: {exc}")
        if missing:
            raise RagDependencyError("RAG dependencies are missing: " + "; ".join(missing))

    def _healthcheck(self) -> bool:
        client = self._opensearch_client()
        if not client.ping():
            raise RagDependencyError(f"OpenSearch is not reachable: {self._opensearch_url()}")
        self._milvus_vector_store()
        self._embed()
        self._reranker_model()
        return True

    def _load_documents(self) -> list[RagDocument]:
        documents: list[RagDocument] = []
        for manifest in MANIFESTS:
            for row in self._jsonl_rows(manifest):
                text = self._document_text(row)
                if not text:
                    continue
                document_id = str(row.get("id") or hashlib.sha256(str(row).encode()).hexdigest()[:16])
                artifact_path = str(row.get("artifact_path") or "")
                dataset_id = self._dataset_id(artifact_path)
                title = str(row.get("title") or "") or " / ".join(
                    str(row.get(key) or "") for key in ("brand", "model", "year", "source_type") if row.get(key)
                )
                source = str(row.get("official_url") or row.get("final_url") or artifact_path)
                metadata = {
                    key: str(row[key])
                    for key in (
                        "brand", "model", "year", "market", "language", "source_type", "artifact_path",
                        "publisher", "report_date", "topic", "license",
                        "document_version", "classification", "acl", "publication_status",
                    )
                    if row.get(key) not in (None, "")
                }
                metadata.update(
                    {
                        "document_id": document_id,
                        "dataset_id": dataset_id,
                        "title": title or document_id,
                        "source": source,
                        "parser": "llamaindex",
                        "chunking": "SentenceSplitter",
                    }
                )
                metadata.setdefault("document_version", str(row.get("version") or "legacy-v1"))
                metadata.setdefault("publication_status", "published")
                documents.append(RagDocument(document_id, dataset_id, title or document_id, text, source, metadata))
        documents.extend(self._load_prechunked_pdf_documents())
        return documents

    def _load_prechunked_pdf_documents(self) -> list[RagDocument]:
        documents: list[RagDocument] = []
        remaining = self._prechunked_max_documents()
        if remaining <= 0:
            return documents
        for manifest in PRECHUNKED_PDF_MANIFESTS:
            for row in self._jsonl_rows(manifest):
                if remaining <= 0:
                    return documents
                if row.get("status") not in {"ok", "cached"}:
                    continue
                cache_path = ROOT_DIR / str(row.get("cache_path") or "")
                if not cache_path.exists():
                    continue
                try:
                    payload = json.loads(cache_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                source_row = payload.get("source") if isinstance(payload.get("source"), dict) else {}
                document_id = str(payload.get("document_id") or row.get("document_id") or source_row.get("id") or "")
                if not document_id:
                    continue
                chunks = payload.get("chunks") if isinstance(payload.get("chunks"), list) else []
                parts = [str(chunk.get("text") or "").strip() for chunk in chunks if isinstance(chunk, dict)]
                text = self._compact("\n\n".join(part for part in parts if part))
                if not text:
                    continue
                title = str(source_row.get("title") or document_id)
                source = str(source_row.get("official_url") or source_row.get("landing_url") or source_row.get("artifact_path") or "")
                metadata = {
                    key: str(source_row[key])
                    for key in (
                        "publisher", "report_date", "topic", "license", "source_type", "artifact_path",
                        "authors", "landing_url", "discovered_by",
                    )
                    if source_row.get(key) not in (None, "")
                }
                metadata.update(
                    {
                        "document_id": document_id,
                        "dataset_id": "automotive-arxiv-corpus",
                        "title": title,
                        "source": source,
                        "parser": "pdf-structure-cache",
                        "chunking": "SentenceSplitter",
                        "document_version": str(source_row.get("sha256") or payload.get("source_sha256") or "arxiv-cache"),
                        "publication_status": "published",
                    }
                )
                documents.append(RagDocument(document_id, "automotive-arxiv-corpus", title, text, source, metadata))
                remaining -= 1
        return documents

    def _build_nodes(self, documents: list[RagDocument]) -> list[Any]:
        from llama_index.core import Document
        from llama_index.core.node_parser import SentenceSplitter

        splitter = SentenceSplitter(chunk_size=self._chunk_size(), chunk_overlap=self._chunk_overlap())
        llama_docs = [
            Document(
                text=document.text,
                id_=document.document_id,
                metadata=document.metadata,
                excluded_embed_metadata_keys=("acl", "classification"),
                excluded_llm_metadata_keys=("acl", "classification"),
            )
            for document in documents
        ]
        nodes = splitter.get_nodes_from_documents(llama_docs, show_progress=True)
        ordinal_by_doc: defaultdict[str, int] = defaultdict(int)
        for node in nodes:
            document_id = str(node.metadata.get("document_id") or node.ref_doc_id or "")
            ordinal = ordinal_by_doc[document_id]
            ordinal_by_doc[document_id] += 1
            text = node.get_content(metadata_mode="none")
            chunk_id = "chunk-" + hashlib.sha256(f"{document_id}:{ordinal}:{text}".encode("utf-8")).hexdigest()[:20]
            node.id_ = chunk_id
            node.metadata["chunk_id"] = chunk_id
            node.metadata["ordinal"] = str(ordinal)
            node.metadata["content_type"] = node.metadata.get("content_type", "text")
            node.metadata["section_path"] = node.metadata.get("section_path", "")
        return nodes

    def _rebuild_milvus(self, nodes: list[Any]) -> None:
        from llama_index.core import StorageContext, VectorStoreIndex

        vector_store = self._milvus_vector_store(overwrite=True)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        self._index = VectorStoreIndex(
            nodes,
            storage_context=storage_context,
            embed_model=self._embed(),
            show_progress=True,
        )

    def _rebuild_opensearch(self, nodes: list[Any]) -> None:
        client = self._opensearch_client()
        index = self._opensearch_index()
        if client.indices.exists(index=index):
            client.indices.delete(index=index)
        client.indices.create(index=index, body=self._opensearch_mapping())
        actions = []
        for node in nodes:
            text = node.get_content(metadata_mode="none")
            metadata = {key: str(value) for key, value in node.metadata.items() if value is not None}
            actions.append(
                {
                    "_op_type": "index",
                    "_index": index,
                    "_id": metadata["chunk_id"],
                    "_source": {
                        "chunk_id": metadata["chunk_id"],
                        "document_id": metadata["document_id"],
                        "dataset_id": metadata["dataset_id"],
                        "title": metadata.get("title", ""),
                        "source": metadata.get("source", ""),
                        "ordinal": int(metadata.get("ordinal", 0)),
                        "text": text,
                        "metadata": metadata,
                    },
                }
            )
        from opensearchpy.helpers import bulk

        bulk(client, actions, request_timeout=120)
        client.indices.refresh(index=index)

    def _dense_search(self, query: str, filters: dict[str, str]) -> list[dict[str, Any]]:
        from llama_index.core import VectorStoreIndex

        if self._index is None:
            self._index = VectorStoreIndex.from_vector_store(self._milvus_vector_store(), embed_model=self._embed())
        retriever = self._index.as_retriever(similarity_top_k=self._dense_top_k() * 2)
        nodes = retriever.retrieve(query)
        hits = []
        for item in nodes:
            node = item.node
            metadata = {key: str(value) for key, value in node.metadata.items() if value is not None}
            hit = {
                "chunk_id": metadata.get("chunk_id") or node.node_id,
                "document_id": metadata.get("document_id", ""),
                "dataset_id": metadata.get("dataset_id", ""),
                "title": metadata.get("title", ""),
                "source": metadata.get("source", ""),
                "ordinal": int(metadata.get("ordinal", 0) or 0),
                "text": node.get_content(metadata_mode="none"),
                "metadata": metadata,
                "dense_score": float(item.score or 0.0),
            }
            if self._matches_filters(hit, filters):
                hits.append(hit)
            if len(hits) >= self._dense_top_k():
                break
        return hits

    def _sparse_search(self, query: str, filters: dict[str, str]) -> list[dict[str, Any]]:
        client = self._opensearch_client()
        body = {
            "size": self._sparse_top_k(),
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query,
                                "fields": ["title^2", "text", "metadata.brand^2", "metadata.model^2", "metadata.system^2"],
                                "type": "best_fields",
                            }
                        }
                    ],
                    "filter": self._opensearch_filters(filters),
                }
            },
        }
        response = client.search(index=self._opensearch_index(), body=body)
        hits = []
        for item in response.get("hits", {}).get("hits", []):
            source = item.get("_source") or {}
            source["sparse_score"] = float(item.get("_score") or 0.0)
            hits.append(source)
        return hits

    def _fuse(self, dense_hits: list[dict[str, Any]], sparse_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        scores: defaultdict[str, float] = defaultdict(float)
        ranks: defaultdict[str, dict[str, int]] = defaultdict(dict)
        for channel, hits in (("dense", dense_hits), ("bm25", sparse_hits)):
            for rank, hit in enumerate(hits, start=1):
                chunk_id = str(hit["chunk_id"])
                by_id.setdefault(chunk_id, hit)
                by_id[chunk_id].update(hit)
                scores[chunk_id] += 1 / (RRF_K + rank)
                ranks[chunk_id][channel] = rank
        ordered = sorted(scores, key=lambda chunk_id: scores[chunk_id], reverse=True)
        results = []
        for chunk_id in ordered:
            hit = by_id[chunk_id]
            hit["score"] = scores[chunk_id]
            hit["retrieval"] = {"fusion": "rrf", "ranks": ranks[chunk_id]}
            results.append(hit)
        return results

    def _rerank(self, query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not hits:
            return []
        model = self._reranker_model()
        pairs = [[query, str(hit.get("text") or "")] for hit in hits]
        scores = model.compute_score(pairs, normalize=True)
        if isinstance(scores, (int, float)):
            scores = [float(scores)]
        reranked = []
        for hit, score in zip(hits, scores, strict=True):
            item = dict(hit)
            item["rerank_score"] = float(score)
            reranked.append(item)
        return sorted(reranked, key=lambda item: item["rerank_score"], reverse=True)

    def _neighbor_context(self, source: dict[str, Any], data_scope: dict[str, Any] | None) -> list[dict[str, Any]]:
        document_id = str(source.get("document_id") or "")
        ordinal = int(source.get("ordinal") or 0)
        client = self._opensearch_client()
        body = {
            "size": 2,
            "sort": [{"ordinal": "asc"}],
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"document_id": document_id}},
                        {"terms": {"ordinal": [ordinal - 1, ordinal + 1]}},
                    ]
                }
            },
        }
        response = client.search(index=self._opensearch_index(), body=body)
        context = []
        for item in response.get("hits", {}).get("hits", []):
            candidate = item.get("_source") or {}
            if self._matches_scope(candidate, data_scope):
                context.append(self._public_result(candidate, full_text=True))
        return context

    def _public_result(self, hit: dict[str, Any], *, full_text: bool = False) -> dict[str, Any]:
        text = str(hit.get("text") or "")
        result = {
            "chunk_id": str(hit.get("chunk_id") or ""),
            "document_id": str(hit.get("document_id") or ""),
            "dataset_id": str(hit.get("dataset_id") or ""),
            "title": str(hit.get("title") or ""),
            "source": str(hit.get("source") or ""),
            "ordinal": int(hit.get("ordinal") or 0),
            "excerpt": text if full_text else text[:1500],
            "metadata": hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {},
        }
        for key in ("score", "dense_score", "sparse_score", "rerank_score", "retrieval"):
            if key in hit:
                result[key] = hit[key]
        return result

    def _embed(self) -> Any:
        if self._embed_model is None:
            self._normalize_proxy_env()
            provider = self._embedding_provider()
            if provider == "huggingface":
                from llama_index.embeddings.huggingface import HuggingFaceEmbedding

                self._embed_model = HuggingFaceEmbedding(
                    model_name=self._embedding_model_name(),
                    trust_remote_code=True,
                    embed_batch_size=self._embed_batch_size(),
                    device=self._embed_device(),
                )
            elif provider in {"openai", "openai-compatible", "ark"}:
                from llama_index.embeddings.openai import OpenAIEmbedding

                api_key = os.getenv("RAG_EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("ARK_API_KEY")
                if not api_key:
                    raise RagDependencyError("RAG_EMBEDDING_API_KEY, OPENAI_API_KEY, or ARK_API_KEY is required for API embeddings")
                self._embed_model = OpenAIEmbedding(
                    model=self._embedding_model_name(),
                    api_key=api_key,
                    api_base=self._embedding_api_base(),
                    dimensions=self._embedding_dim() if self._embedding_dim() > 0 else None,
                    embed_batch_size=self._embed_batch_size(),
                    timeout=float(os.getenv("RAG_EMBEDDING_TIMEOUT_SECONDS", "120")),
                )
            else:
                raise RagDependencyError(f"unsupported RAG_EMBEDDING_PROVIDER: {provider}")
        return self._embed_model

    def _reranker_model(self) -> Any:
        if self._reranker is None:
            self._normalize_proxy_env()
            self._reranker = _CrossEncoderReranker(
                self._rerank_model_name(),
                use_fp16=self._rerank_fp16(),
                device=self._rerank_device(),
                max_length=self._rerank_max_length(),
            )
        return self._reranker

    def _milvus_vector_store(self, *, overwrite: bool = False) -> Any:
        from llama_index.vector_stores.milvus import MilvusVectorStore

        kwargs: dict[str, Any] = {
            "uri": self._milvus_uri(),
            "collection_name": self._milvus_collection(),
            "dim": self._embedding_dim(),
            "overwrite": overwrite,
            "similarity_metric": "COSINE",
            "index_config": {"index_type": "HNSW", "metric_type": "COSINE", "params": {"M": 16, "efConstruction": 200}},
            "search_config": {"metric_type": "COSINE", "params": {"ef": 64}},
        }
        token = os.getenv("RAG_MILVUS_TOKEN", "").strip()
        if token:
            kwargs["token"] = token
        return MilvusVectorStore(**kwargs)

    def _opensearch_client(self) -> Any:
        from opensearchpy import OpenSearch

        username = os.getenv("RAG_OPENSEARCH_USERNAME", "").strip()
        password = os.getenv("RAG_OPENSEARCH_PASSWORD", "").strip()
        auth = (username, password) if username or password else None
        return OpenSearch(
            hosts=[self._opensearch_url()],
            http_auth=auth,
            use_ssl=self._opensearch_url().startswith("https://"),
            verify_certs=os.getenv("RAG_OPENSEARCH_VERIFY_CERTS", "false").strip().lower() in {"1", "true", "yes"},
            timeout=30,
            max_retries=2,
            retry_on_timeout=True,
        )

    @staticmethod
    def _opensearch_mapping() -> dict[str, Any]:
        return {
            "settings": {
                "index": {"number_of_shards": 1, "number_of_replicas": 0},
                "analysis": {
                    "analyzer": {
                        "rag_text_analyzer": {
                            "type": "custom",
                            "tokenizer": "standard",
                            "filter": ["lowercase"],
                        }
                    }
                },
            },
            "mappings": {
                "dynamic": True,
                "properties": {
                    "chunk_id": {"type": "keyword"},
                    "document_id": {"type": "keyword"},
                    "dataset_id": {"type": "keyword"},
                    "title": {"type": "text", "analyzer": "rag_text_analyzer", "fields": {"keyword": {"type": "keyword"}}},
                    "source": {"type": "keyword"},
                    "ordinal": {"type": "integer"},
                    "text": {"type": "text", "analyzer": "rag_text_analyzer"},
                    "metadata": {
                        "type": "object",
                        "dynamic": True,
                        "properties": {
                            "brand": {"type": "keyword"},
                            "model": {"type": "keyword"},
                            "year": {"type": "keyword"},
                            "system": {"type": "keyword"},
                            "source_type": {"type": "keyword"},
                            "document_type": {"type": "keyword"},
                            "document_version": {"type": "keyword"},
                            "publication_status": {"type": "keyword"},
                            "classification": {"type": "keyword"},
                            "acl": {"type": "keyword"},
                            "chunk_id": {"type": "keyword"},
                            "document_id": {"type": "keyword"},
                            "dataset_id": {"type": "keyword"},
                            "content_type": {"type": "keyword"},
                            "section_path": {"type": "keyword"},
                        },
                    },
                }
            },
        }

    @staticmethod
    def _opensearch_filters(filters: dict[str, str]) -> list[dict[str, Any]]:
        clauses = []
        for key, value in filters.items():
            if value:
                field = key if key in {"document_id", "dataset_id"} else f"metadata.{key}"
                clauses.append({"term": {field: value}})
        return clauses

    @staticmethod
    def _matches_filters(hit: dict[str, Any], filters: dict[str, str]) -> bool:
        metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
        for key, value in filters.items():
            if not value:
                continue
            actual = hit.get(key) if key in {"document_id", "dataset_id"} else metadata.get(key)
            if str(actual or "") != str(value):
                return False
        return True

    def _matches_scope(self, hit: dict[str, Any], data_scope: dict[str, Any] | None) -> bool:
        if not data_scope or data_scope.get("scope") == "all":
            return True
        filters = self._merged_filters(None, None, data_scope)
        return self._matches_filters(hit, filters)

    @staticmethod
    def _merged_filters(
        document_ids: list[str] | None,
        metadata: dict[str, str] | None,
        data_scope: dict[str, Any] | None,
    ) -> dict[str, str]:
        filters: dict[str, str] = {}
        if document_ids:
            filters["document_id"] = str(document_ids[0])
        for key, value in (metadata or {}).items():
            if value not in (None, ""):
                filters[str(key)] = str(value)
        if data_scope and data_scope.get("scope") != "all":
            for key, target in (
                ("dataset_id", "dataset_ids"),
                ("document_id", "document_ids"),
                ("source_type", "source_types"),
                ("brand", "brands"),
                ("system", "systems"),
            ):
                values = data_scope.get(target)
                if isinstance(values, list) and values:
                    filters.setdefault(key, str(values[0]))
        return filters

    @staticmethod
    def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _document_text(self, row: dict[str, Any]) -> str:
        artifact_path = str(row.get("artifact_path") or "")
        if not artifact_path:
            return ""
        artifact = ROOT_DIR / artifact_path
        text_dir = self._text_dir(artifact_path)
        text_path = text_dir / f"{artifact.stem}.txt"
        if text_path.exists():
            return self._compact(text_path.read_text(encoding="utf-8", errors="ignore"))
        if artifact.suffix.lower() in {".html", ".htm"} and artifact.exists():
            raw = artifact.read_text(encoding="utf-8", errors="ignore")
            raw = re.sub(r"(?is)<script.*?>.*?</script>|<style.*?>.*?</style>", " ", raw)
            return self._compact(re.sub(r"(?s)<[^>]+>", " ", html.unescape(raw)))
        return ""

    @staticmethod
    def _text_dir(artifact_path: str) -> Path:
        if "report_corpus" in artifact_path:
            return REPORT_TEXT_DIR
        if "repair_corpus" in artifact_path:
            return REPAIR_TEXT_DIR
        return PDF_TEXT_DIR

    @staticmethod
    def _dataset_id(artifact_path: str) -> str:
        if "report_corpus" in artifact_path:
            return "report-corpus"
        if "repair_corpus" in artifact_path:
            return "repair-corpus"
        return "manual-corpus"

    def _corpus_signature(self) -> str:
        paths = [
            *MANIFESTS,
            *PRECHUNKED_PDF_MANIFESTS,
            *sorted(PDF_TEXT_DIR.glob("*.txt")),
            *sorted(REPAIR_TEXT_DIR.glob("*.txt")),
            *sorted(REPORT_TEXT_DIR.glob("*.txt")),
        ]
        signature = tuple((str(path.relative_to(ROOT_DIR)), path.stat().st_mtime_ns, path.stat().st_size) for path in paths if path.exists())
        return hashlib.sha256(repr(signature).encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _compact(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _chunk_size() -> int:
        return int(os.getenv("RAG_CHUNK_SIZE", "1024"))

    @staticmethod
    def _chunk_overlap() -> int:
        return int(os.getenv("RAG_CHUNK_OVERLAP", "150"))

    @staticmethod
    def _dense_top_k() -> int:
        return int(os.getenv("RAG_DENSE_TOP_K", str(DEFAULT_DENSE_TOP_K)))

    @staticmethod
    def _sparse_top_k() -> int:
        return int(os.getenv("RAG_SPARSE_TOP_K", str(DEFAULT_SPARSE_TOP_K)))

    @staticmethod
    def _rerank_top_n() -> int:
        return int(os.getenv("RAG_RERANK_TOP_N", str(DEFAULT_RERANK_TOP_N)))

    @staticmethod
    def _embedding_model_name() -> str:
        provider = RagService._embedding_provider()
        default = "BAAI/bge-small-zh-v1.5" if provider == "huggingface" else "text-embedding-3-large"
        return os.getenv("RAG_EMBEDDING_MODEL", default).strip()

    @staticmethod
    def _embedding_provider() -> str:
        return os.getenv("RAG_EMBEDDING_PROVIDER", "huggingface").strip().lower()

    @staticmethod
    def _embedding_api_base() -> str | None:
        value = os.getenv("RAG_EMBEDDING_API_BASE") or os.getenv("OPENAI_BASE_URL") or os.getenv("ARK_BASE_URL")
        return value.strip().rstrip("/") if value else None

    @staticmethod
    def _embedding_dim() -> int:
        return int(os.getenv("RAG_EMBEDDING_DIM", "512"))

    @staticmethod
    def _embed_batch_size() -> int:
        return int(os.getenv("RAG_EMBED_BATCH_SIZE", "32"))

    @staticmethod
    def _prechunked_max_documents() -> int:
        return int(os.getenv("RAG_PRECHUNKED_PDF_MAX_DOCUMENTS", "334"))

    @staticmethod
    def _embed_device() -> str | None:
        value = os.getenv("RAG_EMBED_DEVICE", "").strip()
        return value or None

    @staticmethod
    def _rerank_model_name() -> str:
        return os.getenv("RAG_RERANK_MODEL", "BAAI/bge-reranker-base").strip()

    @staticmethod
    def _rerank_fp16() -> bool:
        return os.getenv("RAG_RERANK_FP16", "true").strip().lower() in {"1", "true", "yes"}

    @staticmethod
    def _rerank_device() -> str | None:
        return os.getenv("RAG_RERANK_DEVICE", "").strip() or None

    @staticmethod
    def _rerank_max_length() -> int:
        return int(os.getenv("RAG_RERANK_MAX_LENGTH", "512"))

    @staticmethod
    def _milvus_uri() -> str:
        return os.getenv("RAG_MILVUS_URI", "http://127.0.0.1:19530").strip()

    @staticmethod
    def _milvus_collection() -> str:
        return os.getenv("RAG_MILVUS_COLLECTION", "subjects_agent_chunks").strip()

    @staticmethod
    def _opensearch_url() -> str:
        return os.getenv("RAG_OPENSEARCH_URL", "http://127.0.0.1:9200").strip()

    @staticmethod
    def _opensearch_index() -> str:
        return os.getenv("RAG_OPENSEARCH_INDEX", "subjects-agent-chunks").strip()

    @staticmethod
    def _normalize_proxy_env() -> None:
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            value = os.getenv(key, "")
            if value.startswith("socks://"):
                os.environ[key] = "socks5://" + value[len("socks://"):]


class _CrossEncoderReranker:
    def __init__(self, model_name: str, *, use_fp16: bool, device: str | None, max_length: int) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._torch = torch
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self._device = torch.device(device)
        self._max_length = max_length
        self._tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
        self._model = AutoModelForSequenceClassification.from_pretrained(model_name, trust_remote_code=True)
        self._model.to(self._device)
        if use_fp16 and self._device.type == "cuda":
            self._model.half()
        self._model.eval()

    def compute_score(self, pairs: list[list[str]], normalize: bool = True) -> list[float]:
        scores: list[float] = []
        with self._torch.inference_mode():
            for start in range(0, len(pairs), 8):
                batch = pairs[start : start + 8]
                encoded = self._tokenizer(
                    [pair[0] for pair in batch],
                    [pair[1] for pair in batch],
                    padding=True,
                    truncation=True,
                    max_length=self._max_length,
                    return_tensors="pt",
                )
                encoded = {key: value.to(self._device) for key, value in encoded.items()}
                logits = self._model(**encoded).logits.view(-1).float()
                if normalize:
                    logits = self._torch.sigmoid(logits)
                scores.extend(float(value) for value in logits.cpu())
        return scores


rag_service = RagService()
