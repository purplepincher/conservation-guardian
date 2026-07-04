# Architecture

Conservation Guardian follows a four-stage pipeline pattern:

```
Budget → Profile → Detect → Report
```

It also ships a CLI wrapper (`cli.py`) that enforces a budget around an
*external* subprocess, so coding-agent CLIs that don't import the library
can still be governed. See "CLI wrapper" at the end of this document.

## Budget (`budget.py`)

**Purpose:** Enforce hard limits on token usage, cost, and node count.

`WorkflowBudget` is a stateful object that tracks daily spend and per-run token counts. It answers a simple question: *"Is this run allowed?"* It does not profile — it gates.

Key concepts:
- `is_within_budget()` — Pre-flight check before a run
- `record_run()` — Post-run accounting
- `daily_spend()` — Query today's total

## Profile (`profiler.py`)

**Purpose:** Collect and aggregate per-node execution metrics across runs.

The `Profiler` collects `NodeSample` instances (individual observations) and rolls them into `NodeProfile` aggregates. Each profile tracks averages, totals, and trends for tokens, latency, and cost.

Key concepts:
- `NodeSample` — One observation (a single node execution)
- `NodeProfile` — Aggregated stats for a node across all its samples
- `Profiler` — Top-level container; records samples, queries profiles

### Persistence

Profiles serialize to JSON via `save()`/`load()`. The `compare()` method performs trend analysis between two profiler snapshots — useful for tracking cost/latency changes over time.

## Detect (`detector.py`)

**Purpose:** Analyze profile data to surface actionable waste findings.

`WasteDetector` runs a suite of heuristics against the profiler data:

| Check | What it finds | Key threshold |
|-------|---------------|---------------|
| **Overprompted** | High input-to-output ratio | `max_io_ratio` (default 15.0) |
| **Low utilization** | Nodes costing almost nothing despite frequent runs | `low_utilization_threshold` (default 0.1) |
| **Expensive model** | Cost concentrated in very few nodes | `expensive_model_ratio` (default 0.8) |

All thresholds are configurable via constructor parameters.

### Detection Pattern

```python
detector = WasteDetector(profiler, max_io_ratio=10.0)
findings = detector.detect()
for f in findings:
    print(f"[{f.severity}] {f.category}: {f.message}")
    print(f"  → {f.suggestion}")
```

## Report (`report.py`, `reporter.py`)

**Purpose:** Present findings in consumable formats.

- `render_report()` — Quick Markdown rendering
- `Reporter` — Multi-format class with `to_markdown()`, `to_json()`, `to_prometheus()`, `to_slack()`

## Adapters (`adapters/`)

**Purpose:** Bridge external systems to `NodeSample`.

Each adapter implements `extract_samples() → List[NodeSample]`:

| Adapter | Source |
|---------|--------|
| `GenericAdapter` | JSON/JSONL with configurable field mapping |
| `OpenAIAdapter` | OpenAI API response data (with auto-pricing) |
| `LangChainAdapter` | LangChain callback/LLM result data |

Adapters handle malformed records gracefully (skip + log warning) and raise `AdapterError` on source-level failures.

## Exceptions (`exceptions.py`)

```
ConservationGuardianError (base)
├── BudgetExceededError  — budget limit violations
├── InvalidProfileError  — corrupted profile data
└── AdapterError         — data source failures
```

## CLI wrapper (`cli.py`)

**Purpose:** Enforce a budget around an *external* subprocess — any coding
agent CLI (opencode, aider, kimi-style tools) — so this library can govern
tools that don't import it.

```
conservation-guardian run [--max-time-seconds N] [--max-tokens N] [--report PATH] -- <cmd>
```

| Budget | Enforcement | How |
|--------|-------------|-----|
| `--max-time-seconds` | **Hard** | SIGTERM → SIGKILL on deadline; exit `124` |
| `--max-tokens` | **Best-effort** | Scans combined stdout+stderr for token-usage records (OpenAI/Anthropic/generic), kills on exceed; exit `125` |

Design notes:
- The child's stdout/stderr are streamed through unchanged to preserve
  interactive behavior — the wrapper tees into a trailing 256 KB scan window
  (bounded so huge outputs don't blow up memory/CPU).
- Token detection is intentionally model-agnostic and heuristic: if the
  wrapped tool emits no usage telemetry, `--max-tokens` is a documented
  no-op rather than a silent failure mode.
- Exit codes propagate the child's status on normal completion;
  `124`/`125`/`126` distinguish timeout / token-kill / launch-failure.

## Design Principles

1. **Framework-agnostic** — No assumptions about workflow engine
2. **Configurable thresholds** — Every magic number is a parameter
3. **Graceful degradation** — Bad records are skipped, not fatal
4. **Composable** — Use any combination of Budget, Profiler, Detector, Reporter
