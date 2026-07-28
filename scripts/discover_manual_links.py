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
DEFAULT_MANIFEST = ROOT / "resources" / "manual_corpus" / "downloads" / "manifest.jsonl"
DEFAULT_OUT = ROOT / "resources" / "manual_corpus" / "discovered_manual_links.csv"

MANUAL_PATTERNS = (
    "用户手册",
    "车主手册",
    "随车手册",
    "使用手册",
    "说明书",
    "车型手册",
    "manual",
    "Manual",
    "guide/manual",
)

HREF_RE = re.compile(r"<a\b[^>]*?href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")


def clean_label(raw: str) -> str:
    return html.unescape(TAG_RE.sub("", raw)).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover manual-like links from downloaded official pages.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    seen: set[tuple[str, str]] = set()
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
            combined = href + " " + label
            if not any(pattern in combined for pattern in MANUAL_PATTERNS):
                continue
            url = urljoin(base_url, href)
            key = (str(row["id"]), url)
            if key in seen:
                continue
            seen.add(key)
            out_rows.append(
                {
                    "id": f"{row['id']}-manual-{len(out_rows) + 1}",
                    "brand": str(row.get("brand") or ""),
                    "model": str(row.get("model") or ""),
                    "year": str(row.get("year") or ""),
                    "market": str(row.get("market") or ""),
                    "language": str(row.get("language") or ""),
                    "source_type": "discovered_manual_link",
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
