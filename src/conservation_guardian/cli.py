"""Command-line interface for Conservation Guardian.

Provides a thin ``run`` subcommand that wraps an arbitrary subprocess and
enforces a wall-clock / token budget around it. This lets the library be
used as a general budget-enforcement wrapper around *any* coding-agent CLI
(opencode, aider, kimi, …) without requiring the wrapped tool to import
this package.

Usage::

    conservation-guardian run [--max-time-seconds N] [--max-tokens N] \\
        [--report path.json] -- <command> [args...]

Budget semantics
----------------
- ``--max-time-seconds`` is *hard*: the subprocess is terminated (SIGTERM,
  then SIGKILL after a grace period) when the wall-clock budget elapses.
  Exit code on timeout is ``124`` (the GNU ``timeout`` convention).
- ``--max-tokens`` is *best-effort*: the wrapper scans the combined
  stdout/stderr stream for token-usage telemetry emitted by the child and
  kills the process if the cumulative detected total exceeds the limit.
  Detection is heuristic and model-agnostic; if the wrapped tool does not
  emit token usage, this limit is a no-op (documented, not a bug).
  Exit code on token-budget kill is ``125``.

The child's stdout/stderr are streamed through unchanged so interactive
behavior is preserved. An optional JSON report captures run metadata.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from . import __version__

# Exit codes (chosen to avoid clashing with common child exit codes)
EXIT_TIMEOUT = 124
EXIT_TOKEN_BUDGET = 125
EXIT_LAUNCH_FAILURE = 126

# Grace period between SIGTERM and SIGKILL, in seconds.
KILL_GRACE_SECONDS = 5.0

# Best-effort token-usage patterns. Matched against the *entire* combined
# output stream in real time. Order matters only for total accounting (we
# sum prompt+completion; if a record also has total_tokens we prefer the
# explicit prompt/completion pair to avoid double-counting).
_TOKEN_PATTERNS: tuple[re.Pattern[bytes], ...] = (
    # OpenAI-style JSON: "usage": {"prompt_tokens": 123, "completion_tokens": 45}
    re.compile(
        rb'"usage"\s*:\s*\{[^}]*?"prompt_tokens"\s*:\s*(\d+)[^}]*?"completion_tokens"\s*:\s*(\d+)',
        re.DOTALL,
    ),
    re.compile(
        rb'"usage"\s*:\s*\{[^}]*?"completion_tokens"\s*:\s*(\d+)[^}]*?"prompt_tokens"\s*:\s*(\d+)',
        re.DOTALL,
    ),
    # Anthropic-style: "usage":{"input_tokens":123,"output_tokens":45}
    re.compile(
        rb'"usage"\s*:\s*\{[^}]*?"input_tokens"\s*:\s*(\d+)[^}]*?"output_tokens"\s*:\s*(\d+)',
        re.DOTALL,
    ),
    re.compile(
        rb'"usage"\s*:\s*\{[^}]*?"output_tokens"\s*:\s*(\d+)[^}]*?"input_tokens"\s*:\s*(\d+)',
        re.DOTALL,
    ),
    # Generic key=value: input_tokens=123 output_tokens=45
    re.compile(rb"input_tokens\s*[:=]\s*(\d+).*?output_tokens\s*[:=]\s*(\d+)", re.DOTALL),
    re.compile(rb"output_tokens\s*[:=]\s*(\d+).*?input_tokens\s*[:=]\s*(\d+)", re.DOTALL),
    # Single total: total_tokens=123  /  tokens used: 123
    re.compile(rb"total_tokens\s*[:=]\s*(\d+)"),
    re.compile(rb"tokens\s+used\s*[:=]?\s*(\d+)", re.IGNORECASE),
)

# Maximum bytes of trailing output kept for token scanning. Token-usage
# records are small and emitted near the message they describe, so a
# trailing window is sufficient and bounds memory/CPU on huge outputs.
SCAN_WINDOW_BYTES = 256 * 1024


class _RunResult:
    """Internal record of a wrapped subprocess run."""

    def __init__(self, command: list[str], workflow_name: str) -> None:
        self.command = command
        self.workflow_name = workflow_name
        self.started_at = datetime.now(timezone.utc)
        self.finished_at: Optional[datetime] = None
        self.exit_code: Optional[int] = None
        self.killed_reason: Optional[str] = None
        self.tokens_detected = 0
        self.timed_out = False
        self.token_budget_exceeded = False

    @property
    def duration_seconds(self) -> float:
        end = self.finished_at or datetime.now(timezone.utc)
        return (end - self.started_at).total_seconds()

    def to_dict(self) -> dict:
        return {
            "workflow_name": self.workflow_name,
            "command": self.command,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": round(self.duration_seconds, 3),
            "exit_code": self.exit_code,
            "killed_reason": self.killed_reason,
            "timed_out": self.timed_out,
            "token_budget_exceeded": self.token_budget_exceeded,
            "tokens_detected": self.tokens_detected,
            "version": __version__,
        }


def _scan_tokens(buffer: bytes, already_counted: int) -> int:
    """Return the new total tokens accounted for in *buffer*.

    We re-scan the (windowed) accumulated buffer each time and report the
    *maximum* consistent total rather than a per-match delta, which keeps
    the count stable even when matches overlap or stream in fragments.
    """
    if not buffer:
        return already_counted
    best = already_counted
    for pat in _TOKEN_PATTERNS:
        for m in pat.finditer(buffer):
            groups = m.groups()
            if len(groups) >= 2:
                total = int(groups[0]) + int(groups[1])
            else:
                total = int(groups[0])
            if total > best:
                best = total
    return best


def _stream_and_scan(
    proc: subprocess.Popen,
    stream,
    out_stream,
    accumulated: bytearray,
    on_update,
) -> None:
    """Read *stream* line-by-line, passthrough to *out_stream*, scan tokens."""
    for raw in iter(stream.readline, b""):
        out_stream.buffer.write(raw)
        out_stream.buffer.flush()
        accumulated.extend(raw)
        # Bound the scan window so huge outputs don't blow up memory/CPU.
        if len(accumulated) > SCAN_WINDOW_BYTES:
            del accumulated[: len(accumulated) - SCAN_WINDOW_BYTES]
        on_update(bytes(accumulated))
    stream.close()


def cmd_run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="conservation-guardian run",
        description=(
            "Run a command inside a Conservation Guardian budget. "
            "Wall-clock time is enforced hard; token limits are best-effort."
        ),
    )
    parser.add_argument("--max-time-seconds", type=float, default=None,
                        help="Wall-clock budget. On exceed, the child is killed (exit 124).")
    parser.add_argument("--max-tokens", type=int, default=None,
                        help="Best-effort cumulative token budget. On exceed, the child is "
                             "killed (exit 125). Requires the child to emit token usage.")
    parser.add_argument("--report", default=None,
                        help="Optional path to write a JSON run report.")
    parser.add_argument("--workflow-name", default="cli-run",
                        help="Label for the run, included in the JSON report.")
    parser.add_argument("--version", action="version", version=f"conservation-guardian {__version__}")

    # Everything after ``--`` is the wrapped command. We split manually so
    # argparse doesn't try to interpret the child's flags.
    if "--" in argv:
        sep = argv.index("--")
        our_args = argv[:sep]
        child_command = argv[sep + 1:]
    else:
        our_args = argv
        child_command = []

    opts = parser.parse_args(our_args)

    if not child_command:
        parser.error("no command given — expected `conservation-guardian run ... -- <command>`")

    if opts.max_time_seconds is not None and opts.max_time_seconds <= 0:
        parser.error("--max-time-seconds must be positive")
    if opts.max_tokens is not None and opts.max_tokens < 0:
        parser.error("--max-tokens must be non-negative")

    result = _RunResult(command=child_command, workflow_name=opts.workflow_name)

    try:
        proc = subprocess.Popen(
            child_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        print(f"conservation-guardian: launch failed: {exc}", file=sys.stderr)
        result.exit_code = EXIT_LAUNCH_FAILURE
        result.killed_reason = "launch_failure"
        result.finished_at = datetime.now(timezone.utc)
        _maybe_write_report(opts.report, result)
        return EXIT_LAUNCH_FAILURE

    accumulated = bytearray()
    token_lock = threading.Lock()
    token_kill_event = threading.Event()

    def on_update(buf: bytes) -> None:
        if opts.max_tokens is None:
            return
        with token_lock:
            result.tokens_detected = _scan_tokens(buf, result.tokens_detected)
            if result.tokens_detected > opts.max_tokens:
                token_kill_event.set()

    # Stream + scan both pipes concurrently.
    t_out = threading.Thread(
        target=_stream_and_scan,
        args=(proc, proc.stdout, sys.stdout, accumulated, on_update),
        daemon=True,
    )
    t_err = threading.Thread(
        target=_stream_and_scan,
        args=(proc, proc.stderr, sys.stderr, accumulated, on_update),
        daemon=True,
    )
    t_out.start()
    t_err.start()

    deadline = time.monotonic() + opts.max_time_seconds if opts.max_time_seconds else None
    timed_out = False

    while True:
        rc = proc.poll()
        if rc is not None:
            break

        if token_kill_event.is_set():
            _terminate(proc)
            result.token_budget_exceeded = True
            result.killed_reason = "token_budget_exceeded"
            break

        if deadline is not None and time.monotonic() >= deadline:
            _terminate(proc)
            timed_out = True
            result.killed_reason = "timeout"
            break

        time.sleep(0.05)

    # Drain remnant output (bounded by child death).
    proc.wait()
    t_out.join(timeout=2.0)
    t_err.join(timeout=2.0)

    result.finished_at = datetime.now(timezone.utc)
    result.timed_out = timed_out

    if timed_out:
        result.exit_code = EXIT_TIMEOUT
    elif result.token_budget_exceeded:
        result.exit_code = EXIT_TOKEN_BUDGET
    else:
        result.exit_code = proc.returncode

    _maybe_write_report(opts.report, result)
    return result.exit_code


def _terminate(proc: subprocess.Popen) -> None:
    """Politely SIGTERM, then SIGKILL after a grace period."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=KILL_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
    except ProcessLookupError:
        pass


