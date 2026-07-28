from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any

from ..token_estimation import count_messages_tokens, rough_token_count


@dataclass(frozen=True)
class ContextBudgetResult:
    messages: list[dict[str, Any]]
    before_tokens: int
    after_tokens: int
    compacted: bool
    hard_limit_reached: bool
    dropped_units: int = 0


def prepare_messages_with_budget(
    messages: list[dict[str, Any]],
    *,
    system_prompt: str = "",
    tool_schemas: list[dict[str, Any]] | None = None,
) -> ContextBudgetResult:
    """Apply automatic, tool-pair-safe compaction to a request copy."""
    window = max(8_192, int(os.getenv("CLAWD_CONTEXT_WINDOW_TOKENS", "120000")))
    reserve = max(1_024, int(os.getenv("CLAWD_CONTEXT_RESERVED_OUTPUT_TOKENS", "8000")))
    soft = int(window * float(os.getenv("CLAWD_CONTEXT_SOFT_RATIO", "0.72"))) - reserve
    hard = int(window * float(os.getenv("CLAWD_CONTEXT_HARD_RATIO", "0.90"))) - reserve
    overhead = rough_token_count(system_prompt) + rough_token_count(json.dumps(tool_schemas or [], ensure_ascii=False, default=str))
    before = count_messages_tokens(messages) + overhead
    copied = json.loads(json.dumps(messages, ensure_ascii=False, default=str))
    if before <= soft:
        return ContextBudgetResult(copied, before, before, False, False)

    compacted = _externalize_old_tool_results(copied, keep_recent=3)
    after = count_messages_tokens(compacted) + overhead
    if after <= hard:
        return ContextBudgetResult(compacted, before, after, True, False)

    units = _atomic_units(compacted)
    if len(units) <= 3:
        return ContextBudgetResult(compacted, before, after, True, True)

    keep_indices = {0, len(units) - 1}
    running = overhead + count_messages_tokens(units[0] + units[-1])
    for index in range(len(units) - 2, 0, -1):
        cost = count_messages_tokens(units[index])
        if running + cost <= max(soft, 1_024):
            keep_indices.add(index)
            running += cost

    dropped = [unit for index, unit in enumerate(units) if index not in keep_indices]
    kept: list[dict[str, Any]] = []
    snapshot_added = False
    for index, unit in enumerate(units):
        if dropped and not snapshot_added and index > 0 and index in keep_indices:
            kept.append({"role": "user", "content": _snapshot_text(dropped)})
            snapshot_added = True
        if index in keep_indices:
            kept.extend(unit)
    final_tokens = count_messages_tokens(kept) + overhead
    return ContextBudgetResult(kept, before, final_tokens, True, final_tokens > hard, len(dropped))


def _externalize_old_tool_results(messages: list[dict[str, Any]], *, keep_recent: int) -> list[dict[str, Any]]:
    result = json.loads(json.dumps(messages, ensure_ascii=False, default=str))
    positions: list[tuple[int, int | None]] = []
    for message_index, message in enumerate(result):
        if message.get("role") == "tool":
            positions.append((message_index, None))
        content = message.get("content")
        if isinstance(content, list):
            for block_index, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    positions.append((message_index, block_index))
    old_positions = positions[:-keep_recent] if len(positions) > keep_recent else []
    for message_index, block_index in old_positions:
        message = result[message_index]
        if block_index is None:
            message["content"] = _external_marker(str(message.get("content") or ""))
        else:
            block = message["content"][block_index]
            raw = json.dumps(block.get("content"), ensure_ascii=False, default=str)
            block["content"] = _external_marker(raw)
    return result


def _external_marker(raw: str) -> str:
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]
    citations = sorted(set(re.findall(r"\[(\d{1,3})\]", raw)))
    suffix = f"; citation_ids={','.join(citations)}" if citations else ""
    return f"[Externalized old tool result; sha256={digest}{suffix}]"


def _atomic_units(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    units: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        unit = [message]
        if _has_tool_call(message):
            index += 1
            while index < len(messages) and _is_tool_result_message(messages[index]):
                unit.append(messages[index])
                index += 1
            units.append(unit)
            continue
        units.append(unit)
        index += 1
    return units


def _has_tool_call(message: dict[str, Any]) -> bool:
    if message.get("tool_calls"):
        return True
    content = message.get("content")
    return isinstance(content, list) and any(isinstance(block, dict) and block.get("type") == "tool_use" for block in content)


def _is_tool_result_message(message: dict[str, Any]) -> bool:
    if message.get("role") == "tool":
        return True
    content = message.get("content")
    return isinstance(content, list) and bool(content) and all(
        isinstance(block, dict) and block.get("type") == "tool_result" for block in content
    )


def _snapshot_text(units: list[list[dict[str, Any]]]) -> str:
    text = json.dumps(units, ensure_ascii=False, default=str)
    citations = sorted(set(re.findall(r"\[(\d{1,3})\]", text)))
    numbers = re.findall(r"(?<!\w)\d+(?:\.\d+)?(?:%|mm|kg|km|kWh|V|A)?", text)
    user_texts: list[str] = []
    for unit in units:
        for message in unit:
            if message.get("role") == "user" and isinstance(message.get("content"), str):
                user_texts.append(message["content"][:300])
    snapshot = {
        "schema": "context_snapshot/v1",
        "task_goals": user_texts[-3:],
        "confirmed_facts": [],
        "pending_questions": [],
        "tool_state": {"dropped_atomic_units": len(units)},
        "evidence_ids": citations,
        "critical_values": numbers[-40:],
        "constraints": user_texts[:1],
    }
    return "[Automatic context snapshot]\n" + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
