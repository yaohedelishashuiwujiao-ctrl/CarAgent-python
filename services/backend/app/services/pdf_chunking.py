from __future__ import annotations

import hashlib
import math
import re
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PARSER_VERSION = "poppler-layout-structure-v3"


@dataclass(frozen=True)
class PdfChunkingConfig:
    target_chars: int = 1200
    max_chars: int = 1800
    overlap_chars: int = 180
    repeated_margin_ratio: float = 0.55
    repeated_margin_min_pages: int = 3


@dataclass(frozen=True)
class PdfQuality:
    page_count: int
    extracted_chars: int
    chars_per_page: float
    needs_ocr: bool
    removed_margin_lines: int
    parser_version: str = PARSER_VERSION


@dataclass(frozen=True)
class PdfChunk:
    chunk_id: str
    parent_id: str
    ordinal: int
    text: str
    page_start: int
    page_end: int
    section_path: tuple[str, ...]
    content_types: tuple[str, ...]


@dataclass(frozen=True)
class PdfChunkingResult:
    document_id: str
    source_sha256: str
    quality: PdfQuality
    chunks: tuple[PdfChunk, ...]
    config: PdfChunkingConfig

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "source_sha256": self.source_sha256,
            "parser_version": PARSER_VERSION,
            "quality": asdict(self.quality),
            "config": asdict(self.config),
            "chunks": [asdict(chunk) for chunk in self.chunks],
        }


@dataclass(frozen=True)
class _Block:
    text: str
    page: int
    section_path: tuple[str, ...]
    content_type: str


def chunk_pdf(
    path: Path,
    *,
    document_id: str,
    source_sha256: str,
    config: PdfChunkingConfig | None = None,
    timeout_seconds: float = 60.0,
) -> PdfChunkingResult:
    config = config or PdfChunkingConfig()
    _validate_config(config)
    pages = _extract_layout_pages(path, timeout_seconds=timeout_seconds)
    cleaned_pages, removed = _remove_repeated_margins(pages, config)
    blocks = _structure_blocks(cleaned_pages)
    chunks = _pack_blocks(blocks, document_id=document_id, config=config)
    extracted_chars = sum(len(re.sub(r"\s+", "", page)) for page in cleaned_pages)
    page_count = len(cleaned_pages)
    chars_per_page = extracted_chars / max(1, page_count)
    quality = PdfQuality(
        page_count=page_count,
        extracted_chars=extracted_chars,
        chars_per_page=round(chars_per_page, 2),
        needs_ocr=extracted_chars < 100 or chars_per_page < 80,
        removed_margin_lines=removed,
    )
    return PdfChunkingResult(document_id, source_sha256, quality, tuple(chunks), config)


def _extract_layout_pages(path: Path, *, timeout_seconds: float) -> list[str]:
    completed = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"],
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "pdftotext failed"
        raise ValueError(message)
    pages = [page.replace("\r\n", "\n").strip("\n") for page in completed.stdout.split("\f")]
    pages = [page for page in pages if page.strip()]
    if not pages:
        raise ValueError("PDF contains no extractable text; OCR is required")
    return pages


def _remove_repeated_margins(pages: list[str], config: PdfChunkingConfig) -> tuple[list[str], int]:
    candidates: Counter[str] = Counter()
    per_page: list[tuple[list[str], set[str]]] = []
    for page in pages:
        lines = page.splitlines()
        nonempty = [(index, line) for index, line in enumerate(lines) if line.strip()]
        indexes = {index for index, _line in nonempty[:3]} | {index for index, _line in nonempty[-3:]}
        keys = {_margin_key(lines[index]) for index in indexes if _margin_key(lines[index])}
        candidates.update(keys)
        per_page.append((lines, keys))
    threshold = max(config.repeated_margin_min_pages, math.ceil(len(pages) * config.repeated_margin_ratio))
    repeated = {key for key, count in candidates.items() if count >= threshold}
    if not repeated:
        return pages, 0
    cleaned: list[str] = []
    removed = 0
    for lines, _keys in per_page:
        nonempty_indexes = [index for index, line in enumerate(lines) if line.strip()]
        margin_indexes = set(nonempty_indexes[:3]) | set(nonempty_indexes[-3:])
        output: list[str] = []
        for index, line in enumerate(lines):
            if index in margin_indexes and _margin_key(line) in repeated:
                removed += 1
                continue
            output.append(line)
        cleaned.append("\n".join(output))
    return cleaned, removed


