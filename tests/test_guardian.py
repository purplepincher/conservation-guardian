"""Tests for the guardian module."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from conservation_guardian.budget import WorkflowBudget
from conservation_guardian.analyzer import WorkflowDAG
from conservation_guardian.profiler import Profiler, NodeSample, NodeProfile
from conservation_guardian.detector import WasteDetector, WasteFinding
from conservation_guardian.report import render_report


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

class TestWorkflowBudget:
    def test_default_limits(self):
        b = WorkflowBudget()
        assert b.max_tokens_per_run == 500_000
        assert b.max_cost_per_day == 50.0
        assert b.max_nodes_per_workflow == 100

    def test_is_within_budget_ok(self):
        b = WorkflowBudget()
        assert b.is_within_budget(100_000, 50_000) is True

    def test_is_within_budget_exceeds_tokens(self):
        b = WorkflowBudget(max_tokens_per_run=1_000)
        assert b.is_within_budget(800, 300) is False

    def test_is_within_budget_exceeds_daily_cost(self):
        b = WorkflowBudget(max_cost_per_day=0.01)
        b.record_run(100_000, 50_000)
        assert b.is_within_budget(100_000, 50_000) is False

    def test_record_run_returns_cost(self):
        b = WorkflowBudget()
        cost = b.record_run(1_000, 1_000)
        assert cost > 0
        assert b.daily_spend() == cost

    def test_check_node_count(self):
        b = WorkflowBudget(max_nodes_per_workflow=5)
        assert b.check_node_count(5) is True
        assert b.check_node_count(6) is False

    def test_avg_tokens_per_run(self):
        b = WorkflowBudget()
        b.record_run(1_000, 500)
        b.record_run(2_000, 500)
        assert b.avg_tokens_per_run() == 2_000.0

    def test_avg_tokens_no_runs(self):
        b = WorkflowBudget()
        assert b.avg_tokens_per_run() == 0.0


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class TestWorkflowDAG:
    @pytest.fixture()
    def sample_dag(self) -> WorkflowDAG:
        raw = {
            "graph": {
                "nodes": [
                    {"id": "start", "data": {"type": "start", "title": "Start"}},
                    {"id": "llm1", "data": {"type": "llm", "title": "Draft Email", "model": {"provider": "openai", "name": "gpt-4"}}},
                    {"id": "llm2", "data": {"type": "llm", "title": "Draft Email 2", "model": {"provider": "openai", "name": "gpt-4"}}},
                    {"id": "if1", "data": {"type": "if-else", "title": "Check Urgency"}},
                    {"id": "tool1", "data": {"type": "tool", "title": "Send Slack"}},
                    {"id": "end", "data": {"type": "end", "title": "End"}},
                ],
                "edges": [
                    {"sourceId": "start", "targetId": "llm1"},
                    {"sourceId": "start", "targetId": "llm2"},
                    {"sourceId": "llm1", "targetId": "if1"},
                    {"sourceId": "if1", "targetId": "tool1"},
                    {"sourceId": "if1", "targetId": "end"},
                ],
            }
        }
        return WorkflowDAG.from_dict(raw)

    def test_parse_nodes(self, sample_dag: WorkflowDAG):
        assert len(sample_dag.nodes) == 6
        assert sample_dag.entry_node == "start"

    def test_llm_nodes(self, sample_dag: WorkflowDAG):
        llms = sample_dag.llm_nodes()
        assert len(llms) == 2

    def test_redundant_llm_calls(self, sample_dag: WorkflowDAG):
        redundant = sample_dag.redundant_llm_calls()
        assert len(redundant) == 1
        a, b = redundant[0]
        assert "llm" in a.id and "llm" in b.id

    def test_from_empty_dict(self):
        dag = WorkflowDAG.from_dict({})
        assert len(dag.nodes) == 0


# ---------------------------------------------------------------------------
# Profiler
# ---------------------------------------------------------------------------

class TestProfiler:
    @pytest.fixture()
    def profiler_with_data(self) -> Profiler:
        p = Profiler()
        for i in range(20):
            p.record(NodeSample(
                node_id="summarizer",
                input_tokens=4_200,
                output_tokens=180,
                latency_ms=800.0 + i * 10,
                cost_usd=0.015,
                node_title="Summarizer",
            ))
        p.record(NodeSample(
            node_id="classifier",
            input_tokens=500,
            output_tokens=10,
            latency_ms=200.0,
            cost_usd=0.002,
            node_title="Classifier",
        ))
        return p

    def test_run_count(self, profiler_with_data: Profiler):
        p = profiler_with_data.get("summarizer")
        assert p is not None
        assert p.run_count == 20

    def test_avg_tokens(self, profiler_with_data: Profiler):
        p = profiler_with_data.get("summarizer")
        assert p.avg_input_tokens == 4_200.0
        assert p.avg_output_tokens == 180.0

    def test_input_output_ratio(self, profiler_with_data: Profiler):
        p = profiler_with_data.get("summarizer")
        assert p.input_output_ratio == pytest.approx(4200 / 180, rel=0.01)

    def test_top_by_cost(self, profiler_with_data: Profiler):
        top = profiler_with_data.top_by_cost(1)
        assert top[0].node_id == "summarizer"

    def test_is_degrading(self, profiler_with_data: Profiler):
        p = profiler_with_data.get("summarizer")
        # Record more with much higher latency to trigger degradation
        for i in range(10):
            profiler_with_data.record(NodeSample(
                node_id="summarizer",
                input_tokens=4_200,
                output_tokens=180,
                latency_ms=2000.0 + i * 100,
                cost_usd=0.015,
            ))
        assert p.is_degrading() is True

    def test_not_degrading(self):
        p = Profiler()
        for i in range(20):
            p.record(NodeSample(
                node_id="stable",
                input_tokens=100,
                output_tokens=100,
                latency_ms=500.0,
                cost_usd=0.01,
            ))
        profile = p.get("stable")
        assert profile is not None
        assert profile.is_degrading() is False

    # --- Persistence tests ---

    def test_save_and_load_roundtrip(self, profiler_with_data: Profiler):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            profiler_with_data.save(path)
            loaded = Profiler.load(path)
            assert len(loaded.all_profiles()) == len(profiler_with_data.all_profiles())
            orig = profiler_with_data.get("summarizer")
            loaded_p = loaded.get("summarizer")
            assert loaded_p is not None
            assert loaded_p.run_count == orig.run_count
            assert loaded_p.avg_input_tokens == orig.avg_input_tokens
            assert loaded_p.node_title == orig.node_title
        finally:
            os.unlink(path)

    def test_load_corrupted_json(self):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write("{{not valid json!!!")
            path = f.name
        try:
            with pytest.raises(ValueError, match="Corrupted JSON"):
                Profiler.load(path)
        finally:
            os.unlink(path)

    def test_load_non_dict_json(self):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump([1, 2, 3], f)
            path = f.name
        try:
            with pytest.raises(ValueError, match="Expected a JSON object"):
                Profiler.load(path)
        finally:
            os.unlink(path)

    def test_load_missing_file(self):
        with pytest.raises(FileNotFoundError):
            Profiler.load("/tmp/this_file_does_not_exist_12345.json")

    def test_load_skips_corrupted_samples(self):
        data = {
            "profiles": [
                {
                    "node_id": "test",
                    "node_title": "Test",
                    "samples": [
                        {"node_id": "test", "input_tokens": 100, "output_tokens": 50,
                         "latency_ms": 200.0, "cost_usd": 0.01},
                        {"garbage": True},  # missing required fields
                    ],
                }
            ]
        }
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(data, f)
            path = f.name
        try:
            loaded = Profiler.load(path)
            p = loaded.get("test")
            assert p is not None
            assert p.run_count == 1  # only the valid sample loaded
        finally:
            os.unlink(path)

    def test_to_from_dict_roundtrip(self, profiler_with_data: Profiler):
        data = profiler_with_data.to_dict()
        loaded = Profiler.from_dict(data)
        assert len(loaded.all_profiles()) == len(profiler_with_data.all_profiles())

    # --- Trend analysis ---

    def test_compare_detects_worsening_latency(self):
        prev = Profiler()
        for i in range(10):
            prev.record(NodeSample(node_id="n1", input_tokens=100, output_tokens=100,
                                   latency_ms=100.0, cost_usd=0.01))

        curr = Profiler()
        for i in range(10):
            curr.record(NodeSample(node_id="n1", input_tokens=100, output_tokens=100,
                                   latency_ms=200.0, cost_usd=0.01))

        trends = curr.compare(prev)
        latency_trends = [t for t in trends if t["metric"] == "latency"]
        assert len(latency_trends) == 1
        assert latency_trends[0]["direction"] == "worse"

    def test_compare_no_trends_for_stable(self):
        prev = Profiler()
        for i in range(10):
            prev.record(NodeSample(node_id="n1", input_tokens=100, output_tokens=100,
                                   latency_ms=100.0, cost_usd=0.01))
        curr = Profiler()
        for i in range(10):
            curr.record(NodeSample(node_id="n1", input_tokens=100, output_tokens=100,
                                   latency_ms=105.0, cost_usd=0.01))
        trends = curr.compare(prev)
        assert len(trends) == 0

    def test_compare_detects_worsening_cost(self):
        prev = Profiler()
        for i in range(10):
            prev.record(NodeSample(node_id="n1", input_tokens=100, output_tokens=100,
                                   latency_ms=100.0, cost_usd=0.01))
        curr = Profiler()
        for i in range(10):
            curr.record(NodeSample(node_id="n1", input_tokens=100, output_tokens=100,
                                   latency_ms=100.0, cost_usd=0.05))
        trends = curr.compare(prev)
        cost_trends = [t for t in trends if t["metric"] == "cost"]
        assert len(cost_trends) == 1
        assert cost_trends[0]["direction"] == "worse"


# ---------------------------------------------------------------------------
# Unhappy-path / edge-case tests
# ---------------------------------------------------------------------------

class TestProfilerEdgeCases:
    def test_empty_profiler_all_profiles(self):
        p = Profiler()
        assert p.all_profiles() == []

    def test_empty_profiler_top_by_cost(self):
        p = Profiler()
        assert p.top_by_cost(5) == []

    def test_empty_profiler_top_by_tokens(self):
        p = Profiler()
        assert p.top_by_tokens(5) == []

    def test_empty_profile_input_output_ratio(self):
        """Profile with no samples should return 0.0 for input_output_ratio, not inf."""
        profile = NodeProfile(node_id="empty_node")
        assert profile.run_count == 0
        assert profile.input_output_ratio == 0.0

    def test_zero_output_tokens_input_output_ratio(self):
        """Profile where all outputs are 0 should return inf."""
        p = Profiler()
        p.record(NodeSample(node_id="zero_out", input_tokens=100, output_tokens=0,
                            latency_ms=100.0, cost_usd=0.01))
        profile = p.get("zero_out")
        assert profile is not None
        assert profile.input_output_ratio == float("inf")

    def test_zero_input_tokens(self):
        p = Profiler()
        p.record(NodeSample(node_id="zero_in", input_tokens=0, output_tokens=100,
                            latency_ms=100.0, cost_usd=0.01))
        profile = p.get("zero_in")
        assert profile is not None
        assert profile.input_output_ratio == 0.0

    def test_missing_node_title(self):
        p = Profiler()
        p.record(NodeSample(node_id="notitle", input_tokens=100, output_tokens=100,
                            latency_ms=100.0, cost_usd=0.01))
        profile = p.get("notitle")
        assert profile is not None
        assert profile.node_title == ""

    def test_get_nonexistent_node(self):
        p = Profiler()
        assert p.get("does_not_exist") is None

    def test_empty_profile_all_properties(self):
        """Ensure all properties handle empty samples gracefully."""
        profile = NodeProfile(node_id="empty")
        assert profile.avg_input_tokens == 0.0
        assert profile.avg_output_tokens == 0.0
        assert profile.avg_latency_ms == 0.0
        assert profile.avg_cost == 0.0
        assert profile.total_cost == 0.0
        assert profile.total_tokens == 0
        assert profile.input_output_ratio == 0.0
        assert profile.cost_trend() == []
        assert profile.latency_trend() == []
        assert profile.is_degrading() is False


class TestPersistenceEdgeCases:
    def test_load_with_missing_fields(self):
        """JSON with missing optional fields should still load."""
        data = {"profiles": [{"node_id": "x"}]}
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(data, f)
            path = f.name
        try:
            loaded = Profiler.load(path)
            p = loaded.get("x")
            assert p is not None
            assert p.node_title == ""
            assert p.run_count == 0
        finally:
            os.unlink(path)

    def test_load_empty_profiles_list(self):
        data = {"profiles": []}
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(data, f)
            path = f.name
        try:
            loaded = Profiler.load(path)
            assert loaded.all_profiles() == []
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class TestWasteDetector:
    @pytest.fixture()
    def detector(self) -> WasteDetector:
        p = Profiler()
        for _ in range(10):
            p.record(NodeSample(
                node_id="summarizer",
                input_tokens=4_200,
                output_tokens=180,
                latency_ms=800.0,
                cost_usd=0.015,
                node_title="Summarizer",
            ))
        for _ in range(10):
            p.record(NodeSample(
                node_id="rephraser",
                input_tokens=500,
                output_tokens=500,
                latency_ms=300.0,
                cost_usd=0.005,
                node_title="Rephraser",
            ))
        return WasteDetector(p)

    def test_detect_finds_overprompted(self, detector: WasteDetector):
        findings = detector.detect()
        categories = [f.category for f in findings]
        assert "overprompted" in categories

    def test_overprompted_message(self, detector: WasteDetector):
        findings = [f for f in detector.detect() if f.category == "overprompted"]
        assert len(findings) == 1
        assert "4,200" in findings[0].message
        assert "180" in findings[0].message

    def test_no_findings_on_empty(self):
        p = Profiler()
        d = WasteDetector(p)
        assert d.detect() == []

    def test_idle_renamed_to_low_utilization(self):
        """Bug #1: 'idle' category renamed to 'low_utilization'."""
        p = Profiler()
        # Big expensive node
        for _ in range(20):
            p.record(NodeSample(node_id="big", input_tokens=5000, output_tokens=500,
                                latency_ms=800.0, cost_usd=0.10, node_title="Big"))
        # Small cheap node with enough runs
        for _ in range(10):
            p.record(NodeSample(node_id="tiny", input_tokens=50, output_tokens=10,
                                latency_ms=50.0, cost_usd=0.001, node_title="Tiny"))
        d = WasteDetector(p, low_utilization_threshold=0.1)
        findings = d.detect()
        low_util = [f for f in findings if f.category == "low_utilization"]
        assert len(low_util) >= 1
        assert "underused" in low_util[0].message.lower()
        # Ensure no "idle" category exists
        assert not any(f.category == "idle" for f in findings)

    def test_expensive_model_ratio_configurable(self):
        """Bug #2: expensive_model threshold is configurable and defaults to 0.8."""
        # Use exactly 2 nodes: top_by_cost(3) returns 2, len(top)<=2 is satisfied
        p = Profiler()
        for _ in range(10):
            p.record(NodeSample(node_id="a", input_tokens=100, output_tokens=100,
                                latency_ms=100.0, cost_usd=0.10))
        for _ in range(10):
            p.record(NodeSample(node_id="b", input_tokens=100, output_tokens=100,
                                latency_ms=100.0, cost_usd=0.01))
        # Costs: a=1.0, b=0.1 => total=1.1, top2=1.1 => 100%

        # With ratio 0.8, 100% > 0.8 should trigger
        d = WasteDetector(p, expensive_model_ratio=0.8)
        findings = d.detect()
        assert any(f.category == "expensive_model" for f in findings)

        # With a ratio > 1.0, should never trigger
        d2 = WasteDetector(p, expensive_model_ratio=1.5)
        findings2 = d2.detect()
        assert not any(f.category == "expensive_model" for f in findings2)

    def test_expensive_model_min_samples(self):
        """Bug #2: requires minimum sample count before flagging."""
        p = Profiler()
        # Only 2 samples total — below default min of 5
        p.record(NodeSample(node_id="a", input_tokens=100, output_tokens=100,
                            latency_ms=100.0, cost_usd=0.10))
        p.record(NodeSample(node_id="b", input_tokens=100, output_tokens=100,
                            latency_ms=100.0, cost_usd=0.05))
        d = WasteDetector(p, expensive_model_min_samples=5)
        findings = d.detect()
        assert not any(f.category == "expensive_model" for f in findings)

    def test_all_thresholds_configurable(self):
        """Bug #4: all thresholds are configurable via constructor."""
        p = Profiler(degradation_window=3)
        d = WasteDetector(
            p,
            max_io_ratio=5.0,
            low_utilization_threshold=0.05,
            expensive_model_ratio=0.9,
            expensive_model_min_samples=10,
            degradation_window=3,
        )
        assert d.max_io_ratio == 5.0
        assert d.low_utilization_threshold == 0.05
        assert d.expensive_model_ratio == 0.9
        assert d.expensive_model_min_samples == 10
        assert d.degradation_window == 3


