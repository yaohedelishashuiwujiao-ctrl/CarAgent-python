from __future__ import annotations

import html
import os
import re
import urllib.parse
import urllib.request
from typing import Any

from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolOutcomeStatus, ToolResult
from ..registry import ToolCapability, ToolDependencies, ToolExecutionPolicy, ToolSpec


_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
    r'<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.DOTALL,
)
_ANCHOR_RE = re.compile(r'<a[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>', re.DOTALL | re.IGNORECASE)


def _web_search_provider() -> str:
    default = "ark_responses" if os.getenv("ARK_API_KEY", "").strip() else "duckduckgo"
    return os.getenv("CLAWD_WEBSEARCH_PROVIDER", default).strip().lower()


def _collect_source_records(value: Any) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            url = item.get("url") or item.get("source_url")
            if isinstance(url, str) and url.startswith("https://") and url not in seen:
                seen.add(url)
                records.append({
                    "title": str(item.get("title") or item.get("name") or "Web source")[:240],
                    "url": url,
                    "snippet": str(item.get("snippet") or item.get("text") or item.get("summary") or "")[:1200],
                })
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return records


def _strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _normalize_result_url(url: str) -> str:
    url = html.unescape(url)
    if url.startswith("//"):
        url = "https:" + url
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        params = urllib.parse.parse_qs(parsed.query)
        redirected = params.get("uddg", [""])[0]
        if redirected:
            return redirected
    return url


