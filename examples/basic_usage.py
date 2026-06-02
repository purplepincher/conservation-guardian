"""Basic usage — profile nodes, detect waste, generate a report.

Sample output:
    2 LLM nodes, 1 redundant
    [high] Node 'Summarizer' receives 4,200 tokens avg but outputs 180 (ratio 23.3×).
      → Consider extractive pre-filtering, summarization, or reducing the prompt template size.

    # Conservation Report — My Workflow
    ...
"""

from conservation_guardian import (
    Profiler,
    NodeSample,
    WasteDetector,
    Reporter,
    WorkflowBudget,
)

# 1. Profile some runs
profiler = Profiler()
profiler.record(NodeSample(
    node_id="summarizer", input_tokens=4200, output_tokens=180,
    latency_ms=820.0, cost_usd=0.015, node_title="Summarizer",
))
profiler.record(NodeSample(
    node_id="classifier", input_tokens=500, output_tokens=10,
    latency_ms=200.0, cost_usd=0.002, node_title="Classifier",
))

# 2. Detect waste
findings = WasteDetector(profiler).detect()
for f in findings:
    print(f"[{f.severity}] {f.message}")
    print(f"  → {f.suggestion}")

# 3. Generate report
budget = WorkflowBudget()
report = Reporter(budget=budget, profiler=profiler, findings=findings, workflow_name="My Workflow")
print(report.to_markdown())
