#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from openai import OpenAI


ROOT = Path(__file__).resolve().parents[1]


def parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    return json.loads(text)


def load_chunks(manifest: Path, wanted: set[str]) -> dict[str, dict[str, Any]]:
    chunks = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("status") not in {"ok", "cached"}:
            continue
        cache = Path(str(row["cache_path"]))
        cache = cache if cache.is_absolute() else ROOT / cache
        payload = json.loads(cache.read_text(encoding="utf-8"))
        for chunk in payload.get("chunks") or []:
            chunk_id = str(chunk["chunk_id"])
            if chunk_id in wanted:
                chunks[chunk_id] = chunk
    return chunks


def call_json(client: OpenAI, model: str, *, instructions: str, payload: Any) -> dict[str, Any]:
    response = client.responses.create(model=model, instructions=instructions, input=json.dumps(payload, ensure_ascii=False))
    return parse_json(str(response.output_text or ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and judge answers over retrieval results.")
    parser.add_argument("--retrieval", type=Path, required=True)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--model", default=os.getenv("ARK_MODEL") or os.getenv("OPENAI_MODEL") or "")
    args = parser.parse_args()
    resolve = lambda path: path if path.is_absolute() else ROOT / path
    api_key = os.getenv("ARK_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("ARK_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    if not api_key or not args.model:
        raise SystemExit("ARK_API_KEY/OPENAI_API_KEY and --model are required")
    retrieval = json.loads(resolve(args.retrieval).read_text(encoding="utf-8"))
    suite = json.loads(resolve(args.suite).read_text(encoding="utf-8"))
    suite_cases = {case["query"]: case for case in suite["cases"]}
    wanted = {
        chunk_id
        for case in retrieval["cases"]
        for chunk_id in case["returned_chunk_ids"][: args.top_k]
    }
    chunks = load_chunks(resolve(args.manifest), wanted)
    prepared = []
    for index, retrieved in enumerate(retrieval["cases"]):
        source = suite_cases[retrieved["query"]]
        contexts = [
            {"chunk_id": chunk_id, "text": chunks[chunk_id]["text"]}
            for chunk_id in retrieved["returned_chunk_ids"][: args.top_k]
            if chunk_id in chunks
        ]
        prepared.append(
            {
                "item_id": index,
                "query": retrieved["query"],
                "reference_answer": source["reference_answer"],
                "contexts": contexts,
            }
        )
    client = OpenAI(api_key=api_key, base_url=base_url)
    generation_instructions = """Answer each automotive research question using only its supplied contexts.
If the contexts do not support an answer, answer exactly "INSUFFICIENT_EVIDENCE". Otherwise be concise and include
the supporting chunk ids. Return JSON only as {"items":[{"item_id":0,"answer":"...","cited_chunk_ids":["..."]}]}.
Do not use the reference answer because it is not included in this generation input."""
    generated: dict[int, dict[str, Any]] = {}
    for start in range(0, len(prepared), args.batch_size):
        batch = [{key: value for key, value in item.items() if key != "reference_answer"} for item in prepared[start : start + args.batch_size]]
        result = call_json(client, args.model, instructions=generation_instructions, payload=batch)
        for item in result.get("items") or []:
            generated[int(item["item_id"])] = item
        print(f"answered {min(start + args.batch_size, len(prepared))}/{len(prepared)}", flush=True)

    judge_instructions = """Judge RAG answers. Compare answer correctness to the reference answer, and faithfulness only
to the retrieved contexts. Score correctness, faithfulness, and relevance from 0.0 to 1.0. citation_support is 1.0 only
when the cited contexts actually support the answer; use 0.0 when citations are absent or unsupported. An explicit
INSUFFICIENT_EVIDENCE is faithful but not correct when the reference has an answer. Return JSON only as
{"items":[{"item_id":0,"correctness":0.0,"faithfulness":0.0,"relevance":0.0,"citation_support":0.0,"reason":"..."}]}.
Do not reward wording overlap by itself."""
    judged: dict[int, dict[str, Any]] = {}
    for start in range(0, len(prepared), args.batch_size):
        batch = []
        for item in prepared[start : start + args.batch_size]:
            answer = generated.get(item["item_id"], {})
            batch.append({**item, "generated_answer": answer.get("answer", ""), "cited_chunk_ids": answer.get("cited_chunk_ids") or []})
        result = call_json(client, args.model, instructions=judge_instructions, payload=batch)
        for item in result.get("items") or []:
            judged[int(item["item_id"])] = item
        print(f"judged {min(start + args.batch_size, len(prepared))}/{len(prepared)}", flush=True)

    records = []
    for item in prepared:
        item_id = item["item_id"]
        answer = generated.get(item_id, {})
        judgement = judged.get(item_id, {})
        available_ids = {context["chunk_id"] for context in item["contexts"]}
        cited_ids = [str(value) for value in (answer.get("cited_chunk_ids") or [])]
        citation_valid = bool(cited_ids) and all(value in available_ids for value in cited_ids)
        records.append(
            {
                "item_id": item_id,
                "query": item["query"],
                "reference_answer": item["reference_answer"],
                "answer": str(answer.get("answer") or ""),
                "cited_chunk_ids": cited_ids,
                "citation_valid": citation_valid,
                "judgement": judgement,
            }
        )
    mean = lambda key: round(sum(float(record["judgement"].get(key) or 0) for record in records) / max(1, len(records)), 4)
    metrics = {
        "answer_correctness": mean("correctness"),
        "faithfulness": mean("faithfulness"),
        "answer_relevance": mean("relevance"),
        "citation_support": mean("citation_support"),
        "citation_validity": round(sum(record["citation_valid"] for record in records) / max(1, len(records)), 4),
        "abstention_rate": round(sum(record["answer"] == "INSUFFICIENT_EVIDENCE" for record in records) / max(1, len(records)), 4),
    }
    output = resolve(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "schema": "subjects_rag_answer_eval/v1",
                "suite_id": suite["suite_id"],
                "label_quality": suite.get("label_quality"),
                "generator_model": args.model,
                "judge_model": args.model,
                "retrieval_top_k": args.top_k,
                "case_count": len(records),
                "metrics": metrics,
                "cases": records,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
