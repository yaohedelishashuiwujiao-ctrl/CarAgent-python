from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import socket
import urllib.parse
import urllib.request
from typing import Any

from ..context import ToolContext
from ..errors import ToolInputError, ToolPermissionError
from ..protocol import ToolOutcomeStatus, ToolResult
from ..preflight import PreflightDecision
from ..registry import ToolCapability, ToolDependencies, ToolExecutionPolicy, ToolSpec


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_ANCHOR_RE = re.compile(r'<a\b[^>]*href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<text>.*?)</a>', re.IGNORECASE | re.DOTALL)
_JSON_ROW_RE = re.compile(
    r'(?:\\?"name\\?"\s*:\s*\\?"(?P<name>(?:\\\\.|[^"\\])*)\\?"\s*,\s*\\?"data\\?"\s*:\s*\[(?P<data>.*?)\])',
    re.DOTALL,
)
_JSON_STRING_RE = re.compile(r'\\?"((?:\\\\.|[^"\\])*)\\?"')
_LINK_SCORE_TERMS = (
    "config",
    "configuration",
    "params",
    "parameter",
    "spec",
    "specs",
    "product",
    "model",
    "series",
    "detail",
    "参数",
    "配置",
    "详情",
    "车型",
    "车系",
)
_LOW_VALUE_LINK_RE = re.compile(
    r"(login|signup|register|privacy|policy|terms|about|contact|feedback|download/app|"
    r"weibo|wechat|douyin|tiktok|facebook|twitter|instagram|youtube|\.jpg|\.jpeg|\.png|\.gif|\.webp|\.svg|\.css|\.js)",
    re.IGNORECASE,
)
_DEFAULT_ALLOWED_DOMAINS = ("autohome.com.cn",)
MAX_FETCH_BYTES = 1_000_000


def _allowed_domains() -> tuple[str, ...]:
    raw = os.getenv("CLAWD_WEBFETCH_ALLOWED_DOMAINS", "")
    domains = [item.strip().lower().lstrip(".") for item in raw.split(",") if item.strip()]
    return tuple(domains or _DEFAULT_ALLOWED_DOMAINS)


def _is_allowed_domain(hostname: str) -> bool:
    host = hostname.lower().rstrip(".")
    return any(host == domain or host.endswith(f".{domain}") for domain in _allowed_domains())


def _is_private_host(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


def _search_discovered_host(hostname: str, context: ToolContext | None) -> bool:
    if context is None:
        return False
    urls = context.runtime_state.get("web_search_urls")
    if not isinstance(urls, list):
        return False
    for value in urls[-100:]:
        try:
            discovered = urllib.parse.urlparse(str(value))
        except Exception:
            continue
        if discovered.scheme == "https" and (discovered.hostname or "").lower().rstrip(".") == hostname.lower().rstrip("."):
            return True
    return False


def _validate_fetch_url(url: str, context: ToolContext | None = None) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ToolPermissionError("only HTTPS URLs are allowed")
    if not parsed.netloc or parsed.username or parsed.password:
        raise ToolInputError("url must include a valid network location without credentials")
    hostname = parsed.hostname or ""
    if hostname in {"localhost"} or hostname.endswith(".localhost") or _is_private_host(hostname):
        raise ToolPermissionError("refusing to fetch localhost/private network URLs")
    if not _is_allowed_domain(hostname) and not _search_discovered_host(hostname, context):
        allowed = ", ".join(_allowed_domains())
        raise ToolPermissionError(
            "company egress policy only allows WebFetch for configured domains or public HTTPS hosts "
            f"discovered by WebSearch in this run; configured domains: {allowed}"
        )
    return parsed


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, context: ToolContext):
        super().__init__()
        self._context = context

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        _validate_fetch_url(urllib.parse.urljoin(req.full_url, newurl), self._context)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _decode_json_fragment(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        return str(json.loads(f'"{value}"'))
    except Exception:
        value = value.replace(r"\/", "/").replace(r"\"", '"')
        value = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), value)
        return html.unescape(value)


