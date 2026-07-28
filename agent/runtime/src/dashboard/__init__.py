"""Dashboard module: split-screen REPL with data flow visualization."""

from .state import DashboardState, TurnSummary
from .renderers import build_dashboard_layout
from .repl_integration import DashboardManager, make_dashboard_callbacks

__all__ = [
    "DashboardState",
    "TurnSummary",
    "build_dashboard_layout",
    "DashboardManager",
    "make_dashboard_callbacks",
]
