"""Generic adapter — parse JSON/log files with configurable field mapping."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable

from ..exceptions import AdapterError
from ..profiler import NodeSample

logger = logging.getLogger(__name__)

# Default field paths for common formats
DEFAULT_FIELD_MAP: dict[str, str] = {
    "node_id": "node_id",
    "node_title": "node_title",
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "latency_ms": "latency_ms",
    "cost_usd": "cost_usd",
}


class GenericAdapter:
    """Extract NodeSamples from generic JSON/JSONL data with configurable field mapping.

    Parameters
    ----------
    records:
        Iterable of dicts. Used if *path* is None.
    path:
        Path to a JSON (list) or JSONL file. Used if *records* is None.
    field_map:
        Mapping from NodeSample field names to the keys in your data.
        Supports dot-notation for nested fields (e.g. ``"usage.prompt_tokens"``).
        Defaults to identity mapping (same field names).
    cost_per_1k_input:
        If provided, auto-calculate cost_usd when not in the data.
    cost_per_1k_output:
        If provided, auto-calculate cost_usd when not in the data.
    """

    def __init__(
        self,
        records: Iterable[dict] | None = None,
        *,
        path: str | Path | None = None,
        field_map: dict[str, str] | None = None,
        cost_per_1k_input: float = 0.03,
        cost_per_1k_output: float = 0.06,
    ) -> None:
        self._records = list(records) if records is not None else None
        self._path = Path(path) if path else None
        self.field_map = {**DEFAULT_FIELD_MAP, **(field_map or {})}
        self.cost_per_1k_input = cost_per_1k_input
        self.cost_per_1k_output = cost_per_1k_output

    def _load_records(self) -> list[dict]:
        if self._records is not None:
            return self._records
        if self._path is None:
            raise AdapterError("No records or path provided", adapter_name="generic")
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if not content:
                return []
            # Try JSON array first
            try:
                data = json.loads(content)
                if isinstance(data, list):
                    return data
                # Single object — wrap in list
                return [data]
            except json.JSONDecodeError:
                pass
            # Fall back to JSONL
            records: list[dict] = []
            for line_no, line in enumerate(content.splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    logger.warning("Skipping malformed line %d in %s: %s", line_no, self._path, exc)
            return records
        except OSError as exc:
            raise AdapterError(f"Failed to read {self._path}: {exc}", adapter_name="generic", cause=exc) from exc

    def _resolve_field(self, data: dict, path: str, default: Any = None) -> Any:
        """Resolve a dot-separated field path from a dict."""
        current: Any = data
        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return default
            if current is None:
                return default
        return current

    def extract_samples(self) -> list[NodeSample]:
        """Extract a list of NodeSample from generic data."""
        samples: list[NodeSample] = []
        try:
            records = self._load_records()
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"Unexpected error loading records: {exc}", adapter_name="generic", cause=exc) from exc

        fm = self.field_map

        for i, rec in enumerate(records):
            try:
                node_id = str(self._resolve_field(rec, fm.get("node_id", "node_id"), default=f"node_{i}"))
                node_title = str(self._resolve_field(rec, fm.get("node_title", "node_title"), default=""))
                input_tokens = int(self._resolve_field(rec, fm.get("input_tokens", "input_tokens"), default=0))
                output_tokens = int(self._resolve_field(rec, fm.get("output_tokens", "output_tokens"), default=0))
                latency_ms = float(self._resolve_field(rec, fm.get("latency_ms", "latency_ms"), default=0.0))

                cost_usd = self._resolve_field(rec, fm.get("cost_usd", "cost_usd"), default=None)
                if cost_usd is None:
                    cost_usd = (input_tokens * self.cost_per_1k_input + output_tokens * self.cost_per_1k_output) / 1_000
                else:
                    cost_usd = float(cost_usd)

                samples.append(NodeSample(
                    node_id=node_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    cost_usd=cost_usd,
                    node_title=node_title,
                ))
            except Exception as exc:
                logger.warning("Skipping malformed generic record %d: %s", i, exc)

        return samples
