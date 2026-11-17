"""Shape C: an orchestrator delegates reads and keeps refund writes in the host."""

from __future__ import annotations

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.handoff import make_handoff
from lab8.mock_llm import complete
from lab8.runtime import run_readonly_investigators
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.tools import get_claim_view, tool_cost
from lab8.trace import Trace


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    conn = connect(store_path)
    try:
        budget = Budget()
        trace = Trace(make_run_id("orchestrator", claim_id, seed))

        budget.add_tool(tool_cost())
        view = get_claim_view(conn, claim_id)
        # get_claim_view includes the untrusted ticket: never trace or prompt it.
        if not view["found"]:
            trace.add("decision", {"status": "failed", "reason": "unknown_claim"})
            return finish(
                conn, shape="orchestrator", claim_id=claim_id, seed=seed,
                status="failed", proposal_amount=0, budget=budget, trace=trace,
                error_reason="unknown_claim",
            )
        if not view["order_id"]:
            trace.add("decision", {"status": "needs_info", "reason": "missing_order_id"})
            return finish(
                conn, shape="orchestrator", claim_id=claim_id, seed=seed,
                status="needs_info", proposal_amount=0, budget=budget, trace=trace,
                error_reason="missing_order_id",
            )

        handoff = make_handoff(
            request_id=f"REQ-{claim_id}",
            claim_id=claim_id,
            task="investigate_claim",
            facts={},
            allowed_tools=[
                "lookup_order", "lookup_policy", "lookup_refund_history",
                "compute_compensation",
            ],
        )
        trace.add("handoff", safe_dump(handoff))
        complete(budget, seed=seed, purpose="plan", prompt="investigate_claim")
        investigated = run_readonly_investigators(
            conn, budget, handoff, principal_id=view["principal_id"]
        )
        tools = investigated["tools"]
        order_view = tools.get("lookup_order") or {}
        history = tools.get("lookup_refund_history") or {}
        trace.add("investigator", {
            "lookup_order": order_view,
            "policy_count": len((tools.get("lookup_policy") or {}).get("policies") or []),
            "refund_history": {
                "visible": history.get("visible"),
                "refund_count": history.get("refund_count"),
                "refunded_vnd": history.get("refunded_vnd"),
            },
        })
        if not order_view.get("visible"):
            complete(budget, seed=seed, purpose="draft", prompt="escalated")
            return finish(
                conn, shape="orchestrator", claim_id=claim_id, seed=seed,
                status="escalated", proposal_amount=0, budget=budget, trace=trace,
                error_reason=order_view.get("reason"),
            )

        # The investigator quotes only from the scoped, structured order view.
        quote = tools["compute_compensation"]
        proposal = int(quote["amount_vnd"])
        trace.add("quote", quote)
        complete(budget, seed=seed, purpose="draft", prompt=f"proposal={proposal}")
        if proposal <= 0:
            return finish(
                conn, shape="orchestrator", claim_id=claim_id, seed=seed,
                status="denied", proposal_amount=0, budget=budget, trace=trace,
                error_reason=quote.get("reason"),
            )

        # The host enforces approval, order caps and idempotency. Pass the full quote.
        try:
            outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
        except InjectedCrash:
            trace.add("crash", {"at": crash_at})
            trace.write()
            raise
        status = "resolved" if outcome["ok"] else "failed" if outcome["conflict"] else "denied"
        trace.add("host", {"ok": outcome["ok"], "reason": outcome.get("reason")})
        return finish(
            conn, shape="orchestrator", claim_id=claim_id, seed=seed,
            status=status, proposal_amount=proposal, budget=budget, trace=trace,
            error_reason=None if outcome["ok"] else outcome["reason"],
        )
    finally:
        conn.close()
