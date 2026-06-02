# Contributing to Conservation Guardian

Thanks for your interest! Here's how to contribute.

## Development Setup

```bash
git clone https://github.com/SuperInstance/conservation-guardian.git
cd conservation-guardian
python -m venv .venv && source .venv/bin/activate
pip install -e .
pip install pytest ruff mypy
```

## Running Tests

```bash
python -m pytest tests/ -v
```

## Code Style

- We use `ruff` for linting: `ruff check src/ tests/`
- Type hints are encouraged; `mypy src/ --ignore-missing-imports` should pass

## Pull Requests

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Add tests for your changes
4. Ensure all tests pass: `python -m pytest tests/ -v`
5. Submit a PR with a clear description

## Adding Adapters

To add a new data source adapter:

1. Create `src/conservation_guardian/adapters/your_adapter.py`
2. Implement `extract_samples() → List[NodeSample]`
3. Handle malformed records gracefully (log + skip)
4. Raise `AdapterError` on source-level failures
5. Add tests to `tests/test_edge_cases.py`
6. Update `adapters/__init__.py` to export your class

## Reporting Issues

Open a GitHub issue with:
- Python version
- Conservation Guardian version
- Minimal reproducible example
- Expected vs actual behavior

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
