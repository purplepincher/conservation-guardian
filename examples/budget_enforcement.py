"""Budget enforcement — fail fast when a workflow exceeds limits.

Sample output:
    Run 1: $0.0050 — OK
    Run 2: $0.0050 — OK
    Run 3: $0.0050 — OK
    Run 4: BUDGET EXCEEDED: Daily spend $0.0150 exceeds limit $0.0170
"""

from conservation_guardian import WorkflowBudget, BudgetExceededError

# Tight budget for demonstration. Pricing is set explicitly so each run
# costs a predictable $0.005 (100k input + 50k output at 0.00003 / 0.00004
# per 1K tokens); the $0.017 daily cap then trips on the fourth run.
budget = WorkflowBudget(
    max_cost_per_day=0.017,
    max_tokens_per_run=500_000,
    price_input_per_1k=0.00003,
    price_output_per_1k=0.00004,
)

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
