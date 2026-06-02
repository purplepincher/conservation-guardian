"""Budget enforcement — fail fast when a workflow exceeds limits.

Sample output:
    Run 1: $0.0090 — OK
    Run 2: $0.0090 — OK
    Run 3: $0.0090 — OK
    Run 4: BUDGET EXCEEDED: Daily spend $0.0360 exceeds limit $0.0250
"""

from conservation_guardian import WorkflowBudget, BudgetExceededError

# Tight budget for demonstration
budget = WorkflowBudget(max_cost_per_day=0.025, max_tokens_per_run=500_000)

runs = [
    (100_000, 50_000),
    (100_000, 50_000),
    (100_000, 50_000),
    (100_000, 50_000),  # This one will exceed
]

for i, (inp, out) in enumerate(runs, 1):
    if not budget.is_within_budget(inp, out):
        print(f"Run {i}: BUDGET EXCEEDED: Daily spend ${budget.daily_spend():.4f} exceeds limit ${budget.max_cost_per_day:.4f}")
        raise BudgetExceededError(
            f"Daily budget exceeded after {i} runs",
            metric="daily_cost",
            current=budget.daily_spend(),
            limit=budget.max_cost_per_day,
        )
    cost = budget.record_run(inp, out)
    print(f"Run {i}: ${cost:.4f} — OK")