def _fallback_results(raw: str, num: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _ANCHOR_RE.finditer(raw):
        url = _normalize_result_url(match.group("url"))
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if parsed.netloc.endswith("duckduckgo.com"):
            continue
        title = _strip_tags(match.group("title"))
        if not title or url in seen:
            continue
        tail = raw[match.end(): match.end() + 700]
        snippet = _strip_tags(tail)
        if len(snippet) > 220:
            snippet = snippet[:217] + "..."
        results.append({"title": title, "url": url, "snippet": snippet})
        seen.add(url)
        if len(results) >= num:
            break
    return results


class WebSearchTool:
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="WebSearch",
            description=(
                "Search the public web for source discovery and return titles, URLs, and snippets. "
                "Use it when governed SQL/private knowledge reports a coverage boundary. Follow with WebFetch on selected "
                "authoritative URLs before treating detailed claims as verified."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string"},
                    "num": {"type": "integer"},
                },
                "required": ["query"],
            },
            is_read_only=True,
            max_result_size_chars=50_000,
            capability=ToolCapability(
                namespace="data.web.search",
                actions=("search", "discover"),
                entity_types=("web_page", "public_source"),
                input_modes=("natural_language_query",),
                output_modes=("ranked_links", "web_search"),
                limitations=(
                    "Search snippets are discovery evidence, not a substitute for fetching the authoritative source.",
                    "Availability depends on the configured public search endpoint and network policy.",
                ),
                positive_examples=("Discover official manufacturer or supplier pages after local corpus coverage is insufficient.",),
                negative_examples=("Do not repeatedly search equivalent queries or treat snippets as full-document proof.",),
            ),
            execution=ToolExecutionPolicy(
                timeout_s=15,
                retryable_outcomes=(ToolOutcomeStatus.TRANSIENT_FAILURE.value,),
                max_attempts=2,
                concurrency_pool="web",
                supports_parallel=True,
                cache_policy="short_ttl",
            ),
            dependencies=ToolDependencies(
                services=("public_web_search",),
                health_probe="network_dns_health",
                coverage_probe="public_web_index_coverage",
            ),
            preflight_checks=("tool_authorized", "public_web_search_enabled"),
        )

    def run(self, tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
        query = tool_input["query"]
        if not isinstance(query, str) or not query.strip():
            raise ToolInputError("query must be a non-empty string")
        num = tool_input.get("num", 5)
        if not isinstance(num, int) or num < 1 or num > 10:
            raise ToolInputError("num must be an integer between 1 and 10")

        if _web_search_provider() in {"ark", "ark_responses", "volcengine"}:
            return self._run_ark_search(query.strip(), num, context)

        return self._run_duckduckgo_search(query.strip(), num, context)

    def _run_ark_search(self, query: str, num: int, context: ToolContext) -> ToolResult:
        api_key = os.getenv("ARK_API_KEY", "").strip()
        if not api_key:
            return ToolResult(
                name="WebSearch",
                output={"error": "ARK_API_KEY is not configured for official Web Search."},
                is_error=True,
                outcome_status=ToolOutcomeStatus.DEPENDENCY_UNHEALTHY,
                reason_code="WEB_SEARCH_CREDENTIAL_MISSING",
            )
        try:
            import httpx
            from openai import OpenAI

            client = OpenAI(
                base_url=os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
                api_key=api_key,
                http_client=httpx.Client(trust_env=False),
            )
            response = client.responses.create(
                model=os.getenv("CLAWD_WEBSEARCH_MODEL", "doubao-seed-evolving"),
                input=(
                    f"Search the public web for: {query}\n"
                    f"Return at most {num} authoritative results. Preserve each source title, HTTPS URL, and a concise factual snippet."
                ),
                tools=[{"type": "web_search"}],
            )
            payload = response.model_dump(mode="json")
            results = _collect_source_records(payload.get("output") or [])[:num]
            summary = str(getattr(response, "output_text", "") or "")
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        except Exception as exc:
            message = str(exc)
            not_activated = "ToolNotOpen" in message or "has not activated web search" in message
            return ToolResult(
                name="WebSearch",
                output={
                    "error": (
                        "Volcengine Ark Web Search is not activated for this account."
                        if not_activated
                        else f"Official Ark Web Search failed: {message[:800]}"
                    ),
                    "provider": "ark_responses",
                    "activation_url": (
                        "https://console.volcengine.com/common-buy/CC_content_plugin"
                        if not_activated
                        else None
                    ),
                },
                is_error=True,
                outcome_status=ToolOutcomeStatus.DEPENDENCY_UNHEALTHY,
                reason_code="WEB_SEARCH_NOT_ACTIVATED" if not_activated else "WEB_SEARCH_PROVIDER_UNAVAILABLE",
            )

        self._remember_urls(context, results)
        return ToolResult(
            name="WebSearch",
            output={
                "provider": "ark_responses",
                "query": query,
                "result_count": len(results),
                "results": results,
                "summary": summary,
                "provider_usage": usage,
            },
            outcome_status=ToolOutcomeStatus.SUCCESS if results else ToolOutcomeStatus.NO_DATA,
            reason_code=None if results else "WEB_SEARCH_NO_RESULTS",
        )

    def _run_duckduckgo_search(self, query: str, num: int, context: ToolContext) -> ToolResult:

        url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        req = urllib.request.Request(url, headers={"User-Agent": "clawd-codex/0.1"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read(1_000_000).decode("utf-8", errors="replace")

        results: list[dict[str, str]] = []
        for match in _RESULT_RE.finditer(raw):
            results.append(
                {
                    "title": _strip_tags(match.group("title")),
                    "url": _normalize_result_url(match.group("url")),
                    "snippet": _strip_tags(match.group("snippet")),
                }
            )
            if len(results) >= num:
                break
        if not results:
            results = _fallback_results(raw, num)

        self._remember_urls(context, results)
        return ToolResult(
            name="WebSearch",
            output={"provider": "duckduckgo_html", "query": query, "result_count": len(results), "results": results},
            outcome_status=ToolOutcomeStatus.SUCCESS if results else ToolOutcomeStatus.NO_DATA,
            reason_code=None if results else "WEB_SEARCH_NO_RESULTS",
        )

    @staticmethod
    def _remember_urls(context: ToolContext, results: list[dict[str, str]]) -> None:
        discovered_urls = context.runtime_state.setdefault("web_search_urls", [])
        if isinstance(discovered_urls, list):
            for item in results:
                url = str(item.get("url") or "").strip()
                if url and url not in discovered_urls:
                    discovered_urls.append(url)
            if len(discovered_urls) > 100:
                del discovered_urls[:-100]
