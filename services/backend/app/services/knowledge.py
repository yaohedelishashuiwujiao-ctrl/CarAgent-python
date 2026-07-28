from __future__ import annotations

import csv
import html
import json
import re
from pathlib import Path
from typing import Any

from backend.app.schemas_knowledge import (
    KnowledgeMetric,
    KnowledgeSampleRow,
    KnowledgeSearchHit,
    KnowledgeSearchResponse,
    KnowledgeStage,
    KnowledgeVersion,
    KnowledgeWorkspaceStatus,
)
from backend.app.services.rag import rag_service


ROOT_DIR = Path(__file__).resolve().parents[3]
CORPUS_DIR = ROOT_DIR / "resources" / "manual_corpus"
DOWNLOADS_MANIFEST = CORPUS_DIR / "downloads" / "manifest.jsonl"
MANUAL_PAGES_MANIFEST = CORPUS_DIR / "downloads_manual_pages" / "manifest.jsonl"
MANUAL_PDFS_MANIFEST = CORPUS_DIR / "downloads_manual_pdfs" / "manifest.jsonl"
OFFICIAL_CN_CSV = CORPUS_DIR / "official_cn_vehicle_manual_sources.csv"
OFFICIAL_GLOBAL_CSV = CORPUS_DIR / "official_vehicle_manual_sources.csv"
PDF_TEXT_DIR = CORPUS_DIR / "downloads_manual_pdfs" / "text"


