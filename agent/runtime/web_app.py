"""Small zero-dependency web UI for Clawd Code.

Run with:
    python3 web_app.py

Then open http://127.0.0.1:7860
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

WEB_HOME = Path(__file__).resolve().parent / ".web_home"
WEB_HOME.mkdir(exist_ok=True)
os.environ["HOME"] = str(WEB_HOME)

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_PATH = LOG_DIR / "clawd-web.log"


def _configure_logging() -> logging.Logger:
    logger = logging.getLogger("clawd_web")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(threadName)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False
    return logger


LOGGER = _configure_logging()

from src.config import get_default_provider, get_provider_config, load_config, set_api_key, set_default_provider
from src.agent import Session
from src.repl.core import ClawdREPL
from src.providers import PROVIDER_INFO
from src.query_engine import QueryEnginePort
from src.runtime import PortRuntime
from src.runtime_auth import RuntimeAuthError, local_runtime_context, verify_runtime_authorization
from src.routing_decision import decide_route
from src.task_router import build_subjects_lookup_answer, build_subjects_stats_answer, classify_l0
from src.token_estimation import count_messages_tokens
from src.tool_system.agent_loop import AgentRunCancelled, ToolEvent, ToolEventHandler, run_agent_loop, summarize_tool_result, summarize_tool_use
from src.tool_system.protocol import ToolCall
from src.tool_system.scheduler import ToolCallScheduler


HOST = "127.0.0.1"
PORT = int(os.getenv("CLAWD_WEB_PORT", "7860"))
MAX_HISTORY_MESSAGES = 24
OUTPUTS_DIR = Path(__file__).resolve().parent / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)
os.environ["CLAWD_OUTPUT_DIR"] = str(OUTPUTS_DIR)
MPLCONFIG_DIR = Path("/tmp/clawd-matplotlib")
MPLCONFIG_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(MPLCONFIG_DIR))


WEB_REPLS: dict[str, ClawdREPL] = {}
MAX_CONCURRENT_AGENT_RUNS = max(1, int(os.getenv("CLAWD_MAX_CONCURRENT_AGENT_RUNS", "8")))
MAX_AGENT_QUEUE_SECONDS = max(0.0, float(os.getenv("CLAWD_AGENT_QUEUE_TIMEOUT_SECONDS", "0")))
AGENT_RUN_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT_AGENT_RUNS)
AGENT_RUN_STATE_LOCK = threading.Lock()
ACTIVE_AGENT_RUNS = 0
SESSION_LOCKS: dict[str, threading.Lock] = {}
SESSION_LOCKS_GUARD = threading.Lock()
RUNTIME_TOKEN_JTIS: dict[str, int] = {}
RUNTIME_TOKEN_JTIS_LOCK = threading.Lock()
ACTIVE_RUN_CONTEXTS: dict[str, Any] = {}
ACTIVE_RUN_CONTEXTS_LOCK = threading.Lock()


def _get_session_lock(session_id: str) -> threading.Lock:
    with SESSION_LOCKS_GUARD:
        lock = SESSION_LOCKS.get(session_id)
        if lock is None:
            lock = threading.Lock()
            SESSION_LOCKS[session_id] = lock
        return lock


def _acquire_agent_run_slot() -> bool:
    global ACTIVE_AGENT_RUNS
    if MAX_AGENT_QUEUE_SECONDS <= 0:
        acquired = AGENT_RUN_SEMAPHORE.acquire(blocking=False)
    else:
        acquired = AGENT_RUN_SEMAPHORE.acquire(timeout=MAX_AGENT_QUEUE_SECONDS)
    if acquired:
        with AGENT_RUN_STATE_LOCK:
            ACTIVE_AGENT_RUNS += 1
    return acquired


def _release_agent_run_slot() -> None:
    global ACTIVE_AGENT_RUNS
    with AGENT_RUN_STATE_LOCK:
        ACTIVE_AGENT_RUNS = max(0, ACTIVE_AGENT_RUNS - 1)
    AGENT_RUN_SEMAPHORE.release()


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Clawd Code Web</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f7f4;
      --panel: #ffffff;
      --ink: #202124;
      --muted: #667085;
      --line: #d9ded6;
      --accent: #0f766e;
      --accent-dark: #115e59;
      --warn: #9a3412;
      --code: #f1f5f2;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--bg);
      height: 100vh;
      overflow: hidden;
    }
    .settings-panel {
      background: #fbfbf8;
      overflow: auto;
    }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(0, 0.8fr);
      min-width: 0;
      height: 100vh;
    }
    .left-panel {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      min-width: 0;
      overflow: hidden;
    }
    .right-panel {
      border-left: 1px solid var(--line);
      background: #fbfcfb;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      min-width: 360px;
      overflow: hidden;
    }
    .observe-section {
      border-bottom: 1px solid var(--line);
      padding: 14px;
      min-width: 0;
    }
    .observe-section.flow {
      border-bottom: 0;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    .observe-head {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 12px;
      margin-bottom: 10px;
    }
    .observe-title {
      font-size: 12px;
      font-weight: 760;
      color: var(--ink);
    }
    .observe-sub {
      font-size: 11px;
      color: var(--muted);
      white-space: nowrap;
    }
    .observe-metrics {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }
    .observe-metric {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      padding: 8px;
      min-height: 58px;
    }
    .observe-label {
      display: block;
      font-size: 10px;
      color: var(--muted);
      margin-bottom: 5px;
      white-space: nowrap;
    }
    .observe-value {
      display: block;
      font-size: 15px;
      font-weight: 760;
      color: var(--ink);
      line-height: 1.2;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .flow-list {
      position: relative;
      display: flex;
      flex-direction: column;
      gap: 8px;
      overflow: auto;
      padding: 2px 2px 12px 0;
      min-height: 0;
    }
    .flow-list::before {
      content: "";
      position: absolute;
      left: 9px;
      top: 4px;
      bottom: 4px;
      width: 1px;
      background: #dde4df;
    }
    .flow-event {
      position: relative;
      display: grid;
      grid-template-columns: 20px minmax(0, 1fr);
      gap: 8px;
    }
    .flow-dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      margin: 7px 0 0 5px;
      background: #94a3b8;
      border: 2px solid #fff;
      box-shadow: 0 0 0 1px #cbd5e1;
      z-index: 1;
    }
    .flow-event.active .flow-dot { background: var(--accent); box-shadow: 0 0 0 3px #d8f1eb; }
    .flow-event.ok .flow-dot { background: #16a34a; }
    .flow-event.error .flow-dot { background: #dc2626; }
    .flow-event.output .flow-dot { background: #7c3aed; }
    .flow-body {
      min-width: 0;
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 7px;
      padding: 8px 9px;
    }
    .flow-route {
      display: flex;
      align-items: center;
      gap: 6px;
      min-width: 0;
      font-size: 12px;
      font-weight: 730;
      color: var(--ink);
    }
    .flow-route span {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .flow-arrow-inline { color: var(--muted); flex: 0 0 auto; }
    .flow-detail {
      margin-top: 4px;
      font-size: 11px;
      line-height: 1.42;
      color: #50605c;
      overflow-wrap: anywhere;
    }
    .flow-meta {
      margin-top: 5px;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      font-size: 10px;
      color: var(--muted);
    }
    h1 {
      font-size: 20px;
      margin: 0 0 6px;
      font-weight: 750;
      letter-spacing: 0;
    }
    h2 {
      font-size: 13px;
      text-transform: uppercase;
      color: var(--muted);
      margin: 24px 0 10px;
      letter-spacing: 0.04em;
    }
    .sub { color: var(--muted); font-size: 13px; line-height: 1.45; }
    .status {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      padding: 12px;
      margin-top: 16px;
      font-size: 13px;
      line-height: 1.5;
    }
    .pill {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      background: #e6f3ef;
      color: var(--accent-dark);
      font-weight: 650;
      font-size: 12px;
    }
    label { display: block; font-size: 12px; color: var(--muted); margin: 10px 0 5px; }
    input, select, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 10px;
      font: inherit;
      background: #fff;
      color: var(--ink);
    }
    input:focus, select:focus, textarea:focus {
      outline: 2px solid #99d6cd;
      border-color: var(--accent);
    }
    button {
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: #fff;
      font: inherit;
      font-weight: 650;
      padding: 9px 12px;
      cursor: pointer;
    }
    button:hover { background: var(--accent-dark); }
    button.secondary {
      background: #e8ece8;
      color: #26312f;
    }
    button.secondary:hover { background: #dbe2dc; }
    .row { display: flex; gap: 8px; align-items: center; }
    .row > * { min-width: 0; }
    .session-list {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-top: 10px;
    }
    .session-item {
      width: 100%;
      text-align: left;
      background: #fff;
      color: var(--ink);
      border: 1px solid var(--line);
      padding: 8px 9px;
      font-size: 13px;
      font-weight: 550;
    }
    .session-item.active {
      border-color: var(--accent);
      background: #eef8f5;
      color: var(--accent-dark);
    }
    .session-meta {
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 450;
      margin-top: 2px;
    }
    .topbar {
      border-bottom: 1px solid var(--line);
      padding: 14px 18px;
      background: rgba(255,255,255,0.82);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .topbar-right {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .icon-btn {
      width: 34px;
      height: 34px;
      padding: 0;
      display: inline-grid;
      place-items: center;
      border-radius: 6px;
    }
    .modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 50;
      background: rgba(32, 33, 36, 0.38);
      display: none;
      align-items: flex-start;
      justify-content: flex-end;
      padding: 18px;
    }
    .modal-backdrop.open { display: flex; }
    .settings-window {
      width: min(420px, calc(100vw - 36px));
      max-height: calc(100vh - 36px);
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfbf8;
      box-shadow: 0 18px 48px rgba(15, 23, 42, 0.22);
      overflow: auto;
      padding: 18px;
    }
    .settings-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
    }
    .tabs {
      display: flex;
      gap: 4px;
      padding: 3px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f1f3ef;
    }
    .tab {
      background: transparent;
      color: var(--muted);
      padding: 7px 11px;
      border-radius: 6px;
    }
    .tab.active {
      background: #fff;
      color: var(--ink);
      box-shadow: 0 1px 2px rgba(0,0,0,0.05);
    }
    .workspace {
      overflow: auto;
      padding: 20px;
    }
    .messages {
      max-width: 980px;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .msg {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 14px;
      line-height: 1.55;
      word-break: break-word;
    }
    .msg.user {
      border-color: #b7d4ce;
      background: #eef8f5;
      margin-left: 56px;
    }
    .msg.assistant { margin-right: 56px; }
    .role {
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      margin-bottom: 6px;
      text-transform: uppercase;
    }
    .content.plain { white-space: pre-wrap; }
    .markdown { white-space: normal; }
    .markdown > :first-child { margin-top: 0; }
    .markdown > :last-child { margin-bottom: 0; }
    .markdown p { margin: 0.55em 0; }
    .markdown h1, .markdown h2, .markdown h3 {
      margin: 0.85em 0 0.4em;
      line-height: 1.25;
      letter-spacing: 0;
      text-transform: none;
      color: var(--ink);
    }
    .markdown h1 { font-size: 22px; }
    .markdown h2 { font-size: 18px; }
    .markdown h3 { font-size: 15px; }
    .markdown ul, .markdown ol { margin: 0.55em 0; padding-left: 1.4em; }
    .markdown li { margin: 0.22em 0; }
    .markdown hr {
      border: 0;
      border-top: 1px solid var(--line);
      margin: 14px 0;
    }
    .markdown img {
      max-width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .markdown code {
      background: var(--code);
      border: 1px solid var(--line);
      border-radius: 4px;
      padding: 1px 4px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.92em;
    }
    .markdown pre {
      margin: 0.65em 0;
      white-space: pre;
    }
    .markdown pre code {
      display: block;
      background: transparent;
      border: 0;
      padding: 0;
      white-space: pre;
    }
    .markdown table {
      border-collapse: collapse;
      width: 100%;
      margin: 0.75em 0;
      font-size: 13px;
    }
    .markdown th, .markdown td {
      border: 1px solid var(--line);
      padding: 6px 8px;
      text-align: left;
      vertical-align: top;
    }
    .markdown th { background: #f3f6f2; }
    .markdown blockquote {
      margin: 0.65em 0;
      padding-left: 12px;
      border-left: 3px solid var(--line);
      color: var(--muted);
    }
    .process {
      white-space: pre-wrap;
      background: #f8faf7;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 10px;
      margin-bottom: 10px;
      color: #4c5b57;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      line-height: 1.45;
    }
    .composer {
      border-top: 1px solid var(--line);
      padding: 14px 18px;
      background: #fff;
    }
    .composer-inner {
      max-width: 980px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: end;
    }
    textarea {
      min-height: 54px;
      max-height: 180px;
      resize: vertical;
    }
    pre {
      background: var(--code);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      overflow: auto;
      line-height: 1.45;
      white-space: pre-wrap;
    }
    .hidden { display: none; }
    .error { color: var(--warn); }
    @media (max-width: 820px) {
      main { height: auto; min-height: 58vh; grid-template-columns: 1fr; }
      .right-panel { min-width: 0; border-left: 0; border-top: 1px solid var(--line); max-height: 52vh; }
      .observe-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .msg.user { margin-left: 0; }
      .msg.assistant { margin-right: 0; }
      .composer-inner { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <div class="left-panel">
      <div class="topbar">
        <div class="tabs">
          <button class="tab active" data-mode="chat">Chat</button>
          <button class="tab" data-mode="route">Route</button>
          <button class="tab" data-mode="bootstrap">Bootstrap</button>
        </div>
        <div class="topbar-right">
          <span class="pill" id="modePill">chat</span>
          <button class="secondary icon-btn" id="openSettings" title="设置">⚙</button>
        </div>
      </div>
      <div class="workspace">
        <div class="messages" id="messages"></div>
      </div>
      <div class="composer">
        <div class="composer-inner">
          <textarea id="prompt" placeholder="Ask Clawd Code something..."></textarea>
          <button id="send">Send</button>
        </div>
      </div>
    </div>
    <div class="right-panel">
      <div class="observe-section">
        <div class="observe-head">
          <span class="observe-title">运行指标</span>
          <span class="observe-sub" id="metricTool">session</span>
        </div>
        <div class="observe-metrics" id="metricGrid">
          <div class="observe-metric"><span class="observe-label">输入 tokens</span><span class="observe-value">-</span></div>
          <div class="observe-metric"><span class="observe-label">输出 tokens</span><span class="observe-value">-</span></div>
          <div class="observe-metric"><span class="observe-label">工具调用</span><span class="observe-value">0</span></div>
        </div>
      </div>
      <div class="observe-section flow">
        <div class="observe-head">
          <span class="observe-title">数据流事件</span>
          <span class="observe-sub" id="flowPhase">idle</span>
        </div>
        <div class="flow-list" id="dataLog">
          <div class="flow-event">
            <span class="flow-dot"></span>
            <div class="flow-body">
              <div class="flow-route"><span>等待输入</span></div>
              <div class="flow-detail">发送消息后，这里会按实际发生顺序显示数据在组件之间的流动。</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </main>
  <div class="modal-backdrop" id="settingsModal">
    <div class="settings-window settings-panel">
      <div class="settings-head">
        <div>
          <h1 style="margin-bottom: 2px">设置</h1>
          <div class="sub">会话、模型与本地工具</div>
        </div>
        <button class="secondary icon-btn" id="closeSettings" title="关闭">×</button>
      </div>
      <div class="status" id="status">Loading status...</div>

      <h2>Sessions</h2>
      <div class="row">
        <button class="secondary" id="newSession">New</button>
        <button class="secondary" id="refreshSessions">Refresh</button>
      </div>
      <div class="session-list" id="sessionList"></div>

      <h2>Provider</h2>
      <label for="provider">Default provider</label>
      <select id="provider"></select>
      <label for="apiKey">API key</label>
      <input id="apiKey" type="password" autocomplete="off" placeholder="Leave blank to keep current key">
      <label for="baseUrl">Base URL</label>
      <input id="baseUrl" type="text">
      <label for="model">Model</label>
      <input id="model" type="text">
      <div class="row" style="margin-top: 10px">
        <button id="saveConfig">Save</button>
        <button class="secondary" id="refresh">Refresh</button>
      </div>
      <div class="sub" id="configMsg" style="margin-top: 10px"></div>

      <h2>Local Tools</h2>
      <div class="row">
        <button class="secondary" id="summaryBtn">Summary</button>
        <button class="secondary" id="clearBtn">Clear</button>
      </div>
    </div>
  </div>

  <script>
    let sessionId = localStorage.getItem("clawd_session_id") || crypto.randomUUID();
    localStorage.setItem("clawd_session_id", sessionId);
    let mode = "chat";
    let providerInfo = {};
    let telemetryState = null;
    let liveEvents = [];

    const messages = document.getElementById("messages");
    const promptEl = document.getElementById("prompt");
    const statusEl = document.getElementById("status");
    const providerEl = document.getElementById("provider");
    const apiKeyEl = document.getElementById("apiKey");
    const baseUrlEl = document.getElementById("baseUrl");
    const modelEl = document.getElementById("model");
    const configMsg = document.getElementById("configMsg");
    const sessionListEl = document.getElementById("sessionList");
    const dataLogEl = document.getElementById("dataLog");
    const flowPhaseEl = document.getElementById("flowPhase");
    const metricGridEl = document.getElementById("metricGrid");
    const metricToolEl = document.getElementById("metricTool");
    const settingsModal = document.getElementById("settingsModal");
    const openSettingsBtn = document.getElementById("openSettings");
    const closeSettingsBtn = document.getElementById("closeSettings");

    function escapeHtml(text) {
      return String(text || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    function renderDataLog(state) {
      if (!state) {
        flowPhaseEl.textContent = "idle";
        dataLogEl.innerHTML = `
          <div class="flow-event">
            <span class="flow-dot"></span>
            <div class="flow-body">
              <div class="flow-route"><span>等待输入</span></div>
              <div class="flow-detail">发送消息后按实际发生顺序显示数据流。</div>
            </div>
          </div>`;
        metricGridEl.innerHTML = `
          <div class="observe-metric"><span class="observe-label">输入 tokens</span><span class="observe-value">-</span></div>
          <div class="observe-metric"><span class="observe-label">输出 tokens</span><span class="observe-value">-</span></div>
          <div class="observe-metric"><span class="observe-label">工具调用</span><span class="observe-value">0</span></div>`;
        metricToolEl.textContent = "session";
        return;
      }

      flowPhaseEl.textContent = state.phase || "idle";
      const stats = state.stats || {};
      const inTok = stats.usage_input_tokens || 0;
      const outTok = stats.usage_output_tokens || 0;
      const elapsed = stats.elapsed_ms || 0;
      const tokenSource = stats.token_source || "unknown";
      const events = Array.isArray(state.flow_events) ? state.flow_events : [];
      dataLogEl.innerHTML = (events.length ? events : [{
        source: "session",
        target: "idle",
        label: "等待",
        detail: "暂无运行事件",
        status: "idle",
      }]).map((event) => {
        const status = event.status || event.kind || "idle";
        const meta = [];
        if (event.elapsed_ms != null) meta.push(`${event.elapsed_ms}ms`);
        if (event.tokens != null) meta.push(`${event.tokens} tokens`);
        if (event.rows != null) meta.push(`${event.rows} rows`);
        return `
          <div class="flow-event ${escapeHtml(status)}">
            <span class="flow-dot"></span>
            <div class="flow-body">
              <div class="flow-route">
                <span>${escapeHtml(event.source || "source")}</span>
                <span class="flow-arrow-inline">→</span>
                <span>${escapeHtml(event.target || "target")}</span>
              </div>
              <div class="flow-detail"><strong>${escapeHtml(event.label || "")}</strong>${event.detail ? ` · ${escapeHtml(event.detail)}` : ""}</div>
              ${meta.length ? `<div class="flow-meta">${meta.map(escapeHtml).join(" · ")}</div>` : ""}
            </div>
          </div>`;
      }).join("");
      dataLogEl.scrollTop = dataLogEl.scrollHeight;

      metricToolEl.textContent = state.current_tool || state.phase || "session";
      const metrics = [
        { label: tokenSource === "provider" ? "输入 tokens" : "输入 tokens(估)", value: inTok > 0 ? Number(inTok).toLocaleString() : "-" },
        { label: tokenSource === "provider" ? "输出 tokens" : "输出 tokens(估)", value: outTok > 0 ? Number(outTok).toLocaleString() : "-" },
        { label: "工具调用", value: stats.tool_uses || 0 },
        { label: "工具结果", value: stats.tool_results || 0 },
        { label: "工具错误", value: stats.tool_errors || 0 },
        { label: "SQL 行数", value: stats.sql_rows || "-" },
        { label: "耗时", value: elapsed > 0 ? `${elapsed}ms` : "-" },
        { label: "RSS 内存", value: stats.memory_rss_mb != null ? `${Number(stats.memory_rss_mb).toFixed(0)}MB` : "-" },
        { label: "峰值内存", value: stats.memory_peak_rss_mb != null ? `${Number(stats.memory_peak_rss_mb).toFixed(0)}MB` : "-" },
      ];
      metricGridEl.innerHTML = metrics.map(m =>
        `<div class="observe-metric"><span class="observe-label">${escapeHtml(m.label)}</span><span class="observe-value">${escapeHtml(String(m.value))}</span></div>`
      ).join("");
    }

    function openSettings() {
      settingsModal.classList.add("open");
    }

    function closeSettings() {
      settingsModal.classList.remove("open");
    }

    function renderInlineMarkdown(text) {
      let html = escapeHtml(text);
      html = html.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, '<img src="$2" alt="$1">');
      html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
      html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
      html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
      html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
      return html;
    }

    function renderMarkdown(text) {
      const src = String(text || "").replace(/\r\n/g, "\n");
      const blocks = [];
      let inCode = false;
      let codeLang = "";
      let codeLines = [];
      let para = [];
      let list = [];
      let orderedList = [];
      let table = [];

      function flushPara() {
        if (!para.length) return;
        blocks.push(`<p>${renderInlineMarkdown(para.join(" "))}</p>`);
        para = [];
      }
      function flushList() {
        if (!list.length) return;
        blocks.push(`<ul>${list.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`);
        list = [];
      }
      function flushOrderedList() {
        if (!orderedList.length) return;
        blocks.push(`<ol>${orderedList.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ol>`);
        orderedList = [];
      }
      function flushTable() {
        if (table.length < 2) {
          for (const row of table) para.push(row);
          table = [];
          return;
        }
        const rows = table.map((line) => line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
        const sep = rows[1].every((c) => /^:?-{3,}:?$/.test(c));
        if (!sep) {
          for (const row of table) para.push(row);
          table = [];
          return;
        }
        const head = rows[0].map((c) => `<th>${renderInlineMarkdown(c)}</th>`).join("");
        const body = rows.slice(2).map((r) => `<tr>${r.map((c) => `<td>${renderInlineMarkdown(c)}</td>`).join("")}</tr>`).join("");
        blocks.push(`<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`);
        table = [];
      }
      function flushAll() {
        flushTable();
        flushList();
        flushOrderedList();
        flushPara();
      }

      for (const line of src.split("\n")) {
        const fence = line.match(/^```(.*)$/);
        if (fence) {
          if (inCode) {
            blocks.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
            inCode = false;
            codeLang = "";
            codeLines = [];
          } else {
            flushAll();
            inCode = true;
            codeLang = fence[1].trim();
          }
          continue;
        }
        if (inCode) {
          codeLines.push(line);
          continue;
        }
        if (!line.trim()) {
          flushAll();
          continue;
        }
        if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
          flushAll();
          blocks.push("<hr>");
          continue;
        }
        if (/^\|.+\|$/.test(line.trim())) {
          flushList();
          flushOrderedList();
          flushPara();
          table.push(line);
          continue;
        }
        flushTable();
        const heading = line.match(/^(#{1,3})\s+(.+)$/);
        if (heading) {
          flushList();
          flushOrderedList();
          flushPara();
          const level = heading[1].length;
          blocks.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
          continue;
        }
        const bullet = line.match(/^\s*[-*]\s+(.+)$/);
        if (bullet) {
          flushOrderedList();
          flushPara();
          list.push(bullet[1]);
          continue;
        }
        const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
        if (ordered) {
          flushList();
          flushPara();
          orderedList.push(ordered[1]);
          continue;
        }
        const quote = line.match(/^>\s?(.+)$/);
        if (quote) {
          flushList();
          flushOrderedList();
          flushPara();
          blocks.push(`<blockquote>${renderInlineMarkdown(quote[1])}</blockquote>`);
          continue;
        }
        flushList();
        flushOrderedList();
        para.push(line.trim());
      }
      if (inCode) blocks.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
      flushAll();
      return blocks.join("") || "<p></p>";
    }

    function setMessageContent(node, text, options = {}) {
      const content = node.querySelector(".content");
      if (!content) return;
      const trace = Array.isArray(options.trace) && options.trace.length
        ? `<div class="process">Process:\n${escapeHtml(options.trace.join("\n"))}</div>`
        : "";
      if (options.markdown) {
        content.className = "content markdown";
        content.innerHTML = `${trace}${renderMarkdown(text)}`;
      } else {
        content.className = "content plain";
        content.textContent = text;
      }
    }

    function setAssistantStreamContent(node, toolLines, answer) {
      const content = node.querySelector(".content");
      if (!content) return;
      content.className = "content markdown";
      const trace = toolLines.length ? `<div class="process">Process:\n${escapeHtml(toolLines.join("\n"))}</div>` : "";
      content.innerHTML = `${trace}${renderMarkdown(answer || "Thinking...")}`;
    }

    function addMessage(role, text, className, options = {}) {
      const node = document.createElement("div");
      node.className = `msg ${className || role}`;
      const label = document.createElement("span");
      label.className = "role";
      label.textContent = role;
      node.appendChild(label);
      const content = document.createElement("div");
      content.className = "content plain";
      node.appendChild(content);
      messages.appendChild(node);
      setMessageContent(node, text, { markdown: role === "assistant", trace: options.trace || [] });
      node.scrollIntoView({ behavior: "smooth", block: "end" });
      return node;
    }

    function clearMessages() {
      messages.innerHTML = "";
    }

    function renderMessages(items) {
      clearMessages();
      if (!items || !items.length) {
        addMessage("assistant", "Clawd Web is running. Continue this session by sending a message.", "assistant");
        return;
      }
      for (const item of items) {
        addMessage(item.role, item.content, item.role, { trace: item.trace || [] });
      }
    }

    async function api(path, payload) {
      const res = await fetch(path, {
        method: payload ? "POST" : "GET",
        headers: payload ? { "Content-Type": "application/json" } : {},
        body: payload ? JSON.stringify(payload) : undefined,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
      return data;
    }

    async function refreshStatus() {
      const data = await api("/api/status");
      providerInfo = data.providers;
      providerEl.innerHTML = "";
      for (const [name, info] of Object.entries(data.providers)) {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = `${name} - ${info.label}`;
        providerEl.appendChild(option);
      }
      providerEl.value = data.default_provider;
      baseUrlEl.value = data.current.base_url || "";
      modelEl.value = data.current.default_model || "";
      statusEl.innerHTML = `
        <div><strong>Provider:</strong> ${data.default_provider}</div>
        <div><strong>Model:</strong> ${data.current.default_model || "unset"}</div>
        <div><strong>API key:</strong> ${data.current.has_api_key ? "configured" : "not configured"}</div>
        <div><strong>Mode:</strong> ${data.current.has_api_key ? "LLM chat enabled" : "local route fallback"}</div>
      `;
    }

    providerEl.addEventListener("change", () => {
      const info = providerInfo[providerEl.value];
      if (!info) return;
      baseUrlEl.value = info.default_base_url || "";
      modelEl.value = info.default_model || "";
    });

    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        mode = tab.dataset.mode;
        document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
        document.getElementById("modePill").textContent = mode;
        promptEl.placeholder = mode === "chat"
          ? "Ask Clawd Code something..."
          : mode === "route"
            ? "Enter a prompt to route across Clawd commands and tools..."
            : "Enter a prompt to bootstrap a local runtime session...";
      });
    });

    document.getElementById("saveConfig").addEventListener("click", async () => {
      configMsg.textContent = "Saving...";
      try {
        await api("/api/config", {
          provider: providerEl.value,
          api_key: apiKeyEl.value,
          base_url: baseUrlEl.value,
          default_model: modelEl.value,
        });
        apiKeyEl.value = "";
        configMsg.textContent = "Saved.";
        await refreshStatus();
      } catch (err) {
        configMsg.innerHTML = `<span class="error">${err.message}</span>`;
      }
    });

    document.getElementById("refresh").addEventListener("click", refreshStatus);
    document.getElementById("clearBtn").addEventListener("click", clearMessages);
    document.getElementById("refreshSessions").addEventListener("click", loadSessions);
    openSettingsBtn.addEventListener("click", openSettings);
    closeSettingsBtn.addEventListener("click", closeSettings);
    settingsModal.addEventListener("click", (event) => {
      if (event.target === settingsModal) closeSettings();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeSettings();
    });
    document.getElementById("newSession").addEventListener("click", async () => {
      sessionId = crypto.randomUUID();
      localStorage.setItem("clawd_session_id", sessionId);
      clearMessages();
      telemetryState = null;
      liveEvents = [];
      renderDataLog(null);
      addMessage("assistant", "Started a new session.", "assistant");
      try {
        await api("/api/session/new", { session_id: sessionId });
        await loadSessions();
      } catch (err) {
        configMsg.innerHTML = `<span class="error">${err.message}</span>`;
      }
    });
    document.getElementById("summaryBtn").addEventListener("click", async () => {
      const node = addMessage("assistant", "Loading summary...", "assistant");
      try {
        const data = await api("/api/summary");
        setMessageContent(node, data.text, { markdown: true });
      } catch (err) {
        setMessageContent(node, err.message, { markdown: false });
      }
    });

    async function loadSessions() {
      const data = await api("/api/sessions");
      sessionListEl.innerHTML = "";
      for (const session of data.sessions || []) {
        const btn = document.createElement("button");
        btn.className = `session-item ${session.session_id === sessionId ? "active" : ""}`;
        const title = document.createElement("span");
        title.textContent = session.title || session.session_id;
        const meta = document.createElement("span");
        meta.className = "session-meta";
        meta.textContent = `${session.model || "model"} · ${session.message_count || 0} messages`;
        btn.appendChild(title);
        btn.appendChild(meta);
        btn.addEventListener("click", () => loadSession(session.session_id));
        sessionListEl.appendChild(btn);
      }
    }

    async function loadSession(id) {
      const data = await api(`/api/session?session_id=${encodeURIComponent(id)}`);
      sessionId = data.session_id;
      localStorage.setItem("clawd_session_id", sessionId);
      renderMessages(data.messages || []);
      telemetryState = data.telemetry || null;
      renderDataLog(telemetryState);
      await loadSessions();
    }

    async function send() {
      const prompt = promptEl.value.trim();
      if (!prompt) return;
      promptEl.value = "";
      addMessage("user", prompt, "user");
      const node = addMessage("assistant", "Working...", "assistant");
      try {
        if (mode === "chat") {
          await streamChat(prompt, node);
        } else {
          const data = await api(`/api/${mode}`, { prompt, session_id: sessionId });
          setMessageContent(node, data.text, { markdown: true });
          telemetryState = null;
          renderDataLog(null);
        }
      } catch (err) {
        setMessageContent(node, err.message, { markdown: false });
        node.classList.add("error");
      }
    }

    async function streamChat(prompt, node) {
      const res = await fetch("/api/chat_stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt, session_id: sessionId }),
      });
      if (!res.ok || !res.body) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || `${res.status} ${res.statusText}`);
      }

      const decoder = new TextDecoder();
      const reader = res.body.getReader();
      let buffer = "";
      let toolLines = [];
      let answer = "";
      setAssistantStreamContent(node, toolLines, "Thinking...");

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (!line.trim()) continue;
          const event = JSON.parse(line);
          if (event.type === "tool_use") {
            toolLines.push(`→ ${event.tool}: ${event.preview || ""}`);
          } else if (event.type === "tool_result") {
            const detail = event.detail ? ` · ${event.detail}` : "";
            toolLines.push(`✓ ${event.tool}: ${event.status}${detail}`);
          } else if (event.type === "tool_error") {
            toolLines.push(`✗ ${event.tool}: ${event.error}`);
          } else if (event.type === "text_delta") {
            answer += event.text || "";
          } else if (event.type === "final") {
            answer = event.text || answer;
            if (event.trace && event.trace.length) {
              toolLines = event.trace;
            }
            if (event.telemetry) {
              telemetryState = event.telemetry;
              renderDataLog(telemetryState);
            }
          } else if (event.type === "error") {
            throw new Error(event.error || "stream failed");
          } else if (event.type === "telemetry") {
            telemetryState = event.telemetry || null;
            renderDataLog(telemetryState);
          }

          setAssistantStreamContent(node, toolLines, answer || "Thinking...");
          node.scrollIntoView({ behavior: "smooth", block: "end" });
        }
      }
    }

    document.getElementById("send").addEventListener("click", send);
    promptEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) send();
    });

    Promise.all([
      renderDataLog(null),
      refreshStatus().catch((err) => statusEl.textContent = err.message),
      loadSessions().catch((err) => configMsg.textContent = err.message),
      loadSession(sessionId).catch(() => {
        telemetryState = null;
        renderDataLog(null);
        addMessage("assistant", "Clawd Web is running. Configure an API key for live model chat, or use Route/Bootstrap for local runtime demos.", "assistant");
      }),
    ]);
  </script>
</body>
</html>
"""


