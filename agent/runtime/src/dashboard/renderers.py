"""Rich renderable factories for the split-screen dashboard."""

from __future__ import annotations

from rich.console import Group
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .state import DashboardState


# Data flow pipeline nodes
FLOW_NODES = [
    ("INPUT",        "INPUT"),
    ("AGENT",        "AGENT"),
    ("LLM_CALL",     "LLM"),
    ("TOOL_DISPATCH", "TOOL"),
    ("TOOL_RESULT",  "RESULT"),
    ("RESPONSE",     "RESPONSE"),
]

# Map DashboardState.current_phase to flow node keys
PHASE_TO_FLOW = {
    "idle":            None,
    "thinking":        "LLM_CALL",
    "llm_call":        "LLM_CALL",
    "tool_dispatch":   "TOOL_DISPATCH",
    "tool_result":     "TOOL_RESULT",
    "done":            "RESPONSE",
}

ACTIVE_PHASES = {"LLM_CALL", "TOOL_DISPATCH", "TOOL_RESULT", "RESPONSE"}


def _get_flow_phase(state: DashboardState) -> str | None:
    """Return the currently active flow node key based on state."""
    return PHASE_TO_FLOW.get(state.current_phase)


def render_chat_panel(state: DashboardState) -> Panel:
    """Left panel: conversation view with color-coded messages."""
    if not state.conversation_lines:
        return Panel(
            Text("Waiting for input...", style="dim italic"),
            title="Conversation",
            border_style="blue",
            padding=(1, 2),
        )

    # Show last N lines to avoid overflow
    max_lines = 200
    lines = state.conversation_lines[-max_lines:]
    text = Text.assemble(*lines) if lines else Text("No conversation yet", style="dim")

    return Panel(
        text,
        title="Conversation",
        border_style="blue",
        padding=(0, 1),
    )


def render_flow_diagram(state: DashboardState) -> Panel:
    """Top-right panel: data flow pipeline visualization."""
    active_key = _get_flow_phase(state)
    parts: list = []

    for i, (key, label) in enumerate(FLOW_NODES):
        # Arrow between nodes
        if i > 0:
            parts.append((" → ", "dim"))

        # Determine style based on phase status
        if key == active_key and state.is_processing:
            style = "bold bright_yellow"
            display_label = label
            if key == "TOOL_DISPATCH" and state.current_tool_name:
                display_label = state.current_tool_name.upper()
            # Animate with brackets
            display_label = f"[{display_label}]"
        elif key in state.completed_phases:
            style = "bold green"
            display_label = label
        elif key == "INPUT" and state.is_processing:
            style = "bold white"
            display_label = label
        else:
            style = "dim"
            display_label = label

        parts.append((display_label, style))

    # Add token info if available
    if state.total_input_tokens > 0 or state.total_output_tokens > 0:
        parts.append(("\n", ""))
        total = state.total_input_tokens + state.total_output_tokens
        parts.append((
            f"Tokens: {state.total_input_tokens:,} in / {state.total_output_tokens:,} out / {total:,} total",
            "dim",
        ))

    text = Text.assemble(*parts)

    return Panel(
        text,
        title="Data Flow",
        border_style="cyan",
        padding=(0, 1),
    )


def render_event_stream(state: DashboardState) -> Panel:
    """Middle-right panel: scrolling tool event log."""
    if not state.events:
        return Panel(
            Text("No events yet", style="dim italic"),
            title="Event Stream",
            border_style="green",
            padding=(0, 1),
        )

    # Show last 8 events
    visible_events = state.events[-8:]

    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", width=8, no_wrap=True)
    table.add_column(width=14, no_wrap=True)
    table.add_column(ratio=1)

    kind_styles = {
        "tool_use": "cyan",
        "tool_result": "green",
        "tool_error": "red",
    }
    kind_labels = {
        "tool_use": "► USE",
        "tool_result": "✓ RESULT",
        "tool_error": "✗ ERROR",
    }

    for ev in visible_events:
        ts = f"{ev['timestamp']:.1f}s"
        kind = ev["kind"]
        style = kind_styles.get(kind, "white")
        label = kind_labels.get(kind, kind)
        detail = ev.get("detail", "")

        table.add_row(
            ts,
            f"[{style}]{label}[/{style}]",
            f"[{style}]{detail}[/{style}]",
        )

    return Panel(
        table,
        title="Event Stream",
        border_style="green",
    )


def render_metrics_table(state: DashboardState) -> Panel:
    """Bottom-right panel: token and turn metrics."""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", width=16, no_wrap=True)
    table.add_column(style="bold white", no_wrap=True)

    total = state.total_input_tokens + state.total_output_tokens
    table.add_row("Input Tokens",  f"{state.total_input_tokens:,}")
    table.add_row("Output Tokens", f"{state.total_output_tokens:,}")
    table.add_row("Total Tokens",  f"{total:,}")
    table.add_row("Turn Count",    str(state.current_turn))

    if state.turn_summaries:
        last = state.turn_summaries[-1]
        table.add_row("Last Turn",   f"{last.input_tokens:,}+{last.output_tokens:,} ({last.duration_seconds:.1f}s)")

    status_style = "green" if not state.is_processing else "yellow"
    table.add_row("Status",        f"[{status_style}]{state.status_text}[/]")

    return Panel(
        table,
        title="Metrics",
        border_style="magenta",
    )


def build_dashboard_layout(state: DashboardState) -> Layout:
    """Build the full Rich Layout tree: 60/40 horizontal split."""
    root = Layout(name="root")
    root.split_row(
        Layout(name="chat", ratio=3),
        Layout(name="dataflow", ratio=2),
    )

    # Left panel: conversation
    root["chat"].update(render_chat_panel(state))

    # Right panel: vertical stack of data flow, events, metrics
    root["dataflow"].split_column(
        Layout(render_flow_diagram(state), name="flow", size=5),
        Layout(render_event_stream(state), name="events"),
        Layout(render_metrics_table(state), name="metrics", size=10),
    )

    return root
