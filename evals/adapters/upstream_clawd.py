from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_ROOT = PROJECT_ROOT / "experiments" / "clawd-upstream"
sys.path.insert(0, str(UPSTREAM_ROOT))

from src.agent.conversation import Conversation  # noqa: E402
from src.providers.openai_provider import OpenAIProvider  # noqa: E402
from src.tool_system.agent_loop import run_agent_loop  # noqa: E402
from src.tool_system.context import ToolContext  # noqa: E402
from src.tool_system.defaults import build_default_registry  # noqa: E402
from src.tool_system.permissions import ToolPermissionContext  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one case on unmodified upstream Clawd.")
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--model", default="deepseek-v4-flash-260425")
    parser.add_argument("--max-turns", type=int, default=20)
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(PROJECT_ROOT / ".env.local", override=True)
    for proxy_name in ("ALL_PROXY", "all_proxy"):
        if os.getenv(proxy_name, "").lower().startswith("socks://"):
            os.environ.pop(proxy_name, None)

    api_key = os.getenv("ARK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ARK_API_KEY is not configured")

    args.workspace.mkdir(parents=True, exist_ok=True)
    conversation = Conversation()
    conversation.add_user_message(args.prompt_file.read_text(encoding="utf-8"))
    provider = OpenAIProvider(
        api_key=api_key,
        base_url=os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
        model=args.model,
    )
    registry = build_default_registry(include_user_tools=False)
    context = ToolContext(
        workspace_root=args.workspace,
        permission_context=ToolPermissionContext.from_iterables(
            workspace_root=args.workspace,
            allow_docs=True,
        ),
        permission_handler=lambda _tool, _message, _suggestion: (True, True),
    )
    events: list[dict[str, object]] = []

    def on_event(event: object) -> None:
        kind = str(getattr(event, "kind", "message"))
        item: dict[str, object] = {
            "type": kind,
            "tool": getattr(event, "tool_name", None),
            "is_error": bool(getattr(event, "is_error", False)),
        }
        if kind == "tool_use":
            item["input"] = getattr(event, "tool_input", None)
        elif kind in {"tool_error", "tool_result"}:
            item["error"] = getattr(event, "error", None)
            item["status"] = "error" if item["is_error"] else "ok"
        events.append(item)

    started = time.time()
    result = run_agent_loop(
        conversation=conversation,
        provider=provider,
        tool_registry=registry,
        tool_context=context,
        max_turns=args.max_turns,
        stream=False,
        verbose=False,
        on_event=on_event,
    )
    final_text = result.response_text
    status = "max_turns_reached" if final_text.strip() == "[Max tool turns reached]" else "succeeded"
    artifacts = [
        {"path": str(path), "size": path.stat().st_size}
        for path in args.workspace.rglob("*")
        if path.is_file()
    ]
    print(json.dumps({
        "status": status,
        "final_text": final_text,
        "usage": result.usage,
        "run_ms": round((time.time() - started) * 1000, 3),
        "turns": result.num_turns,
        "events": events,
        "artifacts": artifacts,
        "model": args.model,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
