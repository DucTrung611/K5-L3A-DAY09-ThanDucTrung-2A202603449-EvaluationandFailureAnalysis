"""Shape A — deterministic workflow. Student work.

Signature (do not change):

    run_claim(claim_id, *, seed, store_path, crash_at=None) -> RunResult

Suggested steps. Money stays in code (`lab8.tools.compute_compensation`).

1. Open the store. Read the claim through `get_claim_view` (not SELECT *).
2. Missing order_id -> status `needs_info`. Do not write the ledger.
3. `lookup_order`. If not visible -> `escalated`. Do not copy phone, address, item, internal note.
4. Quote from the order row. Ignore delay numbers written in the ticket.
5. Quote amount 0 -> `denied`. Do not call issue_refund.
6. Quote amount > 0 -> `durability.issue_refund(conn, claim_id, <full quote>)`.
   Pass the FULL quote. The host checks the claim's own approval and subtracts
   what earlier claims already got for the same order (T13, T14). Do not
   subtract it yourself or the host subtracts twice.
   Auth failure or order already refunded -> `denied` (the host writes `policy_denied`).
   `proposal_amount` stays the full quote. Amount conflict -> `failed`.
7. Success -> `resolved`. One ledger row, one `refund_committed` audit.
8. Use MockLLM only to draft the reply text, if you want a model call at all.

The model must not choose the refund amount and must not commit the refund.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import sqlite3

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.mock_llm import complete
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish
from lab8.tools import compute_compensation, get_claim_view, lookup_order, tool_cost
from lab8.trace import Trace

SHAPE = "workflow"


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    conn = connect(store_path)
    try:
        return _run(conn, claim_id, seed=seed, crash_at=crash_at)
    finally:
        conn.close()


def _run(conn: sqlite3.Connection, claim_id: str, *, seed: int, crash_at: str | None) -> RunResult:
    budget = Budget()
    trace = Trace(make_run_id(SHAPE, claim_id, seed))

    def done(status: str, proposal: int, reason: str | None) -> RunResult:
        # Model only drafts the reply text. It never sees the ticket or decides money.
        complete(budget, seed=seed, purpose="draft", prompt=f"status={status} proposal={proposal}")
        trace.add("decision", {"status": status, "proposal_amount": proposal, "reason": reason})
        return finish(
            conn,
            shape=SHAPE,
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=proposal,
            budget=budget,
            trace=trace,
            error_reason=reason,
        )

    # Step 1: claim through the allowlisted view. The ticket is user data: never traced, never parsed.
    view = get_claim_view(conn, claim_id)
    budget.add_tool(tool_cost())
    trace.add("claim_view", {"claim_id": claim_id, "found": view["found"], "has_order_id": bool(view.get("order_id"))})
    if not view["found"]:
        return done("failed", 0, "unknown_claim")

    # Step 2: no order id -> ask the customer. Do not guess it from the ticket text.
    if not view.get("order_id"):
        return done("needs_info", 0, "missing_order_id")

    # Step 3: order scoped to the claim's principal. Only ORDER_FIELDS come back.
    order_view = lookup_order(conn, view["order_id"], view["principal_id"])
    budget.add_tool(tool_cost())
    trace.add("lookup_order", {"visible": order_view["visible"], "reason": order_view["reason"], "order": order_view.get("order")})
    if not order_view["visible"]:
        return done("escalated", 0, order_view["reason"])

    # Step 4: quote from structured order fields and current policy only.
    quote = compute_compensation(conn, order_view["order"])
    budget.add_tool(tool_cost())
    trace.add("quote", quote)
    amount = int(quote["amount_vnd"])

    # Step 5: nothing owed -> deny without touching the host write path.
    if amount <= 0:
        return done("denied", 0, quote["reason"])

    # Step 6: host gate. Pass the FULL quote; the host applies approval, per-order cap, idempotency.
    try:
        outcome = issue_refund(conn, claim_id, amount, crash_at=crash_at)
    except InjectedCrash:
        trace.add("crash", {"at": crash_at})
        trace.write()
        raise
    trace.add("issue_refund", {"ok": outcome["ok"], "reason": outcome["reason"], "conflict": outcome["conflict"]})

    # Step 7: map the host outcome. proposal_amount stays the full quote.
    if outcome["ok"]:
        return done("resolved", amount, None)
    if outcome["conflict"]:
        return done("failed", amount, outcome["reason"])
    return done("denied", amount, outcome["reason"])
