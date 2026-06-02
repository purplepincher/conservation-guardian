# Conservation Guardian

A **generic** Workflow Conservation Engine — analyze any workflow for cost efficiency and detect waste.

> Your workflow costs $12/day. Two nodes account for 78% of tokens. They both call GPT-4 for tasks GPT-4o-mini handles.

Conservation Guardian is a framework-agnostic Python library that analyzes workflow execution for cost efficiency and detects waste. It doesn't change your workflows — it tells you what to change and why. Works with any workflow engine (Dify, n8n, LangGraph, Temporal, custom DAGs, etc.).

## What It Does

**Budget tracking** — Set hard limits on tokens per run, cost per day, and node count. Know immediately when a workflow crosses a threshold.

**DAG analysis** — Parse any workflow JSON and surface:
- Redundant LLM calls (same model, same upstream, same job)
- Dead branches (conditional paths that never execute)

**Per-node profiling** — Track tokens in/out, latency, and cost for every node across runs. Spot degradation before it hurts.

**Waste detection** — Find the expensive stuff nobody notices:
- **Overprompted nodes** — 4,200 tokens in, 180 out. You're paying for context the model ignores.
- **Idle nodes** — Running every time, contributing 0.1% of value.
- **Model mismatch** — Using GPT-4 for classification that GPT-4o-mini handles in 12ms.

**Reports** — Markdown summaries you can paste into Slack, Notion, or a PR comment.

## Install

```bash
pip install conservation-guardian
```

## Quick Start

```python
from conservation_guardian.budget import WorkflowBudget
from conservation_guardian.analyzer import WorkflowDAG
from conservation_guardian.profiler import Profiler, NodeSample
from conservation_guardian.detector import WasteDetector
from conservation_guardian.report import render_report

# 1. Load a workflow (generic — works with any engine's JSON)
dag = WorkflowDAG.from_dict(workflow_json)
print(f"{len(dag.llm_nodes())} LLM nodes, {len(dag.redundant_llm_calls())} redundant")

# 2. Profile some runs
profiler = Profiler()
profiler.record(NodeSample(
    node_id="summarizer",
    input_tokens=4200,
    output_tokens=180,
    latency_ms=820.0,
    cost_usd=0.015,
))

# 3. Detect waste
detector = WasteDetector(profiler)
findings = detector.detect()
for f in findings:
    print(f"[{f.severity}] {f.message}")
    print(f"  → {f.suggestion}")

# 4. Generate report
budget = WorkflowBudget()
report = render_report(budget=budget, dag=dag, profiler=profiler, findings=findings)
print(report)
```

## Differences from dify-workflow-guardian

- **Framework-agnostic**: No Dify-specific assumptions in the analyzer
- **Extended node types**: Recognizes `llm`, `llm-chain`, `chat-model`, `switch`, `conditional`, and more
- **Same API**: Drop-in replacement — just change the import package name

## Module Structure

| File | Purpose |
|------|---------|
| `budget.py` | `WorkflowBudget` — token/cost/node limits and daily tracking |
| `analyzer.py` | `WorkflowDAG` — parse workflow JSON, find redundancies and dead branches |
| `profiler.py` | `Profiler`, `NodeProfile`, `NodeSample` — per-node stats and trends |
| `detector.py` | `WasteDetector`, `WasteFinding` — surface actionable waste |
| `report.py` | `render_report()` — Markdown conservation reports |

## Tests

```bash
python -m pytest tests/ -v
```

## Philosophy

Conservation Guardian doesn't optimize your workflows. It tells you where the money goes and what to do about it. The fixes are yours to make — but at least you'll know where to look.

Built for [SuperInstance](https://github.com/SuperInstance).

## License

MIT
