#!/usr/bin/env python3
from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets" / "agent_architecture"

COMMON = """
graph [
  rankdir=TB,
  bgcolor="white",
  pad="0.22",
  nodesep="0.28",
  ranksep="0.48",
  splines=ortho,
  fontname="Noto Sans CJK SC",
  labelloc="t",
  fontsize=24
];
node [
  shape=box,
  style="rounded,filled",
  fontname="Noto Sans CJK SC",
  fontsize=13,
  margin="0.12,0.08",
  color="#3e5f6d",
  penwidth=1.1,
  fillcolor="#f7fafb"
];
edge [
  fontname="Noto Sans CJK SC",
  fontsize=10,
  color="#627b87",
  arrowsize=0.65
];
"""


DIAGRAMS: dict[str, str] = {
    "01_overall_closed_loop": rf"""
digraph G {{
{COMMON}
graph [label="01 总览：Subjects Agent 端到端闭环"];

user [label="用户问题\nprompt / session / tenant-user", fillcolor="#eef7ff"];
frontend [label="前端 AgentPage\n输入、SSE 订阅、过程展示", fillcolor="#eef7ff"];
api [label="Agent Jobs API\nPOST /chat_jobs\nGET /events", fillcolor="#eef7ff"];
job [label="AgentJob\n状态机 + 权限快照 + 幂等键", fillcolor="#fff8e8"];
scheduler [label="调度层\nDRR / Session Lock / Dispatch / Worker", fillcolor="#fff8e8"];
runtime [label="通用 Agent Runtime\nrun_agent_loop()", fillcolor="#eaf8ef"];
control [label="控制面\nRouteDecision / TaskContract\nRunState / RunBudget", fillcolor="#eaf8ef"];
model [label="模型回合\nprovider.chat()\n文本或 tool_use[]", fillcolor="#eaf8ef"];
tools [label="工具系统\nToolCallScheduler + ToolRegistry", fillcolor="#edf9f2"];
tool_surface [label="工具能力面\n结构化数据 / 知识检索 / 产物 / 控制交互", fillcolor="#edf9f2"];
evidence [label="证据与状态回写\ncitations / claims / audit / budget", fillcolor="#f7fafb"];
finalize [label="Finalize\n引用修复 + 契约状态 + final_metadata", fillcolor="#eaf8ef"];
publish [label="Worker 发布\nSUCCEEDED / FAILED\nSSE final/error", fillcolor="#f8f6ff"];
output [label="用户可见结果\n回答 / 引用 / 下载产物 / 错误边界", fillcolor="#eef7ff"];

user -> frontend -> api -> job -> scheduler -> runtime -> control -> model;
model -> tools [label="tool_use[]"];
tools -> tool_surface -> evidence -> control [label="下一轮"];
control -> model [label="继续/重规划"];
model -> finalize [label="无 tool_use 且满足"];
evidence -> finalize [label="目标/契约满足"];
finalize -> publish -> output;
publish -> frontend [label="SSE"];
}}
""",
    "02_backend_job_scheduler": rf"""
digraph G {{
{COMMON}
graph [label="02 后端任务调度：从请求入队到 Worker 执行"];

api [label="create_chat_job()\n校验 prompt/session/idempotency", fillcolor="#eef7ff"];
principal [label="Principal\nrole_ids / data_scope / allowed_tools", fillcolor="#eef7ff"];
route [label="estimate_agent_job_route()\nroute / budget / estimated_cost", fillcolor="#fff8e8"];
persist [label="MySQL 持久化\nagent_chat_jobs + queued event", fillcolor="#f8f6ff"];
queue [label="进程内 Admission Queue\nqueue_key = tenant:user", fillcolor="#fff8e8"];
drr [label="DRR 公平调度\ncredit += quantum\ncredit >= estimated_cost 才出队", fillcolor="#fff8e8"];
session [label="Redis Session Lock\n同 session 串行\n防并发上下文冲突", fillcolor="#fff8e8"];
stage [label="stage_dispatch()\nfencing_token++\nexecution_token", fillcolor="#f8f6ff"];
broker [label="Dispatch Broker\nRedis Stream / Memory Queue", fillcolor="#fff8e8"];
worker [label="Worker Loop\nRUNNING / heartbeat / lease", fillcolor="#fff8e8"];
proxy [label="Proxy Runtime\n_execute_proxy_blocking()\n读取 runtime stream", fillcolor="#eaf8ef"];
settle [label="Settle\nACK / retry / failed\n释放 session lock", fillcolor="#f8f6ff"];

api -> principal -> route -> persist -> queue -> drr -> session -> stage -> broker -> worker -> proxy -> settle;
settle -> queue [label="可重试失败", style=dashed];
worker -> persist [label="状态/事件/心跳", style=dashed];
}}
""",
    "03_runtime_control_plane": rf"""
digraph G {{
{COMMON}
graph [label="03 Runtime 控制面：通用 Agent 如何被约束"];

entry [label="run_agent_loop()\n读取用户请求和会话上下文", fillcolor="#eaf8ef"];
route [label="RouteDecision\nroute / model_tier / budget_class\ntool_profile / preferred_tools", fillcolor="#fff8e8"];
contract [label="TaskRequirementState\nOutputContract\n显式交付约束：PPTX/Chart/JSON/Evidence", fillcolor="#fff8e8"];
state [label="AgentRunState\n模型拥有计划\nRuntime 记录 plan/progress/failed_paths/evidence", fillcolor="#fff8e8"];
budget [label="RunBudget\ntokens / model_turns / tool ledger\nlow-yield / degrade", fillcolor="#fff8e8"];
prompt [label="每轮系统上下文\nroute policy + citation policy\nexecution policy + contract reminder\nrun-state ledger", fillcolor="#eaf8ef"];
candidates [label="工具候选集\nprimary -> fallback -> completion_recovery\nroute + auth + toolHints", fillcolor="#eaf8ef"];
model [label="模型回合\n输出文本或 tool_use[]", fillcolor="#eaf8ef"];
review [label="Runtime Review\ncontract 是否满足\n是否停滞/低收益/需要重规划", fillcolor="#f7fafb"];

entry -> route;
entry -> contract;
entry -> state;
entry -> budget;
route -> prompt;
contract -> prompt;
state -> prompt;
budget -> prompt;
prompt -> candidates -> model -> review;
review -> prompt [label="继续/重规划"];
review -> model [label="下一依赖层"];
review -> "Finalize" [label="满足或阻塞"];
}}
""",
    "04_tool_scheduling_execution": rf"""
digraph G {{
{COMMON}
graph [label="04 工具调度执行：准确性与并行/批处理"];

tool_use [label="模型输出 tool_use[]\n同一轮代表同一依赖层", fillcolor="#eaf8ef"];
normalize [label="normalize_call()\n工具声明 input_aliases -> canonical fields", fillcolor="#edf9f2"];
preflight [label="Preflight\n工具存在 / allowlist / 权限 / data_scope\nschema / dependency / coverage", fillcolor="#edf9f2"];
dedupe [label="Dedupe\nbatch 内去重\nrun 级重复调用拦截", fillcolor="#edf9f2"];
decision [label="Scheduler Decision\nbatch / parallel / serial\n写入 tool_scheduler_decision", fillcolor="#edf9f2"];
batch [label="Batch Path\n同一只读工具 + supports_batch\nrun_batch()", fillcolor="#edf9f2"];
parallel [label="Parallel Path\nread-only + idempotent + no side effect\nsupports_parallel", fillcolor="#edf9f2"];
serial [label="Serial Path\n有副作用/依赖关系/不可并行", fillcolor="#edf9f2"];
registry [label="ToolRegistry.dispatch()\n二次权限检查 + execute_tool_with_policy", fillcolor="#edf9f2"];
pool [label="Resource Pool\nmodel / sql / tool / artifact\n并发与超时控制", fillcolor="#fff8e8"];
result [label="ToolResult\noutcome_status / reason_code\nretryable / diagnostics", fillcolor="#f7fafb"];
ledger [label="Scheduler Ledger\nrequested / dispatched / rejected\nstatus_counts / reason_counts", fillcolor="#f7fafb"];

tool_use -> normalize -> preflight -> dedupe -> decision;
decision -> batch;
decision -> parallel;
decision -> serial;
batch -> registry;
parallel -> registry;
serial -> registry;
registry -> pool -> result -> ledger;
}}
""",
    "05_evidence_output_contract": rf"""
digraph G {{
{COMMON}
graph [label="05 证据与输出闭环：为什么不是只生成文字"];

result [label="工具结果\nSQL rows / knowledge chunks / artifact path\nstructured output", fillcolor="#edf9f2"];
compact [label="Compact Observation\n给下一轮模型的压缩观察\n避免上下文爆炸", fillcolor="#eaf8ef"];
full [label="完整结果保留\nfinal_metadata / tool_audit / citations", fillcolor="#f8f6ff"];
extract [label="Evidence Extractor\ncitation_id / evidence_hash\nsource_type / content", fillcolor="#f7fafb"];
claims [label="Claim Check\ninvalid citation / numeric mismatch\nunsupported / weak support", fillcolor="#f7fafb"];
contract [label="TaskContract 更新\nartifact 路径有效性\nslide count / chart file / structured json", fillcolor="#fff8e8"];
repair [label="Citation Repair\n必要时重写答案补引用\n不允许编造证据", fillcolor="#eaf8ef"];
final [label="Final Metadata\ncitations / claims / evidence_status\nrequirements / route / budget / scheduler ledger", fillcolor="#f8f6ff"];
worker [label="Worker 二次校验\noutput_contract_status unmet -> FAILED\n否则 final", fillcolor="#f8f6ff"];
ui [label="前端展示\n回答 + 来源 + 过程 + 预算 + 失败边界", fillcolor="#eef7ff"];

result -> compact -> "下一轮模型";
result -> full -> extract -> claims -> contract -> repair -> final -> worker -> ui;
contract -> "继续调用工具" [label="未满足", style=dashed];
}}
""",
    "06_multi_instance_boundary": rf"""
digraph G {{
{COMMON}
graph [label="06 多实例边界：本次实测发现并修复的问题"];

front [label="前端连接某个 Backend 实例\n例如 8000/8001/8002", fillcolor="#eef7ff"];
api_a [label="Backend A\n接收 Job\n本地 _queues[A]", fillcolor="#fff8e8"];
api_b [label="Backend B\n也可能运行\n本地 _queues[B]", fillcolor="#fff8e8"];
redis_leader [label="旧设计：Redis 全局 Scheduler Leader\n只有一个实例调度", fillcolor="#fdf2f2", color="#9b4a4a"];
stuck [label="错误结果\nA 收到 Job 但 B 是 leader\nA 本地队列无人调度 -> 永远 queued", fillcolor="#fdf2f2", color="#9b4a4a"];

fix_a [label="修复后：每个 API 实例调度自己的本地队列\nAGENT_SCHEDULER_GLOBAL_LEADER=false", fillcolor="#eaf8ef"];
guard [label="跨实例安全边界\nMySQL 状态刷新\nRedis Session Lock\nexecution_token + fencing_token", fillcolor="#eaf8ef"];
dispatch [label="共享 Dispatch Broker\nRedis Stream", fillcolor="#fff8e8"];
worker [label="Worker 执行并持久化终态\n重复/过期 token 被丢弃", fillcolor="#eaf8ef"];

front -> api_a;
front -> api_b [style=dashed, label="可能连接"];
api_a -> redis_leader -> stuck;
api_b -> redis_leader;

api_a -> fix_a [label="修复"];
api_b -> fix_a [label="修复"];
fix_a -> guard -> dispatch -> worker;
worker -> guard [label="状态回写/防重复", style=dashed];
}}
""",
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, source in DIAGRAMS.items():
        dot_path = OUT / f"{name}.dot"
        png_path = OUT / f"{name}.png"
        dot_path.write_text(source.strip() + "\n", encoding="utf-8")
        subprocess.run(["dot", "-Tpng", str(dot_path), "-o", str(png_path)], check=True)
        print(png_path)


if __name__ == "__main__":
    main()