def _maybe_write_report(path: Optional[str], result: _RunResult) -> None:
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, indent=2, default=str)
    except OSError as exc:
        print(f"conservation-guardian: failed to write report to {path}: {exc}",
              file=sys.stderr)


def main(argv: Optional[list[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if not argv or argv[0] in ("-h", "--help"):
        _print_top_level_help()
        return 0
    if argv[0] in ("-V", "--version"):
        print(f"conservation-guardian {__version__}")
        return 0

    sub = argv[0]
    rest = argv[1:]
    if sub == "run":
        return cmd_run(rest)

    print(f"conservation-guardian: unknown subcommand '{sub}'\n", file=sys.stderr)
    _print_top_level_help(stream=sys.stderr)
    return 2


def _print_top_level_help(stream=None) -> None:
    if stream is None:
        stream = sys.stdout
    stream.write(
        f"conservation-guardian {__version__} — budget enforcement for arbitrary commands\n\n"
        "Usage:\n"
        "  conservation-guardian run [OPTIONS] -- <command> [args...]\n"
        "  conservation-guardian --version\n"
        "  conservation-guardian --help\n\n"
        "Options for `run`:\n"
        "  --max-time-seconds N   Hard wall-clock budget; kill child on exceed (exit 124).\n"
        "  --max-tokens N         Best-effort token budget; kill child on exceed (exit 125).\n"
        "  --report PATH          Write a JSON run report to PATH.\n"
        "  --workflow-name NAME   Label included in the report.\n\n"
        "Example:\n"
        "  conservation-guardian run --max-time-seconds 600 -- opencode exec 'fix bug'\n"
    )
    stream.flush()


if __name__ == "__main__":
    raise SystemExit(main())
