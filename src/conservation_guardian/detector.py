"""Waste detection: find nodes that burn tokens without proportional value."""

from __future__ import annotations

from dataclasses import dataclass

from .profiler import NodeProfile, Profiler


@dataclass
class WasteFinding:
    node_id: str
    node_title: str
    category: str  # "overprompted", "low_utilization", "expensive_model", "redundant"
    severity: str  # "low", "medium", "high"
    message: str
    suggestion: str


# Default thresholds — all configurable via constructor
DEFAULT_MAX_IO_RATIO = 15.0
DEFAULT_LOW_UTILIZATION_THRESHOLD = 0.1
DEFAULT_EXPENSIVE_MODEL_RATIO = 0.8
DEFAULT_EXPENSIVE_MODEL_MIN_SAMPLES = 5
DEFAULT_DEGRADATION_WINDOW = 5


class WasteDetector:
    """Analyze profiler data to surface actionable waste findings.

    All detection thresholds are configurable via constructor parameters.

    Parameters
    ----------
    profiler:
        The profiler instance to analyze.
    max_io_ratio:
        Input-to-output token ratio above which a node is flagged as overprompted.
    low_utilization_threshold:
        Fraction of total cost below which a node is considered underused
        (i.e. it accounts for very little spend despite being run regularly).
    expensive_model_ratio:
        Fraction of total cost that the top-N nodes must account for before
        flagging expensive-model concentration.
    expensive_model_min_samples:
        Minimum total sample count across all nodes before the expensive-model
        concentration check fires (avoids false positives on small datasets).
    degradation_window:
        Number of recent runs to consider when checking for latency degradation.
    """

    def __init__(
        self,
        profiler: Profiler,
        *,
        max_io_ratio: float = DEFAULT_MAX_IO_RATIO,
        low_utilization_threshold: float = DEFAULT_LOW_UTILIZATION_THRESHOLD,
        expensive_model_ratio: float = DEFAULT_EXPENSIVE_MODEL_RATIO,
        expensive_model_min_samples: int = DEFAULT_EXPENSIVE_MODEL_MIN_SAMPLES,
        degradation_window: int = DEFAULT_DEGRADATION_WINDOW,
    ) -> None:
        self.profiler = profiler
        self.max_io_ratio = max_io_ratio
        self.low_utilization_threshold = low_utilization_threshold
        self.expensive_model_ratio = expensive_model_ratio
        self.expensive_model_min_samples = expensive_model_min_samples
        self.degradation_window = degradation_window

    def detect(self) -> list[WasteFinding]:
        findings: list[WasteFinding] = []
        profiles = self.profiler.all_profiles()
        if not profiles:
            return findings

        total_cost = sum(p.total_cost for p in profiles)

        for p in profiles:
            findings.extend(self._check_overprompted(p))
            findings.extend(self._check_low_utilization(p, total_cost))

        findings.extend(self._check_expensive_model_concentration(profiles, total_cost))
        return findings

    def _check_overprompted(self, p: NodeProfile) -> list[WasteFinding]:
        ratio = p.input_output_ratio
        if ratio > self.max_io_ratio and p.avg_input_tokens > 200:
            return [WasteFinding(
                node_id=p.node_id,
                node_title=p.node_title,
                category="overprompted",
                severity="high" if ratio > 30 else "medium",
                message=(
                    f"Node '{p.node_title or p.node_id}' receives {p.avg_input_tokens:,.0f} tokens avg "
                    f"but outputs {p.avg_output_tokens:,.0f} (ratio {ratio:.1f}×)."
                ),
                suggestion="Consider extractive pre-filtering, summarization, or reducing the prompt template size.",
            )]
        return []

    def _check_low_utilization(self, p: NodeProfile, total_cost: float) -> list[WasteFinding]:
        """Flag nodes that account for a tiny fraction of total cost despite being run regularly.

        These are *underused* nodes — they run often but barely spend anything,
        which may mean they're candidates for removal or conditional bypass.
        """
        if total_cost == 0:
            return []
        fraction = p.total_cost / total_cost
        if fraction < self.low_utilization_threshold and p.run_count > 5:
            return [WasteFinding(
                node_id=p.node_id,
                node_title=p.node_title,
                category="low_utilization",
                severity="low",
                message=(
                    f"Node '{p.node_title or p.node_id}' accounts for only {fraction:.1%} of cost "
                    f"over {p.run_count} runs — underused relative to the workflow."
                ),
                suggestion="Consider removing or conditionally bypassing this node.",
            )]
        return []

    def _check_expensive_model_concentration(
        self, profiles: list[NodeProfile], total_cost: float
    ) -> list[WasteFinding]:
        findings: list[WasteFinding] = []
        if not profiles or total_cost == 0:
            return findings

        # Require a minimum number of samples before flagging concentration
        total_samples = sum(p.run_count for p in profiles)
        if total_samples < self.expensive_model_min_samples:
            return findings

        top = self.profiler.top_by_cost(3)
        top_cost = sum(p.total_cost for p in top)
        fraction = top_cost / total_cost

        if fraction > self.expensive_model_ratio and len(top) <= 2:
            names = ", ".join(f"'{p.node_title or p.node_id}'" for p in top)
            findings.append(WasteFinding(
                node_id=",".join(p.node_id for p in top),
                node_title=names,
                category="expensive_model",
                severity="high",
                message=(
                    f"Two nodes ({names}) account for {fraction:.0%} of cost. "
                    f"If they use an expensive model, consider downgrading for simple tasks."
                ),
                suggestion=(
                    "Tasks like classification, extraction, or short summarization "
                    "often run fine on cheaper models."
                ),
            ))
        return findings
