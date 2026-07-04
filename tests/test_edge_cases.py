"""Unhappy-path and edge-case tests."""

from __future__ import annotations

import json
import os
import tempfile
import threading

import pytest

from conservation_guardian.budget import WorkflowBudget
from conservation_guardian.detector import WasteFinding
from conservation_guardian.exceptions import AdapterError, BudgetExceededError
from conservation_guardian.profiler import NodeProfile, NodeSample, Profiler
from conservation_guardian.reporter import Reporter
from conservation_guardian.adapters import GenericAdapter, OpenAIAdapter, LangChainAdapter


# ---------------------------------------------------------------------------
# Empty / minimal profiler
# ---------------------------------------------------------------------------

class TestEmptyProfiler:
    def test_empty_profiler_all_profiles(self):
        p = Profiler()
        assert p.all_profiles() == []

    def test_empty_profiler_top_by_cost(self):
        p = Profiler()
        assert p.top_by_cost(5) == []

    def test_empty_profiler_top_by_tokens(self):
        p = Profiler()
        assert p.top_by_tokens(5) == []

    def test_empty_profile_all_properties(self):
        profile = NodeProfile(node_id="empty")
        assert profile.run_count == 0
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


class TestSingleSample:
    def test_single_sample(self):
        p = Profiler()
        p.record(NodeSample(node_id="solo", input_tokens=100, output_tokens=50,
                             latency_ms=100.0, cost_usd=0.01))
        profile = p.get("solo")
        assert profile is not None
        assert profile.run_count == 1
        assert profile.avg_input_tokens == 100.0
        assert profile.total_cost == 0.01


class TestZeroOutputNodes:
    def test_zero_output_ratio_is_inf(self):
        p = Profiler()
        p.record(NodeSample(node_id="z", input_tokens=100, output_tokens=0,
                             latency_ms=100.0, cost_usd=0.01))
        assert p.get("z").input_output_ratio == float("inf")


# ---------------------------------------------------------------------------
# Corrupted persistence
# ---------------------------------------------------------------------------

class TestCorruptedPersistence:
    def test_corrupted_json_file(self):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write("{{not valid json!!!")
            path = f.name
        try:
            with pytest.raises(ValueError, match="Corrupted JSON"):
                Profiler.load(path)
        finally:
            os.unlink(path)

    def test_non_dict_json(self):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump([1, 2, 3], f)
            path = f.name
        try:
            with pytest.raises(ValueError, match="Expected a JSON object"):
                Profiler.load(path)
        finally:
            os.unlink(path)

    def test_missing_file(self):
        with pytest.raises(FileNotFoundError):
            Profiler.load("/tmp/nonexistent_file_abc123.json")

    def test_corrupted_samples_skipped(self):
        data = {
            "profiles": [{
                "node_id": "test",
                "samples": [
                    {"node_id": "test", "input_tokens": 100, "output_tokens": 50,
                     "latency_ms": 200.0, "cost_usd": 0.01},
                    {"garbage": True},
                ],
            }]
        }
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(data, f)
            path = f.name
        try:
            loaded = Profiler.load(path)
            assert loaded.get("test").run_count == 1
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Adapter edge cases
# ---------------------------------------------------------------------------