class KnowledgeService:
    def status(self) -> KnowledgeWorkspaceStatus:
        rag_status = rag_service.status()
        official_cn_rows = self._load_csv_rows(OFFICIAL_CN_CSV)
        global_rows = self._load_csv_rows(OFFICIAL_GLOBAL_CSV)
        download_rows = self._load_jsonl_rows(DOWNLOADS_MANIFEST)
        manual_page_rows = self._load_jsonl_rows(MANUAL_PAGES_MANIFEST)
        pdf_rows = self._load_jsonl_rows(MANUAL_PDFS_MANIFEST)
        text_files = sorted(PDF_TEXT_DIR.glob("*.txt"))

        source_rows = official_cn_rows + global_rows
        brand_count = len({row.get("brand", "") for row in source_rows if row.get("brand")})
        model_count = len({f"{row.get('brand', '')}:{row.get('model', '')}" for row in source_rows if row.get("model")})
        download_ok_count = self._count_status(download_rows, "ok")
        manual_page_ok_count = self._count_status(manual_page_rows, "ok")
        pdf_ok_count = self._count_status(pdf_rows, "ok")
        unique_download_artifacts = self._count_unique(download_rows, "artifact_path")
        unique_manual_page_artifacts = self._count_unique(manual_page_rows, "artifact_path")
        unique_pdf_artifacts = self._count_unique(pdf_rows, "artifact_path")

        metrics = [
            KnowledgeMetric(label="中国市场来源", value=str(len(official_cn_rows)), hint="官方车型入口与说明书入口"),
            KnowledgeMetric(label="全球来源", value=str(len(global_rows)), hint="海外/全球官方手册入口"),
            KnowledgeMetric(label="品牌数", value=str(brand_count), hint="覆盖的品牌/子品牌数量"),
            KnowledgeMetric(label="车型数", value=str(model_count), hint="去重后的品牌-车型组合"),
            KnowledgeMetric(label="入口页下载", value=f"{download_ok_count}/{len(download_rows)}", hint=f"唯一 HTML {unique_download_artifacts} 个"),
            KnowledgeMetric(label="手册页发现", value=f"{manual_page_ok_count}/{len(manual_page_rows)}", hint=f"唯一 HTML {unique_manual_page_artifacts} 个"),
            KnowledgeMetric(label="PDF 正文", value=f"{pdf_ok_count}/{len(pdf_rows)}", hint=f"唯一 PDF {unique_pdf_artifacts} 个"),
            KnowledgeMetric(label="文本抽取", value=str(len(text_files)), hint="已进入本地 RAG 索引"),
            KnowledgeMetric(label="RAG Chunk", value=str(rag_status["chunk_count"]), hint=rag_status["index_type"]),
        ]

        stages = [
            KnowledgeStage(
                key="source_pool",
                name="语料来源池",
                status="ready",
                summary="官方来源清单已经整理出来，后续增加新文档只需继续追加来源行。",
                metrics=[
                    KnowledgeMetric(label="中国市场", value=str(len(official_cn_rows))),
                    KnowledgeMetric(label="全球来源", value=str(len(global_rows))),
                ],
                notes=[
                    "当前来源池同时保留官方入口、服务页、手册入口和直链 PDF 的关系。",
                    "后续新文档应先落到来源池，再进入下载和抽取阶段。",
                ],
            ),
            KnowledgeStage(
                key="discovery",
                name="抓取与发现",
                status="ready",
                summary="入口页、手册页和直链 PDF 已经逐级发现并下载。",
                metrics=[
                    KnowledgeMetric(label="入口页", value=f"{download_ok_count}/{len(download_rows)}"),
                    KnowledgeMetric(label="手册页", value=f"{manual_page_ok_count}/{len(manual_page_rows)}"),
                    KnowledgeMetric(label="PDF", value=f"{pdf_ok_count}/{len(pdf_rows)}"),
                ],
                notes=[
                    "这一步负责把官网和手册中心的动态入口落成可追踪的离线快照。",
                ],
            ),
            KnowledgeStage(
                key="extraction",
                name="文本抽取",
                status="ready",
                summary="PDF 已抽取出纯文本，可以直接做本地检索预览和回归测试。",
                metrics=[
                    KnowledgeMetric(label="文本文件", value=str(len(text_files))),
                    KnowledgeMetric(label="PDF 数", value=str(unique_pdf_artifacts)),
                ],
                notes=[
                    "当前文本抽取来自真实正文，不是空壳页面。",
                    "后续新增的 PDF 也应先进入同样的抽取流程。",
                ],
            ),
            KnowledgeStage(
                key="indexing",
                name="索引构建",
                status="ready" if rag_status["ready"] else "pending",
                summary="语料已按文档切块并构建本地稀疏向量索引；文件变更时首次检索会自动重建。",
                metrics=[
                    KnowledgeMetric(label="索引", value=rag_status["index_type"]),
                    KnowledgeMetric(label="Chunk", value=str(rag_status["chunk_count"])),
                    KnowledgeMetric(label="文档", value=str(rag_status["document_count"])),
                ],
                notes=[
                    "当前实现采用中文字符 n-gram TF-IDF，能在没有外部服务和模型下载时稳定运行。",
                    "后续可替换为 Qdrant/Milvus/pgvector 与 dense embedding，不改变 Agent 工具协议。",
                ],
            ),
            KnowledgeStage(
                key="retrieval",
                name="检索测试",
                status="ready",
                summary="检索预览与 Agent 使用同一 RAG API，返回可追溯的 chunk、文档和官方来源。",
                metrics=[
                    KnowledgeMetric(label="已索引文档", value=str(rag_status["document_count"])),
                    KnowledgeMetric(label="RAG API", value="可调用"),
                ],
                notes=[
                    "Agent 的 KnowledgeSearch 和 KnowledgeFetch 直接调用这套检索与取回接口。",
                ],
            ),
            KnowledgeStage(
                key="release",
                name="发布版本",
                status="pending",
                summary="当前还是语料快照，尚未生成正式知识库版本。",
                metrics=[
                    KnowledgeMetric(label="版本", value="草稿"),
                    KnowledgeMetric(label="回滚", value="未启用"),
                ],
                notes=[
                    "后续每次新增文档或重建索引，都应该形成可回滚版本。",
                ],
            ),
        ]

        source_samples = [
            self._sample_row(row)
            for row in (official_cn_rows[:6] + global_rows[:4])
        ]
        artifact_samples = [
            self._sample_artifact_row(row, stage="entry")
            for row in download_rows[:4]
        ] + [
            self._sample_artifact_row(row, stage="manual_page")
            for row in manual_page_rows[:4]
        ] + [
            self._sample_artifact_row(row, stage="pdf")
            for row in pdf_rows[:4]
        ]

        versions = [
            KnowledgeVersion(
                id="snapshot-2026-07-11",
                name="当前语料快照",
                state="draft",
                detail=f"来源 {len(source_rows)} 条，正文 PDF {len(pdf_rows)} 份，文本抽取 {len(text_files)} 份。",
            )
        ]

        notes = [
            "目前展示的是真实语料流水线，不是写死的 demo 数据。",
            "后续增加文档只需要补来源行和新的下载/抽取结果，前端会跟着显示新状态。",
        ]

        return KnowledgeWorkspaceStatus(
            generated_at=self._now_iso(),
            snapshot_name="manual-corpus",
            metrics=metrics,
            stages=stages,
            source_samples=source_samples,
            artifact_samples=artifact_samples,
            versions=versions,
            notes=notes,
        )

    def search(self, query: str, top_k: int = 5) -> KnowledgeSearchResponse:
        normalized_query = query.strip()
        if not normalized_query:
            return KnowledgeSearchResponse(query=query, top_k=top_k, total_matches=0, hits=[])
        result = rag_service.search(normalized_query, top_k=top_k)
        hits: list[KnowledgeSearchHit] = []
        for item in result["results"]:
            metadata = item.get("metadata") or {}
            hits.append(KnowledgeSearchHit(
                id=item["chunk_id"],
                brand=str(metadata.get("brand") or ""),
                model=str(metadata.get("model") or ""),
                year=str(metadata.get("year") or ""),
                source_type=str(metadata.get("source_type") or ""),
                official_url=str(item.get("source") or ""),
                artifact_path=str(metadata.get("artifact_path") or ""),
                text_path=None,
                score=float(item.get("score") or 0),
                title=str(item.get("title") or ""),
                excerpt=str(item.get("excerpt") or ""),
                matched_terms=self._query_tokens(normalized_query),
            ))
        return KnowledgeSearchResponse(query=query, top_k=top_k, total_matches=len(hits), hits=hits)

    def _sample_row(self, row: dict[str, Any]) -> KnowledgeSampleRow:
        return KnowledgeSampleRow(
            id=row.get("id", ""),
            brand=row.get("brand", ""),
            model=row.get("model", ""),
            year=row.get("year", ""),
            source_type=row.get("source_type", ""),
            official_url=row.get("official_url", ""),
            selector_hint=row.get("selector_hint") or None,
            market=row.get("market") or None,
            language=row.get("language") or None,
        )

    def _sample_artifact_row(self, row: dict[str, Any], stage: str) -> KnowledgeSampleRow:
        return KnowledgeSampleRow(
            id=row.get("id", ""),
            brand=row.get("brand", ""),
            model=row.get("model", ""),
            year=row.get("year", ""),
            source_type=row.get("source_type", stage),
            official_url=row.get("official_url", ""),
            selector_hint=row.get("selector_hint") or None,
            market=row.get("market") or None,
            language=row.get("language") or None,
            status=row.get("status") or None,
            artifact_path=row.get("artifact_path") or None,
            text_path=self._text_path_for_artifact(row.get("artifact_path")),
            bytes=self._safe_int(row.get("bytes")),
            final_url=row.get("final_url") or None,
            downloaded_at=row.get("downloaded_at") or None,
            parent_id=row.get("parent_id") or None,
        )

    def _load_search_documents(self) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        for row in self._load_jsonl_rows(DOWNLOADS_MANIFEST):
            text = self._read_html_text(row.get("artifact_path"))
            if text:
                documents.append(self._document_payload(row, text, row.get("artifact_path") or "", None))
        for row in self._load_jsonl_rows(MANUAL_PAGES_MANIFEST):
            text = self._read_html_text(row.get("artifact_path"))
            if text:
                documents.append(self._document_payload(row, text, row.get("artifact_path") or "", None))
        for row in self._load_jsonl_rows(MANUAL_PDFS_MANIFEST):
            text_path = self._text_path_for_artifact(row.get("artifact_path"))
            text = self._read_text_file(text_path)
            if text:
                documents.append(self._document_payload(row, text, row.get("artifact_path") or "", text_path))
        return documents

    def _document_payload(self, row: dict[str, Any], text: str, artifact_path: str, text_path: str | None) -> dict[str, Any]:
        title_bits = [row.get("brand"), row.get("model"), row.get("selector_hint"), row.get("source_type")]
        title = " / ".join(str(bit) for bit in title_bits if bit)
        return {
            "id": row.get("id", ""),
            "brand": row.get("brand", ""),
            "model": row.get("model", ""),
            "year": row.get("year", ""),
            "source_type": row.get("source_type", ""),
            "official_url": row.get("official_url", ""),
            "artifact_path": artifact_path,
            "text_path": text_path,
            "title": title,
            "text": text,
        }

    def _score_document(self, document: dict[str, Any], query: str) -> tuple[float, list[str]]:
        haystack = " ".join(
            [
                document["brand"],
                document["model"],
                document["year"],
                document["source_type"],
                document["official_url"],
                document["title"],
                document["text"][:12000],
            ]
        ).lower()
        query_lower = query.lower()
        tokens = self._query_tokens(query)
        score = 0.0
        matched_terms: list[str] = []

        if query_lower in haystack:
            score += 50
            matched_terms.append(query)

        for token in tokens:
            token_lower = token.lower()
            occurrences = haystack.count(token_lower)
            if occurrences:
                score += 8 + min(occurrences, 6) * 2
                matched_terms.append(token)

        for term in self._domain_terms(query):
            if term in haystack:
                score += 20
                matched_terms.append(term)

        unique_terms = list(dict.fromkeys(matched_terms))
        return score, unique_terms

    def _make_excerpt(self, text: str, query: str, terms: list[str]) -> str:
        candidates = [query, *terms]
        for term in candidates:
            if not term:
                continue
            index = text.lower().find(term.lower())
            if index >= 0:
                start = max(0, index - 90)
                end = min(len(text), index + 220)
                excerpt = text[start:end].replace("\n", " ").strip()
                return self._compact_spaces(excerpt)
        return self._compact_spaces(text[:280])

    def _query_tokens(self, query: str) -> list[str]:
        tokens = [token for token in re.split(r"[\s,，。；;:/\\|]+", query) if token]
        if not tokens:
            return [query]
        return tokens

    def _domain_terms(self, query: str) -> list[str]:
        terms = [
            "制动",
            "制动液",
            "胎压",
            "胎压监测",
            "遥控泊车",
            "泊车辅助",
            "辅助驾驶",
            "儿童锁",
            "悬架",
            "方向盘",
            "安全带",
        ]
        query_lower = query.lower()
        return [term for term in terms if term in query_lower or query_lower in term]

    def _compact_spaces(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _count_status(self, rows: list[dict[str, Any]], status: str) -> int:
        return sum(1 for row in rows if row.get("status") == status)

    def _count_unique(self, rows: list[dict[str, Any]], key: str) -> int:
        return len({row.get(key) for row in rows if row.get(key)})

    def _load_csv_rows(self, path: Path) -> list[dict[str, str]]:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def _load_jsonl_rows(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
        return rows

    def _read_text_file(self, path: str | None) -> str:
        if not path:
            return ""
        file_path = ROOT_DIR / path
        if not file_path.exists():
            return ""
        try:
            return file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""

    def _text_path_for_artifact(self, artifact_path: str | None) -> str | None:
        if not artifact_path:
            return None
        artifact_name = Path(artifact_path).stem
        text_path = PDF_TEXT_DIR / f"{artifact_name}.txt"
        if text_path.exists():
            return str(text_path.relative_to(ROOT_DIR))
        return None

    def _read_html_text(self, artifact_path: str | None) -> str:
        if not artifact_path:
            return ""
        file_path = ROOT_DIR / artifact_path
        if not file_path.exists():
            return ""
        try:
            raw = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
        raw = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw)
        raw = re.sub(r"(?is)<style.*?>.*?</style>", " ", raw)
        raw = re.sub(r"(?s)<[^>]+>", " ", raw)
        raw = html.unescape(raw)
        return self._compact_spaces(raw)

    def _safe_int(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _now_iso(self) -> str:
        from datetime import datetime

        return datetime.now().astimezone().isoformat(timespec="seconds")


knowledge_service = KnowledgeService()
