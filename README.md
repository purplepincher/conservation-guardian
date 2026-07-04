# Conservation Guardian

A **generic** Workflow Conservation Engine — analyze any workflow for cost efficiency and detect waste.

> Your workflow costs $12/day. Two nodes account for 78% of tokens. They both call GPT-4 for tasks GPT-4o-mini handles.

## Install

```bash
pip install conservation-guardian
```

This installs both the Python library and a `conservation-guardian`
command-line wrapper (see [CLI wrapper](#cli-wrapper-wrap-any-command-in-a-budget) below).

## Quick Start

```python
from conservation_guardian import (
    Profiler, NodeSample, WasteDetector, Reporter, WorkflowBudget
)

# 1. Profile some runs
profiler = Profiler()
profiler.record(NodeSample(
    node_id="summarizer", input_tokens=4200, output_tokens=180,
    latency_ms=820.0, cost_usd=0.015, node_title="Summarizer",
))

# 2. Detect waste
findings = WasteDetector(profiler).detect()
for f in findings:
    print(f"[{f.severity}] {f.message}")

# 3. Generate report
budget = WorkflowBudget()
report = Reporter(budget=budget, profiler=profiler, findings=findings, workflow_name="My Workflow")
print(report.to_markdown())
```

## API Reference

### WorkflowBudget

Token, cost, and node-count limits for workflow execution.

```python
from conservation_guardian import WorkflowBudget

budget = WorkflowBudget(
    max_tokens_per_run=500_000,   # Max tokens per single run
    max_cost_per_day=50.0,         # Max USD spend per day
    max_nodes_per_workflow=100,    # Max nodes in a workflow
    price_input_per_1k=0.03,      # Input token pricing
    price_output_per_1k=0.06,     # Output token pricing
)

# Pre-flight check
if budget.is_within_budget(input_tokens=100_000, output_tokens=50_000):
    cost = budget.record_run(100_000, 50_000)

# Query
budget.daily_spend()         # Today's total
budget.avg_tokens_per_run()  # Average across all runs
```

### Profiler

Collect and query per-node execution profiles.

```python
from conservation_guardian import Profiler, NodeSample

profiler = Profiler(degradation_window=10)

# Record samples
profiler.record(NodeSample(
    node_id="summarizer", input_tokens=4200, output_tokens=180,
    latency_ms=820.0, cost_usd=0.015, node_title="Summarizer",
))

# Query
profile = profiler.get("summarizer")
profile.run_count            # Number of samples
profile.avg_input_tokens     # Average input tokens
profile.avg_output_tokens    # Average output tokens
profile.avg_latency_ms       # Average latency
profile.avg_cost             # Average cost per run
profile.total_cost           # Total cost across all runs
profile.input_output_ratio   # Input/output token ratio
profile.is_degrading()       # True if latency trending up

# Top N
profiler.top_by_cost(5)
profiler.top_by_tokens(5)
```

#### Persistence

```python
# Save / load
profiler.save("profile.json")
loaded = Profiler.load("profile.json")

# Trend analysis
trends = current_profiler.compare(previous_profiler)
# → [{"metric": "cost", "direction": "worse", "detail": "..."}]
```

### WasteDetector

Analyze profiler data to surface actionable waste findings.

```python
from conservation_guardian import WasteDetector

detector = WasteDetector(
    profiler,
    max_io_ratio=15.0,                # Input/output ratio threshold
    low_utilization_threshold=0.1,     # Cost fraction below which node is "underused"
    expensive_model_ratio=0.8,         # Cost concentration threshold
    expensive_model_min_samples=5,     # Min samples before concentration check
    degradation_window=5,              # Window for degradation detection
)

findings = detector.detect()
for f in findings:
    f.node_id      # "summarizer"
    f.category     # "overprompted", "low_utilization", "expensive_model"
    f.severity     # "high", "medium", "low"
    f.message      # Human-readable description
    f.suggestion   # What to do about it
```

### WorkflowDAG

Parse and analyze workflow structure.

```python
from conservation_guardian import WorkflowDAG

dag = WorkflowDAG.from_dict(workflow_json)
dag.llm_nodes()             # All LLM-type nodes
dag.redundant_llm_calls()   # Pairs of duplicate LLM nodes
dag.dead_branches()         # Unreachable paths
```

### Reporter

Multi-format report generation.

```python
from conservation_guardian import Reporter

reporter = Reporter(
    budget=budget,
    dag=dag,
    profiler=profiler,
    findings=findings,
    workflow_name="My Workflow",
)

reporter.to_markdown()    # Markdown report
reporter.to_json()        # JSON for dashboards
reporter.to_prometheus()  # Prometheus metrics
reporter.to_slack()       # Slack Blocks JSON
```

### Adapters

Extract `NodeSample` data from external systems.

```python
from conservation_guardian.adapters import GenericAdapter, OpenAIAdapter, LangChainAdapter

# Generic: configurable field mapping
adapter = GenericAdapter(
    records=[{"name": "node1", "tokens_in": 100, "tokens_out": 50, "time_ms": 200}],
    field_map={"node_id": "name", "input_tokens": "tokens_in", "output_tokens": "tokens_out", "latency_ms": "time_ms"},
)
samples = adapter.extract_samples()

# OpenAI: auto-pricing by model
adapter = OpenAIAdapter([
    {"model": "gpt-4o", "usage": {"prompt_tokens": 1000, "completion_tokens": 200}},
])

# LangChain: parses callback data
adapter = LangChainAdapter([
    {"llm_output": {"token_usage": {"prompt_tokens": 500}, "model_name": "gpt-4"}},
])

# From file
adapter = GenericAdapter(path="runs.jsonl")
```

### Exceptions

```python
from conservation_guardian import BudgetExceededError, InvalidProfileError, AdapterError

try:
    raise BudgetExceededError("Daily limit hit", metric="daily_cost", current=60.0, limit=50.0)
except BudgetExceededError as e:
    print(e.metric, e.current, e.limit)
```

### CLI wrapper — wrap any command in a budget

The package also ships a `conservation-guardian` CLI that wraps an arbitrary
subprocess and enforces a budget around it. This lets you govern coding-agent
CLIs (opencode, aider, kimi-style tools) — or any other command — without
needing them to import this library.

```bash
# Hard wall-clock budget: kill the child on exceed (exit 124)
conservation-guardian run --max-time-seconds 600 -- opencode exec "refactor foo.py"

# Best-effort token budget: scans child stdout/stderr for token-usage telemetry
# (OpenAI / Anthropic / generic key=value formats) and kills on exceed (exit 125)
conservation-guardian run --max-tokens 100000 -- aider --model gpt-4o

# Emit a JSON run report alongside the normal passthrough
conservation-guardian run --max-time-seconds 300 --report run.json -- my-agent "$TASK"
```

Budget semantics:

| Flag | Enforcement | Exit on exceed |
|------|-------------|----------------|
| `--max-time-seconds N` | **Hard** — child is SIGTERM'd (then SIGKILL'd) on timeout | `124` |
| `--max-tokens N` | **Best-effort** — requires the child to emit token usage; otherwise a no-op | `125` |

The child's `stdout`/`stderr` are streamed through unchanged so interactive
behavior is preserved. `--report PATH` writes a JSON record
(`started_at`, `finished_at`, `duration_seconds`, `exit_code`,
`killed_reason`, `tokens_detected`, …). Launch failures exit `126`.
Run `conservation-guardian --help` for the full synopsis.

## Module Structure

| Module | Purpose |
|--------|---------|
| `budget.py` | `WorkflowBudget` — token/cost/node limits and daily tracking |
| `analyzer.py` | `WorkflowDAG` — parse workflow JSON, find redundancies and dead branches |
| `profiler.py` | `Profiler`, `NodeProfile`, `NodeSample` — per-node stats and trends |
| `detector.py` | `WasteDetector`, `WasteFinding` — surface actionable waste |
| `report.py` | `render_report()` — Quick Markdown rendering |
| `reporter.py` | `Reporter` — Multi-format reports (JSON, Prometheus, Slack) |
| `adapters/` | Data source adapters (Generic, OpenAI, LangChain) |
| `cli.py` | CLI wrapper (`run` subcommand) for budgeting arbitrary subprocesses |
| `exceptions.py` | Custom exceptions |

## Examples

See [`examples/`](examples/) for complete runnable scripts:

- [`basic_usage.py`](examples/basic_usage.py) — Minimal 10-line example
- [`langchain_integration.py`](examples/langchain_integration.py) — LangChain callback data
- [`budget_enforcement.py`](examples/budget_enforcement.py) — Fail-fast on budget overflow
- [`historical_tracking.py`](examples/historical_tracking.py) — Save, load, compare over time

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for the full design walkthrough.

The pipeline is: **Budget → Profile → Detect → Report**

## Development

```bash
pip install -e ".[dev]"          # installs pytest, ruff, mypy
python -m pytest tests/ -v
ruff check src/ tests/
mypy src/ --ignore-missing-imports
```

## License

MIT
