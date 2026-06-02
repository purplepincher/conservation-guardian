"""Per-node profiling: tokens, latency, cost, and historical trends."""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class NodeSample:
    """A single profiling observation for a node execution."""

    node_id: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float
    node_title: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
            "node_title": self.node_title,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NodeSample":
        data = dict(data)  # shallow copy
        if "timestamp" in data and isinstance(data["timestamp"], str):
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class NodeProfile:
    """Aggregate stats for a single workflow node across runs."""

    node_id: str
    node_title: str = ""
    samples: list[NodeSample] = field(default_factory=list, repr=False)

    def record(self, sample: NodeSample) -> None:
        self.samples.append(sample)
        if sample.node_title and not self.node_title:
            self.node_title = sample.node_title

    @property
    def run_count(self) -> int:
        return len(self.samples)

    @property
    def avg_input_tokens(self) -> float:
        return statistics.mean(s.input_tokens for s in self.samples) if self.samples else 0.0

    @property
    def avg_output_tokens(self) -> float:
        return statistics.mean(s.output_tokens for s in self.samples) if self.samples else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return statistics.mean(s.latency_ms for s in self.samples) if self.samples else 0.0

    @property
    def avg_cost(self) -> float:
        return statistics.mean(s.cost_usd for s in self.samples) if self.samples else 0.0

    @property
    def total_cost(self) -> float:
        return sum(s.cost_usd for s in self.samples)

    @property
    def total_tokens(self) -> int:
        return sum(s.input_tokens + s.output_tokens for s in self.samples)

    @property
    def input_output_ratio(self) -> float:
        if not self.samples:
            return 0.0
        avg_out = self.avg_output_tokens
        if avg_out <= 0:
            return float("inf")
        return self.avg_input_tokens / avg_out

    def cost_trend(self, last_n: int = 10) -> list[float]:
        return [s.cost_usd for s in self.samples[-last_n:]]

    def latency_trend(self, last_n: int = 10) -> list[float]:
        return [s.latency_ms for s in self.samples[-last_n:]]

    def is_degrading(self, window: int = 5) -> bool:
        """Return *True* if latency is trending upward over the last *window* runs."""
        if len(self.samples) < window * 2:
            return False
        recent = [s.latency_ms for s in self.samples[-window:]]
        earlier = [s.latency_ms for s in self.samples[-window * 2:-window]]
        return statistics.mean(recent) > statistics.mean(earlier) * 1.2


class Profiler:
    """Collects and queries per-node profiles."""

    def __init__(self, *, degradation_window: int = 5) -> None:
        self._profiles: dict[str, NodeProfile] = {}
        self.degradation_window = degradation_window

    def record(self, sample: NodeSample) -> None:
        profile = self._profiles.get(sample.node_id)
        if profile is None:
            profile = NodeProfile(node_id=sample.node_id, node_title=sample.node_title)
            self._profiles[sample.node_id] = profile
        profile.record(sample)

    def get(self, node_id: str) -> Optional[NodeProfile]:
        return self._profiles.get(node_id)

    def all_profiles(self) -> list[NodeProfile]:
        return list(self._profiles.values())

    def top_by_cost(self, n: int = 5) -> list[NodeProfile]:
        return sorted(self._profiles.values(), key=lambda p: p.total_cost, reverse=True)[:n]

    def top_by_tokens(self, n: int = 5) -> list[NodeProfile]:
        return sorted(self._profiles.values(), key=lambda p: p.total_tokens, reverse=True)[:n]

    # --- Persistence ---

    def to_dict(self) -> dict:
        return {
            "degradation_window": self.degradation_window,
            "profiles": [
                {
                    "node_id": p.node_id,
                    "node_title": p.node_title,
                    "samples": [s.to_dict() for s in p.samples],
                }
                for p in self._profiles.values()
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Profiler":
        profiler = cls(degradation_window=data.get("degradation_window", 5))
        for pdata in data.get("profiles", []):
            node_id = pdata.get("node_id", "")
            node_title = pdata.get("node_title", "")
            profile = NodeProfile(node_id=node_id, node_title=node_title)
            for sdata in pdata.get("samples", []):
                try:
                    profile.record(NodeSample.from_dict(sdata))
                except (TypeError, KeyError, ValueError):
                    # Skip corrupted samples
                    continue
            if node_id:
                profiler._profiles[node_id] = profile
        return profiler

    def save(self, path: str) -> None:
        """Serialize profiler state to a JSON file."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)

    @classmethod
    def load(cls, path: str) -> "Profiler":
        """Load profiler state from a JSON file.

        Raises FileNotFoundError if the file doesn't exist.
        Raises ValueError if the JSON is corrupted or invalid.
        """
        with open(path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Corrupted JSON in {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"Expected a JSON object in {path}, got {type(data).__name__}")
        return cls.from_dict(data)

    # --- Trend Analysis ---

    def compare(self, previous: "Profiler") -> list[dict]:
        """Compare current state to a previous profiler snapshot.

        Returns a list of trend dicts with keys:
            node_id, node_title, metric, direction, detail
        """
        trends: list[dict] = []
        for node_id, profile in self._profiles.items():
            prev_profile = previous.get(node_id)
            if prev_profile is None or prev_profile.run_count == 0:
                continue

            # Latency trend
            if profile.run_count >= self.degradation_window and prev_profile.run_count >= previous.degradation_window:
                cur_avg = profile.avg_latency_ms
                prev_avg = prev_profile.avg_latency_ms
                if prev_avg > 0:
                    change_pct = (cur_avg - prev_avg) / prev_avg * 100
                    if change_pct > 20:
                        trends.append({
                            "node_id": node_id,
                            "node_title": profile.node_title,
                            "metric": "latency",
                            "direction": "worse",
                            "detail": f"Latency increased {change_pct:.1f}% ({prev_avg:.0f}ms → {cur_avg:.0f}ms)",
                        })
                    elif change_pct < -20:
                        trends.append({
                            "node_id": node_id,
                            "node_title": profile.node_title,
                            "metric": "latency",
                            "direction": "better",
                            "detail": f"Latency improved {abs(change_pct):.1f}% ({prev_avg:.0f}ms → {cur_avg:.0f}ms)",
                        })

            # Cost trend
            if profile.run_count > 0 and prev_profile.run_count > 0:
                cur_avg = profile.avg_cost
                prev_avg = prev_profile.avg_cost
                if prev_avg > 0:
                    change_pct = (cur_avg - prev_avg) / prev_avg * 100
                    if change_pct > 20:
                        trends.append({
                            "node_id": node_id,
                            "node_title": profile.node_title,
                            "metric": "cost",
                            "direction": "worse",
                            "detail": f"Cost per run increased {change_pct:.1f}% (${prev_avg:.4f} → ${cur_avg:.4f})",
                        })

            # Degradation check
            if profile.is_degrading(self.degradation_window) and not prev_profile.is_degrading(previous.degradation_window):
                trends.append({
                    "node_id": node_id,
                    "node_title": profile.node_title,
                    "metric": "degradation",
                    "direction": "worse",
                    "detail": "Node is now showing latency degradation (was stable in previous snapshot)",
                })

        return trends
