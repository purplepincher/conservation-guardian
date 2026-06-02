"""Historical tracking — save, load, and compare profiles over time.

Sample output:
    === Monday snapshot ===
    Saved snapshot with 1 nodes

    === Wednesday snapshot (costs went up) ===
    Saved snapshot with 1 nodes

    === Trend comparison ===
    [cost] WORSE: Cost per run increased 300.0% ($0.0100 → $0.0400)
    [latency] WORSE: Latency increased 100.0% (200ms → 400ms)
"""

import tempfile
import os

from conservation_guardian import Profiler, NodeSample, Reporter

# --- Monday snapshot ---
monday = Profiler()
for _ in range(10):
    monday.record(NodeSample(
        node_id="summarizer", input_tokens=1000, output_tokens=200,
        latency_ms=200.0, cost_usd=0.01, node_title="Summarizer",
    ))

path_monday = os.path.join(tempfile.gettempdir(), "monday_profile.json")
monday.save(path_monday)
print("=== Monday snapshot ===")
print(f"Saved snapshot with {len(monday.all_profiles())} nodes")

# --- Wednesday snapshot (costs doubled) ---
wednesday = Profiler()
for _ in range(10):
    wednesday.record(NodeSample(
        node_id="summarizer", input_tokens=1000, output_tokens=200,
        latency_ms=400.0, cost_usd=0.04, node_title="Summarizer",
    ))

path_wednesday = os.path.join(tempfile.gettempdir(), "wednesday_profile.json")
wednesday.save(path_wednesday)
print("\n=== Wednesday snapshot (costs went up) ===")
print(f"Saved snapshot with {len(wednesday.all_profiles())} nodes")

# --- Load and compare ---
loaded_monday = Profiler.load(path_monday)
loaded_wednesday = Profiler.load(path_wednesday)

trends = loaded_wednesday.compare(loaded_monday)
print("\n=== Trend comparison ===")
for t in trends:
    print(f"[{t['metric']}] {t['direction'].upper()}: {t['detail']}")

# Cleanup
os.unlink(path_monday)
os.unlink(path_wednesday)