class TestAdapterEdgeCases:
    def test_generic_adapter_missing_fields(self):
        """Records with missing fields use defaults."""
        adapter = GenericAdapter([{"node_id": "x"}])
        samples = adapter.extract_samples()
        assert len(samples) == 1
        assert samples[0].node_id == "x"
        assert samples[0].input_tokens == 0

    def test_generic_adapter_empty_records(self):
        adapter = GenericAdapter([])
        assert adapter.extract_samples() == []

    def test_generic_adapter_no_source(self):
        with pytest.raises(AdapterError):
            GenericAdapter().extract_samples()

    def test_openai_adapter_auto_pricing(self):
        adapter = OpenAIAdapter([
            {"model": "gpt-4o-mini", "usage": {"prompt_tokens": 1000, "completion_tokens": 200}},
        ])
        samples = adapter.extract_samples()
        assert len(samples) == 1
        assert samples[0].node_id == "gpt-4o-mini"
        assert samples[0].cost_usd > 0

    def test_openai_adapter_no_source(self):
        with pytest.raises(AdapterError):
            OpenAIAdapter().extract_samples()

    def test_langchain_adapter_no_source(self):
        with pytest.raises(AdapterError):
            LangChainAdapter().extract_samples()

    def test_generic_adapter_nested_fields(self):
        adapter = GenericAdapter(
            [{"name": "n1", "metrics": {"in": 100, "out": 50}, "time": 200.0}],
            field_map={
                "node_id": "name",
                "input_tokens": "metrics.in",
                "output_tokens": "metrics.out",
                "latency_ms": "time",
            },
        )
        samples = adapter.extract_samples()
        assert len(samples) == 1
        assert samples[0].input_tokens == 100
        assert samples[0].output_tokens == 50

    def test_generic_adapter_jsonl_file(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
            f.write('{"node_id": "a", "input_tokens": 100, "output_tokens": 50, "latency_ms": 100.0, "cost_usd": 0.01}\n')
            f.write('{"node_id": "b", "input_tokens": 200, "output_tokens": 100, "latency_ms": 200.0, "cost_usd": 0.02}\n')
            path = f.name
        try:
            adapter = GenericAdapter(path=path)
            samples = adapter.extract_samples()
            assert len(samples) == 2
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Concurrent access
# ---------------------------------------------------------------------------

class TestConcurrentAccess:
    def test_concurrent_record(self):
        """Multiple threads recording to the same profiler should not crash."""
        p = Profiler()
        errors: list[Exception] = []

        def worker(node_id: str, count: int) -> None:
            try:
                for _ in range(count):
                    p.record(NodeSample(
                        node_id=node_id,
                        input_tokens=100,
                        output_tokens=50,
                        latency_ms=100.0,
                        cost_usd=0.01,
                    ))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(f"node_{i}", 50)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        total_samples = sum(pp.run_count for pp in p.all_profiles())
        assert total_samples == 200  # 4 threads × 50 each


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class TestExceptions:
    def test_budget_exceeded_error(self):
        with pytest.raises(BudgetExceededError) as exc_info:
            raise BudgetExceededError("over", metric="daily_cost", current=60.0, limit=50.0)
        assert exc_info.value.metric == "daily_cost"
        assert exc_info.value.current == 60.0

    def test_adapter_error_cause(self):
        original = OSError("disk full")
        with pytest.raises(AdapterError) as exc_info:
            raise AdapterError("failed", adapter_name="test", cause=original)
        assert exc_info.value.adapter_name == "test"
        assert exc_info.value.cause is original


# ---------------------------------------------------------------------------
# Reporter edge cases
# ---------------------------------------------------------------------------

class TestReporterFormats:
    def test_json_output(self):
        p = Profiler()
        p.record(NodeSample(node_id="n1", input_tokens=100, output_tokens=50,
                             latency_ms=100.0, cost_usd=0.01, node_title="Node 1"))
        r = Reporter(profiler=p, workflow_name="Test")
        output = r.to_json()
        data = json.loads(output)
        assert data["workflow_name"] == "Test"
        assert len(data["top_nodes"]) == 1

    def test_prometheus_output(self):
        p = Profiler()
        p.record(NodeSample(node_id="n1", input_tokens=100, output_tokens=50,
                             latency_ms=100.0, cost_usd=0.01, node_title="Node 1"))
        r = Reporter(profiler=p, workflow_name="Test")
        output = r.to_prometheus()
        assert "conservation_node_total_cost" in output
        assert 'node="Node 1"' in output

    def test_slack_output(self):
        p = Profiler()
        p.record(NodeSample(node_id="n1", input_tokens=100, output_tokens=50,
                             latency_ms=100.0, cost_usd=0.01, node_title="Node 1"))
        findings = [WasteFinding(node_id="n1", node_title="Node 1", category="overprompted",
                                 severity="high", message="too much", suggestion="less")]
        r = Reporter(profiler=p, findings=findings, workflow_name="Test")
        output = r.to_slack()
        data = json.loads(output)
        assert "blocks" in data
        assert any("Top Nodes" in str(b) for b in data["blocks"])

    def test_empty_reporter(self):
        r = Reporter(workflow_name="Empty")
        assert r.to_json() is not None
        assert r.to_prometheus() is not None
        assert r.to_slack() is not None
        assert r.to_markdown() is not None

    def test_reporter_with_budget(self):
        b = WorkflowBudget()
        b.record_run(1000, 500)
        r = Reporter(budget=b, workflow_name="Test")
        prom = r.to_prometheus()
        assert "conservation_budget_daily_spend" in prom

    def test_markdown_equals_render_report(self):
        p = Profiler()
        p.record(NodeSample(node_id="n1", input_tokens=100, output_tokens=50,
                             latency_ms=100.0, cost_usd=0.01, node_title="N1"))
        r = Reporter(profiler=p, workflow_name="Test")
        assert r.to_markdown() == r.to_markdown()  # idempotent
