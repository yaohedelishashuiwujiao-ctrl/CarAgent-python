#!/usr/bin/env python3
"""Build a no-key, open-access automotive PDF source manifest from arXiv."""
from __future__ import annotations

import argparse
import csv
import hashlib
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


ARXIV_API = "https://export.arxiv.org/api/query"
DEFAULT_QUERIES = ("all:automotive", "all:electric+vehicle", "all:vehicle+battery", "all:vehicle+safety", "all:vehicle+chassis", "all:autonomous+driving")
NS = {"atom": "http://www.w3.org/2005/Atom"}


def fetch(query: str, start: int, size: int) -> bytes:
    params = urllib.parse.urlencode({"search_query": query, "start": start, "max_results": size, "sortBy": "submittedDate", "sortOrder": "descending"})
    request = urllib.request.Request(f"{ARXIV_API}?{params}", headers={"User-Agent": "SubjectsAgent-Platform/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Harvest open automotive arXiv PDF metadata without an API key.")
    parser.add_argument("--output", type=Path, default=Path("resources/report_corpus/arxiv_automotive_sources.csv"))
    parser.add_argument("--max", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--delay", type=float, default=3.0, help="arXiv recommends no more than one request every 3 seconds.")
    args = parser.parse_args()
    rows: dict[str, dict[str, str]] = {}
    for query in DEFAULT_QUERIES:
        for start in range(0, args.max, args.batch_size):
            root = ET.fromstring(fetch(query, start, args.batch_size))
            entries = root.findall("atom:entry", NS)
            if not entries:
                break
            for entry in entries:
                identifier = (entry.findtext("atom:id", default="", namespaces=NS).rsplit("/", 1)[-1]).strip()
                if not identifier:
                    continue
                title = " ".join((entry.findtext("atom:title", default="", namespaces=NS)).split())
                summary = " ".join((entry.findtext("atom:summary", default="", namespaces=NS)).split())
                published = entry.findtext("atom:published", default="", namespaces=NS)[:10]
                authors = "; ".join(author.findtext("atom:name", default="", namespaces=NS) for author in entry.findall("atom:author", NS))
                rows.setdefault(identifier, {
                    "id": f"arxiv-{identifier.replace('/', '-')}", "title": title, "publisher": "arXiv", "report_date": published,
                    "topic": "automotive_research", "license": "See arXiv submission licence", "source_type": "pdf",
                    "official_url": f"https://arxiv.org/pdf/{identifier}", "landing_url": f"https://arxiv.org/abs/{identifier}",
                    "authors": authors, "abstract": summary, "discovered_by": query,
                })
                if len(rows) >= args.max:
                    break
            if len(rows) >= args.max or len(entries) < args.batch_size:
                break
            time.sleep(max(3.0, args.delay))
        if len(rows) >= args.max:
            break
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = ["id", "title", "publisher", "report_date", "topic", "license", "source_type", "official_url", "landing_url", "authors", "abstract", "discovered_by"]
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows.values())
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(f"works={len(rows)} csv={args.output} sha256={digest}")


if __name__ == "__main__":
    main()