def _compact_ws(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _extract_embedded_rows(raw: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _JSON_ROW_RE.finditer(raw):
        name = _compact_ws(_TAG_RE.sub(" ", _decode_json_fragment(match.group("name"))))
        if not name or len(name) > 120:
            continue
        values = [_compact_ws(_TAG_RE.sub(" ", _decode_json_fragment(item.group(1)))) for item in _JSON_STRING_RE.finditer(match.group("data"))]
        values = [item for item in values if item]
        if not values:
            continue
        key = f"{name}|{'|'.join(values[:8])}"
        if key in seen:
            continue
        seen.add(key)
        rows.append({"name": name, "values": values[:12]})
        if len(rows) >= 80:
            break
    return rows


def _extract_links(raw: str, base_url: str) -> list[dict[str, str]]:
    candidates: list[tuple[int, int, dict[str, str]]] = []
    seen: set[str] = set()
    for index, match in enumerate(_ANCHOR_RE.finditer(raw)):
        href = html.unescape(match.group("href")).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        text = _compact_ws(_TAG_RE.sub(" ", match.group("text")))
        key = url.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        if _LOW_VALUE_LINK_RE.search(key) or _LOW_VALUE_LINK_RE.search(text):
            continue
        haystack = f"{text} {parsed.path} {parsed.query}".lower()
        score = sum(1 for term in _LINK_SCORE_TERMS if term.lower() in haystack)
        if not text and score == 0:
            continue
        candidates.append((-score, index, {"text": text[:120], "url": key}))
        if len(candidates) >= 120:
            break
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in candidates[:20]]


def _html_to_text(raw: str, base_url: str) -> tuple[str, list[dict[str, Any]], list[dict[str, str]]]:
    title_match = _TITLE_RE.search(raw)
    title = _compact_ws(_TAG_RE.sub(" ", title_match.group(1))) if title_match else ""
    rows = _extract_embedded_rows(raw)
    links = _extract_links(raw, base_url)
    visible_source = _SCRIPT_STYLE_RE.sub(" ", raw)
    visible = _compact_ws(_TAG_RE.sub(" ", visible_source))
    parts: list[str] = []
    if title:
        parts.append(f"Title: {title}")
    if rows:
        parts.append("Extracted structured rows:")
        for row in rows:
            parts.append(f"- {row['name']}: {' | '.join(row['values'])}")
    if links:
        parts.append("Extracted links:")
        for link in links[:12]:
            label = f"{link['text']} - " if link["text"] else ""
            parts.append(f"- {label}{link['url']}")
    if visible:
        parts.append("Visible text:")
        parts.append(visible)
    return "\n".join(parts), rows, links


class WebFetchTool:
    def spec(self) -> ToolSpec:
        allowed_domains = ", ".join(_allowed_domains())
        return ToolSpec(
            name="WebFetch",
            description=(
                "Fetch one HTTPS URL and return extracted text content. "
                f"Current allowed domains: {allowed_domains}. "
                "A public HTTPS host returned by WebSearch in this same run is also eligible. "
                "Do not invent URLs; the Runtime rejects undiscovered, private, or non-HTTPS targets before network access."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            is_read_only=True,
            max_result_size_chars=30_000,
            capability=ToolCapability(
                namespace="data.web",
                actions=("fetch",),
                entity_types=("web_page",),
                input_modes=("https_url",),
                output_modes=("web_text", "structured_rows", "links", "evidence"),
                limitations=(
                    f"Only HTTPS pages under the current allowlist are available: {allowed_domains}.",
                    "This tool does not search the web or bypass authentication and anti-bot controls.",
                ),
                positive_examples=("Fetch an allowed AutoHome configuration page already identified by URL.",),
                negative_examples=("Do not call for manufacturer, Wikipedia, intranet, localhost, or unlisted domains.",),
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
                services=("public_https",),
                required_config=("CLAWD_WEBFETCH_ALLOWED_DOMAINS",),
                health_probe="network_dns_health",
                coverage_probe="web_domain_allowlist_coverage",
            ),
            preflight_checks=("tool_authorized", "https_url", "web_domain_allowed", "public_ip_only"),
        )

    def run(self, tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
        url = tool_input["url"]
        if not isinstance(url, str) or not url:
            raise ToolInputError("url must be a non-empty string")

        _validate_fetch_url(url, context)

        req = urllib.request.Request(url, headers={"User-Agent": "clawd-codex/0.1"})
        if os.getenv("APP_ENV", "local").lower() in {"local", "dev", "development", "test"}:
            response_context = urllib.request.urlopen(req, timeout=15)
        else:
            response_context = urllib.request.build_opener(_SafeRedirectHandler(context)).open(req, timeout=15)
        with response_context as resp:
            final_url = resp.geturl() if hasattr(resp, "geturl") else url
            _validate_fetch_url(final_url, context)
            content_length = int(resp.headers.get("Content-Length") or 0)
            if content_length > MAX_FETCH_BYTES:
                raise ToolPermissionError("response exceeds the maximum download size")
            raw_bytes = resp.read(MAX_FETCH_BYTES + 1)
            if len(raw_bytes) > MAX_FETCH_BYTES:
                raise ToolPermissionError("response exceeds the maximum download size")
            content_type = resp.headers.get("Content-Type", "")

        text = raw_bytes.decode("utf-8", errors="replace")
        structured_rows: list[dict[str, Any]] = []
        links: list[dict[str, str]] = []
        if "text/html" in content_type:
            text, structured_rows, links = _html_to_text(text, url)

        if len(text) > 30_000:
            text = text[:30_000] + "\n\n... [truncated] ..."

        return ToolResult(
            name="WebFetch",
            output={"url": url, "content_type": content_type, "content": text, "structured_rows": structured_rows, "links": links},
        )

    def preflight(self, tool_input: dict[str, Any], context: ToolContext) -> PreflightDecision:
        url = tool_input.get("url")
        if not isinstance(url, str) or not url.strip():
            return PreflightDecision.reject("WEB_URL_INVALID", "url must be a non-empty string.")
        try:
            _validate_fetch_url(url, context)
        except ToolPermissionError as exc:
            message = str(exc)
            reason = "WEB_DOMAIN_NOT_ALLOWED" if "egress policy" in message else "WEB_TARGET_NOT_ALLOWED"
            return PreflightDecision.reject(
                reason,
                message,
                alternative_capabilities=("data.web.search", "data.document.search"),
                diagnostics={"allowed_domains": list(_allowed_domains())},
            )
        except ToolInputError as exc:
            return PreflightDecision.reject("WEB_URL_INVALID", str(exc))
        return PreflightDecision.allow("WEB_URL_POLICY_PASSED")
