"""Tests for the CLI wrapper mode.

These tests invoke the real ``main()`` entry point with synthetic
subprocesses (``python -c "..."``) so they exercise the full budget
enforcement loop. They need *python* available on PATH and a writable
``/tmp``; both are standard CI assumptions.
"""

from __future__ import annotations

import json
import sys

from conservation_guardian.cli import (
    EXIT_LAUNCH_FAILURE,
    EXIT_TIMEOUT,
    EXIT_TOKEN_BUDGET,
    _scan_tokens,
    main,
)


PY = sys.executable


def _run(argv: list[str]) -> int:
    """Invoke main() and return its exit code.

    argparse raises ``SystemExit(2)`` on validation errors; we surface that
    as a plain return code so assertions stay simple.
    """
    try:
        return main(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0


# ---------------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------------

class TestTopLevel:
    def test_no_args_prints_help_and_returns_zero(self, capsys):
        rc = _run([])
        assert rc == 0
        out = capsys.readouterr().out
        assert "conservation-guardian" in out
        assert "run" in out

    def test_help_flag(self, capsys):
        rc = _run(["--help"])
        assert rc == 0
        assert "run" in capsys.readouterr().out

    def test_version_flag(self, capsys):
        rc = _run(["--version"])
        assert rc == 0
        from conservation_guardian import __version__
        assert __version__ in capsys.readouterr().out

    def test_unknown_subcommand_returns_2(self, capsys):
        rc = _run(["frobnicate"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "unknown subcommand" in err


# ---------------------------------------------------------------------------
# `run` subcommand
# ---------------------------------------------------------------------------

class TestRunBasics:
    def test_runs_command_and_propagates_exit_code(self, capsys):
        rc = _run(["run", "--", PY, "-c", "import sys; sys.exit(7)"])
        assert rc == 7

    def test_streams_child_stdout_through(self, capsys):
        rc = _run(["run", "--", PY, "-c", "print('hello-from-child')"])
        assert rc == 0
        assert "hello-from-child" in capsys.readouterr().out

    def test_streams_child_stderr_through(self, capsys):
        rc = _run(["run", "--", PY, "-c", "import sys; print('boom', file=sys.stderr)"])
        assert rc == 0
        assert "boom" in capsys.readouterr().err

    def test_no_command_is_an_error(self, capsys):
        rc = _run(["run"])
        assert rc == 2
        assert "no command given" in capsys.readouterr().err

    def test_missing_command_is_launch_failure(self, capsys):
        rc = _run(["run", "--", "this-binary-does-not-exist-xyz-123"])
        assert rc == EXIT_LAUNCH_FAILURE
        assert "launch failed" in capsys.readouterr().err

    def test_negative_max_time_rejected(self, capsys):
        rc = _run(["run", "--max-time-seconds", "-1", "--", PY, "-c", "pass"])
        assert rc == 2

    def test_negative_max_tokens_rejected(self, capsys):
        rc = _run(["run", "--max-tokens", "-5", "--", PY, "-c", "pass"])
        assert rc == 2


# ---------------------------------------------------------------------------
# Wall-clock budget
# ---------------------------------------------------------------------------

class TestTimeoutBudget:
    def test_kills_on_timeout(self, capsys):
        # sleep far longer than the budget
        rc = _run(["run", "--max-time-seconds", "1", "--",
                   PY, "-c", "import time; print('go', flush=True); time.sleep(30)"])
        assert rc == EXIT_TIMEOUT

    def test_does_not_kill_when_under_budget(self):
        rc = _run(["run", "--max-time-seconds", "10", "--",
                   PY, "-c", "print('done')"])
        assert rc == 0


# ---------------------------------------------------------------------------
# Token budget (best-effort)
# ---------------------------------------------------------------------------

class TestTokenBudget:
    def test_kills_when_openai_usage_exceeds_budget(self, capsys):
        # 50 + 60 = 110 tokens, budget 100 -> kill
        script = (
            "import time, sys; "
            "sys.stdout.flush(); "
            "print('{\"usage\": {\"prompt_tokens\": 50, \"completion_tokens\": 60}}', flush=True); "
            "time.sleep(20); print('should-not-reach')"
        )
        rc = _run(["run", "--max-tokens", "100", "--", PY, "-c", script])
        assert rc == EXIT_TOKEN_BUDGET
        assert "should-not-reach" not in capsys.readouterr().out

    def test_does_not_kill_when_under_token_budget(self, capsys):
        script = (
            "print('{\"usage\": {\"prompt_tokens\": 10, \"completion_tokens\": 10}}', flush=True)"
        )
        rc = _run(["run", "--max-tokens", "1000", "--", PY, "-c", script])
        assert rc == 0

    def test_kills_on_anthropic_style_usage(self):
        script = (
            "import time, sys; "
            "print('{\"usage\":{\"input_tokens\":40,\"output_tokens\":80}}', flush=True); "
            "time.sleep(20)"
        )
        rc = _run(["run", "--max-tokens", "50", "--", PY, "-c", script])
        assert rc == EXIT_TOKEN_BUDGET

    def test_token_limit_noop_when_child_emits_no_usage(self):
        # No token telemetry -> the limit can't fire; child runs to completion.
        rc = _run(["run", "--max-tokens", "1", "--", PY, "-c", "print('no telemetry here')"])
        assert rc == 0

    def test_kills_on_generic_total_tokens_pattern(self):
        script = (
            "import time; "
            "print('tokens used: 9999', flush=True); "
            "time.sleep(20)"
        )
        rc = _run(["run", "--max-tokens", "100", "--", PY, "-c", script])
        assert rc == EXIT_TOKEN_BUDGET


# ---------------------------------------------------------------------------
# Report output
# ---------------------------------------------------------------------------

class TestReport:
    def test_writes_report_on_success(self, tmp_path, capsys):
        report = tmp_path / "r.json"
        rc = _run(["run", "--report", str(report), "--workflow-name", "wfname",
                   "--", PY, "-c", "print('hi')"])
        assert rc == 0
        data = json.loads(report.read_text())
        assert data["workflow_name"] == "wfname"
        assert data["exit_code"] == 0
        assert data["timed_out"] is False
        assert data["token_budget_exceeded"] is False
        assert data["tokens_detected"] == 0
        assert data["command"] == [PY, "-c", "print('hi')"]
        assert "started_at" in data and "finished_at" in data
        assert data["duration_seconds"] >= 0.0
        assert data["version"] != ""

    def test_report_records_timeout(self, tmp_path):
        report = tmp_path / "r.json"
        rc = _run(["run", "--max-time-seconds", "1", "--report", str(report),
                   "--", PY, "-c", "import time; time.sleep(30)"])
        assert rc == EXIT_TIMEOUT
        data = json.loads(report.read_text())
        assert data["timed_out"] is True
        assert data["killed_reason"] == "timeout"
        assert data["exit_code"] == EXIT_TIMEOUT

    def test_report_records_token_kill_and_count(self, tmp_path):
        report = tmp_path / "r.json"
        script = (
            "print('{\"usage\":{\"prompt_tokens\":300,\"completion_tokens\":400}}', flush=True); "
            "import time; time.sleep(20)"
        )
        rc = _run(["run", "--max-tokens", "100", "--report", str(report),
                   "--", PY, "-c", script])
        assert rc == EXIT_TOKEN_BUDGET
        data = json.loads(report.read_text())
        assert data["token_budget_exceeded"] is True
        assert data["killed_reason"] == "token_budget_exceeded"
        # 300 + 400 detected
        assert data["tokens_detected"] == 700

    def test_report_records_launch_failure(self, tmp_path):
        report = tmp_path / "r.json"
        rc = _run(["run", "--report", str(report),
                   "--", "no-such-binary-xyz-abc"])
        assert rc == EXIT_LAUNCH_FAILURE
        data = json.loads(report.read_text())
        assert data["exit_code"] == EXIT_LAUNCH_FAILURE
        assert data["killed_reason"] == "launch_failure"


# ---------------------------------------------------------------------------
# Token scanner unit tests
# ---------------------------------------------------------------------------

class TestScanTokens:
    def test_empty_buffer_returns_existing(self):
        assert _scan_tokens(b"", 5) == 5

    def test_openai_style(self):
        buf = b'{"usage": {"prompt_tokens": 100, "completion_tokens": 50}}'
        assert _scan_tokens(buf, 0) == 150

    def test_anthropic_style(self):
        buf = b'{"usage":{"input_tokens":40,"output_tokens":80}}'
        assert _scan_tokens(buf, 0) == 120

    def test_generic_keyvalue(self):
        buf = b"input_tokens=10 output_tokens=20"
        assert _scan_tokens(buf, 0) == 30

    def test_total_tokens_only(self):
        buf = b"total_tokens: 42"
        assert _scan_tokens(buf, 0) == 42

    def test_tokens_used_phrase(self):
        buf = b"Tokens used: 9000"
        assert _scan_tokens(buf, 0) == 9000

    def test_no_match_returns_existing(self):
        buf = b"just some log output with no token info"
        assert _scan_tokens(buf, 7) == 7

    def test_takes_maximum_across_patterns(self):
        buf = b"total_tokens=5 input_tokens=100 output_tokens=200"
        # explicit in+out (300) beats the bare total (5)
        assert _scan_tokens(buf, 0) == 300

    def test_fragmented_across_calls_grows_monotonically(self):
        # Simulate a usage JSON arriving in two chunks (newline-delimited
        # readline boundary doesn't split it here, but verify accumulation).
        stored = 0
        stored = _scan_tokens(b'{"usage": {"prompt_tokens": 10, ', stored)
        assert stored == 0  # incomplete
        stored = _scan_tokens(b'{"usage": {"prompt_tokens": 10, "completion_tokens": 20}}', stored)
        assert stored == 30


# ---------------------------------------------------------------------------
# `python -m conservation_guardian`
# ---------------------------------------------------------------------------

class TestModuleMain:
    def test_module_invocation_runs_command(self, tmp_path):
        import subprocess
        report = tmp_path / "m.json"
        proc = subprocess.run(
            [PY, "-m", "conservation_guardian", "run", "--report", str(report),
             "--", PY, "-c", "print('mod')"],
            capture_output=True,
        )
        assert proc.returncode == 0
        assert b"mod" in proc.stdout
        data = json.loads(report.read_text())
        assert data["exit_code"] == 0
