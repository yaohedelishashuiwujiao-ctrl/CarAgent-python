# Initial Agent Evaluation Findings

Date: 2026-07-17

## What is implemented

- `evals/run_eval.py` runs versioned suites against the current backend or the
  upstream Clawd adapter.
- `evals/suites/calibration.json` contains the first 8 low-cost calibration
  cases across routing, parallelism, data correctness, artifact generation,
  boundaries, and no-tool reasoning.
- `evals/adapters/upstream_clawd.py` runs the copied upstream Clawd source as a
  comparison target.
- `tests/test_eval_harness.py` covers the harness parser and scorer basics.

## First calibration results

Current runtime, `parallel_field_catalog`:

- Result: pass, score 1.0
- Tool behavior: two `SubjectsAttributeLookup` calls in one parallel batch
- Invalid tool results: 0
- Median run time: 27.24 s
- Estimated cost at 0.006/0.030 yuan per 1k tokens: 0.1369 yuan

Upstream Clawd, `simple_vehicle_fact`:

- Result: fail, score 0.2
- Final text: `[Max tool turns reached]`
- Tool behavior: 35 calls across Bash, Read, WebSearch, and WebFetch
- Missing capability: no governed `SubjectsAttributeLookup` or `SubjectsSqlQuery`
- Runtime: 115.61 s
- Tokens: 500,532 input, 2,882 output
- Estimated cost at 0.006/0.030 yuan per 1k tokens: 3.0897 yuan

Interpretation: upstream can reason on normal text, but on governed platform
data it lacks the product tool surface and fails by repeated exploration. This
is not primarily a model IQ issue; it is runtime/tool-surface/tool-boundary
behavior.

## Phase 1 L0 routing results

Implemented a zero-model-cost L0 TaskRouter for two high-confidence deterministic
paths:

- `single_vehicle_attribute_query`
- `field_catalog_query`

Current runtime, `simple_vehicle_fact` after L0 routing:

- Result: pass, score 1.0
- Tool behavior: one `SubjectsAttributeLookup` call with extracted entity and
  attribute
- Model usage: 0 input tokens, 0 output tokens
- Runtime: 1.91 s
- Estimated model cost: 0 yuan

Current runtime, `parallel_field_catalog` after L0 routing:

- Result: pass, score 1.0
- Tool behavior: two `SubjectsAttributeLookup` calls in one deterministic
  parallel batch
- Model usage: 0 input tokens, 0 output tokens
- Runtime: 1.03 s
- Estimated model cost: 0 yuan

Interpretation: high-confidence platform data lookups do not need to enter the
full agent loop. The fixed path keeps governed evidence, citations, tool audit,
and parallel metrics while avoiding model-planning cost entirely.

## Phase 1 tool scheduling results

Implemented `ToolCallScheduler` above `ToolRegistry.dispatch`.

Scheduler responsibilities:

- Normalize tool inputs before scheduling.
- Run deterministic preflight before occupying execution pools.
- Reject duplicate calls in the same batch.
- Keep read-only, idempotent, parallel-safe calls in one batch.
- Emit scheduler decisions and parallel batch audit events.

After scheduler integration:

- L0 `simple_vehicle_fact`: pass, score 1.0, runtime 1.06 s, 0 model tokens,
  one tool call.
- L0 `parallel_field_catalog`: pass, score 1.0, runtime 1.07 s, 0 model tokens,
  two tool calls, one scheduler-approved parallel batch.
- Core runtime tests: 64 passed.

The scheduler is now used by deterministic workflows and by the Agent Loop's
parallel execution branch. Registry and execution policy still enforce the final
security, permission, timeout, and resource-pool checks.

Added `tool_scheduler_ledger` to runtime/backend final metadata:

- requested/dispatched/rejected totals
- outcome status counts
- reason-code counts
- bounded recent batch summaries
- run-level call fingerprints for deterministic workflow dedupe

Latest L0 `simple_vehicle_fact` smoke after ledger export: pass, score 1.0,
0.97 s, 0 model tokens, one dispatched tool call, ledger present in final
metadata.

## Phase 1 run budget telemetry

Added `RunBudget` as a separate runtime accounting object. It is not the same
as context-window compaction; it tracks cost and progress risk signals:

- input/output/total tokens
- model turns
- tokens after last progress
- tool requested/dispatched/rejected counts
- low-yield tool action count
- bounded budget events

The object is now exported in final metadata as `run_budget` for deterministic
workflows and Agent Loop runs. Latest L0 `simple_vehicle_fact` smoke: pass,
0.70 s, 0 model tokens, one dispatched tool call, `run_budget` and
`tool_scheduler_ledger` both present.

## Bugs found while evaluating

- Ark Responses provider advertised OpenAI-style `tool_choice` support that the
  current Doubao Responses deployment rejects. Fixed by making provider
  capabilities explicit instead of assuming SDK wire compatibility.
- The calibration scorer was initially too strict about exact tool name and
  treated an equivalent `SubjectsAttributeLookup` result as failure. Fixed by
  allowing semantically equivalent tool alternatives.
- Upstream adapter initially marked `[Max tool turns reached]` as `succeeded`.
  Fixed so future runs report `max_turns_reached`.

## What this says about production readiness

The evaluation must separate these layers:

- Model capability: whether the model can reason and write a coherent answer.
- Runtime capability: routing, invalid-call prevention, parallel execution,
  recovery, plan completion, and stopping criteria.
- Product capability: whether the required governed data tools and evidence
  paths exist.
- Platform reliability: queueing, cancellation, idempotency, concurrency, and
  cost control.

The biggest current design gap is not "more budget" or "fewer calls". It is a
missing negative-routing mechanism: when no available tool can satisfy a request,
the runtime should produce a bounded, evidence-aware failure or clarification
instead of spending many turns on weak substitutes.

## Next release-gate metrics

- Correct-tool rate
- Invalid-call rate
- Max-turn-reached rate
- Unproductive exploration tokens
- Ready-call batching rate
- Citation-supported claim rate
- Artifact validity
- Queue wait and terminal-state correctness
- Pass@1 and pass^k on deterministic data tasks
