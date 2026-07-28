from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CostTracker:
    total_units: int = 0
    events: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    turn_costs: list[dict] = field(default_factory=list)
    last_usage: dict | None = None

    def record(self, label: str, units: int) -> None:
        self.total_units += units
        self.events.append(f'{label}:{units}')

    def record_tokens(self, input_tokens: int, output_tokens: int, turn: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.turn_costs.append({
            "turn": turn,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        })
        self.last_usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
