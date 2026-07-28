# 23-SSE 流式事件返回

## 这一层解决什么问题

Agent 任务可能要跑几十秒甚至更久。用户不能只看一个 loading。前端需要看到任务状态、工具调用、工具结果、最终答案或错误。

SSE 流式事件返回解决的是：后端如何把异步 Agent Job 的过程推给前端，并支持断线续传。

这层的工程意义不只是“实时显示”。它让长任务从黑盒等待变成可解释过程。

Agent 运行中会发生很多中间状态：

```text
queued
running
model_turn
tool_use
tool_result
contract_reminder
fallback
final
error
```

如果前端只轮询最终 status，用户不知道系统是在检索、生成 PPT、等待工具，还是已经失败。SSE 把这些中间事件流式推给前端，改善体验，也方便调试。

## 最小模式

```mermaid
flowchart TD
    UI[Frontend] --> SUB[GET /api/agent/chat_jobs/{job_id}/events]
    SUB --> HIST[get_events_after after_seq]
    HIST --> SEND1[补发历史事件]
    SEND1 --> LIVE[subscribe job queue]
    LIVE --> SEND2[推送新事件]
    SEND2 --> TERM{final/error/cancelled?}
    TERM -- no --> LIVE
    TERM -- yes --> CLOSE[关闭 SSE]
```

## 加上这一层后 Loop 怎么变化

没有 SSE：

```text
前端只能轮询 status
用户不知道 Agent 正在做什么
```

有 SSE：

```text
后端每产生一个事件就推给前端
前端能展示 running/tool_use/tool_result/final/error
断线后用 Last-Event-ID 续传
```

为什么用 SSE 而不是简单轮询？

| 方案 | 问题 |
|---|---|
| 轮询 status | 粒度粗，延迟高，工具过程不可见 |
| WebSocket | 双向能力强，但连接管理更复杂 |
| SSE | 单向事件流足够，浏览器原生支持，适合 job 事件推送 |

这里前端主要是接收后端事件，不需要高频双向通信，所以 SSE 是更轻的选择。

## 我们项目里的真实源码

核心源码：

- `backend/app/routers/agent_jobs.py`
- `backend/app/services/agent_jobs.py`
- `backend/app/services/agent_job_persistence.py`

SSE endpoint：

```text
GET /api/agent/chat_jobs/{job_id}/events
```

支持参数：

```text
after_seq
Last-Event-ID
```

输出格式：

```text
id: {seq}
event: {event_type}
data: {json payload}

```

对应函数：

```text
_sse_event(event_type, seq, payload)
```

## 事件如何补发和订阅

`stream_chat_job_events()` 做了几件事：

1. 检查 job 是否存在。
2. 检查当前 principal 是否有权限访问 job。
3. 如果请求带 `Last-Event-ID`，更新 `after_seq`。
4. `service.subscribe(job_id)` 获取订阅队列。
5. 先 `get_events_after(job_id, after_seq)` 补发历史事件。
6. 如果 job 已经是终态，直接结束。
7. 否则循环等待新事件。
8. 15 秒没有新事件时，回查持久化事件；仍没有则发送 keepalive。

## 终态事件

遇到这些事件会结束流：

```text
final
error
cancelled
```

这和 job terminal status 对应。

## 为什么需要 Last-Event-ID

浏览器或网络断开后，客户端可以带上最后收到的事件 id：

```text
Last-Event-ID: 12
```

服务端会从 seq > 12 的事件继续补发。这样不会因为短暂断线丢掉工具结果或 final。

## 面试官可能怎么问

### 问：你们 Agent 是怎么流式返回的？

30 秒回答：

> 前端订阅后端的 SSE endpoint：`/api/agent/chat_jobs/{job_id}/events`。后端会把 AgentJobService 中的事件按 SSE 格式推送，包含递增 seq、event_type 和 payload。final/error/cancelled 是终态事件。

2 分钟展开：

> 创建 job 后，前端拿到 events_url。订阅时后端先按 after_seq 或 Last-Event-ID 补发历史事件，再订阅 live queue。执行过程中，running、tool_use、tool_result、final 等事件都会推给前端。15 秒没事件时发 keepalive，并回查持久化事件避免漏消息。

源码级追问：

> 实现在 `backend/app/routers/agent_jobs.py` 的 `stream_chat_job_events()`。SSE 格式由 `_sse_event()` 生成。事件数据来自 `AgentJobService.get_events_after()` 和 `subscribe()`。

### 问：为什么不用 WebSocket？

30 秒回答：

> 这里主要是服务端向前端单向推送 Agent 事件，SSE 足够简单，而且天然有 event id 和重连语义。

### 问：断线会不会丢事件？

30 秒回答：

> 不应该。客户端可以带 Last-Event-ID，服务端会补发该 seq 之后的事件；同时事件也会持久化。

## 容易踩坑

- 不要把 SSE event 和模型 token streaming 混为一谈。这里推的是 job event。
- 不要说只靠内存队列，代码会回查 persistence。
- 不要忘记权限检查，跨租户 job 返回 404，避免泄露。

## 本层小结

SSE 是前端体验和任务可恢复性的关键。它把后端异步 job 的内部进度变成用户可见的事件流。