# ---------------------------------------------------------------------------
# Detector unhappy-path
# ---------------------------------------------------------------------------

class TestDetectorEdgeCases:
    def test_detect_all_zero_cost(self):
        """Nodes with zero cost should not crash."""
        p = Profiler()
        for _ in range(10):
            p.record(NodeSample(node_id="free", input_tokens=100, output_tokens=100,
                                latency_ms=100.0, cost_usd=0.0))
        d = WasteDetector(p)
        findings = d.detect()
        # No crash; may or may not have findings
        assert isinstance(findings, list)

    def test_detect_single_node(self):
        p = Profiler()
        for _ in range(10):
            p.record(NodeSample(node_id="only", input_tokens=100, output_tokens=100,
                                latency_ms=100.0, cost_usd=0.01))
        d = WasteDetector(p)
        findings = d.detect()
        assert isinstance(findings, list)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

class TestReport:
    def test_renders_markdown(self):
        b = WorkflowBudget()
        p = Profiler()
        p.record(NodeSample(
            node_id="test",
            input_tokens=1000,
            output_tokens=100,
            latency_ms=500.0,
            cost_usd=0.01,
            node_title="Test Node",
        ))
        findings = [WasteFinding(
            node_id="test",
            node_title="Test Node",
            category="overprompted",
            severity="high",
            message="Test message",
            suggestion="Test suggestion",
        )]
        report = render_report(budget=b, profiler=p, findings=findings, workflow_name="TestFlow")
        assert "# Conservation Report — TestFlow" in report
        assert "Budget Summary" in report
        assert "Top Nodes by Cost" in report
        assert "Waste Findings" in report
        assert "Test Node" in report

    def test_empty_report(self):
        report = render_report(workflow_name="Empty")
        assert "Conservation Report" in report

    def test_report_with_low_utilization_category(self):
        """Report should render low_utilization category correctly."""
        findings = [WasteFinding(
            node_id="x",
            node_title="Node X",
            category="low_utilization",
            severity="low",
            message="Node X is underused.",
            suggestion="Remove it.",
        )]
        report = render_report(findings=findings, workflow_name="Test")
        assert "Low Utilization" in report
