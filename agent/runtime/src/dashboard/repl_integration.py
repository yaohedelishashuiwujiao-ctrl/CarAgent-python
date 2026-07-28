"""Dashboard manager: orchestrates Rich Live rendering with prompt_toolkit input."""

from __future__ import annotations

from typing import Callable

from rich.console import Console
from rich.live import Live
from rich.text import Text

from .state import DashboardState
from .renderers import build_dashboard_layout

from src.tool_system.agent_loop import ToolEvent, ToolEventHandler, TextChunkHandler
from src.tool_system.agent_loop import summarize_tool_use, summarize_tool_result


class DashboardManager:
    """Manages the Rich Live rendering cycle for the split-screen dashboard.

    Key pattern: Live is stopped during prompt_toolkit input and restarted after.
    """

    def __init__(self, console: Console, state: DashboardState):
        self.console = console
        self.state = state
        self._live: Live | None = None

    def start(self) -> None:
        """Start the live dashboard rendering."""
        layout = build_dashboard_layout(self.state)
        self._live = Live(
            layout,
            console=self.console,
            refresh_per_second=4,
            transient=False,
            vertical_overflow="visible",
            screen=False,
        )
        self._live.start()

    def stop(self) -> None:
        """Stop the live rendering. Last frame stays on screen (transient=False)."""
        if self._live is not None:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None

    def update(self) -> None:
        """Update the live display with current state."""
        if self._live is not None:
            try:
                layout = build_dashboard_layout(self.state)
                self._live.update(layout)
            except Exception:
                pass

    def pause_for_input(self) -> None:
        """Stop Live and print a separator, releasing terminal for prompt_toolkit."""
        self.stop()
        self.console.print()

    def resume_after_input(self) -> None:
        """Rebuild and restart Live after user input."""
        self.stop()
        self.start()


def make_dashboard_callbacks(
    state: DashboardState,
    manager: DashboardManager,
    original_on_event: ToolEventHandler | None = None,
    original_on_text: TextChunkHandler | None = None,
) -> tuple[ToolEventHandler | None, TextChunkHandler | None]:
    """Wrap the original agent loop callbacks to also update DashboardState.

    Returns (wrapped_on_event, wrapped_on_text_chunk).
    """

    def _on_event(ev: ToolEvent) -> None:
        # Update state based on event type
        if ev.kind == "tool_use":
            state.current_phase = "tool_dispatch"
            state.current_tool_name = ev.tool_name
            state.current_tool_detail = summarize_tool_use(ev.tool_name, ev.tool_input or {})
            state.push_event("tool_use", ev.tool_name, state.current_tool_detail)
            state.status_text = f"Running: {ev.tool_name}"
            state.complete_phase("LLM_CALL")

        elif ev.kind == "tool_result":
            state.current_phase = "tool_result"
            detail = summarize_tool_result(ev.tool_name, ev.tool_output)
            state.push_event("tool_result", ev.tool_name, detail)
            state.status_text = f"Result: {ev.tool_name}"
            state.complete_phase("TOOL_DISPATCH")

        elif ev.kind == "tool_error":
            state.push_event("tool_error", ev.tool_name, ev.error or "Error")
            state.status_text = f"Error: {ev.tool_name}"

        manager.update()

        # Call original handler
        if original_on_event is not None:
            try:
                original_on_event(ev)
            except Exception:
                pass

    def _on_text_chunk(chunk: str) -> None:
        state.current_phase = "llm_call"
        state.status_text = "Generating response..."
        state.conversation_lines.append(chunk)
        manager.update()

        # Call original handler
        if original_on_text is not None:
            try:
                original_on_text(chunk)
            except Exception:
                pass

    return _on_event, _on_text_chunk
