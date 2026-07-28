#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import json
import re
from pathlib import Path
from urllib.parse import urljoin


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "resources" / "manual_corpus" / "downloads_manual_pages" / "manifest.jsonl"
DEFAULT_OUT = ROOT / "resources" / "manual_corpus" / "discovered_manual_pdfs.csv"

HREF_RE = re.compile(r"<a\b[^>]*?href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")


def clean_label(raw: str) -> str:
    return html.unescape(TAG_RE.sub("", raw)).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover direct PDF manual links from downloaded manual pages.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    seen: set[str] = set()
    out_rows: list[dict[str, str]] = []
    for row in rows:
        if row.get("status") != "ok" or not row.get("artifact_path"):
            continue
        artifact_path = ROOT / str(row["artifact_path"])
        if artifact_path.suffix.lower() not in {".html", ".htm"}:
            continue
        text = artifact_path.read_text(encoding="utf-8", errors="ignore")
        base_url = str(row.get("final_url") or row.get("official_url") or "")
        for match in HREF_RE.finditer(text):
            href = html.unescape(match.group(1)).strip()
            label = clean_label(match.group(2))
            if ".pdf" not in href.lower() and ".pdf" not in label.lower():
                continue
            url = urljoin(base_url, href)
            if not url.lower().endswith(".pdf"):
                continue
            if url in seen:
                continue
            seen.add(url)
            out_rows.append(
                {
                    "id": f"{row['id']}-pdf-{len(out_rows) + 1}",
                    "brand": str(row.get("brand") or ""),
                    "model": str(row.get("model") or ""),
                    "year": str(row.get("year") or ""),
                    "market": str(row.get("market") or ""),
                    "language": str(row.get("language") or ""),
                    "source_type": "manual_pdf",
                    "official_url": url,
                    "selector_hint": label or href,
                    "parent_id": str(row["id"]),
                }
            )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "id",
        "brand",
        "model",
        "year",
        "market",
        "language",
        "source_type",
        "official_url",
        "selector_hint",
        "parent_id",
    ]
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"discovered={len(out_rows)} out={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
