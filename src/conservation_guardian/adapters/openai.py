"""OpenAI API usage adapter — parse usage from API responses or logs."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

from ..exceptions import AdapterError
from ..profiler import NodeSample

logger = logging.getLogger(__name__)


class OpenAIAdapter:
    """Extract profiling samples from OpenAI API response data.

    Accepts either:
    - A list of dicts representing OpenAI API responses (the ``usage`` field),
    or
    - A path to a JSONL file where each line is a serialized API response.

    Expected structure per record (standard OpenAI API response):
    - ``usage.prompt_tokens``
    - ``usage.completion_tokens``
    - ``model`` (used as node_id)
    - Optional ``latency_ms`` (if you tracked request timing)

    Parameters
    ----------
    records:
        Iterable of dicts representing OpenAI API responses.
    path:
        Path to a JSONL file of API responses. Used if *records* is None.
    latency_ms:
        Default latency when not present in the record.
    cost_per_1k_input:
        Cost per 1K input tokens for estimation.
    cost_per_1k_output:
        Cost per 1K output tokens for estimation.
    """

    # Model-specific pricing (per 1K tokens)
    PRICING: dict[str, dict[str, float]] = {
        "gpt-4": {"input": 0.03, "output": 0.06},
        "gpt-4-turbo": {"input": 0.01, "output": 0.03},
        "gpt-4o": {"input": 0.005, "output": 0.015},
        "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
        "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
        "o1": {"input": 0.015, "output": 0.06},
        "o1-mini": {"input": 0.003, "output": 0.012},
        "o3-mini": {"input": 0.0011, "output": 0.0044},
    }

    def __init__(
        self,
        records: Iterable[dict] | None = None,
        *,
        path: str | Path | None = None,
        latency_ms: float = 0.0,
        cost_per_1k_input: float | None = None,
        cost_per_1k_output: float | None = None,
    ) -> None:
        self._records = list(records) if records is not None else None
        self._path = Path(path) if path else None
        self.latency_ms = latency_ms
        self._cost_input = cost_per_1k_input
        self._cost_output = cost_per_1k_output

    def _load_records(self) -> list[dict]:
        if self._records is not None:
            return self._records
        if self._path is None:
            raise AdapterError("No records or path provided", adapter_name="openai")
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
            raise AdapterError(f"Failed to read {self._path}: {exc}", adapter_name="openai", cause=exc) from exc

    def _get_pricing(self, model: str) -> tuple[float, float]:
        """Resolve pricing for a model, falling back to defaults."""
        for key, pricing in self.PRICING.items():
            if key in model:
                return pricing["input"], pricing["output"]
        # Default to gpt-4o pricing
        return 0.005, 0.015

    def extract_samples(self) -> list[NodeSample]:
        """Extract a list of NodeSample from OpenAI API response records."""
        samples: list[NodeSample] = []
        try:
            records = self._load_records()
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"Unexpected error loading records: {exc}", adapter_name="openai", cause=exc) from exc

        for i, rec in enumerate(records):
            try:
                usage = rec.get("usage", rec)
                input_tokens = int(usage.get("prompt_tokens", 0))
                output_tokens = int(usage.get("completion_tokens", 0))
                model = rec.get("model", f"openai_run_{i}")

                latency = float(rec.get("latency_ms", rec.get("latency", self.latency_ms)))

                cost_input = self._cost_input
                cost_output = self._cost_output
                if cost_input is None or cost_output is None:
                    ci, co = self._get_pricing(model)
                    cost_input = cost_input if cost_input is not None else ci
                    cost_output = cost_output if cost_output is not None else co

                cost = (input_tokens * cost_input + output_tokens * cost_output) / 1_000

                samples.append(NodeSample(
                    node_id=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency,
                    cost_usd=cost,
                    node_title=model,
                ))
            except Exception as exc:
                logger.warning("Skipping malformed OpenAI record %d: %s", i, exc)

        return samples
