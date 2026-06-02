"""Markdown conservation reports."""

from __future__ import annotations

from typing import Optional

from .analyzer import WorkflowDAG
from .budget import WorkflowBudget
from .detector import WasteFinding
from .profiler import Profiler


def render_report(
    *,
    budget: Optional[WorkflowBudget] = None,
    dag: Optional[WorkflowDAG] = None,
    profiler: Optional[Profiler] = None,
    findings: Optional[list[WasteFinding]] = None,
    workflow_name: str = "Workflow",
) -> str:
    """Render a full Markdown conservation report."""
    parts: list[str] = []
    parts.append(f"# Conservation Report — {workflow_name}\n")

    if budget is not None:
        parts.append("\n## Budget Summary\n")
        parts.append(f"- **Max tokens / run:** {budget.max_tokens_per_run:,}")
        parts.append(f"- **Max cost / day:** ${budget.max_cost_per_day:.2f}")
        parts.append(f"- **Max nodes / workflow:** {budget.max_nodes_per_workflow}")
        parts.append(f"- **Today's spend:** ${budget.daily_spend():.4f}")
        parts.append(f"- **Avg tokens / run:** {budget.avg_tokens_per_run():,.0f}")

    if dag is not None:
        parts.append("\n## DAG Analysis\n")
        parts.append(f"- **Total nodes:** {len(dag.nodes)}")
        parts.append(f"- **LLM nodes:** {len(dag.llm_nodes())}")

        redundant = dag.redundant_llm_calls()
        if redundant:
            parts.append(f"- **Redundant LLM calls:** {len(redundant)}")
            for a, b in redundant:
                parts.append(f"  - `{a.title or a.id}` ↔ `{b.title or b.id}` (same model & upstream)")
        else:
            parts.append("- **Redundant LLM calls:** None detected ✅")

        dead = dag.dead_branches()
        if dead:
            parts.append(f"- **Dead branches:** {len(dead)}")
            for path in dead:
                labels = [dag.nodes[nid].title or nid for nid in path if nid in dag.nodes]
                parts.append(f"  - `{' → '.join(labels)}`")
        else:
            parts.append("- **Dead branches:** None detected ✅")

    if profiler is not None:
        parts.append("\n## Top Nodes by Cost\n")
        top = profiler.top_by_cost(5)
        if top:
            parts.append("| Node | Runs | Avg In | Avg Out | Avg Cost | Total Cost |")
            parts.append("|------|------|--------|---------|----------|------------|")
            for p in top:
                parts.append(
                    f"| {p.node_title or p.node_id} | {p.run_count} | "
                    f"{p.avg_input_tokens:,.0f} | {p.avg_output_tokens:,.0f} | "
                    f"${p.avg_cost:.4f} | ${p.total_cost:.4f} |"
                )
        else:
            parts.append("_No profiling data yet._")

    if findings:
        parts.append("\n## Waste Findings\n")
        for f in findings:
            icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(f.severity, "⚪")
            parts.append(f"\n### {icon} {f.category.replace('_', ' ').title()} — {f.node_title or f.node_id}\n")
            parts.append(f"\n{f.message}")
            parts.append(f"\n> **Suggestion:** {f.suggestion}\n")
    elif profiler is not None:
        parts.append("\n## Waste Findings\n")
        parts.append("_No waste detected._ ✅")

    return "\n".join(parts) + "\n"
