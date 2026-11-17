"""Shape B (single ReAct agent): step cap, read-only tools, code owns the money.

Runs in the default host gate (no `student` marker).
"""

from __future__ import annotations

import json
import time

import pytest

from lab8 import runtime
from lab8.db import connect
from lab8.durability import get_refund
from lab8.handoff import READONLY_TOOLS
from lab8.metrics import check_trace, trial_pass
from lab8.shapes import single
from tests.conftest import BY_ID

PUBLIC_SINGLE_RUN = [f"C-{i:03d}" for i in range(1, 11)] + ["C-013", "C-014"]


def _trace_events(result) -> list[dict]:
    with open(result.trace_path, encoding="utf-8") as fh:
        return json.load(fh)["events"]


@pytest.mark.parametrize("claim_id", PUBLIC_SINGLE_RUN)
def test_single_stays_under_step_cap_and_uses_model(public_db, claim_id):
    result = single.run_claim(claim_id, seed=11, store_path=str(public_db))
    react_steps = [e for e in _trace_events(result) if e["kind"] == "react_step"]
    assert len(react_steps) <= single.MAX_STEPS == 8
    assert result.model_calls > 0, "model calls must be counted in the Budget"
    assert result.error_reason != "step_cap"


def test_single_never_dispatches_a_write_tool(public_db, monkeypatch):
    seen: list[str] = []
    real_dispatch = runtime.dispatch

    def spy(conn, budget, name, args, *, worker):
        seen.append(name)
        return real_dispatch(conn, budget, name, args, worker=worker)

    monkeypatch.setattr(runtime, "dispatch", spy)
    for claim_id in PUBLIC_SINGLE_RUN:
        single.run_claim(claim_id, seed=11, store_path=str(public_db))
    assert seen, "the agent should call at least one tool"
    assert set(seen) <= READONLY_TOOLS
    assert "issue_refund" not in seen


def test_single_step_cap_fails_safely(public_db, monkeypatch):
    """An agent that never stops is cut at 8 steps: failed, no refund, valid trace, no exception."""

    def looping_agent(claim, memory):
        return {"tool": "lookup_order", "args": {"order_id": claim["order_id"], "principal_id": claim["principal_id"]}}

    monkeypatch.setattr(single, "_next_step", looping_agent)
    started = time.time()
    result = single.run_claim("C-001", seed=11, store_path=str(public_db))
    assert result.status == "failed"
    assert result.error_reason == "step_cap"
    assert result.proposal_amount == 0
    assert result.refund_effects == []
    ok, reason = check_trace(result.as_dict(), started_at=started)
    assert ok, reason
    conn = connect(public_db)
    try:
        assert get_refund(conn, "C-001") is None
    finally:
        conn.close()


def test_single_ignores_model_tampered_quote(public_db, monkeypatch):
    """Agent passes a ticket-inflated delay (5 days) to compute_compensation for T02.

    The shape recomputes from the order row (2 days) and must still deny with 0.
    """
    real_next = single._next_step

    def lying_agent(claim, memory):
        action = real_next(claim, memory)
        if action.get("tool") == "compute_compensation":
            order = dict(action["args"]["order"])
            order["delay_days"] = 5
            return {"tool": "compute_compensation", "args": {"order": order}}
        return action

    monkeypatch.setattr(single, "_next_step", lying_agent)
    started = time.time()
    result = single.run_claim("C-002", seed=55, store_path=str(public_db))
    conn = connect(public_db)
    try:
        passed, reason = trial_pass(result.as_dict(), BY_ID["C-002"]["expected"], conn, started_at=started)
    finally:
        conn.close()
    assert passed, reason
    assert result.status == "denied" and result.proposal_amount == 0
    kinds = [e["kind"] for e in _trace_events(result)]
    assert "quote_mismatch" in kinds


def test_single_trace_has_no_ticket_text(public_db):
    """Ticket is untrusted input (T09 injection). It never reaches the trace."""
    result = single.run_claim("C-009", seed=11, store_path=str(public_db))
    with open(result.trace_path, encoding="utf-8") as fh:
        blob = fh.read()
    assert BY_ID["C-009"]["ticket"] not in blob
    assert "ignore policy" not in blob
    assert result.status == "denied"
