"""Dashboard state management for the split-screen REPL."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TurnSummary:
    turn_number: int
    input_tokens: int
    output_tokens: int
    duration_seconds: float
    tool_count: int = 0


@dataclass
class DashboardState:
    """Mutable state holder for the dashboard panels."""
    conversation_lines: list[str] = field(default_factory=list)
    current_turn: int = 0
    current_phase: str = "idle"  # idle|thinking|llm_call|tool_dispatch|tool_result|done
    current_tool_name: str = ""
    current_tool_detail: str = ""
    events: list[dict] = field(default_factory=list)
    max_events: int = 500
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    turn_summaries: list[TurnSummary] = field(default_factory=list)
    turn_start_time: float = 0.0
    loop_start_time: float = 0.0
    status_text: str = "Ready"
    is_processing: bool = False

    # Track completed phases for the flow diagram
    completed_phases: set[str] = field(default_factory=set)

    def push_event(self, kind: str, tool: str, detail: str) -> None:
        """Append an event to the event stream, trimming if over max."""
        self.events.append({
            "timestamp": time.monotonic() - self.loop_start_time,
            "kind": kind,
            "tool": tool,
            "detail": detail,
        })
        if len(self.events) > self.max_events:
            self.events = self.events[-self.max_events:]

    def begin_turn(self) -> None:
        """Mark the start of a new agent loop turn sequence."""
        self.current_turn += 1
        self.turn_start_time = time.monotonic()
        self.loop_start_time = self.turn_start_time
        self.current_phase = "thinking"
        self.completed_phases = set()
        self.is_processing = True
        self.status_text = f"Turn {self.current_turn}: Thinking..."

    def complete_phase(self, phase: str) -> None:
        """Mark a phase as completed."""
        self.completed_phases.add(phase)

    def end_turn(self, input_tokens: int, output_tokens: int, tool_count: int = 0) -> None:
        """Mark the end of the current turn, recording metrics."""
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        duration = time.monotonic() - self.turn_start_time
        self.turn_summaries.append(TurnSummary(
            turn_number=self.current_turn,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_seconds=duration,
            tool_count=tool_count,
        ))
        self.current_phase = "done"
        self.is_processing = False
        self.status_text = "Ready"

    def reset(self) -> None:
        """Reset all state to initial values."""
        self.conversation_lines.clear()
        self.current_turn = 0
        self.current_phase = "idle"
        self.current_tool_name = ""
        self.current_tool_detail = ""
        self.events.clear()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.turn_summaries.clear()
        self.turn_start_time = 0.0
        self.loop_start_time = 0.0
        self.status_text = "Ready"
        self.is_processing = False
        self.completed_phases.clear()
