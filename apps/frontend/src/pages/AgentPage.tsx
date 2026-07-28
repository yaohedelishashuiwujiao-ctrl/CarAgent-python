import { createElement, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Button, Input, List, Space, Spin, Tag, message as antdMessage } from "antd";
import { DownOutlined, HistoryOutlined, MenuFoldOutlined, MenuUnfoldOutlined, PlusOutlined, ReloadOutlined, SendOutlined, StopOutlined, UpOutlined } from "@ant-design/icons";

import { apiHeaders, apiUrl, fetchJson } from "../api";

// ============================================================================
// 【类型定义区】相当于 Qt 里的 struct / Q_PROPERTY 声明
// TypeScript 编译器用这些类型在编译时检查错误，运行时会被完全擦除
// ============================================================================

// Agent 服务状态：当前用哪个模型、API Key 是否就绪
type AgentStatus = {
  default_provider?: string;
  providers?: Record<string, { label?: string; default_model?: string }>;
  current?: {
    has_api_key?: boolean;
    base_url?: string;
    default_model?: string;
  };
};

// 会话摘要：会话列表里每一条显示的信息
type AgentSessionSummary = {
  session_id: string;
  provider?: string;
  model?: string;
  created_at?: string;
  updated_at?: string;
  message_count?: number;
  title?: string;
};

// 单条消息：用户发的 or 助手回的（类似 Qt 里 QListWidgetItem 的数据模型）
type AgentMessage = {
  role: "user" | "assistant";  // 联合类型：只能是这两个值之一
  content: string;
  trace?: string[];            // 工具调用轨迹，可选字段
  citations?: AgentCitation[]; // 引用来源，可选字段
};

type AgentCitation = {
  citation_id?: number;
  source_type?: string;
  title?: string;
  source?: string;
  content?: string;
  tool_name?: string;
  chunk_id?: string;
  document_id?: string;
  dataset_id?: string;
  url?: string;
  query?: string;
  score?: number;
  metadata?: Record<string, unknown>;
};

type AgentFlowEvent = {
  source?: unknown;
  target?: unknown;
  label?: unknown;
  detail?: unknown;
  status?: unknown;
  elapsed_ms?: unknown;
  tokens?: unknown;
  rows?: unknown;
};

// Agent 执行遥测：相当于 Qt 里进度条/状态栏的数据源
// 记录当前阶段、调了什么工具、耗时多少、token 用了多少
type AgentTelemetry = {
  phase?: string;              // 当前阶段：ingest/thinking/tool_use/stream/final
  current_tool?: string | null;
  current_tool_detail?: string;
  stats?: Record<string, unknown>;
  flow_events?: AgentFlowEvent[];  // 完整的事件流（右侧"过程"面板展示的数据）
  architecture_notes?: string[];
  interview_metrics?: Array<{ label: string; value: string; hint?: string }>;
  trace?: string[];
  citations?: AgentCitation[];
  sources?: AgentCitation[];
  run_budget?: AgentRunBudget | null;
  tool_scheduler_ledger?: AgentToolSchedulerLedger | null;
  route?: string;
  task_contract_status?: string;
  output_contract_status?: string;
  termination_reason?: string;
};

type AgentRunBudget = {
  usage?: {
    input_tokens?: number;
    output_tokens?: number;
    total_tokens?: number;
    tokens_after_last_progress?: number;
    model_turns?: number;
  };
  tools?: {
    requested?: number;
    dispatched?: number;
    rejected?: number;
    low_yield_actions?: number;
  };
  events?: Array<Record<string, unknown>>;
};

type AgentToolSchedulerLedger = {
  requested?: number;
  dispatched?: number;
  rejected?: number;
  status_counts?: Record<string, number>;
  reason_counts?: Record<string, number>;
  batches?: number;
};

type AgentSessionPayload = {
  session_id: string;
  provider?: string;
  model?: string;
  messages?: AgentMessage[];
  created_at?: string;
  updated_at?: string;
  telemetry?: AgentTelemetry | null;
};

// 任务状态机：类似 Qt 里 QStateMachine 的状态枚举
// idle → loading → queued → running → streaming → completed/incomplete/error/stopped
type TaskStatus = "idle" | "loading" | "queued" | "running" | "streaming" | "completed" | "incomplete" | "error" | "stopped";
type ObserveTab = "overview" | "sources" | "process";  // 右侧面板的三个 Tab

// SSE 事件类型：后端推送过来的每一种事件
// 类似 Qt 里 QTcpSocket::readyRead 后你解析出的不同消息类型
type AgentStreamEvent =
  | { type: "queued" | "admitted" | "running" | "cancel_requested" | "cancelled" }
  | { type: "telemetry"; telemetry: AgentTelemetry }
  | { type: "text_delta"; text: string }        // 流式文字输出，一小段一小段推过来
  | { type: "tool_use"; tool: string; preview?: string }   // Agent 开始调用工具
  | { type: "tool_result"; tool: string; status?: string; detail?: string }  // 工具返回结果
  | { type: "tool_error"; tool: string; error?: string }   // 工具调用失败
  | { type: "error"; error?: string }
  | { type: "failed"; error?: string; requirements?: Array<Record<string, unknown>> }
  | {
      type: "final";  // 最终事件：完整回答 + 所有元数据
      text: string;
      trace?: string[];
      telemetry?: AgentTelemetry;
      citations?: AgentCitation[];
      sources?: AgentCitation[];
      run_budget?: AgentRunBudget | null;
      tool_scheduler_ledger?: AgentToolSchedulerLedger | null;
      route?: string;
      task_contract_status?: string;
      output_contract_status?: string;
      termination_reason?: string;
    };

const SESSION_KEY = "platform_agent_session_id";
const LONG_WAIT_MS = 15000;
const STALE_WAIT_MS = 60000;
const MAX_TURNS_PLACEHOLDER = "[Max tool turns reached]";
const INCOMPLETE_ANSWER_TEXT = "任务达到工具调用轮次上限，未能生成完整回答。已保留当前工具过程、来源和失败原因，请在右侧任务洞察中复核。";
const COMPACT_EVENT_COUNT = 8;
const EXPANDED_EVENT_COUNT = 40;
const SOURCE_LINK_LIMIT = 12;
const EVENT_DETAIL_LIMIT = 220;

// ============================================================================
// 【工具函数区】纯 JS 逻辑，类似 Qt 里的 static 辅助函数
// 不依赖 React，输入什么就输出什么
// ============================================================================

