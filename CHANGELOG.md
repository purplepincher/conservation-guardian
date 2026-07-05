# Changelog

All notable changes to Conservation Guardian will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-07-04

### Added
- **CLI wrapper mode** (`conservation-guardian run -- <command>`) — wraps any
  subprocess in a budget, so the package can govern coding-agent CLIs
  (opencode, aider, kimi-style tools) without them importing the library.
  - `--max-time-seconds` hard wall-clock budget (kill child, exit `124`)
  - `--max-tokens` best-effort token budget, scanning child output for
    OpenAI / Anthropic / generic token-usage telemetry (kill child, exit `125`)
  - `--report PATH` JSON run report; `--workflow-name` label
  - Also runnable as `python -m conservation_guardian`
  - New `conservation_guardian.cli` module + `__main__.py`
  - New `tests/test_cli.py` (32 tests)
- `[project.optional-dependencies] dev = [...]` so `pip install -e ".[dev]"`
  (used by CI and CONTRIBUTING) actually resolves.
- `.gitignore` for Python build artifacts.

### Fixed
- **CI could never fail** — `ci.yml` ran `pytest || true`; now runs
  `pytest tests/ -v` and gates on the result. Also installs `-e ".[dev]"`.
- **`pip install -e ".[dev]"` failed** in `test.yml` because no `[dev]` extra
  existed (resolves via the new optional-dependency above).
- **PEP 639 build failure**: `license = "MIT"` SPDX expression coexisted with
  the `License :: OSI Approved :: MIT License` classifier, which modern
  setuptools rejects. Removed the redundant classifier.
- **`examples/budget_enforcement.py` crashed on run 1** instead of run 4 —
  default pricing made each run $6.00 vs. the docstring's claimed $0.009.
  Rewrote with explicit pricing so output matches the documented sample.
- **`examples/langchain_integration.py` ImportError** — it imported
  `LangChainAdapter` from the package root, but adapters live in
  `conservation_guardian.adapters` (as the README correctly documents).
- Build artifacts (`dist/`, `*.egg-info/`, `__pycache__/`) are no longer
  tracked in git.

### Changed
- Bumped version `0.2.0 → 0.3.0`; classifier `3 - Alpha → 4 - Beta`.
- README gains a "CLI wrapper" section documenting the new `run` subcommand.

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

[0.3.0]: https://github.com/purplepincher/conservation-guardian/releases/tag/v0.3.0
[0.2.0]: https://github.com/purplepincher/conservation-guardian/releases/tag/v0.2.0
[0.1.0]: https://github.com/purplepincher/conservation-guardian/releases/tag/v0.1.0
