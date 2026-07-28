from __future__ import annotations

import subprocess
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.services.pdf_chunking import (
    PdfChunkingConfig,
    _Block,
    _looks_like_table,
    _pack_blocks,
    _remove_repeated_margins,
    _split_oversized_block,
    _structure_blocks,
    chunk_pdf,
)
from backend.app.services.rag import RagService


class PdfChunkingTest(unittest.TestCase):
    def test_repeated_headers_and_page_numbers_are_removed(self) -> None:
        pages = [f"Vehicle Engineering Report\n\nBody page {number}\n\nPage {number}" for number in range(1, 5)]
        cleaned, removed = _remove_repeated_margins(pages, PdfChunkingConfig())

        self.assertGreaterEqual(removed, 8)
        self.assertTrue(all("Vehicle Engineering Report" not in page for page in cleaned))
        self.assertTrue(all("Page " not in page for page in cleaned))

    def test_sections_and_table_blocks_are_preserved(self) -> None:
        pages = [
            "1 Introduction\n\nA short vehicle platform overview.\n\n"
            "1.1 Results\n\nModel   Range   Weight\nA       520     1900\nB       610     2050"
        ]
        blocks = _structure_blocks(pages)

        self.assertEqual(blocks[0].content_type, "heading")
        self.assertEqual(blocks[-1].content_type, "table")
        self.assertEqual(blocks[-1].section_path, ("1 Introduction", "1.1 Results"))

    def test_two_column_prose_is_not_mislabeled_as_table(self) -> None:
        lines = [
            "The vehicle controller receives signals.     The planner then selects a route.",
            "The first paragraph continues normally.      The second column is also prose.",
            "No numeric cells occur in this block.         This is an academic paper layout.",
        ]
        self.assertFalse(_looks_like_table(lines))

    def test_chunks_have_stable_parent_page_and_size_metadata(self) -> None:
        blocks = [
            _Block("2 Method", 2, ("2 Method",), "heading"),
            _Block("A" * 900 + ".", 2, ("2 Method",), "paragraph"),
            _Block("B" * 900 + ".", 3, ("2 Method",), "paragraph"),
        ]
        config = PdfChunkingConfig(target_chars=600, max_chars=1000, overlap_chars=100)
        chunks = _pack_blocks(blocks, document_id="doc-1", config=config)

        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(len(chunk.text) <= config.max_chars for chunk in chunks))
        self.assertTrue(all(chunk.parent_id == chunks[0].parent_id for chunk in chunks))
        self.assertEqual(chunks[0].page_start, 2)
        self.assertEqual(chunks[-1].page_end, 3)

    def test_malformed_wide_table_lines_respect_hard_limit(self) -> None:
        block = _Block("wide " + " " * 3000 + " value\nrow  1  2", 1, (), "table")
        parts = _split_oversized_block(block, 1000)

        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part.text) <= 1000 for part in parts))

    @patch("backend.app.services.pdf_chunking.subprocess.run")
    def test_chunk_pdf_reports_quality_and_ocr_signal(self, run: object) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="1 Introduction\n\nUseful body text.\fSecond page body.", stderr=""
        )
        result = chunk_pdf(Path("sample.pdf"), document_id="doc", source_sha256="abc")

        self.assertEqual(result.quality.page_count, 2)
        self.assertTrue(result.quality.needs_ocr)
        self.assertGreaterEqual(len(result.chunks), 1)

    def test_invalid_config_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            chunk_pdf(
                Path("sample.pdf"),
                document_id="doc",
                source_sha256="abc",
                config=PdfChunkingConfig(target_chars=100),
            )

    def test_rag_loads_prechunked_manifest_without_resplitting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "document.json"
            manifest = root / "manifest.jsonl"
            cache.write_text(
                json.dumps(
                    {
                        "document_id": "doc-1",
                        "parser_version": "test-v1",
                        "source": {"title": "Vehicle Test", "official_url": "https://example.test/doc.pdf"},
                        "chunks": [
                            {
                                "chunk_id": "chunk-1",
                                "parent_id": "parent-1",
                                "ordinal": 4,
                                "text": "exact prebuilt chunk",
                                "page_start": 7,
                                "page_end": 8,
                                "section_path": ["Method"],
                                "content_types": ["paragraph"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            manifest.write_text(
                json.dumps({"status": "ok", "cache_path": str(cache)}) + "\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"RAG_PRECHUNKED_MANIFESTS": str(manifest)}):
                chunks = RagService()._load_prechunked_manifest(manifest)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].chunk_id, "chunk-1")
        self.assertEqual(chunks[0].text, "exact prebuilt chunk")
        self.assertEqual(chunks[0].metadata["page_start"], "7")

    def test_retrieval_evaluation_supports_exact_chunks_and_ndcg(self) -> None:
        service = RagService()
        results = [
            {"chunk_id": "wrong", "document_id": "d0"},
            {"chunk_id": "relevant", "document_id": "d1"},
        ]
        with patch.object(service, "search", return_value={"results": results}):
            report = service.evaluate(
                [
                    {
                        "query": "vehicle question",
                        "expected_document_ids": ["d1"],
                        "expected_chunk_ids": ["relevant"],
                        "relevance": {"relevant": 2},
                    }
                ]
            )

        self.assertEqual(report["metrics"]["hit_rate_at_1"], 0.0)
        self.assertEqual(report["metrics"]["hit_rate_at_3"], 1.0)
        self.assertEqual(report["metrics"]["chunk_recall_at_3"], 1.0)
        self.assertGreater(report["metrics"]["ndcg_at_3"], 0.0)
        self.assertLess(report["metrics"]["ndcg_at_3"], 1.0)


if __name__ == "__main__":
    unittest.main()