def _json_response(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _ndjson_event(handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> None:
    body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    handler.wfile.write(body)
    handler.wfile.flush()


def _text_response(handler: BaseHTTPRequestHandler, text: str, content_type: str = "text/html; charset=utf-8") -> None:
    body = text.encode("utf-8")
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _output_file_path(url_path: str) -> Path:
    rel = unquote(url_path.removeprefix("/outputs/")).strip("/")
    if not rel:
        raise ValueError("output file path is required")
    path = (OUTPUTS_DIR / rel).resolve()
    if OUTPUTS_DIR.resolve() not in path.parents and path != OUTPUTS_DIR.resolve():
        raise ValueError("output path is outside allowed directory")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(rel)
    return path


def _file_response(handler: BaseHTTPRequestHandler, path: Path, *, head_only: bool = False) -> None:
    mime, _ = mimetypes.guess_type(str(path))
    if not mime:
        mime = "application/octet-stream"
    data = b"" if head_only else path.read_bytes()
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(path.stat().st_size))
    if path.suffix.lower() in {".pptx", ".xlsx", ".csv"}:
        handler.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
    handler.end_headers()
    if not head_only:
        handler.wfile.write(data)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def _authorize_runtime_request(handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> dict[str, Any]:
    session_id = str(payload.get("session_id") or "default")
    authorization = str(handler.headers.get("Authorization") or "")
    if authorization:
        claims = verify_runtime_authorization(authorization, session_id=session_id)
        _consume_runtime_token(claims)
        claims["_authorization"] = authorization
        return claims
    return local_runtime_context(session_id)


def _consume_runtime_token(claims: dict[str, Any]) -> None:
    jti = str(claims.get("jti") or "")
    expires_at = int(claims.get("exp") or 0)
    now = int(time.time())
    with RUNTIME_TOKEN_JTIS_LOCK:
        expired = [key for key, value in RUNTIME_TOKEN_JTIS.items() if value <= now]
        for key in expired:
            RUNTIME_TOKEN_JTIS.pop(key, None)
        if jti in RUNTIME_TOKEN_JTIS:
            raise RuntimeAuthError("runtime token replay detected")
        RUNTIME_TOKEN_JTIS[jti] = expires_at


def _apply_runtime_context(repl: ClawdREPL, payload: dict[str, Any]) -> None:
    claims = payload.get("_runtime_context")
    if not isinstance(claims, dict):
        claims = local_runtime_context(str(payload.get("session_id") or "default"))
    context = repl.tool_context
    context.tenant_id = str(claims.get("tenant_id") or "")
    context.user_id = str(claims.get("user_id") or "")
    context.job_id = str(claims.get("job_id") or "") or None
    context.trace_id = str(claims.get("trace_id") or claims.get("jti") or "") or None
    context.runtime_authorization = str(claims.get("_authorization") or "") or None
    roles = claims.get("role_ids") if isinstance(claims.get("role_ids"), list) else []
    tools = claims.get("allowed_tools") if isinstance(claims.get("allowed_tools"), list) else []
    context.role_ids = tuple(str(item) for item in roles)
    context.allowed_tools = frozenset(str(item) for item in tools)
    context.data_scope = dict(claims.get("data_scope") or {}) if isinstance(claims.get("data_scope"), dict) else {}
    context.audit_events.clear()
    context.reset_cancel()


def _cancel_runtime_job(job_id: str) -> bool:
    with ACTIVE_RUN_CONTEXTS_LOCK:
        context = ACTIVE_RUN_CONTEXTS.get(job_id)
    if context is None:
        return False
    context.request_cancel()
    return True


def _status_payload() -> dict[str, Any]:
    provider = get_default_provider()
    config = _get_provider_config(provider)
    api_key = _effective_api_key(provider, config)
    return {
        "default_provider": provider,
        "providers": PROVIDER_INFO,
        "current": {
            "has_api_key": bool(api_key),
            "base_url": config.get("base_url", ""),
            "default_model": config.get("default_model", ""),
        },
        "agent_runtime": {
            "max_concurrent_runs": MAX_CONCURRENT_AGENT_RUNS,
            "active_runs": ACTIVE_AGENT_RUNS,
            "queue_timeout_seconds": MAX_AGENT_QUEUE_SECONDS,
            "session_locks": len(SESSION_LOCKS),
        },
    }


def _get_provider_config(provider: str) -> dict[str, Any]:
    try:
        return get_provider_config(provider)
    except ValueError:
        info = PROVIDER_INFO[provider]
        return {
            "api_key": "",
            "base_url": info["default_base_url"],
            "default_model": info["default_model"],
        }


def _effective_api_key(provider: str, config: dict[str, Any]) -> str:
    configured_key = str(config.get("api_key", "")).strip()
    if configured_key:
        return configured_key
    if provider in {"ark", "ark_responses"}:
        return os.getenv("ARK_API_KEY", "").strip()
    return ""


def _fallback_route_text(prompt: str) -> str:
    runtime = PortRuntime()
    matches = runtime.route_prompt(prompt, limit=8)
    if not matches:
        return "No local Clawd command or tool matches were found for this prompt."
    lines = [
        "No API key is configured, so Clawd Web used the local routing layer.",
        "",
        "Matched Clawd commands/tools:",
    ]
    lines.extend(
        f"- [{match.kind}] {match.name} (score {match.score}) - {match.source_hint}"
        for match in matches
    )
    return "\n".join(lines)


def _subjects_workspace() -> Path:
    workspace = Path(os.getenv("CLAWD_SUBJECTS_WORKSPACE") or "/home/zhaoyunpeng/Projects/SubjectsDetection")
    return workspace if workspace.exists() else Path.cwd()


def _session_dir() -> Path:
    path = Path.home() / ".clawd" / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _conversation_messages_for_ui(session: Session) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in session.conversation.messages:
        if message.role not in {"user", "assistant"}:
            continue
        if not isinstance(message.content, str):
            continue
        text = message.content.strip()
        if not text:
            continue
        item: dict[str, Any] = {"role": message.role, "content": text}
        trace = message.metadata.get("trace")
        if isinstance(trace, list) and trace:
            item["trace"] = [str(line) for line in trace if str(line).strip()]
        citations = message.metadata.get("citations")
        if isinstance(citations, list) and citations:
            item["citations"] = [citation for citation in citations if isinstance(citation, dict)]
        items.append(item)
    return items


def _session_telemetry(session: Session) -> dict[str, Any] | None:
    telemetry = session.metadata.get("telemetry")
    return telemetry if isinstance(telemetry, dict) else None


def _attach_trace_to_last_assistant_message(session: Session, trace_lines: list[str]) -> None:
    trace = [line for line in trace_lines if isinstance(line, str) and line.strip()]
    if not trace:
        return
    for message in reversed(session.conversation.messages):
        if message.role == "assistant" and isinstance(message.content, str) and message.content.strip():
            metadata = dict(message.metadata)
            metadata["trace"] = trace
            message.metadata = metadata
            return


def _save_session_with_trace(session: Session, trace_lines: list[str]) -> None:
    _attach_trace_to_last_assistant_message(session, trace_lines)
    session.save()


def _process_memory_stats() -> dict[str, Any]:
    try:
        import psutil  # type: ignore

        proc = psutil.Process(os.getpid())
        mem = proc.memory_info()
        vm = psutil.virtual_memory()
        return {
            "rss_bytes": mem.rss,
            "vms_bytes": mem.vms,
            "rss_mb": round(mem.rss / 1024 / 1024, 1),
            "vms_mb": round(mem.vms / 1024 / 1024, 1),
            "system_used_pct": round(vm.percent, 1),
            "peak_rss_mb": round(getattr(mem, "peak_wset", mem.rss) / 1024 / 1024, 1),
        }
    except Exception:
        rss_mb = 0.0
        vms_mb = 0.0
        try:
            with open("/proc/self/status", "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        rss_mb = round(float(line.split()[1]) / 1024, 1)
                    elif line.startswith("VmSize:"):
                        vms_mb = round(float(line.split()[1]) / 1024, 1)
        except Exception:
            pass
        return {"rss_mb": rss_mb, "vms_mb": vms_mb}


def _tool_output_counts(tool_events: list[ToolEvent]) -> dict[str, int]:
    counts = {"tool_uses": 0, "tool_results": 0, "tool_errors": 0, "sql_rows": 0, "files": 0}
    for event in tool_events:
        if event.kind == "tool_use":
            counts["tool_uses"] += 1
        elif event.kind == "tool_result":
            counts["tool_results"] += 1
            if isinstance(event.tool_output, dict):
                row_count = event.tool_output.get("row_count")
                if isinstance(row_count, int):
                    counts["sql_rows"] += row_count
                file_path = event.tool_output.get("file_path")
                if isinstance(file_path, str) and file_path.strip():
                    counts["files"] += 1
        elif event.kind == "tool_error":
            counts["tool_errors"] += 1
    return counts


def _tool_trace_lines(events: list[ToolEvent]) -> list[str]:
    lines: list[str] = []
    for event in events:
        if event.kind == "tool_use":
            preview = json.dumps(event.tool_input or {}, ensure_ascii=False)
            if len(preview) > 240:
                preview = preview[:237] + "..."
            lines.append(f"→ {event.tool_name}: {preview}")
        elif event.kind == "tool_result":
            status = "error" if event.is_error else "ok"
            detail = ""
            if event.is_error:
                if isinstance(event.tool_output, dict):
                    detail = str(event.tool_output.get("error") or event.tool_output.get("message") or "")
                detail = detail or event.error or ""
            else:
                detail = summarize_tool_result(event.tool_name, event.tool_output)
                prefix = f"{event.tool_name} · "
                if isinstance(detail, str) and detail.startswith(prefix):
                    detail = detail[len(prefix):]
            lines.append(f"✓ {event.tool_name}: {status}{(' · ' + detail) if detail else ''}")
        elif event.kind == "tool_error":
            lines.append(f"✗ {event.tool_name}: {event.error}")
    return lines


def _store_session_telemetry(session: Session, telemetry: dict[str, Any]) -> None:
    session.metadata = dict(session.metadata)
    session.metadata["telemetry"] = telemetry


def _build_telemetry(
    *,
    phase: str,
    session: Session,
    provider: str,
    model: str,
    prompt_tokens: int,
    trace_lines: list[str],
    tool_stats: dict[str, int],
    flow_events: list[dict[str, Any]] | None = None,
    current_tool: str | None = None,
    current_tool_detail: str | None = None,
    usage: dict[str, Any] | None = None,
    response_chars: int | None = None,
    max_turns_reached: bool | None = None,
    elapsed_ms: int | None = None,
    citations: list[dict[str, Any]] | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _flow_snapshot(
        phase=phase,
        session=session,
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        trace_lines=trace_lines,
        tool_stats=tool_stats,
        flow_events=flow_events or [],
        current_tool=current_tool,
        current_tool_detail=current_tool_detail,
        usage=usage,
        response_chars=response_chars,
        max_turns_reached=max_turns_reached,
        elapsed_ms=elapsed_ms,
        citations=citations or [],
        extra_metadata=extra_metadata or {},
    )


def _flow_snapshot(
    *,
    phase: str,
    session: Session,
    provider: str,
    model: str,
    prompt_tokens: int,
    trace_lines: list[str],
    tool_stats: dict[str, int],
    flow_events: list[dict[str, Any]],
    current_tool: str | None = None,
    current_tool_detail: str | None = None,
    usage: dict[str, Any] | None = None,
    response_chars: int | None = None,
    max_turns_reached: bool | None = None,
    elapsed_ms: int | None = None,
    citations: list[dict[str, Any]] | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tool_nodes = {
        "sql": {"label": "SQL / 数据源", "detail": "rows / schema / filters"},
        "chart": {"label": "图表生成", "detail": "PNG / 图像"},
        "ppt": {"label": "PPT 生成", "detail": "PPTX / 单页结论"},
        "files": {"label": "文件系统", "detail": "read / write / export"},
        "vector": {"label": "向量化", "detail": "当前流未使用"},
    }
    current_tool_lower = (current_tool or "").lower()
    tool_node_key = "files"
    if "sql" in current_tool_lower:
        tool_node_key = "sql"
    elif "chart" in current_tool_lower:
        tool_node_key = "chart"
    elif "ppt" in current_tool_lower:
        tool_node_key = "ppt"
    elif current_tool_lower in {"read", "write", "edit", "bash"}:
        tool_node_key = "files"

    tool_label = current_tool or "工具"
    tool_detail = current_tool_detail or ""

    path_map = {
        "ingest": ["user", "memory", "prompt", "llm"],
        "tool_use": ["llm", "planner", "registry", "tool"],
        "tool_result": ["tool", "llm"],
        "stream": ["llm", "answer"],
        "final": ["llm", "answer", "save"],
        "error": ["llm", "answer"],
    }
    active_path = path_map.get(phase, ["user", "memory", "prompt", "llm"])

    nodes = [
        {"id": "user", "label": "用户输入", "detail": "当前问题", "status": "done" if phase != "ingest" else "active"},
        {"id": "memory", "label": "会话记忆", "detail": f"{len(session.conversation.messages)} messages", "status": "done" if phase != "ingest" else "active"},
        {"id": "prompt", "label": "Prompt 组装", "detail": "system + history + user", "status": "active" if phase in {"ingest", "tool_use"} else "done"},
        {"id": "vector", **tool_nodes["vector"], "status": "disabled"},
        {"id": "llm", "label": model, "detail": f"provider={provider}", "status": "active" if phase in {"ingest", "tool_use", "tool_result", "stream"} else "done"},
        {"id": "planner", "label": "工具规划", "detail": "LLM decides next action", "status": "active" if phase == "tool_use" else "done"},
        {"id": "registry", "label": "工具注册表", "detail": "schema validation", "status": "active" if phase == "tool_use" else "done"},
        {"id": "tool", "label": tool_label, "detail": tool_detail or tool_nodes.get(tool_node_key, {}).get("detail", ""), "status": "active" if phase in {"tool_use", "tool_result"} else "idle"},
        {"id": "answer", "label": "回答输出", "detail": "streamed text", "status": "active" if phase in {"stream", "final"} else "idle"},
        {"id": "save", "label": "会话落盘", "detail": "JSON session", "status": "active" if phase == "final" else "idle"},
    ]
    for node in nodes:
        if node["id"] in active_path:
            if phase == "tool_result" and node["id"] == "tool":
                node["status"] = "done"
            elif phase == "final" and node["id"] in {"answer", "save"}:
                node["status"] = "done"
            else:
                node["status"] = "active" if node["id"] == active_path[-1] else "done"

    edges = [
        {"from": "user", "to": "memory", "label": "append prompt"},
        {"from": "memory", "to": "prompt", "label": "history + context"},
        {"from": "prompt", "to": "vector", "label": "no embedding path", "status": "disabled"},
        {"from": "vector", "to": "llm", "label": "direct text prompt"},
        {"from": "llm", "to": "planner", "label": "plan tools"},
        {"from": "planner", "to": "registry", "label": "validate schema"},
        {"from": "registry", "to": "tool", "label": tool_label},
        {"from": "tool", "to": "llm", "label": "structured result"},
        {"from": "llm", "to": "answer", "label": "token stream"},
        {"from": "answer", "to": "save", "label": "persist"},
    ]

    if usage is None:
        usage = {}

    conversation_tokens = count_messages_tokens(session.conversation.get_messages())
    provider_input_tokens = int(usage.get("input_tokens") or 0)
    provider_output_tokens = int(usage.get("output_tokens") or 0)
    provider_total_tokens = int(usage.get("total_tokens") or 0)
    estimated_output_tokens = max(0, int((response_chars or 0) / 3.2))
    input_tokens = provider_input_tokens or prompt_tokens or conversation_tokens
    output_tokens = provider_output_tokens or estimated_output_tokens
    total_tokens = provider_total_tokens or (input_tokens + output_tokens)
    token_source = "provider" if provider_input_tokens or provider_output_tokens or provider_total_tokens else "estimated"
    memory = _process_memory_stats()
    stats = {
        "turns": tool_stats.get("tool_uses", 0),
        "tool_uses": tool_stats.get("tool_uses", 0),
        "tool_results": tool_stats.get("tool_results", 0),
        "tool_errors": tool_stats.get("tool_errors", 0),
        "sql_rows": tool_stats.get("sql_rows", 0),
        "files": tool_stats.get("files", 0),
        "prompt_tokens_est": prompt_tokens,
        "conversation_tokens_est": conversation_tokens,
        "usage_input_tokens": input_tokens,
        "usage_output_tokens": output_tokens,
        "usage_total_tokens": total_tokens,
        "provider_input_tokens": provider_input_tokens,
        "provider_output_tokens": provider_output_tokens,
        "provider_total_tokens": provider_total_tokens,
        "estimated_input_tokens": prompt_tokens or conversation_tokens,
        "estimated_output_tokens": estimated_output_tokens,
        "token_source": token_source,
        "memory_rss_mb": memory.get("rss_mb"),
        "memory_vms_mb": memory.get("vms_mb"),
        "memory_peak_rss_mb": memory.get("peak_rss_mb"),
        "elapsed_ms": elapsed_ms,
        "response_chars": response_chars,
        "max_turns_reached": max_turns_reached,
        "citation_count": len(citations or []),
    }
    tool_uses = max(1, int(tool_stats.get("tool_uses") or 0))
    tool_results = int(tool_stats.get("tool_results") or 0)
    sql_rows = int(tool_stats.get("sql_rows") or 0)
    interview_metrics = [
        {"label": "样本量", "value": str(sql_rows) if sql_rows else "n/a", "hint": "rows touched"},
        {"label": "证据数", "value": str(tool_results), "hint": "successful tool results"},
        {"label": "引用源", "value": str(len(citations or [])), "hint": "Harness citations"},
        {"label": "错误数", "value": str(tool_stats.get("tool_errors") or 0), "hint": "tool failures"},
        {"label": "轮次", "value": str(tool_stats.get("tool_uses") or 0), "hint": "tool turns"},
        {"label": "工具成功率", "value": f"{(tool_results / tool_uses) * 100:.0f}%", "hint": "results / uses"},
        {"label": "结果密度", "value": f"{(sql_rows / max(1, tool_results)):.1f}", "hint": "rows / successful result"},
        {"label": "输入 tokens", "value": f"{input_tokens:,}", "hint": token_source},
        {"label": "输出 tokens", "value": f"{output_tokens:,}", "hint": token_source},
        {"label": "总 tokens", "value": f"{total_tokens:,}", "hint": token_source},
        {"label": "RSS 内存", "value": f"{memory.get('rss_mb', 0):.1f} MB" if memory.get("rss_mb") is not None else "n/a", "hint": "current process"},
        {"label": "峰值 RSS", "value": f"{memory.get('peak_rss_mb', 0):.1f} MB" if memory.get("peak_rss_mb") is not None else "n/a", "hint": "peak process"},
        {"label": "超轮次", "value": "是" if max_turns_reached else "否", "hint": "max-turn cutoff"},
        {"label": "向量化", "value": "未使用", "hint": "current flow has no embedding path"},
    ]
    architecture_notes = [
        "当前流没有向量化/RAG分支，数据直接以结构化工具结果回流给 LLM。",
        "SQL/图表/PPT 结果都作为工具输出进入下轮 LLM，而不是先做 embedding。",
    ]
    snapshot = {
        "phase": phase,
        "current_tool": current_tool,
        "current_tool_detail": tool_detail,
        "nodes": nodes,
        "edges": edges,
        "stats": stats,
        "interview_metrics": interview_metrics,
        "architecture_notes": architecture_notes,
        "trace": trace_lines,
        "flow_events": flow_events,
        "memory": memory,
        "citations": citations or [],
        "sources": citations or [],
    }
    if extra_metadata:
        snapshot.update({key: value for key, value in extra_metadata.items() if value is not None})
    return snapshot


def _session_title(session: Session) -> str:
    for message in session.conversation.messages:
        if message.role == "user" and isinstance(message.content, str) and message.content.strip():
            title = message.content.strip().replace("\n", " ")
            return title[:60]
    return "New session"


def _list_sessions() -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    for file in sorted(_session_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except Exception:
            continue
        conversation = data.get("conversation") or {}
        message_count = len(conversation.get("messages") or [])
        sessions.append({
            "session_id": data.get("session_id") or file.stem,
            "provider": data.get("provider", ""),
            "model": data.get("model", ""),
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at", ""),
            "message_count": message_count,
            "title": _session_title(Session.load(data.get("session_id") or file.stem) or Session(data.get("session_id") or file.stem, "", "")),
        })
    return sessions


def _load_session_payload(session_id: str) -> dict[str, Any]:
    provider_name = get_default_provider()
    config = _get_provider_config(provider_name)
    if not _effective_api_key(provider_name, config):
        session = Session.load(session_id) or Session(
            session_id=session_id,
            provider=provider_name,
            model=str(config.get("default_model") or ""),
        )
        return {
            "session_id": session_id,
            "provider": session.provider,
            "model": session.model,
            "messages": _conversation_messages_for_ui(session),
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "telemetry": _session_telemetry(session),
        }
    repl = _get_web_repl(session_id)
    return {
        "session_id": session_id,
        "provider": repl.session.provider,
        "model": repl.session.model,
        "messages": _conversation_messages_for_ui(repl.session),
        "created_at": repl.session.created_at,
        "updated_at": repl.session.updated_at,
        "telemetry": _session_telemetry(repl.session),
    }


def _get_web_repl(session_id: str) -> ClawdREPL:
    provider_name = get_default_provider()
    repl = WEB_REPLS.get(session_id)
    if repl is not None and repl.provider_name == provider_name:
        return repl

    LOGGER.info("create_repl session=%s provider=%s workspace=%s", session_id, provider_name, _subjects_workspace())
    repl = ClawdREPL(provider_name=provider_name, stream=True)
    loaded = Session.load(session_id)
    if loaded is not None and loaded.provider == provider_name:
        repl.session = loaded
        LOGGER.info("load_session session=%s messages=%d", session_id, len(loaded.conversation.messages))
    else:
        repl.session.session_id = session_id
        repl.session.provider = provider_name
        repl.session.model = repl.provider.model
    workspace = _subjects_workspace()
    repl.tool_context.workspace_root = workspace.resolve()
    repl.tool_context.cwd = workspace.resolve()

    def _web_permission_handler(tool_name: str, message: str, suggestion: str | None) -> tuple[bool, bool]:
        # Web has no blocking permission prompt yet. Read-only tools proceed through
        # normal registry metadata; tools that ask for permission are denied.
        return False, False

    repl.tool_context.permission_handler = _web_permission_handler
    WEB_REPLS[session_id] = repl
    return repl


def _chat(payload: dict[str, Any]) -> dict[str, Any]:
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("Prompt is required.")
    session_id = str(payload.get("session_id") or "default")

    provider_name = get_default_provider()
    config = _get_provider_config(provider_name)
    api_key = _effective_api_key(provider_name, config)
    if not api_key:
        session = Session.load(session_id) or Session(
            session_id=session_id,
            provider=provider_name,
            model=str(config.get("default_model") or ""),
        )
        session.conversation.add_user_message(prompt)
        fallback_text = _fallback_route_text(prompt)
        session.conversation.add_assistant_message(fallback_text, metadata={"mode": "route_fallback"})
        telemetry = _build_telemetry(
            phase="final",
            session=session,
            provider=provider_name,
            model=str(config.get("default_model") or ""),
            prompt_tokens=count_messages_tokens(session.conversation.get_messages()),
            trace_lines=[],
            tool_stats={},
            usage=None,
            response_chars=len(fallback_text),
            max_turns_reached=False,
            elapsed_ms=0,
        )
        _store_session_telemetry(session, telemetry)
        _save_session_with_trace(session, [])
        return {"text": fallback_text, "mode": "route_fallback", "telemetry": telemetry}

    repl = _get_web_repl(session_id)
    _apply_runtime_context(repl, payload)
    repl.session.conversation.add_user_message(prompt)

    tool_events: list[ToolEvent] = []
    started_at = time.perf_counter()
    deterministic = _try_deterministic_workflow(prompt, repl, tool_events.append)
    if deterministic is not None:
        response_content = str(deterministic["text"])
        trace_lines = _tool_trace_lines(tool_events)
        telemetry = _build_telemetry(
            phase="final",
            session=repl.session,
            provider=repl.provider_name,
            model=repl.provider.model,
            prompt_tokens=count_messages_tokens(repl.session.conversation.get_messages()),
            trace_lines=trace_lines,
            tool_stats=_tool_output_counts(tool_events),
            usage=deterministic["usage"],
            response_chars=len(response_content),
            max_turns_reached=False,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            citations=deterministic["citations"],
            extra_metadata={
                "route": deterministic["route"],
                "route_policy_version": "task_router_l0_v1",
                "termination_reason": deterministic["termination_reason"],
                "run_state": {},
                "tool_scheduler_ledger": deterministic.get("tool_scheduler_ledger") or {},
                "run_budget": deterministic.get("run_budget") or {},
                "route_decision": deterministic.get("route_decision") or {},
                "model_tier": deterministic.get("model_tier") or "",
                "budget_class": deterministic.get("budget_class") or "",
                "model_routing": deterministic.get("model_routing") or {},
            },
        )
        _store_session_telemetry(repl.session, telemetry)
        _save_session_with_trace(repl.session, trace_lines)
        return {
            "text": response_content,
            "mode": "deterministic_tool_plan_single_shot_synthesis",
            "model": repl.provider.model,
            "usage": deterministic["usage"],
            "num_turns": 0,
            "citations": deterministic["citations"],
            "claims": [],
            "evidence_status": "supported" if deterministic["citations"] else "not_applicable",
            "route": deterministic["route"],
            "route_policy_version": "task_router_l0_v1",
            "output_contract_status": "not_required",
            "task_contract_status": deterministic["task_contract_status"],
            "requirements": [],
            "termination_reason": deterministic["termination_reason"],
            "run_state": {},
            "tool_scheduler_ledger": deterministic.get("tool_scheduler_ledger") or {},
            "run_budget": deterministic.get("run_budget") or {},
            "route_decision": deterministic.get("route_decision") or {},
            "model_tier": deterministic.get("model_tier") or "",
            "budget_class": deterministic.get("budget_class") or "",
            "model_routing": deterministic.get("model_routing") or {},
            "tool_audit": list(repl.tool_context.audit_events),
            "sources": deterministic["citations"],
            "telemetry": telemetry,
        }

    result = run_agent_loop(
        conversation=repl.session.conversation,
        provider=repl.provider,
        tool_registry=repl.tool_registry,
        tool_context=repl.tool_context,
        max_turns=20,
        stream=False,
        on_event=tool_events.append,
    )
    response_content = result.response_text
    trace_lines = _tool_trace_lines(tool_events)
    telemetry = _build_telemetry(
        phase="final",
        session=repl.session,
        provider=repl.provider_name,
        model=repl.provider.model,
        prompt_tokens=count_messages_tokens(repl.session.conversation.get_messages()),
        trace_lines=trace_lines,
        tool_stats=_tool_output_counts(tool_events),
        usage=result.usage,
        response_chars=len(response_content),
        max_turns_reached=response_content.strip() == "[Max tool turns reached]",
        elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        citations=result.citations or [],
        extra_metadata={
            "route": result.route,
            "route_policy_version": result.route_policy_version,
            "termination_reason": result.termination_reason,
            "run_state": result.run_state or {},
            "tool_scheduler_ledger": result.tool_scheduler_ledger or {},
            "run_budget": result.run_budget or {},
            "route_decision": result.route_decision or {},
            "model_tier": result.model_tier,
            "budget_class": result.budget_class,
            "model_routing": result.model_routing or {},
        },
    )
    _store_session_telemetry(repl.session, telemetry)
    _save_session_with_trace(repl.session, trace_lines)
    LOGGER.info("save_session session=%s messages=%d", repl.session.session_id, len(repl.session.conversation.messages))
    trace_text = "\n".join(trace_lines)
    text = response_content if not trace_text else f"{response_content}\n\n---\nTool trace:\n{trace_text}"
    return {
        "text": text,
        "mode": "cli_agent_loop",
        "model": repl.provider.model,
        "usage": result.usage,
        "num_turns": result.num_turns,
        "citations": result.citations or [],
        "claims": result.claims or [],
        "evidence_status": result.evidence_status,
        "route": result.route,
        "route_policy_version": result.route_policy_version,
        "output_contract_status": result.output_contract_status,
        "task_contract_status": result.task_contract_status,
        "requirements": result.requirements or [],
        "termination_reason": result.termination_reason,
        "run_state": result.run_state or {},
        "tool_scheduler_ledger": result.tool_scheduler_ledger or {},
        "run_budget": result.run_budget or {},
        "route_decision": result.route_decision or {},
        "model_tier": result.model_tier,
        "budget_class": result.budget_class,
        "model_routing": result.model_routing or {},
        "tool_audit": list(repl.tool_context.audit_events),
        "sources": result.citations or [],
        "telemetry": telemetry,
    }


def _try_deterministic_workflow(
    prompt: str,
    repl: ClawdREPL,
    on_event: ToolEventHandler | None = None,
) -> dict[str, Any] | None:
    route = classify_l0(prompt)
    repl.tool_context.audit_events.append(
        {
            "event": "task_router_l0_classified",
            "job_id": repl.tool_context.job_id,
            "trace_id": repl.tool_context.trace_id,
            **route.as_dict(),
        }
    )
    if not route.deterministic:
        return None
    if route.task_type not in {"single_vehicle_attribute_query", "field_catalog_query", "cohort_attribute_query", "vehicle_attribute_stats"}:
        return None

    calls: list[ToolCall] = []
    if route.task_type == "vehicle_attribute_stats":
        calls.append(
            ToolCall(
                name="SubjectsAttributeStats",
                input={"attribute_keywords": list(route.attributes), "entity_keyword": "*", "sample_limit": 5},
                tool_use_id="deterministic_stats_1",
            )
        )
    else:
        for index, attribute in enumerate(route.attributes):
            tool_input: dict[str, Any] = {"attribute_keyword": attribute, "limit": 50}
            if route.task_type == "cohort_attribute_query" and route.entities:
                tool_input["entity_keyword"] = "*"
                tool_input["filter_value_keyword"] = route.entities[0]
                tool_input["limit"] = 20
            elif route.entities:
                tool_input["entity_keyword"] = route.entities[0]
                tool_input["limit"] = 10
            calls.append(ToolCall(name="SubjectsAttributeLookup", input=tool_input, tool_use_id=f"deterministic_{index + 1}"))

    for call in calls:
        if on_event is not None:
            on_event(ToolEvent(kind="tool_use", tool_name=call.name, tool_input=call.input, tool_use_id=call.tool_use_id))

    scheduled = ToolCallScheduler(repl.tool_registry, repl.tool_context).execute(
        calls,
        mode="deterministic_workflow",
        allow_parallel=True,
        max_workers=4,
        dedupe_scope="run",
    )

    results = []
    for item in scheduled:
        call = item.call
        result = item.result
        results.append(result)
        if on_event is not None:
            on_event(
                ToolEvent(
                    kind="tool_result",
                    tool_name=call.name,
                    tool_input=call.input,
                    tool_output=result.output,
                    tool_use_id=call.tool_use_id,
                    is_error=result.is_error,
                    outcome_status=getattr(result.outcome_status, "value", str(result.outcome_status)),
                    reason_code=result.reason_code,
                    retryable=result.retryable,
                )
            )

    answer = build_subjects_stats_answer(route, results) if route.task_type == "vehicle_attribute_stats" else build_subjects_lookup_answer(route, results)
    if route.task_type == "vehicle_attribute_stats":
        response_text = answer["text"]
        usage = {"input_tokens": 0, "output_tokens": 0}
        synthesis = {"model_routing": {"model_override": ""}}
    else:
        synthesis = _synthesize_deterministic_workflow_answer(prompt, route.as_dict(), results, answer, repl)
        response_text = synthesis["text"]
        usage = synthesis["usage"]
    route_decision = decide_route(prompt)
    metadata = {
        "mode": "deterministic_tool_plan_single_shot_synthesis",
        "route": route.task_type,
        "task_router": route.as_dict(),
        "route_decision": route_decision.as_dict(),
        "model_tier": route_decision.model_tier,
        "budget_class": route_decision.budget_class,
        "model_routing": {
            "requested_tier": route_decision.model_tier,
            "model_override": (synthesis.get("model_routing") or {}).get("model_override", ""),
            "provider_default_model": repl.provider.model,
        },
        "citations": answer["citations"],
        "task_contract_status": answer["task_contract_status"],
        "termination_reason": answer["termination_reason"],
        "tool_scheduler_ledger": repl.tool_context.runtime_state.get("tool_scheduler_ledger") or {},
        "run_budget": {
            "usage": {
                "input_tokens": int(usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
                "total_tokens": int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0),
                "tokens_after_last_progress": 0,
                "model_turns": 0 if route.task_type == "vehicle_attribute_stats" else 1,
            },
            "tools": {
                "requested": len(calls),
                "dispatched": len([item for item in scheduled if item.dispatched]),
                "rejected": len([item for item in scheduled if not item.dispatched]),
                "low_yield_actions": len([item for item in scheduled if item.result.is_error]),
            },
        },
    }
    repl.session.conversation.add_assistant_message(response_text, metadata=metadata)
    return {
        "text": response_text,
        "usage": {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "tool_call_count": len(results),
        },
        "citations": answer["citations"],
        "route": route.task_type,
        "route_decision": route_decision.as_dict(),
        "model_tier": route_decision.model_tier,
        "budget_class": route_decision.budget_class,
        "model_routing": metadata["model_routing"],
        "task_contract_status": answer["task_contract_status"],
        "termination_reason": answer["termination_reason"],
        "tool_scheduler_ledger": repl.tool_context.runtime_state.get("tool_scheduler_ledger") or {},
        "run_budget": metadata["run_budget"],
    }


def _synthesize_deterministic_workflow_answer(
    prompt: str,
    route: dict[str, Any],
    results: list[Any],
    answer: dict[str, Any],
    repl: ClawdREPL,
) -> dict[str, Any]:
    observations = []
    for index, result in enumerate(results, start=1):
        output = result.output if isinstance(result.output, dict) else {}
        observations.append(
            {
                "citation_id": index,
                "tool": getattr(result, "name", "tool"),
                "outcome_status": getattr(getattr(result, "outcome_status", None), "value", str(getattr(result, "outcome_status", ""))),
                "reason_code": getattr(result, "reason_code", None),
                "entity_keyword": output.get("entity_keyword"),
                "attribute_keyword": output.get("attribute_keyword"),
                "row_count": output.get("row_count"),
                "filtered_entity_count": output.get("filtered_entity_count"),
                "filter_value_keyword": output.get("filter_value_keyword"),
                "attribute_candidates": _compact_items(output.get("attribute_candidates"), limit=3),
                "filter_attribute_candidates": _compact_items(output.get("filter_attribute_candidates"), limit=3),
                "rows": _compact_items(output.get("rows"), limit=5),
                "coverage_boundary": output.get("coverage_boundary"),
            }
        )
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个数据分析 Agent 的最终回答生成器。上游 runtime 已经完成低成本路由和工具调用。"
                "你的任务是基于给定 observation 给用户一个自然、直接、可读的答案。"
                "不要输出原始 JSON，不要说你不能回答后就停止；如果数据不足，要说明已验证到什么、缺口是什么、边界是什么。"
                "用中文回答，引用证据编号如 [1]。保持简洁。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "user_question": prompt,
                    "route": route,
                    "observations": observations,
                    "citation_count": len(answer.get("citations") or []),
                    "task_contract_status": answer.get("task_contract_status"),
                    "termination_reason": answer.get("termination_reason"),
                },
                ensure_ascii=False,
                default=str,
            ),
        },
    ]
    try:
        route_decision = decide_route(prompt)
        model_override = _model_override_for_tier(route_decision.model_tier)
        kwargs: dict[str, Any] = {"tools": None}
        if model_override:
            kwargs["model"] = model_override
        response = repl.provider.chat(messages, **kwargs)
        text = str(response.content or "").strip()
        if not text:
            raise RuntimeError("empty synthesis response")
        return {
            "text": text,
            "usage": response.usage or {},
            "model_routing": {
                "requested_tier": route_decision.model_tier,
                "model_override": model_override,
                "provider_default_model": repl.provider.model,
            },
        }
    except Exception as exc:
        LOGGER.warning("deterministic_synthesis_failed session=%s error=%s", repl.session.session_id, exc)
        # Last-resort fallback keeps the UI usable, but normal operation should
        # be one LLM synthesis pass over compact observations.
        return {"text": str(answer.get("text") or ""), "usage": {}, "model_routing": {}}


def _compact_items(value: Any, *, limit: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    compact: list[Any] = []
    for item in value[:limit]:
        if not isinstance(item, dict):
            compact.append(item)
            continue
        compact.append(
            {
                key: item.get(key)
                for key in (
                    "vehicle_name",
                    "attribute_id",
                    "attribute_code",
                    "attribute_name",
                    "attribute_unit",
                    "value_number",
                    "value_text",
                    "unit",
                    "matched_vehicle_count",
                    "matched_value_count",
                    "sample_value",
                    "covered_vehicle_count",
                    "populated_value_count",
                    "match_score",
                )
                if key in item
            }
        )
    return compact


def _model_override_for_tier(model_tier: str) -> str:
    tier = "".join(ch if ch.isalnum() else "_" for ch in str(model_tier or "").upper())
    return os.getenv(f"CLAWD_MODEL_TIER_{tier}_MODEL", "").strip() if tier else ""


def _preview_tool_input(tool_input: dict[str, Any] | None) -> str:
    preview = json.dumps(tool_input or {}, ensure_ascii=False)
    return preview if len(preview) <= 240 else preview[:237] + "..."


WEB_ALLOW_DIRECT_STREAM = False


def _preview_prompt(prompt: str) -> str:
    preview = " ".join(prompt.split())
    return preview if len(preview) <= 180 else preview[:177] + "..."


def _chat_stream(handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> None:
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("Prompt is required.")
    session_id = str(payload.get("session_id") or "default")

    if not _acquire_agent_run_slot():
        LOGGER.warning(
            "chat_stream_rejected session=%s reason=global_concurrency_limit limit=%d",
            session_id,
            MAX_CONCURRENT_AGENT_RUNS,
        )
        _json_response(
            handler,
            {
                "error": "Agent concurrency limit reached.",
                "retry_after_seconds": max(1, int(MAX_AGENT_QUEUE_SECONDS) or 3),
                "limit": MAX_CONCURRENT_AGENT_RUNS,
            },
            status=HTTPStatus.TOO_MANY_REQUESTS,
        )
        return

    session_lock = _get_session_lock(session_id)
    if not session_lock.acquire(blocking=False):
        _release_agent_run_slot()
        LOGGER.warning("chat_stream_rejected session=%s reason=session_busy", session_id)
        _json_response(
            handler,
            {
                "error": "This session already has an agent run in progress.",
                "retry_after_seconds": 2,
            },
            status=HTTPStatus.CONFLICT,
        )
        return

    try:
        _chat_stream_locked(handler, payload)
    finally:
        claims = payload.get("_runtime_context")
        job_id = str(claims.get("job_id") or "") if isinstance(claims, dict) else ""
        if job_id:
            with ACTIVE_RUN_CONTEXTS_LOCK:
                ACTIVE_RUN_CONTEXTS.pop(job_id, None)
        session_lock.release()
        _release_agent_run_slot()


def _chat_stream_locked(handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> None:
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("Prompt is required.")
    session_id = str(payload.get("session_id") or "default")

    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "close")
    handler.end_headers()

    provider_name = get_default_provider()
    config = _get_provider_config(provider_name)
    api_key = _effective_api_key(provider_name, config)
    LOGGER.info(
        "chat_stream_start session=%s provider=%s model=%s prompt_chars=%d prompt=%r",
        session_id,
        provider_name,
        config.get("default_model"),
        len(prompt),
        _preview_prompt(prompt),
    )
    if not api_key:
        session = Session.load(session_id) or Session(
            session_id=session_id,
            provider=provider_name,
            model=str(config.get("default_model") or ""),
        )
        session.conversation.add_user_message(prompt)
        text = _fallback_route_text(prompt)
        session.conversation.add_assistant_message(text, metadata={"mode": "route_fallback"})
        telemetry = _build_telemetry(
            phase="final",
            session=session,
            provider=provider_name,
            model=str(config.get("default_model") or ""),
            prompt_tokens=count_messages_tokens(session.conversation.get_messages()),
            trace_lines=[],
            tool_stats={},
            usage=None,
            response_chars=len(text),
            max_turns_reached=False,
            elapsed_ms=0,
        )
        _store_session_telemetry(session, telemetry)
        _save_session_with_trace(session, [])
        LOGGER.info("chat_stream_fallback session=%s reason=no_api_key", session_id)
        _ndjson_event(handler, {"type": "final", "text": text, "trace": [], "telemetry": telemetry, "mode": "route_fallback"})
        return

    repl = _get_web_repl(session_id)
    _apply_runtime_context(repl, payload)
    if repl.tool_context.job_id:
        with ACTIVE_RUN_CONTEXTS_LOCK:
            ACTIVE_RUN_CONTEXTS[repl.tool_context.job_id] = repl.tool_context
    repl.session.conversation.add_user_message(prompt)

    tool_events: list[ToolEvent] = []
    trace_lines: list[str] = []
    flow_events: list[dict[str, Any]] = []
    current_citations: list[dict[str, Any]] = []
    started_at = time.perf_counter()

    def add_flow_event(
        *,
        source: str,
        target: str,
        label: str,
        detail: str = "",
        status: str = "active",
        rows: int | None = None,
        tokens: int | None = None,
    ) -> None:
        item: dict[str, Any] = {
            "source": source,
            "target": target,
            "label": label,
            "detail": detail,
            "status": status,
            "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
        }
        if rows is not None:
            item["rows"] = rows
        if tokens is not None:
            item["tokens"] = tokens
        flow_events.append(item)
        if len(flow_events) > 80:
            del flow_events[:-80]

    def emit_telemetry(
        phase: str,
        *,
        current_tool: str | None = None,
        current_tool_detail: str | None = None,
        usage: dict[str, Any] | None = None,
        response_chars: int | None = None,
        max_turns_reached: bool | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        telemetry = _build_telemetry(
            phase=phase,
            session=repl.session,
            provider=repl.provider_name,
            model=repl.provider.model,
            prompt_tokens=count_messages_tokens(repl.session.conversation.get_messages()),
            trace_lines=trace_lines,
            tool_stats=_tool_output_counts(tool_events),
            flow_events=flow_events,
            current_tool=current_tool,
            current_tool_detail=current_tool_detail,
            usage=usage,
            response_chars=response_chars,
            max_turns_reached=max_turns_reached,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            citations=current_citations,
            extra_metadata=extra_metadata or {},
        )
        _store_session_telemetry(repl.session, telemetry)
        _ndjson_event(handler, {"type": "telemetry", "telemetry": telemetry})
        return telemetry

    add_flow_event(
        source="用户",
        target="会话记忆",
        label="写入用户输入",
        detail=_preview_prompt(prompt),
        status="ok",
    )
    add_flow_event(
        source="会话记忆",
        target="LLM",
        label="组装上下文并请求模型",
        detail=repl.provider.model,
        status="active",
        tokens=count_messages_tokens(repl.session.conversation.get_messages()),
    )
    emit_telemetry("ingest")

    def on_event(event: ToolEvent) -> None:
        tool_events.append(event)
        if event.kind == "tool_use":
            summary = summarize_tool_use(event.tool_name, event.tool_input or {})
            preview = summary if isinstance(summary, str) and summary else _preview_tool_input(event.tool_input)
            trace_lines.append(f"→ {event.tool_name}: {preview}")
            add_flow_event(
                source="LLM",
                target=event.tool_name,
                label="调用工具",
                detail=preview,
                status="active",
            )
            LOGGER.info("tool_use session=%s tool=%s input=%s", session_id, event.tool_name, _preview_tool_input(event.tool_input))
            _ndjson_event(handler, {"type": "tool_use", "tool": event.tool_name, "preview": preview})
            emit_telemetry("tool_use", current_tool=event.tool_name, current_tool_detail=preview)
        elif event.kind == "tool_result":
            status = "error" if event.is_error else "ok"
            detail = ""
            if event.is_error:
                if isinstance(event.tool_output, dict):
                    detail = str(event.tool_output.get("error") or event.tool_output.get("message") or "")
                detail = detail or event.error or ""
            else:
                detail = summarize_tool_result(event.tool_name, event.tool_output)
                prefix = f"{event.tool_name} · "
                if isinstance(detail, str) and detail.startswith(prefix):
                    detail = detail[len(prefix):]
            trace_lines.append(f"✓ {event.tool_name}: {status}{(' · ' + detail) if detail else ''}")
            rows = None
            if isinstance(event.tool_output, dict) and isinstance(event.tool_output.get("row_count"), int):
                rows = event.tool_output.get("row_count")
            add_flow_event(
                source=event.tool_name,
                target="LLM",
                label="工具结果回流",
                detail=detail or status,
                status="error" if event.is_error else "ok",
                rows=rows,
            )
            LOGGER.info(
                "tool_result session=%s tool=%s status=%s outcome=%s reason=%s retryable=%s detail=%s",
                session_id,
                event.tool_name,
                status,
                event.outcome_status,
                event.reason_code,
                event.retryable,
                detail,
            )
            _ndjson_event(
                handler,
                {
                    "type": "tool_result",
                    "tool": event.tool_name,
                    "status": status,
                    "outcome_status": event.outcome_status,
                    "reason_code": event.reason_code,
                    "retryable": event.retryable,
                    "detail": detail,
                },
            )
            emit_telemetry("tool_result", current_tool=event.tool_name, current_tool_detail=detail)
        elif event.kind == "tool_error":
            trace_lines.append(f"✗ {event.tool_name}: {event.error}")
            add_flow_event(
                source=event.tool_name,
                target="LLM",
                label="工具异常",
                detail=event.error or "",
                status="error",
            )
            LOGGER.warning("tool_error session=%s tool=%s error=%s", session_id, event.tool_name, event.error)
            _ndjson_event(handler, {"type": "tool_error", "tool": event.tool_name, "error": event.error})
            emit_telemetry("error", current_tool=event.tool_name, current_tool_detail=event.error)

    first_text_chunk = {"seen": False}

    def on_text_chunk(text: str) -> None:
        if text and not first_text_chunk["seen"]:
            first_text_chunk["seen"] = True
            add_flow_event(
                source="LLM",
                target="回答流",
                label="开始输出",
                detail="streaming text",
                status="output",
            )
            emit_telemetry("stream")
        _ndjson_event(handler, {"type": "text_delta", "text": text})

    deterministic = _try_deterministic_workflow(prompt, repl, on_event)
    if deterministic is not None:
        response_content = str(deterministic["text"])
        current_citations[:] = deterministic["citations"]
        add_flow_event(
            source="TaskRouter",
            target="固定 Workflow",
            label="L0 路由命中",
            detail=str(deterministic["route"]),
            status="ok",
        )
        add_flow_event(
            source="固定 Workflow",
            target="回答流",
            label="输出确定性结果",
            detail=f"{len(response_content)} chars",
            status="ok",
        )
        telemetry = emit_telemetry(
            "final",
            usage=deterministic["usage"],
            response_chars=len(response_content),
            max_turns_reached=False,
            extra_metadata={
                "route": deterministic["route"],
                "route_policy_version": "task_router_l0_v1",
                "termination_reason": deterministic["termination_reason"],
                "run_state": {},
                "tool_scheduler_ledger": deterministic.get("tool_scheduler_ledger") or {},
                "run_budget": deterministic.get("run_budget") or {},
                "route_decision": deterministic.get("route_decision") or {},
                "model_tier": deterministic.get("model_tier") or "",
                "budget_class": deterministic.get("budget_class") or "",
                "model_routing": deterministic.get("model_routing") or {},
            },
        )
        _save_session_with_trace(repl.session, trace_lines)
        LOGGER.info(
            "deterministic_workflow_final session=%s route=%s response_chars=%d usage=%s",
            session_id,
            deterministic["route"],
            len(response_content),
            deterministic["usage"],
        )
        _ndjson_event(
            handler,
            {
                "type": "final",
                "text": response_content,
                "trace": trace_lines,
                "mode": "deterministic_tool_plan_single_shot_synthesis",
                "model": repl.provider.model,
                "usage": deterministic["usage"],
                "num_turns": 0,
                "max_turns_reached": False,
                "citations": deterministic["citations"],
                "claims": [],
                "evidence_status": "supported" if deterministic["citations"] else "not_applicable",
                "route": deterministic["route"],
                "route_policy_version": "task_router_l0_v1",
                "output_contract_status": "not_required",
                "task_contract_status": deterministic["task_contract_status"],
                "requirements": [],
                "termination_reason": deterministic["termination_reason"],
                "run_state": {},
                "tool_scheduler_ledger": deterministic.get("tool_scheduler_ledger") or {},
                "run_budget": deterministic.get("run_budget") or {},
                "route_decision": deterministic.get("route_decision") or {},
                "model_tier": deterministic.get("model_tier") or "",
                "budget_class": deterministic.get("budget_class") or "",
                "model_routing": deterministic.get("model_routing") or {},
                "tool_audit": list(repl.tool_context.audit_events),
                "sources": deterministic["citations"],
                "telemetry": telemetry,
            },
        )
        return

    try:
        if WEB_ALLOW_DIRECT_STREAM and repl._should_try_direct_stream(prompt):
            LOGGER.info("direct_stream_start session=%s", session_id)
            direct_response = repl._stream_direct_response(on_text_chunk=on_text_chunk)
            if direct_response is not None:
                telemetry = emit_telemetry(
                    "final",
                    usage=None,
                    response_chars=len(direct_response),
                    max_turns_reached=False,
                )
                _save_session_with_trace(repl.session, trace_lines)
                LOGGER.info("save_session session=%s messages=%d", repl.session.session_id, len(repl.session.conversation.messages))
                LOGGER.info("direct_stream_final session=%s response_chars=%d", session_id, len(direct_response))
                _ndjson_event(
                    handler,
                    {
                        "type": "final",
                        "text": direct_response,
                        "trace": trace_lines,
                        "mode": "cli_direct_stream",
                        "model": repl.provider.model,
                        "usage": None,
                        "num_turns": 0,
                        "max_turns_reached": False,
                        "telemetry": telemetry,
                    },
                )
                return

        max_turns = int(payload.get("max_turns") or 20)
        LOGGER.info("agent_loop_start session=%s max_turns=%d", session_id, max_turns)
        result = run_agent_loop(
            conversation=repl.session.conversation,
            provider=repl.provider,
            tool_registry=repl.tool_registry,
            tool_context=repl.tool_context,
            max_turns=max_turns,
            stream=repl.stream,
            on_event=on_event,
            on_text_chunk=on_text_chunk if repl.stream else None,
        )
    except AgentRunCancelled:
        LOGGER.info("chat_stream_cancelled session=%s job_id=%s", session_id, repl.tool_context.job_id)
        try:
            _ndjson_event(handler, {"type": "cancelled", "job_id": repl.tool_context.job_id})
        except (BrokenPipeError, ConnectionResetError):
            pass
        return
    except Exception as exc:
        LOGGER.exception("chat_stream_error session=%s", session_id)
        _ndjson_event(handler, {"type": "error", "error": str(exc)})
        return

    response_content = result.response_text
    current_citations[:] = result.citations or []
    add_flow_event(
        source="回答流",
        target="会话文件",
        label="保存回答和过程",
        detail=f"{len(response_content)} chars",
        status="ok",
    )
    telemetry = emit_telemetry(
        "final",
        usage=result.usage,
        response_chars=len(response_content),
        max_turns_reached=response_content.strip() == "[Max tool turns reached]",
        extra_metadata={
            "route": result.route,
            "route_policy_version": result.route_policy_version,
            "termination_reason": result.termination_reason,
            "run_state": result.run_state or {},
            "tool_scheduler_ledger": result.tool_scheduler_ledger or {},
            "run_budget": result.run_budget or {},
            "route_decision": result.route_decision or {},
            "model_tier": result.model_tier,
            "budget_class": result.budget_class,
            "model_routing": result.model_routing or {},
        },
    )
    _save_session_with_trace(repl.session, trace_lines)
    LOGGER.info("save_session session=%s messages=%d", repl.session.session_id, len(repl.session.conversation.messages))
    LOGGER.info(
        "agent_loop_final session=%s turns=%d response_chars=%d max_turns_reached=%s usage=%s",
        session_id,
        result.num_turns,
        len(response_content),
        response_content.strip() == "[Max tool turns reached]",
        result.usage,
    )
    _ndjson_event(
        handler,
        {
            "type": "final",
            "text": response_content,
            "trace": trace_lines,
            "mode": "cli_agent_loop",
            "model": repl.provider.model,
            "usage": result.usage,
            "num_turns": result.num_turns,
            "max_turns_reached": response_content.strip() == "[Max tool turns reached]",
            "citations": result.citations or [],
            "claims": result.claims or [],
            "evidence_status": result.evidence_status,
            "route": result.route,
            "route_policy_version": result.route_policy_version,
            "output_contract_status": result.output_contract_status,
            "task_contract_status": result.task_contract_status,
            "requirements": result.requirements or [],
            "termination_reason": result.termination_reason,
            "run_state": result.run_state or {},
            "tool_scheduler_ledger": result.tool_scheduler_ledger or {},
            "run_budget": result.run_budget or {},
            "route_decision": result.route_decision or {},
            "model_tier": result.model_tier,
            "budget_class": result.budget_class,
            "model_routing": result.model_routing or {},
            "tool_audit": list(repl.tool_context.audit_events),
            "sources": result.citations or [],
            "telemetry": telemetry,
        },
    )


def _format_tool_trace(events: list[ToolEvent]) -> str:
    return "\n".join(_tool_trace_lines(events))


def _route(payload: dict[str, Any]) -> dict[str, Any]:
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("Prompt is required.")
    runtime = PortRuntime()
    matches = runtime.route_prompt(prompt, limit=10)
    lines = [f"Prompt: {prompt}", "", "Routed matches:"]
    if matches:
        lines.extend(
            f"- [{match.kind}] {match.name} (score {match.score}) - {match.source_hint}"
            for match in matches
        )
    else:
        lines.append("- none")
    return {"text": "\n".join(lines), "matches": [match.__dict__ for match in matches]}


def _bootstrap(payload: dict[str, Any]) -> dict[str, Any]:
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("Prompt is required.")
    return {"text": PortRuntime().bootstrap_session(prompt, limit=5).as_markdown()}


class ClawdWebHandler(BaseHTTPRequestHandler):
    server_version = "ClawdWeb/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.info("http client=%s %s", self.address_string(), fmt % args)

    def do_HEAD(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = INDEX_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
        elif path.startswith("/outputs/"):
            try:
                _file_response(self, _output_file_path(path), head_only=True)
            except Exception:
                self.send_response(HTTPStatus.NOT_FOUND)
                self.end_headers()
        else:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path in ("/", "/index.html"):
                _text_response(self, INDEX_HTML)
            elif path.startswith("/outputs/"):
                _file_response(self, _output_file_path(path))
            elif path == "/api/status":
                _json_response(self, _status_payload())
            elif path == "/api/summary":
                _json_response(self, {"text": QueryEnginePort.from_workspace().render_summary()})
            elif path == "/api/sessions":
                _json_response(self, {"sessions": _list_sessions()})
            elif path == "/api/session":
                query = parse_qs(parsed.query)
                session_id = (query.get("session_id") or [""])[0].strip()
                if not session_id:
                    raise ValueError("session_id is required")
                _json_response(self, _load_session_payload(session_id))
            else:
                _json_response(self, {"error": "Not found"}, status=404)
        except Exception as exc:
            LOGGER.exception("get_error path=%s", path)
            _json_response(self, {"error": str(exc)}, status=500)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = _read_json(self)
            if path == "/api/chat":
                payload["_runtime_context"] = _authorize_runtime_request(self, payload)
                _json_response(self, _chat(payload))
            elif path == "/api/chat_stream":
                payload["_runtime_context"] = _authorize_runtime_request(self, payload)
                _chat_stream(self, payload)
            elif path == "/api/cancel":
                claims = _authorize_runtime_request(self, payload)
                requested_job_id = str(payload.get("job_id") or "").strip()
                authorized_job_id = str(claims.get("job_id") or "").strip()
                if not requested_job_id or requested_job_id != authorized_job_id:
                    raise RuntimeAuthError("cancel job id does not match runtime authorization")
                _json_response(self, {"job_id": requested_job_id, "cancel_signalled": _cancel_runtime_job(requested_job_id)})
            elif path == "/api/route":
                _json_response(self, _route(payload))
            elif path == "/api/bootstrap":
                _json_response(self, _bootstrap(payload))
            elif path == "/api/session/new":
                session_id = str(payload.get("session_id") or "").strip()
                if not session_id:
                    raise ValueError("session_id is required")
                WEB_REPLS.pop(session_id, None)
                repl = _get_web_repl(session_id)
                repl.session.save()
                _json_response(self, _load_session_payload(session_id))
            elif path == "/api/config":
                provider = str(payload.get("provider", "")).strip()
                if provider not in PROVIDER_INFO:
                    raise ValueError(f"Unknown provider: {provider}")
                api_key = str(payload.get("api_key", "")).strip()
                base_url = str(payload.get("base_url", "")).strip()
                default_model = str(payload.get("default_model", "")).strip()
                if api_key:
                    set_api_key(provider, api_key=api_key, base_url=base_url, default_model=default_model)
                else:
                    config = _get_provider_config(provider)
                    set_api_key(
                        provider,
                        api_key=config.get("api_key", ""),
                        base_url=base_url,
                        default_model=default_model,
                    )
                set_default_provider(provider)
                _json_response(self, {"ok": True})
            else:
                _json_response(self, {"error": "Not found"}, status=404)
        except RuntimeAuthError as exc:
            LOGGER.warning("runtime_auth_rejected path=%s reason=%s", path, exc)
            _json_response(self, {"error": str(exc)}, status=HTTPStatus.UNAUTHORIZED)
        except Exception as exc:
            LOGGER.exception("post_error path=%s", path)
            _json_response(self, {"error": str(exc)}, status=500)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), ClawdWebHandler)
    LOGGER.info("server_start url=http://%s:%s log=%s", HOST, PORT, LOG_PATH)
    print(f"Clawd Web running at http://{HOST}:{PORT}")
    print(f"Log file: {LOG_PATH}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
