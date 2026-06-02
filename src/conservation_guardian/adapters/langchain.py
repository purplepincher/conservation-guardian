"""LangChain callback adapter — extract NodeSamples from LangChain runs."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable

from ..exceptions import AdapterError
from ..profiler import NodeSample

logger = logging.getLogger(__name__)


class LangChainAdapter:
    """Extract profiling samples from LangChain callback data.

    Accepts either:
    - A list of LangChain ``LLMResult`` / ``ChatGeneration`` dicts (already
      serialized to plain Python objects), or
    - A path to a JSONL file where each line is a serialized run record.

    Each record should have at minimum:
    - ``llm_output.token_usage.prompt_tokens`` (or ``prompt_tokens`` at top level)
    - ``llm_output.token_usage.completion_tokens`` (or ``completion_tokens`` at top level)
    - ``llm_output.model_name`` or ``model_name`` (used as node_id)

    Parameters
    ----------
    records:
        Iterable of dicts representing LangChain LLM runs.
    path:
        Path to a JSONL file of records. Used if *records* is None.
    latency_ms:
        Default latency to use when not present in the record. Set to 0.0
        if unknown.
    cost_per_1k_input:
        Cost per 1K input tokens for estimation.
    cost_per_1k_output:
        Cost per 1K output tokens for estimation.
    """

    def __init__(
        self,
        records: Iterable[dict] | None = None,
        *,
        path: str | Path | None = None,
        latency_ms: float = 0.0,
        cost_per_1k_input: float = 0.03,
        cost_per_1k_output: float = 0.06,
    ) -> None:
        self._records = list(records) if records is not None else None
        self._path = Path(path) if path else None
        self.latency_ms = latency_ms
        self.cost_per_1k_input = cost_per_1k_input
        self.cost_per_1k_output = cost_per_1k_output

    def _load_records(self) -> list[dict]:
        if self._records is not None:
            return self._records
        if self._path is None:
            raise AdapterError("No records or path provided", adapter_name="langchain")
        try:
            records: list[dict] = []
            with open(self._path, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        logger.warning("Skipping malformed line %d in %s: %s", line_no, self._path, exc)
            return records
        except OSError as exc:
            raise AdapterError(f"Failed to read {self._path}: {exc}", adapter_name="langchain", cause=exc) from exc

    @staticmethod
    def _get_nested(data: dict, *keys: str, default: Any = 0) -> Any:
        """Safely traverse nested dicts."""
        current = data
        for key in keys:
            if isinstance(current, dict):
                current = current.get(key, default)
            else:
                return default
        return current

    def extract_samples(self) -> list[NodeSample]:
        """Extract a list of NodeSample from LangChain records.

        Returns an empty list on unrecoverable errors; logs warnings for
        individual bad records.
        """
        samples: list[NodeSample] = []
        try:
            records = self._load_records()
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"Unexpected error loading records: {exc}", adapter_name="langchain", cause=exc) from exc

        for i, rec in enumerate(records):
            try:
                input_tokens = int(
                    self._get_nested(rec, "llm_output", "token_usage", "prompt_tokens")
                    or self._get_nested(rec, "prompt_tokens")
                    or 0
                )
                output_tokens = int(
                    self._get_nested(rec, "llm_output", "token_usage", "completion_tokens")
                    or self._get_nested(rec, "completion_tokens")
                    or 0
                )
                model_name = (
                    self._get_nested(rec, "llm_output", "model_name", default="")
                    or rec.get("model_name", "")
                    or rec.get("invocation_params", {}).get("model_name", "")
                    or f"langchain_run_{i}"
                )
                latency = float(rec.get("latency_ms", rec.get("latency", self.latency_ms)))
                cost = (
                    input_tokens * self.cost_per_1k_input
                    + output_tokens * self.cost_per_1k_output
                ) / 1_000

                samples.append(NodeSample(
                    node_id=model_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency,
                    cost_usd=cost,
                    node_title=model_name,
                ))
            except Exception as exc:
                logger.warning("Skipping malformed LangChain record %d: %s", i, exc)

        return samples
