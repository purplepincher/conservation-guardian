# Conservation Guardian

A Python library and CLI tool for profiling token usage and cost of workflow executions, enforcing budgets around subprocesses, and detecting wasteful LLM call patterns.

## Quickstart

```bash
pip install conservation-guardian
```

Minimal programmatic usage:

```python
from conservation_guardian import Profiler, NodeSample, WasteDetector, Reporter

profiler = Profiler()
profiler.record(NodeSample(
    node_id="summarizer", input_tokens=4200, output_tokens=180,
    latency_ms=820.0, cost_usd=0.015, node_title="Summarizer",
))

findings = WasteDetector(profiler).detect()
for f in findings:
    print(f"[{f.severity}] {f.message}")
```

CLI usage — wrap any command with a time or token budget:

```bash
conservation-guardian run --max-time-seconds 600 -- opencode exec "refactor foo.py"
conservation-guardian run --max-tokens 100000 -- aider --model gpt-4o
conservation-guardian run --max-time-seconds 300 --report run.json -- my-agent "$TASK"
```

## Usage

### 1. Profile nodes programmatically

```python
from conservation_guardian import Profiler, NodeSample

profiler = Profiler(degradation_window=10)

profiler.record(NodeSample(
    node_id="summarizer", input_tokens=4200, output_tokens=180,
    latency_ms=820.0, cost_usd=0.015, node_title="Summarizer",
))

profile = profiler.get("summarizer")
profile.run_count            # 1
profile.avg_input_tokens     # 4200.0
profile.avg_output_tokens    # 180.0
profile.avg_cost             # 0.015
profile.total_cost           # 0.015
profile.input_output_ratio   # 23.33
profile.is_degrading()       # False
```

Save and load:

```python
profiler.save("profile.json")
loaded = Profiler.load("profile.json")
trends = current_profiler.compare(previous_profiler)
# → [{"metric": "cost", "direction": "worse", "detail": "..."}]
```

### 2. Apply a budget

```python
from conservation_guardian import WorkflowBudget

budget = WorkflowBudget(
    max_tokens_per_run=500_000,
    max_cost_per_day=50.0,
    max_nodes_per_workflow=100,
    price_input_per_1k=0.03,
    price_output_per_1k=0.06,
)

if budget.is_within_budget(input_tokens=100_000, output_tokens=50_000):
    cost = budget.record_run(100_000, 50_000)

budget.daily_spend()
budget.avg_tokens_per_run()
```

### 3. Detect waste

```python
from conservation_guardian import WasteDetector

detector = WasteDetector(
    profiler,
    max_io_ratio=15.0,
    low_utilization_threshold=0.1,
    expensive_model_ratio=0.8,
    expensive_model_min_samples=5,
    degradation_window=5,
)

findings = detector.detect()
for f in findings:
    print(f.node_id, f.category, f.severity, f.message, f.suggestion)
```

### 4. Generate reports in multiple formats

```python
from conservation_guardian import Reporter

reporter = Reporter(
    budget=budget,
    dag=dag,
    profiler=profiler,
    findings=findings,
    workflow_name="My Workflow",
)

reporter.to_markdown()
reporter.to_json()
reporter.to_prometheus()
reporter.to_slack()
```

### 5. Parse workflow DAGs

```python
from conservation_guardian import WorkflowDAG

dag = WorkflowDAG.from_dict(workflow_json)

dag.llm_nodes()
dag.redundant_llm_calls()
dag.dead_branches()
```

### 6. Load data from existing systems

```python
from conservation_guardian.adapters import GenericAdapter, OpenAIAdapter, LangChainAdapter

# Generic JSON/JSONL with configurable field mapping
adapter = GenericAdapter(
    records=[{"name": "node1", "tokens_in": 100, "tokens_out": 50, "time_ms": 200}],
    field_map={
        "node_id": "name",
        "input_tokens": "tokens_in",
        "output_tokens": "tokens_out",
        "latency_ms": "time_ms",
    },
)
samples = adapter.extract_samples()

# OpenAI API responses (uses model-based pricing)
adapter = OpenAIAdapter([
    {"model": "gpt-4o", "usage": {"prompt_tokens": 1000, "completion_tokens": 200}},
])

# LangChain callback data (LLMResult dicts)
adapter = LangChainAdapter([
    {"llm_output": {"token_usage": {"prompt_tokens": 500}, "model_name": "gpt-4"}},
])

# From file
adapter = GenericAdapter(path="runs.jsonl")
```

