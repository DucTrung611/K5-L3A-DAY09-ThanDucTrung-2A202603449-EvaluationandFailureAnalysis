"""Shape B — one ReAct agent. Student work.

Use `lab8.runtime.run_react` with max_steps=8. `decide` may pick a read tool.
It must not call `issue_refund`. After the loop, the host validates the quote
and is the only writer.

A decide() that trusts ticket text over the order row will fail hidden claims
where the customer overstates the delay or cites a superseded clause.

Per-order cap: an order may already have been refunded by an earlier claim.
The read tool `lookup_refund_history` shows how much. The host still enforces
the cap, so pass the full quote to `durability.issue_refund`, not a net amount.

Caps: stop at 8 steps. Count model calls in the Budget so cost stays comparable.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.

Design of this implementation
-----------------------------
Code / model boundary:

* The agent (MockLLM via `decide`) only sees structured ids: claim_id, principal_id,
  order_id. The ticket is untrusted user data, so it never reaches the agent's
  observation and never reaches the trace.
* The agent may only pick read tools (`lookup_order`, `lookup_refund_history`,
  `compute_compensation`). `run_react` dispatches them; `dispatch` refuses write tools.
* The agent's final answer is only a stop signal. After the loop the shape's own
  code re-reads the tool results, recomputes the quote from the order row returned by
  `lookup_order` (never from model-supplied arguments), and is the only caller of
  `durability.issue_refund`, with the FULL quote and `crash_at`.

Trace events (allowlisted fields only): `claim` (claim_id, has_order_id),
`react_step` (tool name), `order_view` (the ORDER_FIELDS view from `lookup_order`),
`refund_history` (count, total), `quote`, `host` (ok, reason, conflict), `decision`.
Never written: ticket, parent_secret, raw_headers, phone, address, internal_note,
item_name, the raw claim row or the raw order row.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.mock_llm import complete
from lab8.runtime import ToolRejected, run_react
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.tools import compute_compensation, get_claim_view, tool_cost
from lab8.trace import Trace

SHAPE = "single"
MAX_STEPS = 8


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
        # The model only drafts the reply text. It never sees the ticket or the amount logic.
        complete(budget, seed=seed, purpose="draft", prompt=f"status={status}")
        trace.add("decision", {"status": status, "proposal_amount": int(proposal), "reason": reason})
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

    # 1. Claim view (allowlisted columns, not SELECT *).
    view = get_claim_view(conn, claim_id)
    budget.add_tool(tool_cost())
    if not view.get("found"):
        trace.add("claim", {"claim_id": claim_id, "found": False})
        return done("failed", 0, "unknown_claim")
    trace.add("claim", {"claim_id": claim_id, "found": True, "has_order_id": bool(view.get("order_id"))})

    # 2. Missing order_id -> needs_info. Never pull an order id out of the ticket text.
    if not view.get("order_id"):
        return done("needs_info", 0, "missing_order_id")

    # 3. ReAct loop over read tools. The observation carries ids only, no ticket.
    claim = {
        "claim_id": view["claim_id"],
        "principal_id": view["principal_id"],
        "order_id": view["order_id"],
    }
    memory: dict[str, dict[str, Any]] = {}

    def decide(observation: dict[str, Any]) -> dict[str, Any]:
        last_tool = observation.get("last_tool")
        if last_tool:
            memory[last_tool] = observation.get("last_result") or {}
        action = _next_step(claim, memory)
        label = action.get("tool") or "final"
        complete(budget, seed=seed, purpose="react", prompt=f"step={len(memory)} next={label}")
        trace.add("react_step", {"step": len(memory) + 1, "action": label})
        return action

    try:
        loop = run_react(
            conn,
            budget,
            observation={"claim": claim},
            decide=decide,
            max_steps=MAX_STEPS,
            worker=False,
        )
    except ToolRejected as exc:
        trace.add("error", {"reason": "tool_rejected"})
        return done("failed", 0, f"tool_rejected:{exc}")

    if loop["stopped"] == "step_cap":
        return done("failed", 0, "step_cap")
    # The last tool result arrives together with the final action; keep it too.
    last_tool = loop["observation"].get("last_tool")
    if last_tool and last_tool not in memory:
        memory[last_tool] = loop["observation"].get("last_result") or {}

    # 4. Code (not the model) decides from the tool results.
    order_view = memory.get("lookup_order") or {}
    trace.add("order_view", safe_dump(_order_trace(order_view)))
    if not order_view.get("visible"):
        return done("escalated", 0, order_view.get("reason") or "order_not_visible")

    history = memory.get("lookup_refund_history") or {}
    if history.get("visible"):
        trace.add(
            "refund_history",
            {"refund_count": int(history.get("refund_count") or 0), "refunded_vnd": int(history.get("refunded_vnd") or 0)},
        )

    # Recompute the quote from the order row the host returned. Model-supplied
    # arguments to compute_compensation are not trusted (they could carry a ticket delay).
    quote = compute_compensation(conn, order_view["order"])
    budget.add_tool(tool_cost())
    model_quote = memory.get("compute_compensation") or {}
    if model_quote and int(model_quote.get("amount_vnd") or 0) != int(quote["amount_vnd"]):
        trace.add("quote_mismatch", {"model_vnd": int(model_quote.get("amount_vnd") or 0), "code_vnd": int(quote["amount_vnd"])})
    trace.add("quote", safe_dump(quote))
    proposal = int(quote["amount_vnd"])

    # 5. Quote 0 -> denied, no host write.
    if proposal <= 0:
        return done("denied", 0, quote.get("reason"))

    # 6. Host is the only writer. Full quote; the host subtracts earlier refunds on the order.
    try:
        outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    except InjectedCrash:
        trace.add("crash", {"at": crash_at})
        trace.write()
        raise
    trace.add("host", {"ok": bool(outcome["ok"]), "reason": outcome["reason"], "conflict": bool(outcome["conflict"])})

    if not outcome["ok"]:
        status = "failed" if outcome["conflict"] else "denied"
        return done(status, proposal, outcome["reason"])
    return done("resolved", proposal, None)


def _next_step(claim: dict[str, Any], memory: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The agent's policy: pick the next read tool, or stop. Never a write tool."""
    order_args = {"order_id": claim["order_id"], "principal_id": claim["principal_id"]}
    if "lookup_order" not in memory:
        return {"tool": "lookup_order", "args": order_args}
    order_view = memory["lookup_order"]
    if not order_view.get("visible"):
        return {"final": {"status": "escalated"}}
    if "lookup_refund_history" not in memory:
        return {"tool": "lookup_refund_history", "args": order_args}
    if "compute_compensation" not in memory:
        return {"tool": "compute_compensation", "args": {"order": order_view["order"]}}
    return {"final": {"status": "propose", "amount_vnd": int(memory["compute_compensation"].get("amount_vnd") or 0)}}


def _order_trace(order_view: dict[str, Any]) -> dict[str, Any]:
    """lookup_order already allowlists ORDER_FIELDS; copy only those keys."""
    out: dict[str, Any] = {"visible": bool(order_view.get("visible")), "reason": order_view.get("reason")}
    order = order_view.get("order")
    if isinstance(order, dict):
        out["order"] = {
            key: order[key]
            for key in ("order_id", "status", "delay_days", "shipping_fee_vnd", "paid_vnd", "damage_verified", "carrier")
            if key in order
        }
    return out
