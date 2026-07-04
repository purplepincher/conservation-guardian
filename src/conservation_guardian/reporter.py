"""Multi-format reporter for conservation reports."""

from __future__ import annotations

import json as _json
from typing import Optional

from .analyzer import WorkflowDAG
from .budget import WorkflowBudget
from .detector import WasteFinding
from .profiler import Profiler


class Reporter:
    """Generate conservation reports in multiple formats.

    Parameters
    ----------
    budget:
        Optional budget instance for summary data.
    dag:
        Optional DAG for structural analysis.
    profiler:
        Optional profiler for per-node stats.
    findings:
        Optional list of waste findings.
    workflow_name:
        Display name for the workflow.
    """

    def __init__(
        self,
        *,
        budget: Optional[WorkflowBudget] = None,
        dag: Optional[WorkflowDAG] = None,
        profiler: Optional[Profiler] = None,
        findings: Optional[list[WasteFinding]] = None,
        workflow_name: str = "Workflow",
    ) -> None:
        self.budget = budget
        self.dag = dag
        self.profiler = profiler
        self.findings = findings or []
        self.workflow_name = workflow_name

    # ------------------------------------------------------------------
    # Markdown
    # ------------------------------------------------------------------

    def to_markdown(self) -> str:
        """Render the report as Markdown (same as ``render_report()``)."""
        from .report import render_report
        return render_report(
            budget=self.budget,
            dag=self.dag,
            profiler=self.profiler,
            findings=self.findings,
            workflow_name=self.workflow_name,
        )

    # ------------------------------------------------------------------
    # JSON
    # ------------------------------------------------------------------

    def to_json(self, *, indent: int = 2) -> str:
        """Return a structured JSON representation suitable for dashboards."""
        data: dict = {
            "workflow_name": self.workflow_name,
            "budget": self._serialize_budget(),
            "dag": self._serialize_dag(),
            "top_nodes": self._serialize_top_nodes(),
            "findings": self._serialize_findings(),
        }
        return _json.dumps(data, indent=indent, default=str)

    # ------------------------------------------------------------------
    # Prometheus
    # ------------------------------------------------------------------

    def to_prometheus(self) -> str:
        """Return Prometheus-format metrics."""
        lines: list[str] = []

        if self.budget is not None:
            lines.append('# HELP conservation_budget_daily_spend Total spend today in USD')
            lines.append('# TYPE conservation_budget_daily_spend gauge')
            lines.append(f'conservation_budget_daily_spend {{workflow="{self.workflow_name}"}} {self.budget.daily_spend():.6f}')
            lines.append('# HELP conservation_budget_avg_tokens_per_run Average tokens per run')
            lines.append('# TYPE conservation_budget_avg_tokens_per_run gauge')
            lines.append(f'conservation_budget_avg_tokens_per_run {{workflow="{self.workflow_name}"}} {self.budget.avg_tokens_per_run():.0f}')

        if self.profiler is not None:
            for p in self.profiler.all_profiles():
                labels = f'workflow="{self.workflow_name}",node="{p.node_title or p.node_id}"'
                lines.append(f'conservation_node_total_cost{{{labels}}} {p.total_cost:.6f}')
                lines.append(f'conservation_node_avg_cost{{{labels}}} {p.avg_cost:.6f}')
                lines.append(f'conservation_node_avg_latency_ms{{{labels}}} {p.avg_latency_ms:.1f}')
                lines.append(f'conservation_node_run_count{{{labels}}} {p.run_count}')
                lines.append(f'conservation_node_avg_input_tokens{{{labels}}} {p.avg_input_tokens:.0f}')
                lines.append(f'conservation_node_avg_output_tokens{{{labels}}} {p.avg_output_tokens:.0f}')

        if self.findings:
            lines.append('# HELP conservation_findings_total Number of waste findings')
            lines.append('# TYPE conservation_findings_total gauge')
            lines.append(f'conservation_findings_total {{workflow="{self.workflow_name}"}} {len(self.findings)}')
            for severity in ("high", "medium", "low"):
                count = sum(1 for f in self.findings if f.severity == severity)
                lines.append(f'conservation_findings_by_severity{{workflow="{self.workflow_name}",severity="{severity}"}} {count}')

        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # Slack
    # ------------------------------------------------------------------

    def to_slack(self) -> str:
        """Return Slack Blocks API JSON as a string."""
        blocks: list[dict] = []

        # Header
        blocks.append({
            "type": "header",
            "text": {"type": "plain_text", "text": f"🔍 Conservation Report — {self.workflow_name}"},
        })

        # Budget summary
        if self.budget is not None:
            fields = [
                {"type": "mrkdwn", "text": f"*Daily Spend:* ${self.budget.daily_spend():.4f}"},
                {"type": "mrkdwn", "text": f"*Avg Tokens/Run:* {self.budget.avg_tokens_per_run():,.0f}"},
            ]
            blocks.append({"type": "section", "fields": fields})

        # Top nodes
        if self.profiler is not None:
            top = self.profiler.top_by_cost(3)
            if top:
                lines = []
                for p in top:
                    lines.append(f"• *{p.node_title or p.node_id}* — {p.run_count} runs, ${p.total_cost:.4f} total")
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "*Top Nodes by Cost:*\n" + "\n".join(lines)},
                })

        # Findings
        if self.findings:
            icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}
            lines = []
            for f in self.findings:
                icon = icons.get(f.severity, "⚪")
                lines.append(f"{icon} *{f.category.replace('_', ' ').title()}* — {f.node_title or f.node_id}")
                lines.append(f"  _{f.message}_")
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*Waste Findings:*\n" + "\n".join(lines)},
            })
        else:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "✅ No waste detected."},
            })

        return _json.dumps({"blocks": blocks}, indent=2)

    # ------------------------------------------------------------------
    # Internal serialization helpers
    # ------------------------------------------------------------------

    def _serialize_budget(self) -> Optional[dict]:
        if self.budget is None:
            return None
        return {
            "max_tokens_per_run": self.budget.max_tokens_per_run,
            "max_cost_per_day": self.budget.max_cost_per_day,
            "daily_spend": self.budget.daily_spend(),
            "avg_tokens_per_run": self.budget.avg_tokens_per_run(),
        }

    def _serialize_dag(self) -> Optional[dict]:
        if self.dag is None:
            return None
        return {
            "total_nodes": len(self.dag.nodes),
            "llm_nodes": len(self.dag.llm_nodes()),
            "redundant_llm_calls": len(self.dag.redundant_llm_calls()),
            "dead_branches": len(self.dag.dead_branches()),
        }

    def _serialize_top_nodes(self) -> list[dict]:
        if self.profiler is None:
            return []
        top = self.profiler.top_by_cost(10)
        return [
            {
                "node_id": p.node_id,
                "node_title": p.node_title,
                "run_count": p.run_count,
                "avg_input_tokens": p.avg_input_tokens,
                "avg_output_tokens": p.avg_output_tokens,
                "avg_cost": p.avg_cost,
                "total_cost": p.total_cost,
                "avg_latency_ms": p.avg_latency_ms,
            }
            for p in top
        ]

    def _serialize_findings(self) -> list[dict]:
        return [
            {
                "node_id": f.node_id,
                "node_title": f.node_title,
                "category": f.category,
                "severity": f.severity,
                "message": f.message,
                "suggestion": f.suggestion,
            }
            for f in self.findings
        ]
