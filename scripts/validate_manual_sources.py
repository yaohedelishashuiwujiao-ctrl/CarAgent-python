#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT / "resources" / "manual_corpus" / "official_vehicle_manual_sources.csv"


def normalize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(parsed.path, safe="/%")
    query = urllib.parse.quote(parsed.query, safe="=&%?/+,:;@")
    fragment = urllib.parse.quote(parsed.fragment, safe="")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, query, fragment))


def check_url(url: str, timeout: float) -> tuple[int | None, str]:
    url = normalize_url(url)
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": "Mozilla/5.0 subjects-agent-manual-seed-validator/0.1",
            "Accept": "text/html,application/pdf,*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(2048)
            return int(resp.status), ""
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.reason
    except urllib.error.URLError as exc:
        return None, str(exc.reason)
    except TimeoutError:
        return None, "timeout"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate official vehicle manual source URLs.")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help="Path to official_vehicle_manual_sources.csv")
    parser.add_argument("--max", type=int, default=0, help="Validate at most N rows. 0 means all rows.")
    parser.add_argument("--timeout", type=float, default=12.0, help="Per-request timeout in seconds.")
    parser.add_argument(
        "--source-type",
        choices=["html_manual", "manual_portal", "manual_portal_cn", "discovered_manual_link", "manual_pdf"],
        help="Filter by source_type.",
    )
    args = parser.parse_args()

    path = Path(args.csv)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    if args.source_type:
        rows = [row for row in rows if row.get("source_type") == args.source_type]
    if args.max > 0:
        rows = rows[: args.max]

    ok = 0
    for row in rows:
        status, error = check_url(row["official_url"], args.timeout)
        passed = status is not None and 200 <= status < 400
        ok += int(passed)
        suffix = "" if passed else f" {error}".rstrip()
        print(f"{'OK' if passed else 'FAIL'} {status or '-'} {row['id']} {row['official_url']}{suffix}")

    print(f"validated={len(rows)} ok={ok} failed={len(rows) - ok}")
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