def _margin_key(line: str) -> str:
    compact = re.sub(r"\s+", " ", line).strip().lower()
    compact = re.sub(r"\b\d+\b", "#", compact)
    return compact if 2 <= len(compact) <= 160 else ""


def _structure_blocks(pages: list[str]) -> list[_Block]:
    blocks: list[_Block] = []
    section_path: tuple[str, ...] = ()
    for page_number, page in enumerate(pages, start=1):
        for raw in re.split(r"\n\s*\n", page):
            lines = [line.rstrip() for line in raw.splitlines() if line.strip()]
            if not lines:
                continue
            first = re.sub(r"\s+", " ", lines[0]).strip()
            if len(lines) == 1 and _heading_level(first) is not None:
                level = _heading_level(first) or 1
                section_path = (*section_path[: level - 1], first)
                blocks.append(_Block(first, page_number, section_path, "heading"))
                continue
            content_type = "table" if _looks_like_table(lines) else "paragraph"
            if content_type == "table":
                text = "\n".join(line.strip() for line in lines)
            else:
                text = _join_paragraph(lines)
            if text:
                blocks.append(_Block(text, page_number, section_path, content_type))
    return blocks


def _heading_level(text: str) -> int | None:
    if not text or len(text) > 140 or text.endswith((".", "。", ";", "；", ",", "，")):
        return None
    match = re.match(r"^(\d+(?:\.\d+){0,4})\s+\S", text)
    if match:
        return min(5, match.group(1).count(".") + 1)
    if re.match(r"^(chapter|section)\s+\d+\b", text, re.IGNORECASE):
        return 1
    if re.match(r"^(abstract|introduction|background|method(?:ology)?|results?|discussion|conclusions?|references|摘要|引言|背景|方法|结果|讨论|结论|参考文献)\b", text, re.IGNORECASE):
        return 1
    latin = [char for char in text if char.isalpha() and char.isascii()]
    if 3 <= len(text.split()) <= 14 and latin and sum(char.isupper() for char in latin) / len(latin) > 0.8:
        return 2
    return None


def _looks_like_table(lines: list[str]) -> bool:
    if len(lines) < 3:
        return False
    columnar = sum(1 for line in lines if len(re.findall(r"\S\s{2,}\S", line)) >= 1)
    numeric = sum(1 for line in lines if len(re.findall(r"[-+]?\d+(?:\.\d+)?", line)) >= 2)
    # Layout-preserving extraction often puts two prose columns on one line.  A
    # wide gap alone therefore is not enough evidence of a table; require both
    # aligned columns and repeated numeric cells, or overwhelmingly numeric rows.
    return (
        columnar >= max(2, math.ceil(len(lines) * 0.6))
        and numeric >= max(2, math.ceil(len(lines) * 0.25))
    ) or numeric >= max(3, math.ceil(len(lines) * 0.75))


