# SubjectsAgent Evaluation

This directory contains reproducible, versioned evaluations for the production
data-analysis Agent. The goal is to separate model quality from Runtime quality
and from product-level tool coverage.

## Comparison tracks

1. **Runtime-controlled** — current Runtime and upstream Clawd use the same
   model, portable tools, workspace, permissions, timeout, and task inputs.
2. **Product end-to-end** — each system runs with its actual production tool
   surface. Tool availability is part of the measured product capability.
3. **Current-Runtime ablation** — parallel execution, plan completion contract,
   routing, and context compaction are toggled independently.
4. **Reference ceiling** — optional Claude/Codex runs are reported separately;
   they are not causal baselines because the model and Runtime both differ.

Never merge scores from these tracks into one unexplained leaderboard.

## Evaluation dimensions

| Dimension | Primary metric |
|---|---|
| End-to-end completion | deterministic task pass rate, expert pass rate |
| Tool routing and arguments | correct-tool rate, invalid-call rate, hallucinated-tool rate |
| Long-horizon control | plan completion, recovery rate, pass^k |
| Data correctness | execution accuracy, requested-field coverage, numeric/unit consistency |
| Evidence quality | citation validity, claim support, source relevance |
| Efficiency | wall time, first-action latency, model turns, tokens, cost |
| Parallelism | ready-call batching rate, batch width, serial/parallel latency ratio |
| Robustness | recovery from empty results, schema mismatch, timeout, dependency failure |
| Safety | authorization, SQL policy, SSRF/path policy, prompt injection |
| Platform reliability | queue wait, cancellation, idempotency, concurrency, terminal-state correctness |
| Deliverable quality | artifact validity plus blinded domain-expert rubric |

The dimensions follow established benchmark patterns: BFCL-style function and
parallel-call accuracy, GAIA-style real-world multi-step completion, tau-bench
style repeated reliability, and BIRD-style SQL execution correctness and
efficiency. Product-specific evidence, security, and platform metrics are added
because public benchmarks do not cover our governed data environment.

References:

- BFCL: https://gorilla.cs.berkeley.edu/leaderboard
- GAIA: https://arxiv.org/abs/2311.12983
- tau-bench: https://arxiv.org/abs/2406.12045
- BIRD: https://bird-bench.github.io/

## Experimental controls

- Freeze model deployment, provider API, temperature/options, database snapshot,
  document index version, tool schemas, permissions, system prompt, and timeout.
- Give every target a clean session and workspace.
- Run deterministic cases at least three times and report pass@1 and pass^k.
- Randomize target order to reduce provider-time and cache bias.
- Score deterministic assertions before any LLM judge.
- Blind expert and pairwise reviews to target identity.
- Store raw traces; never score only the final prose.
- Report failures and partial completions, not only averages.

## Initial suites

- `calibration.json`: small, inexpensive suite used to validate the Harness.
- `core_data_analysis.json`: governed SQL, aggregation, evidence, and artifacts.
- `tool_routing.json`: single, multiple, parallel, irrelevant, and malformed calls.
- `robustness_security.json`: dependency faults, policy boundaries, injection.
- `platform_reliability.json`: concurrency, queueing, cancellation, idempotency.

Only the calibration suite is checked in initially. Gold answers for larger
suites must be generated from frozen database/document snapshots and reviewed by
a domain expert before they become release gates.

## Run

```bash
python3 evals/run_eval.py \
  --suite evals/suites/calibration.json \
  --target current \
  --base-url http://127.0.0.1:8000 \
  --repetitions 1 \
  --output /tmp/subjects-agent-calibration.json
```

The output includes the manifest, raw per-run records, per-case scores, and
aggregate operational metrics. A calibration run is diagnostic and is not a
release gate.

## RAG Retrieval Eval

The current LlamaIndex Agentic RAG retriever can be evaluated with:

```bash
python3 scripts/evaluate_current_rag.py \
  --suite evals/suites/rag_pilot_v1.json \
  --out evals/results/llamaindex_light_rag_pilot_v1.json
```

The report includes hit rate, recall, MRR, latency percentiles, returned
document IDs, returned chunk IDs, and the frozen RAG index configuration.
