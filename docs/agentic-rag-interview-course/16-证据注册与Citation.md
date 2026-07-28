# 16-证据注册与 Citation

## 这一层解决什么问题

RAG/SQL/Web 工具返回的信息如果不进入证据系统，模型最后可能引用混乱、编造来源、或者把无证据内容说成事实。

证据注册与 Citation 让最终答案可追溯。

这层不是简单“在答案后面加引用”。它解决的是事实回答的归因问题。

在 Agentic RAG 里，证据来源不止 RAG：

| 来源 | 例子 |
|---|---|
| SQL | 车型参数、统计结果、结构化配置 |
| RAG | 用户手册、论文、维修资料 chunk |
| WebFetch | 网页内容 |
| Tool artifact | 图表、PPT、导出的中间结果 |

如果不统一注册证据，模型最后很可能把 SQL 数值、RAG 文本和自己推理混在一起。Citation 系统把工具结果先登记成证据，再要求最终答案引用已登记的 citation id。

## 最小模式

```mermaid
flowchart TD
    TOOL[工具结果] --> EXTRACT[抽取 evidence candidate]
    EXTRACT --> DEDUPE[按 citation key 去重]
    DEDUPE --> ID[分配 citation_id]
    ID --> HASH[生成 evidence_hash]
    HASH --> LEDGER[写入 citations / evidence ledger]
    LEDGER --> ANSWER[最终答案引用 [1] [2]]
    ANSWER --> REPAIR[引用修复 / claim validation]
```

## 加上这一层后 Loop 怎么变化

没有证据系统：

```text
模型看到工具结果
最终答案可能不引用或乱引用
```

有证据系统：

```text
工具结果被注册成 citation
最终事实声明必须引用有效 citation id
引用错误会触发修复
```

这里的关键设计是：citation id 不是模型随便编的。

正确流程是：

```text
工具结果 -> 抽取 evidence candidate -> 去重 -> 分配 citation_id -> final answer 引用这些 id
```

如果模型引用了不存在的编号，Runtime 可以发现并修复。这比“请你提供引用”的 prompt 可靠得多。

## 我们项目里的真实源码

核心文件：

- `agent_runtime/src/tool_system/agent_loop.py`
- `backend/app/services/agent_job_persistence.py`

关键函数：

- `register_evidence()`
- `_extract_evidence_candidates()`
- `_citation_key()`
- `repair_citations_if_needed()`
- `_format_citation_list()`

持久化表：

- `agent_evidence_snapshot`
- `agent_claim`
- `agent_context_snapshot`

## 关键参数 / 数据结构

Citation item 关键字段：

| 字段 | 说明 |
|---|---|
| `citation_id` | 最终答案引用编号 |
| `source_type` | SQL / knowledge / web_fetch 等 |
| `source` | 来源工具或 URL |
| `title` | 证据标题 |
| `content` | 证据内容快照 |
| `metadata` | 数据版本、chunk_id、行数等 |
| `evidence_hash` | 证据哈希 |
| `tool_name` | 产生证据的工具 |

## 面试官可能怎么问

### 问：RAG 答案怎么保证有引用？

30 秒回答：

> RAG 和其他工具结果会被 Runtime 注册成 citation，分配编号和 evidence_hash。最终答案如果引用缺失或引用了不存在的编号，会触发 citation repair，让模型只基于已注册证据重写答案。

2 分钟展开：

> 我们不是让模型自己随便写来源。工具结果进入 Agent Loop 后，会抽取 evidence candidate，去重后分配 citation_id。最终 factual claim 需要引用这些 id。对于结构化数据问题，如果 SQL 证据里有数值，Runtime 还会要求最终答案优先引用 SQL citation，避免模型说“无法确认”。

源码级追问：

> `register_evidence()` 在 `agent_loop.py` 内部定义。它会对 candidate 生成 `evidence_hash`，加入 `citations` 列表。`repair_citations_if_needed()` 会检查最终文本中的 bracket ids 是否属于 valid ids；如果不合法，会再调用模型做证据约束重写。

### 问：如果模型引用了不存在的 [99] 呢？

30 秒回答：

> Runtime 会检测 citation id 是否有效，无效就触发引用修复，要求模型只能使用已有 citation ids。

## 如果继续追问到细节

可以说：

- SQL/Knowledge/Web evidence 都可以进入 citation。
- Evidence ledger 在 RunState 中保留摘要，完整证据在 final metadata 和持久化层。
- claim 和 evidence snapshot 可用于审计。

## 本层小结

Citation 不是展示格式，而是事实可信度和审计链路的一部分。