// 解析 SSE 帧：把原始文本解析成结构化事件对象
// 类似 Qt 里解析 TCP 数据包的逻辑
function parseJobEvent(frame: string): AgentStreamEvent | null {
  const lines = frame.split(/\r?\n/);
  const eventName = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
  const data = lines
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (!data) return null;
  const envelope = JSON.parse(data) as { event_type?: string; payload?: Record<string, unknown> };
  const type = String(envelope.event_type || eventName || "message");
  return { type, ...(envelope.payload || {}) } as AgentStreamEvent;
}

function formatTime(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatDuration(value?: unknown) {
  const ms = typeof value === "number" ? value : Number(value || 0);
  if (!Number.isFinite(ms) || ms <= 0) return "-";
  if (ms < 1000) return `${ms}ms`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

// 根据任务状态和阶段返回中文标签（状态栏显示用）
// 类似 Qt 里 switch(status) { case ...: return "xxx"; }
function phaseLabel(status: TaskStatus, phase?: string, maxTurnsReached?: boolean) {
  if (maxTurnsReached || status === "incomplete") return "达到工具轮次上限";
  if (status === "error") return "任务失败";
  if (status === "stopped") return "已停止";
  if (status === "completed" || phase === "final") return "已完成";
  if (status === "queued" || phase === "queue") return "排队中";
  if (phase === "thinking") return "正在思考";
  if (phase === "tool_use") return "正在调用工具";
  if (phase === "tool_result") return "正在读取工具结果";
  if (phase === "stream") return "正在生成回答";
  if (phase === "ingest") return "正在理解问题";
  if (status === "running") return "正在执行";
  if (status === "loading") return "正在加载";
  return "空闲";
}

function eventText(value: unknown) {
  return value == null ? "" : String(value);
}

function asNumber(value: unknown, fallback = 0) {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function formatCountMap(value?: Record<string, number>) {
  if (!value || !Object.keys(value).length) return "-";
  return Object.entries(value)
    .map(([key, count]) => `${key}:${count}`)
    .join(" · ");
}

function compactText(value: unknown, limit = EVENT_DETAIL_LIMIT) {
  const text = eventText(value).replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit)}...` : text;
}

function traceLines(value: unknown) {
  if (Array.isArray(value)) return value.map((item) => String(item)).filter(Boolean);
  if (typeof value === "string") return value.split(/\n+/).map((item) => item.trim()).filter(Boolean);
  return [];
}

function isMaxTurnsReached(telemetry: AgentTelemetry | null) {
  return telemetry?.stats?.max_turns_reached === true;
}

function isIncompleteAnswer(text?: string) {
  const normalized = (text || "").trim();
  return !normalized || normalized === MAX_TURNS_PLACEHOLDER;
}

function isContractIncomplete(value: AgentTelemetry | null) {
  return (
    value?.output_contract_status === "unmet" ||
    String(value?.termination_reason || "").startsWith("incomplete")
  );
}

function normalizeMessages(messages: AgentMessage[]) {
  const normalized: AgentMessage[] = [];
  for (const message of messages) {
    const previous = normalized[normalized.length - 1];
    if (message.role === "assistant" && previous?.role === "assistant") {
      const trace = [...traceLines(previous.trace), ...traceLines(message.trace)];
      const citations = [...(previous.citations || []), ...(message.citations || [])];
      normalized[normalized.length - 1] = {
        role: "assistant",
        content: message.content && message.content !== "__streaming__" ? message.content : previous.content,
        trace: trace.length ? trace : undefined,
        citations: citations.length ? dedupeCitations(citations) : undefined,
      };
    } else {
      normalized.push(message);
    }
  }
  return normalized;
}

function citationKey(item: AgentCitation) {
  return String(item.citation_id ?? item.chunk_id ?? item.url ?? item.source ?? item.title ?? JSON.stringify(item));
}

function dedupeCitations(items: AgentCitation[]) {
  const seen = new Set<string>();
  const out: AgentCitation[] = [];
  for (const item of items) {
    const key = citationKey(item);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out;
}

function telemetryCitations(telemetry: AgentTelemetry | null) {
  return dedupeCitations([...(telemetry?.citations || []), ...(telemetry?.sources || [])]);
}

function citationSource(item: AgentCitation) {
  return String(item.source || item.url || item.metadata?.source || item.metadata?.document_id || item.tool_name || "-");
}

function citationTitle(item: AgentCitation) {
  const id = item.citation_id != null ? `[${item.citation_id}] ` : "";
  return `${id}${item.title || item.source_type || item.tool_name || "证据"}`;
}

// ============================================================================
// 【子组件区】相当于 Qt 里自定义的小 Widget
// 每个函数 = 一个可复用的 UI 组件
// ============================================================================

// 引用来源列表组件：显示 Agent 引用的证据（类似 Qt 里自定义的 QListWidget）
function CitationList({ citations, compact = false }: { citations: AgentCitation[]; compact?: boolean }) {
  const items = dedupeCitations(citations).slice(0, compact ? 6 : 30);
  if (!items.length) return null;
  return (
    <div className={compact ? "agent-citations compact" : "agent-citations"}>
      <div className="agent-citations-title">证据来源</div>
      {items.map((item) => {
        const source = citationSource(item);
        const href = /^https?:\/\//.test(source) ? source : item.url && /^https?:\/\//.test(item.url) ? item.url : "";
        const meta = [
          item.source_type,
          item.tool_name,
          item.chunk_id ? `chunk=${item.chunk_id}` : "",
          item.document_id ? `doc=${item.document_id}` : "",
          item.query ? "SQL" : "",
        ].filter(Boolean);
        return (
          <div className="agent-citation" key={citationKey(item)}>
            <div className="agent-citation-head">
              <strong>{citationTitle(item)}</strong>
              {meta.length ? <span>{meta.join(" · ")}</span> : null}
            </div>
            {href ? (
              <a href={href} target="_blank" rel="noreferrer">
                {source}
              </a>
            ) : (
              <small>{source}</small>
            )}
            {item.content ? <p>{compactText(item.content, compact ? 160 : 360)}</p> : null}
          </div>
        );
      })}
    </div>
  );
}

function extractUrls(events: AgentFlowEvent[]) {
  const urls = new Set<string>();
  for (const event of events) {
    const detail = eventText(event.detail);
    for (const match of detail.matchAll(/https?:\/\/[^\s"')，。]+/g)) {
      urls.add(match[0]);
    }
  }
  return [...urls];
}

function summarizeTelemetry(telemetry: AgentTelemetry | null, elapsedOverride?: number) {
  const stats = telemetry?.stats || {};
  const events = telemetry?.flow_events || [];
  const toolUses = Number(stats.tool_uses ?? events.filter((item) => eventText(item.label).includes("调用工具")).length);
  const toolResults = Number(stats.tool_results ?? events.filter((item) => eventText(item.label).includes("结果")).length);
  const toolErrors = Number(stats.tool_errors ?? events.filter((item) => eventText(item.status) === "error").length);
  const elapsedMs = Number(stats.elapsed_ms || elapsedOverride || 0);
  const responseChars = Number(stats.response_chars || 0);
  const maxTurnsReached = stats.max_turns_reached === true;
  const urls = extractUrls(events);
  const zeroResultSearches = events.filter((item) => eventText(item.detail).includes("results=0")).length;
  const successfulFetches = events.filter((item) => eventText(item.source).includes("WebFetch") && eventText(item.status) === "ok").length;
  const quality =
    maxTurnsReached
      ? "未完成"
      : toolErrors > 0 || zeroResultSearches > toolResults / 2
      ? "需复核"
      : urls.length >= 2 || successfulFetches >= 2
        ? "较高"
        : toolResults > 0
          ? "中等"
          : "待确认";
  return {
    events,
    urls,
    toolUses,
    toolResults,
    toolErrors,
    elapsedMs,
    responseChars,
    maxTurnsReached,
    zeroResultSearches,
    successfulFetches,
    quality,
    successRate: toolUses ? Math.round((toolResults / toolUses) * 100) : 0,
  };
}

function IncompleteAnswer({ summary }: { summary: ReturnType<typeof summarizeTelemetry> }) {
  return (
    <div className="agent-incomplete-answer">
      <strong>未生成完整回答</strong>
      <p>{INCOMPLETE_ANSWER_TEXT}</p>
      <div>
        <span>{summary.toolUses} 次工具调用</span>
        <span>{summary.toolErrors} 个失败</span>
        <span>{summary.zeroResultSearches} 次搜索未命中</span>
      </div>
    </div>
  );
}

function inlineMarkdown(text: string) {
  const parts: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)]+\)|https?:\/\/[^\s)]+)/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index == null) continue;
    if (match.index > last) parts.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("**")) {
      parts.push(<strong key={`${match.index}-strong`}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("`")) {
      parts.push(<code key={`${match.index}-code`}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("[")) {
      const link = token.match(/^\[([^\]]+)\]\((https?:\/\/[^)]+)\)$/);
      parts.push(
        <a key={`${match.index}-link`} href={link?.[2]} target="_blank" rel="noreferrer">
          {link?.[1]}
        </a>,
      );
    } else {
      parts.push(
        <a key={`${match.index}-url`} href={token} target="_blank" rel="noreferrer">
          {token}
        </a>,
      );
    }
    last = match.index + token.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

