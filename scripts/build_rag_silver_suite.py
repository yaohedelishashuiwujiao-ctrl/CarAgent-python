#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from pathlib import Path
from typing import Any

from openai import OpenAI


ROOT = Path(__file__).resolve().parents[1]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def candidates(manifest: Path, *, seed: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for row in read_jsonl(manifest):
        if row.get("status") not in {"ok", "cached"}:
            continue
        cache_path = Path(str(row["cache_path"]))
        cache_path = cache_path if cache_path.is_absolute() else ROOT / cache_path
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        source = payload.get("source") or {}
        eligible = []
        for chunk in payload.get("chunks") or []:
            text = str(chunk.get("text") or "")
            section = " / ".join(chunk.get("section_path") or [])
            types = set(chunk.get("content_types") or [])
            if not 850 <= len(text) <= 1800 or "paragraph" not in types:
                continue
            if re.search(r"\b(references|bibliography|acknowledg|appendix)\b", section, re.I):
                continue
            if len(re.findall(r"[A-Za-z]{3,}", text)) < 80:
                continue
            eligible.append(chunk)
        if eligible:
            # One case per document prevents prolific papers dominating the suite.
            chooser = random.Random(f"{seed}:{payload['document_id']}")
            chunk = chooser.choice(eligible)
            selected.append(
                {
                    "document_id": payload["document_id"],
                    "chunk_id": chunk["chunk_id"],
                    "title": source.get("title") or payload["document_id"],
                    "page_start": chunk["page_start"],
                    "page_end": chunk["page_end"],
                    "section_path": chunk.get("section_path") or [],
                    "evidence": str(chunk["text"]),
                }
            )
    random.Random(seed).shuffle(selected)
    return selected


def parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    return json.loads(text)


def generate_batch(client: OpenAI, model: str, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence = [
        {
            "item_id": index,
            "title": item["title"],
            "section": item["section_path"],
            "text": item["evidence"],
        }
        for index, item in enumerate(batch)
    ]
    instructions = """You create retrieval evaluation questions for automotive research PDFs.
For every evidence item, write exactly one natural English question and a concise reference answer.
The answer must be fully supported by that evidence only. Paraphrase: do not copy a full evidence sentence into the question.
Do not ask for the paper title, authors, section name, page number, or generic definitions. Prefer technical relationships,
conditions, measured findings, design choices, limitations, or comparisons. Make the query specific enough that a relevant
passage is distinguishable among 1000 automotive papers, but do not include the answer verbatim.
Return JSON only as {"items":[{"item_id":0,"query":"...","reference_answer":"..."}]}.
Return one item for every input item and preserve item_id."""
    response = client.responses.create(
        model=model,
        instructions=instructions,
        input=json.dumps(evidence, ensure_ascii=False),
    )
    payload = parse_json(str(response.output_text or ""))
    generated = payload.get("items")
    if not isinstance(generated, list):
        raise ValueError("model response has no items list")
    by_id = {int(item["item_id"]): item for item in generated}
    if set(by_id) != set(range(len(batch))):
        raise ValueError("model response item ids do not match the batch")
    return [by_id[index] for index in range(len(batch))]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a model-generated, evidence-grounded silver RAG suite.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260719)
    parser.add_argument("--model", default=os.getenv("ARK_MODEL") or os.getenv("OPENAI_MODEL") or "")
    args = parser.parse_args()
    api_key = os.getenv("ARK_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("ARK_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    if not api_key or not args.model:
        raise SystemExit("ARK_API_KEY/OPENAI_API_KEY and --model are required")
    manifest = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    output = args.out if args.out.is_absolute() else ROOT / args.out
    pool = candidates(manifest, seed=args.seed)[: args.count]
    if len(pool) < args.count:
        raise SystemExit(f"only {len(pool)} eligible documents for {args.count} cases")
    client = OpenAI(api_key=api_key, base_url=base_url)
    cases: list[dict[str, Any]] = []
    for start in range(0, len(pool), args.batch_size):
        batch = pool[start : start + args.batch_size]
        generated = generate_batch(client, args.model, batch)
        for source, item in zip(batch, generated, strict=True):
            query = str(item.get("query") or "").strip()
            answer = str(item.get("reference_answer") or "").strip()
            if not query or not answer:
                raise ValueError("empty generated query or answer")
            cases.append(
                {
                    "id": f"silver-{len(cases) + 1:03d}",
                    "query": query,
                    "reference_answer": answer,
                    "expected_document_ids": [source["document_id"]],
                    "expected_chunk_ids": [source["chunk_id"]],
                    "relevance": {source["chunk_id"]: 2},
                    "evidence": {
                        "page_start": source["page_start"],
                        "page_end": source["page_end"],
                        "section_path": source["section_path"],
                    },
                }
            )
        print(f"generated {len(cases)}/{len(pool)}", flush=True)
    suite = {
        "suite_id": "automotive-arxiv-silver-v1",
        "label_quality": "silver_model_generated_not_human_held_out",
        "description": "Model-generated questions grounded in one sampled evidence chunk per document. Suitable for retrieval regression and ablation, not a production accuracy claim until human review.",
        "generator_model": args.model,
        "seed": args.seed,
        "cases": cases,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(suite, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "cases": len(cases)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
