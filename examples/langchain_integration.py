"""LangChain integration — hook into LangChain callback system.

This example shows how to extract profiling data from LangChain LLM runs
and feed it into Conservation Guardian.

Sample output:
    Extracted 3 samples from LangChain runs
    [high] Node 'gpt-4' receives 4,200 tokens avg but outputs 180 (ratio 23.3×).
      → Consider extractive pre-filtering, summarization, or reducing the prompt template size.
"""

from conservation_guardian import (
    Profiler,
    WasteDetector,
    Reporter,
    LangChainAdapter,
)

# Simulated LangChain callback data (in practice, collected via callbacks)
langchain_records = [
    {
        "llm_output": {
            "token_usage": {"prompt_tokens": 4200, "completion_tokens": 180},
            "model_name": "gpt-4",
        },
        "latency_ms": 820.0,
    },
    {
        "llm_output": {
            "token_usage": {"prompt_tokens": 500, "completion_tokens": 10},
            "model_name": "gpt-4",
        },
        "latency_ms": 200.0,
    },
    {
        "llm_output": {
            "token_usage": {"prompt_tokens": 200, "completion_tokens": 150},
            "model_name": "gpt-3.5-turbo",
        },
        "latency_ms": 150.0,
    },
]

# Extract samples using the adapter
adapter = LangChainAdapter(langchain_records)
samples = adapter.extract_samples()
print(f"Extracted {len(samples)} samples from LangChain runs")

# Feed into profiler
profiler = Profiler()
for sample in samples:
    profiler.record(sample)

# Detect and report
findings = WasteDetector(profiler).detect()
for f in findings:
    print(f"[{f.severity}] {f.message}")
    print(f"  → {f.suggestion}")

report = Reporter(profiler=profiler, findings=findings, workflow_name="LangChain Agent")
print(report.to_markdown())