// Markdown 渲染组件：把 Agent 返回的 Markdown 文本渲染成 HTML
// 支持标题、列表、表格、代码、链接等
// 类似 Qt 里用 QTextBrowser + QTextDocument 渲染富文本
function MarkdownAnswer({ text }: { text: string }) {
  const blocks = useMemo(() => {
    const normalized = text.replace(/\r\n/g, "\n").replace(/\s+\|\s+\|/g, " |\n|");
    const lines = normalized.split("\n");
    const items: ReactNode[] = [];
    let paragraph: string[] = [];
    let list: string[] = [];
    let orderedList: string[] = [];
    let tableRows: string[][] = [];

    function flushParagraph() {
      if (!paragraph.length) return;
      items.push(<p key={`p-${items.length}`}>{inlineMarkdown(paragraph.join(" "))}</p>);
      paragraph = [];
    }

    function flushList() {
      if (!list.length) return;
      items.push(
        <ul key={`ul-${items.length}`}>
          {list.map((item, index) => (
            <li key={`${index}-${item}`}>{inlineMarkdown(item)}</li>
          ))}
        </ul>,
      );
      list = [];
    }

    function flushOrderedList() {
      if (!orderedList.length) return;
      items.push(
        <ol key={`ol-${items.length}`}>
          {orderedList.map((item, index) => (
            <li key={`${index}-${item}`}>{inlineMarkdown(item)}</li>
          ))}
        </ol>,
      );
      orderedList = [];
    }

    function flushTable() {
      if (tableRows.length < 2) {
        tableRows.forEach((row) => paragraph.push(row.join(" | ")));
        tableRows = [];
        return;
      }
      const [head, maybeSeparator, ...body] = tableRows;
      const hasSeparator = maybeSeparator.every((cell) => /^:?-{3,}:?$/.test(cell));
      const rows = hasSeparator ? body : [maybeSeparator, ...body];
      items.push(
        <div className="agent-markdown-table-wrap" key={`table-${items.length}`}>
          <table>
            <thead>
              <tr>
                {head.map((cell, index) => (
                  <th key={`${index}-${cell}`}>{inlineMarkdown(cell)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={`${rowIndex}-${row.join("-")}`}>
                  {row.map((cell, cellIndex) => (
                    <td key={`${cellIndex}-${cell}`}>{inlineMarkdown(cell)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      tableRows = [];
    }

    for (const rawLine of lines) {
      const line = rawLine.trim();
      if (!line || line === "---") {
        flushParagraph();
        flushList();
        flushOrderedList();
        flushTable();
        if (line === "---") items.push(<hr key={`hr-${items.length}`} />);
        continue;
      }
      if (line.startsWith("|") && line.endsWith("|")) {
        flushParagraph();
        flushList();
        flushOrderedList();
        tableRows.push(line.split("|").slice(1, -1).map((cell) => cell.trim()));
        continue;
      }
      const heading = line.match(/^(#{1,4})\s+(.+)$/);
      if (heading) {
        flushParagraph();
        flushList();
        flushOrderedList();
        flushTable();
        const level = Math.min(heading[1].length, 4);
        items.push(createElement(`h${level}`, { key: `h-${items.length}` }, inlineMarkdown(heading[2])));
        continue;
      }
      const bullet = line.match(/^[-*]\s+(.+)$/);
      if (bullet) {
        flushParagraph();
        flushOrderedList();
        flushTable();
        list.push(bullet[1]);
        continue;
      }
      const ordered = line.match(/^\d+[.)]\s+(.+)$/);
      if (ordered) {
        flushParagraph();
        flushList();
        flushTable();
        orderedList.push(ordered[1]);
        continue;
      }
      flushTable();
      paragraph.push(line);
    }
    flushParagraph();
    flushList();
    flushOrderedList();
    flushTable();
    return items;
  }, [text]);

  return <div className="agent-markdown">{blocks}</div>;
}

// ============================================================================
// 【主组件】相当于 Qt 里的 QMainWindow
// 这是整个 Agent 页面的入口，包含：
//   - 左侧：会话列表（类似 QListWidget）
//   - 中间：对话区（类似 QTextEdit + 输入框）
//   - 右侧：任务洞察面板（类似 QTabWidget 三个 Tab）
// ============================================================================
export function AgentPage() {
  // ---------- 状态声明（相当于 Qt 里的成员变量 + Q_PROPERTY）----------
  // useState：React 的状态管理，类似 Qt 的 Q_PROPERTY + NOTIFY 信号
  // 调用 setXxx(newValue) 后，React 自动重新渲染受影响的 UI
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [sessions, setSessions] = useState<AgentSessionSummary[]>([]);
  const [session, setSession] = useState<AgentSessionPayload | null>(null);
  const [composer, setComposer] = useState("");
  const [loadingSessions, setLoadingSessions] = useState(false);
  const [loadingSession, setLoadingSession] = useState(false);
  const [sending, setSending] = useState(false);
  const [taskStatus, setTaskStatus] = useState<TaskStatus>("idle");
  const [taskStartedAt, setTaskStartedAt] = useState<number | null>(null);
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);
  const [clock, setClock] = useState(Date.now());
  const [telemetry, setTelemetry] = useState<AgentTelemetry | null>(null);
  const [activeSessionId, setActiveSessionId] = useState(() => localStorage.getItem(SESSION_KEY) || crypto.randomUUID());
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [observeTab, setObserveTab] = useState<ObserveTab>("overview");
  const [flowExpanded, setFlowExpanded] = useState(false);
  const [sessionsCollapsed, setSessionsCollapsed] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const observeRef = useRef<HTMLElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const activeJobIdRef = useRef<string | null>(null);

  const currentMessages = useMemo(() => normalizeMessages(session?.messages ?? []), [session]);
  const summary = useMemo(() => summarizeTelemetry(telemetry, taskStartedAt ? clock - taskStartedAt : undefined), [telemetry, taskStartedAt, clock]);
  const longWait = sending && lastEventAt != null && clock - lastEventAt > LONG_WAIT_MS;
  const staleWait = sending && lastEventAt != null && clock - lastEventAt > STALE_WAIT_MS;

  // ---------- 数据请求函数（相当于 Qt 里的槽函数 / 网络请求处理）----------

  // 获取 Agent 服务状态（Provider、模型、API Key）
  async function loadStatus() {
    try {
      const data = await fetchJson<AgentStatus>("/api/agent/status");
      setStatus(data);
    } catch (error) {
      setTaskStatus("error");
      antdMessage.error(`读取 Agent 状态失败: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  // 加载会话列表（左侧面板的数据源）
  async function loadSessions(selectId?: string) {
    setLoadingSessions(true);
    try {
      const data = await fetchJson<{ sessions: AgentSessionSummary[] }>("/api/agent/sessions");
      setSessions(data.sessions || []);
      const nextId = selectId || activeSessionId;
      const found = data.sessions?.find((item) => item.session_id === nextId) ?? data.sessions?.[0];
      if (found && found.session_id !== activeSessionId) {
        setActiveSessionId(found.session_id);
        localStorage.setItem(SESSION_KEY, found.session_id);
        await loadSession(found.session_id);
      } else if (!session && found) {
        await loadSession(found.session_id);
      }
    } catch (error) {
      setTaskStatus("error");
      antdMessage.error(`读取会话列表失败: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setLoadingSessions(false);
    }
  }

  // 加载指定会话的消息内容（中间对话区的数据源）
  async function loadSession(sessionId: string) {
    setLoadingSession(true);
    setTaskStatus("loading");
    try {
      const data = await fetchJson<AgentSessionPayload>(`/api/agent/session?session_id=${encodeURIComponent(sessionId)}`);
      setSession(data);
      setTelemetry(data.telemetry ?? null);
      setTaskStatus(isMaxTurnsReached(data.telemetry ?? null) || isContractIncomplete(data.telemetry ?? null) ? "incomplete" : data.telemetry?.phase === "final" ? "completed" : "idle");
      setActiveSessionId(sessionId);
      localStorage.setItem(SESSION_KEY, sessionId);
    } catch (error) {
      setTaskStatus("error");
      antdMessage.error(`读取会话失败: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setLoadingSession(false);
    }
  }

  // 创建新会话（点击"新会话"按钮触发）
  async function createSession() {
    const sessionId = crypto.randomUUID();
    try {
      const response = await fetch(apiUrl("/api/agent/session/new"), {
        method: "POST",
        headers: apiHeaders(true),
        body: JSON.stringify({ session_id: sessionId }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `Request failed: ${response.status}`);
      }
      const data = (await response.json()) as AgentSessionPayload;
      setSessions((prev) => [
        {
          session_id: data.session_id,
          provider: data.provider,
          model: data.model,
          created_at: data.created_at,
          updated_at: data.updated_at,
          message_count: data.messages?.length ?? 0,
          title: "New session",
        },
        ...prev.filter((item) => item.session_id !== data.session_id),
      ]);
      await loadSession(sessionId);
      antdMessage.success("已创建新会话");
    } catch (error) {
      setTaskStatus("error");
      antdMessage.error(`创建会话失败: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  function patchCurrentAssistant(text: string, trace: string[] = [], citations: AgentCitation[] = []) {
    setSession((prev) => {
      if (!prev) return prev;
      const messages = [...(prev.messages || [])];
      const last = messages[messages.length - 1];
      const nextCitations = citations.length ? dedupeCitations(citations) : undefined;
      if (last && last.role === "assistant" && last.content === "__streaming__") {
        messages[messages.length - 1] = { role: "assistant", content: text, trace, citations: nextCitations };
      } else if (last && last.role === "assistant" && sending) {
        messages[messages.length - 1] = { ...last, content: text, trace, citations: nextCitations || last.citations };
      } else {
        messages.push({ role: "assistant", content: text, trace, citations: nextCitations });
      }
      return { ...prev, messages };
    });
  }

  function updateTelemetry(next: AgentTelemetry) {
    setTelemetry(next);
    setLastEventAt(Date.now());
    if (isMaxTurnsReached(next) || isContractIncomplete(next)) setTaskStatus("incomplete");
    else if (next.phase === "final") setTaskStatus("completed");
    else if (next.phase === "stream") setTaskStatus("streaming");
    else setTaskStatus("running");
  }

  // 停止当前任务（点击"停止"按钮触发）
  async function stopMessage() {
    const jobId = activeJobIdRef.current;
    setSending(false);
    setTaskStatus("stopped");
    patchCurrentAssistant("正在取消本次任务。", telemetry?.trace || []);
    try {
      if (jobId) {
        await fetch(apiUrl(`/api/agent/chat_jobs/${encodeURIComponent(jobId)}/cancel`), { method: "POST", headers: apiHeaders() });
      }
    } finally {
      abortRef.current?.abort();
      abortRef.current = null;
      activeJobIdRef.current = null;
    }
  }

  // ★★★ 核心函数：发送消息 ★★★
  // 整个流程：POST 创建任务 → GET SSE 流 → 逐帧解析事件 → 更新状态 → UI 自动刷新
  // 类似 Qt 里：emit sendClicked → QNetworkReply::readyRead → 解析 → 更新 UI
  async function sendMessage() {
    const prompt = composer.trim();
    if (!prompt || sending) return;
    const sessionId = activeSessionId || crypto.randomUUID();
    const started = Date.now();
    abortRef.current = new AbortController();
    setComposer("");
    setSending(true);
    setTaskStatus("running");
    setTaskStartedAt(started);
    setLastEventAt(started);
    setFlowExpanded(false);
    setObserveTab("overview");
    setTelemetry({
      phase: "ingest",
      current_tool_detail: prompt,
      flow_events: [{ source: "用户", target: "会话记忆", label: "写入用户输入", detail: prompt, status: "ok", elapsed_ms: 0 }],
      stats: { tool_uses: 0, tool_results: 0, tool_errors: 0, elapsed_ms: 0 },
      interview_metrics: [],
    });
    setSession((prev) => ({
      session_id: sessionId,
      provider: prev?.provider || status?.default_provider,
      model: prev?.model || status?.current?.default_model,
      created_at: prev?.created_at,
      updated_at: prev?.updated_at,
      telemetry: prev?.telemetry ?? null,
      messages: [...(prev?.messages || []), { role: "user", content: prompt }, { role: "assistant", content: "__streaming__" }],
    }));

    // 第1步：POST 创建任务，拿到 job_id 和 events_url
    try {
      const createResponse = await fetch(apiUrl("/api/agent/chat_jobs"), {
        method: "POST",
        headers: { ...apiHeaders(true), "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({
          session_id: sessionId,
          prompt,
        }),
        signal: abortRef.current.signal,
      });

      if (!createResponse.ok) {
        const payload = await createResponse.json().catch(() => ({}));
        throw new Error(payload.detail || `Request failed: ${createResponse.status}`);
      }
      const created = (await createResponse.json()) as { job_id: string; events_url: string };
      activeJobIdRef.current = created.job_id;
      setTaskStatus("queued");
      setTelemetry((current) => ({ ...(current || {}), phase: "queue", current_tool_detail: "等待调度" }));

      // 第2步：GET events_url，建立 SSE 长连接，流式读取事件
      const response = await fetch(apiUrl(created.events_url), { headers: apiHeaders(), signal: abortRef.current.signal });
      if (!response.ok || !response.body) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `Request failed: ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let assistantText = "";
      let assistantTrace: string[] = [];
      let assistantCitations: AgentCitation[] = [];
      let terminal = false;

      // 第3步：逐帧读取 SSE 事件，类似 Qt 里 while(socket->canReadLine()) 循环
      while (!terminal) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split(/\r?\n\r?\n/);
        buffer = frames.pop() || "";
        for (const frame of frames) {
          const event = parseJobEvent(frame);
          if (!event) continue;

          setLastEventAt(Date.now());
          if (event.type === "queued") {
            setTaskStatus("queued");
          } else if (event.type === "admitted" || event.type === "running") {
            setTaskStatus("running");
            setTelemetry((current) => ({
              ...(current || {}),
              phase: "thinking",
              current_tool: null,
              current_tool_detail: "模型正在分析问题",
            }));
          } else if (event.type === "cancel_requested") {
            setTaskStatus("stopped");
          } else if (event.type === "cancelled") {
            setTaskStatus("stopped");
            patchCurrentAssistant("任务已取消。", assistantTrace, assistantCitations);
            terminal = true;
          } else if (event.type === "telemetry") {
            updateTelemetry(event.telemetry);
          } else if (event.type === "text_delta") {
            setTaskStatus("streaming");
            setTelemetry((current) => ({ ...(current || {}), phase: "stream", current_tool: null, current_tool_detail: "正在生成回答" }));
            assistantText += event.text;
            patchCurrentAssistant(assistantText, assistantTrace, assistantCitations);
          } else if (event.type === "tool_use") {
            setTaskStatus("running");
            setTelemetry((current) => ({
              ...(current || {}),
              phase: "tool_use",
              current_tool: event.tool,
              current_tool_detail: event.preview || "正在调用工具",
            }));
            assistantTrace = [...assistantTrace, `→ ${event.tool}${event.preview ? `: ${event.preview}` : ""}`];
            patchCurrentAssistant(assistantText || "__streaming__", assistantTrace, assistantCitations);
          } else if (event.type === "tool_result") {
            setTelemetry((current) => ({
              ...(current || {}),
              phase: "tool_result",
              current_tool: event.tool,
              current_tool_detail: event.detail || "正在读取工具结果",
            }));
            assistantTrace = [...assistantTrace, `✓ ${event.tool}${event.detail ? ` · ${event.detail}` : ""}`];
            patchCurrentAssistant(assistantText || "__streaming__", assistantTrace, assistantCitations);
          } else if (event.type === "tool_error") {
            assistantTrace = [...assistantTrace, `✗ ${event.tool}${event.error ? ` · ${event.error}` : ""}`];
            patchCurrentAssistant(assistantText || "__streaming__", assistantTrace, assistantCitations);
          } else if (event.type === "error") {
            throw new Error(event.error || "Agent stream failed");
          } else if (event.type === "failed") {
            setTaskStatus("error");
            const failureText = `任务未完成：${event.error || "必需的任务契约未满足。"}`;
            patchCurrentAssistant(failureText, assistantTrace, assistantCitations);
            antdMessage.error(failureText);
            terminal = true;
          } else if (event.type === "final") {
            assistantText = isIncompleteAnswer(event.text) ? INCOMPLETE_ANSWER_TEXT : event.text || assistantText;
            assistantTrace = event.trace || assistantTrace;
            assistantCitations = dedupeCitations([...(event.citations || []), ...(event.sources || []), ...telemetryCitations(event.telemetry ?? null)]);
            const finalTelemetry: AgentTelemetry = {
              ...(event.telemetry || telemetry || {}),
              phase: event.telemetry?.phase || "final",
              run_budget: event.run_budget ?? event.telemetry?.run_budget ?? telemetry?.run_budget ?? null,
              tool_scheduler_ledger: event.tool_scheduler_ledger ?? event.telemetry?.tool_scheduler_ledger ?? telemetry?.tool_scheduler_ledger ?? null,
              route: event.route ?? event.telemetry?.route ?? telemetry?.route,
              task_contract_status: event.task_contract_status ?? event.telemetry?.task_contract_status ?? telemetry?.task_contract_status,
              output_contract_status: event.output_contract_status ?? event.telemetry?.output_contract_status ?? telemetry?.output_contract_status,
              termination_reason: event.termination_reason ?? event.telemetry?.termination_reason ?? telemetry?.termination_reason,
            };
            updateTelemetry(finalTelemetry);
            setTaskStatus(isMaxTurnsReached(finalTelemetry) || isContractIncomplete(finalTelemetry) || isIncompleteAnswer(event.text) ? "incomplete" : "completed");
            patchCurrentAssistant(assistantText, assistantTrace, assistantCitations);
            terminal = true;
          }
          if (terminal) break;
        }
      }
      if (terminal) await reader.cancel();

      await loadSessions(sessionId);
    } catch (error) {
      if ((error as Error).name === "AbortError") return;
      setTaskStatus("error");
      antdMessage.error(`发送失败: ${error instanceof Error ? error.message : String(error)}`);
      setSession((prev) => {
        if (!prev) return prev;
        const messages = [...(prev.messages || [])];
        const last = messages[messages.length - 1];
        const failed = "请求失败，请重试。";
        if (last?.role === "assistant" && last.content === "__streaming__") {
          messages[messages.length - 1] = { role: "assistant", content: failed };
        } else {
          messages.push({ role: "assistant", content: failed });
        }
        return { ...prev, messages };
      });
    } finally {
      abortRef.current = null;
      activeJobIdRef.current = null;
      setSending(false);
    }
  }

  // ---------- useEffect：生命周期钩子（相当于 Qt 的 showEvent / 构造函数里初始化）----------
  // 组件首次挂载时执行：加载状态、会话列表
  // 空数组 [] 表示只在挂载时执行一次，类似 Qt 构造函数
  useEffect(() => {
    void loadStatus();
    void loadSession(activeSessionId);
    void loadSessions(activeSessionId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // sending 为 true 时启动计时器，每秒更新 clock（用于显示"已运行 X 秒"）
  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [sending]);

  // 消息更新后自动滚动到底部（类似 Qt 里 scrollToBottom()）
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [session?.messages?.length, telemetry?.phase]);

  const activeSummary = sessions.find((item) => item.session_id === activeSessionId);
  const activeTitle = activeSummary?.title || currentMessages.find((item) => item.role === "user")?.content || "当前会话";
  const latestEvents = summary.events.slice(-COMPACT_EVENT_COUNT).reverse();
  const visibleEvents = flowExpanded ? summary.events.slice(-EXPANDED_EVENT_COUNT) : latestEvents;
  const visibleSources = summary.urls.slice(0, SOURCE_LINK_LIMIT);
  const runBudget = telemetry?.run_budget || null;
  const budgetUsage = runBudget?.usage || {};
  const budgetTools = runBudget?.tools || {};
  const schedulerLedger = telemetry?.tool_scheduler_ledger || null;
  const schedulerRequested = asNumber(schedulerLedger?.requested ?? budgetTools.requested);
  const schedulerDispatched = asNumber(schedulerLedger?.dispatched ?? budgetTools.dispatched);
  const schedulerRejected = asNumber(schedulerLedger?.rejected ?? budgetTools.rejected);
  const contractIncomplete = isContractIncomplete(telemetry);
  const currentCitations = useMemo(
    () => dedupeCitations([...currentMessages.flatMap((item) => item.citations || []), ...telemetryCitations(telemetry)]),
    [currentMessages, telemetry],
  );
  const processEventCount = summary.events.length || traceLines(telemetry?.trace).length;

  function showProcessPanel() {
    setObserveTab("process");
    setFlowExpanded(true);
    window.requestAnimationFrame(() => observeRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" }));
  }

  // ---------- 渲染区（相当于 Qt 的 paintEvent / setupUI）----------
  // 返回 JSX 描述页面结构，React 根据状态自动决定渲染什么
  return (
    <div className="agent-page">
      <div className="page-title agent-titlebar">
        <div>
          <h2>AI Agent</h2>
          <p>面向真实分析任务的对话、证据和工具过程工作台。</p>
        </div>
        <Space wrap>
          <Tag color={status?.current?.has_api_key ? "green" : "red"}>{status?.current?.has_api_key ? "API Key 已加载" : "缺少 API Key"}</Tag>
          <Button icon={<ReloadOutlined />} onClick={() => void loadSessions(activeSessionId)}>
            刷新
          </Button>
          <Button icon={sessionsCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => setSessionsCollapsed((collapsed) => !collapsed)}>
            {sessionsCollapsed ? "展开会话" : "折叠会话"}
          </Button>
          <Button icon={<PlusOutlined />} onClick={() => void createSession()}>
            新会话
          </Button>
          <Button icon={<HistoryOutlined />} onClick={() => setSettingsOpen(true)}>
            设置
          </Button>
        </Space>
      </div>

      <div className={sessionsCollapsed ? "agent-grid sessions-collapsed" : "agent-grid"}>
        {!sessionsCollapsed ? (
        <section className="agent-panel agent-sessions">
          <div className="agent-panel-head">
            <div>
              <div className="agent-panel-title">会话</div>
              <div className="agent-panel-sub">按最近更新时间排列。</div>
            </div>
            <Spin spinning={loadingSessions} />
          </div>
          <List
            size="small"
            dataSource={sessions}
            locale={{ emptyText: "暂无会话" }}
            renderItem={(item) => {
              const displayMessageCount = item.session_id === activeSessionId ? currentMessages.length : (item.message_count ?? 0);
              return (
                <List.Item className={item.session_id === activeSessionId ? "agent-session active" : "agent-session"} onClick={() => void loadSession(item.session_id)}>
                  <div className="agent-session-title">{item.title || "New session"}</div>
                  <div className="agent-session-meta">
                    <span>{displayMessageCount} 条消息</span>
                    <span>{item.model || item.provider || "-"}</span>
                    <span>{formatTime(item.updated_at || item.created_at)}</span>
                  </div>
                </List.Item>
              );
            }}
          />
        </section>
        ) : null}

        <section className="agent-panel agent-chat">
          <div className="agent-panel-head agent-chat-head">
            <div>
              <div className="agent-panel-title">{activeTitle}</div>
              <div className="agent-panel-sub">
                {activeSummary?.session_id || activeSessionId || "-"} · {session?.provider || status?.default_provider || "-"} · {session?.model || status?.current?.default_model || "-"}
              </div>
            </div>
            <Spin spinning={loadingSession || sending} />
          </div>

          <div className={`agent-runbar ${taskStatus}`}>
            <div>
              <strong>{phaseLabel(taskStatus, telemetry?.phase)}</strong>
              <span>{telemetry?.current_tool ? ` · ${telemetry.current_tool}` : ""}</span>
              {telemetry?.current_tool_detail ? <p>{telemetry.current_tool_detail}</p> : null}
              {longWait ? <p className="agent-runbar-note">{staleWait ? `任务已运行 ${formatDuration(clock - (taskStartedAt || clock))}，仍在等待 Agent 事件。` : "任务耗时较长，仍在等待 Agent 响应。"}</p> : null}
            </div>
            <div className="agent-runbar-metrics">
              <span>{summary.toolUses} tools</span>
              <span>{summary.toolErrors} errors</span>
              <span>{formatDuration(summary.elapsedMs)}</span>
            </div>
          </div>

          <div className="agent-messages">
            {currentMessages.length ? (
              currentMessages.map((item, index) => (
                <div key={`${item.role}-${index}`} className={`agent-message ${item.role}`}>
                  <div className="agent-message-role">{item.role === "user" ? "用户" : "助手"}</div>
                  <div className="agent-message-content">
                    {item.content === "__streaming__" ? (
                      <div className="agent-thinking">
                        <span>正在处理</span>
                        <small>{phaseLabel(taskStatus, telemetry?.phase)}</small>
                      </div>
                    ) : item.role === "assistant" ? (
                      <>
                        <MarkdownAnswer text={item.content} />
                        <CitationList citations={item.citations || []} compact />
                      </>
                    ) : (
                      item.content
                    )}
                  </div>
                  {processEventCount ? (
                    <div className="agent-message-trace">
                      <button type="button" onClick={showProcessPanel}>
                        查看过程摘要与 {processEventCount} 条事件
                      </button>
                    </div>
                  ) : null}
                </div>
              ))
            ) : (
              <div className="agent-empty">输入一个分析问题。任务执行时会显示阶段、来源、失败和最终回答。</div>
            )}
            <div ref={bottomRef} />
          </div>

          <div className="agent-composer">
            <Input.TextArea
              value={composer}
              onChange={(event) => setComposer(event.target.value)}
              autoSize={{ minRows: 3, maxRows: 8 }}
              disabled={sending}
              placeholder="输入分析需求，例如：调研小鹏X9悬架"
              onPressEnter={(event) => {
                if (!event.shiftKey) {
                  event.preventDefault();
                  void sendMessage();
                }
              }}
            />
            <div className="agent-composer-actions">
              {sending ? (
                <Button danger icon={<StopOutlined />} onClick={stopMessage}>
                  停止
                </Button>
              ) : (
                <Button type="primary" icon={<SendOutlined />} onClick={() => void sendMessage()} disabled={!composer.trim()}>
                  发送
                </Button>
              )}
            </div>
          </div>
        </section>

        <aside className="agent-panel agent-observe" ref={observeRef}>
          <div className="agent-panel-head">
            <div>
              <div className="agent-panel-title">任务洞察</div>
              <div className="agent-panel-sub">{phaseLabel(taskStatus, telemetry?.phase)}</div>
            </div>
          </div>

          <div className="agent-observe-tabs">
            {(["overview", "sources", "process"] as ObserveTab[]).map((tab) => (
              <button key={tab} className={observeTab === tab ? "active" : ""} onClick={() => setObserveTab(tab)}>
                {tab === "overview" ? "概览" : tab === "sources" ? "来源" : "过程"}
              </button>
            ))}
          </div>

          {observeTab === "overview" ? (
            <>
              <div className="agent-quality">
                <span>证据质量</span>
                <strong>{summary.quality}</strong>
                <p>
                  {contractIncomplete ? `任务契约未完整满足：${telemetry?.termination_reason || telemetry?.task_contract_status || "incomplete"}。` : ""}
                  {summary.zeroResultSearches ? `${summary.zeroResultSearches} 次搜索未命中。` : ""}
                  {summary.toolErrors ? `${summary.toolErrors} 个工具失败。` : ""}
                  {!contractIncomplete && !summary.zeroResultSearches && !summary.toolErrors ? "当前过程没有明显工具异常。" : ""}
                </p>
              </div>
              <div className="agent-stats">
                <div className="agent-stat">
                  <div className="agent-stat-label">工具调用</div>
                  <div className="agent-stat-value">{summary.toolUses}</div>
                  <div className="agent-stat-hint">total turns</div>
                </div>
                <div className="agent-stat">
                  <div className="agent-stat-label">成功率</div>
                  <div className="agent-stat-value">{summary.successRate}%</div>
                  <div className="agent-stat-hint">results / uses</div>
                </div>
                <div className="agent-stat">
                  <div className="agent-stat-label">失败</div>
                  <div className="agent-stat-value">{summary.toolErrors}</div>
                  <div className="agent-stat-hint">tool errors</div>
                </div>
                <div className="agent-stat">
                  <div className="agent-stat-label">来源</div>
                  <div className="agent-stat-value">{currentCitations.length || summary.urls.length}</div>
                  <div className="agent-stat-hint">{currentCitations.length ? "citations" : "detected urls"}</div>
                </div>
              </div>
              {runBudget || schedulerLedger ? (
                <>
                  <div className="agent-runtime-head">
                    <span>运行预算 / 工具调度</span>
                    <small>{telemetry?.route || "agent_loop"}</small>
                  </div>
                  <div className="agent-stats">
                    <div className="agent-stat">
                      <div className="agent-stat-label">模型轮次</div>
                      <div className="agent-stat-value">{asNumber(budgetUsage.model_turns)}</div>
                      <div className="agent-stat-hint">model turns</div>
                    </div>
                    <div className="agent-stat">
                      <div className="agent-stat-label">Tokens</div>
                      <div className="agent-stat-value">{asNumber(budgetUsage.total_tokens)}</div>
                      <div className="agent-stat-hint">
                        in {asNumber(budgetUsage.input_tokens)} / out {asNumber(budgetUsage.output_tokens)}
                      </div>
                    </div>
                    <div className="agent-stat">
                      <div className="agent-stat-label">调度执行</div>
                      <div className="agent-stat-value">
                        {schedulerDispatched}/{schedulerRequested}
                      </div>
                      <div className="agent-stat-hint">dispatched / requested</div>
                    </div>
                    <div className="agent-stat">
                      <div className="agent-stat-label">调度拒绝</div>
                      <div className="agent-stat-value">{schedulerRejected}</div>
                      <div className="agent-stat-hint">preflight / duplicate / invalid</div>
                    </div>
                  </div>
                  <div className="agent-runtime-detail">
                    <div>
                      <span>进展后 Tokens</span>
                      <strong>{asNumber(budgetUsage.tokens_after_last_progress)}</strong>
                    </div>
                    <div>
                      <span>低收益调用</span>
                      <strong>{asNumber(budgetTools.low_yield_actions)}</strong>
                    </div>
                    <p>结果：{formatCountMap(schedulerLedger?.status_counts)}</p>
                    <p>拒绝原因：{formatCountMap(schedulerLedger?.reason_counts)}</p>
                  </div>
                </>
              ) : null}
            </>
          ) : null}

          {observeTab === "sources" ? (
            <div className="agent-source-list">
              {currentCitations.length ? (
                <CitationList citations={currentCitations} />
              ) : summary.urls.length ? (
                <>
                  {visibleSources.map((url) => (
                    <a key={url} href={url} target="_blank" rel="noreferrer">
                      {url}
                    </a>
                  ))}
                  {summary.urls.length > SOURCE_LINK_LIMIT ? <div className="agent-source-more">还有 {summary.urls.length - SOURCE_LINK_LIMIT} 个来源在过程事件中。</div> : null}
                </>
              ) : (
                <div className="agent-empty">暂未从工具事件中识别到来源链接。</div>
              )}
            </div>
          ) : null}

          {observeTab === "process" ? (
            <div className="agent-flow-wrap">
              <div className="agent-flow-toolbar">
                <span>
                  {summary.events.length} 条事件
                  {flowExpanded && summary.events.length > EXPANDED_EVENT_COUNT ? ` · 显示最近 ${EXPANDED_EVENT_COUNT} 条` : ""}
                </span>
                <button type="button" onClick={() => setFlowExpanded((open) => !open)}>
                  {flowExpanded ? <UpOutlined /> : <DownOutlined />} {flowExpanded ? `只看最近 ${COMPACT_EVENT_COUNT} 条` : `展开最近 ${EXPANDED_EVENT_COUNT} 条`}
                </button>
              </div>
              <div className="agent-flow">
                {visibleEvents.map((event, index) => {
                  const detail = eventText(event.detail);
                  return (
                    <div className={`agent-flow-item ${eventText(event.status) || "idle"}`} key={`${eventText(event.source)}-${index}-${eventText(event.elapsed_ms)}`}>
                      <div className="agent-flow-route">
                        <span>{eventText(event.source) || "source"}</span>
                        <span>→</span>
                        <span>{eventText(event.target) || "target"}</span>
                      </div>
                      <div className="agent-flow-label">{eventText(event.label)}</div>
                      {detail ? (
                        <div className="agent-flow-detail" title={detail}>
                          {compactText(detail)}
                        </div>
                      ) : null}
                      <div className="agent-flow-meta">
                        {event.elapsed_ms != null ? <span>{formatDuration(event.elapsed_ms)}</span> : null}
                        {event.tokens != null ? <span>{eventText(event.tokens)} tokens</span> : null}
                        {event.rows != null ? <span>{eventText(event.rows)} rows</span> : null}
                      </div>
                    </div>
                  );
                })}
                {!summary.events.length ? <div className="agent-empty">等待一次完整分析流程。</div> : null}
              </div>
            </div>
          ) : null}
        </aside>
      </div>

      <div className={settingsOpen ? "agent-settings open" : "agent-settings"}>
        <div className="agent-settings-backdrop" onClick={() => setSettingsOpen(false)} />
        <div className="agent-settings-panel">
          <div className="agent-panel-head">
            <div>
              <div className="agent-panel-title">设置</div>
              <div className="agent-panel-sub">当前平台使用的 Agent 服务状态。</div>
            </div>
            <Button size="small" onClick={() => setSettingsOpen(false)}>
              关闭
            </Button>
          </div>
          <div className="agent-setting-row">
            <span>默认 Provider</span>
            <strong>{status?.default_provider || "-"}</strong>
          </div>
          <div className="agent-setting-row">
            <span>模型</span>
            <strong>{status?.current?.default_model || "-"}</strong>
          </div>
          <div className="agent-setting-row">
            <span>Base URL</span>
            <strong>{status?.current?.base_url || "-"}</strong>
          </div>
          <div className="agent-setting-row">
            <span>API Key</span>
            <strong>{status?.current?.has_api_key ? "已加载" : "未加载"}</strong>
          </div>
          <div className="agent-setting-row">
            <span>最近会话</span>
            <strong>{sessions.length}</strong>
          </div>
          <div className="agent-setting-row">
            <span>当前会话</span>
            <strong>{activeSessionId}</strong>
          </div>
        </div>
      </div>
    </div>
  );
}
