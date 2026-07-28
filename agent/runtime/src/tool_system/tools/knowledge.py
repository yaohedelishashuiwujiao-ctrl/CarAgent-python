from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolOutcomeStatus, ToolResult
from ..preflight import PreflightDecision
from ..registry import ToolCapability, ToolDependencies, ToolExecutionPolicy, ToolSpec


DEFAULT_TOP_K = 8
MAX_TOP_K = 20


def _rag_provider() -> str:
    return os.getenv("RAG_PROVIDER", "platform").strip().lower()


def _uses_platform_agentic_rag(provider: str) -> bool:
    return provider in {"platform", "llamaindex", "llamaindex-agentic-rag"}


def _platform_rag_base_url() -> str:
    return os.getenv("RAG_PLATFORM_BASE_URL", "http://127.0.0.1:8000").strip().rstrip("/")


def _platform_rag_ready() -> tuple[bool, dict[str, Any]]:
    if _platform_rag_base_url():
        return True, {}
    return False, {
        "error": "LlamaIndex Agentic RAG is not configured",
        "provider": "llamaindex-agentic-rag",
        "missing": ["RAG_PLATFORM_BASE_URL"],
    }


def _platform_json_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    context: ToolContext | None = None,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "subjects-agent/0.1"}
    api_key = os.getenv("RAG_PLATFORM_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if context and context.runtime_authorization:
        headers["Authorization"] = context.runtime_authorization
    req = urllib.request.Request(_platform_rag_base_url() + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read(2_000_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        raise ToolInputError(f"Platform RAG HTTP {exc.code}: {body[:1000]}") from exc
    except urllib.error.URLError as exc:
        raise ToolInputError(f"Platform RAG request failed: {exc.reason}") from exc
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        raise ToolInputError(f"Platform RAG returned non-JSON response: {raw[:1000]}") from exc
    if not isinstance(parsed, dict):
        raise ToolInputError("Platform RAG returned an unexpected non-object response")
    return parsed


def _first_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("chunks", "records", "results", "hits", "documents", "data"):
            nested = value.get(key)
            found = _first_list(nested)
            if found:
                return found
    return []


def _hits_with_parent_metadata(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return [item for item in _first_list(payload) if isinstance(item, dict)]

    parent_doc = data.get("doc") if isinstance(data.get("doc"), dict) else {}
    chunks = data.get("chunks")
    if isinstance(chunks, list):
        enriched: list[dict[str, Any]] = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            item = dict(chunk)
            if parent_doc.get("dataset_id"):
                item.setdefault("dataset_id", parent_doc.get("dataset_id"))
            if parent_doc.get("name") or parent_doc.get("location"):
                item.setdefault("document_name", parent_doc.get("name") or parent_doc.get("location"))
                item.setdefault("source", parent_doc.get("location") or parent_doc.get("name"))
            enriched.append(item)
        return enriched
    return [item for item in _first_list(data) if isinstance(item, dict)]


def _text_from_hit(hit: dict[str, Any]) -> str:
    for key in ("content", "text", "chunk", "snippet", "excerpt", "summary"):
        value = hit.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _title_from_hit(hit: dict[str, Any]) -> str:
    document = hit.get("document")
    if isinstance(document, dict):
        for key in ("name", "title", "filename"):
            value = document.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("document_name", "doc_name", "docnm_kwd", "title", "filename", "name"):
        value = hit.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Untitled document"


def _source_from_hit(hit: dict[str, Any]) -> str:
    for key in ("source", "url", "path", "document_url", "location"):
        value = hit.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    document = hit.get("document")
    if isinstance(document, dict):
        for key in ("source", "url", "path", "location"):
            value = document.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _id_from_hit(hit: dict[str, Any]) -> str:
    for key in ("chunk_id", "id", "chunkId"):
        value = hit.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _document_id_from_hit(hit: dict[str, Any]) -> str:
    for key in ("document_id", "doc_id", "documentId"):
        value = hit.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    document = hit.get("document")
    if isinstance(document, dict):
        for key in ("id", "document_id", "doc_id"):
            value = document.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _dataset_id_from_hit(hit: dict[str, Any]) -> str:
    for key in ("dataset_id", "datasetId", "knowledgebase_id", "kb_id"):
        value = hit.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    document = hit.get("document") or hit.get("doc")
    if isinstance(document, dict):
        for key in ("dataset_id", "datasetId", "knowledgebase_id", "kb_id"):
            value = document.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _page_from_hit(hit: dict[str, Any]) -> int | None:
    for key in ("page", "page_number", "pageNum", "position"):
        value = hit.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _score_from_hit(hit: dict[str, Any]) -> float | None:
    for key in ("score", "similarity", "rank_score"):
        value = hit.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                pass
    return None


def _normalize_hits(payload: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    raw_hits = _hits_with_parent_metadata(payload)
    normalized: list[dict[str, Any]] = []
    for raw in raw_hits:
        if not isinstance(raw, dict):
            continue
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        merged = {**metadata, **raw}
        text = _text_from_hit(merged)
        if not text:
            continue
        item = {
            "chunk_id": _id_from_hit(merged),
            "document_id": _document_id_from_hit(merged),
            "dataset_id": _dataset_id_from_hit(merged),
            "title": _title_from_hit(merged),
            "source": _source_from_hit(merged),
            "page": _page_from_hit(merged),
            "score": _score_from_hit(merged),
            "excerpt": text[:1500],
        }
        normalized.append({key: value for key, value in item.items() if value not in (None, "")})
        if len(normalized) >= limit:
            break
    return normalized


def _string_list(value: Any, *, max_items: int = 20) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        raw = [str(part).strip() for part in value]
    else:
        raise ToolInputError("expected a string or list of strings")
    return [part for part in raw if part][:max_items]


_GENERIC_ENTITY_TOKENS = {
    "mpv", "suv", "ev", "phev", "hev", "awd", "fwd", "rwd", "cdc", "ccd",
    "abs", "esp", "adas", "noa", "acc", "pdf", "ppt", "pptx",
}


def _query_entity_anchors(query: str) -> list[str]:
    """Extract conservative entity/id anchors without pretending to do NER.

    The goal is to catch obvious out-of-corpus matches (BMW/G15/8系/X9), not
    to reject generic topical retrieval. Pure years and common engineering
    abbreviations are intentionally ignored.
    """
    anchors: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{1,}", query or ""):
        lowered = token.lower()
        if lowered in _GENERIC_ENTITY_TOKENS or re.fullmatch(r"20\d{2}", lowered):
            continue
        anchors.append(lowered)
    anchors.extend(re.sub(r"\s+", "", item) for item in re.findall(r"\d{1,2}\s*系", query or ""))
    return list(dict.fromkeys(anchors))[:8]


def _result_matches_entity_anchor(result: dict[str, Any], anchors: list[str]) -> bool:
    if not anchors:
        return True
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    primary_haystack = " ".join(
        str(value)
        for value in (
            result.get("title"), result.get("source"),
            metadata.get("brand"), metadata.get("model"), metadata.get("year"),
        )
        if value not in (None, "")
    ).lower().replace(" ", "")
    normalized_anchors = [anchor.replace(" ", "") for anchor in anchors]
    if any(anchor in primary_haystack for anchor in normalized_anchors):
        return True
    content_haystack = str(result.get("excerpt") or "").lower().replace(" ", "")
    # A single model/id-shaped string can occur incidentally in a long chunk.
    # Require corroboration from two anchors when only body text matches.
    return sum(anchor in content_haystack for anchor in normalized_anchors) >= 2


class KnowledgeSearchTool:
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="KnowledgeSearch",
            description=(
                "Search the configured private knowledge base for PDF/PPT/Word reports, OCR text, "
                "tables, and other indexed materials. Use this before web search when the user asks "
                "about internal reports, local documents, market research, product facts, or evidence "
                "that may exist in the organization's files. Return enough cited snippets, then synthesize."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string", "description": "Natural-language search query."},
                    "top_k": {"type": "integer", "description": f"Number of chunks to return, 1-{MAX_TOP_K}. Defaults to {DEFAULT_TOP_K}."},
                    "dataset_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional dataset ids for the LlamaIndex Agentic RAG index.",
                    },
                    "document_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional document ids to narrow retrieval.",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional exact metadata filters for the platform RAG provider, such as {\"brand\": \"蔚来\", \"model\": \"ET5\", \"year\": \"2025\"}.",
                    },
                    "metadata_condition": {
                        "type": "object",
                        "description": "Reserved for provider-native metadata filters.",
                    },
                    "keyword": {"type": "boolean", "description": "Reserved compatibility flag; hybrid retrieval always includes BM25."},
                    "use_kg": {"type": "boolean", "description": "Reserved compatibility flag; not used by the LlamaIndex provider."},
                    "toc_enhance": {"type": "boolean", "description": "Reserved compatibility flag; structure metadata is indexed during ingestion."},
                },
                "required": ["query"],
            },
            is_read_only=True,
            strict=True,
            max_result_size_chars=60_000,
            capability=ToolCapability(
                namespace="data.document",
                actions=("search", "retrieve"),
                entity_types=("document", "document_chunk"),
                input_modes=("natural_language_query", "metadata_filter"),
                output_modes=("ranked_chunks", "evidence"),
                limitations=(
                "Search quality depends on Milvus/OpenSearch index coverage, parsing quality, ACL metadata, and freshness.",
                "An empty result does not establish that the real-world fact is false.",
                ),
                positive_examples=("Find governed internal reports or manual passages relevant to a question.",),
                negative_examples=("Do not use as a substitute for structured numeric data when governed SQL covers the fact.",),
            ),
            execution=ToolExecutionPolicy(
                timeout_s=30,
                retryable_outcomes=(ToolOutcomeStatus.TRANSIENT_FAILURE.value,),
                max_attempts=2,
                concurrency_pool="knowledge",
                supports_parallel=True,
                cache_policy="request",
            ),
            dependencies=ToolDependencies(
                services=("llamaindex_agentic_rag", "milvus", "opensearch", "bge_m3", "bge_reranker"),
                health_probe="knowledge_provider_health",
                coverage_probe="knowledge_index_coverage",
            ),
            preflight_checks=("tool_authorized", "knowledge_provider_healthy", "knowledge_scope"),
        )

    def run(self, tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
        provider = _rag_provider()
        if _uses_platform_agentic_rag(provider):
            query = str(tool_input.get("query") or "").strip()
            if not query:
                raise ToolInputError("query must be a non-empty string")
            top_k = max(1, min(int(tool_input.get("top_k") or DEFAULT_TOP_K), MAX_TOP_K))
            ready, error = _platform_rag_ready()
            if not ready:
                return ToolResult(name="KnowledgeSearch", output=error, is_error=True)
            document_ids = _string_list(tool_input.get("document_ids"))
            metadata = tool_input.get("metadata")
            if metadata is not None and not isinstance(metadata, dict):
                raise ToolInputError("metadata must be an object")
            response = _platform_json_request(
                "POST",
                "/api/rag/search",
                {"query": query, "top_k": top_k, "document_ids": document_ids, "metadata": metadata or {}},
                context=context,
            )
            results = response.get("results") if isinstance(response.get("results"), list) else []
            anchors = _query_entity_anchors(query)
            matching_results = [
                item
                for item in results
                if isinstance(item, dict) and _result_matches_entity_anchor(item, anchors)
            ]
            if anchors and results and not matching_results:
                return ToolResult(
                    name="KnowledgeSearch",
                    output={
                        "provider": "llamaindex-agentic-rag",
                        "query": query,
                        "dataset_ids": ["manual-corpus"],
                        "document_ids": document_ids,
                        "result_count": 0,
                        "results": [],
                        "coverage_boundary": (
                            "The LlamaIndex Agentic RAG index returned topical chunks but none covered the named entity/id anchors: "
                            + ", ".join(anchors)
                        ),
                        "entity_anchors": anchors,
                        "rejected_result_count": len(results),
                    },
                    outcome_status=ToolOutcomeStatus.DATA_COVERAGE_INSUFFICIENT,
                    reason_code="KNOWLEDGE_ENTITY_COVERAGE_INSUFFICIENT",
                )
            if anchors:
                results = matching_results
            return ToolResult(
                name="KnowledgeSearch",
                output={
                    "provider": "llamaindex-agentic-rag",
                    "query": query,
                    "dataset_ids": ["manual-corpus"],
                    "document_ids": document_ids,
                    "result_count": len(results),
                    "results": results,
                },
                outcome_status=ToolOutcomeStatus.SUCCESS if results else ToolOutcomeStatus.NO_DATA,
                reason_code=None if results else "KNOWLEDGE_NO_RESULTS",
            )
        return ToolResult(
            name="KnowledgeSearch",
            output={"error": f"unsupported RAG_PROVIDER: {provider}", "supported": ["llamaindex-agentic-rag"]},
            is_error=True,
        )

    def preflight(self, tool_input: dict[str, Any], context: ToolContext) -> PreflightDecision:
        provider = _rag_provider()
        if _uses_platform_agentic_rag(provider):
            ready, error = _platform_rag_ready()
        else:
            return PreflightDecision.reject(
                "KNOWLEDGE_PROVIDER_UNSUPPORTED",
                f"Unsupported RAG provider: {provider}",
                disable_tool_for_run=True,
            )
        if not ready:
            return PreflightDecision.reject(
                "KNOWLEDGE_DEPENDENCY_UNAVAILABLE",
                str(error.get("error") if isinstance(error, dict) else error),
                disable_tool_for_run=True,
                diagnostics=error if isinstance(error, dict) else {},
            )
        return PreflightDecision.allow("KNOWLEDGE_PROVIDER_CONFIGURED")


class KnowledgeFetchTool:
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="KnowledgeFetch",
            description=(
                "Fetch details for a specific knowledge-base chunk or document after KnowledgeSearch. "
                "Use it only when the search excerpt is relevant but too short to answer with confidence."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "dataset_id": {"type": "string", "description": "Dataset id returned by KnowledgeSearch."},
                    "chunk_id": {"type": "string", "description": "Chunk id returned by KnowledgeSearch."},
                    "document_id": {"type": "string", "description": "Document id returned by KnowledgeSearch."},
                },
            },
            is_read_only=True,
            strict=True,
            max_result_size_chars=80_000,
            capability=ToolCapability(
                namespace="data.document",
                actions=("fetch",),
                entity_types=("document", "document_chunk"),
                input_modes=("document_reference", "chunk_reference"),
                output_modes=("document_chunk", "evidence"),
                limitations=("Requires a valid document or chunk reference from the configured provider.",),
                positive_examples=("Expand a relevant search hit when its excerpt is insufficient.",),
                negative_examples=("Do not call without a reference returned by KnowledgeSearch.",),
            ),
            execution=ToolExecutionPolicy(
                timeout_s=30,
                retryable_outcomes=(ToolOutcomeStatus.TRANSIENT_FAILURE.value,),
                max_attempts=2,
                concurrency_pool="knowledge",
                supports_parallel=True,
                cache_policy="request",
            ),
            dependencies=ToolDependencies(
                services=("llamaindex_agentic_rag", "milvus", "opensearch", "bge_m3", "bge_reranker"),
                health_probe="knowledge_provider_health",
            ),
            preflight_checks=("tool_authorized", "knowledge_provider_healthy", "knowledge_reference_valid"),
        )

    def run(self, tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
        provider = _rag_provider()
        if _uses_platform_agentic_rag(provider):
            chunk_id = str(tool_input.get("chunk_id") or "").strip()
            if not chunk_id:
                raise ToolInputError("chunk_id is required; pass the chunk_id returned by KnowledgeSearch")
            ready, error = _platform_rag_ready()
            if not ready:
                return ToolResult(name="KnowledgeFetch", output=error, is_error=True)
            result = _platform_json_request(
                "GET",
                f"/api/rag/chunks/{urllib.parse.quote(chunk_id, safe='')}",
                context=context,
            )
            return ToolResult(
                name="KnowledgeFetch",
                output={
                    "provider": "llamaindex-agentic-rag",
                    "dataset_id": result.get("dataset_id", "manual-corpus"),
                    "document_id": result.get("document_id"),
                    "chunk_id": chunk_id,
                    "results": [result],
                },
            )
        return ToolResult(
            name="KnowledgeFetch",
            output={"error": f"unsupported RAG_PROVIDER: {provider}", "supported": ["llamaindex-agentic-rag"]},
            is_error=True,
        )

    def preflight(self, tool_input: dict[str, Any], context: ToolContext) -> PreflightDecision:
        provider = _rag_provider()
        if _uses_platform_agentic_rag(provider):
            ready, error = _platform_rag_ready()
            if not str(tool_input.get("chunk_id") or "").strip():
                return PreflightDecision.reject(
                    "KNOWLEDGE_REFERENCE_MISSING",
                    "LlamaIndex Agentic RAG KnowledgeFetch requires a chunk_id returned by KnowledgeSearch.",
                )
        else:
            return PreflightDecision.reject(
                "KNOWLEDGE_PROVIDER_UNSUPPORTED",
                f"Unsupported RAG provider: {provider}",
                disable_tool_for_run=True,
            )
        if not ready:
            return PreflightDecision.reject(
                "KNOWLEDGE_DEPENDENCY_UNAVAILABLE",
                str(error.get("error") if isinstance(error, dict) else error),
                disable_tool_for_run=True,
                diagnostics=error if isinstance(error, dict) else {},
            )
        return PreflightDecision.allow("KNOWLEDGE_REFERENCE_READY")