## How it works

The library collects per-node samples (`NodeSample`), each containing input/output token counts, latency, and cost. A `Profiler` aggregates these into per-node profiles (`NodeProfile`) that expose averages, totals, and trends. `WasteDetector` applies configurable thresholds to profiles and returns `WasteFinding` objects for **three** categories: over‑prompted nodes, low‑utilization nodes, and expensive‑model concentration. (Latency degradation is detectable via `NodeProfile.is_degrading()` and `Profiler.compare()`, but `WasteDetector.detect()` does **not** currently emit a degradation finding — see [Capability verification](#capability-verification) below.)

The `WorkflowBudget` tracks running totals of tokens and cost per day; `record_run` checks limits before allowing execution. `WorkflowDAG` parses workflow JSON into a DAG and identifies redundant LLM calls and dead branches.

The CLI (`conservation-guardian run`) wraps an arbitrary subprocess, passes through its stdout/stderr, and enforces two independent budgets:

- **Wall‑clock timeout** (`--max-time-seconds`): hard limit; the child is SIGTERM’d (then SIGKILL’d) and the wrapper exits 124.
- **Token budget** (`--max-tokens`): best‑effort; the wrapper scans the child’s stdout/stderr for token‑usage patterns (OpenAI’s `{"usage": {"prompt_tokens": …}}`, Anthropic‑style, generic `key=value`, or the phrase `"Tokens used: …"`). When cumulative tokens exceed the limit the child is killed and the wrapper exits 125.

A `--report PATH` flag writes a JSON file with timestamps, exit code, kill reason, and detected token counts. Launch failures exit 126.

## CLI options

| Flag | Enforcement | Exit on exceed |
|------|-------------|----------------|
| `--max-time-seconds N` | Hard — child is killed on timeout | 124 |
| `--max-tokens N` | Best‑effort — requires child to emit token usage | 125 |
| `--report PATH` | Writes a JSON run report | — |
| `--workflow-name NAME` | Labels the report for identification | — |

## Constraints and limitations

- Token‑limit enforcement depends on the child process printing token usage in a recognised format. It does **not** inspect internal API calls of the child.
- Token detection operates on line‑by‑line output; a token count that spans a partial buffer may be missed or delayed until the next line.
- Daily spend tracking (`budget.daily_spend()`) resets to zero when the process starts; it is not persisted across restarts unless manually saved/loaded.
- The CLI currently scans for the patterns implemented in `_scan_tokens`; new formats must be added manually.
- Wall‑clock timeout gives a 5‑second grace period after SIGTERM before SIGKILL is sent.
- The library requires Python 3.10+ (matching `pyproject.toml`).

## Project structure

| Module | Purpose |
|--------|---------|
| `budget.py` | `WorkflowBudget` — token/cost/node limits and daily tracking |
| `analyzer.py` | `WorkflowDAG` — parse workflow JSON, find redundancies and dead branches |
| `profiler.py` | `Profiler`, `NodeProfile`, `NodeSample` — per-node stats and trends |
| `detector.py` | `WasteDetector`, `WasteFinding` — surface actionable waste |
| `report.py` | `render_report()` — quick Markdown rendering |
| `reporter.py` | `Reporter` — multi‑format reports (JSON, Prometheus, Slack) |
| `adapters/` | Data source adapters (Generic, OpenAI, LangChain) |
| `cli.py` | CLI wrapper (`run` subcommand) for budgeting arbitrary subprocesses |
| `exceptions.py` | Custom exceptions (`BudgetExceededError`, `InvalidProfileError`, `AdapterError`) |

## License

MIT. See [LICENSE](LICENSE).

## Capability verification

Every claim below was traced to working code and/or a passing test (109 tests,
all green). Markers follow this org's convention:

- ✅ **real today** — traced to working code
- ⚠️ **real but conditional** — works, but needs something external or has caveats
- 🔮 **aspirational / later phase** — described as a direction, not implemented

### ✅ Real today

| Capability | Where in code |
|------------|---------------|
| Per-node profiling: record samples, aggregate averages/totals/trends | `profiler.py::Profiler`, `NodeProfile` — tested in `TestProfiler` |
| `NodeProfile.is_degrading()` — latency trend detection (needs 2×window samples) | `profiler.py` — tested in `test_is_degrading` / `test_not_degrading` |
| `Profiler.compare()` — trend analysis between snapshots (20% change threshold) | `profiler.py` — tested in `test_compare_*` |
| Save/load profiler state to JSON (skips corrupted samples) | `profiler.py::save/load` — tested in `test_save_and_load_roundtrip` |
| Budget enforcement: token/cost/node limits, daily tracking | `budget.py::WorkflowBudget` — tested in `TestWorkflowBudget` |
| Waste detection: 3 categories (overprompted, low_utilization, expensive_model) | `detector.py::WasteDetector.detect` — tested in `TestWasteDetector` |
| DAG analysis: parse JSON, find redundant LLM calls, dead branches | `analyzer.py::WorkflowDAG` — tested in `TestWorkflowDAG` |
| Multi-format reports: Markdown, JSON, Prometheus, Slack | `reporter.py::Reporter` / `report.py::render_report` — tested in `TestReport` |
| Adapters: Generic (dot-notation field mapping), OpenAI (auto-pricing), LangChain | `adapters/` — tested in adapter tests |
| CLI subprocess wrapper: hard timeout (exit 124), best-effort token budget (exit 125) | `cli.py::cmd_run` — tested in `test_cli.py` |

### ⚠️ Real but conditional

| Capability | Condition |
|------------|-----------|
| `WasteDetector(degradation_window=N)` | The parameter is **accepted and stored** but **never used by `detect()`**. There is no degradation finding category. Use `NodeProfile.is_degrading()` or `Profiler.compare()` directly for latency trend analysis. |
| `--max-tokens` CLI budget | Best-effort only. Requires the wrapped process to emit token-usage telemetry in a recognised format (OpenAI JSON, Anthropic JSON, `key=value`, or `"Tokens used: …"`). If the child emits nothing, this limit is a **no-op**. |
| `WorkflowBudget.daily_spend()` | In-memory only. Resets to zero when the process restarts. Not persisted unless you save/load the profiler separately. |
| OpenAI adapter pricing | Hardcoded table covers gpt-4, gpt-4-turbo, gpt-4o, gpt-4o-mini, gpt-3.5-turbo, o1, o1-mini, o3-mini. Unknown models fall back to gpt-4o pricing. Prices may be outdated. |
| `WorkflowDAG.redundant_llm_calls()` | Heuristic only: flags LLM nodes with the same model provider/name AND the same upstream sources. May produce false positives or miss semantic duplicates. |
| `WorkflowDAG.dead_branches()` | Heuristic: flags branches from if-else/switch/conditional nodes that lead only to leaf nodes. Simplified — does not analyze runtime data flow. |

### Undocumented-but-real API surface

These methods exist, are tested, but are not shown in the README examples:

| Where | Symbol | What it does |
|-------|--------|-------------|
| `NodeProfile` | `cost_trend(last_n=10)` / `latency_trend(last_n=10)` | Return recent sample values as a list for charting. |
| `NodeProfile` | `total_tokens` / `avg_latency_ms` | Aggregate token count and average latency. |
| `Profiler` | `top_by_tokens(n=5)` | Top nodes by total token consumption (complement to `top_by_cost`). |
| `Profiler` | `all_profiles()` | All profiles as a list. |
| `WorkflowBudget` | `check_node_count(n)` | Check whether a node count is within `max_nodes_per_workflow`. |
| `GenericAdapter` | dot-notation `field_map` | Field paths like `"usage.prompt_tokens"` resolve nested dicts. |

### 🔮 Aspirational / later phase

| Claimed direction | Status |
|-------------------|--------|
| `WasteFinding` category `"redundant"` | Listed in the `category` field comment but **never produced by `detect()`**. Would presumably come from DAG analysis integration. |
| Latency degradation as a `WasteFinding` | Not implemented in `detect()`. The detection infrastructure exists (`is_degrading()`, `compare()`) but is not wired into the detector's `detect()` method. |

## Additional documentation

- [Architecture](docs/architecture.md) — full design walkthrough
- [Examples](examples/) — complete runnable scripts:
  - [`basic_usage.py`](examples/basic_usage.py)
  - [`langchain_integration.py`](examples/langchain_integration.py)
  - [`budget_enforcement.py`](examples/budget_enforcement.py)
  - [`historical_tracking.py`](examples/historical_tracking.py)
