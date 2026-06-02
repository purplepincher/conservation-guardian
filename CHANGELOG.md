# Changelog

All notable changes to Conservation Guardian will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-06-02

### Added
- **Reporter class** with multi-format export: Markdown, JSON, Prometheus, Slack blocks
- **Data source adapters**: `GenericAdapter`, `OpenAIAdapter`, `LangChainAdapter`
  - Each adapter: `extract_samples() → List[NodeSample]`
  - Configurable field mapping (generic), auto-pricing (OpenAI), nested field resolution (LangChain)
- **Persistence layer**: `Profiler.save()`, `Profiler.load()`, `Profiler.compare()`
  - JSON serialization with graceful handling of corrupted samples
  - Trend analysis between profiler snapshots (cost, latency, degradation)
- **Custom exceptions**: `BudgetExceededError`, `InvalidProfileError`, `AdapterError`
- **Integration examples** (`examples/`):
  - `basic_usage.py`, `langchain_integration.py`, `budget_enforcement.py`, `historical_tracking.py`
- **Edge-case and concurrency tests** (`tests/test_edge_cases.py`)
  - Empty profiler, single sample, zero-output nodes, corrupted JSON
  - Missing adapter fields, concurrent access (threading)
- **CI/CD**: GitHub Actions workflow (pytest on Python 3.10–3.12, ruff, mypy)
- **Documentation**: Architecture guide (`docs/architecture.md`), `CONTRIBUTING.md`, `CHANGELOG.md`
- All detection thresholds are configurable via constructor parameters

### Changed
- Renamed `idle` detection category to `low_utilization` for clarity
- Improved error messages in persistence layer

## [0.1.0] — 2026-06-01

### Added
- Initial release
- `WorkflowBudget` — token/cost/node limits and daily tracking
- `WorkflowDAG` — parse workflow JSON, find redundancies and dead branches
- `Profiler` / `NodeProfile` / `NodeSample` — per-node profiling
- `WasteDetector` / `WasteFinding` — waste detection heuristics
- `render_report()` — Markdown conservation reports
- Core test suite

[0.2.0]: https://github.com/SuperInstance/conservation-guardian/releases/tag/v0.2.0
[0.1.0]: https://github.com/SuperInstance/conservation-guardian/releases/tag/v0.1.0
