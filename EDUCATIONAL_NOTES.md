# Educational Notes — conservation-guardian

This document explains the waste-detection heuristics and budget model at
more depth than the README allows. Every threshold and formula below was
traced to the source code.

## The three waste-detection checks (and the one that's missing)

`WasteDetector.detect()` runs exactly three checks against profiler data.
Understanding each one's thresholds is the key to tuning the detector for
your workflow.

### 1. Overprompted (`_check_overprompted`)

**What it finds:** Nodes that receive far more input tokens than they produce
in output — a sign of bloated prompts or unnecessary context.

**Condition:** Both must be true:
- `input_output_ratio > max_io_ratio` (default 15.0)
- `avg_input_tokens > 200`

The 200-token floor prevents flagging nodes that are simply small. Without
it, a node receiving 15 tokens and outputting 1 would have ratio 15.0 and
get flagged — a false positive on trivial operations.

**Severity:** `high` if ratio > 30, otherwise `medium`.

**The ratio edge case:** If `avg_output_tokens` is 0 (the node produces no
output), `input_output_ratio` returns `float("inf")`. This means a node
that always returns empty will always be flagged as overprompted — which
may or may not be what you want. The 200-token input floor partially
mitigates this.

### 2. Low utilization (`_check_low_utilization`)

**What it finds:** Nodes that account for a tiny fraction of total cost
despite being run frequently — candidates for removal or conditional bypass.

**Condition:** Both must be true:
- `total_cost / overall_total_cost < low_utilization_threshold` (default 0.1, i.e. 10%)
- `run_count > 5`

The 5-run floor ensures the node has been exercised enough to draw a
conclusion. A node run twice and costing nothing might just be early in
its lifecycle.

**Severity:** Always `low`.

**Important:** This check divides by `total_cost`. If all nodes have zero
cost (e.g., you didn't set `cost_usd` on samples), the check short-circuits
and produces no findings. This is correct behavior — without cost data,
"low utilization by cost" is meaningless.

### 3. Expensive model concentration (`_check_expensive_model_concentration`)

**What it finds:** When the top-3 nodes by cost account for more than 80%
of total spend — a sign that a few expensive operations dominate.

**Condition:** All must be true:
- `total_samples across all nodes >= expensive_model_min_samples` (default 5)
- `top_3_cost / total_cost > expensive_model_ratio` (default 0.8)
- `len(top_3) <= 2` (only fires when 1-2 nodes dominate, not 3)

The `len(top) <= 2` guard is subtle: `top_by_cost(3)` can return fewer than
3 nodes if the profiler has fewer profiles. The check only fires when the
concentration is in 1-2 nodes — if 3 nodes each account for ~27%, that's
spread out enough to not flag.

**Severity:** Always `high`.

### The missing check: latency degradation

`NodeProfile.is_degrading(window=5)` exists and is tested — it compares the
mean latency of the last `window` samples to the mean of the `window`
samples before that, and returns `True` if recent latency is more than 20%
higher. But **`detect()` never calls it**. The `degradation_window`
parameter on `WasteDetector` is accepted, stored, and then ignored.

This means: if you want degradation findings, you must call
`profile.is_degrading()` yourself and construct `WasteFinding` objects
manually, or use `Profiler.compare()` which does check degradation as part
of its trend analysis.

## The budget model

### How `is_within_budget` works

```
total = input_tokens + output_tokens
if total > max_tokens_per_run:       return False
cost = (input * price_in + output * price_out) / 1000
if daily_spend[today] + cost > max_cost_per_day:  return False
return True
```

Two independent gates: a per-run token cap and a cumulative daily cost cap.
Both must pass. Note that `is_within_budget` does **not** check
`max_nodes_per_workflow` — that's a separate `check_node_count()` call.

### How `record_run` works

```
total = input_tokens + output_tokens
run_token_counts.append(total)
cost = (input * price_in + output * price_out) / 1000
daily_spend[date.today()] += cost
return cost
```

`record_run` always succeeds — it records the run regardless of whether it
was within budget. The budget check (`is_within_budget`) and the recording
(`record_run`) are **separate calls**. This is by design: you check before
running, then record after. If you skip the check, the run still gets
recorded and counts against future checks.

### The daily spend reset

`daily_spend` is keyed by `date.today()`. When the calendar date changes,
yesterday's spend becomes invisible to `daily_spend()` (which defaults to
today). But the old entries remain in the dict — they're just not queried.
There is no automatic cleanup.

More importantly, the entire `_daily_spend` dict lives in memory. When the
process restarts, it's gone. There is no persistence layer for budget state
— only the `Profiler` has `save()`/`load()`.

## The `compare()` method's thresholds

`Profiler.compare(previous)` compares the current profiler state to a
previous snapshot and returns trend dicts. Three checks, each with a 20%
threshold:

| Metric | Direction | Threshold | Condition |
|--------|-----------|-----------|-----------|
| Latency | worse/better | ±20% | Both profilers must have ≥ `degradation_window` runs |
| Cost | worse only | +20% | Both profilers must have > 0 runs |
| Degradation | worse only | N/A | Current is degrading AND previous was not |

The latency check is **bidirectional** (reports both improvements and
regressions), but the cost check is **unidirectional** (only reports cost
increases, not decreases). This is a deliberate choice — cost decreases are
good news and don't need alerting.