def _join_paragraph(lines: list[str]) -> str:
    text = "\n".join(line.strip() for line in lines)
    text = re.sub(r"(?<=[A-Za-z])-\n(?=[A-Za-z])", "", text)
    text = re.sub(r"(?<![。！？；.!?;:：])\n(?=\S)", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _pack_blocks(blocks: list[_Block], *, document_id: str, config: PdfChunkingConfig) -> list[PdfChunk]:
    chunks: list[PdfChunk] = []
    current: list[_Block] = []
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_chars
        if not current:
            return
        body = "\n\n".join(block.text for block in current).strip()
        if not body:
            current = []
            current_chars = 0
            return
        path = next((block.section_path for block in reversed(current) if block.section_path), ())
        parent_key = " / ".join(path) or "document-root"
        parent_id = "parent-" + hashlib.sha256(f"{document_id}:{parent_key}".encode()).hexdigest()[:20]
        ordinal = len(chunks)
        chunk_id = "chunk-" + hashlib.sha256(f"{document_id}:{ordinal}:{body}".encode()).hexdigest()[:20]
        chunks.append(
            PdfChunk(
                chunk_id=chunk_id,
                parent_id=parent_id,
                ordinal=ordinal,
                text=body,
                page_start=min(block.page for block in current),
                page_end=max(block.page for block in current),
                section_path=path,
                content_types=tuple(sorted({block.content_type for block in current})),
            )
        )
        overlap = _tail_overlap(body, config.overlap_chars)
        current = [_Block(overlap, current[-1].page, path, "overlap")] if overlap else []
        current_chars = len(overlap)

    for original in blocks:
        split_blocks = _split_oversized_block(original, config.max_chars)
        for block in split_blocks:
            if block.content_type == "heading" and current:
                flush()
                current = []
                current_chars = 0
            projected = current_chars + len(block.text) + (2 if current else 0)
            if current and projected > config.max_chars:
                if all(item.content_type == "overlap" for item in current):
                    current = []
                    current_chars = 0
                else:
                    flush()
                    if current and current_chars + len(block.text) + 2 > config.max_chars:
                        current = []
                        current_chars = 0
            current.append(block)
            current_chars += len(block.text) + (2 if len(current) > 1 else 0)
            if current_chars >= config.target_chars and block.content_type != "heading":
                flush()
    flush()
    return chunks


def _split_oversized_block(block: _Block, max_chars: int) -> list[_Block]:
    if len(block.text) <= max_chars:
        return [block]
    if block.content_type == "table":
        lines = block.text.splitlines()
        # Repeat a compact header across table fragments, but do not let a
        # malformed layout line consume the whole chunk budget.
        header = lines[0] if len(lines[0]) <= min(300, max_chars // 3) else ""
        data_lines = lines[1:] if header else lines
        parts: list[_Block] = []
        current = header
        payload_budget = max_chars - len(header) - (1 if header else 0)
        for raw_line in data_lines:
            line_parts = [raw_line[start : start + payload_budget] for start in range(0, len(raw_line), payload_budget)] or [""]
            for line in line_parts:
                separator = 1 if current else 0
                if current and len(current) + len(line) + separator > max_chars:
                    parts.append(_Block(current, block.page, block.section_path, "table"))
                    current = header
                    separator = 1 if current else 0
                current = f"{current}\n{line}" if separator else line
        if current:
            parts.append(_Block(current, block.page, block.section_path, "table"))
        return parts
    sentences = re.split(r"(?<=[。！？；.!?;])\s+", block.text)
    parts = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) + 1 > max_chars and current:
            parts.append(_Block(current, block.page, block.section_path, block.content_type))
            current = ""
        if len(sentence) > max_chars:
            for start in range(0, len(sentence), max_chars):
                piece = sentence[start:start + max_chars]
                if piece:
                    parts.append(_Block(piece, block.page, block.section_path, block.content_type))
            continue
        current = f"{current} {sentence}".strip()
    if current:
        parts.append(_Block(current, block.page, block.section_path, block.content_type))
    return parts


def _tail_overlap(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return ""
    tail = text[-limit:]
    boundary = min((index for index in (tail.find("。"), tail.find("."), tail.find("\n")) if index >= 0), default=-1)
    return tail[boundary + 1 :].strip() if boundary >= 0 else tail.strip()


def _validate_config(config: PdfChunkingConfig) -> None:
    if config.target_chars < 200:
        raise ValueError("target_chars must be at least 200")
    if config.max_chars < config.target_chars:
        raise ValueError("max_chars must be greater than or equal to target_chars")
    if not 0 <= config.overlap_chars < config.target_chars:
        raise ValueError("overlap_chars must be between 0 and target_chars")
