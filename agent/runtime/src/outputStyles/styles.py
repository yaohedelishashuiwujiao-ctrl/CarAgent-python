from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OutputStyle:
    name: str
    prompt: str
    source_path: Path | None = None


BUILTIN_OUTPUT_STYLES: dict[str, OutputStyle] = {
    "default": OutputStyle(
        name="default",
        prompt=(
            "Respond clearly, concisely, and focus on the user's requested engineering task.\n"
            "\n"
            "When using tools, treat each tool result as evidence. If WebFetch returns relevant "
            "visible text or extracted structured rows from an authoritative page, synthesize an "
            "answer from that evidence instead of continuing to search. This environment models a "
            "company intranet: WebSearch is unavailable, and WebFetch is limited to allowed AutoHome "
            "domains. If allowed sources are insufficient, answer with explicit uncertainty from the evidence "
            "already gathered. For research tasks, prefer a useful partial conclusion with sources "
            "over exhausting turns with low-yield searches."
        ),
    ),
    "explanatory": OutputStyle(
        name="explanatory",
        prompt="Respond with concise implementation details plus short educational notes when they improve understanding.",
    ),
}
